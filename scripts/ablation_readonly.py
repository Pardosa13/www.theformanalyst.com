#!/usr/bin/env python3
"""Answer the 2026-07 audit-feature question using a READ-ONLY database role.

backtest.py's own entrypoint cannot run against a read-only role: main() calls
ensure_tables() (DDL) and INSERT INTO backtest_runs within its first few lines.

But the part that actually answers the question needs no writes at all.
run_model_competition() — the Track E competition that trains every candidate,
runs the walk-forward folds and computes the Champion Score — takes X as a
parameter and performs no INSERT, UPDATE or DDL anywhere in its body. So this
script:

    load_historical_data()      read-only SELECTs
    load_strike_rate_data()     read-only SELECTs
    build_training_set()        pure compute -> X with all 197 features
    run_model_competition()     pure compute, once per feature set

and changes exactly one thing between the two calls: whether the 61 columns
added by the 2026-07-21 audit (commit 9491ea7) plus opponent_quality_form
(233ff6b) are present. Same rows, same chronological splits, same undated-
meeting repair, same Champion Score formula (v6). Features are the only
variable.

Nothing is written to the database and no champion can be promoted, so this is
safe to point at production with a read-only role.

Usage:
    DATABASE_URL=postgresql://readonly:...@host:port/db \
        python scripts/ablation_readonly.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

USING_POSTGREST = bool(os.environ.get('POSTGREST_URL')) and not os.environ.get('DATABASE_URL')
if USING_POSTGREST:
    # backtest.py reads DATABASE_URL at import time and exits without one, but
    # nothing on this path touches the engine — the loader comes from
    # validate_holdout_75_25, which fetches over the read-only PostgREST API.
    os.environ.setdefault('DATABASE_URL', 'postgresql://unused:unused@127.0.0.1:5432/unused')
elif not os.environ.get('DATABASE_URL'):
    sys.exit("Set DATABASE_URL (direct Postgres) or POSTGREST_URL (read-only REST API).")

import backtest as bt  # noqa: E402  (import after the env check; module reads it at import)


def load_inputs():
    """Return (df, strike_rate_data) from whichever read-only source is configured."""
    if USING_POSTGREST:
        print(f"Loading via PostgREST: {os.environ['POSTGREST_URL']}")
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'vh', os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'validate_holdout_75_25.py'))
        vh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(vh)
        return vh.load_data_via_postgrest()
    print("Loading via direct Postgres connection")
    return bt.load_historical_data(), bt.load_strike_rate_data()


def _fmt(v, spec='.2f'):
    return 'n/a' if v is None else format(float(v), spec)


def summarise(label, results):
    rows = []
    for r in results:
        m = r.get('metrics') or {}
        wf = m.get('walk_forward') or {}
        folds = [f.get('roi') for f in (wf.get('folds') or []) if f.get('bets', 0)]
        rows.append({
            'variant': label,
            'model_name': r.get('model_name'),
            'selection_score': r.get('selection_score'),
            'roi': m.get('roi'),
            'strike_rate': m.get('strike_rate'),
            'a_e_ratio': m.get('a_e_ratio'),
            'bets': m.get('number_of_bets'),
            'fold_rois': folds,
            'wf_mean_roi': (sum(folds) / len(folds)) if folds else None,
        })
    rows.sort(key=lambda d: (d['selection_score'] is not None, d['selection_score']), reverse=True)
    return rows


def print_table(label, feature_count, rows):
    print(f"\n{label}  [{feature_count} features]")
    print(f"  {'model':<24}{'ChampScore':>11}{'holdoutROI':>12}{'SR%':>8}"
          f"{'A/E':>7}{'bets':>7}{'wfMeanROI':>11}  folds")
    for d in rows:
        print(f"  {str(d['model_name'])[:23]:<24}"
              f"{_fmt(d['selection_score']):>11}"
              f"{_fmt(d['roi']):>12}"
              f"{_fmt(d['strike_rate']):>8}"
              f"{_fmt(d['a_e_ratio']):>7}"
              f"{_fmt(d['bets'], '.0f'):>7}"
              f"{_fmt(d['wf_mean_roi']):>11}"
              f"  {[round(f, 1) for f in d['fold_rois']]}")


def main():
    print("Loading data (read-only)...")
    df, sr = load_inputs()

    X, y_roi, y_won, sp_values, race_ids, horse_ids, meeting_dates = \
        bt.build_training_set(df, sr)
    print(f"Training matrix: {X.shape[0]} rows x {X.shape[1]} features")

    audit_cols = [c for c in bt.AUDIT_FEATURES_2026_07 if c in X.columns]
    missing = [c for c in bt.AUDIT_FEATURES_2026_07 if c not in X.columns]
    if missing:
        print(f"NOTE: {len(missing)} audit columns absent from this matrix: {missing}")

    variants = [
        ('current_207', X, X.shape[1]),
        ('preaudit_146', X.drop(columns=audit_cols), X.shape[1] - len(audit_cols)),
    ]

    out = {}
    for label, X_variant, n_feat in variants:
        print(f"\n{'=' * 78}\nRunning Track E competition: {label} ({n_feat} features)\n{'=' * 78}")
        _best, results = bt.run_model_competition(
            X_variant, y_roi, y_won, sp_values, race_ids, meeting_dates, df
        )
        out[label] = {'n_features': n_feat, 'rows': summarise(label, results)}

    print("\n" + "=" * 78)
    print("RESULT — identical rows and splits, audit features the only variable")
    print("=" * 78)
    for label in ('current_207', 'preaudit_146'):
        print_table(label, out[label]['n_features'], out[label]['rows'])

    def best(label):
        scored = [d for d in out[label]['rows'] if d['selection_score'] is not None]
        return max(scored, key=lambda d: d['selection_score']) if scored else None

    cur, pre = best('current_207'), best('preaudit_146')
    print("\n" + "-" * 78)
    if cur and pre:
        delta = cur['selection_score'] - pre['selection_score']
        print(f"Best Champion Score  current({out['current_207']['n_features']}) "
              f"{cur['selection_score']:.2f}  vs  pre-audit("
              f"{out['preaudit_146']['n_features']}) {pre['selection_score']:.2f}"
              f"   delta {delta:+.2f}")
        if delta > 1.0:
            print("\nVERDICT: the audit features HELP. They are not the cause of the "
                  "recent bad models — investigate what changed in the data or "
                  "pipeline after run 136.")
        elif delta < -1.0:
            print("\nVERDICT: the audit features HURT on this data. Rolling them back "
                  "and re-promoting from the 136-feature set is justified.")
        else:
            print("\nVERDICT: no meaningful difference (within the +/-1.0 promotion "
                  "edge). The features are not what changed — look at the data and "
                  "the pipeline.")
        if (cur['wf_mean_roi'] or 0) < 0 and (pre['wf_mean_roi'] or 0) < 0:
            print("\nNOTE: BOTH feature sets show a negative walk-forward mean ROI. "
                  "Neither has demonstrable out-of-sample edge on this data, whatever "
                  "the Champion Scores say relative to each other.")
    else:
        print("Could not compare: a variant produced no scored candidate.")
    print("-" * 78)

    dest = os.environ.get('ABLATION_JSON', 'ablation_result.json')
    with open(dest, 'w') as fh:
        json.dump(out, fh, indent=2, default=str)
    print(f"\nWrote {dest}")


if __name__ == '__main__':
    main()
