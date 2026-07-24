import json
import os
import pickle
import sys
import types
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

# numpy/pandas are core, always-installed dependencies (unlike sklearn/
# xgboost/etc below, which this file stubs out to stay runnable without the
# heavier ML stack). Import them for real up front so backtest.py's own
# `import numpy as np` / `import pandas as pd` — and every other test module
# that imports backtest.py after this one via the shared sys.modules cache —
# never bind to a bare stub module regardless of import order across the
# whole test session.
import numpy  # noqa: F401
import pandas  # noqa: F401

if "sqlalchemy" not in sys.modules:
    sqlalchemy = types.ModuleType("sqlalchemy")
    sqlalchemy.create_engine = lambda *args, **kwargs: None
    sqlalchemy.text = lambda sql: sql
    sys.modules["sqlalchemy"] = sqlalchemy
    sqlalchemy_orm = types.ModuleType("sqlalchemy.orm")
    sqlalchemy_orm.sessionmaker = lambda *args, **kwargs: None
    sys.modules["sqlalchemy.orm"] = sqlalchemy_orm

if "joblib" not in sys.modules:
    joblib_stub = types.ModuleType("joblib")
    joblib_stub.load = lambda filename: pickle.load(open(filename, "rb"))
    sys.modules["joblib"] = joblib_stub

if "numpy" not in sys.modules:
    sys.modules["numpy"] = types.ModuleType("numpy")

if "pandas" not in sys.modules:
    sys.modules["pandas"] = types.ModuleType("pandas")

if "sklearn" not in sys.modules:
    sklearn = types.ModuleType("sklearn")
    sys.modules["sklearn"] = sklearn
    ensemble = types.ModuleType("sklearn.ensemble")
    ensemble.RandomForestRegressor = type("RandomForestRegressor", (), {})
    ensemble.RandomForestClassifier = type("RandomForestClassifier", (), {})
    sys.modules["sklearn.ensemble"] = ensemble
    base = types.ModuleType("sklearn.base")
    base.BaseEstimator = type("BaseEstimator", (), {})
    base.RegressorMixin = type("RegressorMixin", (), {})
    base.clone = lambda estimator: estimator
    sys.modules["sklearn.base"] = base
    model_selection = types.ModuleType("sklearn.model_selection")
    model_selection.TimeSeriesSplit = type("TimeSeriesSplit", (), {})
    model_selection.StratifiedKFold = type("StratifiedKFold", (), {})
    sys.modules["sklearn.model_selection"] = model_selection
    preprocessing = types.ModuleType("sklearn.preprocessing")
    preprocessing.LabelEncoder = type("LabelEncoder", (), {})
    preprocessing.StandardScaler = type("StandardScaler", (), {})
    sys.modules["sklearn.preprocessing"] = preprocessing
    pipeline = types.ModuleType("sklearn.pipeline")
    pipeline.Pipeline = type("Pipeline", (), {})
    sys.modules["sklearn.pipeline"] = pipeline
    neural_network = types.ModuleType("sklearn.neural_network")
    neural_network.MLPClassifier = type("MLPClassifier", (), {})
    sys.modules["sklearn.neural_network"] = neural_network
    metrics = types.ModuleType("sklearn.metrics")
    metrics.mean_squared_error = lambda *args, **kwargs: 0.0
    metrics.log_loss = lambda *args, **kwargs: 0.0
    metrics.brier_score_loss = lambda *args, **kwargs: 0.0
    sys.modules["sklearn.metrics"] = metrics
    calibration = types.ModuleType("sklearn.calibration")
    calibration.CalibratedClassifierCV = type("CalibratedClassifierCV", (), {})
    sys.modules["sklearn.calibration"] = calibration

import backtest


class DummySavedModel:
    # Real live-contract feature names: save_best_model_to_db now rejects
    # promotion for artifacts trained on features ml_predict.py cannot
    # generate, so the dummy artifact must use live-computable names.
    feature_names_in_ = ["horse_age", "horse_weight"]


class FetchResult:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, champion=None, challenger_id=200):
        self.champion = champion
        self.challenger_id = challenger_id
        self.inserted_challenger = None
        self.deactivated_champions = False
        self.activated_challenger = None
        self.rejected_challenger = None
        self.promotion_history = None
        self.committed = False
        self.champion_promoted_at = datetime.utcnow() - timedelta(days=1)
        self.pipeline_alerts = []
        self.resolved_alert_keys = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "SELECT id, validation_roi, validation_strike_rate" in sql:
            return FetchResult(self.champion)
        if "INSERT INTO backtest_best_model" in sql:
            self.inserted_challenger = params
            return FetchResult([self.challenger_id])
        if "SET is_active = FALSE" in sql:
            self.deactivated_champions = True
            self.retention_days = params["retention_days"]
            return FetchResult(None)
        if "SET is_active = TRUE" in sql:
            self.activated_challenger = params
            return FetchResult(None)
        if "INSERT INTO backtest_model_promotions" in sql:
            self.promotion_history = params
            return FetchResult(None)
        if "UPDATE backtest_best_model SET promotion_reason" in sql:
            self.rejected_challenger = params
            return FetchResult(None)
        if "SELECT id FROM ml_pipeline_alerts" in sql:
            return FetchResult(None)
        if "INSERT INTO ml_pipeline_alerts" in sql:
            self.pipeline_alerts.append(params)
            return FetchResult(None)
        if "UPDATE ml_pipeline_alerts SET message" in sql:
            self.pipeline_alerts.append(params)
            return FetchResult(None)
        if "UPDATE ml_pipeline_alerts SET resolved_at" in sql:
            self.resolved_alert_keys.append(params.get("key"))
            return FetchResult(None)
        raise AssertionError(f"Unhandled SQL in fake connection: {sql}")

    def commit(self):
        self.committed = True


class FakeEngine:
    def __init__(self, conn):
        self.conn = conn

    def connect(self):
        return self.conn


def champion_row(champion_score=10.0):
    champion_metrics = {
        "selection_score": champion_score,
        "scoring_formula_version": backtest.SCORING_FORMULA_VERSION,
        "roi": 4.0,
        "strike_rate": 22.0,
        "log_loss": 0.6,
        "brier_score": 0.2,
        "calibration": {"expected_calibration_error": 0.01},
        "stability": {"roi_last_100": 4.0, "roi_last_250": 4.0},
        "walk_forward": {
            "folds": [{"roi": 4.0, "strike_rate": 22.0, "bets": 50} for _ in range(backtest.MIN_WALK_FORWARD_FOLDS)],
            "roi_std": 0.5,
        },
    }
    return [
        101,  # id
        12.0,  # validation_roi
        22.0,  # validation_strike_rate
        15.0,  # validation_profit_units
        150,  # validation_bets
        4.0,  # validation_drawdown
        5,  # validation_longest_losing_streak
        1.2,  # validation_bankroll_growth
        0.8,  # validation_volatility
        champion_score,  # combined_score / Champion Score
        json.dumps(champion_metrics),
    ]


def metrics(selection_score=11.0, roi=5.0, strike_rate=20.0, bets=150, walk_forward_folds=2):
    data = {
        "selection_score": selection_score,
        "roi": roi,
        "strike_rate": strike_rate,
        "number_of_bets": bets,
        "profit_units": 8.0,
        "drawdown": 3.0,
        "longest_losing_streak": 4,
        "bankroll_growth": 1.1,
        "volatility": 0.7,
        "log_loss": 0.6,
        "brier_score": 0.2,
        "calibration": {"expected_calibration_error": 0.01},
        "stability": {"roi_last_100": roi, "roi_last_250": roi},
        "scoring_formula_version": backtest.SCORING_FORMULA_VERSION,
    }
    if walk_forward_folds is not None:
        data["walk_forward"] = {
            "folds": [
                {"roi": 5.0 + i, "strike_rate": 20.0 + i, "bets": 50}
                for i in range(walk_forward_folds)
            ],
            "roi_std": 0.5,
        }
    return data


def save_model_with_fake_db(monkeypatch, tmp_path, conn, validation_metrics):
    pkl_file = tmp_path / "challenger.pkl"
    with open(pkl_file, "wb") as model_file:
        pickle.dump(DummySavedModel(), model_file)
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(backtest.joblib, "load", lambda filename: pickle.load(open(filename, "rb")))

    backtest.save_best_model_to_db(
        str(pkl_file),
        combined_score=validation_metrics["selection_score"],
        run_id=321,
        model_type="xgboost",
        model_name="XGBoost Challenger",
        validation_metrics=validation_metrics,
    )

    return conn


class NotLiveScorableSavedModel:
    feature_names_in_ = ["horse_age", "training_only_made_up_feature"]


def test_challenger_trained_on_non_live_computable_features_cannot_promote(monkeypatch, tmp_path):
    """An otherwise-winning challenger whose artifact was trained on a feature
    live scoring (ml_predict.py) cannot generate must not become champion —
    its edge would be silently median-filled away on every real meeting."""
    conn = FakeConnection(champion=champion_row(10.0))

    pkl_file = tmp_path / "challenger.pkl"
    with open(pkl_file, "wb") as model_file:
        pickle.dump(NotLiveScorableSavedModel(), model_file)
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(backtest.joblib, "load", lambda filename: pickle.load(open(filename, "rb")))

    backtest.save_best_model_to_db(
        str(pkl_file),
        combined_score=13.0,
        run_id=321,
        model_type="xgboost",
        model_name="XGBoost Challenger",
        validation_metrics=metrics(selection_score=13.0),
    )

    assert conn.activated_challenger is None
    assert conn.deactivated_champions is False
    assert conn.promotion_history is None
    assert conn.rejected_challenger["id"] == conn.challenger_id
    assert "cannot be generated by live scoring" in conn.rejected_challenger["reason"]
    assert "training_only_made_up_feature" in conn.rejected_challenger["reason"]


def test_better_challenger_promotes_immediately_when_champion_is_under_seven_days_old(monkeypatch, tmp_path):
    conn = FakeConnection(champion=champion_row(10.0))

    # Margin (13.0 - 10.0 = 3.0) clears PROMOTION_SELECTION_SCORE_EDGE (1.0) —
    # promotion requires a real improvement, not just any positive delta.
    save_model_with_fake_db(monkeypatch, tmp_path, conn, metrics(selection_score=13.0))

    assert conn.champion_promoted_at > datetime.utcnow() - timedelta(days=7)
    assert conn.deactivated_champions is True
    assert conn.activated_challenger["id"] == conn.challenger_id
    assert "Promoted: challenger Champion Score 13.000 beat Champion Score 10.000" in conn.activated_challenger["reason"]
    assert conn.rejected_challenger is None
    assert conn.committed is True


def test_otherwise_qualified_challenger_without_walk_forward_folds_cannot_promote(monkeypatch, tmp_path):
    conn = FakeConnection(champion=champion_row(10.0))

    save_model_with_fake_db(
        monkeypatch,
        tmp_path,
        conn,
        metrics(selection_score=13.0, walk_forward_folds=None),
    )

    assert conn.activated_challenger is None
    assert conn.deactivated_champions is False
    assert conn.promotion_history is None
    assert conn.rejected_challenger["id"] == conn.challenger_id
    assert "Cannot promote: model has 0 walk-forward fold(s)" in conn.rejected_challenger["reason"]


def test_marginal_challenger_within_score_edge_does_not_promote(monkeypatch, tmp_path):
    """A challenger that only barely beats the champion (less than
    PROMOTION_SELECTION_SCORE_EDGE) must NOT be promoted — otherwise the
    champion could be swapped on noise from a single validation window rather
    than a real, repeatable improvement."""
    conn = FakeConnection(champion=champion_row(10.0))

    save_model_with_fake_db(monkeypatch, tmp_path, conn, metrics(selection_score=10.5, walk_forward_folds=None))

    assert conn.activated_challenger is None
    assert conn.deactivated_champions is False
    assert conn.promotion_history is None
    assert conn.rejected_challenger["id"] == conn.challenger_id
    assert conn.rejected_challenger["reason"] == (
        "Rejected: challenger Champion Score 10.500 did not beat Champion Score 10.000"
    )


def test_worse_challenger_remains_rejected(monkeypatch, tmp_path):
    conn = FakeConnection(champion=champion_row(10.0))

    save_model_with_fake_db(monkeypatch, tmp_path, conn, metrics(selection_score=9.5))

    assert conn.activated_challenger is None
    assert conn.deactivated_champions is False
    assert conn.promotion_history is None
    assert conn.rejected_challenger["id"] == conn.challenger_id
    assert conn.rejected_challenger["reason"] == (
        "Rejected: challenger Champion Score 9.500 did not beat Champion Score 10.000"
    )


def test_validation_failures_still_block_higher_scoring_challenger(monkeypatch, tmp_path):
    conn = FakeConnection(champion=champion_row(10.0))

    save_model_with_fake_db(
        monkeypatch,
        tmp_path,
        conn,
        metrics(selection_score=12.0, roi=-1.0, strike_rate=20.0, bets=150),
    )

    assert conn.activated_challenger is None
    assert conn.deactivated_champions is False
    assert conn.promotion_history is None
    assert conn.rejected_challenger["reason"] == "Rejected: challenger validation ROI is not positive"


def test_promotion_preserves_model_history_and_rollback_records(monkeypatch, tmp_path):
    conn = FakeConnection(champion=champion_row(10.0))

    save_model_with_fake_db(monkeypatch, tmp_path, conn, metrics(selection_score=12.0))

    assert conn.deactivated_champions is True
    assert conn.retention_days == backtest.CHAMPION_ROLLBACK_RETENTION_DAYS
    assert conn.promotion_history["old_champion_id"] == 101
    assert conn.promotion_history["new_champion_id"] == conn.challenger_id
    assert conn.promotion_history["run_id"] == 321
    assert conn.promotion_history["model_type"] == "xgboost"
    assert "Champion Score 12.000 beat Champion Score 10.000" in conn.promotion_history["reason"]


def test_champion_without_walk_forward_folds_records_durable_alert(monkeypatch, tmp_path):
    """champion_row() never carries walk_forward.folds — the same situation as
    Champion 74, promoted before walk-forward evaluation existed. This must
    open a durable ml_pipeline_alerts row (not just a log line) per the
    walk_forward_fold_count invariant."""
    conn = FakeConnection(champion=champion_row(10.0))

    save_model_with_fake_db(monkeypatch, tmp_path, conn, metrics(selection_score=10.5))

    assert len(conn.pipeline_alerts) == 1
    alert = conn.pipeline_alerts[0]
    assert alert["key"] == "champion_missing_walk_forward_validation"
    assert alert["severity"] == "blocking"
    assert "id=101" in alert["message"]


def test_promoting_a_validated_challenger_over_stale_champion_resolves_alert(monkeypatch, tmp_path):
    """When a stale champion is finally replaced by a challenger that DOES
    carry enough walk-forward folds, the open alert should be resolved — the
    promotion is itself the rollback review the alert was asking for."""
    conn = FakeConnection(champion=champion_row(10.0))
    challenger_metrics = metrics(selection_score=13.0)
    challenger_metrics["walk_forward"] = {
        "folds": [{"roi": 5.0, "strike_rate": 20.0, "bets": 50}, {"roi": 6.0, "strike_rate": 21.0, "bets": 50}],
        "roi_std": 0.5,
    }

    save_model_with_fake_db(monkeypatch, tmp_path, conn, challenger_metrics)

    assert conn.activated_challenger is not None
    assert "champion_missing_walk_forward_validation" in conn.resolved_alert_keys


def test_non_stale_champion_resolves_any_previously_open_alert(monkeypatch, tmp_path):
    """A champion carrying enough walk-forward folds must not be flagged as
    stale, and any previously-open alert for it should be cleared."""
    walk_forward = {
        "folds": [{"roi": 4.0, "strike_rate": 18.0, "bets": 60}, {"roi": 5.0, "strike_rate": 19.0, "bets": 60}],
        "roi_std": 0.5,
    }
    champion_metrics_blob = {"selection_score": 10.0, "walk_forward": walk_forward}
    champion = champion_row(10.0)
    champion[10] = json.dumps(champion_metrics_blob)
    conn = FakeConnection(champion=champion)

    save_model_with_fake_db(monkeypatch, tmp_path, conn, metrics(selection_score=10.5))

    assert conn.pipeline_alerts == []
    assert "champion_missing_walk_forward_validation" in conn.resolved_alert_keys


def test_bootstrap_significance_gate_blocks_noisy_score_edge_win():
    """A challenger that clears PROMOTION_SELECTION_SCORE_EDGE on the headline
    Champion Score but whose walk-forward fold-level ROI is not consistently
    better than the champion's (here: one big win, one loss, vs a steady
    champion) should fail the paired-bootstrap significance gate."""
    challenger_folds = [1.0, -50.0]
    champion_folds = [-2.0, -3.0]
    p_value = backtest._paired_bootstrap_p_value(challenger_folds, champion_folds)
    assert p_value is not None
    assert p_value > backtest.PROMOTION_MAX_BOOTSTRAP_P_VALUE


def test_bootstrap_significance_gate_passes_consistent_improvement():
    """A challenger that beats the champion on every walk-forward fold should
    clear the significance gate (low bootstrap p-value)."""
    challenger_folds = [10.0, 12.0, 11.0]
    champion_folds = [-5.0, -4.0, -6.0]
    p_value = backtest._paired_bootstrap_p_value(challenger_folds, champion_folds)
    assert p_value is not None
    assert p_value <= backtest.PROMOTION_MAX_BOOTSTRAP_P_VALUE


def test_bootstrap_significance_gate_skipped_with_fewer_than_two_folds():
    assert backtest._paired_bootstrap_p_value([5.0], [-5.0]) is None
    assert backtest._paired_bootstrap_p_value([], []) is None


def test_validation_windows_overlap_note_flags_disjoint_windows():
    challenger_window = {'start': '2026-06-01', 'end': '2026-06-30'}
    champion_window = {'start': '2026-01-01', 'end': '2026-01-31'}
    comparable, note = backtest._validation_windows_overlap_note(challenger_window, champion_window)
    assert comparable is False
    assert '2026-06-01' in note and '2026-01-31' in note


def test_validation_windows_overlap_note_passes_overlapping_windows():
    challenger_window = {'start': '2026-06-01', 'end': '2026-06-30'}
    champion_window = {'start': '2026-06-15', 'end': '2026-07-15'}
    comparable, note = backtest._validation_windows_overlap_note(challenger_window, champion_window)
    assert comparable is True
    assert note == ""


def test_validation_windows_overlap_note_treats_missing_window_as_comparable():
    # An old champion row saved before validation_period existed has no
    # window to compare against — this must not manufacture a false alarm.
    comparable, note = backtest._validation_windows_overlap_note({'start': '2026-06-01', 'end': '2026-06-30'}, {})
    assert comparable is True
    assert note == ""
    comparable, note = backtest._validation_windows_overlap_note(None, None)
    assert comparable is True


def test_value_edge_backtest_filters_out_low_edge_selections():
    # Two races: race 1's top pick has a big edge over the market and wins;
    # race 2's top pick has almost no edge (pred barely above 1/sp) and loses.
    # A min_edge filter should drop race 2 and keep race 1, raising ROI.
    selections = pandas.DataFrame({
        'pred': [0.60, 0.21],
        'won': [1, 0],
        'sp': [3.0, 5.0],  # market-implied prob: 0.333, 0.20
    })
    analysis = backtest._value_edge_backtest(selections)
    thresholds = {row['min_edge']: row for row in analysis['thresholds']}
    assert thresholds[0.0]['bets'] == 2
    # 0.60 - 0.333 = 0.267 edge on race 1; 0.21 - 0.20 = 0.01 edge on race 2.
    assert thresholds[0.05]['bets'] == 1
    assert thresholds[0.05]['roi_pct'] > thresholds[0.0]['roi_pct']


def test_value_edge_backtest_handles_empty_selections():
    analysis = backtest._value_edge_backtest(pandas.DataFrame(columns=['pred', 'won', 'sp']))
    assert analysis == {'thresholds': [], 'best_threshold': None}


# ── check_active_champion_staleness / _heal_stale_champion ──────────────────
# These cover Change 1: a champion missing walk-forward folds must be
# re-tested and repaired automatically in the same nightly run, instead of
# only logging a warning that waits for someone to run the backfill script
# by hand.

class FakeHealConnection:
    """Fakes just the SQL surface _heal_stale_champion / check_active_champion_staleness touch."""

    def __init__(self, champion_row, pkl_bytes, is_active=True, rejected_rows=None):
        self.champion_row = champion_row  # (id, selection_metrics_json)
        self.pkl_bytes = pkl_bytes
        self.is_active = is_active
        self.rejected_rows = rejected_rows or []
        self.updated_champion = None
        self.pipeline_alerts = []
        self.resolved_alert_keys = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "SELECT id, selection_metrics FROM backtest_best_model" in sql:
            return FetchResult(self.champion_row)
        if "SELECT pkl_data, is_active FROM backtest_best_model" in sql:
            return FetchResult((self.pkl_bytes, self.is_active))
        if "FROM backtest_best_model\n        WHERE is_active = FALSE" in sql:
            return FetchResultAll(self.rejected_rows)
        if "UPDATE backtest_best_model" in sql and "SET selection_metrics" in sql:
            self.updated_champion = params
            return FetchResult(None)
        if "SELECT id FROM ml_pipeline_alerts" in sql:
            return FetchResult(None)
        if "INSERT INTO ml_pipeline_alerts" in sql:
            self.pipeline_alerts.append(params)
            return FetchResult(None)
        if "UPDATE ml_pipeline_alerts SET message" in sql:
            self.pipeline_alerts.append(params)
            return FetchResult(None)
        if "UPDATE ml_pipeline_alerts SET resolved_at" in sql:
            self.resolved_alert_keys.append(params.get("key"))
            return FetchResult(None)
        raise AssertionError(f"Unhandled SQL in fake heal connection: {sql}")

    def commit(self):
        self.committed = True


class FetchResultAll:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


def _pkl_bytes(obj):
    import io as _io
    buf = _io.BytesIO()
    pickle.dump(obj, buf)
    return buf.getvalue()


def _setup_heal_env(monkeypatch, conn, meeting_dates=None, walk_forward_result=None):
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(
        backtest.joblib, "load",
        lambda f: pickle.load(f) if hasattr(f, "read") else pickle.load(open(f, "rb")),
    )
    monkeypatch.setattr(backtest, "load_historical_data", lambda: (None, None))

    X = pandas.DataFrame({"horse_age": [1.0, 2.0, 3.0, 4.0], "horse_weight": [1.0, 1.0, 2.0, 2.0]})
    y_won = pandas.Series([1, 0, 1, 0])
    sp_values = [3.0, 4.0, 5.0, 6.0]
    race_ids = [1, 2, 3, 4]
    horse_ids = [10, 11, 12, 13]
    dates = meeting_dates or ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    monkeypatch.setattr(
        backtest, "build_training_set",
        lambda df, srd: (X, None, y_won, sp_values, race_ids, horse_ids, dates),
    )

    wf = walk_forward_result or {
        "n_splits": 2, "roi_std": 0.5,
        "folds": [{"roi": 6.0, "strike_rate": 22.0, "bets": 50}, {"roi": 7.0, "strike_rate": 23.0, "bets": 50}],
    }
    monkeypatch.setattr(backtest, "_walk_forward_metrics_for_model", lambda model, X_, yw, sp, rids: wf)
    return wf


def test_check_active_champion_staleness_self_heals_without_rollback(monkeypatch):
    champion_metrics = {"selection_score": 10.0, "roi": 4.0, "strike_rate": 18.0}
    conn = FakeHealConnection(
        champion_row=(101, json.dumps(champion_metrics)),
        pkl_bytes=_pkl_bytes(DummySavedModel()),
        is_active=True,
        rejected_rows=[],  # nothing to roll back to
    )
    _setup_heal_env(monkeypatch, conn)

    backtest.check_active_champion_staleness(run_id=42)

    assert conn.updated_champion is not None
    assert conn.updated_champion["id"] == 101
    updated_metrics = json.loads(conn.updated_champion["metrics"])
    assert backtest._walk_forward_fold_count(updated_metrics) == 2
    # No rejected challenger beat it, so the champion stays active and the
    # durable alert is resolved rather than left open for a human to act on.
    assert "champion_missing_walk_forward_validation" in conn.resolved_alert_keys
    assert conn.pipeline_alerts == []


def test_check_active_champion_staleness_self_heals_and_rolls_back(monkeypatch):
    champion_metrics = {"selection_score": 10.0, "roi": 4.0, "strike_rate": 18.0}
    rejected_metrics = {
        "roi": 50.0, "strike_rate": 40.0,
        "walk_forward": {"folds": [{"roi": 50.0, "bets": 50}, {"roi": 55.0, "bets": 50}], "roi_std": 0.1},
    }
    rejected_row = (555, "random_forest", "RF Challenger", 5.0, json.dumps(rejected_metrics), _pkl_bytes(DummySavedModel()))
    conn = FakeHealConnection(
        champion_row=(101, json.dumps(champion_metrics)),
        pkl_bytes=_pkl_bytes(DummySavedModel()),
        is_active=True,
        rejected_rows=[rejected_row],
    )
    _setup_heal_env(monkeypatch, conn)

    rollback_calls = []
    monkeypatch.setattr(
        backtest, "rollback_to_champion",
        lambda model_id, reason='': rollback_calls.append((model_id, reason)),
    )

    backtest.check_active_champion_staleness(run_id=42)

    assert conn.updated_champion is not None  # champion's real score still gets persisted
    assert len(rollback_calls) == 1
    assert rollback_calls[0][0] == 555
    assert "Self-heal rollback" in rollback_calls[0][1]
    assert "champion_missing_walk_forward_validation" in conn.resolved_alert_keys


def test_check_active_champion_staleness_records_blocking_alert_when_heal_impossible(monkeypatch):
    # No 'roi' component stored — same situation as a champion promoted
    # before selection_metrics carried raw components. Nothing can be safely
    # recomputed, so this must stay a visible, honest blocking alert rather
    # than pretending to have healed it.
    champion_metrics = {"selection_score": 10.0}
    conn = FakeHealConnection(
        champion_row=(101, json.dumps(champion_metrics)),
        pkl_bytes=_pkl_bytes(DummySavedModel()),
        is_active=True,
    )
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))

    backtest.check_active_champion_staleness(run_id=42)

    assert conn.updated_champion is None
    assert len(conn.pipeline_alerts) == 1
    alert = conn.pipeline_alerts[0]
    assert alert["key"] == "champion_missing_walk_forward_validation"
    assert alert["severity"] == "blocking"
    assert "Automatic self-heal could not complete" in alert["message"]


def test_check_active_champion_staleness_skips_healthy_champion(monkeypatch):
    healthy_metrics = {
        "selection_score": 10.0,
        "walk_forward": {"folds": [{"roi": 4.0, "bets": 50}, {"roi": 5.0, "bets": 50}], "roi_std": 0.3},
    }
    conn = FakeHealConnection(champion_row=(101, json.dumps(healthy_metrics)), pkl_bytes=None)
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("_heal_stale_champion should not run for a non-stale champion")
    monkeypatch.setattr(backtest, "_heal_stale_champion", _fail_if_called)

    backtest.check_active_champion_staleness(run_id=42)

    assert "champion_missing_walk_forward_validation" in conn.resolved_alert_keys
    assert conn.pipeline_alerts == []
