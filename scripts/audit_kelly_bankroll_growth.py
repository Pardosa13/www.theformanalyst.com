#!/usr/bin/env python3
"""Step 1 of the model 81 incident: sweep every stored row for an implausible
kelly_staking.bankroll_growth, and say which format produced it.

Model 81 was fallback-promoted with a Champion Score of 2,030,782,877.129
because its stored kelly_staking.bankroll_growth was 406,156,577.65 and the
v6 formula weights that field *5.0. That block carried neither
avg_horses_backed_per_race nor races_with_zero_bets, so it was written by the
deleted single-bet simulator (_simulate_kelly_staking) rather than by
_simulate_joint_kelly_staking — the v6 formula is simply the first thing that
ever read the field, and it detonated on the first legacy row it touched.

What this sweep decides:
  * Only OLD-FORMAT rows flagged (is_new_format = None): pure legacy
    contamination. The scoring fix alone closes the incident, and the live
    joint solver needs no separate investigation.
  * Any NEW-FORMAT row flagged: _solve_joint_kelly / _simulate_joint_kelly_staking
    has a bug of its own — reproduce the race/odds condition that triggers it
    (leading suspects: odds at or near 1.0, or reserve R approaching 1.0
    making 1.0 - R underflow in Q / (o * (1.0 - R))) and pin it with a test.

Also reports, for every flagged row, what the Champion Score was when stored
and what it recomputes to under the CURRENT formula — the clamp and the
old-format zeroing should drag every one of them back into the plausible
range.

Read-only: this script never writes to the database.

Usage:
    DATABASE_URL=postgres://... python3 scripts/audit_kelly_bankroll_growth.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text

# bankroll_growth is a fractional return (0.5 = +50%). Anything above +1000%
# over a validation window is not a result, it is a defect — deliberately
# looser than the [-1.0, 5.0] scoring clamp so the sweep still surfaces rows
# that sit just outside what the formula is willing to credit.
IMPLAUSIBLE_BANKROLL_GROWTH = float(os.environ.get('ML_IMPLAUSIBLE_BANKROLL_GROWTH', '10.0'))

SWEEP_SQL = """
    SELECT id, model_type, is_active, combined_score,
           selection_metrics::json -> 'kelly_staking' ->> 'bankroll_growth' AS bankroll_growth,
           selection_metrics::json -> 'kelly_staking' ->> 'avg_horses_backed_per_race' AS is_new_format,
           selection_metrics
    FROM backtest_best_model
    WHERE selection_metrics IS NOT NULL
      AND (selection_metrics::json -> 'kelly_staking' ->> 'bankroll_growth') IS NOT NULL
      AND (selection_metrics::json -> 'kelly_staking' ->> 'bankroll_growth')::float > :threshold
    ORDER BY (selection_metrics::json -> 'kelly_staking' ->> 'bankroll_growth')::float DESC
    LIMIT 20
"""

# Context, not a filter: the ordinary scores this incident has to be measured
# against. Every legitimate row on record scores in the low-to-mid 40s.
CONTEXT_SQL = """
    SELECT id, model_type, is_active, combined_score
    FROM backtest_best_model
    WHERE selection_metrics IS NOT NULL
    ORDER BY combined_score DESC NULLS LAST
    LIMIT 10
"""


def main():
    db_url = os.environ.get('DATABASE_URL', '')
    if not db_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)

    # Imported only after the DATABASE_URL check: backtest.py exits at import
    # time without one.
    import backtest

    engine = create_engine(db_url, pool_pre_ping=True)
    with engine.connect() as conn:
        flagged = conn.execute(text(SWEEP_SQL), {'threshold': IMPLAUSIBLE_BANKROLL_GROWTH}).fetchall()
        context = conn.execute(text(CONTEXT_SQL)).fetchall()

    print(f"Highest stored Champion Scores on record (context for what 'plausible' means):")
    for model_id, model_type, is_active, score in context:
        flag = " (ACTIVE)" if is_active else ""
        score_text = f"{score:.3f}" if score is not None else "None"
        print(f"  id={model_id}{flag} {model_type} combined_score={score_text}")
    print()

    print(f"Rows with kelly_staking.bankroll_growth > {IMPLAUSIBLE_BANKROLL_GROWTH}: {len(flagged)}")
    new_format_hits = []
    for model_id, model_type, is_active, score, growth, is_new_format, metrics_json in flagged:
        try:
            metrics = json.loads(metrics_json) if metrics_json else {}
        except Exception:
            metrics = {}
        recomputed = backtest._selection_score_from_metrics(metrics, force_recompute=True)
        fmt = "NEW (joint solver)" if is_new_format is not None else "OLD (pre-joint-solver)"
        if is_new_format is not None:
            new_format_hits.append(model_id)
        flag = " (ACTIVE)" if is_active else ""
        stored_text = f"{score:.3f}" if score is not None else "None"
        recomputed_text = f"{recomputed:.3f}" if recomputed is not None else "None"
        print(f"  id={model_id}{flag} {model_type} format={fmt}")
        print(f"      bankroll_growth={growth}")
        print(f"      Champion Score stored={stored_text} -> recomputed under current formula={recomputed_text}")
        if recomputed is not None and not (
            backtest.PLAUSIBLE_CHAMPION_SCORE_RANGE[0] <= recomputed <= backtest.PLAUSIBLE_CHAMPION_SCORE_RANGE[1]
        ):
            print("      >>> STILL implausible after the fix — the scoring change does not cover this row.")

    print()
    if not flagged:
        print("No row on record carries an implausible bankroll_growth. Nothing further to do.")
        return 0
    if new_format_hits:
        print(f">>> {len(new_format_hits)} NEW-FORMAT row(s) flagged: {new_format_hits}")
        print(">>> The joint solver has a bug of its own, separate from the legacy contamination.")
        print(">>> Reproduce the triggering race/odds condition and pin it with a regression test.")
        return 1
    print(">>> Only old-format rows flagged: pure legacy contamination, as diagnosed.")
    print(">>> The scoring fix (clamp + old-format zeroing) covers this; the live joint")
    print(">>> solver needs no separate fix.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
