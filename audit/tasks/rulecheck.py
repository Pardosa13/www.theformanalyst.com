"""Why did the month-by-month sweep (sweep3) not pick the Place Bet rule?

Rule: ran 2nd last start + analyzer 'Weight vs Field - Below (2-3kg)',
backed for a place at Betfair place BSP (8% commission).

Uses exactly the same runners, conditions and pick rules as sweep3, and for
each month prints: the rule's own result that month, and what it looked like
on the earlier months the sweep had to pick from (bets, ROI, strength z),
its rank among all patterns, and which pick rule it failed.
"""
import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import bf_files, frame, sweep3
from audit.tasks.hunt2 import extra_conditions, notes_conditions
from audit.tasks.patterns import conditions as base_conditions

A = "last start 2"
B = "an: Weight vs Field - Below (2-3kg)"


def run(ctx):
    d = frame.build(ctx)
    d = d[d.mkt_p.notna() & (d.date >= sweep3.START)].reset_index(drop=True)
    d = bf_files.attach(d, sweep3.MONTHS)
    d["placed"] = d["placed"].fillna(0).astype(int)
    k = sweep3.places_paid(d.field.values)
    has_pb = (d.place_bsp > 1) & (k >= 2)
    ip = np.where(has_pb, 1 / d.place_bsp, np.nan)
    book = pd.Series(ip).groupby(d.race_id.values).transform("sum").values
    n_pb = pd.Series(has_pb.astype(int)).groupby(d.race_id.values).transform("sum").values
    d["place_q"] = np.where(has_pb & (n_pb == d.field.values), np.clip(ip / book * k, 1e-4, 0.999), np.nan)
    raw_ret = d.ret_place.values.copy()
    d.loc[d.place_q.isna(), ["ret_place", "ret_place_lay"]] = np.nan

    conds = {}
    conds.update(base_conditions(d))
    conds.update(extra_conditions(d))
    conds.update(notes_conditions(ctx, d))
    conds = {n: m for n, m in conds.items() if not any(t in n for t in sweep3.LEAKY)}
    conds.update(sweep3.form_conditions(d))
    names = list(conds)
    for nm in (A, B):
        emit("rulecheck", f"condition '{nm}' in sweep: {nm in conds} runners={int(conds[nm].sum()) if nm in conds else 0}")
    if A not in conds or B not in conds:
        return
    rule = conds[A] & conds[B]
    has_raw = np.isfinite(raw_ret)
    has_sw = np.isfinite(d.ret_place.values)
    emit("rulecheck", f"rule runners={int(rule.sum())} with a place price={int((rule & has_raw).sum())} "
                      f"kept by the sweep (whole race place-priced)={int((rule & has_sw).sum())}")
    for label, m in (("all place-priced", rule & has_raw), ("sweep's set", rule & has_sw)):
        r = raw_ret[m]
        emit("rulecheck", f"{label}: bets={m.sum()} placed={100 * d.placed.values[m].mean():.1f}% roi={100 * r.mean():.1f}%")

    M = np.column_stack([conds[n] for n in names]).astype(np.float32)
    ret = d.ret_place.values
    var = (d.place_bsp.values - 1) * sweep3.KEEP
    ok = np.isfinite(ret) & np.isfinite(var) & (var > 0) & (var < 1e4)
    r = np.where(ok, ret, 0.0)
    v = np.where(ok, var, 0.0)
    months = d.date.dt.to_period("M").astype(str).values
    stats = sweep3._month_stats(M, r, v, months, ok)
    order = sorted(stats)
    ia, ib = sorted((names.index(A), names.index(B)))
    iu = np.triu_indices(len(names))
    rows = []
    for t, mo in enumerate(order):
        te = (months == mo) & ok & rule
        own = (mo, int(te.sum()), round(100 * d.placed.values[te].mean(), 1) if te.sum() else None,
               round(100 * r[te].mean(), 1) if te.sum() else None)
        if t < sweep3.WF_FIRST - 1:
            rows.append(own + (None, None, None, None, None, "no test this month (search only)"))
            continue
        N = sum(stats[x][0] for x in order[:t])
        P = sum(stats[x][1] for x in order[:t])
        V = sum(stats[x][2] for x in order[:t])
        with np.errstate(divide="ignore", invalid="ignore"):
            z = P / np.sqrt(V)
            roi = P / N
        n0, z0, roi0 = int(N[ia, ib]), float(z[ia, ib]), float(roi[ia, ib])
        cand = (N[iu] >= sweep3.WF_MIN_SEARCH) & (roi[iu] > 0) & (z[iu] > sweep3.WF_Z)
        stronger = int((cand & (z[iu] > z0)).sum())
        why = []
        if n0 < sweep3.WF_MIN_SEARCH:
            why.append(f"too few earlier bets ({n0} < {sweep3.WF_MIN_SEARCH})")
        if roi0 <= 0:
            why.append("lost money in earlier months")
        if z0 <= sweep3.WF_Z:
            why.append(f"not strong enough (z {z0:.2f} <= {sweep3.WF_Z})")
        if not why and stronger >= sweep3.WF_MAX_PICK:
            why.append(f"passed, but {stronger} stronger patterns filled the {sweep3.WF_MAX_PICK} slots")
        rows.append(own + (n0, round(100 * roi0, 1), round(z0, 2), int(cand.sum()), stronger,
                           "; ".join(why) or "PICKED"))
    ctx.table("rulecheck.months", ["month", "bets_this_month", "placed_%", "roi_this_month",
                                   "earlier_bets", "earlier_roi", "earlier_z", "patterns_passing", "stronger_than_rule",
                                   "verdict"], rows)
