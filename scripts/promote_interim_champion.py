#!/usr/bin/env python3
"""Step 2 of the model 81 incident: check a candidate's feature contract, then
promote it as the manual interim champion.

Model 81 is live and serving predictions and Kelly stake recommendations off a
Champion Score of 2,030,782,877.129 that came entirely from one contaminated
metrics field. Everything it has produced since promotion is untrustworthy, so
a known-good champion has to go back in front of it — independently of, and
ahead of, the code fix landing.

Eligibility is not just "highest real score". A candidate is only usable if
its artifact carries a feature list live scoring can actually generate: a
model predating this release's feature addition is missing
opponent_quality_form and is not eligible, in which case the next-best
candidate that does carry the current contract is the one to promote. (Model
112 is permanently_incompatible — 204 features against the current 207 — and
must not be reactivated under any circumstances.)

Read-only by default: it reports the check and stops. Pass --promote to
actually call rollback_to_champion, which re-runs its own eligibility and
comparability checks and will refuse an ineligible model regardless of what
this script decided.

Usage:
    DATABASE_URL=postgres://... python3 scripts/promote_interim_champion.py 77
    DATABASE_URL=postgres://... python3 scripts/promote_interim_champion.py 77 --promote
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import joblib
from sqlalchemy import text

# Registers ConsensusRegressor on __main__ so joblib.load can unpickle ensemble
# artifacts saved under either module path — see model_classes.py.
import model_classes  # noqa: F401


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model_id', type=int, help='candidate to check/promote (e.g. 77)')
    parser.add_argument('--promote', action='store_true',
                        help='actually promote after the checks pass (default: report only)')
    parser.add_argument('--reason', default='Manual interim promotion after model 81 incident — see incident notes')
    args = parser.parse_args()

    if not os.environ.get('DATABASE_URL'):
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2

    import backtest

    live_feature_names = backtest._live_scoring_feature_names()
    if live_feature_names is None:
        print("Could not import the live scoring feature contract from ml_predict. "
              "Refusing to judge eligibility blind.", file=sys.stderr)
        return 2
    live_set = set(live_feature_names)
    print(f"Live scoring contract: {len(live_feature_names)} features "
          f"(opponent_quality_form present: {'opponent_quality_form' in live_set})")
    print()

    with backtest.engine.connect() as conn:
        row = conn.execute(text("""
            SELECT id, model_type, model_name, is_active, combined_score, selection_metrics, pkl_data
            FROM backtest_best_model WHERE id = :id
        """), {'id': args.model_id}).fetchone()
        active = conn.execute(text("""
            SELECT id, model_type, combined_score FROM backtest_best_model
            WHERE is_active = TRUE
            ORDER BY promoted_at DESC NULLS LAST, updated_at DESC, id DESC
        """)).fetchall()

    print("Currently active:")
    for active_id, active_type, active_score in active:
        score_text = f"{active_score:.3f}" if active_score is not None else "None"
        print(f"  id={active_id} {active_type} combined_score={score_text}")
    print()

    if not row:
        print(f"Model {args.model_id} does not exist.", file=sys.stderr)
        return 2
    model_id, model_type, model_name, is_active, score, metrics_json, pkl_bytes = row
    try:
        metrics = json.loads(metrics_json) if metrics_json else {}
    except Exception:
        metrics = {}

    score_text = f"{score:.3f}" if score is not None else "None"
    print(f"Candidate id={model_id} {model_type} '{model_name}' "
          f"is_active={is_active} combined_score={score_text}")

    problems = []
    if metrics.get('permanently_incompatible'):
        problems.append("marked permanently_incompatible — must never be reactivated")
    ok, guard_reason = backtest.can_become_champion(metrics)
    if not ok:
        problems.append(f"fails can_become_champion: {guard_reason}")
    recomputed = backtest._selection_score_from_metrics(metrics, force_recompute=True)
    if recomputed is not None:
        print(f"  Champion Score recomputed under the current formula: {recomputed:.3f}")
        low, high = backtest.PLAUSIBLE_CHAMPION_SCORE_RANGE
        if not (low <= recomputed <= high):
            problems.append(f"recomputed Champion Score {recomputed:.3f} is outside "
                            f"the plausible range {backtest.PLAUSIBLE_CHAMPION_SCORE_RANGE}")

    if not pkl_bytes:
        problems.append("no stored artifact — cannot score a live race")
    else:
        try:
            model = joblib.load(io.BytesIO(pkl_bytes))
        except Exception as e:
            model = None
            problems.append(f"artifact failed to load: {e}")
        if model is not None:
            features = backtest._stored_feature_list(model)
            if features is None:
                problems.append("no persisted feature list — cannot generate live predictions")
            else:
                missing_from_live = [str(f) for f in features if str(f) not in live_set]
                print(f"  Artifact feature list: {len(features)} features")
                print(f"  opponent_quality_form present: {'opponent_quality_form' in {str(f) for f in features}}")
                if missing_from_live:
                    problems.append(
                        f"{len(missing_from_live)} feature(s) live scoring cannot generate: "
                        f"{missing_from_live[:10]}"
                    )
                if 'opponent_quality_form' not in {str(f) for f in features}:
                    problems.append(
                        "predates this release's feature addition (no opponent_quality_form) — "
                        "not eligible as the interim champion; use the next-best candidate that "
                        "carries the current contract"
                    )

    print()
    if problems:
        print(f"NOT ELIGIBLE — model {model_id} fails {len(problems)} check(s):")
        for problem in problems:
            print(f"  * {problem}")
        return 1

    print(f"ELIGIBLE — model {model_id} carries the current feature contract and can score live races.")
    if not args.promote:
        print("Report-only run. Re-run with --promote to activate it.")
        return 0

    backtest.rollback_to_champion(model_id, reason=args.reason)
    print(f"Promoted model {model_id} as interim champion.")

    with backtest.engine.connect() as conn:
        after = conn.execute(text("""
            SELECT id, is_active FROM backtest_best_model WHERE id IN (:new_id, 81)
        """), {'new_id': model_id}).fetchall()
    print("Post-promotion state:")
    for after_id, after_active in after:
        print(f"  id={after_id} is_active={after_active}")
    if any(after_id == 81 and after_active for after_id, after_active in after):
        print("  >>> WARNING: model 81 is STILL ACTIVE. Deactivate it before trusting live output.")
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
