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


class DummyScoringModel:
    """Saved artifact that can actually be re-scored, for heal paths that run a
    full out-of-sample re-validation rather than only a walk-forward re-test."""

    feature_names_in_ = ["horse_age", "horse_weight"]

    def predict_proba(self, X):
        import numpy as np
        p = np.full(len(X), 0.4)
        return np.column_stack([1 - p, p])


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
    # save_best_model_to_db always recomputes the CHAMPION's score from raw
    # components (force_recompute=True) while taking the challenger's stored
    # selection_score at face value. So the champion's stored selection_score
    # above is ignored, and these tests only mean what their names say if the
    # RECOMPUTED champion score actually equals `champion_score`.
    #
    # Under the v5 A/E formula the a_e_ratio term is what closes that gap
    # (a missing a_e_ratio contributes 0.0, which is why every fixture here
    # silently recomputed to -8.5 and inverted the intended comparisons).
    # Solve for the a_e_ratio that lands the recomputed score on the value
    # each test asked for, rather than hardcoding a number that breaks again
    # the next time an unrelated term in the formula is retuned.
    base_score = backtest._selection_score_from_metrics(
        {k: v for k, v in champion_metrics.items() if k != "selection_score"},
        force_recompute=True,
    )
    champion_metrics["a_e_ratio"] = 1.0 + ((champion_score - base_score) / 10.0)
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


def test_negative_roi_challenger_promotes_when_it_wins_on_champion_score(monkeypatch, tmp_path):
    """The positive-ROI promotion requirement is gone by design: promotion picks
    the best-scoring ELIGIBLE candidate by Champion Score, positive ROI or not.
    A challenger that clears every remaining gate (sample size, strike rate,
    feature contract, walk-forward folds, score edge) must be promoted even
    though its holdout ROI is negative."""
    conn = FakeConnection(champion=champion_row(10.0))

    save_model_with_fake_db(
        monkeypatch,
        tmp_path,
        conn,
        metrics(selection_score=12.0, roi=-1.0, strike_rate=20.0, bets=150),
    )

    assert conn.activated_challenger is not None
    assert conn.activated_challenger["id"] == conn.challenger_id
    assert conn.deactivated_champions is True
    assert conn.promotion_history["new_champion_id"] == conn.challenger_id
    assert "Champion Score 12.000 beat Champion Score 10.000" in conn.activated_challenger["reason"]


def test_validation_failures_still_block_higher_scoring_challenger(monkeypatch, tmp_path):
    """The remaining gates are untouched by the ROI-rule removal: a
    below-minimum validation sample still blocks a challenger that would
    otherwise win on Champion Score."""
    conn = FakeConnection(champion=champion_row(10.0))

    save_model_with_fake_db(
        monkeypatch,
        tmp_path,
        conn,
        metrics(selection_score=12.0, roi=-1.0, strike_rate=20.0,
                bets=backtest.MIN_VALIDATION_BETS - 1),
    )

    assert conn.activated_challenger is None
    assert conn.deactivated_champions is False
    assert conn.promotion_history is None
    assert conn.rejected_challenger["reason"] == "Rejected: validation sample too small"


def test_below_gate_strike_rate_still_blocks_a_negative_roi_challenger(monkeypatch, tmp_path):
    """Removing the ROI rule must not open the strike-rate gate as a side
    effect."""
    conn = FakeConnection(champion=champion_row(10.0))

    save_model_with_fake_db(
        monkeypatch,
        tmp_path,
        conn,
        metrics(selection_score=12.0, roi=-1.0,
                strike_rate=backtest.MIN_PROMOTION_STRIKE_RATE_PCT - 1.0, bets=150),
    )

    assert conn.activated_challenger is None
    assert conn.promotion_history is None
    assert conn.rejected_challenger["reason"] == (
        "Rejected: challenger validation strike rate is below promotion gate"
    )


def test_all_negative_walk_forward_folds_still_veto_promotion(monkeypatch, tmp_path):
    """The every-fold-negative veto is a separate, still-active rule: it is not
    the positive-ROI requirement and survives its removal."""
    conn = FakeConnection(champion=champion_row(10.0))
    losing = metrics(selection_score=12.0, roi=-1.0, strike_rate=20.0, bets=150)
    losing["walk_forward"] = {
        "folds": [{"roi": -2.0, "strike_rate": 18.0, "bets": 50},
                  {"roi": -4.0, "strike_rate": 17.0, "bets": 50}],
        "roi_std": 0.5,
    }

    save_model_with_fake_db(monkeypatch, tmp_path, conn, losing)

    assert conn.activated_challenger is None
    assert conn.promotion_history is None
    assert "every walk-forward fold was negative" in conn.rejected_challenger["reason"]


def test_promotion_rule_text_no_longer_advertises_a_positive_roi_requirement():
    rule = backtest._promotion_rule_text()

    assert "positive validation ROI" not in rule
    assert "negative ROI is not by itself disqualifying" in rule
    assert f"at least {backtest.MIN_VALIDATION_BETS} validation bets" in rule
    assert f"strike rate >= {backtest.MIN_PROMOTION_STRIKE_RATE_PCT:.1f}%" in rule
    assert f"at least {backtest.MIN_WALK_FORWARD_FOLDS} walk-forward folds" in rule


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
    """A champion with no walk_forward.folds — the same situation as Champion
    74, promoted before walk-forward evaluation existed. This must open a
    durable ml_pipeline_alerts row (not just a log line) per the
    walk_forward_fold_count invariant.

    The fold-less state is built explicitly here: champion_row() itself now
    carries MIN_WALK_FORWARD_FOLDS folds, so relying on the fixture to supply
    the defect (as this test used to) silently stopped exercising it. Only the
    folds are emptied — every other raw component and the current
    scoring_formula_version stay intact, so this asserts the walk-forward
    invariant rather than tripping the separate comparability guard first."""
    champion = champion_row(10.0)
    fold_less_metrics = json.loads(champion[10])
    fold_less_metrics["walk_forward"] = {"folds": [], "roi_std": 0.0}
    champion[10] = json.dumps(fold_less_metrics)
    conn = FakeConnection(champion=champion)

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
    champion = champion_row(10.0)
    # Override only walk_forward. Replacing the whole blob (as this test used
    # to) dropped every raw metric component and the scoring_formula_version,
    # so _assert_champion_comparable raised before the staleness path this
    # test is actually about could ever run.
    champion_metrics_blob = json.loads(champion[10])
    champion_metrics_blob["walk_forward"] = walk_forward
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


def test_snapshot_coverage_gap_note_flags_large_gap():
    challenger_metrics = {'jockey_snapshot_coverage_pct': 60.0, 'trainer_snapshot_coverage_pct': 55.0}
    champion_metrics = {'jockey_snapshot_coverage_pct': 20.0, 'trainer_snapshot_coverage_pct': 50.0}
    comparable, note = backtest._snapshot_coverage_gap_note(challenger_metrics, champion_metrics)
    assert comparable is False
    # Jockey gap (40pp) exceeds the tolerance; trainer gap (5pp) doesn't.
    assert 'jockey coverage champion=20.0% challenger=60.0%' in note
    assert 'trainer coverage' not in note


def test_snapshot_coverage_gap_note_passes_similar_coverage():
    challenger_metrics = {'jockey_snapshot_coverage_pct': 42.0, 'trainer_snapshot_coverage_pct': 39.0}
    champion_metrics = {'jockey_snapshot_coverage_pct': 38.0, 'trainer_snapshot_coverage_pct': 41.0}
    comparable, note = backtest._snapshot_coverage_gap_note(challenger_metrics, champion_metrics)
    assert comparable is True
    assert note == ""


def test_snapshot_coverage_gap_note_treats_missing_coverage_as_comparable():
    # A champion row saved before this metric existed has no coverage to
    # compare against — this must not manufacture a false alarm.
    comparable, note = backtest._snapshot_coverage_gap_note({'jockey_snapshot_coverage_pct': 60.0}, {})
    assert comparable is True
    assert note == ""


def test_promotion_reason_notes_large_snapshot_coverage_gap(monkeypatch, tmp_path):
    """Visibility only, not a gate: a challenger trained with much higher
    jockey/trainer snapshot coverage than the stored champion still promotes
    when it otherwise qualifies, but the promotion reason must record the
    gap so it isn't silently treated as an apples-to-apples comparison."""
    champion = champion_row(10.0)
    champion_metrics = json.loads(champion[10])
    champion_metrics['jockey_snapshot_coverage_pct'] = 10.0
    champion_metrics['trainer_snapshot_coverage_pct'] = 12.0
    champion[10] = json.dumps(champion_metrics)
    conn = FakeConnection(champion=champion)

    challenger_metrics = metrics(selection_score=13.0)
    challenger_metrics['jockey_snapshot_coverage_pct'] = 55.0
    challenger_metrics['trainer_snapshot_coverage_pct'] = 50.0
    save_model_with_fake_db(monkeypatch, tmp_path, conn, challenger_metrics)

    assert conn.activated_challenger["id"] == conn.challenger_id
    reason = conn.activated_challenger["reason"]
    assert "Promoted: challenger Champion Score 13.000 beat Champion Score 10.000" in reason
    assert "snapshot coverage differs by more than" in reason
    assert "jockey coverage champion=10.0% challenger=55.0%" in reason


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

    def __init__(self, champion_row, pkl_bytes, is_active=True, rejected_rows=None, row_metrics=None):
        self.champion_row = champion_row  # (id, selection_metrics_json)
        self.pkl_bytes = pkl_bytes
        self.is_active = is_active
        self.rejected_rows = rejected_rows or []
        self.row_metrics = row_metrics or {}
        self.updated_champion = None
        self.deactivated_champion = None
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
        if "SELECT pkl_data FROM backtest_best_model" in sql:
            # check_active_champion_staleness re-opens the champion artifact to
            # verify it still carries a usable feature list.
            return FetchResult((self.pkl_bytes,) if self.pkl_bytes else None)
        if "SELECT selection_metrics FROM backtest_best_model" in sql:
            return FetchResult((json.dumps(self.row_metrics.get(params.get("id"), {})),))
        if "UPDATE backtest_best_model" in sql and "SET is_active = FALSE" in sql:
            self.deactivated_champion = params
            return FetchResult(None)
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


def test_champion_carrying_pre_joint_kelly_metrics_is_fully_re_validated(monkeypatch):
    """A champion promoted under the old one-bet-per-race Kelly simulation has
    bankroll numbers describing a staking plan that no longer exists. Scoring
    it from those against challengers measured on the joint allocation would
    compare two different plans, so the heal must rebuild its raw metrics —
    not just bolt walk-forward folds onto the stale ones."""
    champion_metrics = {
        "selection_score": 10.0, "roi": 4.0, "strike_rate": 18.0,
        # Old format: no avg_horses_backed_per_race, because the old simulator
        # only ever backed the top pick.
        "kelly_staking": {"bankroll_growth": 3.0, "final_bankroll": 4.0,
                          "max_drawdown_pct": 5.0, "ruined": False},
    }
    conn = FakeHealConnection(
        champion_row=(101, json.dumps(champion_metrics)),
        pkl_bytes=_pkl_bytes(DummyScoringModel()),
        is_active=True,
        rejected_rows=[],
    )
    _setup_heal_env(monkeypatch, conn)

    backtest.check_active_champion_staleness(run_id=42)

    updated_metrics = json.loads(conn.updated_champion["metrics"])
    healed_kelly = updated_metrics["kelly_staking"]
    assert "avg_horses_backed_per_race" in healed_kelly
    assert healed_kelly["bankroll_growth"] != 3.0


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
    # "Healthy" has to satisfy every staleness predicate, not just the fold
    # count: current scoring_formula_version, all required raw components, and
    # an artifact with an intact feature list. Reuse the champion fixture so
    # this test can't drift out of sync with the invariant list again.
    healthy_metrics = json.loads(champion_row(10.0)[10])
    healthy_metrics["walk_forward"] = {
        "folds": [{"roi": 4.0, "bets": 50}, {"roi": 5.0, "bets": 50}], "roi_std": 0.3,
    }
    # A real artifact with an intact feature list: the nightly check now also
    # opens the champion's own pkl, so pkl_bytes=None would itself read as stale.
    conn = FakeHealConnection(
        champion_row=(101, json.dumps(healthy_metrics)),
        pkl_bytes=_pkl_bytes(DummySavedModel()),
    )
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(
        backtest.joblib, "load",
        lambda f: pickle.load(f) if hasattr(f, "read") else pickle.load(open(f, "rb")),
    )

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("_heal_stale_champion should not run for a non-stale champion")
    monkeypatch.setattr(backtest, "_heal_stale_champion", _fail_if_called)

    backtest.check_active_champion_staleness(run_id=42)

    assert "champion_missing_walk_forward_validation" in conn.resolved_alert_keys
    assert conn.pipeline_alerts == []


# ─────────────────────────────────────────────
# HARD INVARIANT: a model with no persisted feature list can never be champion
#
# Regression cover for Champion 79 (CatBoost, trained 2026-07-18), which was
# promoted through the self-heal rollback path with feature_names_in_ = None
# and then sat active for days. ml_predict raises
#   RuntimeError: ML feature contract failed: model artifact has no persisted
#   feature list
# before predict() for such an artifact, so it scores ZERO live races — yet the
# self-heal logic only ever asked "does something else score higher?", never
# "is the current champion even usable?", and re-stamped it as freshly
# validated every night.
# ─────────────────────────────────────────────
class FeaturelessSavedModel:
    """An artifact exactly like Champion 79: no persisted feature list."""
    feature_names_in_ = None


def test_featureless_challenger_cannot_promote_through_track_e(monkeypatch, tmp_path):
    """Track E path. An empty feature list yields an empty not_live_computable
    list, so before the explicit gate every downstream check read as 'passed'
    for an artifact that cannot score a race at all."""
    conn = FakeConnection(champion=champion_row(10.0))
    pkl_file = tmp_path / "featureless.pkl"
    with open(pkl_file, "wb") as model_file:
        pickle.dump(FeaturelessSavedModel(), model_file)
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(backtest.joblib, "load", lambda filename: pickle.load(open(filename, "rb")))

    # Scores far above the champion: this must be rejected on usability alone.
    backtest.save_best_model_to_db(
        str(pkl_file), combined_score=99.0, run_id=321,
        model_type="catboost", model_name="CatBoost Challenger",
        validation_metrics=metrics(selection_score=99.0),
    )

    assert conn.activated_challenger is None
    assert conn.deactivated_champions is False
    assert "no persisted feature list" in conn.rejected_challenger["reason"]


def test_featureless_champion_is_replaced_even_by_lower_scoring_challenger(monkeypatch):
    """Self-heal path, replacement available. The replacement scores WORSE than
    the broken champion — it must still win, because a usable model beats an
    unusable one regardless of Champion Score. Under the old score-gated logic
    this is exactly the case that left Champion 79 active."""
    champion_metrics = {"selection_score": 10.0, "roi": 4.0, "strike_rate": 18.0}
    weak_rejected_metrics = {
        "roi": 0.5, "strike_rate": 12.0,
        "walk_forward": {"folds": [{"roi": 0.4, "bets": 50}, {"roi": 0.6, "bets": 50}], "roi_std": 0.1},
    }
    weak_score = backtest._selection_score_from_metrics(weak_rejected_metrics, force_recompute=True)
    assert weak_score < 10.0, "fixture must score below the champion for this test to mean anything"

    rejected_row = (
        555, "random_forest", "Weak RF Challenger", weak_score,
        json.dumps(weak_rejected_metrics), _pkl_bytes(DummySavedModel()),
    )
    conn = FakeHealConnection(
        champion_row=(79, json.dumps(champion_metrics)),
        pkl_bytes=_pkl_bytes(FeaturelessSavedModel()),
        is_active=True,
        rejected_rows=[rejected_row],
        row_metrics={555: weak_rejected_metrics},
    )
    _setup_heal_env(monkeypatch, conn)

    rollback_calls = []
    monkeypatch.setattr(
        backtest, "rollback_to_champion",
        lambda model_id, reason='': rollback_calls.append((model_id, reason)),
    )

    backtest.check_active_champion_staleness(run_id=172)

    assert len(rollback_calls) == 1
    assert rollback_calls[0][0] == 555
    assert "no persisted feature list" in rollback_calls[0][1]
    # The broken champion must never be re-stamped as freshly validated.
    assert conn.deactivated_champion is None  # rollback_to_champion handles deactivation


def test_featureless_champion_is_deactivated_when_no_usable_replacement(monkeypatch):
    """Self-heal path, nothing usable to fall back to. The champion must be
    deactivated and flagged permanently_incompatible rather than left active,
    leaving no active champion so the next validated challenger promotes on
    its own merits with no comparison bar."""
    champion_metrics = {"selection_score": 10.0, "roi": 4.0, "strike_rate": 18.0}
    conn = FakeHealConnection(
        champion_row=(79, json.dumps(champion_metrics)),
        pkl_bytes=_pkl_bytes(FeaturelessSavedModel()),
        is_active=True,
        rejected_rows=[],  # nothing to fall back to
        row_metrics={79: champion_metrics},
    )
    _setup_heal_env(monkeypatch, conn)

    def _fail_if_called(model_id, reason=''):
        raise AssertionError("must not activate any model when none is usable")
    monkeypatch.setattr(backtest, "rollback_to_champion", _fail_if_called)

    backtest.check_active_champion_staleness(run_id=172)

    assert conn.deactivated_champion is not None, "broken champion must not stay active"
    assert conn.deactivated_champion["id"] == 79
    flagged = json.loads(conn.deactivated_champion["metrics"])
    assert flagged["permanently_incompatible"] is True
    assert flagged["unusable_missing_feature_list"] is True
    # And it must be reported honestly, not logged as a successful self-heal.
    assert any(
        a.get("key") == "champion_missing_walk_forward_validation" and a.get("severity") == "blocking"
        for a in conn.pipeline_alerts
    )


def test_featureless_model_cannot_be_activated_by_direct_rollback(monkeypatch):
    """Manual/direct rollback path. can_become_champion and
    _assert_champion_comparable both read selection_metrics only and never open
    the artifact, so without an explicit artifact check this path could still
    activate an unusable model."""
    eligible_metrics = {
        "roi": 5.0, "strike_rate": 20.0, "log_loss": 0.6, "brier_score": 0.2,
        "calibration": {"expected_calibration_error": 0.01},
        "stability": {"roi_last_100": 5.0, "roi_last_250": 5.0},
        "scoring_formula_version": backtest.SCORING_FORMULA_VERSION,
        "walk_forward": {"folds": [{"roi": 5.0, "bets": 50}, {"roi": 6.0, "bets": 50}], "roi_std": 0.2},
    }

    class RollbackConn(FakeHealConnection):
        def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            if "SELECT id, retained_until, selection_metrics" in sql:
                return FetchResult((79, None, json.dumps(eligible_metrics)))
            if "SELECT pkl_data FROM backtest_best_model" in sql:
                return FetchResult((_pkl_bytes(FeaturelessSavedModel()),))
            if "SET is_active = TRUE" in sql:
                raise AssertionError("a featureless model must never be activated")
            return super().execute(statement, params)

    conn = RollbackConn(champion_row=(79, "{}"), pkl_bytes=None)
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(
        backtest.joblib, "load",
        lambda f: pickle.load(f) if hasattr(f, "read") else pickle.load(open(f, "rb")),
    )

    try:
        backtest.rollback_to_champion(79, reason="manual rollback attempt")
    except ValueError as exc:
        assert "no persisted feature list" in str(exc)
    else:
        raise AssertionError("rollback_to_champion must refuse a featureless artifact")


# ─────────────────────────────────────────────
# ensure_champion_exists_after_run
#
# The end-of-run safety net: none of the strict promotion gates in
# save_best_model_to_db ("no comparable champion existed", "walk-forward all
# negative", positive ROI, etc.) may be the reason a night ends with zero
# active champions. If check_active_champion_staleness evicted an unusable
# incumbent with nothing to fall back to, and every fresh challenger this run
# then failed the strict bar, this function must still find and promote the
# best Champion Score among valid, feature-complete candidates on record —
# under an explicitly logged/alerted fallback rule, never silently.
# ─────────────────────────────────────────────

def _dummy_live_contract():
    """The live feature contract, as the fallback tests' dummy artifacts see it.

    ensure_champion_exists_after_run now requires a fallback candidate's stored
    feature list to BE the current live contract, not merely a subset of it
    (that is what let a 146-feature model look eligible against a 207-feature
    live contract). The dummy artifacts here carry two features, so the live
    contract has to be those same two for a valid candidate to stay valid —
    every "must be excluded" fixture still fails for its own reason: no
    feature list at all, or a feature live scoring cannot generate.
    """
    return ["horse_age", "horse_weight"]


class FakeEnsureConnection:
    """Fakes the SQL surface ensure_champion_exists_after_run touches."""

    def __init__(self, active_row=None, candidate_rows=None):
        self.active_row = active_row  # (id, selection_metrics_json) or None
        self.candidate_rows = candidate_rows or []
        self.stamped_updates = []
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
        if "SELECT id, selection_metrics FROM backtest_best_model" in sql and "WHERE is_active = TRUE" in sql:
            return FetchResult(self.active_row)
        if "SELECT id, model_type, model_name, selection_metrics, pkl_data" in sql:
            return FetchResultAll(self.candidate_rows)
        if "SET selection_metrics = :metrics, combined_score" in sql:
            self.stamped_updates.append(params)
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
        raise AssertionError(f"Unhandled SQL in fake ensure-champion connection: {sql}")

    def commit(self):
        self.committed = True


def test_ensure_champion_exists_after_run_noop_when_champion_healthy(monkeypatch):
    conn = FakeEnsureConnection(active_row=(101, json.dumps({"roi": 4.0})))
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))

    def _fail_if_called(model_id, reason=''):
        raise AssertionError("must not touch rollback when a usable champion is already active")
    monkeypatch.setattr(backtest, "rollback_to_champion", _fail_if_called)

    backtest.ensure_champion_exists_after_run(run_id=99)

    assert conn.pipeline_alerts == []
    assert conn.stamped_updates == []


def test_ensure_champion_exists_after_run_promotes_best_valid_feature_complete_candidate(monkeypatch):
    """No usable active champion (e.g. evicted earlier in the same run for
    missing feature list, with nothing to fall back to at the time). Several
    rejected candidates are on record: one with no persisted feature list, one
    trained on a feature live scoring can't generate, one with too few
    walk-forward folds — all must be skipped regardless of score — leaving two
    genuinely valid candidates, of which the higher Champion Score must win,
    tagged as a fallback promotion rather than a normal one."""
    featureless_row = (
        201, "random_forest", "RF Featureless",
        json.dumps(metrics(roi=99.0)), _pkl_bytes(FeaturelessSavedModel()),
    )
    not_live_row = (
        202, "xgboost", "XGB NotLive",
        json.dumps(metrics(roi=98.0)), _pkl_bytes(NotLiveScorableSavedModel()),
    )
    no_folds_row = (
        203, "xgboost", "XGB NoFolds",
        json.dumps(metrics(roi=50.0, walk_forward_folds=None)), _pkl_bytes(DummySavedModel()),
    )
    weak_valid_row = (
        204, "random_forest", "RF Weak", json.dumps(metrics(roi=1.0)), _pkl_bytes(DummySavedModel()),
    )
    strong_valid_row = (
        205, "xgboost", "XGB Strong", json.dumps(metrics(roi=20.0)), _pkl_bytes(DummySavedModel()),
    )
    conn = FakeEnsureConnection(
        active_row=None,
        candidate_rows=[featureless_row, not_live_row, no_folds_row, weak_valid_row, strong_valid_row],
    )
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(backtest.joblib, "load", lambda f: pickle.load(f))
    monkeypatch.setattr(backtest, "_live_scoring_feature_names", _dummy_live_contract)

    rollback_calls = []
    monkeypatch.setattr(
        backtest, "rollback_to_champion",
        lambda model_id, reason='': rollback_calls.append((model_id, reason)),
    )

    backtest.ensure_champion_exists_after_run(run_id=77)

    assert len(rollback_calls) == 1
    assert rollback_calls[0][0] == 205, "must pick the higher-scoring of the two genuinely valid candidates"
    assert "FALLBACK PROMOTION" in rollback_calls[0][1]
    assert "no usable active champion existed" in rollback_calls[0][1]
    assert conn.stamped_updates and conn.stamped_updates[-1]["id"] == 205
    assert any(a.get("key") == "fallback_champion_promotion" and a.get("severity") == "blocking" for a in conn.pipeline_alerts)


def test_ensure_champion_exists_after_run_refuses_a_candidate_that_lost_on_every_fold(monkeypatch):
    """The fallback used to waive the 'every walk-forward fold negative' veto
    on the reasoning that some champion beats no champion. That is backwards:
    a candidate whose own out-of-sample folds all lose money is not a weaker
    champion, it is a known-losing one, and promoting it unopposed puts real
    stakes on bets nothing has validated. No champion shows no picks, which
    costs nothing. Ending the night with zero champions and a blocking alert
    is the correct outcome here."""
    negative_metrics = metrics(roi=-3.0)
    negative_metrics["walk_forward"] = {
        "folds": [{"roi": -1.0, "bets": 50}, {"roi": -2.0, "bets": 50}], "roi_std": 0.3,
    }
    only_row = (301, "random_forest", "RF AllNegative", json.dumps(negative_metrics), _pkl_bytes(DummySavedModel()))
    conn = FakeEnsureConnection(active_row=None, candidate_rows=[only_row])
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(backtest.joblib, "load", lambda f: pickle.load(f))
    monkeypatch.setattr(backtest, "_live_scoring_feature_names", _dummy_live_contract)

    def _fail_if_called(model_id, reason=''):
        raise AssertionError("a candidate that lost on every walk-forward fold must never be promoted")
    monkeypatch.setattr(backtest, "rollback_to_champion", _fail_if_called)

    backtest.ensure_champion_exists_after_run(run_id=88)

    alert = next(a for a in conn.pipeline_alerts if a.get("key") == "no_active_champion")
    assert alert["severity"] == "blocking"
    assert "every walk-forward fold" in alert["message"]
    assert conn.stamped_updates == []


def test_ensure_champion_exists_after_run_records_blocking_alert_when_nothing_valid(monkeypatch):
    """Nothing usable exists anywhere on record. There is genuinely nothing
    that can be promoted — this must be a loud, durable blocking alert, not a
    silent no-op that looks identical to 'everything is fine'."""
    featureless_row = (
        201, "random_forest", "RF Featureless",
        json.dumps(metrics(roi=99.0)), _pkl_bytes(FeaturelessSavedModel()),
    )
    conn = FakeEnsureConnection(active_row=None, candidate_rows=[featureless_row])
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(backtest.joblib, "load", lambda f: pickle.load(f))
    monkeypatch.setattr(backtest, "_live_scoring_feature_names", _dummy_live_contract)

    def _fail_if_called(model_id, reason=''):
        raise AssertionError("must not promote anything when nothing valid exists")
    monkeypatch.setattr(backtest, "rollback_to_champion", _fail_if_called)

    backtest.ensure_champion_exists_after_run(run_id=55)

    assert any(
        a.get("key") == "no_active_champion" and a.get("severity") == "blocking"
        for a in conn.pipeline_alerts
    )
    assert conn.stamped_updates == []


def _corrupt_metrics():
    """A candidate whose stored metrics recompute to an absurd Champion Score.

    Deliberately corrupted through a term the Kelly clamp does NOT cover: the
    clamp fixes the one field that caused the model 81 incident, while this
    bound is the backstop for every other way a stored metrics blob can go
    bad. A test that corrupted bankroll_growth here would pass on the clamp
    alone and prove nothing about the fallback path.
    """
    data = metrics(roi=406156577.65)
    data["selection_score"] = None
    data["stability"] = {}
    return data


def test_fallback_promotion_rejects_a_candidate_with_an_implausible_score(monkeypatch):
    """The model 81 incident, reproduced at the promotion layer.

    A candidate whose recomputed Champion Score is astronomically high must be
    EXCLUDED from the fallback pool, not ranked at the top of it. The fallback
    path has no incumbent to beat and no edge threshold to clear, so "highest
    score wins" is the whole rule — which makes a garbage number the single
    most dangerous thing that can be on record. A modest, honest candidate
    must be promoted over it."""
    corrupt_row = (
        81, "xgboost", "XGB Corrupted", json.dumps(_corrupt_metrics()), _pkl_bytes(DummySavedModel()),
    )
    honest_row = (
        77, "ensemble", "Ensemble Honest", json.dumps(metrics(roi=6.0)), _pkl_bytes(DummySavedModel()),
    )
    conn = FakeEnsureConnection(active_row=None, candidate_rows=[corrupt_row, honest_row])
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(backtest.joblib, "load", lambda f: pickle.load(f))
    monkeypatch.setattr(backtest, "_live_scoring_feature_names", _dummy_live_contract)

    rollback_calls = []
    monkeypatch.setattr(
        backtest, "rollback_to_champion",
        lambda model_id, reason='': rollback_calls.append((model_id, reason)),
    )

    backtest.ensure_champion_exists_after_run(run_id=81)

    assert len(rollback_calls) == 1
    assert rollback_calls[0][0] == 77, "the corrupted candidate must never win the fallback"
    assert conn.stamped_updates[-1]["id"] == 77


def test_fallback_promotion_alerts_rather_than_promoting_the_only_corrupt_candidate(monkeypatch):
    """If excluding implausible scores empties the pool, ending the run with
    zero active champions and a loud blocking alert is the correct outcome —
    strictly better than silently promoting garbage into live scoring."""
    corrupt_row = (
        81, "xgboost", "XGB Corrupted", json.dumps(_corrupt_metrics()), _pkl_bytes(DummySavedModel()),
    )
    conn = FakeEnsureConnection(active_row=None, candidate_rows=[corrupt_row])
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(backtest.joblib, "load", lambda f: pickle.load(f))
    monkeypatch.setattr(backtest, "_live_scoring_feature_names", _dummy_live_contract)

    def _fail_if_called(model_id, reason=''):
        raise AssertionError("a candidate with an implausible score must never be promoted")
    monkeypatch.setattr(backtest, "rollback_to_champion", _fail_if_called)

    backtest.ensure_champion_exists_after_run(run_id=81)

    assert any(
        a.get("key") == "no_active_champion" and a.get("severity") == "blocking"
        for a in conn.pipeline_alerts
    )
    assert conn.stamped_updates == []


def test_fallback_promotion_keeps_the_whole_observed_score_range_eligible(monkeypatch):
    """The bound exists to reject corruption, not to re-litigate model quality.
    Every real score on record sits in the low-to-mid 40s, so a candidate
    anywhere in that range — and well beyond it — must stay eligible."""
    for roi in (-40.0, 0.0, 44.0, 120.0):
        row = (
            90, "xgboost", "XGB Ordinary", json.dumps(metrics(roi=roi, selection_score=None)),
            _pkl_bytes(DummySavedModel()),
        )
        conn = FakeEnsureConnection(active_row=None, candidate_rows=[row])
        monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
        monkeypatch.setattr(backtest.joblib, "load", lambda f: pickle.load(f))
        monkeypatch.setattr(backtest, "_live_scoring_feature_names", _dummy_live_contract)

        rollback_calls = []
        monkeypatch.setattr(
            backtest, "rollback_to_champion",
            lambda model_id, reason='': rollback_calls.append(model_id),
        )

        backtest.ensure_champion_exists_after_run(run_id=91)

        assert rollback_calls == [90], f"an ordinary candidate (roi={roi}) must stay eligible"


# ── Champion Score: a contradicted holdout ROI earns nothing ────────────────
# Model 81's headline holdout ROI was +50.6% while every one of its own
# walk-forward folds was negative (-2.7%, -14.3%, -6.8%). The old blend still
# handed it 0.3 * 50.6 = +15.2 points of Champion Score for that number, which
# is most of what made it look promotable. A holdout ROI that every honest
# out-of-sample fold contradicts must buy no credit at all.

def _model_81_shaped_metrics(holdout_roi):
    data = metrics(roi=holdout_roi, selection_score=None)
    data["walk_forward"] = {
        "folds": [
            {"roi": -2.7, "bets": 60},
            {"roi": -14.3, "bets": 60},
            {"roi": -6.8, "bets": 60},
        ],
        "roi_std": 0.0,
    }
    return data


def test_holdout_roi_earns_no_credit_when_every_walk_forward_fold_is_negative():
    """The model 81 case. A wildly positive holdout must score exactly the same
    as a zero holdout once every fold disagrees with it — otherwise the number
    we know is unreliable is still doing the promoting."""
    great_holdout = backtest._selection_score_from_metrics(
        _model_81_shaped_metrics(50.6), force_recompute=True)
    zero_holdout = backtest._selection_score_from_metrics(
        _model_81_shaped_metrics(0.0), force_recompute=True)

    assert great_holdout == zero_holdout, "a contradicted holdout ROI must add nothing to the score"
    # And the score it does get is the walk-forward evidence: mean of the folds.
    mean_fold_roi = (-2.7 + -14.3 + -6.8) / 3
    only_folds = backtest._selection_score_from_metrics(
        {k: v for k, v in _model_81_shaped_metrics(0.0).items() if k != "roi"}, force_recompute=True)
    assert abs(great_holdout - only_folds) < 1e-9
    assert great_holdout < mean_fold_roi + 1.0, "score must sit at the walk-forward evidence, not above it"


def test_holdout_roi_still_counts_against_a_model_when_it_is_worse_than_the_folds():
    """Dropping the holdout term must not become a way to launder a bad
    holdout into a better score: it loses its weight only where it would
    RAISE the score. A holdout worse than the folds agrees with them."""
    bad_holdout = backtest._selection_score_from_metrics(
        _model_81_shaped_metrics(-60.0), force_recompute=True)
    zero_holdout = backtest._selection_score_from_metrics(
        _model_81_shaped_metrics(0.0), force_recompute=True)

    assert bad_holdout < zero_holdout, "a holdout worse than the folds must still drag the score down"


def test_normal_blend_survives_when_the_folds_are_not_all_negative():
    """The 0.3/0.7 blend is unchanged for every model whose walk-forward
    evidence has not uniformly contradicted its holdout."""
    def _mixed(holdout_roi):
        data = metrics(roi=holdout_roi, selection_score=None)
        data["walk_forward"] = {
            "folds": [{"roi": -4.0, "bets": 50}, {"roi": 6.0, "bets": 50}], "roi_std": 0.0,
        }
        # Neutralise the stability penalty (which is itself measured against
        # the holdout ROI) so this asserts on the blend alone.
        data["stability"] = {}
        return data

    mixed, zero_holdout = _mixed(20.0), _mixed(0.0)

    scored = backtest._selection_score_from_metrics(mixed, force_recompute=True)
    scored_zero = backtest._selection_score_from_metrics(zero_holdout, force_recompute=True)

    assert abs((scored - scored_zero) - (0.3 * 20.0)) < 1e-9


# ── Fallback promotion: no model from a previous era is eligible ────────────

class ShortContractModel:
    """An artifact whose feature list is a strict SUBSET of the live contract —
    the 146-vs-207 shape, which the old subset check waved through."""
    feature_names_in_ = ["horse_age"]  # the fallback tests' live contract has two


def _old_era_row(row_id, mutate):
    data = metrics(roi=6.0, selection_score=None)
    mutate(data)
    return (row_id, "xgboost", "XGB OldEra", json.dumps(data), _pkl_bytes(DummySavedModel()))


def _run_fallback(monkeypatch, conn, rollback_calls):
    monkeypatch.setattr(backtest, "engine", FakeEngine(conn))
    monkeypatch.setattr(backtest.joblib, "load", lambda f: pickle.load(f))
    monkeypatch.setattr(backtest, "_live_scoring_feature_names", _dummy_live_contract)
    monkeypatch.setattr(
        backtest, "rollback_to_champion",
        lambda model_id, reason='': rollback_calls.append((model_id, reason)),
    )
    backtest.ensure_champion_exists_after_run(run_id=811)


def test_fallback_promotion_refuses_a_model_scored_by_an_older_formula(monkeypatch):
    """Every model stored before this release has scoring_formula_version NULL.
    Their combined_scores were produced by rules that no longer exist, so
    ranking them against today's models is meaningless — the fallback must
    exclude them outright and end the run with no champion instead."""
    stale_row = _old_era_row(77, lambda d: d.pop("scoring_formula_version"))
    conn = FakeEnsureConnection(active_row=None, candidate_rows=[stale_row])
    rollback_calls = []

    _run_fallback(monkeypatch, conn, rollback_calls)

    assert rollback_calls == [], "a model from an older scoring formula must never be fallback-promoted"
    alert = next(a for a in conn.pipeline_alerts if a.get("key") == "no_active_champion")
    assert alert["severity"] == "blocking"
    assert "scoring_formula_version" in alert["message"]
    assert conn.stamped_updates == []


def test_fallback_promotion_refuses_a_model_whose_feature_contract_predates_live_scoring(monkeypatch):
    """The 146-vs-207 mismatch, at the promotion layer. A model trained on
    fewer features than live scoring now generates would have ~60 columns
    median-filled on every race — it is not the model its stored score
    describes, no matter how that score compares."""
    short_row = (
        78, "xgboost", "XGB ShortContract",
        json.dumps(metrics(roi=6.0, selection_score=None)), _pkl_bytes(ShortContractModel()),
    )
    conn = FakeEnsureConnection(active_row=None, candidate_rows=[short_row])
    rollback_calls = []

    _run_fallback(monkeypatch, conn, rollback_calls)

    assert rollback_calls == [], "a model whose feature contract predates the live one must not be promoted"
    alert = next(a for a in conn.pipeline_alerts if a.get("key") == "no_active_champion")
    assert alert["severity"] == "blocking"
    assert "feature contract" in alert["message"]


def test_fallback_promotion_prefers_no_champion_over_the_best_of_a_stale_field(monkeypatch):
    """The decision this release encodes: given only old-era models — even a
    high-scoring one — the correct outcome is zero active champions, not the
    best of them. A current-era candidate in the same field still wins."""
    stale_high = _old_era_row(77, lambda d: (d.pop("scoring_formula_version"), d.update(roi=40.0)))
    current_low = (
        90, "random_forest", "RF Current",
        json.dumps(metrics(roi=1.0, selection_score=None)), _pkl_bytes(DummySavedModel()),
    )
    conn = FakeEnsureConnection(active_row=None, candidate_rows=[stale_high, current_low])
    rollback_calls = []

    _run_fallback(monkeypatch, conn, rollback_calls)

    assert len(rollback_calls) == 1
    assert rollback_calls[0][0] == 90, "the current-era candidate must win over a higher-scoring stale one"
