import os
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as appmod
from models import db, User, Meeting, Race, Horse, Prediction, Result


def _runner(race, name, mask, routes, position, sp):
    horse = Horse(race_id=race.id, horse_name=name)
    db.session.add(horse)
    db.session.flush()
    db.session.add(Prediction(horse_id=horse.id, score=90.0, ladbrokes_signal_mask=mask, best_bet_routes=routes))
    db.session.add(Result(horse_id=horse.id, finish_position=position, sp=sp))


def test_proven_edge_best_bets_get_their_own_pnl_cohort():
    with appmod.app.app_context():
        db.create_all()
        try:
            user = User(username='pe', email='pe@example.com', password_hash='x')
            db.session.add(user)
            db.session.flush()
            meeting = Meeting(user_id=user.id, meeting_name='Test', uploaded_at=datetime(2026, 1, 1))
            db.session.add(meeting)
            db.session.flush()
            race = Race(meeting_id=meeting.id, race_number=1)
            db.session.add(race)
            db.session.flush()
            # Proven Edge only (no Ladbrokes badge): wins at $4.
            _runner(race, 'Edge Winner', 0, 'proven_edge', 1, 4.0)
            # Proven Edge plus another route: loses.
            _runner(race, 'Edge Loser', 1, 'favourite,proven_edge', 3, 2.0)
            # Not Proven Edge.
            _runner(race, 'Maiden Only', 0, 'maiden', 1, 3.0)
            db.session.commit()

            performance = {row['key']: row for row in appmod.calculate_ladbrokes_signal_performance()}
            edge = performance['proven_edge']
            assert edge['bets'] == 2
            assert edge['wins'] == 1
            assert edge['profit'] == 20.0  # +30 win, -10 loss
            assert len(edge['history']) == 2
            assert edge['history'][-1]['cumulative'] == 20.0
            # Existing Ladbrokes cohorts are unaffected.
            assert performance['sweet_spot']['bets'] == 1
        finally:
            db.session.remove()
            db.drop_all()
