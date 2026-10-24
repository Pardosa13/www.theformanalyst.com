"""Hunt for profitable angles, judged at Betfair SP (after 8% commission).

Search period: Feb-Jun. Test period: Jul-Sep (never used to pick).
Each bet's luck is measured against the market's own probability, so a
longshot pattern needs much more proof than a favourite one.
A null run (winners re-drawn from the market odds, so no real edge) shows
how many "finds" pure luck produces.
"""
import itertools

import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import frame

SPLIT = pd.Timestamp("2026-07-01")
MIN_BETS = 150


def stats(ret, p, bsp, mask, lay=False):
    n = int(mask.sum())
    if n == 0:
        return n, np.nan, np.nan
    r = ret[mask]
    pp, b = p[mask], bsp[mask]
    if lay:
        mu = pp * -(b - 1) + (1 - pp) * 0.92
        var = pp * (b - 1) ** 2 + (1 - pp) * 0.92 ** 2 - mu ** 2
    else:
        mu = pp * (b - 1) * 0.92 - (1 - pp)
        var = pp * ((b - 1) * 0.92) ** 2 + (1 - pp) - mu ** 2
    z = (r.sum() - mu.sum()) / np.sqrt(max(var.sum(), 1e-9))
    return n, 100 * r.mean(), z


def conditions(d):
    c = {}
    def add(name, m):
        m = np.asarray(m, dtype=bool)
        if m.sum() >= MIN_BETS:
            c[name] = m
    for lo, hi in [(1, 2), (2, 3), (3, 4), (4, 6), (6, 10), (10, 20), (20, 50), (50, 1000)]:
        add(f"bsp {lo}-{hi}", (d.bsp >= lo) & (d.bsp < hi))
    for k in range(1, 6):
        add(f"mkt rank {k}", d.bsp_rank == k)
    for k in range(1, 5):
        add(f"analyzer rank {k}", d.a_rank == k)
        add(f"pfai rank {k}", d.pfai_rank == k)
        add(f"time rank {k}", d.time_rank == k)
        add(f"wclass rank {k}", d.wc_rank == k)
    for lo, hi in [(1, 4), (4, 7), (7, 10), (10, 30)]:
        add(f"barrier {lo}-{hi-1}", (d.barrier >= lo) & (d.barrier < hi))
    for lo, hi in [(0, 1100), (1100, 1300), (1300, 1500), (1500, 1800), (1800, 2200), (2200, 4000)]:
        add(f"dist {lo}-{hi}", (d.dist >= lo) & (d.dist < hi))
    for lo, hi in [(2, 7), (7, 9), (9, 11), (11, 13), (13, 30)]:
        add(f"field {lo}-{hi-1}", (d.field >= lo) & (d.field < hi))
    for a in range(2, 8):
        add(f"age {a}", d.age == a)
    for s in d.sex.dropna().unique():
        add(f"sex {s}", d.sex == s)
    add("first starter", d.career_starts == 0)
    add("starts 1-3", d.career_starts.between(1, 3))
    add("starts 20+", d.career_starts >= 20)
    add("maiden (0 wins, 1+ starts)", (d.career_wins == 0) & (d.career_starts > 0))
    for lo, hi in [(0, 15), (15, 29), (29, 60), (60, 120), (120, 2000)]:
        add(f"days since {lo}-{hi}", (d.days_since >= lo) & (d.days_since < hi))
    for k in (1, 2, 3):
        add(f"last start {k}", d.last_pos == k)
    add("last start 4-6", d.last_pos.between(4, 6))
    add("last start 7+", d.last_pos >= 7)
    add("last margin <1L", d.last_margin < 1)
    add("last margin 5L+", d.last_margin >= 5)
    lp = d.last_price / d.bsp
    add("shorter than last start x0.5", lp > 2)
    add("drifted vs last start x2", lp < 0.5)
    add("dist up 200m+", d.dist - d.last_dist >= 200)
    add("dist down 200m+", d.last_dist - d.dist >= 200)
    for cond in ("good", "soft", "heavy", "firm", "synthetic"):
        add(f"cond {cond}", d.track_condition.fillna("").str.lower().str.contains(cond))
    add("claim apprentice", d.claim > 0)
    for lo, hi in [(0, 54), (54, 56), (56, 58), (58, 70)]:
        add(f"weight {lo}-{hi}", (d.weight >= lo) & (d.weight < hi))
    for k in range(1, 11):
        add(f"race no {k}", d.race_number == k)
    add("race no 11+", d.race_number >= 11)
    add("settle leader (pf)", d.settle == 1)
    add("settle 2-3 (pf)", d.settle.between(2, 3))
    add("settle back half (pf)", d.settle >= d.field / 2)
    for st in d.run_style.dropna().unique():
        add(f"run style {st}", d.run_style == st)
    # Rated price overlays (pre-race ratings vs market price)
    for name, col in (("pfai", "pfai_price"), ("neural", "neural_price"), ("assessed", "assessed_price"), ("wclass", "wc_price"), ("analyzer", "a_prob")):
        ratio = (d.bsp / d[col]) if col != "a_prob" else (d.bsp * d[col])
        for lo, hi in [(0, 0.67), (0.67, 0.9), (0.9, 1.1), (1.1, 1.5), (1.5, 2.5), (2.5, 100)]:
            add(f"{name} bsp/rated {lo}-{hi}", (ratio >= lo) & (ratio < hi))
    for col, top in (("jockey", 25), ("trainer", 25), ("track", 30)):
        for v in d[col].value_counts().head(top).index:
            add(f"{col} {v}", d[col] == v)
    for m in sorted(d.date.dt.month.unique()):
        add(f"month {m}", d.date.dt.month == m)
    return c


def scan(d, conds, ret_col, lay, label):
    tr = (d.date < SPLIT).values
    te = ~tr
    ret = d[ret_col].values
    p = d.mkt_p.values
    b = d.bsp.values
    names = list(conds)
    rows = []
    for nm in names:
        m = conds[nm]
        rows.append((nm, m))
    # pairs among the singles
    for a, bb in itertools.combinations(names, 2):
        if a.split(" ")[0] == bb.split(" ")[0]:
            continue
        m = conds[a] & conds[bb]
        if m.sum() >= MIN_BETS:
            rows.append((f"{a} + {bb}", m))
    out = []
    for nm, m in rows:
        n1, r1, z1 = stats(ret, p, b, m & tr, lay)
        if n1 < MIN_BETS * 0.6 or not (z1 > 2.0 and r1 > 0):
            continue
        n2, r2, z2 = stats(ret, p, b, m & te, lay)
        out.append((nm, n1, r1, z1, n2, r2, z2))
    emit(f"pat.{label}", f"#tested {len(rows)} conditions; passed search (ROI>0, z>2): {len(out)}")
    return pd.DataFrame(out, columns=["pattern", "n_search", "roi_search", "z_search", "n_test", "roi_test", "z_test"])


def run(ctx):
    d = frame.build(ctx)
    d = d[d.mkt_p.notna() & (d.date >= pd.Timestamp("2026-02-01"))].reset_index(drop=True)
    emit("pat", f"usable runners={len(d)} races={d.race_id.nunique()} {d.date.min().date()}..{d.date.max().date()}")
    emit("pat", f"coverage pfai={d.pfai_price.notna().mean():.2f} assessed={d.assessed_price.notna().mean():.2f} a_prob={d.a_prob.notna().mean():.2f} a_score={d.a_score.notna().mean():.2f} settle={d.settle.notna().mean():.2f} days_since={d.days_since.notna().mean():.2f} starts={d.career_starts.notna().mean():.2f} last_price={d.last_price.notna().mean():.2f}")
    emit("pat", f"overall back@BSP roi={100*d.ret_bsp.mean():.2f}% lay@BSP roi={100*d.ret_lay.mean():.2f}%")

    conds = conditions(d)
    emit("pat", f"single conditions={len(conds)}")
    for ret_col, lay, label in (("ret_bsp", False, "back"), ("ret_lay", True, "lay")):
        res = scan(d, conds, ret_col, lay, label)
        res["both"] = (res.roi_test > 0) & (res.z_test > 1.0)
        emit(f"pat.{label}", f"#held up in test (ROI>0, z>1): {int(res.both.sum())}")
        ctx.dframe(f"pat.{label}.held", res[res.both].sort_values("z_test", ascending=False).round(2), limit=60)
        ctx.dframe(f"pat.{label}.top_search", res.sort_values("z_search", ascending=False).round(2), limit=40)

        # Null benchmark: winners re-drawn from market probabilities.
        rng = np.random.default_rng(7)
        null_counts = []
        for _ in range(2):
            nd = d.copy()
            u = rng.random(len(nd))
            cum = nd.groupby("race_id")["mkt_p"].cumsum().values
            start = cum - nd.mkt_p.values
            ru = nd.groupby("race_id")["race_id"].transform(lambda s: rng.random()).values
            nd["won"] = ((ru >= start) & (ru < cum)).astype(int)
            nd["ret_bsp"] = np.where(nd.won == 1, (nd.bsp - 1) * 0.92, -1.0)
            nd["ret_lay"] = np.where(nd.won == 1, -(nd.bsp - 1), 0.92)
            nr = scan(nd, conds, ret_col, lay, f"{label}.null")
            null_counts.append(int(((nr.roi_test > 0) & (nr.z_test > 1.0)).sum()))
        emit(f"pat.{label}", f"#NULL (no-edge world) 'held up' counts: {null_counts}")
