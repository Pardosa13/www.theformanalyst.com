#!/usr/bin/env python3
"""Owner override: activate a specific model as champion outside the normal
promotion rule, and deactivate whatever is champion now.

Written for the model 152 / 143 override: 152 is the best-of-available
candidate on Champion Score but its validation ROI is negative, so it was
never going to be promoted by an ordinary nightly run. This script makes that
override explicit, deliberate and auditable rather than a hand-typed UPDATE.

It goes through backtest.rollback_to_champion(), which is the only supported
activation path and re-runs its own eligibility checks:

  * the target row still has a stored artifact and is inside its retention
    window (retained_until NULL or in the future),
  * can_become_champion(): at least MIN_WALK_FORWARD_FOLDS walk-forward folds
    on record,
  * _assert_champion_comparable(): all raw Champion Score components present
    and scoring_formula_version matching the current formula,
  * the artifact loads and carries a persisted feature list, so live scoring
    can actually use it.

None of those is an ROI gate — rollback_to_champion has never required a
positive validation ROI, so it accepts a negative-ROI model as long as the
checks above pass. That is what makes it the right tool for this override.

Read-only by default: it reports what would happen and stops. Pass --activate
to perform the switch.

Usage:
    DATABASE_URL=postgres://... python3 scripts/manual_override_activate_champion.py 152
    DATABASE_URL=postgres://... python3 scripts/manual_override_activate_champion.py 152 --activate
    DATABASE_URL=postgres://... python3 scripts/manual_override_activate_champion.py 152 \
        --activate --expect-current-champion 143
"""
import argparse
import io
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import joblib
from sqlalchemy import text

# Registers ConsensusRegressor on __main__ so joblib.load can unpickle ensemble
# artifacts saved under either module path — see model_classes.py.
import model_classes  # noqa: F401

DEFAULT_REASON_TEMPLATE = (
    "MANUAL OVERRIDE (not a promotion earned under the normal rule) — "
    "Manually activated: best-of-available candidate despite negative ROI, "
    "per owner override on {today}"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('model_id', type=int, help='model to activate as champion (e.g. 152)')
    parser.add_argument('--activate', action='store_true',
                        help='actually perform the switch (default: report only)')
    parser.add_argument('--expect-current-champion', type=int, default=None,
                        help='refuse to run unless this model is the current champion (e.g. 143)')
    parser.add_argument('--reason', default=None,
                        help='override the recorded manual-override reason text')
    args = parser.parse_args()

    if not os.environ.get('DATABASE_URL'):
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2

    reason = args.reason or DEFAULT_REASON_TEMPLATE.format(today=date.today().isoformat())

    import backtest

    with backtest.engine.connect() as conn:
        row = conn.execute(text("""
            SELECT id, model_type, model_name, is_active, combined_score, validation_roi,
                   validation_strike_rate, validation_bets, retained_until, selection_metrics, pkl_data
            FROM backtest_best_model WHERE id = :id
        """), {'id': args.model_id}).fetchone()
        current = conn.execute(text("""
            SELECT id, model_type, model_name, combined_score, validation_roi
            FROM backtest_best_model
            WHERE is_active = TRUE
            ORDER BY promoted_at DESC NULLS LAST, updated_at DESC, id DESC
        """)).fetchall()

    print("Currently active:")
    if not current:
        print("  (none)")
    for cur_id, cur_type, cur_name, cur_score, cur_roi in current:
        score_text = f"{cur_score:.3f}" if cur_score is not None else "None"
        roi_text = f"{cur_roi:.2f}%" if cur_roi is not None else "None"
        print(f"  id={cur_id} {cur_type} '{cur_name}' combined_score={score_text} validation_roi={roi_text}")
    print()

    if args.expect_current_champion is not None:
        active_ids = [cur_id for cur_id, *_ in current]
        if active_ids != [args.expect_current_champion]:
            print(f"Refusing to run: expected model {args.expect_current_champion} to be the sole active "
                  f"champion, found {active_ids or 'none'}. Re-check the state before overriding.",
                  file=sys.stderr)
            return 2

    if not row:
        print(f"Model {args.model_id} does not exist.", file=sys.stderr)
        return 2

    (model_id, model_type, model_name, is_active, score, val_roi, val_sr,
     val_bets, retained_until, metrics_json, pkl_bytes) = row
    try:
        metrics = json.loads(metrics_json) if metrics_json else {}
    except Exception:
        metrics = {}

    score_text = f"{score:.3f}" if score is not None else "None"
    roi_text = f"{val_roi:.2f}%" if val_roi is not None else "None"
    sr_text = f"{val_sr:.2f}%" if val_sr is not None else "None"
    print(f"Target id={model_id} {model_type} '{model_name}' is_active={is_active}")
    print(f"  stored combined_score={score_text} validation_roi={roi_text} "
          f"strike_rate={sr_text} bets={val_bets}")
    print(f"  retained_until={retained_until}")
    recomputed = backtest._selection_score_from_metrics(metrics, force_recompute=True)
    if recomputed is not None:
        print(f"  Champion Score recomputed under the current formula: {recomputed:.3f}")
    if val_roi is not None and val_roi <= 0:
        print("  NOTE: validation ROI is not positive. rollback_to_champion applies no ROI gate, "
              "so this does not block activation — it is the whole point of this override.")
    print()

    # Report the gates rollback_to_champion will enforce, so a refusal is not a
    # surprise. rollback_to_champion re-checks all of these itself and is the
    # authority; this is a preview, not a substitute.
    problems = []
    if metrics.get('permanently_incompatible'):
        problems.append("marked permanently_incompatible — must never be reactivated")
    ok, guard_reason = backtest.can_become_champion(metrics)
    if not ok:
        problems.append(f"fails can_become_champion: {guard_reason}")
    missing = backtest._missing_selection_metric_components(metrics)
    if missing:
        problems.append(f"selection_metrics missing raw component(s): {', '.join(missing)}")
    stored_version = metrics.get('scoring_formula_version')
    if stored_version != backtest.SCORING_FORMULA_VERSION:
        problems.append(f"scoring_formula_version={stored_version or 'missing'} does not match current "
                        f"{backtest.SCORING_FORMULA_VERSION} — rollback_to_champion will refuse it")
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
                print(f"  Artifact feature list: {len(features)} features")
                live_feature_names = backtest._live_scoring_feature_names()
                if live_feature_names is not None:
                    live_set = set(live_feature_names)
                    missing_from_live = [str(f) for f in features if str(f) not in live_set]
                    if missing_from_live:
                        problems.append(
                            f"{len(missing_from_live)} feature(s) live scoring cannot generate: "
                            f"{missing_from_live[:10]}"
                        )

    print()
    if problems:
        print(f"NOT ELIGIBLE — model {model_id} fails {len(problems)} check(s):")
        for problem in problems:
            print(f"  * {problem}")
        print("These are the non-ROI safety checks and are NOT waived by this override.")
        return 1

    print(f"ELIGIBLE — model {model_id} can be activated as champion.")
    print(f"Reason to be recorded: {reason}")
    if not args.activate:
        print("Report-only run. Re-run with --activate to perform the switch.")
        return 0

    backtest.rollback_to_champion(model_id, reason=reason)

    # Make the override durably visible in the admin alert feed too, so it is
    # not mistaken for a model that earned promotion under the normal rule.
    with backtest.engine.connect() as conn:
        backtest.record_pipeline_alert(
            conn, 'manual_champion_override',
            message=(
                f"Champion {model_id} was activated by manual owner override, not by the normal promotion "
                f"rule. Previous champion: {current[0][0] if current else 'none'}. "
                f"Target validation ROI: {roi_text}. Reason: {reason}"
            ),
            severity='warning',
        )
        conn.commit()

        after = conn.execute(text("""
            SELECT id, is_active, promotion_reason FROM backtest_best_model
            WHERE is_active = TRUE OR id = :id
            ORDER BY id
        """), {'id': model_id}).fetchall()

    print("Post-activation state:")
    for after_id, after_active, after_reason in after:
        print(f"  id={after_id} is_active={after_active} promotion_reason={after_reason}")
    active_now = [after_id for after_id, after_active, _ in after if after_active]
    if active_now != [model_id]:
        print(f"  >>> WARNING: expected only model {model_id} active, found {active_now}.")
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
