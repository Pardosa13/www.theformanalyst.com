"""Guards on the access and forgery checks added to app.py.

Each test drives the real Flask app through its test client against the local
SQLite database, so a route that loses its check fails here rather than in
production.
"""
import uuid

import pytest

import app as appmod
from models import db, User, Meeting, Race, Horse


@pytest.fixture()
def client():
    appmod.app.config['TESTING'] = True
    appmod.limiter.enabled = False
    with appmod.app.test_client() as c:
        yield c
    appmod.limiter.enabled = True


def _make_user(is_admin=False):
    with appmod.app.app_context():
        name = f"sec-{uuid.uuid4().hex[:10]}"
        user = User(username=name, email=f"{name}@example.com", is_admin=is_admin)
        user.set_password('pw-' + name)
        db.session.add(user)
        db.session.commit()
        return user.id


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True


def _make_meeting(owner_id):
    with appmod.app.app_context():
        meeting = Meeting(user_id=owner_id, meeting_name=f"Test {uuid.uuid4().hex[:6]}")
        db.session.add(meeting)
        db.session.flush()
        race = Race(meeting_id=meeting.id, race_number=1)
        db.session.add(race)
        db.session.flush()
        horse = Horse(race_id=race.id, horse_name='Test Runner')
        db.session.add(horse)
        db.session.commit()
        return meeting.id, horse.id


def test_database_export_is_admin_only(client):
    _login(client, _make_user())
    assert client.get('/export-all-data').status_code == 403


def test_model_download_is_admin_only(client):
    _login(client, _make_user())
    assert client.get('/form_analyst_best_random_forest.pkl').status_code == 403


def test_debug_routes_are_admin_only(client):
    _login(client, _make_user())
    meeting_id, horse_id = _make_meeting(_make_user())
    assert client.get(f'/api/debug/meeting/{meeting_id}/positions').status_code == 403
    assert client.get(f'/api/debug/horse/{horse_id}').status_code == 403


def test_cross_site_post_is_blocked(client):
    _login(client, _make_user())
    resp = client.post('/backtest/run-now', headers={'Origin': 'https://evil.example'})
    assert resp.status_code == 403
    resp = client.post('/backtest/run-now', headers={'Origin': 'null'})
    assert resp.status_code == 403


def test_same_site_post_passes_origin_check(client):
    # A non-admin is bounced by the route itself (302), not by the origin check.
    _login(client, _make_user())
    resp = client.post('/backtest/run-now', headers={'Origin': 'http://localhost'})
    assert resp.status_code == 302


def test_backtest_cannot_be_started_by_a_link(client):
    _login(client, _make_user(is_admin=True))
    assert client.get('/backtest/run-now').status_code == 405


def test_only_owner_or_admin_can_change_a_meeting(client):
    owner_id = _make_user()
    meeting_id, horse_id = _make_meeting(owner_id)

    _login(client, _make_user())
    assert client.post(f'/api/horse/{horse_id}/toggle-scratch').status_code == 403
    assert client.post(f'/api/meeting/{meeting_id}/update-bias', json={'pace_bias': 1}).status_code == 403
    assert client.post(f'/results/{meeting_id}/mark-scratched-and-complete').status_code == 403
    resp = client.post(f'/results/{meeting_id}/save', data={'race_number': 1})
    assert resp.status_code == 302 and '/history' in resp.headers['Location']

    with appmod.app.app_context():
        assert db.session.get(Horse, horse_id).is_scratched is not True


def test_login_attempts_are_rate_limited():
    appmod.app.config['TESTING'] = True
    with appmod.app.test_client() as c:
        statuses = [
            c.post('/login', data={'username': 'nobody', 'password': 'wrong'},
                   environ_base={'REMOTE_ADDR': '203.0.113.9'}).status_code
            for _ in range(11)
        ]
    assert statuses[:10] == [302] * 10
    assert statuses[10] == 429


def test_app_starts_without_secret_key_in_production():
    # A missing SECRET_KEY once crashed every worker on Railway. The app must
    # boot, and the derived key must be stable and never the old placeholder.
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != 'SECRET_KEY'}
    env['DATABASE_URL'] = 'sqlite:///:memory:'
    code = "import app; print(app.app.secret_key)"
    runs = [subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=120)
            for _ in range(2)]
    for r in runs:
        assert r.returncode == 0, r.stderr[-2000:]
    keys = [r.stdout.strip().splitlines()[-1] for r in runs]
    assert keys[0] == keys[1]
    assert keys[0] != 'your-secret-key-change-in-production'
