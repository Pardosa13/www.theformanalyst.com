import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

pd = pytest.importorskip("pandas")

import backtest


def _selections(rows):
    return pd.DataFrame(rows, columns=["pred", "sp", "won"])


def test_kelly_staking_grows_bankroll_on_consistent_positive_edge():
    # Model consistently overestimates a horse that wins at decimal odds 3.0
    # (true win rate ~0.5 vs market-implied ~0.33) — a real edge should grow
    # the simulated bankroll over many bets.
    rows = [{"pred": 0.5, "sp": 3.0, "won": 1 if i % 2 == 0 else 0} for i in range(20)]
    result = backtest._simulate_kelly_staking(_selections(rows))
    assert result["bankroll_growth"] > 0
    assert result["final_bankroll"] > 1.0
    assert result["ruined"] is False


def test_kelly_staking_shrinks_bankroll_with_no_edge():
    # Model's predicted probability matches the market-implied probability
    # exactly (no edge) — Kelly fraction should be ~0, so the simulated
    # bankroll should stay close to flat rather than compounding either way.
    rows = [{"pred": 1 / 3, "sp": 3.0, "won": 1 if i % 3 == 0 else 0} for i in range(30)]
    result = backtest._simulate_kelly_staking(_selections(rows))
    assert abs(result["bankroll_growth"]) < 0.5


def test_kelly_staking_never_stakes_more_than_the_configured_cap():
    # An extreme overconfident prediction (pred=0.99 on long odds) would
    # imply a huge raw Kelly fraction — the max_stake_pct cap must still hold,
    # so one bad prediction can't wipe out the simulated bankroll in one shot.
    rows = [{"pred": 0.99, "sp": 20.0, "won": 0}]
    result = backtest._simulate_kelly_staking(_selections(rows), max_stake_pct=0.05)
    # Losing a single bet capped at 5% of bankroll leaves at least 95%.
    assert result["final_bankroll"] >= 0.95 - 1e-9


def test_kelly_staking_handles_empty_selections():
    result = backtest._simulate_kelly_staking(_selections([]))
    assert result == {"bankroll_growth": 0.0, "final_bankroll": 1.0, "max_drawdown_pct": 0.0, "ruined": False}


def test_evaluate_model_on_validation_includes_kelly_staking_metric():
    class DummyProbaModel:
        feature_names_in_ = ["speed"]

        def predict_proba(self, X):
            import numpy as np
            return np.column_stack([1 - X["speed"].values, X["speed"].values])

    model = DummyProbaModel()
    X_val = pd.DataFrame({"speed": [0.8, 0.3, 0.6]})
    y_won_val = [1, 0, 1]
    race_ids_val = [1, 2, 3]
    sp_val = [2.5, 4.0, 3.0]

    metrics = backtest.evaluate_model_on_validation(model, X_val, y_won_val, race_ids_val, sp_val)

    assert "kelly_staking" in metrics
    assert set(metrics["kelly_staking"].keys()) == {"bankroll_growth", "final_bankroll", "max_drawdown_pct", "ruined"}
