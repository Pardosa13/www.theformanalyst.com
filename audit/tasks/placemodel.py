"""Can a model beat Betfair's PLACE market? (horse racing, read-only)

Every lead that survived the audits was a place bet, and the place market
looked slower than the win market. This starts from the place market's own
chance (Betfair place SP, margin removed per race) and lets LightGBM add only
what the market misses, using the same form, ratings and record inputs as
residual.py. Trained Feb-Jun, judged Jul-Oct (never seen).

Real edge = a log-loss gain clearly above what the same model finds in
made-up worlds where placings are drawn from the place market itself.
Overlays are then backed for the place at Betfair place SP (8% commission).
"""
import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import residual, trending
from audit.tasks.sweep3 import KEEP

SPLIT = pd.Timestamp("2026-07-01")


def _fit(f, d, y, label, ctx):
    import lightgbm as lgb
    q = np.clip(d.place_q.values, 1e-4, 1 - 1e-4)
    init = np.log(q / (1 - q))
    tr = (d.date < SPLIT).values
    te = ~tr
    cut = d.date[tr].quantile(0.8)
    fit_m = tr & (d.date < cut).values
    val_m = tr & (d.date >= cut).values
    cat = [c for c in ("track_code", "going_code", "sex_code") if c in f.columns]
    params = dict(objective="binary", learning_rate=0.03, num_leaves=15, min_data_in_leaf=200,
                  feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0,
                  verbose=-1, seed=7, num_threads=6)
    dtr = lgb.Dataset(f[fit_m], y[fit_m], init_score=init[fit_m], categorical_feature=cat, free_raw_data=False)
    dva = lgb.Dataset(f[val_m], y[val_m], init_score=init[val_m], categorical_feature=cat, reference=dtr)
    bst = lgb.train(params, dtr, num_boost_round=2000, valid_sets=[dva],
                    callbacks=[lgb.early_stopping(100, verbose=False)])
    p = 1 / (1 + np.exp(-(bst.predict(f[te], raw_score=True) + init[te])))
    # Rescale so each race's chances add up to the places paid.
    k = trending.places_paid(d.field.values[te])
    total = pd.Series(p).groupby(d.race_id.values[te]).transform("sum").values
    p = np.clip(p * k / total, 1e-4, 1 - 1e-4)
    yt, qt = y[te], q[te]
    ll = lambda z: float(-np.mean(yt * np.log(z) + (1 - yt) * np.log(1 - z)))
    gain = ll(qt) - ll(p)
    emit(f"place.{label}", f"trees={bst.best_iteration} test runners={int(te.sum())} "
                          f"logloss market={ll(qt):.5f} model={ll(p):.5f} gain={gain:+.5f} (positive = beats market)")
    return gain, p, te, bst


def run(ctx):
    d = trending._prepare(ctx)
    d = d[d.place_q.notna() & np.isfinite(d.ret_place.values)].reset_index(drop=True)
    emit("place", f"runners={len(d)} races={d.race_id.nunique()} train<{SPLIT.date()} "
                  f"test={int((d.date >= SPLIT).sum())}")
    f = residual.features(d)
    for c in ("log_bsp", "mkt_rank", "price_vs_last"):
        f[c] = f[c]      # win-market inputs stay: both prices are known at the jump
    y = d.placed.values.astype(float)
    gain, p, te, bst = _fit(f, d, y, "real", ctx)

    pb = d.place_bsp.values[te]
    ret = d.ret_place.values[te]
    qt = d.place_q.values[te]
    rows = []
    for th in (1.05, 1.10, 1.20, 1.30):
        for lo, hi in ((1, 2), (2, 4), (4, 1000), (1, 1000)):
            m = (p / qt >= th) & (pb >= lo) & (pb < hi)
            n = int(m.sum())
            if n < 30:
                continue
            w = (pb[m] - 1) * KEEP
            mu = qt[m] * w - (1 - qt[m])
            var = qt[m] * w ** 2 + (1 - qt[m]) - mu ** 2
            z = (ret[m].sum() - mu.sum()) / np.sqrt(var.sum())
            rows.append((th, f"{lo}-{hi}", n, round(100 * y[te][m].mean(), 1), round(100 * ret[m].mean(), 1), round(z, 2)))
    ctx.table("place.overlays_test", ["model/market_at_least", "place_bsp_band", "bets", "placed_pct", "roi_place", "z"], rows)
    imp = pd.Series(bst.feature_importance("gain"), index=f.columns).sort_values(ascending=False)
    emit("place.real", "top inputs by gain %: " + ", ".join(
        f"{k}={v}" for k, v in (100 * imp / imp.sum()).round(1).head(12).items()))

    rng = np.random.default_rng(17)
    nulls = []
    for i in range(3):
        fake = (rng.random(len(d)) < np.nan_to_num(d.place_q.values)).astype(float)
        nulls.append(round(_fit(f, d, fake, f"null{i}", ctx)[0], 5))
    emit("place", f"SUMMARY gain real={gain:+.5f} no-edge worlds={nulls} (real edge = clearly above them)")
