"""75/25 held-out validation of the CURRENT feature pipeline — the same
methodology used to validate the 2026-07 audit features (PR #295):

  1. Build the full training matrix with backtest.build_training_set()
     (i.e. the current 204-feature contract).
  2. Chronological 75/25 split by meeting-date quantile. The newest 25% is
     UNTOUCHED: no model selection, tuning, or iteration may look at it.
  3. Walk-forward evaluation (same WALK_FORWARD_N_SPLITS / embargo as the
     nightly job) on the older 75% only — this is the development signal.
  4. One single fit-on-dev, score-on-holdout pass per candidate family for
     the final untouched-holdout ROI / strike-rate numbers.

Data access (either works):
  DATABASE_URL          read-only Postgres URL — uses backtest.load_historical_data()
  POSTGREST_URL         read-only PostgREST base URL (as used for the #295 audit)
  POSTGREST_API_KEY     optional key, sent as `apikey` + `Authorization: Bearer`

Usage:
  python scripts/validate_holdout_75_25.py [--families rf,mlp,xgboost,lightgbm,catboost]
                                           [--quantile 0.75] [--out report.json]

Candidate families mirror Track E's default (untuned) configurations; boosted
families are skipped with a warning if their library is not installed.
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

USING_POSTGREST = bool(os.environ.get('POSTGREST_URL')) and not os.environ.get('DATABASE_URL')
if USING_POSTGREST:
    # backtest.py refuses to import without DATABASE_URL; the PostgREST path
    # never touches backtest.engine, so a local placeholder is safe.
    os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')

import numpy as np
import pandas as pd

import backtest
import ml_predict
from strike_rate_matching import (
    build_strike_rate_lookup, build_strike_rate_history_lookup, normalize_name,
)

log = backtest.log

DEFAULT_FAMILIES = 'rf,mlp,xgboost,lightgbm,catboost'


# ── PostgREST data loading (mirrors backtest.load_historical_data's SQL) ─────

def _postgrest_fetch(table, select, order=None, page_size=10000):
    import requests

    base = os.environ.get('POSTGREST_URL').rstrip('/')
    headers = {'Accept': 'application/json'}
    api_key = os.environ.get('POSTGREST_API_KEY')
    if api_key:
        headers['apikey'] = api_key
        headers['Authorization'] = f'Bearer {api_key}'

    rows = []
    offset = 0
    while True:
        params = {'select': select, 'limit': page_size, 'offset': offset}
        if order:
            params['order'] = order
        response = requests.get(f'{base}/{table}', headers=headers, params=params, timeout=120)
        response.raise_for_status()
        page = response.json()
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    log.info("PostgREST: fetched %s rows from %s", len(rows), table)
    return pd.DataFrame(rows)


def load_data_via_postgrest():
    horses = _postgrest_fetch('horses', 'id,race_id,horse_name,csv_data,is_scratched')
    races = _postgrest_fetch('races', 'id,meeting_id,track_condition,distance,race_class,ratings_json,speed_maps_json')
    meetings = _postgrest_fetch('meetings', 'id,date,track,meeting_name,rail_position')
    results = _postgrest_fetch('results', 'horse_id,finish_position,sp')

    results = results[(results['finish_position'].fillna(0) > 0)]
    horses = horses[~horses['is_scratched'].fillna(False)]

    df = (horses.merge(results, left_on='id', right_on='horse_id', how='inner')
                .drop(columns=['horse_id'])
                .merge(races.rename(columns={'id': '_race_pk', 'distance': 'race_distance'}),
                       left_on='race_id', right_on='_race_pk', how='inner')
                .merge(meetings.rename(columns={'id': '_meeting_pk', 'date': 'meeting_date',
                                                'track': 'meeting_track'}),
                       left_on='meeting_id', right_on='_meeting_pk', how='inner'))
    df = df.rename(columns={'id': 'horse_id'})
    df = df.sort_values(['meeting_date', 'race_id', 'horse_id']).reset_index(drop=True)
    df = backtest.repair_missing_meeting_dates(df)

    # strike-rate lookups (current snapshot + dated history + A2E extras)
    sr = _postgrest_fetch(
        'strike_rates',
        'type,name,l100_wins,l100_runs,career_actual_to_expected,last100_actual_to_expected,career_runs,updated_at',
        order='updated_at.desc',
    )
    strike_rate_data = {'jockeys': {}, 'trainers': {}, 'jockeys_history': {}, 'trainers_history': {},
                        'jockeys_extra': {}, 'trainers_extra': {}}
    for sr_type, key, extra_key in (('jockey', 'jockeys', 'jockeys_extra'),
                                    ('trainer', 'trainers', 'trainers_extra')):
        subset = sr[sr['type'] == sr_type]
        strike_rate_data[key] = build_strike_rate_lookup(
            list(subset[['name', 'l100_wins', 'l100_runs']].itertuples(index=False, name=None)))
        extras = {}
        for row in subset.itertuples(index=False):
            norm = normalize_name(str(row.name or ''))
            if norm and norm not in extras:
                extras[norm] = {'career_a2e': row.career_actual_to_expected,
                                'l100_a2e': row.last100_actual_to_expected,
                                'career_runs': row.career_runs}
        strike_rate_data[extra_key] = extras

    try:
        snapshots = _postgrest_fetch(
            'strike_rate_snapshots', 'type,name,l100_wins,l100_runs,snapshot_date',
            order='snapshot_date.asc',
        )
        for sr_type, key in (('jockey', 'jockeys_history'), ('trainer', 'trainers_history')):
            subset = snapshots[snapshots['type'] == sr_type]
            strike_rate_data[key] = build_strike_rate_history_lookup(
                list(subset[['name', 'l100_wins', 'l100_runs', 'snapshot_date']]
                     .itertuples(index=False, name=None)))
    except Exception as e:
        log.warning("No strike_rate_snapshots via PostgREST (%s); training rows fall back "
                    "to the current snapshot, as backtest does.", e)

    # PuntingForm per-runner lookups from the races frame
    ratings_lookup = {}
    speedmaps_lookup = {}
    for row in races.itertuples(index=False):
        try:
            d = row.ratings_json
            while isinstance(d, str):
                d = json.loads(d)
            for item in ((d or {}).get('payLoad') or []):
                ratings_lookup[(item.get('raceId'), item.get('tabNo'))] = item
        except Exception:
            pass
        try:
            d = row.speed_maps_json
            while isinstance(d, str):
                d = json.loads(d)
            for pf_race in ((d or {}).get('payLoad') or []):
                rid = pf_race.get('raceId')
                for item in (pf_race.get('items') or []):
                    speedmaps_lookup[(rid, item.get('tabNo'))] = item
        except Exception:
            pass
    strike_rate_data['pf_ratings'] = ratings_lookup
    strike_rate_data['pf_speedmaps'] = speedmaps_lookup
    log.info("PostgREST: %s ratings entries, %s speed-map entries.",
             len(ratings_lookup), len(speedmaps_lookup))
    return df, strike_rate_data


# ── Candidate families (Track E default configurations, untuned) ─────────────

def build_candidates(families):
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    candidates = {}
    for family in families:
        try:
            if family == 'rf':
                candidates['rf'] = CalibratedClassifierCV(
                    RandomForestClassifier(
                        random_state=42, n_jobs=-1, class_weight='balanced_subsample',
                        n_estimators=250, max_depth=10, min_samples_leaf=15, max_features='sqrt',
                    ),
                    method='isotonic',
                    cv=TimeSeriesSplit(n_splits=backtest.WALK_FORWARD_N_SPLITS),
                )
            elif family == 'mlp':
                candidates['mlp'] = Pipeline([
                    ('scaler', StandardScaler()),
                    ('mlp', MLPClassifier(
                        hidden_layer_sizes=(64, 32), alpha=1e-3, batch_size=512,
                        learning_rate_init=1e-3, max_iter=200, early_stopping=True,
                        n_iter_no_change=10, validation_fraction=0.15, random_state=42,
                    )),
                ])
            elif family in ('xgboost', 'lightgbm', 'catboost'):
                candidates[family] = CalibratedClassifierCV(
                    backtest._optional_classifier(family),
                    method='isotonic',
                    cv=TimeSeriesSplit(n_splits=backtest.WALK_FORWARD_N_SPLITS),
                )
            else:
                log.warning("Unknown candidate family %r skipped.", family)
        except Exception as e:
            log.warning("Skipping %s (library unavailable or failed to construct): %s", family, e)
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--families', default=DEFAULT_FAMILIES)
    parser.add_argument('--quantile', type=float, default=0.75,
                        help='Dev fraction of the chronological split (default 0.75).')
    parser.add_argument('--out', default=None, help='Optional JSON report path.')
    args = parser.parse_args()

    if USING_POSTGREST:
        df, strike_rate_data = load_data_via_postgrest()
    elif os.environ.get('DATABASE_URL'):
        df, strike_rate_data = backtest.load_historical_data()
    else:
        print("ERROR: set DATABASE_URL (read-only) or POSTGREST_URL [+ POSTGREST_API_KEY].",
              file=sys.stderr)
        sys.exit(2)

    if len(df) < 200:
        print(f"ERROR: only {len(df)} horse-race rows available — not enough to validate.",
              file=sys.stderr)
        sys.exit(2)

    X, y_roi, y_won, sp_values, race_ids, horse_ids, meeting_dates = backtest.build_training_set(
        df, strike_rate_data
    )

    # Feature-contract sanity: training columns must be live-generatable.
    live_missing = [c for c in X.columns if c not in set(ml_predict.FEATURE_NAMES)]
    log.info("Feature contract: %s training columns, %s live-contract names, "
             "not_live_computable=%s", X.shape[1], len(ml_predict.FEATURE_NAMES), live_missing)

    dates = pd.to_datetime(pd.Series(meeting_dates), errors='coerce')
    dated_mask = ~dates.isna()
    if (~dated_mask).any():
        log.warning("Excluding %s undated rows (cannot be split chronologically).",
                    int((~dated_mask).sum()))
    keep = np.where(dated_mask.values)[0]
    X = X.iloc[keep].reset_index(drop=True)
    y_won = y_won.iloc[keep].reset_index(drop=True)
    sp_values = [sp_values[i] for i in keep]
    race_ids = [race_ids[i] for i in keep]
    dates = dates.iloc[keep].reset_index(drop=True)

    order = dates.argsort().values
    X = X.iloc[order].reset_index(drop=True)
    y_won = y_won.iloc[order].reset_index(drop=True)
    sp_values = pd.Series(sp_values).iloc[order].reset_index(drop=True)
    race_ids = [race_ids[i] for i in order]
    dates = dates.iloc[order].reset_index(drop=True)

    cutoff = dates.quantile(args.quantile)
    dev_mask = dates <= cutoff
    holdout_mask = ~dev_mask

    X_dev, X_hold = X[dev_mask].reset_index(drop=True), X[holdout_mask].reset_index(drop=True)
    y_dev, y_hold = y_won[dev_mask].reset_index(drop=True), y_won[holdout_mask].reset_index(drop=True)
    sp_dev = sp_values[dev_mask].reset_index(drop=True).tolist()
    sp_hold = np.asarray(sp_values[holdout_mask], dtype=float)
    race_ids_dev = [r for r, m in zip(race_ids, dev_mask) if m]
    race_ids_hold = [r for r, m in zip(race_ids, holdout_mask) if m]

    log.info("Split @ q=%.2f (cutoff=%s): dev=%s rows (%s..%s), UNTOUCHED holdout=%s rows (%s..%s)",
             args.quantile, cutoff.date(), len(X_dev),
             dates[dev_mask].min().date(), dates[dev_mask].max().date(),
             len(X_hold), dates[holdout_mask].min().date(), dates[holdout_mask].max().date())

    # Dev-only median imputation for the final holdout pass; walk-forward
    # re-derives its own per-fold medians internally.
    dev_median = X_dev.median()
    X_dev_filled = X_dev.fillna(dev_median)
    X_hold_filled = X_hold.fillna(dev_median)

    report = {
        'generated_at': datetime.utcnow().isoformat(),
        'rows_total': int(len(X)), 'rows_dev': int(len(X_dev)), 'rows_holdout': int(len(X_hold)),
        'cutoff_date': str(cutoff.date()),
        'dev_range': [str(dates[dev_mask].min().date()), str(dates[dev_mask].max().date())],
        'holdout_range': [str(dates[holdout_mask].min().date()), str(dates[holdout_mask].max().date())],
        'feature_count': int(X.shape[1]),
        'not_live_computable': live_missing,
        'families': {},
    }

    families = [f.strip() for f in args.families.split(',') if f.strip()]
    for family, model in build_candidates(families).items():
        log.info("── %s: walk-forward on dev 75%% ──", family)
        wf = backtest._walk_forward_metrics_for_model(model, X_dev, y_dev, sp_dev, race_ids_dev)
        fold_rois = [f['roi'] for f in wf['folds'] if f['bets'] > 0]
        wf_mean = float(np.mean(fold_rois)) if fold_rois else None

        log.info("── %s: single untouched-holdout evaluation ──", family)
        try:
            final_model = backtest._clone_for_fold_fit(model, y_dev)
            final_model.fit(X_dev_filled, y_dev)
            hold = backtest.evaluate_model_on_validation(
                final_model, X_hold_filled, y_hold, race_ids_hold, sp_hold)
        except Exception as e:
            log.error("Holdout evaluation failed for %s: %s", family, e)
            continue

        report['families'][family] = {
            'walk_forward': wf,
            'walk_forward_mean_fold_roi': wf_mean,
            'holdout': {
                'roi': hold['roi'], 'strike_rate': hold['strike_rate'],
                'bets': hold['number_of_bets'], 'winners': hold['winners'],
                'profit_units': hold['profit_units'], 'log_loss': hold['log_loss'],
                'brier_score': hold['brier_score'],
            },
        }
        log.info(
            "RESULT %s: dev walk-forward folds=%s mean_fold_roi=%s roi_std=%.2f | "
            "UNTOUCHED holdout roi=%.1f%% strike_rate=%.1f%% bets=%s winners=%s",
            family, wf['n_splits'],
            f"{wf_mean:.1f}%" if wf_mean is not None else "n/a", wf['roi_std'],
            hold['roi'], hold['strike_rate'], hold['number_of_bets'], hold['winners'],
        )

    print("\n" + "=" * 88)
    print(f"75/25 HELD-OUT VALIDATION — current {X.shape[1]}-feature pipeline")
    print(f"dev {report['dev_range'][0]}..{report['dev_range'][1]} ({report['rows_dev']} rows) | "
          f"untouched holdout {report['holdout_range'][0]}..{report['holdout_range'][1]} "
          f"({report['rows_holdout']} rows)")
    print("=" * 88)
    print(f"{'family':<10} {'wf folds':>8} {'wf mean ROI':>12} {'wf ROI std':>11} "
          f"{'holdout ROI':>12} {'holdout SR':>11} {'bets':>6}")
    for family, res in report['families'].items():
        wf_mean = res['walk_forward_mean_fold_roi']
        print(f"{family:<10} {res['walk_forward']['n_splits']:>8} "
              f"{(f'{wf_mean:.1f}%' if wf_mean is not None else 'n/a'):>12} "
              f"{res['walk_forward']['roi_std']:>10.2f} "
              f"{res['holdout']['roi']:>11.1f}% {res['holdout']['strike_rate']:>10.1f}% "
              f"{res['holdout']['bets']:>6}")
    if live_missing:
        print(f"\nWARNING: {len(live_missing)} training feature(s) not live-computable: {live_missing}")

    if args.out:
        with open(args.out, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nFull report written to {args.out}")


if __name__ == '__main__':
    main()
