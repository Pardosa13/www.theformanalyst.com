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
    sys.modules["sklearn.preprocessing"] = preprocessing
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
    feature_names_in_ = ["speed", "class"]


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
        '{"selection_score": %.3f}' % champion_score,
    ]


def metrics(selection_score=11.0, roi=5.0, strike_rate=20.0, bets=150):
    return {
        "selection_score": selection_score,
        "roi": roi,
        "strike_rate": strike_rate,
        "number_of_bets": bets,
        "profit_units": 8.0,
        "drawdown": 3.0,
        "longest_losing_streak": 4,
        "bankroll_growth": 1.1,
        "volatility": 0.7,
    }


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


def test_marginal_challenger_within_score_edge_does_not_promote(monkeypatch, tmp_path):
    """A challenger that only barely beats the champion (less than
    PROMOTION_SELECTION_SCORE_EDGE) must NOT be promoted — otherwise the
    champion could be swapped on noise from a single validation window rather
    than a real, repeatable improvement."""
    conn = FakeConnection(champion=champion_row(10.0))

    save_model_with_fake_db(monkeypatch, tmp_path, conn, metrics(selection_score=10.5))

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


def _base_recompute_metrics(**overrides):
    base = {
        "roi": 10.0,
        "strike_rate": 20.0,
        "kelly_staking": {"growth_rate_per_bet": 0.01, "max_drawdown_pct": 5.0, "ruined": False},
        "walk_forward": {
            "folds": [
                {"roi": 8.0, "strike_rate": 19.0, "bets": 50, "kelly_growth_rate": 0.008},
                {"roi": 9.0, "strike_rate": 21.0, "bets": 50, "kelly_growth_rate": 0.009},
            ],
            "roi_std": 0.5,
            "kelly_growth_std": 0.0005,
        },
    }
    base.update(overrides)
    return base


def test_selection_score_rewards_positive_kelly_growth(monkeypatch):
    """Item 10 (run 141): realistic Kelly-staking growth should positively
    contribute to the Champion Score, not just be a reported side metric."""
    with_kelly = backtest._selection_score_from_metrics(_base_recompute_metrics(), force_recompute=True)
    without_kelly = backtest._selection_score_from_metrics(
        _base_recompute_metrics(kelly_staking={"growth_rate_per_bet": 0.0, "max_drawdown_pct": 0.0, "ruined": False}),
        force_recompute=True,
    )
    assert with_kelly > without_kelly


def test_selection_score_heavily_penalises_kelly_ruin(monkeypatch):
    """A model whose capped fractional-Kelly staking would have blown the
    bankroll on the holdout must score noticeably worse, even if its flat-
    stake ROI looks fine — the whole point of item 10 is that flat-stake ROI
    alone can hide an unplayable staking profile."""
    ruined_score = backtest._selection_score_from_metrics(
        _base_recompute_metrics(kelly_staking={"growth_rate_per_bet": 0.01, "max_drawdown_pct": 5.0, "ruined": True}),
        force_recompute=True,
    )
    healthy_score = backtest._selection_score_from_metrics(_base_recompute_metrics(), force_recompute=True)
    assert healthy_score - ruined_score >= 10.0


def test_selection_score_penalises_unstable_kelly_growth_across_folds():
    """Two models with the same mean walk-forward Kelly growth but different
    fold-to-fold variance should not score the same — the less stable one
    (higher kelly_growth_std) must score lower, mirroring the existing
    roi_std stability penalty."""
    stable = backtest._selection_score_from_metrics(_base_recompute_metrics(), force_recompute=True)
    unstable_metrics = _base_recompute_metrics()
    unstable_metrics["walk_forward"]["kelly_growth_std"] = 5.0
    unstable = backtest._selection_score_from_metrics(unstable_metrics, force_recompute=True)
    assert stable > unstable


def test_selection_score_backward_compatible_with_pre_kelly_records():
    """Old rows saved before Kelly staking existed have no 'kelly_staking' key
    at all — recomputing their score must not crash and must be unaffected
    (0.0 contribution) rather than treating missing data as a penalty or bonus."""
    legacy_metrics = {
        "roi": 10.0,
        "strike_rate": 20.0,
        "walk_forward": {"folds": [{"roi": 8.0, "strike_rate": 19.0, "bets": 50}], "roi_std": 0.5},
    }
    score = backtest._selection_score_from_metrics(legacy_metrics, force_recompute=True)
    assert isinstance(score, float)
