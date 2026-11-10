"""Jockey/trainer training inputs must never see a race's own result or later.

Before this, any training row without a dated PuntingForm snapshot kept
TODAY's jockey/trainer numbers, so old races were scored with form that had
not happened yet.
"""
import os

import numpy as np
import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

pytest.importorskip("pandas")
pytest.importorskip("sqlalchemy")
pytest.importorskip("sklearn")

import pandas as pd

import backtest
import point_in_time_rates as pit


def _history(runs):
    """runs: (name, date, won, expected)."""
    return pit.RunHistory([r[0] for r in runs], [r[1] for r in runs],
                          [r[2] for r in runs], [r[3] for r in runs])


def test_market_probabilities_sum_to_one_per_race():
    probs = pit.race_market_probabilities([1, 1, 2, 2, 2], [2.0, 2.0, 4.0, 4.0, None])
    assert probs[:2] == pytest.approx([0.5, 0.5])
    assert probs[2:] == pytest.approx([0.5, 0.5, 0.0])


def test_before_only_counts_strictly_earlier_dates():
    history = _history([
        ('j smith', '2026-03-01', 1, 0.5),
        ('j smith', '2026-03-02', 0, 0.2),
        ('j smith', '2026-03-02', 1, 0.3),   # same day as the race asked about
        ('j smith', '2026-03-09', 1, 0.4),   # after it
    ])
    assert history.before('j smith', '2026-03-02') == (1, 1.0, 0.5)
    assert history.since('j smith', '2026-03-02') == (3, 2.0, pytest.approx(0.9))
    assert history.before('nobody', '2026-03-02') == (0, 0.0, 0.0)


def test_win_pct_needs_ten_earlier_runs_and_uses_only_the_last_hundred():
    runs = [('j smith', f'2026-01-{d:02d}', 0, 0.1) for d in range(1, 10)]
    history = _history(runs)
    assert pit.win_pct_before(history, 'j smith', '2026-02-01') == -1.0   # 9 runs

    # 150 old losers then 100 recent winners: only the recent 100 count.
    runs = ([('j smith', '2025-06-01', 0, 0.1)] * 150) + ([('j smith', '2025-12-01', 1, 0.1)] * 100)
    history = _history(runs)
    assert pit.win_pct_before(history, 'j smith', '2026-01-01') == pytest.approx(100.0)
    assert pit.l100_a2e_before(history, 'j smith', '2026-01-01') == pytest.approx(10.0)


def test_future_results_never_change_an_earlier_rows_value():
    past = [('j smith', f'2026-01-{d:02d}', d % 2, 0.25) for d in range(1, 21)]
    future_good = past + [('j smith', '2026-06-01', 1, 0.05)] * 50
    future_bad = past + [('j smith', '2026-06-01', 0, 0.05)] * 50
    asked = '2026-02-01'
    for func in (pit.win_pct_before, pit.l100_a2e_before):
        assert func(_history(future_good), 'j smith', asked) == func(_history(future_bad), 'j smith', asked)


def test_career_totals_have_later_runs_taken_back_out():
    history = _history(
        [('j smith', '2026-01-10', 1, 0.5)] * 4            # before the race
        + [('j smith', '2026-03-01', 1, 1.0)] * 10)        # the race day and after
    current = {'career_runs': 1000, 'career_wins': 110, 'career_expected_wins': 100.0}
    career_a2e, career_runs = pit.career_before(history, 'j smith', '2026-03-01', current)
    assert career_runs == 990
    assert career_a2e == pytest.approx((110 - 10) / (100.0 - 10.0))


def test_career_a2e_falls_back_to_own_earlier_runs_for_a_new_rider():
    """Nearly the whole career sits after the race: today's totals say nothing."""
    history = _history(
        [('a prentice', '2026-01-10', 1, 0.5)] * 12
        + [('a prentice', '2026-03-01', 0, 0.5)] * 100)
    current = {'career_runs': 112, 'career_wins': 12, 'career_expected_wins': 56.0}
    career_a2e, career_runs = pit.career_before(history, 'a prentice', '2026-03-01', current)
    assert career_runs == 12
    assert career_a2e == pytest.approx(12 / 6.0)


def test_career_values_are_unknown_without_any_data():
    history = _history([])
    career_a2e, career_runs = pit.career_before(history, 'nobody', '2026-03-01', None)
    assert np.isnan(career_a2e) and np.isnan(career_runs)
    career_a2e, career_runs = pit.career_before(history, 'nobody', None, {'career_runs': 50})
    assert np.isnan(career_a2e) and np.isnan(career_runs)


def test_fill_keeps_dated_snapshots_and_replaces_fallbacks():
    names = ['j smith'] * 12
    dates = [f'2026-01-{d:02d}' for d in range(1, 13)]
    won = [1] * 12
    rows = [{'jockey_sr': 99.0, 'jockey_l100_a2e': 9.0, 'jockey_career_a2e': 9.0,
             'jockey_career_runs': 9.0} for _ in names]
    dated = [False] * 11 + [True]
    stats = pit.fill_point_in_time_rates(rows, names, dates, won, [0.5] * 12, 'jockey', dated, {})
    assert rows[0]['jockey_sr'] == -1.0            # nothing earlier
    assert rows[10]['jockey_sr'] == 100.0          # 10 earlier runs, all winners
    assert rows[11]['jockey_sr'] == 99.0           # dated PuntingForm copy kept
    assert np.isnan(rows[0]['jockey_l100_a2e'])
    assert rows[10]['jockey_l100_a2e'] == pytest.approx(2.0)
    assert stats['sr_dated_snapshot'] == 1 and stats['sr_own_results'] == 1 and stats['sr_unknown'] == 10


def _training_frame(later_winner):
    """Three race days. Jockey 'J Smith' rides in every race; the day-3 result
    is the only thing that differs between the two frames."""
    rows = []
    horse_id = 0
    for day, race_id in ((1, 101), (2, 102), (3, 103)):
        for runner in range(1, 13):
            horse_id += 1
            if day < 3:
                finish = runner
            else:
                finish = 1 if runner == later_winner else (runner + 1 if runner < later_winner else runner)
            rows.append({
                'horse_id': horse_id, 'race_id': race_id, 'horse_name': f'Horse {horse_id}',
                'csv_data': {'horse jockey': 'J Smith' if runner == 1 else f'Rider {runner}',
                             'horse trainer': f'Trainer {runner}', 'horse weight': '56',
                             'horse number': str(runner), 'race number': '1'},
                'is_scratched': False, 'finish_position': finish, 'sp': 12.0,
                'track_condition': 'good', 'race_distance': 1200, 'race_class': 'BM64',
                'meeting_id': day, 'meeting_date': f'2026-03-{day:02d}', 'meeting_track': 'Testville',
                'meeting_name': f'2603{day:02d}_Testville', 'rail_position': 0,
                'analyzer_score': 50.0, 'analyzer_notes': '', 'predicted_odds': 12.0,
            })
    return pd.DataFrame(rows)


def test_training_rows_do_not_change_when_only_later_results_change():
    """The whole point: a later result must not move an earlier row's inputs."""
    def sr_data(later_winner):
        # Today's career totals already hold the day-3 result, as they would.
        current = {'j smith': {'career_a2e': 3.0, 'l100_a2e': 3.0, 'career_runs': 500,
                               'career_wins': 79 + (1 if later_winner == 1 else 0),
                               'career_expected_wins': 60.0}}
        return {'jockeys': {}, 'trainers': {}, 'jockeys_extra': current, 'trainers_extra': {}}

    cols = ['jockey_sr', 'jockey_l100_a2e', 'jockey_career_a2e', 'jockey_career_runs',
            'trainer_sr', 'trainer_l100_a2e', 'trainer_career_a2e', 'trainer_career_runs']

    frames = []
    for later_winner in (1, 7):   # J Smith wins on day 3, or does not
        X, _y_roi, _y_won, _sp, race_ids, _horse_ids, _dates = backtest.build_training_set(
            _training_frame(later_winner), sr_data(later_winner))
        frames.append(X.assign(race_id=race_ids))
    a, b = frames
    early = a.race_id.isin([101, 102])
    assert early.sum() == 24
    pd.testing.assert_frame_equal(a.loc[early, cols].reset_index(drop=True),
                                  b.loc[b.race_id.isin([101, 102]), cols].reset_index(drop=True))
    # Today's leaked numbers (3.0) are gone from every row.
    assert not (a['jockey_l100_a2e'] == 3.0).any()
    assert not (a['jockey_career_a2e'] == 3.0).any()
    # Day 1 has no earlier runs, so the rider's rate is the live 'unknown' value.
    assert (a.loc[a.race_id == 101, 'jockey_sr'] == -1.0).all()
