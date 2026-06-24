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
