#!/usr/bin/env python3
"""Audit every backtest_best_model row for the Champion 79 defect: an artifact
with no persisted feature list.

Such an artifact cannot score a single live race — ml_predict raises
"ML feature contract failed: model artifact has no persisted feature list"
before predict() — so any row flagged here is a model that must never be
allowed to become (or remain) the active champion.

Read-only by default. Reports:
  * which model is currently is_active = TRUE, and whether it is usable
  * every other row with a missing/empty feature list
  * which of those rows _best_rejected_challenger could still surface

Usage:
    DATABASE_URL=postgres://... python3 scripts/audit_champion_feature_lists.py
"""
import io
import json
import os
import sys

import joblib
from sqlalchemy import create_engine, text


def stored_feature_list(model):
    """Mirror of backtest._stored_feature_list — missing and empty are the same."""
    stored = getattr(model, 'feature_names_in_', None)
    if stored is None:
        stored = getattr(model, '_form_analyst_expected_features', None)
    if stored is None:
        return None
    stored = list(stored)
    return stored or None


def main():
    db_url = os.environ.get('DATABASE_URL', '')
    if not db_url:
        print("DATABASE_URL is not set.", file=sys.stderr)
        return 2
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)

    engine = create_engine(db_url, pool_pre_ping=True)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, model_type, model_name, is_active, combined_score,
                   promoted_at, selection_metrics, pkl_data
            FROM backtest_best_model
            ORDER BY is_active DESC, combined_score DESC NULLS LAST, id DESC
        """)).fetchall()

    broken = []
    active = None
    print(f"{'id':>6}  {'active':>6}  {'score':>10}  {'features':>9}  model")
    print("-" * 78)
    for row in rows:
        model_id, model_type, model_name, is_active, score, promoted_at, metrics_json, pkl = row
        if not pkl:
            features, note = None, "NO ARTIFACT"
        else:
            try:
                features = stored_feature_list(joblib.load(io.BytesIO(bytes(pkl))))
                note = "" if features else "NO FEATURE LIST"
            except Exception as exc:
                features, note = None, f"LOAD FAILED: {exc}"

        usable = features is not None
        if not usable:
            broken.append((model_id, is_active, score, note))
        if is_active:
            active = (model_id, model_name, usable, features, metrics_json, promoted_at)

        print(
            f"{model_id:>6}  {str(bool(is_active)):>6}  "
            f"{(f'{score:.3f}' if score is not None else 'None'):>10}  "
            f"{(len(features) if features else 0):>9}  "
            f"{model_name or model_type} {note}"
        )

    print()
    print("=" * 78)
    if active is None:
        print("ACTIVE CHAMPION: none (no row has is_active = TRUE).")
    else:
        model_id, model_name, usable, features, metrics_json, promoted_at = active
        print(f"ACTIVE CHAMPION: id={model_id} ({model_name}) promoted_at={promoted_at}")
        print(f"  usable for live scoring: {usable}")
        print(f"  feature count: {len(features) if features else 0}")
        try:
            metrics = json.loads(metrics_json) if metrics_json else {}
        except Exception:
            metrics = {}
        print(f"  scoring_formula_version: {metrics.get('scoring_formula_version')}")
        print(f"  permanently_incompatible: {metrics.get('permanently_incompatible', False)}")
        if not usable:
            print("  >>> DEFECT PRESENT: this champion cannot score any live race.")

    print()
    print(f"Rows with a missing/empty feature list: {len(broken)}")
    for model_id, is_active, score, note in broken:
        flag = " (ACTIVE)" if is_active else ""
        score_text = f"{score:.3f}" if score is not None else "None"
        print(f"  id={model_id}{flag} combined_score={score_text} — {note}")

    # _best_rejected_challenger orders by combined_score DESC and takes 20.
    # Anything unusable inside that window is a candidate the rollback path
    # would have to skip; if the skip logic ever regresses, these are the rows
    # that would get selected again.
    inactive_broken = [b for b in broken if not b[1]]
    if inactive_broken:
        print()
        print(f"Of those, {len(inactive_broken)} are inactive rows that "
              f"_best_rejected_challenger must skip rather than promote.")

    return 1 if broken else 0


if __name__ == '__main__':
    sys.exit(main())
