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


def test_ml_performance_stats_accepts_filters_and_limits():
    source = _function_source('calculate_ml_performance_stats')
    assert 'track_filter=""' in source
    assert 'limit_param="all"' in source
    assert 'LOWER(m.meeting_name) LIKE :track_filter' in source
    assert 'race_order = race_order[:limit]' in source


def test_ml_specific_analytics_use_ml_score_for_ranking():
    component_source = _function_source('api_component_analysis')
    assert 'Prediction.ml_score' in component_source
    assert 'ranking_score = (ml_score or 0) if use_ml' in component_source

    price_source = _function_source('api_price_analysis')
    assert 'Prediction.ml_score if use_ml else Prediction.score' in price_source
    assert "x['prediction'].ml_score" in price_source


def test_ml_performance_cutoff_is_centralized():
    source = Path('app.py').read_text()
    assert "ML_PERFORMANCE_MEETING_NAME_CUTOFF = '260625'" in source
    assert "def _ml_performance_meeting_name_sql" in source
    assert "cutoff_sql = _ml_performance_meeting_name_sql('m')" in _function_source('calculate_ml_performance_stats')
    assert "_filter_verified_ml_performance_meetings" in _function_source('_filter_ml_predictions')


def test_ml_performance_raw_sql_evaluates_cutoff_helper_before_sql_execution():
    function_sources = [
        _function_source('calculate_ml_performance_stats'),
        Path('ml_shadow_routes.py').read_text(),
    ]

    for function_source in function_sources:
        search_from = 0
        raw_sql_templates = []
        while True:
            sql_start = function_source.find("text(f\"\"\"", search_from)
            if sql_start == -1:
                break
            sql_end = function_source.index("\"\"\"", sql_start + len("text(f\"\"\""))
            raw_sql_templates.append(function_source[sql_start:sql_end])
            search_from = sql_end + 3

        assert raw_sql_templates
        for raw_sql_template in raw_sql_templates:
            assert "{_ml_performance_meeting_name_sql('m')}" not in raw_sql_template
            assert "_ml_performance_meeting_name_sql" not in raw_sql_template


def test_ml_data_page_passes_filters_to_overview_stats():
    source = _function_source('ml_data_analytics')
    assert 'calculate_ml_performance_stats(' in source
    assert 'track_filter=track_filter' in source
    assert 'date_from=date_from' in source
    assert 'date_to=date_to' in source
    assert 'limit_param=limit_param' in source
