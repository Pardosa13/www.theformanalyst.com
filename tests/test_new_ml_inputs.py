"""Track bias, saved PF form feed, and the PuntingForm race-id fix."""
import json
import os
from datetime import date, datetime

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("sqlalchemy")

import pf_form
import track_bias


def _cd(track='Caulfield', distance='1200m', barrier='1', style='LEADER'):
    return {'track': track, 'distance': distance, 'horse barrier': barrier, 'runningposition': style}


# ── Track bias ────────────────────────────────────────────────────────────────

def test_track_bias_only_uses_earlier_days():
    day1, day2 = date(2026, 1, 1), date(2026, 1, 2)
    rows = [
        (day1, _cd(barrier='1'), 1),
        (day1, _cd(barrier='12'), 5),
        (day2, _cd(barrier='1'), 3),   # must see day1 only, not itself
    ]
    feats = track_bias.point_in_time_features(rows)
    assert np.isnan(feats[0]['tb_barrier_win_rate'])
    assert feats[2]['tb_barrier_starts'] == 1   # the day-1 barrier-1 runner only
    # Same-day rows never see each other: with no earlier history there is nothing to report
    assert np.isnan(feats[1]['tb_barrier_starts'])


def test_track_bias_shrinks_small_samples_and_shows_an_edge():
    stats = track_bias.TrackBiasStats()
    # Everywhere: barrier 1 wins 10%. At Caulfield sprints: barrier 1 wins 40% over 200 starts.
    stats.add_counts('Flemington', '1200m', '1', 'LEADER', 1000, 100, 300)
    stats.add_counts('Caulfield', '1200m', '1', 'LEADER', 200, 80, 120)
    caulfield = stats.features(*track_bias.runner_inputs(_cd()))
    assert caulfield['tb_barrier_win_edge'] > 1.5
    # A track with 2 starts stays close to the all-tracks rate
    stats.add_counts('Hanging Rock', '1200m', '1', 'LEADER', 2, 2, 2)
    tiny = stats.features(*track_bias.runner_inputs(_cd(track='Hanging Rock')))
    assert 0.9 < tiny['tb_barrier_win_edge'] < 1.4


def test_live_aggregated_counts_match_training_walk():
    day1, day2 = date(2026, 1, 1), date(2026, 1, 2)
    history = [(day1, _cd(barrier=str(b % 6 + 1), style=s), f)
               for b, (s, f) in enumerate([('LEADER', 1), ('ONPACE', 2), ('BACKMARKER', 3),
                                           ('LEADER', 4), ('MIDFIELD', 1), ('LEADER', 6)])]
    target = (day2, _cd(barrier='1', style='LEADER'), 1)
    training = track_bias.point_in_time_features(history + [target])[-1]
    live = track_bias.TrackBiasStats()
    for _, cd, finish in history:
        live.add_counts(*track_bias.runner_inputs(cd), 1, int(finish == 1), int(finish <= 3))
    live_feats = live.features(*track_bias.runner_inputs(target[1]))
    for name in track_bias.TRACK_BIAS_FEATURES:
        assert training[name] == pytest.approx(live_feats[name]), name


# ── Saved PF form feed ────────────────────────────────────────────────────────

def _raw_runner(forms, **extra):
    runner = {'name': 'Alpha', 'tabNo': 3, 'barrier': 6, 'originalBarrier': 8, 'handicap': 64,
              'prepRuns': 3, 'gearChanges': 'Blinkers first time. Tongue tie off.',
              'position': 1, 'margin': 2.0, 'priceSP': 3.0, 'careerWins': 5, 'forms': forms}
    runner.update(extra)
    return runner


def _form(day, position=2, starters=10, in_run='finish,2;settling_down,8;m800,7;m400,6;',
          stewards='', trial=False, flucs='opening,6.00;starting,4.00;', top4=None):
    return {'meetingDate': f'{day}T00:00:00', 'position': position, 'starters': starters,
            'inRun': in_run, 'stewardsReport': stewards, 'isBarrierTrial': trial, 'flucs': flucs,
            'priceSP': 4.0, 'priceBF': 4.6,
            'top4Finishers': top4 or [{'runnerName': 'Rival', 'position': 1}]}


def test_trim_runner_drops_fields_that_change_after_the_race():
    trimmed = pf_form.trim_runner(_raw_runner([_form('2026-09-01')]))
    for leaked in ('position', 'margin', 'priceSP', 'careerWins'):
        assert leaked not in trimmed


def test_runner_features_from_saved_feed():
    runner = pf_form.trim_runner(_raw_runner([
        _form('2026-10-20', position=1),                          # today's race: ignored
        _form('2026-10-01', stewards='Held up for a run near the 200m.'),
        _form('2026-09-25', trial=True),                          # trial before the last run
        _form('2026-09-10', position=5, in_run='finish,5;settling_down,2;m400,3;'),
    ]))
    feats = pf_form.runner_features(runner, date(2026, 10, 20), rival_names=['Rival', 'Alpha'])
    assert feats['pf_handicap_rating'] == 64
    assert feats['pf_barrier_moved'] == -2
    assert feats['pf_blinkers_on'] == 1 and feats['pf_gear_off'] == 1
    assert feats['st_excuse_last'] == 1
    assert feats['pf_trials_since_last'] == 0
    # Last start: settled 8th of 10 -> 7/9; 6th at the 400 -> 2nd: gained 4 of 9
    assert feats['ir_last_settle_pct'] == pytest.approx(7 / 9)
    assert feats['ir_last_late_gain'] == pytest.approx(4 / 9)
    assert feats['mk_last_drift'] == pytest.approx(4 / 6)
    # Beaten by 'Rival' in both counted races
    assert feats['ft_net_vs_rivals'] == -2


def test_conditions_read_last_24_hours_only():
    feats = pf_form.conditions_features({
        'penetrometer': '5.26', 'rainfall': 'nil last 24 hours, 4mm last 7 days',
        'irrigation': '8mm last 24 hours, 30mm last 7 days', 'weather': 'Showers'})
    assert feats == {'cond_penetrometer': 5.26, 'cond_rain_mm': 0.0,
                     'cond_irrigation_24h_mm': 8.0, 'cond_weather': 2.0}


def test_snapshot_only_counts_when_saved_before_the_jump():
    start = datetime(2026, 10, 20, 4, 0)
    assert pf_form.snapshot_usable(datetime(2026, 10, 19, 21, 0), start)
    assert not pf_form.snapshot_usable(datetime(2026, 10, 20, 5, 0), start)
    assert not pf_form.snapshot_usable(None, start)
    assert not pf_form.snapshot_usable(datetime(2026, 10, 19, 21, 0), None)


# ── PuntingForm race-id fix ───────────────────────────────────────────────────

def test_ratings_are_looked_up_by_todays_race_not_last_start():
    pytest.importorskip("sklearn")
    import backtest
    import ml_predict

    todays = {'raceId': 5001, 'tabNo': 2, 'pfaiPrice': 3.5, 'pfaiRank': 1}
    last_start_other_horse = {'raceId': 4001, 'tabNo': 2, 'pfaiPrice': 41.0, 'pfaiRank': 12}
    ratings = {(5001, 2): todays, (4001, 2): last_start_other_horse}
    cd = {'race id': '4001', 'horse number': '2', 'race number': '6'}   # 'race id' = last start

    train, _, _ = backtest.extract_features(
        {'csv_data': cd, 'meeting_id': 9, 'track_condition': 'good'},
        pf_ratings_lookup=ratings, pf_race_ids={(9, 6): 5001})
    live = ml_predict.extract_features(cd, 'good', pf_ratings_lookup=ratings, pf_race_id=5001)
    assert train['pfai_price'] == live['pfai_price'] == 3.5

    # No race id for today -> blank, never the last-start race's runner
    train_blank, _, _ = backtest.extract_features(
        {'csv_data': cd, 'meeting_id': 9, 'track_condition': 'good'}, pf_ratings_lookup=ratings)
    assert np.isnan(train_blank['pfai_price'])


def test_new_artifacts_are_stamped_for_the_fixed_lookup():
    pytest.importorskip("sklearn")
    import backtest
    import ml_predict

    class M:
        pass

    model = backtest._attach_model_metadata(M(), 'x', 'x', 1, 'a.pkl', ['f'], {})
    assert model._form_analyst_pf_lookup_version == ml_predict.PF_LOOKUP_VERSION == backtest.PF_LOOKUP_VERSION


def test_training_fills_track_bias_from_earlier_meetings():
    pytest.importorskip("sklearn")
    import backtest

    df = pd.DataFrame([
        {'horse_id': 1, 'meeting_date': '2026-01-01', 'finish_position': 1, 'csv_data': json.dumps(_cd())},
        {'horse_id': 2, 'meeting_date': '2026-01-08', 'finish_position': 4, 'csv_data': json.dumps(_cd())},
    ])
    rows = [{}, {}]
    backtest._fill_point_in_time_track_bias(rows, [1, 2], df)
    assert np.isnan(rows[0]['tb_barrier_starts'])   # no earlier history
    assert rows[1]['tb_barrier_starts'] == 1


def test_training_medians_never_leave_gaps():
    """A column empty in every training row (new data only on recent races)
    must not stay NaN after filling: the MLP candidate rejects any NaN."""
    pytest.importorskip("sklearn")
    import backtest

    X = pd.DataFrame({'a': [1.0, np.nan, 3.0], 'new_source': [np.nan, np.nan, np.nan]})
    medians = backtest._training_medians(X)
    assert medians['a'] == 2.0 and medians['new_source'] == 0.0
    assert not X.fillna(medians).isna().any().any()
