import os
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as appmod
from models import db, User, Meeting, Race, Horse, Prediction


def _runner(race, name, analyzer, pfai, ml, notes='x'):
    horse = Horse(race_id=race.id, horse_name=name, csv_data={'pfaiScore': pfai})
    db.session.add(horse)
    db.session.flush()
    db.session.add(Prediction(horse_id=horse.id, score=analyzer, ml_score=ml, notes=notes))
    return horse


def test_daily_run_records_proven_edge_but_not_retired_maiden_route(monkeypatch):
    # Proven Edge comes from the notes; fake the parser so the test does not
    # depend on the exact analyzer note wording.
    def fake_parse(notes):
        if notes == 'hot trainer':
            return {'k': {'key': 'k', 'name': 'Trainer - Hot Form (L100 22%+ SR)'}}
        return {}
    monkeypatch.setattr(appmod, 'parse_notes_component_matches', fake_parse)

    with appmod.app.app_context():
        db.create_all()
        try:
            user = User(username='rr', email='rr@example.com', password_hash='x')
            db.session.add(user)
            db.session.flush()
            meeting = Meeting(user_id=user.id, meeting_name='Test', uploaded_at=datetime(2026, 10, 17))
            db.session.add(meeting)
            db.session.flush()

            # Maiden, all three agree on A.
            r1 = Race(meeting_id=meeting.id, race_number=1, race_class='Maiden Plate')
            # Not a maiden, all three agree on C, which has a Proven Edge factor.
            r2 = Race(meeting_id=meeting.id, race_number=2, race_class='BM64')
            # Not a maiden, all three agree on E, no Proven Edge: no route.
            r3 = Race(meeting_id=meeting.id, race_number=3, race_class='BM64')
            # Maiden, but ML disagrees: no route.
            r4 = Race(meeting_id=meeting.id, race_number=4, race_class='Maiden')
            db.session.add_all([r1, r2, r3, r4])
            db.session.flush()
            a = _runner(r1, 'A', 90, 80, 100); _runner(r1, 'B', 80, 70, 50)
            c = _runner(r2, 'C', 90, 80, 100, notes='hot trainer'); _runner(r2, 'D', 80, 70, 50)
            e = _runner(r3, 'E', 90, 80, 100); _runner(r3, 'F', 80, 70, 50)
            g = _runner(r4, 'G', 90, 80, 10); _runner(r4, 'H', 80, 70, 50)
            db.session.commit()

            recorded = appmod.record_best_bet_routes([r1, r2, r3, r4])
            db.session.commit()

            # Maiden is a retired route: A no longer qualifies.
            assert 'maiden' in appmod.RETIRED_BEST_BET_ROUTES
            assert recorded == 1
            assert a.prediction.best_bet_routes is None
            assert c.prediction.best_bet_routes == 'proven_edge'
            assert e.prediction.best_bet_routes is None
            assert g.prediction.best_bet_routes is None
            assert c.prediction.best_bet_qualified_at is not None

            # Recording again is safe: routes only grow, never duplicate.
            appmod.record_best_bet_routes([r2])
            assert c.prediction.best_bet_routes == 'proven_edge'

            # And the ML Data chart now counts it without anyone opening Best Bets.
            assert any(cohort == 'proven_edge' for cohort, _l, _r in appmod.BEST_BET_ROUTE_COHORTS)
        finally:
            db.session.remove()
            db.drop_all()


def test_best_bets_page_and_daily_run_share_one_rule():
    source = Path('app.py').read_text()
    page = source[source.index('def best_bets('):]
    page = page[:page.index('\n@app.route(', 1)]
    assert 'best_bet_routes_for_horse(' in page
    daily = Path('daily_run.py').read_text()
    assert 'record_best_bet_routes(races)' in daily
    run_pass = daily[daily.index('def run_pass('):]
    assert 'record_routes(db, races_to_run)' in run_pass
    assert 'record_routes(db, open_races(meeting, race_has_started))' in run_pass


def test_value_top_pick_is_retired():
    assert 'value_top_pick' in appmod.RETIRED_BEST_BET_ROUTES


def test_value_top_pick_route_needs_ml_top_and_edge(monkeypatch):
    # The rule itself, with the route switched back on.
    monkeypatch.setattr(appmod, 'RETIRED_BEST_BET_ROUTES', frozenset())
    monkeypatch.setattr(appmod, 'parse_notes_component_matches', lambda notes: {})
    with appmod.app.app_context():
        db.create_all()
        try:
            user = User(username='vt', email='vt@example.com', password_hash='x')
            db.session.add(user)
            db.session.flush()
            meeting = Meeting(user_id=user.id, meeting_name='Test', uploaded_at=datetime(2026, 10, 17))
            db.session.add(meeting)
            db.session.flush()
            race = Race(meeting_id=meeting.id, race_number=1, race_class='BM64')
            db.session.add(race)
            db.session.flush()
            # ML top pick J (not the analyzer's or PFAI's top) with a 4pt edge.
            j = _runner(race, 'J', 70, 60, 100)
            k = _runner(race, 'K', 90, 80, 50)
            j.prediction.value_edge_pct = 4.0
            k.prediction.value_edge_pct = 12.0  # bigger edge, but not the ML top pick
            db.session.commit()

            appmod.record_best_bet_routes([race])
            assert j.prediction.best_bet_routes == 'value_top_pick'
            assert k.prediction.best_bet_routes is None

            # Below the bar: no route.
            race2 = Race(meeting_id=meeting.id, race_number=2, race_class='BM64')
            db.session.add(race2)
            db.session.flush()
            m = _runner(race2, 'M', 70, 60, 100)
            _runner(race2, 'N', 90, 80, 50)
            m.prediction.value_edge_pct = appmod.VALUE_TOP_PICK_MIN_EDGE_PCT - 0.1
            db.session.commit()
            appmod.record_best_bet_routes([race2])
            assert m.prediction.best_bet_routes is None
        finally:
            db.session.remove()
            db.drop_all()
