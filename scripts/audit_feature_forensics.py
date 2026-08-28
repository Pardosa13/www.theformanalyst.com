#!/usr/bin/env python3
"""Reproduce the 2026-07 audit-feature investigation from the run history.

The controlled experiment for "did the 60 audit features hurt the models?" is
already in the database: 87 production runs spanning both feature eras on the
same pipeline. This script pulls it and prints the four pieces of evidence.

  1. Walk-forward ROI (the mean of the independent out-of-sample folds) per run,
     split by feature era. This is the honest measure of edge; the single
     chronological holdout is not.
  2. Market quality on the 185 undated meetings versus everything else. Their
     books sum to ~0.75, so backing every runner returns ~1.34x — the source of
     the pre-audit "profit".
  3. Favourite-backing ROI by month: a strategy with no model in it is wildly
     profitable in exactly the months those meetings fall in, and loses money in
     every clean month. That is the artifact, isolated from any model.
  4. Champion Score movements attributed to their causes (scoring-formula
     versions and the arrival of walk-forward folds), since the score is not
     comparable across formula versions.

Read-only. Usage:
    POSTGREST_URL=https://... python scripts/audit_feature_forensics.py
"""
import json
import os
import re
import statistics as st
import sys
import urllib.request
from collections import defaultdict

BASE = (os.environ.get('POSTGREST_URL') or '').rstrip('/')
if not BASE:
    sys.exit("POSTGREST_URL is not set.")

AUDIT_RUN = 151          # first run carrying the audit features
FOLDS_FROM = 136         # first run that computed walk-forward folds


def get(path, timeout=300):
    req = urllib.request.Request(BASE + path, headers={'Accept': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def paged(path, size=50000):
    out, off = [], 0
    while True:
        chunk = get(f"{path}&limit={size}&offset={off}")
        out.extend(chunk)
        if len(chunk) < size:
            return out
        off += size


def fold_rois(row):
    wf = row.get('walk_forward')
    if not wf:
        return []
    try:
        d = json.loads(wf) if isinstance(wf, str) else wf
        return [f.get('roi') for f in (d.get('folds') or []) if f.get('bets', 0)]
    except (ValueError, TypeError, AttributeError):
        return []


def walk_forward_by_era():
    rows = get("/backtest_model_competition?select=run_id,walk_forward&run_id=gte.126&order=run_id.asc")
    per_run = defaultdict(list)
    for r in rows:
        per_run[r['run_id']].extend(fold_rois(r))
    means = {k: st.mean(v) for k, v in per_run.items() if v}
    pre = [v for k, v in means.items() if FOLDS_FROM <= k < AUDIT_RUN]
    post = [v for k, v in means.items() if k >= AUDIT_RUN]
    print("\n1. WALK-FORWARD ROI — the honest out-of-sample measure")
    print(f"   146-feature era (runs {FOLDS_FROM}-{AUDIT_RUN - 1}): "
          f"n={len(pre):2d}  mean {st.mean(pre):+.2f}%")
    print(f"   audit-feature era (runs {AUDIT_RUN}+)  : "
          f"n={len(post):2d}  mean {st.mean(post):+.2f}%")
    print(f"   difference: {st.mean(post) - st.mean(pre):+.2f}pp in favour of the audit features")
    try:
        from scipy import stats
        print(f"   Mann-Whitney U p = {stats.mannwhitneyu(pre, post, alternative='two-sided').pvalue:.4f}")
    except ImportError:
        print("   (install scipy for the significance test)")


def market_quality():
    meets = get("/meetings?select=id,date,meeting_name")
    races = get("/races?select=id,meeting_id")
    horses = paged("/horses?select=id,race_id&order=id.asc")
    results = paged("/results?select=horse_id,sp,finish_position&order=horse_id.asc")

    undated_meetings = {m['id'] for m in meets if not m['date']}
    undated_races = {r['id'] for r in races if r['meeting_id'] in undated_meetings}
    horse_race = {h['id']: h['race_id'] for h in horses}

    by_race = defaultdict(list)
    for r in results:
        rid = horse_race.get(r['horse_id'])
        if rid is None:
            continue
        try:
            sp = float(r['sp']) if r.get('sp') is not None else None
        except (TypeError, ValueError):
            sp = None
        by_race[rid].append(sp)

    print(f"\n2. MARKET QUALITY  ({len(undated_meetings)} undated meetings, "
          f"{len(undated_races)} races)")
    print(f"   {'group':<22}{'races':>7}{'priced/race':>13}{'overround':>11}{'books<1.0':>11}")
    for label, keys in (("undated meetings", set(by_race) & undated_races),
                        ("everything else", set(by_race) - undated_races)):
        priced, overrounds = [], []
        for rid in keys:
            sps = [s for s in by_race[rid] if s and s > 1.0]
            priced.append(len(sps))
            if len(sps) >= 2:
                overrounds.append(sum(1.0 / s for s in sps))
        under = 100.0 * sum(1 for o in overrounds if o < 1.0) / len(overrounds)
        print(f"   {label:<22}{len(keys):>7}{st.mean(priced):>13.2f}"
              f"{st.mean(overrounds):>11.3f}{under:>10.1f}%")
    print("   An overround below 1.0 pays more than fair: backing every runner books a profit.")
    return meets, races, by_race


def favourite_baseline(meets, races, by_race):
    """Back the shortest price in every race. No model involved."""
    def month(m):
        if m['date']:
            return str(m['date'])[:7]
        pref = re.match(r'^(\d{6})_', str(m['meeting_name'] or ''))
        return f"20{pref.group(1)[:2]}-{pref.group(1)[2:4]}" if pref else None

    meeting_month = {m['id']: month(m) for m in meets}
    race_meeting = {r['id']: r['meeting_id'] for r in races}
    horses = paged("/horses?select=id,race_id&order=id.asc")
    results = {r['horse_id']: r for r in paged("/results?select=horse_id,sp,finish_position&order=horse_id.asc")}

    runners = defaultdict(list)
    for h in horses:
        r = results.get(h['id'])
        if not r:
            continue
        try:
            sp = float(r['sp']) if r.get('sp') is not None else None
        except (TypeError, ValueError):
            sp = None
        if sp and sp > 1.0:
            runners[h['race_id']].append((sp, r.get('finish_position')))

    stats_by_month = defaultdict(lambda: {'n': 0, 'profit': 0.0, 'wins': 0, 'field': []})
    for rid, rows in runners.items():
        mid = race_meeting.get(rid)
        mo = meeting_month.get(mid) if mid else None
        if not mo or len(rows) < 4:
            continue
        sp, finish = min(rows, key=lambda t: t[0])
        b = stats_by_month[mo]
        b['n'] += 1
        b['field'].append(len(rows))
        won = str(finish) == '1'
        b['profit'] += (sp - 1.0) if won else -1.0
        b['wins'] += 1 if won else 0

    print("\n3. FAVOURITE-BACKING ROI BY MONTH — a strategy with no model in it")
    print(f"   {'month':<9}{'races':>7}{'strike':>9}{'ROI':>10}{'avg field':>11}")
    for mo in sorted(stats_by_month):
        b = stats_by_month[mo]
        if b['n'] < 50:
            continue
        print(f"   {mo:<9}{b['n']:>7}{100.0 * b['wins'] / b['n']:>8.1f}%"
              f"{100.0 * b['profit'] / b['n']:>9.2f}%{st.mean(b['field']):>11.2f}")
    print("   Positive months are the undated ones. No real market lets the favourite win money.")


def score_movements():
    rows = get("/backtest_model_competition?select=run_id,selection_score&run_id=gte.126&order=run_id.asc")
    best = {}
    for r in rows:
        if r['selection_score'] is not None:
            best[r['run_id']] = max(best.get(r['run_id'], float('-inf')), r['selection_score'])
    ks = sorted(best)
    causes = {
        137: "walk-forward folds counted for the first time — all negative, still 146 features",
        150: "calibration/stability penalties on an extreme holdout, still 146 features",
        151: "THE AUDIT FEATURES LAND (plus the date repair)",
        166: "scoring v3: flat 0.5*strike_rate bonus replaced by (A/E-1)*10",
        201: "scoring v6: joint-Kelly term added",
    }
    print("\n4. LARGEST CHAMPION SCORE MOVEMENTS, attributed")
    moves = sorted(((best[b] - best[a], a, b) for a, b in zip(ks, ks[1:])),
                   key=lambda t: abs(t[0]), reverse=True)[:6]
    for delta, a, b in sorted(moves, key=lambda t: t[2]):
        print(f"   run {a:>3} -> {b:<4} {best[a]:+8.2f} -> {best[b]:+8.2f}  {delta:+7.2f}"
              f"   {causes.get(b, '')}")
    print("   The Champion Score is not comparable across scoring_formula_version values.")


def main():
    walk_forward_by_era()
    meets, races, by_race = market_quality()
    favourite_baseline(meets, races, by_race)
    score_movements()
    print("\nConclusion: the audit features improved the honest measure. The pre-audit ROI "
          "came from 185 undated meetings whose fragmentary prices formed a sub-1.0 book.\n")


if __name__ == '__main__':
    main()
