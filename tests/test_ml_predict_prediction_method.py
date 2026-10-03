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
    assert 'method=%s probability_source=%s probability_sum=%.6f' in source
    assert 'raw_min=%s raw_max=%s normalised_min=%s normalised_max=%s' in source


def test_predict_meeting_returns_model_probabilities_not_the_display_score():
    """ml_score is a 0-100 display stretch. Value edge and Kelly need the
    model's own race win probabilities, which must be produced from the raw
    model output and blended BEFORE the display stretch is applied."""
    source = _function_source('predict_meeting')
    produce = 'win_probabilities, probability_source = race_win_probabilities(model, raw_preds)'
    blend = '_blend_race_with_live_market(\n            win_probabilities,'
    stretch = '((raw_preds - min_p) / (max_p - min_p)) * 100'
    assert produce in source
    assert blend in source
    assert source.index(produce) < source.index(blend) < source.index(stretch)
    assert 'return all_scores, by_race, probabilities_by_race' in source


def test_predict_meeting_logs_feature_diagnostics_immediately_before_scoring():
    source = _function_source('predict_meeting')
    diagnostics_call = '_log_prediction_feature_diagnostics(model, meeting_id, race, X_raw)'
    prediction_call = 'raw_preds, prediction_method = _predict_raw_scores(model, X)'
    assert diagnostics_call in source
    assert source.index(diagnostics_call) < source.index(prediction_call)
    between = source[source.index(diagnostics_call) + len(diagnostics_call):source.index(prediction_call)]
    assert 'log.' not in between


def test_feature_diagnostics_log_contains_required_fields():
    source = _function_source('_log_prediction_feature_diagnostics')
    for field in [
        'model_id=%s',
        'model_type=%s',
        'model_class=%s',
        'predict_method=%s',
        'expected_feature_count=%s',
        'generated_feature_count=%s',
        'feature_counts_match=%s',
        'feature_order_matches=%s',
        'missing_feature_names=%s',
        'extra_feature_names=%s',
        'features_defaulted_to_zero=%s',
        'model_first_10_feature_names=%s',
        'generated_first_10_feature_names=%s',
    ]:
        assert field in source
    assert 'log.info(' in source
    assert "'predict_proba' if hasattr(model, 'predict_proba') else 'predict'" in source
