#!/usr/bin/env python3
"""
One-off backfill: run a champion model that predates walk-forward evaluation
through the SAME walk-forward fold procedure current challengers use, and
recompute its true Champion Score under the current selection formula.

Background: champion id=74 was promoted before walk-forward evaluation
existed in the pipeline, so it holds "active champion" status with zero
walk-forward fold data. Every challenger since is compared against a champion
that was never tested the same way (see backtest.py's
walk_forward_fold_count invariant / MIN_WALK_FORWARD_FOLDS). This script
closes that gap for one specific champion without waiting for a brand-new
model to be trained from scratch — it reuses the champion's own saved
artifact and re-fits fresh clones of it on each walk-forward fold, exactly
like backtest.py's run_model_competition() does for challengers.

Usage:
    # Dry run (default): report only, no DB writes.
    python scripts/backfill_champion_walk_forward.py

    # Backfill a specific model id instead of the current active champion.
    python scripts/backfill_champion_walk_forward.py --champion-id 74

    # Actually write the backfilled Champion Score back to the champion row,
    # and roll back to the best previously-rejected challenger if it now
    # outscores the backfilled champion.
    python scripts/backfill_champion_walk_forward.py --champion-id 74 --apply

Environment:
    DATABASE_URL must be set (same as backtest.py).
"""
import argparse
import io
import json
import logging
import sys

import joblib

import backtest

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout)


def _load_model_row(conn, champion_id=None):
    if champion_id is not None:
        row = conn.execute(backtest.text("""
            SELECT id, model_type, model_name, combined_score, selection_metrics, pkl_data, is_active
            FROM backtest_best_model WHERE id = :id
        """), {'id': champion_id}).fetchone()
        if not row:
            raise SystemExit(f"No backtest_best_model row with id={champion_id}")
        return row
    row = conn.execute(backtest.text("""
        SELECT id, model_type, model_name, combined_score, selection_metrics, pkl_data, is_active
        FROM backtest_best_model
        WHERE is_active = TRUE
        ORDER BY promoted_at DESC NULLS LAST, updated_at DESC, id DESC
        LIMIT 1
    """)).fetchone()
    if not row:
        raise SystemExit("No active champion found and no --champion-id given.")
    return row


def _best_rejected_challenger(conn, exclude_id, expected_features=None):
    """Best-scoring previously-rejected challenger, rescored under the CURRENT
    formula (mirrors save_best_model_to_db's force_recompute treatment of a
    stored champion) so this is a fair, up-to-date comparison rather than
    trusting whatever combined_score each row happened to be saved with.

    This only recomputes the SCORING FORMULA from each row's own stored
    walk_forward — it does not regenerate walk_forward itself (that would mean
    re-fitting every candidate, which is out of scope here). A candidate whose
    stored feature contract no longer matches today's feature set is skipped
    rather than trusted at face value: its walk_forward folds were computed
    against a feature set that predates changes since made, which is exactly
    the kind of staleness this rollback comparison must not silently rely on.
    """
    rows = conn.execute(backtest.text("""
        SELECT id, model_type, model_name, combined_score, selection_metrics, pkl_data
        FROM backtest_best_model
        WHERE is_active = FALSE AND id != :exclude_id AND pkl_data IS NOT NULL
        ORDER BY combined_score DESC
        LIMIT 20
    """), {'exclude_id': exclude_id}).fetchall()
    best = None
    skipped_stale_features = []
    for row in rows:
        metrics = {}
        if row[4]:
            try:
                metrics = json.loads(row[4])
            except Exception:
                metrics = {}
        if expected_features is not None and row[5]:
            try:
                candidate_model = joblib.load(io.BytesIO(row[5]))
            except Exception:
                candidate_model = None
            candidate_features = getattr(candidate_model, 'feature_names_in_', None) if candidate_model else None
            if candidate_features is not None and list(candidate_features) != list(expected_features):
                skipped_stale_features.append(row[0])
                continue
        score = backtest._selection_score_from_metrics(metrics, force_recompute=True) if metrics else row[3]
        if score is None:
            continue
        if best is None or score > best['score']:
            best = {
                'id': row[0], 'model_type': row[1], 'model_name': row[2], 'score': score,
                'fold_count': backtest._walk_forward_fold_count(metrics),
            }
    if skipped_stale_features:
        log.warning(
            "Excluded %s rejected-challenger row(s) from the rollback comparison because their stored feature "
            "contract predates the current feature set (walk_forward folds computed on old columns can't be "
            "trusted at face value): ids=%s",
            len(skipped_stale_features), skipped_stale_features,
        )
    return best


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--champion-id', type=int, default=None,
                         help='backtest_best_model id to backfill (default: current active champion)')
    parser.add_argument('--apply', action='store_true',
                         help='Write the backfilled Champion Score back to the DB, and roll back to a '
                              'better-scoring rejected challenger if one exists. Without this flag, the '
                              'script only reports what it would do.')
    args = parser.parse_args()

    backtest.ensure_tables()

    with backtest.engine.connect() as conn:
        champion = _load_model_row(conn, args.champion_id)
        champion_id, model_type, model_name, stored_score, selection_metrics_json, pkl_bytes, is_active = champion
        log.info("Backfilling model id=%s (%s / %s), currently_active=%s, stored_combined_score=%s",
                  champion_id, model_type, model_name, is_active, stored_score)

        champion_metrics = {}
        if selection_metrics_json:
            try:
                champion_metrics = json.loads(selection_metrics_json)
            except Exception:
                champion_metrics = {}
        if 'roi' not in champion_metrics:
            raise SystemExit(
                f"Model id={champion_id} has no raw metric components (roi, strike_rate, etc.) stored in "
                "selection_metrics — only a frozen final score. Its Champion Score can't be recomputed from "
                "here; it needs a full re-validation run, not a walk-forward backfill."
            )

        existing_fold_count = backtest._walk_forward_fold_count(champion_metrics)
        if existing_fold_count >= backtest.MIN_WALK_FORWARD_FOLDS:
            log.info(
                "Model id=%s already has %s walk-forward fold(s) (>= MIN_WALK_FORWARD_FOLDS=%s) — "
                "nothing to backfill.", champion_id, existing_fold_count, backtest.MIN_WALK_FORWARD_FOLDS,
            )
            return

        if not pkl_bytes:
            raise SystemExit(f"Model id={champion_id} has no stored pkl_data to re-fit for walk-forward folds.")
        model = joblib.load(io.BytesIO(pkl_bytes))

        log.info("Loading historical data and rebuilding the training set (same pipeline as backtest.py)...")
        df, strike_rate_data = backtest.load_historical_data()
        X, y_roi, y_won, sp_values, race_ids, horse_ids, meeting_dates = backtest.build_training_set(
            df, strike_rate_data
        )
        dates = backtest.pd.to_datetime(backtest.pd.Series(meeting_dates), errors='coerce')
        order = dates.argsort().values
        X = X.iloc[order].reset_index(drop=True)
        y_won = y_won.iloc[order].reset_index(drop=True)
        sp_values = backtest.pd.Series(sp_values).iloc[order].reset_index(drop=True)
        race_ids = [race_ids[i] for i in order]

        expected_features = getattr(model, 'feature_names_in_', None)
        if expected_features is not None and list(expected_features) != list(X.columns):
            raise SystemExit(
                f"Model id={champion_id}'s stored feature contract does not match the current feature set — "
                "it predates one or more feature-engineering changes and can't be safely re-fit on today's "
                "columns. This needs a fresh model, not a walk-forward backfill."
            )

        log.info(
            "Running walk-forward evaluation (n_splits=%s, embargo_rows=%s) on model id=%s...",
            backtest.WALK_FORWARD_N_SPLITS, backtest.WALK_FORWARD_EMBARGO_ROWS, champion_id,
        )
        walk_forward = backtest._walk_forward_metrics_for_model(model, X, y_won, sp_values, race_ids)
        log.info(
            "Walk-forward result for id=%s: folds=%s roi_std=%.2f fold_rois=%s",
            champion_id, walk_forward['n_splits'], walk_forward['roi_std'],
            [round(f['roi'], 1) for f in walk_forward['folds']],
        )

        if walk_forward['n_splits'] < backtest.MIN_WALK_FORWARD_FOLDS:
            log.warning(
                "Backfill produced only %s completed fold(s) (< MIN_WALK_FORWARD_FOLDS=%s) — dataset may be "
                "too small, or too many folds hit single-class training slices. Recording what we got, but "
                "the invariant will remain unsatisfied.",
                walk_forward['n_splits'], backtest.MIN_WALK_FORWARD_FOLDS,
            )

        updated_metrics = {**champion_metrics, 'walk_forward': walk_forward}
        old_score = backtest._selection_score_from_metrics(champion_metrics, force_recompute=True)
        new_score = backtest._selection_score_from_metrics(updated_metrics, force_recompute=True)

        best_challenger = _best_rejected_challenger(conn, exclude_id=champion_id, expected_features=list(X.columns))

        print("=" * 78)
        print(f"Backfill report for model id={champion_id} ({model_type} / {model_name})")
        print(f"  Champion Score BEFORE backfill (0.3x holdout ROI only): {old_score:.3f}")
        print(f"  Champion Score AFTER backfill  (with walk-forward):     {new_score:.3f}")
        if best_challenger:
            print(
                f"  Best previously-rejected challenger: id={best_challenger['id']} "
                f"({best_challenger['model_type']}) Champion Score={best_challenger['score']:.3f}"
            )
            if best_challenger['score'] > new_score + backtest.PROMOTION_SELECTION_SCORE_EDGE:
                print(
                    "  RECOMMENDATION: rollback review triggered — the backfilled champion score no longer "
                    f"beats challenger id={best_challenger['id']} by the promotion edge "
                    f"({backtest.PROMOTION_SELECTION_SCORE_EDGE:.3f}). Promote the challenger."
                )
            else:
                print("  RECOMMENDATION: backfilled champion still beats the best rejected challenger. No rollback needed.")
        else:
            print("  No previously-rejected challenger with a stored artifact was found to compare against.")
        print("=" * 78)

        if not args.apply:
            print("Dry run (no --apply passed) — no database changes were made.")
            return

        conn.execute(backtest.text("""
            UPDATE backtest_best_model
            SET selection_metrics = :metrics, combined_score = :score, updated_at = NOW()
            WHERE id = :id
        """), {'metrics': json.dumps(updated_metrics), 'score': new_score, 'id': champion_id})

        if is_active and best_challenger and best_challenger['score'] > new_score + backtest.PROMOTION_SELECTION_SCORE_EDGE:
            conn.commit()
            reason = (
                f"Backfill rollback: champion {champion_id}'s backfilled Champion Score {new_score:.3f} "
                f"(now including {walk_forward['n_splits']} walk-forward fold(s)) no longer beats "
                f"previously-rejected challenger {best_challenger['id']}'s Champion Score "
                f"{best_challenger['score']:.3f}."
            )
            log.info(reason)
            backtest.rollback_to_champion(best_challenger['id'], reason=reason)
            if best_challenger['fold_count'] >= backtest.MIN_WALK_FORWARD_FOLDS:
                with backtest.engine.connect() as conn2:
                    backtest.resolve_pipeline_alert(conn2, 'champion_missing_walk_forward_validation')
                    conn2.commit()
        else:
            if is_active and walk_forward['n_splits'] >= backtest.MIN_WALK_FORWARD_FOLDS:
                backtest.resolve_pipeline_alert(conn, 'champion_missing_walk_forward_validation')
            conn.commit()

        print(f"Applied: model id={champion_id} selection_metrics/combined_score updated in the database.")


if __name__ == '__main__':
    main()
