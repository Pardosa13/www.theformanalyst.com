#!/usr/bin/env python3
"""Read-only answer to: what is actually live right now, and did the last
nightly run promote anything?

Prints, in one pass and without writing anything:

  1. The active champion in backtest_best_model (what live scoring uses).
  2. The most recent rows in backtest_model_promotions.
  3. The most recent backtest_runs rows - this is the durable trace of whether
     the cron job ran at all. main() inserts a 'running' row as its first DB
     action, so a run that produced no logs but has a row here got past module
     import; no new row at all means the job never started.
  4. Any open (unresolved) ml_pipeline_alerts, which is where a blocked
     promotion records itself.
  5. The status of one specific candidate model, if you pass an id.

Usage:
    DATABASE_URL=postgres://... python3 scripts/report_champion_status.py
    DATABASE_URL=postgres://... python3 scripts/report_champion_status.py --model-id 154
"""
import argparse
import json
import os
import sys

from sqlalchemy import create_engine, text

SCORING_FORMULA_VERSION = 'champion_score_v6_joint_kelly'


def _engine():
    url = os.environ.get('DATABASE_URL')
    if not url:
        sys.exit("DATABASE_URL not set. Run this where the cron job runs, or "
                 "paste the Railway Postgres connection string in.")
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return create_engine(url)


def _metrics(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _fmt_pct(value):
    return 'n/a' if value is None else f"{value:.2f}%"


def _rule(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def show_champion(conn):
    _rule("1. ACTIVE CHAMPION (backtest_best_model WHERE is_active = TRUE)")
    rows = conn.execute(text("""
        SELECT id, run_id, model_type, model_name, combined_score,
               validation_roi, validation_strike_rate, validation_bets,
               promoted_at, promotion_reason, scoring_formula_version,
               selection_metrics, deactivated_at, retained_until
        FROM backtest_best_model
        WHERE is_active = TRUE
        ORDER BY promoted_at DESC NULLS LAST, updated_at DESC, id DESC
    """)).fetchall()

    if not rows:
        print("NO ACTIVE CHAMPION. Live scoring has no model. Check the open "
              "alerts in section 4.")
        return

    if len(rows) > 1:
        print(f"WARNING: {len(rows)} rows are is_active = TRUE. There should "
              f"be exactly one.\n")

    for row in rows:
        metrics = _metrics(row.selection_metrics)
        folds = metrics.get('walk_forward_folds') or metrics.get('folds') or []
        print(f"  model id            : {row.id}")
        print(f"  model               : {row.model_name} ({row.model_type})")
        print(f"  trained in run_id   : {row.run_id}")
        print(f"  champion score      : {row.combined_score}")
        print(f"  validation ROI      : {_fmt_pct(row.validation_roi)}")
        print(f"  validation SR       : {_fmt_pct(row.validation_strike_rate)}")
        print(f"  validation bets     : {row.validation_bets}")
        print(f"  promoted_at         : {row.promoted_at}")
        print(f"  walk-forward folds  : {len(folds) if hasattr(folds, '__len__') else 'n/a'}")
        print(f"  scoring formula     : {row.scoring_formula_version} "
              f"({'current' if row.scoring_formula_version == SCORING_FORMULA_VERSION else 'STALE'})")
        if metrics.get('permanently_incompatible'):
            print("  FLAG                : permanently_incompatible = True")
        print(f"  promotion_reason    : {(row.promotion_reason or '').strip()}")
        print()


def show_promotions(conn, limit):
    _rule(f"2. MOST RECENT PROMOTIONS (backtest_model_promotions, last {limit})")
    rows = conn.execute(text("""
        SELECT id, run_id, old_champion_id, new_champion_id, model_type,
               promotion_reason, promoted_at
        FROM backtest_model_promotions
        ORDER BY promoted_at DESC NULLS LAST, id DESC
        LIMIT :limit
    """), {'limit': limit}).fetchall()

    if not rows:
        print("No promotions have ever been recorded.")
        return

    for row in rows:
        print(f"  [{row.promoted_at}] run {row.run_id}: "
              f"model {row.old_champion_id} -> {row.new_champion_id} "
              f"({row.model_type})")
        reason = (row.promotion_reason or '').strip()
        if reason:
            print(f"      reason: {reason[:400]}")
        print()


def show_runs(conn, limit):
    _rule(f"3. RECENT BACKTEST RUNS (backtest_runs, last {limit})")
    print("A row here means the job reached main(). No new row means it died "
          "during module import\nor never fired - which is what 'no logs at "
          "all' looks like.\n")
    rows = conn.execute(text("""
        SELECT id, started_at, completed_at, status, total_races, notes
        FROM backtest_runs
        ORDER BY id DESC
        LIMIT :limit
    """), {'limit': limit}).fetchall()

    if not rows:
        print("No runs recorded at all.")
        return

    for row in rows:
        print(f"  run {row.id:<6} {str(row.status):<10} "
              f"started {row.started_at}  completed {row.completed_at}  "
              f"races={row.total_races}")
        if row.notes:
            print(f"      notes: {str(row.notes)[:400]}")


def show_alerts(conn):
    _rule("4. OPEN PIPELINE ALERTS (ml_pipeline_alerts WHERE resolved_at IS NULL)")
    rows = conn.execute(text("""
        SELECT alert_key, severity, message, run_id, created_at, updated_at
        FROM ml_pipeline_alerts
        WHERE resolved_at IS NULL
        ORDER BY updated_at DESC, id DESC
    """)).fetchall()

    if not rows:
        print("None. Nothing is flagged as blocking promotion.")
        return

    for row in rows:
        print(f"  [{row.severity}] {row.alert_key} (run {row.run_id}, "
              f"last seen {row.updated_at})")
        print(f"      {str(row.message or '')[:500]}")
        print()


def show_model(conn, model_id):
    _rule(f"5. CANDIDATE MODEL {model_id}")
    row = conn.execute(text("""
        SELECT id, run_id, model_type, model_name, combined_score, is_active,
               validation_roi, validation_strike_rate, validation_bets,
               promoted_at, promotion_reason, scoring_formula_version,
               selection_metrics, created_at, deactivated_at, retained_until,
               (pkl_data IS NOT NULL) AS has_artifact
        FROM backtest_best_model
        WHERE id = :id
    """), {'id': model_id}).fetchone()

    if not row:
        print(f"No backtest_best_model row with id {model_id}. It was never "
              f"saved as a candidate.")
        return

    metrics = _metrics(row.selection_metrics)
    folds = metrics.get('walk_forward_folds') or metrics.get('folds') or []
    fold_count = len(folds) if hasattr(folds, '__len__') else 0
    print(f"  is_active           : {row.is_active}")
    print(f"  model               : {row.model_name} ({row.model_type})")
    print(f"  created_at          : {row.created_at}")
    print(f"  trained in run_id   : {row.run_id}")
    print(f"  champion score      : {row.combined_score}")
    print(f"  validation ROI      : {_fmt_pct(row.validation_roi)}")
    print(f"  validation SR       : {_fmt_pct(row.validation_strike_rate)}")
    print(f"  validation bets     : {row.validation_bets}")
    print(f"  walk-forward folds  : {fold_count}")
    if fold_count:
        signs = ['+' if (f.get('roi') or 0) > 0 else '-' for f in folds
                 if isinstance(f, dict)]
        if signs:
            print(f"  fold ROI signs      : {' '.join(signs)}")
    print(f"  artifact stored     : {row.has_artifact}")
    print(f"  retained_until      : {row.retained_until}")
    print(f"  scoring formula     : {row.scoring_formula_version} "
          f"({'current' if row.scoring_formula_version == SCORING_FORMULA_VERSION else 'STALE'})")
    print(f"  promoted_at         : {row.promoted_at}")
    print(f"  deactivated_at      : {row.deactivated_at}")

    promo = conn.execute(text("""
        SELECT id, run_id, old_champion_id, promoted_at, promotion_reason
        FROM backtest_model_promotions
        WHERE new_champion_id = :id
        ORDER BY promoted_at DESC NULLS LAST, id DESC
    """), {'id': model_id}).fetchall()
    if promo:
        print(f"\n  It WAS promoted - {len(promo)} promotion row(s):")
        for p in promo:
            print(f"    [{p.promoted_at}] run {p.run_id}, "
                  f"replaced model {p.old_champion_id}")
    else:
        print("\n  It was NEVER promoted (no backtest_model_promotions row "
              "names it as new_champion_id).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-id', type=int, default=None,
                        help='Also report on this specific candidate, e.g. 154')
    parser.add_argument('--promotions', type=int, default=10,
                        help='How many recent promotions to show (default 10)')
    parser.add_argument('--runs', type=int, default=10,
                        help='How many recent runs to show (default 10)')
    args = parser.parse_args()

    engine = _engine()
    with engine.connect() as conn:
        show_champion(conn)
        show_promotions(conn, args.promotions)
        show_runs(conn, args.runs)
        show_alerts(conn)
        if args.model_id is not None:
            show_model(conn, args.model_id)
    print()


if __name__ == '__main__':
    main()
