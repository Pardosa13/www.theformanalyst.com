import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

import backtest


def test_market_implied_prob_removes_overround():
    # Three runners at 2.0, 4.0, 4.0 imply raw probabilities 0.5, 0.25, 0.25
    # (summing to exactly 1.0 here, i.e. no overround) - a good sanity check
    # that the normalisation is a no-op when there's nothing to remove.
    sp = pd.Series([2.0, 4.0, 4.0])
    race_id = pd.Series([1, 1, 1])
    implied = backtest._market_implied_prob(sp, race_id)
    assert implied.tolist() == pytest.approx([0.5, 0.25, 0.25])


def test_market_implied_prob_normalises_away_bookmaker_overround():
    # Raw 1/sp values here sum to 1.2 (a typical ~20% overround field) -
    # normalising must rescale each runner's share so the field sums to 1.0,
    # not just report the raw (misleadingly high) implied probabilities.
    sp = pd.Series([2.0, 3.0, 6.0])  # raw: 0.5, 0.333, 0.1667 -> sums to 1.0
    # Use a shorter-priced field so raw sums above 1.0.
    sp = pd.Series([1.5, 3.0, 6.0])  # raw: 0.667, 0.333, 0.1667 -> sums to 1.1667
    race_id = pd.Series([9, 9, 9])
    implied = backtest._market_implied_prob(sp, race_id)
    assert implied.sum() == pytest.approx(1.0)


def test_market_implied_prob_is_scoped_per_race():
    sp = pd.Series([2.0, 2.0])
    race_id = pd.Series([1, 2])  # different races, each a single "runner"
    implied = backtest._market_implied_prob(sp, race_id)
    # Each is the only "runner" in its own race, so its own raw implied
    # probability (0.5) is entirely re-normalised to 1.0 within that race.
    assert implied.tolist() == pytest.approx([1.0, 1.0])


class _LinearProbaModel:
    """Predicts win probability directly from a 'prob' feature column."""
    feature_names_in_ = ["prob"]

    def predict_proba(self, X):
        p = np.clip(X["prob"].values, 1e-6, 1 - 1e-6)
        return np.column_stack([1 - p, p])


def test_evaluate_model_on_validation_skips_race_with_no_value_edge(monkeypatch):
    monkeypatch.setattr(backtest, "MIN_VALUE_EDGE", 0.0)
    model = _LinearProbaModel()
    # Race 1: model's top pick (0.5) has sp=2.0 -> market implied prob for a
    # 2-runner field at [2.0, 2.0] is [0.5, 0.5] -> zero edge, must be skipped.
    # Race 2: model's top pick (0.6) has sp=3.0 -> market implied for
    # [3.0, 6.0] is normalised to [0.667, 0.333] -> edge = 0.6 - 0.667 < 0,
    # still no value, also skipped.
    # Race 3: model's top pick (0.6) has sp=3.0, other runner sp=10.0 ->
    # market implied [10/13, 3/13] = [0.769, 0.231] -> this runner's implied
    # is 0.231, model says 0.6 -> a real positive edge, must be bet.
    X_val = pd.DataFrame({"prob": [0.5, 0.5, 0.4, 0.6, 0.6, 0.2]})
    y_won_val = [1, 0, 0, 1, 1, 0]
    race_ids_val = [1, 1, 2, 2, 3, 3]
    sp_val = [2.0, 2.0, 3.0, 6.0, 3.0, 10.0]

    metrics = backtest.evaluate_model_on_validation(model, X_val, y_won_val, race_ids_val, sp_val)

    assert metrics["races_considered"] == 3
    assert metrics["number_of_bets"] == 1
    assert metrics["races_skipped_no_value"] == 2
    assert metrics["average_value_edge"] > 0


def test_evaluate_model_on_validation_bets_every_race_when_edge_always_clears(monkeypatch):
    monkeypatch.setattr(backtest, "MIN_VALUE_EDGE", 0.0)
    model = _LinearProbaModel()
    # Model's top pick massively overestimates vs a long-priced market
    # favourite-of-two in both races -> always positive value.
    X_val = pd.DataFrame({"prob": [0.6, 0.2, 0.6, 0.2]})
    y_won_val = [1, 0, 0, 1]
    race_ids_val = [1, 1, 2, 2]
    sp_val = [10.0, 1.5, 10.0, 1.5]

    metrics = backtest.evaluate_model_on_validation(model, X_val, y_won_val, race_ids_val, sp_val)

    assert metrics["races_considered"] == 2
    assert metrics["number_of_bets"] == 2
    assert metrics["races_skipped_no_value"] == 0


def test_add_race_relative_features_computes_market_implied_prob_variants():
    feature_rows = [
        {"market_implied_prob": 0.6},
        {"market_implied_prob": 0.3},
        {"market_implied_prob": 0.1},
    ]
    race_ids = [1, 1, 1]
    result = backtest.add_race_relative_features(feature_rows, race_ids)
    assert result[0]["market_implied_prob_race_rank"] == 1.0
    assert result[0]["market_implied_prob_vs_race_best"] == 0.0
    assert result[2]["market_implied_prob_race_rank"] == 3.0
