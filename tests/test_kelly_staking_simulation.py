import os
import random

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


_BASE_SCORE_METRICS = {
    "roi": 5.0, "a_e_ratio": 1.0, "log_loss": 0.0, "brier_score": 0.0,
    "calibration": {}, "stability": {}, "walk_forward": {},
}


def _kelly_block(bankroll_growth, ruined=False, max_drawdown_pct=0.0, new_format=True):
    block = {
        "bankroll_growth": bankroll_growth,
        "max_drawdown_pct": max_drawdown_pct,
        "ruined": ruined,
    }
    if new_format:
        # The two keys only _simulate_joint_kelly_staking ever writes; their
        # absence is what marks a record as coming from the deleted single-bet
        # simulator.
        block["avg_horses_backed_per_race"] = 1.4
        block["races_with_zero_bets"] = 3
    return block


def _score(kelly_staking=None):
    metrics = {**_BASE_SCORE_METRICS, "selection_score": None}
    if kelly_staking is not None:
        metrics["kelly_staking"] = kelly_staking
    return backtest._selection_score_from_metrics(metrics)


def test_champion_score_rewards_kelly_growth_and_punishes_ruin():
    no_kelly = _score()
    grew = _score(_kelly_block(0.4))
    busted = _score(_kelly_block(0.4, ruined=True))
    assert grew > no_kelly
    assert busted < no_kelly


def test_champion_score_clamps_an_implausible_bankroll_growth():
    # Model 81 incident: a stored bankroll_growth of 406,156,577.65 was
    # weighted *5.0 straight into the score, producing a Champion Score of
    # ~2.03bn that won an unopposed fallback promotion. bankroll_growth is a
    # fractional return, so no validation-window simulation can legitimately
    # reach anything near this — the term must be clamped, not trusted.
    corrupted = _score(_kelly_block(406156577.65))
    at_clamp = _score(_kelly_block(5.0))
    assert corrupted == pytest.approx(at_clamp)
    assert corrupted < 100.0


def test_champion_score_clamps_an_implausible_bankroll_loss():
    assert _score(_kelly_block(-9999.0)) == pytest.approx(_score(_kelly_block(-1.0)))


@pytest.mark.parametrize("bad_growth", [float("inf"), float("nan"), "not-a-number", None])
def test_champion_score_ignores_an_unusable_bankroll_growth(bad_growth):
    # Non-finite or non-numeric values must degrade to "no Kelly evidence",
    # never propagate a nan/inf through the whole score.
    assert _score(_kelly_block(bad_growth)) == pytest.approx(_score())


def test_champion_score_ignores_kelly_records_predating_the_joint_solver():
    # A kelly_staking block with no avg_horses_backed_per_race came from the
    # deleted single-bet simulator. Its bankroll_growth measures a different
    # quantity under different assumptions and was never validated against the
    # v6 formula's expectations, so it earns nothing — not even a clamped
    # amount — and its `ruined` flag costs nothing either. This is what stops
    # the next legacy landmine from mattering at all.
    assert _score(_kelly_block(0.4, new_format=False)) == pytest.approx(_score())
    assert _score(_kelly_block(406156577.65, new_format=False)) == pytest.approx(_score())
    assert _score(_kelly_block(0.4, ruined=True, new_format=False)) == pytest.approx(_score())


def test_a_realistic_simulation_run_lands_well_inside_the_clamp():
    # The clamp must catch corruption without truncating honest results. A
    # model with a small genuine edge (30% estimated against a 27.8% market
    # price, hitting 32% of the time) over 300 races finishes around +55%,
    # nowhere near the +500% ceiling. Note the ceiling IS reachable by a
    # sustained extreme edge compounding over a long window, so it is a
    # deliberate cap on how much any one term may contribute, not a claim
    # that no honest simulation can ever reach it.
    random.seed(7)
    rows = []
    for race in range(300):
        field = [
            ("r%d" % race, 0.30 if runner == 0 else 0.14, 3.6 if runner == 0 else 7.0, 0)
            for runner in range(6)
        ]
        winner = 0 if random.random() < 0.32 else random.randrange(1, 6)
        field[winner] = field[winner][:3] + (1,)
        rows.extend(field)

    result = backtest._simulate_joint_kelly_staking(_eval_df(rows))

    assert 0.0 < result["bankroll_growth"] < 5.0
    unclamped_component = result["bankroll_growth"] * 5.0 - result["max_drawdown_pct"] * 0.02
    scored = _score(_kelly_block(
        result["bankroll_growth"], max_drawdown_pct=result["max_drawdown_pct"],
    ))
    assert scored - _score() == pytest.approx(unclamped_component)


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


# ─────────────────────────────────────────────
# Model 81 incident, step 3c: the live joint solver was checked for a bug of
# its own that could produce a runaway bankroll_growth. The closed form divides
# by o * (1.0 - R), so the two ways it can blow up are odds at or near 1.0 and
# a reserve R approaching 1.0. Both are already guarded — the odds > 1.0 filter
# and the reserve >= 1.0 break — and the KKT positivity check catches whatever
# slips between them. These tests pin that, so the guards cannot be removed as
# "dead code" later and re-open the same failure mode from the live side.
# ─────────────────────────────────────────────

@pytest.mark.parametrize("odds", [1.0000001, 1.0, 0.5, 0.0, -2.0])
def test_solver_never_divides_through_a_degenerate_price(odds):
    # A near-certain runner at a near-1.0 price is the sharpest form of the
    # division blow-up: the reserve 1/o sits just under 1.0, so 1.0 - R
    # underflows toward zero and the closed form returns an enormous negative
    # stake. It must produce no bet, not a huge one.
    stakes = solve_joint_kelly([("a", 0.999, odds)], kelly_fraction_multiplier=1.0)
    assert stakes == {}


def test_solver_refuses_a_backed_set_that_covers_the_whole_book():
    # sum(1/o) >= 1.0 means the backed set covers the book and 1.0 - R is zero
    # or negative — the closed form stops meaning anything there.
    assert solve_joint_kelly([(i, 0.09, 1.02) for i in range(12)]) == {}


def test_total_race_exposure_is_bounded_even_for_a_miscalibrated_model():
    # Probabilities summing well above 1.0 (a badly miscalibrated model) must
    # still not commit more than the per-race cap. This bound is what makes a
    # runaway bankroll structurally impossible in the joint simulator: no race
    # can risk more than max_total_stake_pct of the bankroll.
    stakes = solve_joint_kelly(
        [(1, 0.8, 3.0), (2, 0.7, 3.0), (3, 0.6, 3.0)],
        kelly_fraction_multiplier=1.0, max_total_stake_pct=0.20,
    )
    assert sum(stakes.values()) == pytest.approx(0.20)
    assert all(0.0 <= stake <= 0.20 for stake in stakes.values())


def test_simulation_stays_finite_on_degenerate_prices():
    # End to end: a field of degenerate prices must leave the bankroll
    # untouched and the reported growth finite, never nan/inf.
    rows = [("r1", 0.999, 1.0, 1), ("r1", 0.999, 0.0, 0), ("r2", 0.5, 1.0, 0)]
    result = backtest._simulate_joint_kelly_staking(_eval_df(rows))
    assert result["bankroll_growth"] == pytest.approx(0.0)
    assert result["final_bankroll"] == pytest.approx(1.0)
    assert result["races_with_zero_bets"] == 2
