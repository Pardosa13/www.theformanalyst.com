from pathlib import Path


def test_unsettled_meetings_query_does_not_require_ml_scores():
    source = Path("ml_shadow_routes.py").read_text()
    start = source.index("def _unsettled_puntingform_meetings_sql")
    end = source.index("def register_ml_shadow_routes", start)
    helper_source = source[start:end].lower()

    assert "left join results" in helper_source
    assert "r.id is null" in helper_source
    assert "m.puntingform_id is not null" in helper_source
    assert "predictions" not in helper_source
    assert "ml_score" not in helper_source


def test_bulk_ml_score_route_reuses_single_meeting_scorer_without_settlement():
    source = Path("ml_shadow_routes.py").read_text()
    start = source.index("def ml_shadow_score_visible")
    end = source.index("@app.route('/api/ml-shadow/results", start)
    route_source = source[start:end]

    assert "_visible_ml_shadow_meetings_query().all()" in route_source
    assert "_ml_scored_meeting_ids(db)" in route_source
    assert "_score_meeting_ml(db, meeting.id)" in route_source
    assert "_settle_meeting_results" not in route_source
    assert "Result(" not in route_source
    assert "results_created" not in route_source
    assert "Checked {checked} meetings. Generated ML scores for {generated} meetings. Skipped {skipped} already scored." in route_source


def test_ml_shadow_bulk_button_calls_score_visible_not_settle_all():
    source = Path("templates/ml_shadow.html").read_text()
    assert "onclick=\"scoreVisibleMeetings()\"" in source
    assert "fetch('/api/ml-shadow/score-visible', { method: 'POST' })" in source
    assert "location.reload()" in source

    start = source.index("async function scoreVisibleMeetings")
    end = source.index("async function loadMeeting", start)
    function_source = source[start:end]
    assert "/api/ml-shadow/settle-all" not in function_source
