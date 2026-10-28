"""Do the live place rules (place_bet.py) pick exactly the horses the audit tested?

1. For every past runner the sweep used, run the site's own rule code
   (place_bet.rule_matches on the saved form row and analyzer notes) and
   compare with the audit's condition pairs. Prints matches, mismatches and
   examples of any mismatch, plus each rule's record from the live code.
2. "Finished within 1 length last start" read the saved 'form margin', which
   held an older run for many horses until 28 Oct 2026. Re-test that rule on
   the TRUE last-start margin, taken from the newest past run in the
   meeting's saved form file, and show how often the saved margin was right.
"""
import csv
import io
import json
import sys

import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import sweep3

sys.path.insert(0, "/app")

PAIRS = {
    'place_2nd_light': ('last start 2', 'an: Weight vs Field - Below (2-3kg)'),
    'place_close_trackpod': ('last margin <1L', 'an: Track Podium Rate - Strong (55-69%)'),
    'place_32_tdnowin': ('an: Track+Distance Win Rate - No Wins', 'an: Ran places: 3rd 2nd'),
    'place_32_poortrack': ('an: Track - Poor Performance', 'an: Ran places: 3rd 2nd'),
    'place_goingpod_backsprint': ('an: Condition Podium Rate - Strong (55-69%)', 'an: Running Position - Backmarker Sprint'),
    'place_won_onpace_mile': ('last start 1', 'an: Running Position - OnPace Mile'),
}


def _load_json(v):
    while isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return {}
    return v if isinstance(v, dict) else {}


def _record(ctx, label, mask, d):
    """Bets, place %, ROI and months up on the sweep's place-priced set."""
    ret = d.ret_place.values
    m = mask & np.isfinite(ret)
    n = int(m.sum())
    if not n:
        return (label, 0, None, None, None)
    months = d.date.dt.to_period("M").astype(str).values
    by = pd.DataFrame({"mo": months[m], "r": ret[m]}).groupby("mo")["r"].agg(["sum", "count"])
    active = by[by["count"] >= 10]
    return (label, n, round(100 * d.placed.values[m].mean(), 1), round(100 * ret[m].mean(), 1),
            f"{int((active['sum'] > 0).sum())}/{len(active)}")


def true_last_margins(ctx, d):
    """horse row -> raw 'form margin' of its newest past run in the meeting form file."""
    from past_runs import horse_key, parse_date
    want = {}
    for i, (mid, rn, name) in enumerate(zip(d.meeting_id.values, d.race_number.values, d.horse_name.values)):
        want.setdefault(int(mid), {})[horse_key(rn, name)] = i
    out = np.full(len(d), np.nan)
    found = np.zeros(len(d), bool)
    ids = list(want)
    for start in range(0, len(ids), 25):
        with ctx.conn.cursor() as cur:
            cur.execute("select id, date, csv_data from meetings where id = any(%s)", (ids[start:start + 25],))
            batch = cur.fetchall()
        for mid, mdate, text in batch:
            keys = want.get(mid) or {}
            if not text or not isinstance(text, str):
                continue
            best = {}
            try:
                for row in csv.DictReader(io.StringIO(text)):
                    row = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
                    key = horse_key(row.get('race number'), row.get('horse name'))
                    if key not in keys:
                        continue
                    run_date = parse_date(row.get('form meeting date'))
                    if run_date is None or (mdate and run_date >= mdate):
                        continue
                    if key not in best or run_date > best[key][0]:
                        best[key] = (run_date, row.get('form margin'))
            except Exception:
                continue
            for key, (_dt, margin) in best.items():
                i = keys[key]
                found[i] = True
                try:
                    out[i] = float(str(margin).replace('$', '').replace(',', '').strip())
                except (TypeError, ValueError):
                    pass
    return out, found


def run(ctx):
    import place_bet
    d, names, M, bets, fakes = sweep3.prepare(ctx, report=False)
    col = {n: i for i, n in enumerate(names)}
    missing = [n for pair in PAIRS.values() for n in pair if n not in col]
    if missing:
        emit("routecheck", f"#ERROR audit conditions not found: {missing}")
        return
    # The site's own inputs: saved form row and the latest prediction's notes (as the audit used).
    rows = ctx.df("""select h.id horse_id, h.csv_data,
                            (select p.notes from predictions p where p.horse_id = h.id and p.notes is not null order by p.id desc limit 1) notes
                     from horses h where h.id = any(%s)""", (list(map(int, d.horse_id.values)),))
    info = {int(r.horse_id): (r.csv_data, r.notes) for r in rows.itertuples(index=False)}
    comps = {hid: place_bet.note_components(notes) for hid, (_c, notes) in info.items()}
    live = {key: np.zeros(len(d), bool) for key in PAIRS}
    for i, hid in enumerate(d.horse_id.values):
        csv_data, _notes = info.get(int(hid), ({}, None))
        csv_data = _load_json(csv_data)
        for rule in place_bet.RULES:
            live[rule.key][i] = place_bet.rule_matches(rule, csv_data, comps.get(int(hid), set()))

    table, recs = [], []
    for rule in place_bet.RULES:
        a, b = PAIRS[rule.key]
        audit = (M[:, col[a]] > 0) & (M[:, col[b]] > 0)
        lv = live[rule.key]
        both, a_only, l_only = int((audit & lv).sum()), int((audit & ~lv).sum()), int((~audit & lv).sum())
        table.append((rule.key, int(audit.sum()), int(lv.sum()), both, a_only, l_only,
                      round(100 * both / max(audit.sum(), 1), 2)))
        recs.append(_record(ctx, f"{rule.key} (audit pair)", audit, d))
        recs.append(_record(ctx, f"{rule.key} (live code)", lv, d))
        for tag, mm in (("audit only", audit & ~lv), ("live only", ~audit & lv)):
            for i in np.nonzero(mm)[0][:5]:
                hid = int(d.horse_id.values[i])
                csv_data = _load_json(info.get(hid, ({}, None))[0])
                emit(f"routecheck.diff.{rule.key}", f"{tag} horse_id={hid} {d.date.values[i]!s:.10} last10={csv_data.get('horse last10')!r} "
                     f"margin={csv_data.get('form margin')!r} live_components={sorted(c for c in comps.get(hid, set()) if any(x in c for x in rule.components))}")
    ctx.table("routecheck.agreement", ["rule", "audit_picks", "live_code_picks", "both", "audit_only", "live_only", "agree_%"], table)
    ctx.table("routecheck.records", ["rule", "bets", "placed_%", "roi_place", "months_up"], recs)

    # 2. The margin rule on the true last-start margin.
    true_margin, found = true_last_margins(ctx, d)
    saved = d.last_margin.values
    both_known = found & np.isfinite(true_margin) & np.isfinite(saved)
    months = d.date.dt.to_period("M").astype(str).values
    agree_rows = []
    for mo in sorted(set(months)):
        m = both_known & (months == mo)
        if m.sum():
            agree_rows.append((mo, int(m.sum()), round(100 * np.mean(np.isclose(saved[m], true_margin[m])), 1)))
    ctx.table("routecheck.margin_saved_vs_true", ["month", "runners", "saved_margin_correct_%"], agree_rows)
    podium = M[:, col['an: Track Podium Rate - Strong (55-69%)']] > 0
    ctx.table("routecheck.margin_rule", ["rule", "bets", "placed_%", "roi_place", "months_up"], [
        _record(ctx, "saved margin <1L + track podium (what the audit tested)", (saved < 1) & podium, d),
        _record(ctx, "TRUE margin <1L + track podium (what the site does from 28 Oct)", (true_margin < 1) & podium, d),
        _record(ctx, "no true margin found", ~found & podium, d),
    ])
    emit("routecheck", "done")
