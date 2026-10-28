"""Overnight sweep for profitable betting patterns, judged at Betfair SP.

Four parts, each printed to the logs:

1. calib    Is the market itself wrong anywhere? Win and place BSP price bands:
            actual win/place rate vs what the price says, ROI backing and laying.
2. harville Does the place market disagree with the win market? Each runner's
            chance of placing worked out from the WIN prices (Harville), compared
            with the PLACE price. Fitted on early months, judged on later months.
3. form     Jockey, trainer, sire, trainer-at-track and track/barrier form as
            actual wins vs the market's expected wins, using ONLY races run on
            earlier dates (nothing peeks at results).
4. wf       The honest test. Each month, pick patterns (single conditions and
            pairs) using only earlier months, then bet them in that month.
            The total over all months is what following the hunt would have
            made. The same thing is run on made-up results drawn from the
            market's own prices (a world with no edge) to show what luck gives.

Win bets: back/lay at win BSP. Place bets: back/lay at place BSP. 8% commission
on winnings throughout.
"""
import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import bf_files, frame
from audit.tasks.hunt2 import extra_conditions, notes_conditions
from audit.tasks.patterns import conditions as base_conditions

KEEP = 0.92                      # after 8% commission
MONTHS = [(2026, m) for m in range(2, 11)]
START = pd.Timestamp("2026-02-01")
MIN_N = 250                      # runners a condition needs over the whole period
WF_FIRST = 4                     # first test month is the 4th month of data
WF_MIN_SEARCH = 150              # bets in the earlier months to pick a pattern
WF_Z = 2.5                       # how strong a pattern must look in earlier months
WF_MAX_PICK = 150                # patterns carried into a month (strongest first)
# Conditions built with knowledge of later months; left out of the honest test.
LEAKY = ("jockey upgrade", "jockey downgrade", "top-20 jockey", "month ")


def places_paid(field):
    return np.where(field >= 8, 3, np.where(field >= 5, 2, 0))


# ── 1. Market calibration ───────────────────────────────────────────────────

def calib(ctx, d):
    half = d.date >= d.date.quantile(0.5)
    rows = []
    bands = [(1, 1.5), (1.5, 2), (2, 3), (3, 4), (4, 6), (6, 8), (8, 12), (12, 20), (20, 35),
             (35, 60), (60, 100), (100, 1001)]
    for lo, hi in bands:
        m = (d.bsp >= lo) & (d.bsp < hi)
        for per, pm in (("1st half", ~half), ("2nd half", half), ("all", pd.Series(True, index=d.index))):
            x = d[m & pm]
            if len(x) < 50:
                continue
            rows.append(("win", f"{lo}-{hi}", per, len(x), round(100 * x.won.mean(), 2),
                         round(100 * (1 / x.bsp).mean(), 2), round(100 * x.mkt_p.mean(), 2),
                         round(100 * x.ret_bsp.mean(), 1), round(100 * x.ret_lay.mean(), 1)))
    p = d[d.place_bsp > 1]
    pbands = [(1, 1.2), (1.2, 1.5), (1.5, 2), (2, 3), (3, 5), (5, 8), (8, 15), (15, 1001)]
    for lo, hi in pbands:
        m = (p.place_bsp >= lo) & (p.place_bsp < hi)
        for per, pm in (("1st half", ~half[p.index]), ("2nd half", half[p.index]), ("all", pd.Series(True, index=p.index))):
            x = p[m & pm]
            if len(x) < 50:
                continue
            rows.append(("place", f"{lo}-{hi}", per, len(x), round(100 * x.placed.mean(), 2),
                         round(100 * (1 / x.place_bsp).mean(), 2), round(100 * x.place_q.mean(), 2),
                         round(100 * x.ret_place.mean(), 1), round(100 * x.ret_place_lay.mean(), 1)))
    ctx.table("sweep3.calib", ["market", "price", "period", "runners", "actual_%", "price_says_%",
                               "fair_%", "roi_back", "roi_lay"], rows)


# ── 2. Win market vs place market (Harville) ────────────────────────────────

def harville(p, k):
    """Chance of finishing in the top k from win chances p (one race)."""
    p = np.clip(np.asarray(p, float), 1e-6, 0.999)
    out = p.copy()
    if k >= 2:
        r = p / (1 - p)
        out = out + p * (r.sum() - r)
    if k >= 3:
        den = 1 - p[:, None] - p[None, :]
        A = (p[:, None] * p[None, :]) / ((1 - p[:, None]) * np.clip(den, 1e-6, None))
        np.fill_diagonal(A, 0.0)
        out = out + p * (A.sum() - A.sum(axis=1) - A.sum(axis=0))
    return np.clip(out, 1e-6, 0.999)


def _logit(x):
    x = np.clip(x, 1e-6, 1 - 1e-6)
    return np.log(x / (1 - x))


def _fit_logistic(X, y, iters=30, ridge=1e-3):
    X = np.column_stack([np.ones(len(X)), X])
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        z = np.clip(X @ w, -30, 30)
        mu = 1 / (1 + np.exp(-z))
        W = mu * (1 - mu)
        H = X.T @ (X * W[:, None]) + ridge * np.eye(X.shape[1])
        w = w + np.linalg.solve(H, X.T @ (y - mu) - ridge * w)
    return w


def _predict(w, X):
    X = np.column_stack([np.ones(len(X)), X])
    return 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))


def _logloss(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def harville_part(ctx, d, split):
    d = d.copy()
    h = np.full(len(d), np.nan)
    field, mkt = d.field.values, d.mkt_p.values
    for idx in d.groupby("race_id").indices.values():
        k = int(places_paid(np.array([field[idx[0]]]))[0])
        p = mkt[idx]
        if k < 2 or not np.all(np.isfinite(p)):
            continue
        h[idx] = harville(p / p.sum(), k)
    d["h_place"] = h
    x = d[(d.place_bsp > 1) & d.h_place.notna() & d.place_q.notna()].copy()
    tr, te = (x.date < split).values, (x.date >= split).values
    emit("sweep3.harville", f"runners={len(x)} fit_months<{split.date()} fit={tr.sum()} judge={te.sum()}")
    y = x.placed.values.astype(float)
    Xm = np.column_stack([_logit(x.place_q.values)])
    Xh = np.column_stack([_logit(x.h_place.values)])
    Xb = np.column_stack([_logit(x.place_q.values), _logit(x.h_place.values), np.log(x.bsp.values)])
    preds = {}
    for name, X in (("place market only", Xm), ("win market (Harville) only", Xh), ("both + win price", Xb)):
        w = _fit_logistic(X[tr], y[tr])
        preds[name] = _predict(w, X)
        emit("sweep3.harville", f"{name}: judge-months log loss={_logloss(preds[name][te], y[te]):.5f} "
                                f"(raw place market={_logloss(x.place_q.values[te], y[te]):.5f}) weights={np.round(w, 3).tolist()}")
    # Bets: back or lay the place where the combined view disagrees with the place price.
    pc = preds["both + win price"]
    pb = x.place_bsp.values
    ev_back = pc * (pb - 1) * KEEP - (1 - pc)
    ev_lay = (1 - pc) * KEEP - pc * (pb - 1)
    rows = []
    for side, ev, ret in (("back place", ev_back, x.ret_place.values), ("lay place", ev_lay, x.ret_place_lay.values)):
        for e in (0.0, 0.02, 0.05, 0.10, 0.20):
            for per, pm in (("fit months", tr), ("judge months", te)):
                m = pm & (ev > e)
                n = int(m.sum())
                if n:
                    rows.append((side, e, per, n, round(100 * ret[m].mean(), 1),
                                 round(float(ret[m].sum() / np.sqrt(np.sum(np.abs(pb[m] - 1)) + 1e-9)), 2)))
    ctx.table("sweep3.harville.bets", ["bet", "min_expected_edge", "period", "bets", "roi", "z"], rows)
    # Simple, nothing fitted: Harville place chance / place-market chance.
    ratio = x.h_place.values / x.place_q.values
    rows = []
    for lo, hi in ((0, 0.8), (0.8, 0.9), (0.9, 1.0), (1.0, 1.1), (1.1, 1.25), (1.25, 1.5), (1.5, 100)):
        for per, pm in (("fit months", tr), ("judge months", te)):
            m = pm & (ratio >= lo) & (ratio < hi)
            n = int(m.sum())
            if n:
                rows.append((f"{lo}-{hi}", per, n, round(100 * x.placed.values[m].mean(), 1),
                             round(100 * x.place_q.values[m].mean(), 1),
                             round(100 * x.ret_place.values[m].mean(), 1), round(100 * x.ret_place_lay.values[m].mean(), 1)))
    ctx.table("sweep3.harville.ratio", ["win_says/place_says", "period", "runners", "placed_%", "place_mkt_%",
                                        "roi_back_place", "roi_lay_place"], rows)


# ── 3. Point-in-time form (no peeking) ──────────────────────────────────────

def _before(d, key, val, exp, window_days=None, lookup=None):
    """For each runner: count, actual and expected for `key` over races on
    EARLIER dates only (optionally only the last `window_days` days).
    `lookup` reads another column's names from the same table (e.g. the
    jockey from last start), still as at the runner's race date."""
    ok = np.isfinite(exp)
    t = pd.DataFrame({"key": key.values, "date": d.date.values,
                      "a": np.where(ok, val, 0.0), "e": np.where(ok, exp, 0.0), "n": ok.astype(float)})
    day = t.groupby(["key", "date"], sort=False)[["a", "e", "n"]].sum().reset_index().sort_values(["key", "date"])
    g = day.groupby("key")
    for c in ("a", "e", "n"):
        day["c" + c] = g[c].cumsum()
    tab = day[["key", "date", "ca", "ce", "cn"]].sort_values("date")
    lk = key if lookup is None else lookup
    look = pd.DataFrame({"key": lk.values, "date": d.date.values, "i": np.arange(len(d))}).sort_values("date")
    now = pd.merge_asof(look, tab, on="date", by="key", allow_exact_matches=False).sort_values("i")
    a, e, n = (now[c].fillna(0).values for c in ("ca", "ce", "cn"))
    if window_days:
        look2 = look.copy()
        look2["date"] = look2["date"] - pd.Timedelta(days=window_days)
        look2 = look2.sort_values("date")
        old = pd.merge_asof(look2, tab, on="date", by="key", allow_exact_matches=True).sort_values("i")
        a = a - old["ca"].fillna(0).values
        e = e - old["ce"].fillna(0).values
        n = n - old["cn"].fillna(0).values
    return n, a, e


def form_conditions(d):
    c = {}

    def add(name, m):
        m = np.asarray(m, dtype=bool)
        if m.sum() >= MIN_N:
            c[name] = m
    bar = pd.cut(d.barrier, [0, 3, 6, 9, 40], labels=["1-3", "4-6", "7-9", "10+"]).astype(str)
    dist = pd.cut(d.dist, [0, 1100, 1300, 1600, 2000, 5000], labels=["-1100", "1100-1300", "1300-1600", "1600-2000", "2000+"]).astype(str)
    s = lambda col: d[col].fillna("").astype(str).str.strip().str.lower()
    J, T, S, K = s("jockey"), s("trainer"), s("sire"), d.track.fillna("").astype(str)
    both = lambda x, y, sep: (x + sep + y).where((x != "") & (y != ""), "")
    keys = {
        "jockey": J, "trainer": T, "sire": S,
        "trainer@track": both(T, K, "@"), "jockey+trainer": both(J, T, "+"),
        "track/dist/barrier": (K + "|" + dist + "|" + bar).where((K != "") & (bar != "nan") & (dist != "nan"), ""),
    }
    for label, key in keys.items():
        for window, wl in ((None, "all-time"), (90, "last 90d")):
            n, a, e = _before(d, key, d.won.values.astype(float), d.mkt_p.values, window)
            ae = (a + 2) / (e + 2)
            big = n >= (15 if window else 40)
            for lo, hi, tag in ((1.5, 99, "A/E 1.5+"), (1.2, 1.5, "A/E 1.2-1.5"), (0, 0.6, "A/E <0.6"), (0.6, 0.85, "A/E 0.6-0.85")):
                add(f"form {label} {wl} win {tag}", big & (ae >= lo) & (ae < hi) & (key != "").values)
            n, a, e = _before(d, key, d.placed.values.astype(float), d.place_q.values, window)
            ae = (a + 2) / (e + 2)
            big = n >= (15 if window else 40)
            for lo, hi, tag in ((1.25, 99, "A/E 1.25+"), (0, 0.75, "A/E <0.75")):
                add(f"form {label} {wl} place {tag}", big & (ae >= lo) & (ae < hi) & (key != "").values)
    # Jockey switch judged on earlier dates only.
    won, mp = d.won.values.astype(float), d.mkt_p.values
    n, a, e = _before(d, J, won, mp)
    ae_now = (a + 5) / (e + 5)
    lj = d.last_jockey.fillna("").astype(str).str.strip().str.lower()
    n2, a2, e2 = _before(d, J, won, mp, lookup=lj)   # last start's jockey, as known on race day
    ae_last = (a2 + 5) / (e2 + 5)
    changed = (J != lj) & (lj != "") & (J != "")
    add("jockey switch to better form jockey (no-peek)", changed & (ae_now - ae_last >= 0.2))
    add("jockey switch to worse form jockey (no-peek)", changed & (ae_now - ae_last <= -0.2))
    return c


# ── 4. Walk-forward pattern picking (the honest test) ───────────────────────

def _month_stats(M, ret, var, months, ok):
    out = {}
    for mo in np.unique(months):
        sel = (months == mo) & ok
        Ms = M[sel]
        r = ret[sel].astype(np.float32)
        v = var[sel].astype(np.float32)
        out[mo] = (Ms.T @ Ms, (Ms * r[:, None]).T @ Ms, (Ms * v[:, None]).T @ Ms)
    return out


def walk_forward(d, names, M, ret, var):
    var = np.asarray(var, float)
    ok = np.isfinite(ret) & np.isfinite(var) & (var > 0) & (var < 1e4)
    r = np.where(ok, ret, 0.0)
    v = np.where(ok, var, 0.0)
    months = d.date.dt.to_period("M").astype(str).values
    stats = _month_stats(M, r, v, months, ok)
    order = sorted(stats)
    k = len(names)
    iu = np.triu_indices(k)
    res, picks_seen = [], {}
    for t in range(WF_FIRST - 1, len(order)):
        mo = order[t]
        N = sum(stats[x][0] for x in order[:t])
        P = sum(stats[x][1] for x in order[:t])
        V = sum(stats[x][2] for x in order[:t])
        with np.errstate(divide="ignore", invalid="ignore"):
            z = P / np.sqrt(V)
            roi = P / N
        cand = (N[iu] >= WF_MIN_SEARCH) & (roi[iu] > 0) & (z[iu] > WF_Z)
        a, b = iu[0][cand], iu[1][cand]
        top = np.argsort(-z[a, b])[:WF_MAX_PICK]
        a, b = a[top], b[top]
        te = (months == mo) & ok
        Mt = M[te] > 0
        bet = np.zeros(te.sum(), bool)
        for x, y in zip(a, b):
            hit = Mt[:, x] & Mt[:, y]
            bet |= hit
            nm = names[x] if x == y else f"{names[x]} + {names[y]}"
            s = picks_seen.setdefault(nm, [0, 0, 0.0])
            s[0] += 1
            s[1] += int(hit.sum())
            s[2] += float(r[te][hit].sum())
        rt = r[te][bet]
        res.append((mo, len(a), int(bet.sum()), float(rt.sum()), float(v[te][bet].sum())))
    return res, picks_seen


def _wf_summary(res):
    bets = sum(x[2] for x in res)
    profit = sum(x[3] for x in res)
    var = sum(x[4] for x in res)
    return bets, (100 * profit / bets if bets else float("nan")), (profit / np.sqrt(var) if var > 0 else float("nan"))


def prepare(ctx, report=True):
    """Runners, conditions, bet returns and no-edge worlds shared by the sweep's tasks."""
    d = frame.build(ctx)
    d = d[d.mkt_p.notna() & (d.date >= START)].reset_index(drop=True)
    d = bf_files.attach(d, MONTHS)
    d["placed"] = d["placed"].fillna(0).astype(int)
    k = places_paid(d.field.values)
    has_pb = (d.place_bsp > 1) & (k >= 2)
    ip = np.where(has_pb, 1 / d.place_bsp, np.nan)
    book = pd.Series(ip).groupby(d.race_id.values).transform("sum").values
    n_pb = pd.Series(has_pb.astype(int)).groupby(d.race_id.values).transform("sum").values
    # Place market's fair chance: place prices scaled so the race adds up to the places paid.
    d["place_q"] = np.where(has_pb & (n_pb == d.field.values), np.clip(ip / book * k, 1e-4, 0.999), np.nan)
    # Place bets only where the whole race has place prices (same set for real and luck tests).
    d.loc[d.place_q.isna(), ["ret_place", "ret_place_lay"]] = np.nan
    if report:
        months = d.date.dt.to_period("M")
        emit("sweep3", f"runners={len(d)} races={d.race_id.nunique()} {d.date.min().date()}..{d.date.max().date()} "
                       f"place_priced={has_pb.mean():.2f} months={months.value_counts().sort_index().to_dict()}")
        emit("sweep3", "overall ROI: back win={:.2f}% lay win={:.2f}% back place={:.2f}% lay place={:.2f}%".format(
            100 * d.ret_bsp.mean(), 100 * d.ret_lay.mean(), 100 * np.nanmean(d.ret_place), 100 * np.nanmean(d.ret_place_lay)))

    conds = {}
    conds.update(base_conditions(d))
    conds.update(extra_conditions(d))
    conds.update(notes_conditions(ctx, d))
    conds = {n: m for n, m in conds.items() if not any(t in n for t in LEAKY)}
    fc = form_conditions(d)
    conds.update(fc)
    names = list(conds)
    M = np.column_stack([conds[n] for n in names]).astype(np.float32)
    emit("sweep3.wf", f"conditions={len(names)} (no-peek form ones={len(fc)}) pairs={len(names) * (len(names) - 1) // 2}")

    b, pb = d.bsp.values, d.place_bsp.values
    bets = {
        "back win": (d.ret_bsp.values, (b - 1) * KEEP),
        "lay win": (d.ret_lay.values, (b - 1)),
        "back place": (d.ret_place.values, (pb - 1) * KEEP),
        "lay place": (d.ret_place_lay.values, (pb - 1)),
    }
    # No-edge worlds: results drawn from the market's own prices.
    rng = np.random.default_rng(11)
    fakes = []
    for _ in range(3):
        g = np.log(d.mkt_p.values) + rng.gumbel(size=len(d))
        won = pd.Series(-g).groupby(d.race_id.values).rank(method="first").values == 1
        placed = rng.random(len(d)) < np.nan_to_num(d.place_q.values, nan=0.0)
        fakes.append({
            "back win": np.where(won, (b - 1) * KEEP, -1.0),
            "lay win": np.where(won, -(b - 1), KEEP),
            "back place": np.where(np.isnan(d.ret_place.values), np.nan, np.where(placed, (pb - 1) * KEEP, -1.0)),
            "lay place": np.where(np.isnan(d.ret_place.values), np.nan, np.where(placed, -(pb - 1), KEEP)),
        })
    return d, names, M, bets, fakes


def run(ctx):
    d, names, M, bets, fakes = prepare(ctx)
    calib(ctx, d)
    mlist = sorted(d.date.dt.to_period("M").unique())
    split = mlist[int(len(mlist) * 0.6)].to_timestamp()
    try:
        harville_part(ctx, d, split)
    except Exception as e:
        emit("sweep3.harville", f"#ERROR {type(e).__name__}: {e}")

    for label, (ret, var) in bets.items():
        res, seen = walk_forward(d, names, M, ret, var)
        n, roi, z = _wf_summary(res)
        ctx.table(f"sweep3.wf.{label}.months", ["month", "patterns_picked", "bets", "profit_units", "var"],
                  [(m, p, nb, round(pr, 1), round(vv, 1)) for m, p, nb, pr, vv in res])
        null = []
        for f in fakes:
            fres, _ = walk_forward(d, names, M, f[label], var)
            null.append(_wf_summary(fres))
        emit(f"sweep3.wf.{label}", f"REAL: bets={n} roi={roi:.2f}% z={z:.2f} | NO-EDGE worlds: " +
             " ; ".join(f"bets={a} roi={b_:.2f}% z={c:.2f}" for a, b_, c in null))
        # Patterns picked in 3+ months, with what they did in the months they were bet.
        rows = sorted(((nm, s[0], s[1], round(100 * s[2] / s[1], 1) if s[1] else None)
                       for nm, s in seen.items() if s[0] >= 3), key=lambda t: -(t[3] or -999))
        ctx.table(f"sweep3.wf.{label}.repeat_picks", ["pattern", "months_picked", "bets_after_picking", "roi_after_picking"], rows, limit=40)
    emit("sweep3", "done")
