"""Shin favourite-longshot correction and the model/market blend."""
import math

import pytest

from market_probability import (
    PLAUSIBLE_Z_RANGE,
    blend_probabilities,
    blend_probabilities_by_race,
    fair_probabilities,
    fair_probabilities_by_race,
    shin_z,
    summarise_z,
)


# A realistic eight-runner Australian thoroughbred book: sums to ~1.108, with
# the margin loaded onto the long prices.
TYPICAL_BOOK = [2.5, 4.0, 6.0, 9.0, 12.0, 21.0, 34.0, 51.0]


def naive(sp_list):
    raw = [1.0 / sp for sp in sp_list]
    total = sum(raw)
    return [value / total for value in raw]


class TestShinZ:
    def test_solves_a_plausible_insider_proportion(self):
        raw = [1.0 / sp for sp in TYPICAL_BOOK]
        z = shin_z(raw)
        assert z is not None
        assert PLAUSIBLE_Z_RANGE[0] <= z <= PLAUSIBLE_Z_RANGE[1]

    def test_bigger_overround_implies_bigger_z(self):
        tight = shin_z([1.0 / sp for sp in [2.2, 3.5, 5.5, 9.0, 15.0]])
        loose = shin_z([1.0 / sp for sp in [1.9, 3.0, 4.5, 7.0, 11.0]])
        assert tight is not None and loose is not None
        assert loose > tight

    def test_underround_book_has_nothing_to_solve(self):
        # Two runners at 5.0 sum to 0.4: no margin to attribute to insiders.
        assert shin_z([0.2, 0.2]) is None

    def test_single_runner_has_nothing_to_solve(self):
        assert shin_z([0.5]) is None


class TestFairProbabilities:
    def test_probabilities_sum_to_one(self):
        probabilities = fair_probabilities(TYPICAL_BOOK)
        assert math.isclose(sum(probabilities), 1.0, rel_tol=1e-9)

    def test_corrects_the_bias_in_the_right_direction(self):
        """Naive normalisation removes the average margin but keeps the bias.

        Shin must move probability FROM the longshots TO the favourites
        relative to naive normalisation — that is the whole correction.
        """
        shin = fair_probabilities(TYPICAL_BOOK)
        flat = naive(TYPICAL_BOOK)
        assert shin[0] > flat[0], "shortest price must gain"
        assert shin[1] > flat[1]
        assert shin[-1] < flat[-1], "longest price must lose"
        assert shin[-2] < flat[-2]
        # And the correction must be monotone in price. The ABSOLUTE shift is
        # not the thing to check — it necessarily shrinks again at the very
        # long end, where the probability itself is heading to zero. The
        # proportional shift is what has to fall monotonically as the price
        # lengthens: every runner is scaled down relative to naive
        # normalisation by more than the one shorter than it.
        ratios = [s / f for s, f in zip(shin, flat)]
        assert ratios == sorted(ratios, reverse=True)
        assert ratios[0] > 1.0 > ratios[-1]

    def test_every_fair_probability_is_below_its_raw_reciprocal(self):
        # The overround has to come out of somewhere: with a book above 1.0 no
        # runner's true chance can be as high as 1/SP.
        for sp, probability in zip(TYPICAL_BOOK, fair_probabilities(TYPICAL_BOOK)):
            assert probability < (1.0 / sp)

    def test_expected_wins_falls_below_the_raw_market_estimate(self):
        """The A/E denominator must shrink, which is the point of the change.

        Raw 1/SP sums to the book (>1), so summing it over a model's selections
        overstates expected wins and understates A/E for every model at once.
        """
        assert sum(fair_probabilities(TYPICAL_BOOK)) < sum(1.0 / sp for sp in TYPICAL_BOOK)

    def test_single_runner_field(self):
        assert fair_probabilities([3.0]) == [1.0]

    def test_scratched_and_missing_prices_return_none(self):
        result = fair_probabilities([2.0, None, 4.0, "n/a", 8.0, float("nan")])
        assert result[1] is None and result[3] is None and result[5] is None
        priced = [p for p in result if p is not None]
        assert len(priced) == 3
        assert math.isclose(sum(priced), 1.0, rel_tol=1e-9)

    def test_prices_at_or_below_one_are_not_prices(self):
        result = fair_probabilities([1.0, 0.5, -3.0, 2.0, 3.0])
        assert result[:3] == [None, None, None]
        assert math.isclose(sum(result[3:]), 1.0, rel_tol=1e-9)

    def test_no_usable_prices_at_all(self):
        assert fair_probabilities([None, None]) == [None, None]
        assert fair_probabilities([]) == []
        assert fair_probabilities(None) == []

    def test_underround_book_falls_back_to_naive_normalisation(self):
        sps = [5.0, 5.0, 5.0]
        assert fair_probabilities(sps) == pytest.approx(naive(sps))

    def test_return_z_reports_the_solved_insider_proportion(self):
        probabilities, z = fair_probabilities(TYPICAL_BOOK, return_z=True)
        assert z is not None and 0.0 < z < 1.0
        assert math.isclose(sum(probabilities), 1.0, rel_tol=1e-9)

    def test_return_z_is_none_when_the_race_fell_back(self):
        _probabilities, z = fair_probabilities([5.0, 5.0], return_z=True)
        assert z is None

    def test_extreme_book_still_returns_usable_probabilities(self):
        # A three-short-priced-placegetters book of the kind book_quality.py
        # gates out: it must degrade, never raise.
        result = fair_probabilities([1.2, 1.3, 1.4])
        assert all(p is not None for p in result)
        assert math.isclose(sum(result), 1.0, rel_tol=1e-9)


class TestFairProbabilitiesByRace:
    def test_splits_and_stitches_by_race(self):
        # Race 'a' is a real book (sums to 1.033); race 'b' is an underround
        # (0.75), so only 'a' has a margin for Shin to attribute.
        sps = [2.0, 3.0, 5.0, 4.0, 4.0, 4.0]
        race_ids = ['a', 'a', 'a', 'b', 'b', 'b']
        probabilities, z_values = fair_probabilities_by_race(sps, race_ids)
        assert math.isclose(sum(probabilities[:3]), 1.0, rel_tol=1e-9)
        assert math.isclose(sum(probabilities[3:]), 1.0, rel_tol=1e-9)
        assert len(z_values) == 1

    def test_interleaved_races_are_grouped_not_assumed_contiguous(self):
        sps = [2.0, 4.0, 3.0, 4.0, 6.0, 4.0]
        race_ids = ['a', 'b', 'a', 'b', 'a', 'b']
        probabilities, _z = fair_probabilities_by_race(sps, race_ids)
        assert math.isclose(probabilities[0] + probabilities[2] + probabilities[4], 1.0, rel_tol=1e-9)
        assert math.isclose(probabilities[1] + probabilities[3] + probabilities[5], 1.0, rel_tol=1e-9)


class TestBlendProbabilities:
    MODEL = [0.5, 0.3, 0.2]
    MARKET = [0.3, 0.3, 0.4]

    def test_alpha_one_is_the_model_alone(self):
        assert blend_probabilities(self.MODEL, self.MARKET, 1.0) == pytest.approx(self.MODEL)

    def test_alpha_zero_is_the_market_alone(self):
        assert blend_probabilities(self.MODEL, self.MARKET, 0.0) == pytest.approx(self.MARKET)

    def test_blend_sums_to_one_and_moves_toward_the_market(self):
        blended = blend_probabilities(self.MODEL, self.MARKET, 0.5)
        assert math.isclose(sum(blended), 1.0, rel_tol=1e-9)
        # Renormalisation means a blended value need not sit numerically
        # between its two inputs, but every runner must move in the direction
        # the market disagrees with the model, and by less than the whole way.
        for value, model_p, market_p in zip(blended, self.MODEL, self.MARKET):
            if math.isclose(model_p, market_p):
                continue
            assert (value - model_p) * (market_p - model_p) > 0
            assert abs(value - model_p) < abs(market_p - model_p)

    def test_blend_is_geometric_not_arithmetic(self):
        blended = blend_probabilities(self.MODEL, self.MARKET, 0.5)
        geometric = [math.sqrt(m * k) for m, k in zip(self.MODEL, self.MARKET)]
        total = sum(geometric)
        assert blended == pytest.approx([g / total for g in geometric])

    def test_unpriced_runners_keep_their_model_probability_relative_to_each_other(self):
        blended = blend_probabilities([0.5, 0.3, 0.2], [0.3, None, 0.4], 0.5)
        assert math.isclose(sum(blended), 1.0, rel_tol=1e-9)
        assert all(value is not None for value in blended)

    def test_out_of_range_alpha_degrades_to_pure_model(self):
        assert blend_probabilities(self.MODEL, self.MARKET, None) == pytest.approx(self.MODEL)
        assert blend_probabilities(self.MODEL, self.MARKET, "junk") == pytest.approx(self.MODEL)
        assert blend_probabilities(self.MODEL, self.MARKET, 5.0) == pytest.approx(self.MODEL)
        assert blend_probabilities(self.MODEL, self.MARKET, -1.0) == pytest.approx(self.MARKET)

    def test_zero_model_probability_does_not_blow_up_the_race(self):
        blended = blend_probabilities([0.0, 0.6, 0.4], [0.2, 0.5, 0.3], 0.5)
        assert all(value is None or math.isfinite(value) for value in blended)
        assert math.isclose(sum(v for v in blended if v is not None), 1.0, rel_tol=1e-9)

    def test_large_field_does_not_underflow(self):
        model = [1.0 / 24] * 24
        market = [1.0 / 24] * 24
        blended = blend_probabilities(model, market, 0.5)
        assert math.isclose(sum(blended), 1.0, rel_tol=1e-9)

    def test_empty_input(self):
        assert blend_probabilities([], [], 0.5) == []

    def test_shorter_market_list_is_padded_not_misaligned(self):
        blended = blend_probabilities([0.5, 0.3, 0.2], [0.3], 0.5)
        assert len(blended) == 3
        assert math.isclose(sum(blended), 1.0, rel_tol=1e-9)


class TestBlendProbabilitiesByRace:
    def test_each_race_renormalises_independently(self):
        model = [0.5, 0.5, 0.25, 0.75]
        market = [0.3, 0.7, 0.5, 0.5]
        race_ids = ['a', 'a', 'b', 'b']
        blended = blend_probabilities_by_race(model, market, race_ids, 0.5)
        assert math.isclose(blended[0] + blended[1], 1.0, rel_tol=1e-9)
        assert math.isclose(blended[2] + blended[3], 1.0, rel_tol=1e-9)

    def test_alpha_one_leaves_a_normalised_race_untouched(self):
        model = [0.5, 0.3, 0.2]
        blended = blend_probabilities_by_race(model, [0.4, 0.4, 0.2], ['a'] * 3, 1.0)
        assert list(blended) == pytest.approx(model)

    def test_rows_without_a_market_price_survive(self):
        model = [0.6, 0.4]
        blended = blend_probabilities_by_race(model, [None, None], ['a', 'a'], 0.5)
        assert list(blended) == pytest.approx(model)


class TestSummariseZ:
    def test_reports_the_shape_of_a_healthy_run(self):
        summary = summarise_z([0.02, 0.03, 0.025])
        assert summary['races_solved'] == 3
        assert summary['mean_z_in_plausible_range'] is True

    def test_flags_a_run_pinned_near_zero(self, caplog):
        with caplog.at_level('WARNING'):
            summary = summarise_z([0.0, 0.0, 1e-9], label='test')
        assert summary['mean_z_in_plausible_range'] is False
        assert 'implausible' in caplog.text

    def test_no_solved_races_is_not_an_error(self):
        summary = summarise_z([])
        assert summary['races_solved'] == 0
        assert summary['mean_z'] is None
        summary = summarise_z(None)
        assert summary['races_solved'] == 0
