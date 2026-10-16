import os
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as appmod
from models import db, User, Meeting, Race, Horse, Prediction, Result

START = datetime(2026, 10, 17, 5, 0)


def _race(meeting, number, start=START):
    race = Race(meeting_id=meeting.id, race_number=number, start_time_utc=start)
    db.session.add(race)
    db.session.flush()
    return race


def _runner(race, name, prob, edge, position, sp, scored=None, captured=None):
    horse = Horse(race_id=race.id, horse_name=name)
    db.session.add(horse)
    db.session.flush()
    before = race.start_time_utc - timedelta(minutes=10)
    db.session.add(Prediction(
        horse_id=horse.id, score=50.0, ml_win_probability=prob,
        ml_scored_at=scored or before,
        value_edge_pct=edge, value_edge_captured_at=(captured or before) if edge is not None else None,
    ))
    db.session.add(Result(horse_id=horse.id, finish_position=position, sp=sp))


def test_top_pick_value_test_bets_only_the_top_pick_and_cuts_by_edge():
    with appmod.app.app_context():
        db.create_all()
        try:
            user = User(username='tp', email='tp@example.com', password_hash='x')
            db.session.add(user)
            db.session.flush()
            meeting = Meeting(user_id=user.id, meeting_name='Test', uploaded_at=START)
            db.session.add(meeting)
            db.session.flush()

            # Race 1: top pick has a 6pt edge and wins at $4. A longshot with a
            # bigger edge must NOT be bet: this is top pick only.
            r1 = _race(meeting, 1)
            _runner(r1, 'Top Winner', 0.40, 6.0, 1, 4.0)
            _runner(r1, 'Longshot', 0.10, 9.0, 2, 20.0)
            # Race 2: top pick has a 1pt edge and loses.
            r2 = _race(meeting, 2)
            _runner(r2, 'Top Loser', 0.35, 1.0, 3, 3.0)
            _runner(r2, 'Other', 0.20, -2.0, 1, 5.0)
            # Race 3: top pick had no live price: left out.
            r3 = _race(meeting, 3)
            _runner(r3, 'Unpriced', 0.50, None, 1, 2.0)
            # Race 4: top pick's edge was captured after the jump: left out.
            r4 = _race(meeting, 4)
            _runner(r4, 'Late', 0.50, 9.0, 1, 2.0, captured=START + timedelta(minutes=5))
            # Race 5: before the test start date: left out entirely.
            r5 = _race(meeting, 5, start=datetime(2026, 10, 1))
            _runner(r5, 'Old', 0.50, 9.0, 1, 2.0)
            db.session.commit()

            result = appmod.calculate_top_pick_value_test()
            rows = {row['min_edge_pct']: row for row in result['rows']}
            assert result['races_with_top_pick'] == 4
            assert result['races_priced'] == 2
            assert rows[0.0]['bets'] == 2
            assert rows[0.0]['profit'] == 20.0  # +30 win, -10 loss
            assert rows[5.0]['bets'] == 1
            assert rows[5.0]['wins'] == 1
            assert rows[5.0]['roi'] == 300.0
            assert rows[8.0]['bets'] == 0
        finally:
            db.session.remove()
            db.drop_all()


def test_ml_data_page_shows_the_top_pick_value_test():
    source = Path('app.py').read_text()
    start = source.index('def ml_data_analytics(')
    route = source[start:source.index('\n@app.route(', start)]
    assert 'calculate_top_pick_value_test(' in route
    assert 'top_pick_value_test=top_pick_value_test' in route
    template = Path('templates/ml_data.html').read_text()
    assert 'Live Test: Top Pick When It Beats the Price' in template


def test_value_test_rows_carry_a_chart_history():
    with appmod.app.app_context():
        db.create_all()
        try:
            user = User(username='vh', email='vh@example.com', password_hash='x')
            db.session.add(user)
            db.session.flush()
            meeting = Meeting(user_id=user.id, meeting_name='Hist', uploaded_at=START)
            db.session.add(meeting)
            db.session.flush()
            later = _race(meeting, 2, start=START + timedelta(hours=1))
            _runner(later, 'Later', 0.5, 6.0, 2, 3.0)
            earlier = _race(meeting, 1)
            _runner(earlier, 'Earlier', 0.5, 6.0, 1, 4.0)
            db.session.commit()

            rows = {r['min_edge_pct']: r for r in appmod.calculate_top_pick_value_test()['rows']}
            history = rows[5.0]['history']
            assert rows[5.0]['label'] == '+5 pts'
            assert rows[0.0]['label'] == 'Any edge (0+)'
            # Oldest first: the race-1 win, then the race-2 loss.
            assert [h['race'] for h in history] == [1, 2]
            assert [h['cumulative'] for h in history] == [30.0, 20.0]
        finally:
            db.session.remove()
            db.drop_all()
    template = Path('templates/ml_data.html').read_text()
    assert "renderSignalPnl('valueTestPnl', 'valueTestHistory', 'valueTestPnlChart')" in template
    assert 'id="valueTestPnlChart"' in template
