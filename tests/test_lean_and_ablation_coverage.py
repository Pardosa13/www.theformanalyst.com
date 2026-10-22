"""Fewer inputs (lean candidate) and judging input groups only on enough data."""
import json
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("lightgbm")

import feature_ablation
from model_classes import LeanLightGBMClassifier


def _data(n=3000, n_noise=40, seed=0):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({f'noise_{i}': rng.normal(size=n) for i in range(n_noise)})
    X['signal_a'] = rng.normal(size=n)
    X['signal_b'] = rng.normal(size=n)
    logit = -2 + 1.5 * X['signal_a'] - 1.0 * X['signal_b']
    y = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(int)
    return X, y


def test_lean_model_keeps_the_useful_inputs_and_drops_the_rest():
    X, y = _data()
    model = LeanLightGBMClassifier(top_k=5).fit(X, y)
    assert len(model.kept_columns_) == 5
    assert {'signal_a', 'signal_b'} <= set(model.kept_columns_)
    # The artifact still takes the full input list.
    assert list(model.feature_names_in_) == list(X.columns)
    assert model.predict_proba(X).shape == (len(X), 2)


def test_lean_model_honours_ablation_drops():
    X, y = _data()
    model = LeanLightGBMClassifier(top_k=5, drop_columns=('signal_a',)).fit(X, y)
    assert 'signal_a' not in model.kept_columns_


def test_coverage_and_too_little_data():
    X = pd.DataFrame({'a': [1, None, None, None], 'b': [None] * 4, 'c': [1, 2, 3, 4]})
    cov = feature_ablation.group_coverage_pct(X, {'g1': ['a'], 'g2': ['b', 'c'], 'g3': ['missing']})
    assert cov == {'g1': 25.0, 'g2': 100.0, 'g3': None}


def test_race_relative_extras_are_measured_as_a_group():
    cols = ['pfai_score', 'pfai_score_race_rank', 'pfai_score_vs_race_best', 'pfai_score_race_percentile']
    variants, present = feature_ablation.build_variants(cols)
    assert present['race_relative_extras'] == ['pfai_score_vs_race_best', 'pfai_score_race_percentile']
    assert variants['all-race_relative_extras'] == ['pfai_score', 'pfai_score_race_rank']


def test_latest_drop_columns_reads_only_drop_verdicts():
    from sqlalchemy import create_engine, text
    engine = create_engine('sqlite:///:memory:')
    report = {'run_id': 9, 'verdicts': {
        'debut_weight': {'verdict': 'DROP'},
        'pace_shape': {'verdict': 'TOO_LITTLE_DATA'},
        'race_relative_extras': {'verdict': 'KEEP'},
    }}
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE feature_ablation_runs (id INTEGER PRIMARY KEY, run_id INTEGER, "
                          "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, report_json TEXT)"))
        conn.execute(text("INSERT INTO feature_ablation_runs (run_id, report_json) VALUES (9, :j)"),
                     {'j': json.dumps(report)})
    cols = ['rs_is_debut', 'rs_weight_lengths', 'rs_n_speed', 'other']
    assert feature_ablation.latest_drop_columns(engine, cols) == ['rs_is_debut', 'rs_weight_lengths']


def test_no_report_means_nothing_dropped():
    from sqlalchemy import create_engine
    assert feature_ablation.latest_drop_columns(create_engine('sqlite:///:memory:'), ['a']) == []
    assert feature_ablation.latest_drop_columns(None, ['a']) == []


def test_ml_data_page_shows_the_ablation_report():
    from pathlib import Path
    source = Path('app.py').read_text()
    start = source.index('def ml_data_analytics(')
    end = source.index('\n@app.route(', start)
    assert 'feature_ablation.latest_report(db.engine)' in source[start:end]
    template = Path('templates/ml_data.html').read_text()
    assert 'Which Inputs Help the Model (Nightly Ablation)' in template
    assert "'TOO_LITTLE_DATA': ('Too little data yet'" in template


def test_competition_gets_the_ablation_drops():
    from pathlib import Path
    source = Path('backtest.py').read_text()
    assert 'lean_drop_columns = feature_ablation.latest_drop_columns(engine, X.columns)' in source
    assert "candidates['lightgbm_lean'] = LeanLightGBMClassifier(" in source
