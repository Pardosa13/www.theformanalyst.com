import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

pd = pytest.importorskip("pandas")

import backtest
import ml_predict
from model_classes import solve_joint_kelly


def _eval_df(rows):
    frame = pd.DataFrame(rows, columns=["race_id", "pred", "sp", "won"])
    frame["row_id"] = range(len(frame))
    return frame


def test_single_included_runner_matches_the_classic_kelly_formula():
    # With one runner in the backed set the multi-outcome solution must reduce
    # to x = (pO - 1) / (O - 1) — the standard single-bet Kelly stake — or the
    # closed form has been mis-transcribed.
    stakes = solve_joint_kelly(
        [("a", 0.5, 3.0)], kelly_fraction_multiplier=1.0, max_total_stake_pct=None
    )
    assert stakes["a"] == pytest.approx((0.5 * 3.0 - 1.0) / (3.0 - 1.0))


def test_runners_without_a_positive_expected_value_get_no_stake():
    # p * O <= 1 means the price is short of fair: no stake, and no entry in
    # the result at all.
    assert solve_joint_kelly([("a", 0.2, 3.0), ("b", 0.25, 4.0)]) == {}


def test_several_overpriced_runners_in_one_race_are_backed_together():
    stakes = solve_joint_kelly(
        [("fav", 0.5, 3.0), ("outsider", 0.25, 6.0)],
        kelly_fraction_multiplier=1.0,
        max_total_stake_pct=None,
    )
    # Both clear p*O > 1, so the allocation covers both. The favourite's stake
    # is LARGER here than it would be as a lone bet: backing the outsider too
    # cuts the chance of the race returning nothing, which makes the combined
    # position safer and lets each leg carry more. Sizing the two bets
    # independently would miss that entirely.
    assert set(stakes) == {"fav", "outsider"}
    assert stakes["fav"] == pytest.approx(1.0 / 3.0)
    assert stakes["outsider"] == pytest.approx(1.0 / 6.0)
    assert stakes["fav"] > solve_joint_kelly(
        [("fav", 0.5, 3.0)], kelly_fraction_multiplier=1.0, max_total_stake_pct=None
    )["fav"]


def test_total_stake_across_a_race_is_capped():
    stakes = solve_joint_kelly(
        [("fav", 0.5, 3.0), ("outsider", 0.25, 6.0)],
        kelly_fraction_multiplier=1.0,
        max_total_stake_pct=0.20,
    )
    assert sum(stakes.values()) == pytest.approx(0.20)
    # Capping must shrink the allocation proportionally, not re-rank it.
    assert stakes["fav"] / stakes["outsider"] == pytest.approx(2.0)


def test_joint_kelly_simulation_grows_bankroll_on_a_real_edge():
    # The model rates a 3.0-shot at 0.5 and it wins every second race — a real
    # edge, so the compounded bankroll should finish ahead.
    rows = [(race, 0.5, 3.0, 1 if race % 2 == 0 else 0) for race in range(20)]
    result = backtest._simulate_joint_kelly_staking(_eval_df(rows))
    assert result["bankroll_growth"] > 0
    assert result["final_bankroll"] > 1.0
    assert result["ruined"] is False
    assert result["avg_horses_backed_per_race"] == pytest.approx(1.0)
    assert result["races_with_zero_bets"] == 0


def test_joint_kelly_simulation_skips_races_with_nothing_overpriced():
    # Every runner priced at or shorter than the model's own assessment: no
    # value bet, so no stake and a flat bankroll.
    rows = [(1, 0.3, 3.0, 1), (1, 0.2, 4.0, 0), (2, 0.25, 4.0, 0), (2, 0.1, 5.0, 1)]
    result = backtest._simulate_joint_kelly_staking(_eval_df(rows))
    assert result["final_bankroll"] == pytest.approx(1.0)
    assert result["avg_horses_backed_per_race"] == 0.0
    assert result["races_with_zero_bets"] == 2


def test_joint_kelly_simulation_backs_a_varying_number_of_runners_per_race():
    # Race 1 has nothing overpriced, race 2 has one runner, race 3 has two.
    # A solver that always bet the top pick would report a flat 1.0 here.
    rows = [
        (1, 0.30, 3.0, 1), (1, 0.20, 4.0, 0),
        (2, 0.50, 3.0, 1), (2, 0.20, 4.0, 0),
        (3, 0.50, 3.0, 1), (3, 0.25, 6.0, 0),
    ]
    result = backtest._simulate_joint_kelly_staking(_eval_df(rows))
    assert result["avg_horses_backed_per_race"] == pytest.approx(1.0)
    assert result["races_with_zero_bets"] == 1


def test_joint_kelly_simulation_handles_an_empty_frame():
    result = backtest._simulate_joint_kelly_staking(_eval_df([]))
    assert result == {
        "bankroll_growth": 0.0,
        "final_bankroll": 1.0,
        "max_drawdown_pct": 0.0,
        "ruined": False,
        "avg_horses_backed_per_race": 0.0,
        "races_with_zero_bets": 0,
    }


def test_evaluate_model_on_validation_includes_joint_kelly_staking_metric():
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
    assert set(metrics["kelly_staking"].keys()) == {
        "bankroll_growth", "final_bankroll", "max_drawdown_pct", "ruined",
        "avg_horses_backed_per_race", "races_with_zero_bets",
    }


def test_champion_score_rewards_kelly_growth_and_punishes_ruin():
    base = {"roi": 5.0, "a_e_ratio": 1.0, "log_loss": 0.0, "brier_score": 0.0,
            "calibration": {}, "stability": {}, "walk_forward": {}}
    no_kelly = backtest._selection_score_from_metrics({**base, "selection_score": None})
    grew = backtest._selection_score_from_metrics({
        **base, "selection_score": None,
        "kelly_staking": {"bankroll_growth": 0.4, "max_drawdown_pct": 0.0, "ruined": False},
    })
    busted = backtest._selection_score_from_metrics({
        **base, "selection_score": None,
        "kelly_staking": {"bankroll_growth": 0.4, "max_drawdown_pct": 0.0, "ruined": True},
    })
    assert grew > no_kelly
    assert busted < no_kelly


def test_live_staking_allocates_identically_to_the_backtest_simulation():
    # ml_predict and backtest must stake the same way on the same inputs, or
    # the plan the nightly run validates is not the plan the site displays.
    probs_odds = [(101, 0.5, 3.0), (102, 0.25, 6.0), (103, 0.1, 5.0)]
    live = ml_predict.compute_kelly_stakes_for_race([
        {"horse_id": horse_id, "win_probability": prob, "odds": odds}
        for horse_id, prob, odds in probs_odds
    ])
    assert live == backtest._solve_joint_kelly(probs_odds)


def test_live_staking_ignores_runners_with_no_usable_market_price():
    stakes = ml_predict.compute_kelly_stakes_for_race([
        {"horse_id": 1, "win_probability": 0.5, "odds": 3.0},
        {"horse_id": 2, "win_probability": 0.5, "odds": None},
        {"horse_id": 3, "win_probability": 0.5, "odds": 1.0},
        {"horse_id": 4, "win_probability": None, "odds": 4.0},
    ])
    assert set(stakes) == {1}
