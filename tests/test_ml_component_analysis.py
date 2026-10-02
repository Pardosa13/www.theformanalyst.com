"""The ML Data page's Component Analysis must rank runners by ML score.

It once silently showed the analyzer's numbers after the analysis moved to
the nightly backtest. These tests pin both halves: backtest.py builds an
ML-ranked copy, and the route hands that copy to source=ml requests.
"""
import json
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pandas as pd
from sqlalchemy import text

import backtest


def _two_races():
    # In each race the analyzer prefers the loser; the ML model prefers the winner.
    rows = []
    for race_id in (1, 2):
        base = race_id * 10
        rows += [
            dict(horse_id=base + 1, race_id=race_id, horse_name=f'Analyzer Pick {race_id}',
                 analyzer_score=90.0, analyzer_notes='', finish_position=4, sp=3.0,
                 csv_data={}, meeting_name='Test', track_condition='Good'),
            dict(horse_id=base + 2, race_id=race_id, horse_name=f'ML Pick {race_id}',
                 analyzer_score=50.0, analyzer_notes='', finish_position=1, sp=5.0,
                 csv_data={}, meeting_name='Test', track_condition='Good'),
        ]
    ml_scores = {11: 10.0, 12: 80.0, 21: 15.0, 22: 70.0}
    return pd.DataFrame(rows), ml_scores


def test_ml_version_ranks_by_ml_score():
    df, ml_scores = _two_races()
    analyzer = backtest.run_full_component_analysis(df)
    ml = backtest.run_full_component_analysis(df, ml_scores=ml_scores)
    assert analyzer['winner_gap_meta']['top_pick_wins'] == 0
    assert ml['winner_gap_meta']['top_pick_wins'] == 2


def test_ml_version_only_uses_ml_scored_runners():
    df, ml_scores = _two_races()
    ml_scores.pop(11)
    ml_scores.pop(12)  # race 1 has no ML scores at all
    ml = backtest.run_full_component_analysis(df, ml_scores=ml_scores)
    assert ml['winner_gap_meta']['total_races'] <= 1


def test_route_serves_ml_copy_for_source_ml():
    import app as appmod
    from models import db, User

    with appmod.app.app_context():
        db.session.execute(text(
            "CREATE TABLE IF NOT EXISTS component_analysis_cache ("
            "id INTEGER PRIMARY KEY, run_id INTEGER, payload TEXT, computed_at TIMESTAMP)"
        ))
        db.session.execute(text("DELETE FROM component_analysis_cache"))
        payload = {'components': [{'name': 'analyzer'}],
                   'ml_source': {'components': [{'name': 'ml'}]}}
        db.session.execute(
            # computed_at left NULL: SQLite would hand back a string, not the
            # datetime Postgres returns.
            text("INSERT INTO component_analysis_cache (run_id, payload, computed_at) VALUES (1, :p, NULL)"),
            {'p': json.dumps(payload)},
        )
        admin = User(username=f'ca-admin-{os.getpid()}-{id(payload)}', email=f'ca{id(payload)}@example.com', is_admin=True)
        admin.set_password('x')
        db.session.add(admin)
        db.session.commit()
        admin_id = admin.id

    appmod.limiter.enabled = False
    try:
        with appmod.app.test_client() as c:
            with c.session_transaction() as sess:
                sess['_user_id'] = str(admin_id)
            ml = c.get('/api/data/component-analysis?source=ml').get_json()
            analyzer = c.get('/api/data/component-analysis').get_json()
    finally:
        appmod.limiter.enabled = True

    assert ml['components'][0]['name'] == 'ml'
    assert analyzer['components'][0]['name'] == 'analyzer'
    assert 'ml_source' not in analyzer
