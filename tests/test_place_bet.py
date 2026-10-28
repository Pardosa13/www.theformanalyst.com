"""Place Bet route: ran 2nd last start + 2-3kg under the field, settled on the place."""
import os
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as appmod
import place_bet
from models import db, User, Meeting, Race, Horse, Prediction, Result

LIGHT = "+ 10.0: Weight 54.0kg below race avg"     # analyzer note: 2-3kg under the field
HEAVY = "- 10.0: Weight 60.0kg above race avg"


def _runner(race, name, last_pos, notes, fin=None, score=50.0):
    horse = Horse(race_id=race.id, horse_name=name, csv_data={'form position': str(last_pos)})
    db.session.add(horse)
    db.session.flush()
    db.session.add(Prediction(horse_id=horse.id, score=score, notes=notes))
    if fin is not None:
        db.session.add(Result(horse_id=horse.id, finish_position=fin, sp=4.0))
    return horse


def _setup():
    user = User(username='pb', email='pb@example.com', password_hash='x')
    db.session.add(user)
    db.session.flush()
    meeting = Meeting(user_id=user.id, meeting_name='Test', uploaded_at=datetime(2026, 10, 28),
                      date=datetime(2026, 10, 28).date())
    db.session.add(meeting)
    db.session.flush()
    race = Race(meeting_id=meeting.id, race_number=1, race_class='BM64',
                start_time_utc=datetime(2026, 10, 28, 5, 0))
    db.session.add(race)
    db.session.flush()
    return race


def test_rule_needs_both_second_last_start_and_light_weight():
    with appmod.app.app_context():
        db.create_all()
        try:
            race = _setup()
            yes = _runner(race, 'Yes', 2, LIGHT)
            won_last = _runner(race, 'WonLast', 1, LIGHT)
            heavy = _runner(race, 'Heavy', 2, HEAVY)
            db.session.commit()
            assert place_bet.qualifies(yes)
            assert not place_bet.qualifies(won_last)
            assert not place_bet.qualifies(heavy)
            # It is a Best Bets route on its own: no 3-way agreement needed.
            routes, _badges, _agree = appmod.best_bet_routes_for_horse(
                yes, race, appmod.top_signal_horse_ids(race.horses))
            assert place_bet.ROUTE in routes
        finally:
            db.session.remove()
            db.drop_all()


def test_daily_run_records_the_place_route():
    with appmod.app.app_context():
        db.create_all()
        try:
            race = _setup()
            yes = _runner(race, 'Yes', 2, LIGHT)
            _runner(race, 'No', 5, LIGHT, score=90)
            db.session.commit()
            assert appmod.record_best_bet_routes([race]) == 1
            assert place_bet.ROUTE in yes.prediction.best_bet_routes
        finally:
            db.session.remove()
            db.drop_all()


def test_performance_settles_on_the_place_not_the_win():
    with appmod.app.app_context():
        db.create_all()
        try:
            race = _setup()
            # 8 starters, so 3 places pay. The rule's horse runs 3rd: a placed bet.
            pick = _runner(race, 'Pick', 2, LIGHT, fin=3)
            for i in range(7):
                _runner(race, f'O{i}', 6, HEAVY, fin=[1, 2, 4, 5, 6, 7, 8][i])
            db.session.commit()
            rec = place_bet.performance(db.session)
            history = rec['rows'][0]
            assert history['bets'] == 1 and history['placed'] == 1
            # No Betfair place price stored yet: a place % but no profit line.
            assert history['priced_bets'] == 0 and history['roi'] is None
            assert pick is not None
        finally:
            db.session.remove()
            db.drop_all()


def test_places_paid_follow_betfair_rules():
    assert place_bet.places_paid(12) == 3
    assert place_bet.places_paid(8) == 3
    assert place_bet.places_paid(7) == 2
    assert place_bet.places_paid(5) == 2
    assert place_bet.places_paid(4) == 1


def test_win_track_record_leaves_place_bets_out():
    import betfair_check
    assert "place_2nd_light" in betfair_check._BEST_BET_BASE
