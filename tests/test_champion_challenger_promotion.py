import os
import pickle
import sys
import types
from datetime import datetime, timedelta

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

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
    sys.modules["sklearn.model_selection"] = model_selection
    preprocessing = types.ModuleType("sklearn.preprocessing")
    preprocessing.LabelEncoder = type("LabelEncoder", (), {})
    sys.modules["sklearn.preprocessing"] = preprocessing
    metrics = types.ModuleType("sklearn.metrics")
    metrics.mean_squared_error = lambda *args, **kwargs: 0.0
    metrics.log_loss = lambda *args, **kwargs: 0.0
    metrics.brier_score_loss = lambda *args, **kwargs: 0.0
    sys.modules["sklearn.metrics"] = metrics

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

    save_model_with_fake_db(monkeypatch, tmp_path, conn, metrics(selection_score=11.0))

    assert conn.champion_promoted_at > datetime.utcnow() - timedelta(days=7)
    assert conn.deactivated_champions is True
    assert conn.activated_challenger["id"] == conn.challenger_id
    assert "Promoted: challenger Champion Score 11.000 beat Champion Score 10.000" in conn.activated_challenger["reason"]
    assert conn.rejected_challenger is None
    assert conn.committed is True


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
