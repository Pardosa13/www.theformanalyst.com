"""Guards on the access and forgery checks added to app.py.

Each test drives the real Flask app through its test client against the local
SQLite database, so a route that loses its check fails here rather than in
production.
"""
from conftest import session_id_for
import uuid

import pytest

import app as appmod
from models import db, User, Meeting, Race, Horse


@pytest.fixture(autouse=True)
def _tables_exist():
    # Other test modules point the app at a shared in-memory database and
    # drop its tables when they finish.
    # In a full run that left these tests with no users table. Create the
    # tables if they are missing; create_all never touches existing ones.
    with appmod.app.app_context():
        db.create_all()
    yield


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
        sess['_user_id'] = session_id_for(user_id)
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


# ----- Added with the Nov 2026 security pass -----

import io
import logging


def _limited_client():
    """A test client with rate limits switched on."""
    appmod.app.config['TESTING'] = True
    appmod.limiter.enabled = True
    return appmod.app.test_client()


def test_security_headers_on_every_page(client):
    resp = client.get('/login')
    csp = resp.headers['Content-Security-Policy']
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "form-action 'self'" in csp
    assert resp.headers['X-Content-Type-Options'] == 'nosniff'
    assert resp.headers['X-Frame-Options'] == 'DENY'
    assert resp.headers['Referrer-Policy'] == 'strict-origin-when-cross-origin'
    assert 'camera=()' in resp.headers['Permissions-Policy']


def test_hsts_only_over_https(client):
    assert 'Strict-Transport-Security' not in client.get('/login').headers
    resp = client.get('/login', headers={'X-Forwarded-Proto': 'https'})
    assert resp.headers['Strict-Transport-Security'].startswith('max-age=31536000')


def test_plain_http_is_sent_to_https_in_production(client, monkeypatch):
    monkeypatch.setenv('RAILWAY_ENVIRONMENT', 'production')
    resp = client.get('/login?x=1', headers={'X-Forwarded-Proto': 'http'})
    assert resp.status_code == 308
    assert resp.headers['Location'].startswith('https://')
    assert resp.headers['Location'].endswith('/login?x=1')
    # Health checks and internal calls carry no forwarded header: no redirect.
    assert client.get('/login').status_code == 200


def test_no_https_redirect_off_railway(client, monkeypatch):
    monkeypatch.delenv('RAILWAY_ENVIRONMENT', raising=False)
    monkeypatch.delenv('TRUST_PROXY', raising=False)
    assert client.get('/login', headers={'X-Forwarded-Proto': 'http'}).status_code == 200


def test_logged_out_api_call_gets_401_and_page_goes_to_login(client):
    resp = client.get('/api/meetings/today')
    assert resp.status_code == 401
    assert resp.get_json()['success'] is False
    # The AFL API used to turn the login redirect into a 500.
    assert client.get('/api/afl/fixtures').status_code == 401
    assert client.post('/api/afl/sync/fixtures').status_code == 401
    resp = client.get('/dashboard')
    assert resp.status_code == 302 and '/login' in resp.headers['Location']


def test_private_pages_are_not_cached(client):
    _login(client, _make_user())
    resp = client.get('/history')
    assert resp.headers['Cache-Control'] == 'no-store'


def test_password_change_logs_out_other_browsers(client):
    user_id = _make_user()
    _login(client, user_id)
    assert client.get('/history').status_code == 200
    with appmod.app.app_context():
        user = db.session.get(User, user_id)
        user.set_password('a-brand-new-password')
        db.session.commit()
    resp = client.get('/history')
    assert resp.status_code == 302 and '/login' in resp.headers['Location']


def test_deactivated_account_is_logged_out(client):
    user_id = _make_user()
    _login(client, user_id)
    with appmod.app.app_context():
        db.session.get(User, user_id).is_active = False
        db.session.commit()
    assert client.get('/history').status_code == 302


def test_old_bare_id_cookie_is_refused(client):
    user_id = _make_user()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True
    assert client.get('/history').status_code == 302


def test_changing_own_password_keeps_you_logged_in(client):
    user_id = _make_user(is_admin=True)
    with appmod.app.app_context():
        name = db.session.get(User, user_id).username
    _login(client, user_id)
    resp = client.post('/admin', data={
        'action': 'change_my_password',
        'current_password': 'pw-' + name,
        'new_password': 'longer-password-1',
        'confirm_password': 'longer-password-1',
    })
    assert resp.status_code in (200, 302)
    assert client.get('/history').status_code == 200
    with appmod.app.app_context():
        assert db.session.get(User, user_id).check_password('longer-password-1')


def test_short_passwords_are_refused(client):
    _login(client, _make_user(is_admin=True))
    name = f"new-{uuid.uuid4().hex[:8]}"
    client.post('/admin', data={'action': 'create_user', 'username': name,
                                'email': f'{name}@example.com', 'password': 'short9'})
    with appmod.app.app_context():
        assert User.query.filter_by(username=name).first() is None


def test_bad_username_and_email_are_refused(client):
    _login(client, _make_user(is_admin=True))
    client.post('/admin', data={'action': 'create_user', 'username': '<script>x',
                                'email': 'x@example.com', 'password': 'long-enough-pw'})
    client.post('/admin', data={'action': 'create_user', 'username': 'okname123',
                                'email': 'not-an-email', 'password': 'long-enough-pw'})
    with appmod.app.app_context():
        assert User.query.filter_by(username='<script>x').first() is None
        assert User.query.filter_by(username='okname123').first() is None


def test_failed_login_is_logged(client, caplog):
    with caplog.at_level(logging.WARNING, logger='security'):
        client.post('/login', data={'username': 'ghost', 'password': 'nope'})
    assert any('SECURITY|login-failed' in r.getMessage() and 'username=ghost' in r.getMessage()
               for r in caplog.records)


def test_one_account_cannot_be_guessed_from_many_addresses():
    target = f"victim-{uuid.uuid4().hex[:8]}"
    c = _limited_client()
    try:
        statuses = [
            c.post('/login', data={'username': target, 'password': 'wrong'},
                   environ_base={'REMOTE_ADDR': f'198.51.100.{i}'}).status_code
            for i in range(21)
        ]
    finally:
        appmod.limiter.enabled = False
    assert statuses[:20] == [302] * 20
    assert statuses[20] == 429


def test_costly_buttons_are_rate_limited(monkeypatch):
    monkeypatch.delenv('ODDS_API_KEY', raising=False)
    c = _limited_client()
    _login(c, _make_user())
    try:
        statuses = [c.post('/api/mma/sync/odds').status_code for _ in range(31)]
    finally:
        appmod.limiter.enabled = False
    assert 429 not in statuses[:30]
    assert statuses[30] == 429


def test_leaked_default_admin_password_is_closed():
    with appmod.app.app_context():
        name = f"oldadmin-{uuid.uuid4().hex[:8]}"
        user = User(username=name, email=f"{name}@example.com", is_admin=True)
        user.set_password('changeme123')
        db.session.add(user)
        db.session.commit()
        uid = user.id

        assert appmod.lock_leaked_default_admins(None) >= 1
        assert db.session.get(User, uid).is_active is False

        assert appmod.lock_leaked_default_admins('a-strong-replacement') >= 1
        user = db.session.get(User, uid)
        assert user.is_active is True
        assert user.check_password('a-strong-replacement')
        assert appmod.lock_leaked_default_admins('a-strong-replacement') == 0


def test_script_json_cannot_break_out_of_a_script_block():
    out = appmod._script_json('{"name": "</script><script>alert(1)</script>"}')
    assert '</script>' not in out and '<' not in out
    import json
    assert json.loads(out)['name'] == '</script><script>alert(1)</script>'
    assert appmod._script_json(None) == 'null'


def test_upload_must_be_a_text_csv(client):
    _login(client, _make_user())
    resp = client.post('/analyze', data={'csv_file': (io.BytesIO(b'\x89PNG\r\n\x1a\n\x00\x00'), 'x.csv')},
                       content_type='multipart/form-data')
    assert resp.status_code == 302 and '/dashboard' in resp.headers['Location']
    resp = client.post('/analyze', data={'csv_file': (io.BytesIO(b'a,b\n1,2'), 'x.exe')},
                       content_type='multipart/form-data')
    assert resp.status_code == 302 and '/dashboard' in resp.headers['Location']


def test_oversized_upload_is_refused(client):
    _login(client, _make_user())
    big = io.BytesIO(b'a' * (17 * 1024 * 1024))
    resp = client.post('/analyze', data={'csv_file': (big, 'big.csv')}, content_type='multipart/form-data')
    assert resp.status_code == 413


def test_bet_tracker_refuses_nan_and_infinity(client):
    _login(client, _make_user(is_admin=True))
    for bad in ('nan', 'inf', '-inf', 'Infinity'):
        resp = client.post('/api/bet-tracker/bets', json={'stake': bad, 'odds': 2, 'selection': 'X'})
        assert resp.status_code == 400, bad
        resp = client.post('/api/bet-tracker/bets', json={'stake': 10, 'odds': bad, 'selection': 'X'})
        assert resp.status_code == 400, bad


def test_ml_export_refusal_does_not_crash(client):
    _login(client, _make_user())
    resp = client.get('/data/export')
    assert resp.status_code == 302 and '/history' in resp.headers['Location']
