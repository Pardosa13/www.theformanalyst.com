"""How did the six live place rules go in a month they were never fitted on?

The rules (place_bet.RULES) were chosen on 28 Oct 2026 from Feb-Sep results.
Betfair's October file was not out then, so October is unseen data: the
first honest test. The site's own rule code is run over every runner and each
rule is settled at Betfair place SP (8% commission), month by month.

"z" is the result against what the place prices alone predict (0 = no edge,
2+ = unlikely to be luck).
"""
import json
import sys

import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import trending
from audit.tasks.sweep3 import KEEP

sys.path.insert(0, "/app")

FITTED_UNTIL = pd.Timestamp("2026-10-01")


def _load_json(v):
    while isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return {}
    return v if isinstance(v, dict) else {}


def _record(label, period, m, d):
    ret = d.ret_place.values
    m = m & np.isfinite(ret)
    n = int(m.sum())
    if not n:
        return (label, period, 0, None, None, None, None)
    z = trending._z(ret[m], d.place_q.values[m], (d.place_bsp.values[m] - 1) * KEEP)
    return (label, period, n, round(100 * d.placed.values[m].mean(), 1),
            round(100 * float(np.clip(d.place_q.values[m], 0, 1).mean()), 1),
            round(100 * ret[m].mean(), 1), None if z != z else round(z, 2))


def run(ctx):
    import place_bet
    d = trending._prepare(ctx)
    rows = ctx.df("""select h.id horse_id, h.csv_data,
                            (select p.notes from predictions p where p.horse_id = h.id and p.notes is not null
                             order by p.id desc limit 1) notes
                     from horses h where h.id = any(%s)""", (list(map(int, d.horse_id.values)),))
    info = {int(r.horse_id): (r.csv_data, r.notes) for r in rows.itertuples(index=False)}
    live = {rule.key: np.zeros(len(d), bool) for rule in place_bet.RULES}
    for i, hid in enumerate(d.horse_id.values):
        csv_data, notes = info.get(int(hid), ({}, None))
        for rule in place_bet.rules_for(_load_json(csv_data), notes):
            live[rule.key][i] = True

    fitted = (d.date < FITTED_UNTIL).values
    months = d.date.dt.to_period("M").astype(str).values
    emit("octcheck", f"runners={len(d)} fitted Feb-Sep={int(fitted.sum())} unseen Oct={int((~fitted).sum())} "
                     f"all runners place roi: fitted={100 * np.nanmean(d.ret_place.values[fitted]):.1f}% "
                     f"unseen={100 * np.nanmean(d.ret_place.values[~fitted]):.1f}%")
    cols = ["rule", "period", "bets", "placed_%", "price_says_%", "roi_place", "z"]
    out = []
    any_rule = np.zeros(len(d), bool)
    for rule in place_bet.RULES:
        m = live[rule.key]
        any_rule |= m
        out.append(_record(rule.key, "Feb-Sep (fitted)", m & fitted, d))
        out.append(_record(rule.key, "Oct (unseen)", m & ~fitted, d))
    out.append(_record("ANY RULE (one bet per runner)", "Feb-Sep (fitted)", any_rule & fitted, d))
    out.append(_record("ANY RULE (one bet per runner)", "Oct (unseen)", any_rule & ~fitted, d))
    ctx.table("octcheck.rules", cols, out)
    by_month = [_record("ANY RULE", mo, any_rule & (months == mo), d) for mo in sorted(set(months))]
    ctx.table("octcheck.any_rule_by_month", cols, by_month)

    # Same question for late firmers backed for the place (the audit's strongest lead).
    firmed = (np.log(d.pre_wap.where(d.pre_wap > 1) / d.bsp) >= 0.1).values
    close = (d.last_margin < 1).values
    extra = []
    for label, m in (("firmed 10%+ (win price), place bet", firmed),
                     ("firmed 10%+ AND within 1L last start, place bet", firmed & close)):
        extra.append(_record(label, "Feb-Sep", m & fitted, d))
        extra.append(_record(label, "Oct (unseen)", m & ~fitted, d))
    ctx.table("octcheck.firmers", cols, extra)
    emit("octcheck", "done")
