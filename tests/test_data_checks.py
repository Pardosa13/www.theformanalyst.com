"""Daily data checks catch a horse saved with an old run as its last start."""
import os
from datetime import date, datetime

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as appmod
import data_checks
from models import db, User, Meeting, Race, Horse

FORM_FILE = (
    "race number,horse name,form meeting date,form position\n"
    "1,Good,08/10/26,4\n1,Good,07/11/25,2\n"
    "1,Bad,10/10/26,3\n1,Bad,01/01/25,1\n"
)


def _setup(n_good, n_bad):
    user = User(username='dc', email='dc@example.com', password_hash='x')
    db.session.add(user)
    db.session.flush()
    meeting = Meeting(user_id=user.id, meeting_name='261028_Test', date=date(2026, 10, 28),
                      uploaded_at=datetime(2026, 10, 28), csv_data=FORM_FILE)
    db.session.add(meeting)
    db.session.flush()
    race = Race(meeting_id=meeting.id, race_number=1)
    db.session.add(race)
    db.session.flush()
    for i in range(n_good):
        db.session.add(Horse(race_id=race.id, horse_name='Good', form='2x3173534',
                             csv_data={'horse last10': '2x3173534', 'form position': '4', 'form meeting date': '08/10/26'}))
    for i in range(n_bad):
        # Saved the OLD run: 1st on 01/01/25, but form says it ran 3rd last start.
        db.session.add(Horse(race_id=race.id, horse_name='Bad', form='13',
                             csv_data={'horse last10': '13', 'form position': '1', 'form meeting date': '01/01/25'}))
    db.session.commit()


def test_form_last_position_reads_the_right_end():
    assert data_checks.form_last_position('2x3173534') == 4
    assert data_checks.form_last_position('12x') == 2
    assert data_checks.form_last_position('310') == 10
    assert data_checks.form_last_position('') is None


def test_check_fails_and_alerts_once_when_old_runs_are_saved():
    sent = []
    with appmod.app.app_context():
        db.create_all()
        try:
            _setup(n_good=10, n_bad=20)
            res = data_checks.run_and_save(db.session, date(2026, 10, 28), alert=sent.append)
            assert not res['last_start_position']['ok']
            assert res['last_start_position']['failed'] == 20
            assert not res['last_start_date']['ok']
            assert len(sent) == 2
            # A second pass the same day does not alert again.
            data_checks.run_and_save(db.session, date(2026, 10, 28), alert=sent.append)
            assert len(sent) == 2
            fails = data_checks.latest_failures(db.session)
            assert {f['name'] for f in fails} == {'last_start_position', 'last_start_date'}
        finally:
            db.session.remove()
            db.drop_all()


def test_check_passes_on_correct_data():
    sent = []
    with appmod.app.app_context():
        db.create_all()
        try:
            _setup(n_good=30, n_bad=0)
            res = data_checks.run_and_save(db.session, date(2026, 10, 28), alert=sent.append)
            assert res['last_start_position']['ok'] and res['last_start_date']['ok']
            assert sent == [] and data_checks.latest_failures(db.session) == []
        finally:
            db.session.remove()
            db.drop_all()
