import os
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as appmod
from models import db, User, Meeting, Race, Horse, Prediction


def _runner(race, name, analyzer, pfai, ml, notes='x', csv_data=None):
    horse = Horse(race_id=race.id, horse_name=name, csv_data={'pfaiScore': pfai, **(csv_data or {})})
    db.session.add(horse)
    db.session.flush()
    db.session.add(Prediction(horse_id=horse.id, score=analyzer, ml_score=ml, notes=notes))
    return horse


def test_nothing_is_recorded_without_the_live_market():
    """28 Oct 2026: Maiden, Proven Edge, 3-way agreement and Value Top Pick were
    removed (their pre-race picks lost). 15 Nov 2026: the place rules came off
    Best Bets too. The rules now need the live market (best_bet_rules.py), so
    the daily run, which has no prices, records nothing."""
    with appmod.app.app_context():
        db.create_all()
        try:
            user = User(username='rr', email='rr@example.com', password_hash='x')
            db.session.add(user)
            db.session.flush()
            meeting = Meeting(user_id=user.id, meeting_name='Test', uploaded_at=datetime(2026, 10, 28))
            db.session.add(meeting)
            db.session.flush()
            # Maiden, all three agree on A, with a hot-trainer (old Proven Edge) note and a big value edge.
            r1 = Race(meeting_id=meeting.id, race_number=1, race_class='Maiden Plate')
            r2 = Race(meeting_id=meeting.id, race_number=2, race_class='BM64')
            db.session.add_all([r1, r2])
            db.session.flush()
            a = _runner(r1, 'A', 90, 80, 100, notes='+20.0 : Trainer hot form (25% L100)')
            a.prediction.value_edge_pct = 15.0
            _runner(r1, 'B', 80, 70, 50)
            # A place-rule horse that is nobody's top pick.
            c = _runner(r2, 'C', 50, 40, 10, notes='+4.0 : Ran places: 3rd 2nd\n+ 0.0 : No wins at this track+distance',
                        csv_data={'horse last10': '3x52'})
            _runner(r2, 'D', 90, 80, 100)
            db.session.commit()

            recorded = appmod.record_best_bet_routes([r1, r2])
            db.session.commit()
            assert recorded == 0
            assert a.prediction.best_bet_routes is None
            assert c.prediction.best_bet_routes is None
            assert c.prediction.best_bet_qualified_at is None

            # Routes only grow, never duplicate, when the page records them.
            appmod.merge_best_bet_tracking(c.prediction, ['agree_ml_fav_quiet'], {})
            appmod.merge_best_bet_tracking(c.prediction, ['agree_ml_fav_quiet', 'place_32_tdnowin'], {})
            assert c.prediction.best_bet_routes == 'agree_ml_fav_quiet,place_32_tdnowin'
            assert c.prediction.best_bet_qualified_at is not None

            for gone in ('RETIRED_BEST_BET_ROUTES', 'PROVEN_EDGE_COMPONENTS', 'MAIDEN_AGREEMENT_BADGE',
                         'VALUE_TOP_PICK_MIN_EDGE_PCT', 'evaluate_component_edge_badges'):
                assert not hasattr(appmod, gone)
        finally:
            db.session.remove()
            db.drop_all()


def test_best_bets_page_picks_with_the_live_market_rules():
    source = Path('app.py').read_text()
    page = source[source.index('def best_bets('):]
    page = page[:page.index('\n@app.route(', 1)]
    assert 'best_bet_rules.rules_for(' in page
    assert 'place_bet.matching_rules(' not in page
    rule = source[source.index('def best_bet_routes_for_horse('):]
    rule = rule[:rule.index('\n\n\n', 1)]
    assert 'return [], {}, False' in rule
    daily = Path('daily_run.py').read_text()
    assert 'record_best_bet_routes(races)' in daily
    run_pass = daily[daily.index('def run_pass('):]
    assert 'record_routes(db, races_to_run)' in run_pass
    assert 'record_routes(db, open_races(meeting, race_has_started))' in run_pass
