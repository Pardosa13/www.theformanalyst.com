"""Which groups of ML inputs actually help? (diagnostic only)

Trains the same model, on the same races and the same walk-forward folds,
with and without each group of inputs, and reports the difference. It never
touches champion promotion or live scoring.

Variants:
  baseline        the inputs the model had before the 2026-10 additions
  +<group>        baseline plus one group
  all             everything
  all-<group>     everything except one group
  baseline seed2  baseline again with a different random seed: how much the
                  numbers move on noise alone, so a group's difference can be
                  judged against it

Main measure: race log loss vs the market (model minus FLB-corrected market,
lower is better), averaged over folds. It uses every runner in every race, so
it is far less noisy than top-pick ROI. Mean fold ROI and strike rate of the
top pick are reported alongside.

Run on its own:   python feature_ablation.py
Nightly:          backtest.main() calls run_feature_ablation() at the end
                  unless FEATURE_ABLATION=0.
"""
import json
import logging
import os
import time

import numpy as np
import pandas as pd

from past_runs import PAST_RUN_FEATURES, PAST_RUN_RELATIVE_COLS
from pf_form import PF_FORM_FEATURES, PF_FORM_RELATIVE_COLS
from track_bias import TRACK_BIAS_FEATURES

log = logging.getLogger('feature_ablation')


def _with_relative(cols, relative_cols):
    out = list(cols)
    for col in relative_cols:
        out += [f'{col}_race_rank', f'{col}_vs_race_avg']
    return out


# PuntingForm ratings/speed-map inputs: present before, but fed from the wrong
# race until the 2026-10 race-id fix, so worth measuring on their own.
PF_RATINGS_COLS = [
    'pf_time_rank', 'pf_time_price', 'pf_early_time_rank', 'pf_weight_class_rank',
    'pf_adj_weight_class_rank', 'pf_class_change', 'pf_predicted_settle',
    'pf_avg_hist_settle', 'pf_run_style', 'pf_is_reliable', 'pfai_price', 'pfai_rank',
    'sm_assessed_price', 'sm_speed', 'sm_settle', 'sm_map_a2e', 'sm_jockey_a2e',
    'sm_rated_run_style', 'sm_rated_settle', 'sm_assessed_prob',
    'pf_time_rank_race_rank', 'pf_time_rank_vs_race_avg',
    'pf_weight_class_rank_race_rank', 'pf_weight_class_rank_vs_race_avg',
    'pfai_price_race_rank', 'pfai_price_vs_race_avg',
    'sm_assessed_prob_race_rank', 'sm_assessed_prob_vs_race_avg',
    'sm_settle_race_rank', 'sm_settle_vs_race_avg',
    'pf_early_time_rank_race_rank', 'pf_early_time_rank_vs_race_avg',
]

# In-run columns from the form feed that belong to the pace-shape group.
PACE_FORM_COLS = ['ir_speed_points_l3', 'ir_settle_vs_best']

FEATURE_GROUPS = {
    'past_runs': _with_relative(PAST_RUN_FEATURES, PAST_RUN_RELATIVE_COLS),
    'ratings_prices': ['pf_early_time_price', 'pf_weight_class_price', 'pf_adj_weight_class_price'],
    'track_bias': _with_relative(TRACK_BIAS_FEATURES, ['tb_barrier_win_edge', 'tb_style_win_edge']),
    'pf_form_feed': [c for c in _with_relative(PF_FORM_FEATURES, PF_FORM_RELATIVE_COLS)
                     if c not in PACE_FORM_COLS],
    'pf_ratings': PF_RATINGS_COLS,
    # Late Oct 2026: first starters and weight (full history) ...
    'debut_weight': ['rs_is_debut', 'rs_debut_share', 'rs_debut_x_share',
                     'rs_weight_lengths', 'rs_weight_over_53_5'],
    # ... and pace shape (needs the saved form feed, so only from 19 Oct 2026).
    'pace_shape': PACE_FORM_COLS + ['rs_n_speed', 'rs_race_speed_total', 'rs_rival_speed_max',
                                    'rs_lone_speed', 'rs_style_change'],
}
# Groups added in 2026-10 (pf_ratings existed before, so it stays in baseline).
NEW_GROUPS = ('past_runs', 'ratings_prices', 'track_bias', 'pf_form_feed', 'debut_weight', 'pace_shape')


def build_variants(columns):
    """{variant name: column list}, every list in the training matrix's own order."""
    columns = list(columns)
    present = {g: [c for c in cols if c in set(columns)] for g, cols in FEATURE_GROUPS.items()}
    new_cols = {c for g in NEW_GROUPS for c in present[g]}
    baseline = [c for c in columns if c not in new_cols]
    variants = {'baseline': baseline}
    for group in NEW_GROUPS:
        if present[group]:
            keep = set(baseline) | set(present[group])
            variants[f'+{group}'] = [c for c in columns if c in keep]
    variants['all'] = columns
    for group, cols in present.items():
        if cols:
            drop = set(cols)
            variants[f'all-{group}'] = [c for c in columns if c not in drop]
    return variants, present


def _model(seed=42):
    from lightgbm import LGBMClassifier
    from model_classes import RaceConditionalLogit
    return RaceConditionalLogit(LGBMClassifier(
        n_estimators=180, max_depth=5, learning_rate=0.06, num_leaves=31,
        subsample=0.9, subsample_freq=1, colsample_bytree=0.9,
        random_state=seed, n_jobs=-1, verbosity=-1,
    ))


def _summarise(walk_forward):
    folds = [f for f in walk_forward.get('folds', []) if f.get('bets')]
    if not folds:
        return None

    def mean(key):
        vals = [f[key] for f in folds if f.get(key) is not None]
        return float(np.mean(vals)) if vals else None

    return {
        'folds': len(folds),
        'race_log_loss_vs_market': mean('race_log_loss_vs_market'),
        'roi': mean('roi'),
        'strike_rate': mean('strike_rate'),
        'fold_rois': [round(f['roi'], 1) for f in folds],
        'fold_log_loss_vs_market': [round(f['race_log_loss_vs_market'], 4)
                                    for f in folds if f.get('race_log_loss_vs_market') is not None],
    }


def _group_importance(X, y, race_ids, present):
    """Share of the full model's total gain that each group accounts for."""
    from lightgbm import LGBMClassifier
    model = LGBMClassifier(n_estimators=180, max_depth=5, learning_rate=0.06, num_leaves=31,
                           subsample=0.9, subsample_freq=1, colsample_bytree=0.9,
                           random_state=42, n_jobs=-1, verbosity=-1, importance_type='gain')
    model.fit(X.fillna(X.median()), y)
    gain = pd.Series(model.feature_importances_, index=X.columns, dtype=float)
    total = float(gain.sum()) or 1.0
    by_group = {g: round(100.0 * float(gain[[c for c in cols if c in gain.index]].sum()) / total, 1)
                for g, cols in present.items()}
    grouped = {c for cols in present.values() for c in cols}
    by_group['everything_else'] = round(100.0 * float(gain[[c for c in gain.index if c not in grouped]].sum()) / total, 1)
    new_cols = [c for g in NEW_GROUPS for c in present.get(g, [])]
    top_new = gain[new_cols].sort_values(ascending=False).head(15) if new_cols else pd.Series(dtype=float)
    return by_group, [(name, round(100.0 * float(v) / total, 2)) for name, v in top_new.items()]


def run_feature_ablation(X, y_won, sp_values, race_ids, meeting_dates, run_id=None, engine=None):
    """Run every variant and log/persist the comparison. Returns the report dict."""
    from backtest import WALK_FORWARD_N_SPLITS, _walk_forward_metrics_for_model

    started = time.time()
    dates = pd.to_datetime(pd.Series(meeting_dates), errors='coerce')
    keep = np.where(~dates.isna().values)[0]
    order = keep[dates.iloc[keep].argsort().values]
    X = X.iloc[order].reset_index(drop=True)
    y = pd.Series(y_won).iloc[order].reset_index(drop=True)
    sp = [sp_values[i] for i in order]
    rids = [race_ids[i] for i in order]

    variants, present = build_variants(X.columns)
    log.info("FEATURE_ABLATION start run_id=%s rows=%s variants=%s groups=%s", run_id, len(X),
             len(variants) + 1, {g: len(c) for g, c in present.items()})

    results = {}
    jobs = list(variants.items()) + [('baseline seed2', variants['baseline'])]
    for name, cols in jobs:
        seed = 7 if name.endswith('seed2') else 42
        try:
            wf = _walk_forward_metrics_for_model(_model(seed), X[cols], y, sp, rids,
                                                 n_splits=WALK_FORWARD_N_SPLITS)
            results[name] = _summarise(wf)
            if results[name]:
                results[name]['n_features'] = len(cols)
            log.info("FEATURE_ABLATION variant=%s features=%s result=%s", name, len(cols),
                     json.dumps(results[name]))
        except Exception as exc:
            log.warning("FEATURE_ABLATION variant=%s failed: %s", name, exc)
            results[name] = None

    base = results.get('baseline') or {}
    noise = None
    if base.get('race_log_loss_vs_market') is not None and \
            (results.get('baseline seed2') or {}).get('race_log_loss_vs_market') is not None:
        noise = abs(results['baseline seed2']['race_log_loss_vs_market'] - base['race_log_loss_vs_market'])

    def ll(name):
        return (results.get(name) or {}).get('race_log_loss_vs_market')

    lines = []
    for name, res in results.items():
        if not res or ll(name) is None or ll('baseline') is None:
            continue
        lines.append(f"{name:<22} feats={res['n_features']:>3} logloss_vs_mkt={ll(name):+.4f} "
                     f"roi={res['roi']:+.1f}% sr={res['strike_rate']:.1f}% folds={res['fold_rois']}")

    # One verdict per group, from both directions: adding it to the baseline,
    # and removing it from everything. Negative = the group lowers log loss
    # (helps). It has to beat twice the seed-to-seed noise both ways to count.
    verdicts = {}
    threshold = 2 * max(noise or 0.0, 1e-4)
    for group in FEATURE_GROUPS:
        add = (ll(f'+{group}') - ll('baseline')) if ll(f'+{group}') is not None and ll('baseline') is not None else None
        remove = (ll('all') - ll(f'all-{group}')) if ll(f'all-{group}') is not None and ll('all') is not None else None
        effects = [e for e in (add, remove) if e is not None]
        if not effects or noise is None:
            verdict = 'UNCLEAR'
        elif all(e < -threshold for e in effects):
            verdict = 'KEEP'
        elif all(e > threshold for e in effects):
            verdict = 'DROP'
        else:
            verdict = 'UNCLEAR'
        verdicts[group] = {'add_effect': add, 'remove_effect': remove, 'verdict': verdict}
        lines.append(f"VERDICT {group:<16} {verdict:<8} add={add if add is None else round(add, 4)} "
                     f"remove={remove if remove is None else round(remove, 4)} (negative = helps)")

    try:
        importance, top_new = _group_importance(X, y, rids, present)
    except Exception as exc:
        log.warning("FEATURE_ABLATION importance failed: %s", exc)
        importance, top_new = {}, []

    report = {
        'run_id': run_id, 'rows': len(X), 'seconds': round(time.time() - started),
        'noise_log_loss': noise, 'results': results, 'verdicts': verdicts, 'summary': lines,
        'gain_share_by_group_pct': importance, 'top_new_inputs_gain_pct': top_new,
    }
    log.info("FEATURE_ABLATION SUMMARY (lower log loss vs market is better; noise floor=%s)\n%s",
             f"{noise:.4f}" if noise is not None else 'n/a', "\n".join(lines))
    log.info("FEATURE_ABLATION gain share by group (%%): %s", importance)
    log.info("FEATURE_ABLATION top new inputs by gain (%%): %s", top_new)
    log.info("FEATURE_ABLATION done in %ss", report['seconds'])

    if engine is not None:
        try:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS feature_ablation_runs (
                        id SERIAL PRIMARY KEY,
                        run_id INTEGER,
                        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                        report_json TEXT
                    )
                """))
                conn.execute(text("INSERT INTO feature_ablation_runs (run_id, report_json) VALUES (:r, :j)"),
                             {'r': run_id, 'j': json.dumps(report, default=str)})
        except Exception as exc:
            log.warning("FEATURE_ABLATION could not save report: %s", exc)
    return report


def enabled():
    return os.environ.get('FEATURE_ABLATION', '1').strip().lower() not in ('0', 'false', 'no', 'off')


if __name__ == '__main__':
    import backtest

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    df, strike_rate_data = backtest.load_historical_data()
    X, _, y_won, sp_values, race_ids, _, meeting_dates = backtest.build_training_set(df, strike_rate_data)
    run_feature_ablation(X, y_won, sp_values, race_ids, meeting_dates, engine=backtest.engine)
