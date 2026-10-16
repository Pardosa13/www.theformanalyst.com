"""Daily automation: reading PuntingForm's track rating and rail, and the safety rules."""
from pathlib import Path

import pytest

import daily_run as dr


@pytest.mark.parametrize('text_value, number, surface, expected', [
    ('Good 4', None, None, 'good'),
    (None, 3, None, 'good'),
    ('Soft 5', 5, None, 'soft'),
    ('Soft 7', None, None, 'soft'),
    ('Heavy 8', 8, None, 'heavy'),
    ('Firm 2', None, None, 'firm'),
    ('Synthetic', None, None, 'synthetic'),
    ('Good 4', 4, 'Polytrack', 'synthetic'),
    ('', None, None, None),
])
def test_rating_word(text_value, number, surface, expected):
    assert dr.rating_word(text_value, number, surface) == expected


@pytest.mark.parametrize('value, expected', [
    ('True', 0), ('true position entire circuit', 0), ('+3m', 3), ('Out 6m entire circuit', 6),
    (4, 4), ('', None), (None, None), ('n/a', None),
])
def test_rail_metres(value, expected):
    assert dr.rail_metres(value) == expected


def test_resolve_uses_conditions_when_complete(monkeypatch):
    monkeypatch.setattr(dr, 'expected_from_form', lambda *a: pytest.fail('should not need the fallback'))
    assert dr.resolve_conditions({'trackCondition': 'Soft 6', 'rail': '+2m'}, 'k', 1) == ('soft', 2, 'conditions')


def test_resolve_falls_back_to_expected_then_default(monkeypatch):
    monkeypatch.setattr(dr, 'expected_from_form', lambda *a: ('good', 3))
    assert dr.resolve_conditions(None, 'k', 1) == ('good', 3, 'expected')
    monkeypatch.setattr(dr, 'expected_from_form', lambda *a: (None, None))
    assert dr.resolve_conditions({'trackCondition': 'Heavy 9'}, 'k', 1) == ('heavy', 0, 'default')


def test_conditions_lookup_by_id_then_track():
    item = {'meetingId': 77, 'track': 'Flemington'}
    table = {'77': item, 'track:flemington': item}
    assert dr.conditions_for(table, 77, 'x') is item
    assert dr.conditions_for(table, 1, 'FLEMINGTON ') is item
    assert dr.conditions_for(table, 1, 'Randwick') is None


def test_abandoned_flag_reads_pf_spelling():
    assert dr.is_abandoned({'abandonded': True}) and dr.is_abandoned({'abandoned': 'true'})
    assert not dr.is_abandoned({'abandonded': False}) and not dr.is_abandoned(None)


def test_scratch_signature_changes_only_when_scratchings_change():
    v1 = lambda data, track: (set(data.get(track, [])), [], True)
    v2 = lambda items, track: (set(), [])
    a = dr.scratch_signature({'Scone': [(1, 3)]}, [], 'Scone', v1, v2)
    assert a == dr.scratch_signature({'Scone': [(1, 3)]}, [], 'Scone', v1, v2)
    assert a != dr.scratch_signature({'Scone': [(1, 3), (2, 5)]}, [], 'Scone', v1, v2)


def test_alert_without_chat_id_only_logs(monkeypatch):
    monkeypatch.delenv('TELEGRAM_ADMIN_CHAT_ID', raising=False)
    assert dr.alert('test') is False


def test_import_button_and_automation_share_one_import_function():
    source = Path('app.py').read_text()
    route = source[source.index('def api_import_meeting'):source.index('def import_meeting_from_pf')]
    assert 'import_meeting_from_pf(' in route
    core = source[source.index('def import_meeting_from_pf'):source.index('@app.route("/import-from-api")')]
    for web_only in ('request.', 'jsonify(', 'current_user'):
        assert web_only not in core


def test_rescore_uses_rating_of_races_still_to_run():
    source = Path('app.py').read_text()
    impl = source[source.index('def _update_scratchings_impl'):]
    impl = impl[:impl.index('\ndef ', 10)]
    assert 'all_races[0].track_condition if all_races else \'good\'' not in impl
    assert 'open_races' in impl


class _Resp:
    def __init__(self, ok, status, payload):
        self.ok, self.status_code, self._payload, self.text = ok, status, payload, str(payload)

    def json(self):
        return self._payload


def test_alert_logs_telegram_refusal_without_leaking_token(monkeypatch, caplog):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '123:SECRET')
    monkeypatch.setenv('TELEGRAM_ADMIN_CHAT_ID', '42')
    monkeypatch.setattr(dr.requests, 'post', lambda *a, **k: _Resp(False, 401, {'description': 'Unauthorized'}))
    with caplog.at_level('INFO'):
        assert dr.alert('hello') is False
    assert 'Unauthorized' in caplog.text and 'SECRET' not in caplog.text


def test_alert_reports_unresolved_reference(monkeypatch, caplog):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '${{www.theformanalyst.com.TELEGRAM_BOT_TOKEN}}')
    monkeypatch.setenv('TELEGRAM_ADMIN_CHAT_ID', '42')
    assert dr.alert('hello') is False
    assert 'no usable TELEGRAM_BOT_TOKEN' in caplog.text


def test_alert_network_error_does_not_log_url(monkeypatch, caplog):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '123:SECRET')
    monkeypatch.setenv('TELEGRAM_ADMIN_CHAT_ID', '42')

    def boom(*a, **k):
        raise ConnectionError('https://api.telegram.org/bot123:SECRET/sendMessage')
    monkeypatch.setattr(dr.requests, 'post', boom)
    assert dr.alert('hello') is False
    assert 'SECRET' not in caplog.text


def test_each_pass_reprices_scored_meetings_with_races_to_run():
    source = Path('daily_run.py').read_text()
    body = source[source.index('def run_pass('):]
    assert "elif needs_ml(db, races_to_run):\n                score_ml(db, meeting, summary)\n            else:\n                reprice_ml(db, meeting)" in body


def test_reprice_failure_is_logged_not_raised(monkeypatch, caplog):
    import sys
    import types
    fake = types.ModuleType('ml_shadow_routes')
    fake._reprice_meeting_market = lambda db, meeting_id: (0, 'no live market')
    monkeypatch.setitem(sys.modules, 'ml_shadow_routes', fake)
    meeting = types.SimpleNamespace(id=7, meeting_name='261017_Flemington')
    with caplog.at_level('WARNING'):
        dr.reprice_ml(None, meeting)
    assert 'ML re-price failed for 261017_Flemington: no live market' in caplog.text


def test_route_recording_failure_is_logged_not_raised(monkeypatch, caplog):
    import sys
    import types

    def boom(races):
        raise RuntimeError('db gone')
    fake_app = types.ModuleType('app')
    fake_app.record_best_bet_routes = boom
    monkeypatch.setitem(sys.modules, 'app', fake_app)
    rolled_back = []
    db = types.SimpleNamespace(session=types.SimpleNamespace(
        commit=lambda: None, rollback=lambda: rolled_back.append(True)))
    with caplog.at_level('ERROR'):
        dr.record_routes(db, [])
    assert rolled_back == [True]
    assert 'Recording Best Bets routes failed' in caplog.text
