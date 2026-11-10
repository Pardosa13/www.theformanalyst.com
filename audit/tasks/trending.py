"""Do "trending" jockeys, trainers and combos make money?

A runner is "trending" when its jockey, trainer, jockey+trainer pair, or
jockey/trainer at this track has been hot lately. Everything is worked out
from races on EARLIER dates only, the way the site would see it on race
morning. Four kinds of "hot" are tested over the last 14, 30 and 90 days and
all-time:

  strike   win strike rate at or above a level
  beat     won more than the market expected (actual / expected wins)
  profit   backing every runner at Betfair SP made money
  streak   winners in the last 7 days (a stable "catching fire")

Each rule is judged three ways: win bet at Betfair SP, win bet at the
bookmaker SP, and place bet at Betfair place SP (8% commission on Betfair).
"z" is how far the result sits from what the prices alone predict; the same
rules are re-run on made-up results drawn from the market's own prices, to
show how many look good by luck.
"""
import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import bf_files, frame
from audit.tasks.sweep3 import KEEP, MONTHS, START, _before, places_paid

MIN_BETS = 150
MIN_MONTH_BETS = 15
# (window days or None for all-time, label, min earlier runs per key type)
WINDOWS = (
    (14, "last 14d", {"jockey": 15, "trainer": 8, "jockey+trainer": 4, "trainer@track": 4, "jockey@track": 5}),
    (30, "last 30d", {"jockey": 30, "trainer": 15, "jockey+trainer": 6, "trainer@track": 6, "jockey@track": 8}),
    (90, "last 90d", {"jockey": 60, "trainer": 30, "jockey+trainer": 10, "trainer@track": 10, "jockey@track": 12}),
    (None, "all-time", {"jockey": 100, "trainer": 60, "jockey+trainer": 20, "trainer@track": 20, "jockey@track": 25}),
)


def _prepare(ctx):
    d = frame.build(ctx)
    d = d[d.mkt_p.notna() & (d.date >= START)].reset_index(drop=True)
    d = bf_files.attach(d, MONTHS)
    d["placed"] = d["placed"].fillna(0).astype(int)
    k = places_paid(d.field.values)
    has_pb = (d.place_bsp > 1) & (k >= 2)
    ip = np.where(has_pb, 1 / d.place_bsp, np.nan)
    book = pd.Series(ip).groupby(d.race_id.values).transform("sum").values
    n_pb = pd.Series(has_pb.astype(int)).groupby(d.race_id.values).transform("sum").values
    d["place_q"] = np.where(has_pb & (n_pb == d.field.values), np.clip(ip / book * k, 1e-4, 0.999), np.nan)
    d.loc[d.place_q.isna(), "ret_place"] = np.nan
    return d


def _rules(d):
    """{rule name: mask}. Every number comes from earlier dates only."""
    s = lambda col: d[col].fillna("").astype(str).str.strip().str.lower()
    J, T, K = s("jockey"), s("trainer"), d.track.fillna("").astype(str)
    both = lambda x, y, sep: (x + sep + y).where((x != "") & (y != ""), "")
    keys = {"jockey": J, "trainer": T, "jockey+trainer": both(J, T, "+"),
            "trainer@track": both(T, K, "@"), "jockey@track": both(J, K, "@")}
    won = d.won.values.astype(float)
    ret = np.nan_to_num(d.ret_bsp.values, nan=0.0)
    ones = np.ones(len(d))
    rules = {}

    def add(name, m):
        m = np.asarray(m, bool)
        if m.sum() >= MIN_BETS:
            rules[name] = m

    for label, key in keys.items():
        named = (key != "").values
        for window, wl, floors in WINDOWS:
            n, a, e = _before(d, key, won, d.mkt_p.values, window)
            _, profit, _ = _before(d, key, ret, ones, window)
            enough = named & (n >= floors[label])
            with np.errstate(divide="ignore", invalid="ignore"):
                sr = np.where(n > 0, a / n, 0.0)
                ae = (a + 1) / (e + 1)
                roi = np.where(n > 0, profit / n, 0.0)
            for lo in (0.20, 0.25, 0.33):
                add(f"{label} {wl}: strike {int(lo * 100)}%+", enough & (sr >= lo))
            for lo in (1.3, 1.6):
                add(f"{label} {wl}: beat market x{lo}+", enough & (ae >= lo))
            for lo in (0.2, 0.5):
                add(f"{label} {wl}: profit {int(lo * 100)}%+ at BSP", enough & (roi >= lo))
            add(f"{label} {wl}: COLD under market x0.6", enough & (ae < 0.6))
            add(f"{label} {wl}: COLD strike under 5%", enough & (sr < 0.05))
    # "Catching fire": winners in the last 7 days.
    for label in ("jockey", "trainer", "jockey+trainer"):
        n, a, _ = _before(d, keys[label], won, d.mkt_p.values, 7)
        named = (keys[label] != "").values
        for wins in ((4, 6) if label == "jockey" else (2, 3) if label == "trainer" else (2,)):
            add(f"{label} last 7d: {wins}+ winners", named & (a >= wins))
    # Hot jockey on a hot trainer's horse.
    nj, aj, ej = _before(d, J, won, d.mkt_p.values, 30)
    nt, at, et = _before(d, T, won, d.mkt_p.values, 30)
    add("hot jockey AND hot trainer (both beat market x1.3+, last 30d)",
        (nj >= 30) & (nt >= 15) & ((aj + 1) / (ej + 1) >= 1.3) & ((at + 1) / (et + 1) >= 1.3))
    return rules


def _z(ret, p, win_amount):
    """Result vs what the prices predict, in standard deviations."""
    ok = np.isfinite(ret) & np.isfinite(p) & np.isfinite(win_amount)
    if ok.sum() < 30:
        return np.nan
    r, q, w = ret[ok], p[ok], win_amount[ok]
    mu = q * w - (1 - q)
    var = q * w ** 2 + (1 - q) - mu ** 2
    return float((r.sum() - mu.sum()) / np.sqrt(var.sum()))


def _row(name, d, m, months, half):
    x = d[m]
    b = x.bsp.values
    win_z = _z(x.ret_bsp.values, x.mkt_p.values, (b - 1) * KEEP)
    pm = np.isfinite(x.ret_place.values)
    place_z = _z(x.ret_place.values, x.place_q.values, (x.place_bsp.values - 1) * KEEP)
    mo = months[m]
    up = tot = 0
    for v in np.unique(mo):
        r = x.ret_bsp.values[mo == v]
        if len(r) >= MIN_MONTH_BETS:
            tot += 1
            up += int(np.nansum(r) > 0)
    h = half[m]
    pct = lambda v: round(100 * float(v), 1) if np.isfinite(v) else None
    return (name, int(m.sum()), pct(x.won.mean()), pct(x.mkt_p.mean()), round(float(np.nanmedian(b)), 1),
            pct(np.nanmean(x.ret_bsp.values)), pct(np.nanmean(x.ret_sp.values)),
            None if win_z != win_z else round(win_z, 2), f"{up}/{tot}",
            pct(np.nanmean(x.ret_bsp.values[~h])) if (~h).sum() >= 30 else None,
            pct(np.nanmean(x.ret_bsp.values[h])) if h.sum() >= 30 else None,
            int(pm.sum()), pct(x.placed.values[pm].mean()) if pm.sum() else None,
            pct(np.nanmean(x.ret_place.values)) if pm.sum() else None,
            None if place_z != place_z else round(place_z, 2))


COLS = ["rule", "bets", "win_%", "market_says_%", "median_bsp", "roi_bsp", "roi_sp", "z_win", "months_up",
        "roi_1st_half", "roi_2nd_half", "place_bets", "placed_%", "roi_place", "z_place"]


def run(ctx):
    d = _prepare(ctx)
    emit("trending", f"runners={len(d)} races={d.race_id.nunique()} {d.date.min().date()}..{d.date.max().date()} "
                     f"all runners: roi_bsp={100 * d.ret_bsp.mean():.1f}% roi_sp={100 * d.ret_sp.mean():.1f}% "
                     f"roi_place={100 * np.nanmean(d.ret_place):.1f}%")
    rules = _rules(d)
    months = d.date.dt.to_period("M").astype(str).values
    half = (d.date >= d.date.quantile(0.5)).values
    rows = [_row(name, d, m, months, half) for name, m in rules.items()]
    ctx.table("trending.all", COLS, rows, limit=400)
    for title, key in (("trending.best_win", 7), ("trending.best_place", 14)):
        best = sorted((r for r in rows if r[key] is not None), key=lambda r: -r[key])[:25]
        ctx.table(title, COLS, best)

    # Luck check: same rules, results re-drawn from the market's own prices.
    b, pb = d.bsp.values, d.place_bsp.values
    real_w = np.array([r[7] if r[7] is not None else np.nan for r in rows])
    real_p = np.array([r[14] if r[14] is not None else np.nan for r in rows])
    emit("trending.luck", f"rules={len(rows)} REAL: z_win>=2: {int((real_w >= 2).sum())}, z_win>=3: {int((real_w >= 3).sum())}, "
                          f"z_win<=-2: {int((real_w <= -2).sum())} | z_place>=2: {int((real_p >= 2).sum())}, "
                          f"z_place>=3: {int((real_p >= 3).sum())}, z_place<=-2: {int((real_p <= -2).sum())}")
    rng = np.random.default_rng(23)
    for world in range(4):
        g = np.log(d.mkt_p.values) + rng.gumbel(size=len(d))
        won = pd.Series(-g).groupby(d.race_id.values).rank(method="first").values == 1
        placed = rng.random(len(d)) < np.nan_to_num(d.place_q.values, nan=0.0)
        fw = np.where(won, (b - 1) * KEEP, -1.0)
        fp = np.where(np.isnan(d.ret_place.values), np.nan, np.where(placed, (pb - 1) * KEEP, -1.0))
        zw = np.array([_z(fw[m], d.mkt_p.values[m], (b[m] - 1) * KEEP) for m in rules.values()])
        zp = np.array([_z(fp[m], d.place_q.values[m], (pb[m] - 1) * KEEP) for m in rules.values()])
        emit("trending.luck", f"NO-EDGE world {world}: z_win>=2: {int((zw >= 2).sum())}, z_win>=3: {int((zw >= 3).sum())}, "
                              f"z_win<=-2: {int((zw <= -2).sum())} | z_place>=2: {int((zp >= 2).sum())}, "
                              f"z_place>=3: {int((zp >= 3).sum())}, z_place<=-2: {int((zp <= -2).sum())}")
    emit("trending", "done")
