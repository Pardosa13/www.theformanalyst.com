#!/usr/bin/env python3
"""Compare the pre-2026-07-21 (146-era) feature set against the current one.

WHY THIS EXISTS
---------------
The obvious experiment — `git checkout` the pre-audit backtest.py and run it —
does NOT isolate the audit features, because that file also carries:

  1. The undated-meeting bug. 185 manually-uploaded meetings had NULL dates.
     They sorted as NaT past every chronological cutoff and landed 100% in the
     validation split, carrying ~3.9 priced runners/race at overround 0.74 —
     a "market" that pays ~26% above fair. Commit 9491ea7 repaired the dates
     and excluded the still-undated rows. Any ROI the old file reports on its
     validation set is measured partly on that fabricated book.

  2. Champion Score formula v2, which was `blended_roi + 0.5 * strike_rate`.
     Strike rate is a 0-100 number, so a 30% strike rate contributed a flat
     +15 points to EVERY model, unconditionally. The current v6 formula
     replaced that with `(a_e_ratio - 1.0) * 10`, which is centred on zero.
     A v2 score and a v6 score are different units; comparing them directly
     is meaningless.

So this script instead runs the CURRENT backtest.py twice against the SAME
data, changing exactly one variable: whether the 61 audit features are in the
training matrix. Same date repair, same splits, same scoring formula.

USAGE
-----
    DATABASE_URL=postgresql://...  python scripts/compare_audit_feature_sets.py

SAFETY
------
backtest.py has no dry-run mode and several code paths can activate a champion
(save_best_model_to_db, _replace_unusable_champion, _heal_stale_champion,
ensure_champion_exists_after_run). Point DATABASE_URL at a RESTORED SNAPSHOT,
not production — otherwise the deliberately-crippled 136-feature ablation model
can be promoted as champion. This script refuses to run unless you have
acknowledged that with ALLOW_NONPROD_WRITES=1.
"""
import json
import os
import subprocess
import sys
from datetime import datetime

from sqlalchemy import create_engine, text

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _engine():
    url = os.environ['DATABASE_URL']
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return create_engine(url, pool_pre_ping=True)


def _max_run_id(engine):
    with engine.connect() as conn:
        row = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM backtest_runs")).fetchone()
    return int(row[0])


def _competition_rows(engine, run_id):
    """Every Track E candidate scored in this run, best Champion Score first."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT model_type, model_name, selection_score, validation_roi,
                   validation_strike_rate, validation_bets, log_loss,
                   brier_score, walk_forward
            FROM backtest_model_competition
            WHERE run_id = :rid
            ORDER BY selection_score DESC NULLS LAST
        """), {'rid': run_id}).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        wf = d.pop('walk_forward', None)
        folds = []
        if wf:
            try:
                folds = [f.get('roi') for f in (json.loads(wf).get('folds') or [])]
            except (ValueError, TypeError, AttributeError):
                folds = []
        d['walk_forward_fold_rois'] = folds
        d['walk_forward_mean_roi'] = (sum(folds) / len(folds)) if folds else None
        out.append(d)
    return out


def run_variant(label, ablate, outdir, engine):
    env = dict(os.environ)
    env['ABLATE_AUDIT_FEATURES'] = '1' if ablate else '0'
    env['ML_MODEL_VERSION'] = f"diag_{label}_{datetime.utcnow():%Y%m%d%H%M}"
    logpath = os.path.join(outdir, f'{label}.log')
    print(f"\n=== running variant: {label} "
          f"({'136 pre-audit features' if ablate else '197 current features'}) ===")
    print(f"    log -> {logpath}")

    before = _max_run_id(engine)
    with open(logpath, 'w') as fh:
        rc = subprocess.call([sys.executable, os.path.join(REPO, 'backtest.py')],
                             cwd=REPO, env=env, stdout=fh, stderr=subprocess.STDOUT)
    after = _max_run_id(engine)
    print(f"    exit code: {rc}")

    run_id = after if after > before else None
    if run_id is None:
        print("    WARNING: no new backtest_runs row — the run did not get far "
              "enough to record anything. Check the log.")
    return {
        'label': label,
        'ablate': ablate,
        'feature_set': '136 (pre-audit)' if ablate else '197 (current)',
        'exit_code': rc,
        'log': logpath,
        'run_id': run_id,
        'candidates': _competition_rows(engine, run_id) if run_id else [],
    }


def _fmt(v, spec='.2f'):
    return 'n/a' if v is None else format(v, spec)


def report(results):
    print("\n" + "=" * 78)
    print("CHAMPION SCORE / ROI / STRIKE RATE — identical data, features the only variable")
    print("=" * 78)
    for res in results:
        print(f"\n{res['label']}  [{res['feature_set']}]  run_id={res['run_id']}")
        if not res['candidates']:
            print("  (no candidates recorded)")
            continue
        print(f"  {'model':<22}{'ChampScore':>11}{'holdoutROI':>12}"
              f"{'SR%':>8}{'bets':>7}{'wfMeanROI':>11}  folds")
        for c in res['candidates']:
            print(f"  {str(c['model_name'])[:21]:<22}"
                  f"{_fmt(c['selection_score']):>11}"
                  f"{_fmt(c['validation_roi']):>12}"
                  f"{_fmt(c['validation_strike_rate']):>8}"
                  f"{_fmt(c['validation_bets'], 'd'):>7}"
                  f"{_fmt(c['walk_forward_mean_roi']):>11}"
                  f"  {[round(f, 1) for f in c['walk_forward_fold_rois']]}")

    best = {}
    for res in results:
        scored = [c for c in res['candidates'] if c['selection_score'] is not None]
        best[res['label']] = max(scored, key=lambda c: c['selection_score']) if scored else None

    cur, pre = best.get('current_207'), best.get('preaudit_146')
    print("\n" + "-" * 78)
    if cur and pre:
        delta = cur['selection_score'] - pre['selection_score']
        print(f"Best Champion Score  current(197) {cur['selection_score']:.2f}  "
              f"vs  pre-audit(136) {pre['selection_score']:.2f}   delta {delta:+.2f}")
        if delta > 1.0:
            verdict = ("The audit features HELP. Dropping them scores worse, so they are "
                       "not the cause of the recent bad models — investigate the data/pipeline "
                       "changes after run 136 instead.")
        elif delta < -1.0:
            verdict = ("The audit features HURT on this data. Rolling them back and "
                       "re-promoting from the 136-feature set is justified.")
        else:
            verdict = ("No meaningful difference (within the +/-1.0 promotion edge). The "
                       "features are not what changed — look at the data and the pipeline.")
        print("\nVERDICT: " + verdict)
        if (cur['walk_forward_mean_roi'] or 0) < 0 and (pre['walk_forward_mean_roi'] or 0) < 0:
            print("\nNOTE: both feature sets have a NEGATIVE walk-forward mean ROI. Neither "
                  "has demonstrable out-of-sample edge on this data, whatever the "
                  "Champion Scores say relative to each other.")
    else:
        print("Could not compare: at least one variant produced no scored candidate.")
    print("-" * 78)


def main():
    if not os.environ.get('DATABASE_URL'):
        sys.exit("DATABASE_URL is not set. Point it at a RESTORED SNAPSHOT, not production.")
    if os.environ.get('ALLOW_NONPROD_WRITES') != '1':
        sys.exit(
            "Refusing to run.\n"
            "backtest.py writes models and can promote a champion; the ablation "
            "variant is deliberately crippled and must never win production.\n"
            "Restore a snapshot of the database, point DATABASE_URL at it, then "
            "re-run with ALLOW_NONPROD_WRITES=1."
        )

    outdir = os.environ.get('DIAG_OUTDIR') or os.path.join(REPO, 'diag_runs')
    os.makedirs(outdir, exist_ok=True)

    engine = _engine()
    results = [
        run_variant('current_207', ablate=False, outdir=outdir, engine=engine),
        run_variant('preaudit_146', ablate=True, outdir=outdir, engine=engine),
    ]

    summary = os.path.join(outdir, 'summary.json')
    with open(summary, 'w') as fh:
        json.dump(results, fh, indent=2, default=str)
    report(results)
    print(f"\nWrote {summary}")
    print("\nTrust the walk-forward fold mean over the single holdout ROI: the "
          "holdout is what made run 133 look like +30-42%.")


if __name__ == '__main__':
    main()
