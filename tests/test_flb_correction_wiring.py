"""The favourite-longshot correction where it actually changes a number.

market_probability.py is unit-tested on its own in test_market_probability.py.
These tests are about the wiring: that the A/E ratio in the nightly validation
and the live Kelly staking path really do read a corrected market probability,
and that the scoring-formula version was bumped to say so.
"""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

import backtest
import ml_predict
from market_probability import fair_probabilities


class ConstantScoreModel:
    """Scores runners by a fixed per-row value, so the selection is known."""

    def __init__(self, scores):
        self.scores = list(scores)

    def predict_proba(self, X):
        scores = np.asarray(self.scores[: len(X)], dtype=float)
        return np.column_stack([1.0 - scores, scores])


def _validation_frame(races):
    """races: list of (race_id, [(sp, won, pred), ...]) -> the five arrays
    evaluate_model_on_validation takes."""
    race_ids, sps, wons, preds = [], [], [], []
    for race_id, runners in races:
        for sp, won, pred in runners:
            race_ids.append(race_id)
            sps.append(sp)
            wons.append(won)
            preds.append(pred)
    X = pd.DataFrame({'feature': np.arange(len(race_ids), dtype=float)})
    return X, pd.Series(wons), race_ids, np.array(sps, dtype=float), preds


# Three complete six-runner books with real overrounds (1.135, 1.064, 1.086).
# The model's top pick is the second favourite in every race, and wins one of
# the three.
RACES = [
    ('r1', [(2.2, 0, 0.10), (3.6, 1, 0.90), (5.5, 0, 0.05), (9.0, 0, 0.04), (14.0, 0, 0.03), (26.0, 0, 0.02)]),
    ('r2', [(2.8, 0, 0.10), (3.4, 0, 0.90), (5.0, 0, 0.05), (8.5, 0, 0.04), (16.0, 1, 0.03), (31.0, 0, 0.02)]),
    ('r3', [(1.9, 0, 0.10), (4.2, 0, 0.90), (6.5, 1, 0.05), (11.0, 0, 0.04), (19.0, 0, 0.03), (41.0, 0, 0.02)]),
]


def _metrics():
    X, y_won, race_ids, sp, preds = _validation_frame(RACES)
    return backtest.evaluate_model_on_validation(
        ConstantScoreModel(preds), X, y_won, race_ids, sp
    )


def test_scoring_formula_version_records_the_change():
    # A change to how selection_score is computed must invalidate every stored
    # model, which is what a new version string triggers.
    assert backtest.SCORING_FORMULA_VERSION == 'champion_score_v7_flb_corrected_ae'


def test_a_e_ratio_is_measured_against_corrected_probabilities():
    metrics = _metrics()
    assert metrics['a_e_market_probability_method'] == 'shin_flb_corrected'

    # Expected wins must equal the sum of the SELECTED runners' Shin
    # probabilities — not the sum of their raw 1/SP.
    expected = 0.0
    raw_expected = 0.0
    for _race_id, runners in RACES:
        sps = [sp for sp, _won, _pred in runners]
        selected = max(range(len(runners)), key=lambda i: runners[i][2])
        expected += fair_probabilities(sps)[selected]
        raw_expected += 1.0 / sps[selected]

    assert metrics['a_e_ratio'] == pytest.approx(metrics['winners'] / expected)
    assert metrics['a_e_ratio'] != pytest.approx(metrics['winners'] / raw_expected)


def test_correction_raises_a_e_because_the_overround_is_removed():
    """Every model's A/E must move UP under v7, because raw 1/SP sums above 1.

    This is the uniform shift that makes v6 and v7 Champion Scores
    incomparable, and the reason the version string had to change.
    """
    metrics = _metrics()
    raw_expected = sum(
        1.0 / max(runners, key=lambda r: r[2])[0] for _race_id, runners in RACES
    )
    assert metrics['a_e_ratio'] > (metrics['winners'] / raw_expected)


def test_a_e_survives_a_race_with_no_usable_prices():
    races = list(RACES) + [('r4', [(None, 0, 0.5), (None, 1, 0.4)])]
    X, y_won, race_ids, sp, preds = _validation_frame(races)
    metrics = backtest.evaluate_model_on_validation(
        ConstantScoreModel(preds), X, y_won, race_ids, sp
    )
    # The unpriced race contributes no expectation, exactly as a NaN 1/SP did,
    # so A/E is still computed off the three priced races.
    assert metrics['a_e_ratio'] is not None
    assert np.isfinite(metrics['a_e_ratio'])


def test_z_summary_reports_a_plausible_insider_proportion():
    _X, _y, race_ids, sp, _preds = _validation_frame(RACES)
    summary = backtest._log_flb_z_summary(sp, race_ids, label='test')
    assert summary['races_solved'] == 3
    assert summary['mean_z_in_plausible_range'] is True


class TestLiveKellyStaking:
    PREDICTIONS = [
        {'horse_id': 1, 'win_probability': 0.40, 'odds': 3.0},
        {'horse_id': 2, 'win_probability': 0.30, 'odds': 4.0},
        {'horse_id': 3, 'win_probability': 0.20, 'odds': 7.0},
        {'horse_id': 4, 'win_probability': 0.10, 'odds': 15.0},
    ]

    def test_a_champion_with_no_alpha_stakes_exactly_as_before(self):
        """Passing the model's probabilities through the blend at alpha=1.0
        would still renormalise them, and `predictions` is only the PRICED
        runners — so an unblended champion must bypass the blend entirely."""
        from model_classes import solve_joint_kelly

        stakes = ml_predict.compute_kelly_stakes_for_race(self.PREDICTIONS, market_alpha=1.0)
        unchanged = solve_joint_kelly(
            [(p['horse_id'], p['win_probability'], p['odds']) for p in self.PREDICTIONS],
            ml_predict.KELLY_FRACTION_MULTIPLIER,
            ml_predict.KELLY_MAX_TOTAL_STAKE_PCT,
        )
        assert stakes == unchanged

    def test_blending_toward_the_market_changes_the_stakes(self):
        unblended = ml_predict.compute_kelly_stakes_for_race(self.PREDICTIONS, market_alpha=1.0)
        blended = ml_predict.compute_kelly_stakes_for_race(self.PREDICTIONS, market_alpha=0.5)
        assert blended != unblended

    def test_unusable_prices_are_dropped_not_guessed(self):
        stakes = ml_predict.compute_kelly_stakes_for_race(
            [
                {'horse_id': 1, 'win_probability': 0.5, 'odds': 3.0},
                {'horse_id': 2, 'win_probability': 0.3, 'odds': None},
                {'horse_id': 3, 'win_probability': 0.2, 'odds': 0.5},
            ],
            market_alpha=0.5,
        )
        assert set(stakes) <= {1}

    def test_no_predictions_is_an_empty_plan_not_an_error(self):
        assert ml_predict.compute_kelly_stakes_for_race([], market_alpha=0.5) == {}
        assert ml_predict.compute_kelly_stakes_for_race(None) == {}


class TestModelMarketAlpha:
    def test_a_model_without_an_alpha_is_pure_model(self):
        assert ml_predict.model_market_alpha(object()) == 1.0

    def test_a_stored_alpha_is_used(self):
        class Model:
            pass

        model = Model()
        setattr(model, ml_predict.MODEL_MARKET_ALPHA_ATTR, 0.35)
        assert ml_predict.model_market_alpha(model) == pytest.approx(0.35)

    @pytest.mark.parametrize('bad', ['junk', float('nan'), 1.5, -0.5, float('inf')])
    def test_a_corrupted_alpha_degrades_to_pure_model(self, bad):
        class Model:
            pass

        model = Model()
        setattr(model, ml_predict.MODEL_MARKET_ALPHA_ATTR, bad)
        assert ml_predict.model_market_alpha(model) == 1.0
