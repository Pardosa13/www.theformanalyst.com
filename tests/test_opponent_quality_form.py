"""Point-in-time correctness of the opponent-quality form feature.

The score answers "who did this horse actually beat", so the only thing that
can make it useless is leakage: if a row can see the race it is a row of, or
anything after it, the feature stops being something live scoring could ever
reproduce.
"""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

pytest.importorskip("pandas")
pytest.importorskip("sqlalchemy")
pytest.importorskip("sklearn")

import backtest


def _run(rows):
    """rows: (race_id, meeting_date, horse_name, finish_position)."""
    race_ids = [row[0] for row in rows]
    dates = [row[1] for row in rows]
    names = [row[2] for row in rows]
    positions = [row[3] for row in rows]
    field_lookup = backtest._build_form_field_lookup(race_ids, names, positions)
    return backtest._compute_point_in_time_form_scores(names, race_ids, dates, field_lookup)


def test_a_horses_first_ever_run_carries_the_default_score():
    per_row, _final = _run([
        (1, '2026-01-01', 'alpha', 1),
        (1, '2026-01-01', 'bravo', 2),
    ])
    assert per_row[('alpha', 1)] == backtest.FORM_SCORE_DEFAULT
    assert per_row[('bravo', 1)] == backtest.FORM_SCORE_DEFAULT


def test_the_race_being_scored_never_feeds_its_own_feature_value():
    # Both runners meet on day one; the feature for that race must still be
    # the default, with the result only folded in for later races.
    per_row, final = _run([
        (1, '2026-01-01', 'alpha', 1),
        (1, '2026-01-01', 'bravo', 2),
        (2, '2026-01-08', 'alpha', 1),
        (2, '2026-01-08', 'charlie', 2),
    ])
    assert per_row[('alpha', 1)] == backtest.FORM_SCORE_DEFAULT
    # Race 1: alpha beat bravo (a 0.0-rated rival) by one place -> 0 + (2-1).
    assert per_row[('alpha', 2)] == pytest.approx(1.0)
    assert final['alpha'] == pytest.approx(1.0)


def test_beating_a_stronger_field_by_more_scores_higher():
    # Two horses each win their second start. `strong_beater` beats a rival
    # that has already proved itself, and beats it by three places; `weak`
    # scrapes past a rival with no record at all.
    per_row, _final = _run([
        (1, '2026-01-01', 'proven', 1),
        (1, '2026-01-01', 'tailed_off', 4),
        (2, '2026-01-08', 'strong_beater', 1),
        (2, '2026-01-08', 'proven', 4),
        (3, '2026-01-08', 'weak', 1),
        (3, '2026-01-08', 'unraced', 2),
        (4, '2026-01-15', 'strong_beater', 1),
        (4, '2026-01-15', 'weak', 2),
    ])
    assert per_row[('strong_beater', 4)] > per_row[('weak', 4)]


def test_a_days_results_do_not_leak_between_races_run_on_that_day():
    # `proven` runs on day two in one race and `later` in another the same
    # day. `later`'s opponent rating for `proven` must be `proven`'s day-one
    # score, not the score its day-two run produced.
    per_row, final = _run([
        (1, '2026-01-01', 'proven', 1),
        (1, '2026-01-01', 'tailed_off', 3),
        (2, '2026-01-08', 'proven', 3),
        (2, '2026-01-08', 'someone', 1),
        (3, '2026-01-08', 'later', 1),
        (3, '2026-01-08', 'proven', 2),
    ])
    # proven after day one: 0 + (3 - 1) = 2.0
    assert per_row[('proven', 2)] == pytest.approx(2.0)
    # later's day-two run reads proven at 2.0, plus the one place it beat it
    # by — it must not see whatever race 2 did to proven's score.
    assert final['later'] == pytest.approx(3.0)


def test_scores_do_not_depend_on_the_order_rows_arrive_in():
    forward = [
        (1, '2026-01-01', 'alpha', 1),
        (1, '2026-01-01', 'bravo', 2),
        (2, '2026-01-08', 'alpha', 2),
        (2, '2026-01-08', 'bravo', 1),
    ]
    _per_row_a, final_a = _run(forward)
    _per_row_b, final_b = _run(list(reversed(forward)))
    assert final_a == final_b


def test_the_feature_lands_in_the_training_matrix_with_its_race_relative_pair():
    features, _, _ = backtest.extract_features({'csv_data': {}}, {}, {})
    keys = list(features)
    # Column order comes from insertion order and is mirrored by
    # ml_predict.FEATURE_NAMES, so this adjacency is load-bearing.
    assert keys[keys.index('dam_win_rate') + 1] == 'opponent_quality_form'

    rows = backtest.add_race_relative_features(
        [dict(features, opponent_quality_form=2.0),
         dict(features, opponent_quality_form=-1.0)],
        [1, 1],
    )
    # Higher is better, so the better score must rank first.
    assert rows[0]['opponent_quality_form_race_rank'] == 1
    assert rows[1]['opponent_quality_form_race_rank'] == 2
    assert rows[0]['opponent_quality_form_vs_race_avg'] == pytest.approx(1.5)
