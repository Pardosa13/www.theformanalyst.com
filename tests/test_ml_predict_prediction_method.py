from pathlib import Path

SOURCE = Path('ml_predict.py').read_text()


def _function_source(name: str) -> str:
    start = SOURCE.index(f'def {name}(')
    next_def = SOURCE.find('\ndef ', start + 1)
    return SOURCE[start:] if next_def == -1 else SOURCE[start:next_def]


def test_live_prediction_helper_prefers_predict_proba_for_classifiers():
    source = _function_source('_predict_raw_scores')
    assert "hasattr(model, 'predict_proba')" in source
    assert 'model.predict_proba(X)' in source
    assert 'probabilities[:, 1]' in source
    assert "'predict_proba'" in source
    assert 'model.predict(X)' in source
    assert "'predict'" in source


def test_predict_meeting_uses_prediction_helper_and_preserves_race_normalisation():
    source = _function_source('predict_meeting')
    assert '_predict_raw_scores(model, X)' in source
    assert 'model.predict(X)' not in source
    assert '((raw_preds - min_p) / (max_p - min_p)) * 100' in source
    assert 'np.full_like(raw_preds, 50.0)' in source
    assert 'method=%s raw_min=%s raw_max=%s normalised_min=%s normalised_max=%s' in source
