#!/usr/bin/env python3
"""
mma_backtest.py
================
Read-only accuracy report for UFC/MMA predictions.

Unlike afl_backtest.py, this does not train anything — there is no in-repo
training pipeline for the CatBoost model (models/catboost_ufc_model.pkl is
trained externally on the Octagon-AI dataset). This script exists because
before it there was no way to tell whether the model's predictions were any
good: mma_sync.py generates a mma_predictions row once per fight while it is
still upcoming and never touches it again once the fight completes (see
"Skip prediction for completed fights that already have one" in mma_sync.py),
so joining that row against the now-known mma_fights.winner_name gives a
clean, leak-free predicted-vs-actual dataset with no extra logging needed.

Usage:
  DATABASE_URL=postgresql://... python mma_backtest.py --report
  DATABASE_URL=postgresql://... python mma_backtest.py --report --save

--save additionally inserts a snapshot row into mma_prediction_accuracy_log
(created on first use) so accuracy/calibration can be tracked over time
rather than only ever being visible for whatever's currently in Postgres.

Predictions with model_version='fallback_5050' (the CatBoost model was
unavailable/errored and predict_fight() wrote a bare coin-flip) are excluded
from accuracy/Brier/log-loss and reported separately — mixing them in would
just measure "how close is 50/50 to the base rate", not real model skill.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mma_backtest")

FALLBACK_MODEL_VERSION = "fallback_5050"

SCORED_FIGHTS_SQL = """
    SELECT
        f.id AS fight_id, f.fighter_1_name, f.fighter_2_name, f.winner_name,
        f.weight_class, f.is_title_fight,
        p.predicted_winner, p.f1_win_probability, p.f2_win_probability,
        COALESCE(p.model_version, 'catboost_v1') AS model_version
    FROM mma_fights f
    JOIN mma_events e ON e.id = f.event_id
    JOIN mma_predictions p ON p.fight_id = f.id
    WHERE e.is_completed = TRUE
      AND f.winner_name IS NOT NULL
      AND p.f1_win_probability IS NOT NULL
      AND p.f2_win_probability IS NOT NULL
"""


def db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise SystemExit("DATABASE_URL must be set")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def get_conn():
    return psycopg2.connect(db_url())


def ensure_log_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mma_prediction_accuracy_log (
                id SERIAL PRIMARY KEY,
                computed_at TIMESTAMP DEFAULT NOW(),
                sample_size INTEGER NOT NULL,
                accuracy FLOAT,
                brier_score FLOAT,
                log_loss FLOAT,
                fallback_predictions INTEGER,
                details_json JSONB
            )
        """)
    conn.commit()


def load_scored_fights(conn) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(SCORED_FIGHTS_SQL)
        return [dict(r) for r in cur.fetchall()]


def _clip(p: float, eps: float = 1e-6) -> float:
    return min(1 - eps, max(eps, p))


def score(rows: list[dict]) -> dict:
    real_rows = [r for r in rows if r["model_version"] != FALLBACK_MODEL_VERSION]
    fallback_count = len(rows) - len(real_rows)

    if not real_rows:
        return {
            "sample_size": 0,
            "fallback_predictions": fallback_count,
            "accuracy": None,
            "brier_score": None,
            "log_loss": None,
            "by_weight_class": [],
            "by_title_fight": [],
        }

    correct = 0
    brier_sum = 0.0
    logloss_sum = 0.0
    by_weight_class: dict[str, dict] = {}
    by_title: dict[str, dict] = {"title": {"n": 0, "correct": 0}, "non_title": {"n": 0, "correct": 0}}

    for r in real_rows:
        f1_won = 1 if r["winner_name"] == r["fighter_1_name"] else 0
        f1_prob = _clip(float(r["f1_win_probability"]))
        is_correct = int(r["predicted_winner"] == r["winner_name"])

        correct += is_correct
        brier_sum += (f1_prob - f1_won) ** 2
        logloss_sum += -(f1_won * math.log(f1_prob) + (1 - f1_won) * math.log(1 - f1_prob))

        wc = r["weight_class"] or "unknown"
        bucket = by_weight_class.setdefault(wc, {"n": 0, "correct": 0})
        bucket["n"] += 1
        bucket["correct"] += is_correct

        title_key = "title" if r["is_title_fight"] else "non_title"
        by_title[title_key]["n"] += 1
        by_title[title_key]["correct"] += is_correct

    n = len(real_rows)
    return {
        "sample_size": n,
        "fallback_predictions": fallback_count,
        "accuracy": round(correct / n, 4),
        "brier_score": round(brier_sum / n, 4),
        "log_loss": round(logloss_sum / n, 4),
        "by_weight_class": sorted(
            [
                {"weight_class": k, "n": v["n"], "accuracy": round(v["correct"] / v["n"], 4)}
                for k, v in by_weight_class.items()
            ],
            key=lambda x: -x["n"],
        ),
        "by_title_fight": [
            {"segment": k, "n": v["n"], "accuracy": round(v["correct"] / v["n"], 4) if v["n"] else None}
            for k, v in by_title.items()
        ],
    }


def save_snapshot(conn, summary: dict) -> int:
    ensure_log_table(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO mma_prediction_accuracy_log
                (sample_size, accuracy, brier_score, log_loss, fallback_predictions, details_json)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                summary["sample_size"],
                summary["accuracy"],
                summary["brier_score"],
                summary["log_loss"],
                summary["fallback_predictions"],
                json.dumps(summary),
            ),
        )
        snapshot_id = cur.fetchone()[0]
    conn.commit()
    return snapshot_id


def report(save: bool = False) -> dict:
    conn = get_conn()
    try:
        rows = load_scored_fights(conn)
        summary = score(rows)
        summary["generated_at"] = datetime.now(timezone.utc).isoformat()
        if save:
            summary["snapshot_id"] = save_snapshot(conn, summary)
        return summary
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MMA prediction accuracy report (read-only; no training)")
    parser.add_argument("--report", action="store_true", help="Print an accuracy report for completed, scored fights")
    parser.add_argument("--save", action="store_true", help="Persist the report as a row in mma_prediction_accuracy_log")
    args = parser.parse_args()

    if not (args.report or args.save):
        parser.print_help()
        sys.exit(0)

    summary = report(save=args.save)
    print(json.dumps(summary, indent=2, default=str))
    if summary["sample_size"] == 0:
        log.warning(
            "No completed fights with a real (non-fallback) prediction and known winner were found yet."
        )


if __name__ == "__main__":
    main()
