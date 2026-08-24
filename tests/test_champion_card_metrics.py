"""The champion card must report ROI / strike rate the way the challenger does.

The Active Production Model panel used to carry no ROI or strike rate at all,
so the only way to compare the running champion with the latest challenger was
to trust two numbers that were never measured the same way. Both cards now read
those metrics out of the same backtest_best_model columns through the same
mapper — these tests exist to keep them on that single path, because two
independently computed "ROI"s on one screen invite exactly the wrong comparison.
"""

import re
from pathlib import Path

import pytest

APP_SOURCE = Path('app.py').read_text()
ML_PREDICT_SOURCE = Path('ml_predict.py').read_text()
TEMPLATE = Path('templates/ml_data.html').read_text()


def _function_source(source: str, name: str) -> str:
    start = source.index(f'def {name}(')
    next_def = source.find('\ndef ', start + 1)
    next_route = source.find('\n@app.route(', start + 1)
    end = min(x for x in (next_def, next_route, len(source)) if x != -1)
    return source[start:end]


def test_champion_and_challenger_read_the_same_columns_through_the_same_mapper():
    champion = _function_source(APP_SOURCE, 'champion_model_backtest_summary')
    challenger = _function_source(APP_SOURCE, 'latest_ml_challenger_summary')

    for source in (champion, challenger):
        assert '_ML_MODEL_SUMMARY_COLUMNS' in source
        assert '_ml_model_summary_from_row(row)' in source

    assert 'bm.validation_roi' in APP_SOURCE.split('_ML_MODEL_SUMMARY_COLUMNS = """')[1]
    assert 'bm.validation_strike_rate' in APP_SOURCE.split('_ML_MODEL_SUMMARY_COLUMNS = """')[1]


def test_champion_summary_is_looked_up_by_the_champions_own_model_or_run_id():
    champion = _function_source(APP_SOURCE, 'champion_model_backtest_summary')
    assert 'WHERE bm.id = :model_id' in champion
    assert 'WHERE bm.run_id = :run_id' in champion
    # Never the newest completed run: that is the challenger's row, not the
    # champion's, and reading it here would show the challenger's numbers twice.
    assert "br.status = 'complete'" not in champion


def test_ml_data_route_puts_roi_and_strike_rate_on_the_champion_metadata():
    route = _function_source(APP_SOURCE, 'ml_data_analytics')
    assert 'champion_model_backtest_summary(' in route
    assert "run_id=active_model_metadata.get('training_backtest_run_id')" in route
    assert "active_model_metadata['roi'] = champion_backtest.get('roi')" in route
    assert "active_model_metadata['strike_rate'] = champion_backtest.get('strike_rate')" in route


def test_active_production_model_metadata_exposes_its_model_id():
    metadata = _function_source(ML_PREDICT_SOURCE, 'active_production_model_metadata')
    assert "'model_id': getattr(model, '_form_analyst_model_id', None)" in metadata


def _roi_field(card: str) -> str:
    match = re.search(
        r'<div class="active-model-label">ROI / Strike Rate</div>\s*'
        r'(<div class="active-model-value">.*?</div>)',
        card, re.S,
    )
    assert match, 'card is missing its ROI / Strike Rate field'
    return match.group(1)


def _cards():
    champion, challenger = TEMPLATE.split('<div class="latest-challenger">')
    return champion, challenger


def test_both_cards_render_an_roi_strike_rate_field():
    champion, challenger = _cards()
    assert _roi_field(champion)
    assert _roi_field(challenger)


def test_the_two_cards_format_the_same_numbers_identically():
    jinja2 = pytest.importorskip('jinja2')
    env = jinja2.Environment()
    champion, challenger = _cards()

    metrics = {'roi': 12.3456, 'strike_rate': 21.0}
    champion_out = env.from_string(_roi_field(champion)).render(active_model_metadata=metrics)
    challenger_out = env.from_string(_roi_field(challenger)).render(latest_challenger=metrics)
    assert champion_out == challenger_out == '<div class="active-model-value">12.3% / 21.0%</div>'


def test_the_champion_card_says_unknown_rather_than_erroring_without_metrics():
    jinja2 = pytest.importorskip('jinja2')
    env = jinja2.Environment()
    champion, _ = _cards()
    field = env.from_string(_roi_field(champion))

    assert 'Unknown / Unknown' in field.render(active_model_metadata={'roi': None, 'strike_rate': None})
    # A metadata dict that predates these keys must not 500 the whole page.
    assert 'Unknown / Unknown' in field.render(active_model_metadata={})
