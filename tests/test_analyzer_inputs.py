"""The analyzer's score and components as ML inputs (analyzer_inputs.py)."""
import math

import numpy as np
import pandas as pd

import analyzer_inputs as ai

NOTES = ('+20.0: Jockey hot form\n-10.0: Trainer cold\n+5.0: ** SIGNALS AGREE\n'
         'Analyzer Score (normalized): 55.0')


def test_runner_features_from_notes():
    f = ai.runner_features(70.0, NOTES)
    assert f['an_score'] == 70.0
    assert f['an_raw_score'] == 55.0
    assert f['an_points_pos'] == 20.0      # the SIGNALS AGREE line is left out
    assert f['an_points_neg'] == 10.0
    assert f['an_cat_jockey'] == 1.0
    assert f['an_cat_trainer'] == 1.0


def test_missing_notes_stay_blank_not_zero():
    f = ai.runner_features(None, None)
    assert all(math.isnan(v) for v in f.values())
    f = ai.runner_features(0.0, 'Scratched')
    assert all(math.isnan(v) for v in f.values())


def test_training_and_live_give_identical_values():
    rows = [{'x': 1, ai.SCORE_KEY: 80.0, ai.NOTES_KEY: NOTES},
            {'x': 2, ai.SCORE_KEY: 60.0, ai.NOTES_KEY: NOTES},
            {'x': 3, ai.SCORE_KEY: 70.0, ai.NOTES_KEY: NOTES}]
    live = ai.add_analyzer_features(pd.DataFrame(rows))
    training = pd.DataFrame(rows + [{'x': 9, ai.SCORE_KEY: 10.0, ai.NOTES_KEY: NOTES}])
    training['race_id'] = [1, 1, 1, 2]
    training = ai.add_analyzer_features(training, race_key='race_id').iloc[:3]
    assert list(live.columns) == ['x'] + ai.ANALYZER_FEATURES
    for col in ai.ANALYZER_FEATURES:
        np.testing.assert_allclose(live[col].values, training[col].values)
    assert list(live['an_score_race_rank']) == [1.0, 3.0, 2.0]
    assert live['an_score_vs_race_avg'].iloc[0] == 10.0
