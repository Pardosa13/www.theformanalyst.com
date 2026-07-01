from pathlib import Path

APP_SOURCE = Path('app.py').read_text()

ML_DATA_ROUTES = [
    'api_jurisdiction_strength',
    'api_state_performance',
    'api_score_analysis',
    'api_component_analysis',
    'api_external_factors',
    'api_probability_calibration',
    'api_price_analysis',
    'api_pnl_over_time',
    'api_sole_leader_analysis',
    'api_field_size',
    'api_days_since_run',
    'api_market_divergence',
    'api_monthly_performance',
    'api_pfai_analysis',
    'api_combination_analysis',
    'api_betting_filters',
    'api_race_tempo_analysis',
]


def _function_source(name: str) -> str:
    start = APP_SOURCE.index(f'def {name}(')
    next_route = APP_SOURCE.find('\n@app.route(', start + 1)
    return APP_SOURCE[start:] if next_route == -1 else APP_SOURCE[start:next_route]


def test_ml_data_api_routes_honor_source_ml():
    for route_name in ML_DATA_ROUTES:
        source = _function_source(route_name)
        assert "request.args.get('source', '') == 'ml'" in source, route_name
        assert (
            '_filter_ml_predictions(' in source
            or '_join_ml_predictions_for_race_ids(' in source
            or 'Prediction.ml_score.isnot(None)' in source
        ), route_name


def test_ml_data_page_always_requests_ml_source():
    template = Path('templates/ml_data.html').read_text()
    assert "source: 'ml'" in template
