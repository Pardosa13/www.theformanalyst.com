"""Past-run (runs 2-10) features: parsing, leakage guard, and train/serve parity."""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

np = pytest.importorskip("numpy")
pytest.importorskip("pandas")
pytest.importorskip("sqlalchemy")

import past_runs

HEADER = (
    "meeting date,track,race number,distance,horse name,horse weight,horse number,"
    "form meeting date,form track,form distance,form position,form margin,form price,"
    "form time,form track condition,form weight, race id\n"
)


def _row(name, form_date, position, margin, price='5.0', form_time='01:10.00',
         form_track='Flemington', form_distance='1200', condition='Good 4', race_id='1',
         meeting_date='20/10/2026 12:00:00 AM', track='Flemington', race_no='3'):
    return (f"{meeting_date},{track},{race_no},1200m,{name},58,1,{form_date},{form_track},"
            f"{form_distance},{position},{margin},{price},{form_time},{condition},57,{race_id}\n")


def test_uses_every_saved_run_not_just_last_start():
    csv_text = HEADER + ''.join([
        _row('Alpha', '01/10/26', 1, 2.0, race_id='10'),
        _row('Alpha', '15/09/26', 5, 4.0, race_id='11', condition='Heavy 8'),
        _row('Alpha', '01/09/26', 3, 1.0, race_id='12', form_track='Caulfield'),
    ])
    feats = past_runs.past_run_features_for_meeting(csv_text)[('3', 'alpha')]
    assert feats['pr_n_runs'] == 3
    # A win is stored as a negative margin: (-2 + 4 + 1) / 3
    assert feats['pr_avg_margin_l3'] == pytest.approx(1.0)
    assert feats['pr_best_margin_l5'] == pytest.approx(-2.0)
    assert feats['pr_win_rate_l5'] == pytest.approx(1 / 3)
    assert feats['pr_place_rate_l5'] == pytest.approx(2 / 3)
    assert feats['pr_track_runs_l10'] == 2
    assert feats['pr_wet_avg_margin_l10'] == pytest.approx(4.0)
    assert feats['pr_days_l1_l2'] == 16
    # Last margin -2 vs average of runs 2-3 (2.5)
    assert feats['pr_margin_trend'] == pytest.approx(-4.5)


def test_horse_speed_adjusts_race_time_by_margin():
    # Same race, same winner time: the beaten horse must come out slower.
    csv_text = HEADER + ''.join([
        _row('Winner', '01/10/26', 1, 1.5, race_id='20'),
        _row('Beaten', '01/10/26', 6, 5.0, race_id='20'),
    ])
    out = past_runs.past_run_features_for_meeting(csv_text)
    assert out[('3', 'winner')]['pr_last_adj_speed'] == pytest.approx(1200 / 70.0)
    assert out[('3', 'beaten')]['pr_last_adj_speed'] == pytest.approx(1200 / (70.0 + 5 * 0.17))


def test_runs_on_or_after_meeting_date_are_ignored():
    csv_text = HEADER + ''.join([
        _row('Gamma', '20/10/26', 1, 3.0, race_id='30'),   # the race itself
        _row('Gamma', '25/10/26', 1, 3.0, race_id='31'),   # future
        _row('Gamma', '01/10/26', 8, 6.0, race_id='32'),
    ])
    feats = past_runs.past_run_features_for_meeting(csv_text)[('3', 'gamma')]
    assert feats['pr_n_runs'] == 1
    assert feats['pr_win_rate_l5'] == 0


def test_first_starter_and_bad_input():
    csv_text = HEADER + _row('Debut', '', '', '')
    feats = past_runs.past_run_features_for_meeting(csv_text)[('3', 'debut')]
    assert feats['pr_n_runs'] == 0
    assert np.isnan(feats['pr_avg_margin_l3'])
    assert past_runs.past_run_features_for_meeting(None) == {}
    assert past_runs.past_run_features_for_meeting('not,a\nform file') == {}


def test_training_and_live_extract_identical_past_run_values():
    pytest.importorskip("sklearn")
    import backtest
    import ml_predict

    csv_text = HEADER + ''.join([
        _row('Alpha', '01/10/26', 2, 1.0, race_id='10'),
        _row('Alpha', '15/09/26', 1, 0.5, race_id='11'),
    ])
    per_horse = past_runs.past_run_features_for_meeting(csv_text)
    cd = {'horse name': 'Alpha', 'race number': '3', 'horse weight': '58', 'distance': '1200m'}

    training_lookup = {(77, race_no, horse): f for (race_no, horse), f in per_horse.items()}
    train_feats, _, _ = backtest.extract_features(
        {'csv_data': cd, 'meeting_id': 77, 'horse_name': 'Alpha', 'track_condition': 'good'},
        past_run_lookup=training_lookup,
    )
    live_feats = ml_predict.extract_features(cd, 'good', past_run_lookup=per_horse)
    # load_past_run_lookup stores tuples to save memory; they must decode identically.
    tuple_lookup = {k: tuple(f[n] for n in past_runs.PAST_RUN_FEATURES) for k, f in training_lookup.items()}
    tuple_feats, _, _ = backtest.extract_features(
        {'csv_data': cd, 'meeting_id': 77, 'horse_name': 'Alpha', 'track_condition': 'good'},
        past_run_lookup=tuple_lookup,
    )
    assert tuple_feats['pr_avg_margin_l3'] == train_feats['pr_avg_margin_l3']

    for name in past_runs.PAST_RUN_FEATURES:
        a, b = train_feats[name], live_feats[name]
        assert (np.isnan(a) and np.isnan(b)) or a == pytest.approx(b), name
    assert train_feats['pr_n_runs'] == 2

    # Column order: the past-run block sits right after opponent_quality_form in both.
    train_keys = list(train_feats)
    live_keys = list(live_feats)
    start = train_keys.index('opponent_quality_form') + 1
    assert train_keys[start:start + len(past_runs.PAST_RUN_FEATURES)] == past_runs.PAST_RUN_FEATURES
    assert live_keys[live_keys.index('opponent_quality_form') + 1:][:len(past_runs.PAST_RUN_FEATURES)] == \
        past_runs.PAST_RUN_FEATURES
    names = ml_predict.FEATURE_NAMES
    idx = names.index('opponent_quality_form') + 1
    assert names[idx:idx + len(past_runs.PAST_RUN_FEATURES)] == past_runs.PAST_RUN_FEATURES
    for col in past_runs.PAST_RUN_RELATIVE_COLS:
        assert f'{col}_race_rank' in names and f'{col}_vs_race_avg' in names
