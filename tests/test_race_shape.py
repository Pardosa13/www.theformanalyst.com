"""First starters, weight in lengths and pace shape (race_shape.py + pf_form speed points)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

import feature_ablation
import pf_form
from race_shape import RACE_SHAPE_FEATURES, add_race_shape_features, kg_per_length


def test_quirin_points():
    assert pf_form.quirin_points(1, 10) == 4
    assert pf_form.quirin_points(2, 10) == 2
    assert pf_form.quirin_points(3, 10) == 1
    assert pf_form.quirin_points(4, 16) == 1   # front quarter of a big field
    assert pf_form.quirin_points(6, 10) == 0
    assert pf_form.quirin_points(None, 10) is None


def _form(day, settle):
    return {'meetingDate': f'{day}T00:00:00', 'position': 3, 'starters': 10,
            'inRun': f'finish,3;settling_down,{settle};m400,3;'}


def test_speed_points_and_most_forward_ever():
    runner = pf_form.trim_runner({'name': 'A', 'tabNo': 1, 'forms': [
        _form('2026-10-01', 1), _form('2026-09-01', 2), _form('2026-08-01', 5), _form('2026-07-01', 4),
    ]})
    out = pf_form.runner_features(runner, date(2026, 10, 20))
    assert out['ir_speed_points_l3'] == 4 + 2 + 0
    # Last settle 1st (0.0) vs most forward earlier, 2nd (1/9).
    assert out['ir_settle_vs_best'] == pytest.approx(0 - 1 / 9)


def test_unreadable_run_leaves_speed_points_blank():
    runner = pf_form.trim_runner({'name': 'A', 'tabNo': 1, 'forms': [
        _form('2026-10-01', 1), {'meetingDate': '2026-09-01T00:00:00', 'position': 2, 'starters': 10},
    ]})
    out = pf_form.runner_features(runner, date(2026, 10, 20))
    assert np.isnan(out['ir_speed_points_l3'])


def test_kg_per_length_falls_with_distance():
    assert kg_per_length(1000) == pytest.approx(1.75)
    assert kg_per_length(1600) == pytest.approx(1.0)
    assert kg_per_length(1200) > kg_per_length(2000) > kg_per_length(2400)
    assert kg_per_length(800) == pytest.approx(1.75)   # clamped


def _race(**cols):
    temp = pd.DataFrame(cols)
    temp['field_size'] = len(temp)
    return add_race_shape_features(temp)


def test_debut_share_and_weight():
    temp = _race(career_runs=[0, 0, 5, 12], horse_weight=[54.0, 56.0, 58.0, 60.0],
                 weight_vs_avg=[3.0, 1.0, -1.0, -3.0], distance=[1200] * 4)
    assert list(temp['rs_is_debut']) == [1, 1, 0, 0]
    assert temp['rs_debut_share'].iloc[0] == 0.5
    assert list(temp['rs_debut_x_share']) == [0.5, 0.5, 0, 0]
    # 3kg over the average at 1200m (1.5kg a length) = 2 lengths.
    assert temp['rs_weight_lengths'].iloc[3] == pytest.approx(2.0)
    assert temp['rs_weight_over_53_5'].iloc[0] == pytest.approx(0.5)


def test_lone_leader_and_pace_pressure():
    temp = _race(ir_speed_points_l3=[10, 2, 0, np.nan])
    assert list(temp['rs_rival_speed_max'][:3]) == [2, 10, 10]
    assert temp['rs_lone_speed'].iloc[0] == 1
    assert temp['rs_lone_speed'].iloc[1] == 0
    assert np.isnan(temp['rs_lone_speed'].iloc[3])   # unknown horse stays blank
    assert (temp['rs_n_speed'] == 1).all()
    assert (temp['rs_race_speed_total'] == 12).all()

    contested = _race(ir_speed_points_l3=[10, 8, 0])
    assert contested['rs_lone_speed'].iloc[0] == 0
    assert (contested['rs_n_speed'] == 2).all()


def test_no_in_run_data_means_blank_not_zero():
    temp = _race(ir_speed_points_l3=[np.nan, np.nan])
    for name in ('rs_n_speed', 'rs_race_speed_total', 'rs_rival_speed_max', 'rs_lone_speed'):
        assert temp[name].isna().all(), name


def test_style_change():
    # Speed map has it settling 1st of 5 (0.0); it usually settles at 0.75.
    temp = _race(sm_settle=[1, 5, 3, 2, 4], ir_avg_settle_pct_l5=[0.75, 0.5, np.nan, 0.25, 0.75])
    assert temp['rs_style_change'].iloc[0] == pytest.approx(-0.75)
    assert np.isnan(temp['rs_style_change'].iloc[2])


def test_training_frame_groups_by_race():
    temp = pd.DataFrame({'race_id': [1, 1, 2, 2], 'career_runs': [0, 3, 0, 0],
                         'ir_speed_points_l3': [8, 0, 8, 8]})
    temp['field_size'] = 2
    add_race_shape_features(temp, race_key='race_id')
    assert list(temp['rs_debut_share']) == [0.5, 0.5, 1.0, 1.0]
    assert list(temp['rs_lone_speed']) == [1, 0, 0, 0]
    assert set(RACE_SHAPE_FEATURES) <= set(temp.columns)


def test_ablation_measures_the_new_groups_separately():
    groups = feature_ablation.FEATURE_GROUPS
    assert set(groups['debut_weight']) | set(groups['pace_shape']) >= set(RACE_SHAPE_FEATURES)
    assert 'ir_speed_points_l3' in groups['pace_shape']
    assert 'ir_speed_points_l3' not in groups['pf_form_feed']
    assert {'debut_weight', 'pace_shape'} <= set(feature_ablation.NEW_GROUPS)
