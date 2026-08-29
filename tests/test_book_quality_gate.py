"""The book-quality gate and the Champion Score formula-version guard."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from book_quality import (MIN_PRICE_COVERAGE, race_book_quality,
                          unusable_race_ids, usable_sp)
from scoring_versions import group_by_formula_version, scores_comparable


class TestUsableSp:
    @pytest.mark.parametrize('value', [None, 0, 1.0, 0.5, -3, 'not a price', ''])
    def test_rejects_unusable(self, value):
        assert usable_sp(value) is None

    @pytest.mark.parametrize('value,expected', [(4.5, 4.5), ('4.5', 4.5), (1.01, 1.01), (101, 101.0)])
    def test_accepts_real_prices(self, value, expected):
        assert usable_sp(value) == expected

    def test_rejects_nan_and_inf(self):
        assert usable_sp(float('nan')) is None
        assert usable_sp(float('inf')) is None


class TestRaceBookQuality:
    def test_complete_book_is_usable(self):
        q = race_book_quality([(3.0, 1), (4.0, 2), (5.0, 3), (8.0, 5), (12.0, 5)])
        assert q['usable'] is True
        assert q['coverage'] == 1.0
        assert q['reason'] is None

    def test_top_four_only_book_is_rejected(self):
        """The exact shape of the 1,489 broken races: only placegetters priced."""
        runners = [(3.0, 1), (4.0, 2), (6.0, 3), (9.0, 4)] + [(None, 5)] * 8
        q = race_book_quality(runners)
        assert q['usable'] is False
        assert q['priced'] == 4 and q['runners'] == 12
        assert q['coverage'] == pytest.approx(1 / 3)
        assert 'incomplete' in q['reason']

    def test_scratched_runners_do_not_count_against_coverage(self):
        q = race_book_quality([(3.0, 1), (4.0, 2), (5.0, 5), (None, 0), (None, 0), (None, 0)])
        assert q['usable'] is True
        assert q['runners'] == 3

    def test_race_with_no_runners_is_not_usable(self):
        assert race_book_quality([])['usable'] is False
        assert race_book_quality([(None, 0)])['usable'] is False

    def test_coverage_exactly_at_threshold_passes(self):
        # 17 of 20 priced == 0.85 exactly.
        runners = [(3.0, 5)] * 17 + [(None, 5)] * 3
        q = race_book_quality(runners)
        assert q['coverage'] == pytest.approx(MIN_PRICE_COVERAGE)
        assert q['usable'] is True

    def test_just_below_threshold_fails(self):
        runners = [(3.0, 5)] * 16 + [(None, 5)] * 4
        assert race_book_quality(runners)['usable'] is False

    def test_overround_reported_and_sub_one_book_flagged(self):
        q = race_book_quality([(4.0, 1), (4.0, 2), (4.0, 3), (4.0, 5)])
        assert q['overround'] == pytest.approx(1.0)
        assert q['suspicious_overround'] is True

    def test_healthy_book_not_flagged_suspicious(self):
        q = race_book_quality([(2.0, 1), (3.0, 2), (6.0, 3), (12.0, 5)])
        assert q['overround'] > 1.02
        assert q['suspicious_overround'] is False

    def test_overround_needs_two_prices(self):
        q = race_book_quality([(3.0, 1), (None, 5), (None, 5), (None, 5)])
        assert q['overround'] is None

    def test_a_complete_but_thin_book_is_still_usable(self):
        """A small field is not a broken field — coverage, not field size, decides."""
        q = race_book_quality([(1.9, 1), (2.1, 2), (9.0, 5)])
        assert q['usable'] is True


class TestUnusableRaceIds:
    def test_separates_good_and_bad_races(self):
        rows = (
            [(1, 3.0, 1), (1, 4.0, 2), (1, 7.0, 5)]              # complete
            + [(2, 3.0, 1), (2, 4.0, 2)] + [(2, None, 5)] * 9    # placegetters only
        )
        bad = unusable_race_ids(rows)
        assert set(bad) == {2}
        assert bad[2]['coverage'] < MIN_PRICE_COVERAGE

    def test_empty_input(self):
        assert unusable_race_ids([]) == {}


class TestChampionScoreComparability:
    """A v2 score and a v6 score are different units; the UI must not compare them."""

    @staticmethod
    def _guard():
        return scores_comparable

    def test_same_version_is_comparable(self):
        f = self._guard()
        a = {'champion_score': 10.0, 'scoring_formula_version': 'champion_score_v6_joint_kelly'}
        b = {'champion_score': -3.0, 'scoring_formula_version': 'champion_score_v6_joint_kelly'}
        assert f(a, b) is True

    def test_different_versions_are_not_comparable(self):
        f = self._guard()
        a = {'champion_score': 49.0, 'scoring_formula_version': 'champion_score_v2_walk_forward_calibrated'}
        b = {'champion_score': -26.0, 'scoring_formula_version': 'champion_score_v6_joint_kelly'}
        assert f(a, b) is False

    def test_missing_version_is_not_comparable(self):
        f = self._guard()
        a = {'champion_score': 49.0, 'scoring_formula_version': None}
        b = {'champion_score': -26.0, 'scoring_formula_version': 'champion_score_v6_joint_kelly'}
        assert f(a, b) is False

    def test_a_single_score_needs_no_comparison(self):
        f = self._guard()
        assert f({'champion_score': 1.0, 'scoring_formula_version': None}) is True

    def test_scores_absent_means_nothing_to_compare(self):
        f = self._guard()
        assert f({'champion_score': None}, {'champion_score': None}) is True
        assert f(None, None) is True

    def test_grouping_splits_series_by_version(self):
        grouped = group_by_formula_version([
            (133, 49.12, 'champion_score_v2_walk_forward_calibrated'),
            (212, -26.46, 'champion_score_v6_joint_kelly'),
            (201, -24.92, 'champion_score_v6_joint_kelly'),
            (140, 9.63, 'champion_score_v2_walk_forward_calibrated'),
            (99, 5.0, None),
            (100, None, 'champion_score_v6_joint_kelly'),
        ])
        assert list(grouped) == ['champion_score_v2_walk_forward_calibrated',
                                 'champion_score_v6_joint_kelly', 'unversioned']
        assert grouped['champion_score_v2_walk_forward_calibrated'] == [(133, 49.12), (140, 9.63)]
        assert grouped['champion_score_v6_joint_kelly'] == [(201, -24.92), (212, -26.46)]
        assert grouped['unversioned'] == [(99, 5.0)]
