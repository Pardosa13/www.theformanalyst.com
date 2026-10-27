"""A model that only learns where the Betfair market is wrong.

The market's chance (from BSP) goes in as the starting point (LightGBM
init_score), so the trees can only add what the market misses: form,
ratings, track and distance records, first/second/third up, jockey
changes, the track itself, market size. Trained Feb-Jun, judged on Jul-Sep,
which it never sees.

If it beats the market on unseen races, backing its overlays at BSP should
make money. A "no-edge world" run (winners re-drawn from the market's own
odds) shows what the same model finds by pure luck.
"""
import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import bf_files, frame

SPLIT = pd.Timestamp("2026-07-01")
MONTHS = [(2026, m) for m in range(2, 10)]


def _rate(n, w):
    return np.where(n > 0, w / n.clip(lower=1), np.nan)


def _target_encode(train, full, col, k=50):
    prior = train.won.mean()
    g = train.groupby(col)["won"].agg(["sum", "count"])
    enc = (g["sum"] + k * prior) / (g["count"] + k)
    return full[col].map(enc).fillna(prior)


def features(d):
    tr = d[d.date < SPLIT]
    f = pd.DataFrame(index=d.index)
    rr = lambda c, asc=False: d.groupby("race_id")[c].rank(ascending=asc, method="average")
    f["a_score_rank"] = rr("a_score")
    f["a_score_vs_avg"] = d.a_score - d.groupby("race_id")["a_score"].transform("mean")
    f["a_prob"] = d.a_prob
    for c in ("pfai_price", "assessed_price", "wc_price", "time_price"):
        f[f"log_{c}_vs_bsp"] = np.log(d[c].where(d[c] > 1) / d.bsp)
    for c in ("pfai_rank", "time_rank", "wc_rank", "settle", "sm_speed", "sm_settle", "class_change",
              "career_starts", "last_pos", "last_margin", "days_since", "nth_up", "weight", "claim",
              "barrier", "field", "age", "dist", "race_number"):
        f[c] = d[c]
    f["career_win_pct"] = _rate(d.career_starts, d.career_wins)
    f["log_last_price"] = np.log(d.last_price.where(d.last_price > 1))
    f["price_vs_last"] = np.log(d.bsp / d.last_price.where(d.last_price > 1))
    f["dist_change"] = d.dist - d.last_dist
    f["weight_change"] = d.weight - d.last_weight
    f["barrier_pct"] = d.barrier / d.field
    for r in ("track", "trackdist", "dist", "good", "soft", "heavy", "synth"):
        f[f"rec_{r}_n"] = d[f"rec_{r}_n"]
        f[f"rec_{r}_pct"] = _rate(d[f"rec_{r}_n"], d[f"rec_{r}_w"])
    up_n = np.where(d.nth_up == 1, d.rec_1up_n, np.where(d.nth_up == 2, d.rec_2up_n, np.nan))
    up_w = np.where(d.nth_up == 1, d.rec_1up_w, np.where(d.nth_up == 2, d.rec_2up_w, np.nan))
    f["rec_this_up_n"] = up_n
    f["rec_this_up_pct"] = np.where(up_n > 0, up_w / np.maximum(up_n, 1), np.nan)
    cond = d.track_condition.fillna("").str.lower()
    going = np.select([cond.str.contains("heavy"), cond.str.contains("soft"), cond.str.contains("synth")],
                      ["heavy", "soft", "synth"], "good")
    f["rec_going_pct"] = np.select([going == "heavy", going == "soft", going == "synth"],
                                   [f.rec_heavy_pct, f.rec_soft_pct, f.rec_synth_pct], f.rec_good_pct)
    f["going_code"] = pd.Categorical(going).codes
    f["sex_code"] = pd.Categorical(d.sex).codes
    f["jockey_sr"] = _target_encode(tr, d, "jockey")
    f["trainer_sr"] = _target_encode(tr, d, "trainer")
    f["sire_sr"] = _target_encode(tr, d, "sire", k=100)
    enc = tr.groupby("jockey")["won"].agg(["sum", "count"])
    prior = tr.won.mean()
    jmap = ((enc["sum"] + 50 * prior) / (enc["count"] + 50))
    jmap.index = jmap.index.str.lower()
    f["jockey_change"] = (d.jockey.fillna("").str.lower() != d.last_jockey).astype(float)
    f["jockey_upgrade"] = f["jockey_sr"] - d.last_jockey.map(jmap).fillna(prior)
    f["track_code"] = pd.Categorical(d.track).codes
    f["log_market_vol"] = np.log1p(d.market_vol)
    f["mkt_rank"] = d.bsp_rank
    f["log_bsp"] = np.log(d.bsp)
    return f


def _race_norm(p, race):
    return p / pd.Series(p).groupby(race).transform("sum").values


def fit_eval(f, d, won, label, ctx, extra=None):
    import lightgbm as lgb
    cols = list(f.columns) + (extra or [])
    X = f.copy()
    if extra:
        for c in extra:
            X[c] = d[c]
    X = X[cols]
    mkt = d.mkt_p.values
    init = np.log(mkt / (1 - mkt))
    tr = (d.date < SPLIT).values
    te = ~tr
    dates_tr = d.date[tr]
    cut = dates_tr.quantile(0.8)
    fit_m = tr & (d.date < cut).values
    val_m = tr & (d.date >= cut).values
    params = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=200,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0,
                  verbose=-1, seed=7)
    cat = [c for c in ("track_code", "going_code", "sex_code") if c in cols]
    dtr = lgb.Dataset(X[fit_m], won[fit_m], init_score=init[fit_m], categorical_feature=cat, free_raw_data=False)
    dva = lgb.Dataset(X[val_m], won[val_m], init_score=init[val_m], categorical_feature=cat, reference=dtr)
    bst = lgb.train(params, dtr, num_boost_round=2000, valid_sets=[dva],
                    callbacks=[lgb.early_stopping(100, verbose=False)])
    raw = bst.predict(X[te], raw_score=True) + init[te]
    p = _race_norm(1 / (1 + np.exp(-raw)), d.race_id.values[te])
    races = d.race_id.values[te]
    nr = len(np.unique(races))
    y = won[te]
    ll = lambda q: -np.sum(y * np.log(np.clip(q, 1e-9, 1))) / nr
    emit(f"res.{label}", f"trees={bst.best_iteration} test races={nr} logloss/race market={ll(mkt[te]):.4f} model={ll(p):.4f} gain={ll(mkt[te]) - ll(p):+.4f} (positive = model beats market)")
    bsp = d.bsp.values[te]
    ret = np.where(y == 1, (bsp - 1) * 0.92, -1.0)
    ratio = p / mkt[te]
    rows = []
    for th in (1.05, 1.1, 1.2, 1.3, 1.5, 2.0):
        for lo, hi in ((1, 4), (4, 10), (10, 1000), (1, 1000)):
            m = (ratio >= th) & (bsp >= lo) & (bsp < hi)
            n = int(m.sum())
            if n < 30:
                continue
            pp = mkt[te][m]
            b = bsp[m]
            mu = pp * (b - 1) * 0.92 - (1 - pp)
            var = pp * ((b - 1) * 0.92) ** 2 + (1 - pp) - mu ** 2
            z = (ret[m].sum() - mu.sum()) / np.sqrt(var.sum())
            rows.append((th, f"{lo}-{hi}", n, int(y[m].sum()), round(100 * ret[m].mean(), 1), round(z, 2)))
    ctx.table(f"res.{label}.overlays", ["model/market_at_least", "bsp_band", "bets", "wins", "roi_bsp", "z_vs_market"], rows)
    gain = pd.Series(bst.feature_importance("gain"), index=cols).sort_values(ascending=False)
    gain = (100 * gain / gain.sum()).round(1).head(15)
    emit(f"res.{label}", "top inputs by gain %: " + ", ".join(f"{k}={v}" for k, v in gain.items()))
    return ll(mkt[te]) - ll(p)


def run(ctx):
    d = frame.build(ctx)
    d = d[d.mkt_p.notna() & (d.date >= pd.Timestamp("2026-02-01"))].reset_index(drop=True)
    d = bf_files.attach(d, MONTHS)
    d["pre_move"] = np.log(d.pre_wap.where(d.pre_wap > 1) / d.bsp)
    d["pre_range"] = np.log(d.pre_max.where(d.pre_max > 1) / d.pre_min.where(d.pre_min > 1))
    emit("res", f"runners={len(d)} races={d.race_id.nunique()} train<{SPLIT.date()} test={int((d.date >= SPLIT).sum())}")
    f = features(d)
    won = d.won.values.astype(float)
    g1 = fit_eval(f, d, won, "form_only", ctx)
    g2 = fit_eval(f, d, won, "with_price_moves", ctx, extra=["pre_move", "pre_range"])
    # No-edge world: winners drawn from the market's own odds, same model.
    rng = np.random.default_rng(11)
    nulls = []
    for k in range(2):
        u = d.groupby("race_id")["race_id"].transform(lambda s: rng.random()).values
        cum = d.groupby("race_id")["mkt_p"].cumsum().values
        fake = ((u >= cum - d.mkt_p.values) & (u < cum)).astype(float)
        nulls.append(round(fit_eval(f, d, fake, f"null{k}", ctx), 4))
    emit("res", f"SUMMARY gain form_only={g1:+.4f} with_price_moves={g2:+.4f} no-edge-world gains={nulls} (real edge = clearly above the no-edge gains)")
