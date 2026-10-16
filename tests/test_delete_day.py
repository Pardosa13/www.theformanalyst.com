"""Admin can delete a whole raceday from the History page in one go."""
import uuid
from datetime import date

from sqlalchemy import text

import app as appmod
from models import db, User, Meeting, Race, Horse, Prediction

DAY = date(2031, 3, 7)
OTHER_DAY = date(2031, 3, 8)


def _user(admin):
    u = User(username=f'del-{uuid.uuid4().hex[:8]}', email=f'{uuid.uuid4().hex[:8]}@example.com', is_admin=admin)
    u.set_password('x')
    db.session.add(u)
    db.session.flush()
    return u


def _meeting(owner, day):
    m = Meeting(user_id=owner.id, meeting_name=f'{day:%y%m%d}_T{uuid.uuid4().hex[:6]}', track='T', date=day)
    db.session.add(m)
    db.session.flush()
    r = Race(meeting_id=m.id, race_number=1)
    db.session.add(r)
    db.session.flush()
    h = Horse(race_id=r.id, horse_name='Runner')
    db.session.add(h)
    db.session.flush()
    db.session.add(Prediction(horse_id=h.id, score=1.0))
    db.session.execute(text('INSERT INTO score_history (horse_id, race_id, changed_at, reason) '
                            "VALUES (:h, :r, CURRENT_TIMESTAMP, 'upload')"), {'h': h.id, 'r': r.id})
    return m, h


def _post(user_id, day):
    appmod.limiter.enabled = False
    try:
        with appmod.app.test_client() as c:
            with c.session_transaction() as sess:
                sess['_user_id'] = str(user_id)
            return c.post('/meetings/delete-day', data={'date': day.isoformat()})
    finally:
        appmod.limiter.enabled = True


def test_admin_deletes_every_meeting_on_the_day_and_nothing_else():
    with appmod.app.app_context():
        db.create_all()
        admin = _user(True)
        a, ha = _meeting(admin, DAY)
        b, hb = _meeting(admin, DAY)
        keep, hk = _meeting(admin, OTHER_DAY)
        db.session.commit()
        ids, horse_ids, admin_id, keep_id = [a.id, b.id], [ha.id, hb.id], admin.id, keep.id

    assert _post(admin_id, DAY).status_code == 302

    with appmod.app.app_context():
        assert Meeting.query.filter(Meeting.id.in_(ids)).count() == 0
        assert Horse.query.filter(Horse.id.in_(horse_ids)).count() == 0
        assert Prediction.query.filter(Prediction.horse_id.in_(horse_ids)).count() == 0
        left = db.session.execute(text('SELECT COUNT(*) FROM score_history WHERE horse_id IN (:a, :b)'),
                                  {'a': horse_ids[0], 'b': horse_ids[1]}).scalar()
        assert left == 0
        assert db.session.get(Meeting, keep_id) is not None


def test_non_admin_cannot_delete_a_day():
    with appmod.app.app_context():
        db.create_all()
        user = _user(False)
        m, _ = _meeting(user, DAY)
        db.session.commit()
        user_id, meeting_id = user.id, m.id

    _post(user_id, DAY)

    with appmod.app.app_context():
        assert db.session.get(Meeting, meeting_id) is not None
