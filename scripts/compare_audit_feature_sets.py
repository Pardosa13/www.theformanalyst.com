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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_variant(label, ablate, outdir):
    env = dict(os.environ)
    env['ABLATE_AUDIT_FEATURES'] = '1' if ablate else '0'
    env['ML_MODEL_VERSION'] = f"diag_{label}_{datetime.utcnow():%Y%m%d%H%M}"
    logpath = os.path.join(outdir, f'{label}.log')
    print(f"\n=== running variant: {label} "
          f"({'136 pre-audit features' if ablate else '197 current features'}) ===")
    print(f"    log -> {logpath}")
    with open(logpath, 'w') as fh:
        rc = subprocess.call([sys.executable, os.path.join(REPO, 'backtest.py')],
                             cwd=REPO, env=env, stdout=fh, stderr=subprocess.STDOUT)
    print(f"    exit code: {rc}")
    return {'label': label, 'ablate': ablate, 'exit_code': rc, 'log': logpath}


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

    results = [
        run_variant('current_207', ablate=False, outdir=outdir),
        run_variant('preaudit_146', ablate=True, outdir=outdir),
    ]

    summary = os.path.join(outdir, 'summary.json')
    with open(summary, 'w') as fh:
        json.dump(results, fh, indent=2)
    print(f"\nWrote {summary}")
    print("\nCompare Champion Score / ROI / strike rate / A-E ratio for the two "
          "run_ids in backtest_best_model, and the per-fold walk-forward ROIs.\n"
          "The walk-forward fold mean is the number to trust — not the single "
          "holdout ROI, which is what made run 133 look like +30-42%.")


if __name__ == '__main__':
    main()
