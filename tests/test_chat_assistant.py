"""Guards on the chat assistant (chat_assistant.py and /api/chat).

Each test pins one problem the old assistant had: conversations broke from
the sixth question, statistics counted one arbitrary runner per race, value
analysis ignored losers, quaddies used races 5-8 and kept scratched runners,
and admin-only data was not gated.
"""
import json
import uuid
from datetime import date
from types import SimpleNamespace

import pytest

import app as appmod
import chat_assistant as ca
from models import db, User, Meeting, Race, Horse, Prediction, Result, ChatMessage

TODAY = date(2026, 10, 2)


# ── History ────────────────────────────────────────────────────────────────

def test_history_always_starts_with_the_user_and_alternates():
    # Six questions in: the last 12 rows start with an assistant reply.
    history = []
    for i in range(6):
        history += [('user', f'q{i}'), ('assistant', f'a{i}')]
    history.append(('user', 'q6'))
    msgs = ca.build_messages(history, TODAY)
    assert msgs[0]['role'] == 'user'
    assert all(a['role'] != b['role'] for a, b in zip(msgs, msgs[1:]))
    assert msgs[-1]['role'] == 'user'


def test_failed_reply_leaves_two_user_rows_that_get_merged():
    msgs = ca.build_messages([('user', 'first'), ('user', 'second')], TODAY)
    assert len(msgs) == 1
    texts = [b['text'] for b in msgs[0]['content']]
    assert '2026-10-02' in texts[0]
    assert 'first' in texts[1] and 'second' in texts[1]


# ── Statistics ─────────────────────────────────────────────────────────────

def _runner(finish, sp, **kw):
    return {'finish': finish, 'sp': sp, **kw}


def test_summarise_counts_every_runner_and_level_stakes_roi():
    stats = ca.summarise([_runner(1, 5.0), _runner(2, 3.0), _runner(5, 9.0), _runner(1, None)])
    assert stats['runners'] == 4 and stats['wins'] == 2
    # Priced: win at $5 (+40), two losers (-20); unpriced winner left out.
    assert stats['profit_at_10_level'] == 20.0
    assert stats['roi_pct'] == round(20 / 30 * 100, 1)


@pytest.fixture()
def seeded():
    """One owner, one meeting of 5 races x 3 runners, results for all."""
    with appmod.app.app_context():
        owner = User(username=f'chat-{uuid.uuid4().hex[:8]}', email=f'{uuid.uuid4().hex[:8]}@example.com')
        owner.set_password('x')
        db.session.add(owner)
        db.session.flush()
        meeting = Meeting(user_id=owner.id, meeting_name=f'2026-10-02_Testtrack_{uuid.uuid4().hex[:4]}',
                          track='Testtrack', date=TODAY)
        db.session.add(meeting)
        db.session.flush()
        for race_no in range(1, 6):
            race = Race(meeting_id=meeting.id, race_number=race_no, race_class='Maiden' if race_no == 5 else 'BM64')
            db.session.add(race)
            db.session.flush()
            # Runner A: top ML pick, wins at $4. B: second, loses. C: scratched in race 5.
            for name, ml, score, finish, sp, odds, scratched in [
                ('A', 90.0, 70.0, 1, 4.0, '$3.00', False),
                ('B', 50.0, 95.0, 4, 2.0, '$4.00', False),
                ('C', 99.0, 99.0, 0 if race_no == 5 else 6, None if race_no == 5 else 20.0, '$9.00', race_no == 5),
            ]:
                h = Horse(race_id=race.id, horse_name=f'{name}{race_no}', is_scratched=scratched,
                          csv_data={'horse age': '3', 'horse sex': 'Mare'})
                db.session.add(h)
                db.session.flush()
                db.session.add(Prediction(horse_id=h.id, score=score, ml_score=ml, predicted_odds=odds))
                db.session.add(Result(horse_id=h.id, finish_position=finish, sp=sp))
        db.session.commit()
        ca._settled_cache.update(rows=None)
        yield SimpleNamespace(meeting_id=meeting.id, owner_id=owner.id)
        ca._settled_cache.update(rows=None)


def _mine(rows, meeting_id):
    return [r for r in rows if r['meeting_id'] == meeting_id]


def test_rank_groups_count_every_runner_in_every_race(seeded, monkeypatch):
    with appmod.app.app_context():
        rows = _mine(ca.settled_runners(), seeded.meeting_id)
        monkeypatch.setattr(ca, 'settled_runners', lambda: rows)
        out = ca.model_performance(model='ml', group_by='rank')
    groups = {g['group']: g for g in out['groups']}
    # Races 1-4 have three runners; race 5 lost its scratched runner.
    assert groups['top pick']['runners'] == 5
    assert groups['2nd pick']['runners'] == 5
    assert groups['3rd pick']['runners'] == 4
    # By ML, C tops races 1-4 and loses; A tops race 5 and wins.
    assert groups['top pick']['wins'] == 1


def test_value_analysis_includes_losers(seeded, monkeypatch):
    with appmod.app.app_context():
        rows = _mine(ca.settled_runners(), seeded.meeting_id)
        monkeypatch.setattr(ca, 'settled_runners', lambda: rows)
        out = ca.value_analysis()
    total = sum(g['runners'] for g in out['groups'])
    losers = total - sum(g['wins'] for g in out['groups'])
    assert total == 14 and losers > 0


def test_quaddie_uses_last_four_races_and_skips_scratched(seeded):
    with appmod.app.app_context():
        out = ca.get_quaddie(seeded.meeting_id, per_leg=3)
    assert [leg['race_number'] for leg in out['legs']] == [2, 3, 4, 5]
    race5 = out['legs'][-1]['selections']
    assert 'C5' not in [s['horse'] for s in race5]
    assert out['combinations'] == 3 * 3 * 3 * 2


def test_race_card_ranks_and_hides_scratched(seeded, monkeypatch):
    monkeypatch.setattr(ca, '_live_prices', lambda *a: {})
    with appmod.app.app_context():
        card = ca.get_race_card(seeded.meeting_id, 5)
    assert card['scratched'] == ['C5']
    assert [r['horse'] for r in card['runners']] == ['A5', 'B5']
    assert card['runners'][0]['ml_rank'] == 1 and card['has_run'] is True


# ── Tool gating and validation ─────────────────────────────────────────────

def test_best_bets_tool_is_admin_only():
    regular = SimpleNamespace(is_admin=False)
    admin = SimpleNamespace(is_admin=True)
    assert 'get_best_bets' not in [t['name'] for t in ca.tools_for(regular)]
    assert 'get_best_bets' in [t['name'] for t in ca.tools_for(admin)]
    assert 'error' in ca.run_tool('get_best_bets', {}, regular, TODAY)


def test_bad_tool_input_is_rejected_not_run():
    user = SimpleNamespace(is_admin=False)
    assert 'error' in ca.run_tool('model_performance', {'model': 'astrology'}, user, TODAY)
    assert 'error' in ca.run_tool('get_race_card', {'meeting_id': 1}, user, TODAY)
    assert 'error' in ca.run_tool('people_stats', 'not a dict', user, TODAY)


# ── Streaming loop ─────────────────────────────────────────────────────────

class _FakeStream:
    def __init__(self, texts, message):
        self._texts, self._message = texts, message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(SimpleNamespace(type='text', text=t) for t in self._texts)

    def get_final_message(self):
        return self._message


class _FakeClient:
    def __init__(self, turns):
        self.turns, self.calls = list(turns), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        return self.turns.pop(0)


def test_stream_reply_runs_tools_then_streams_the_answer(monkeypatch):
    ran = []
    monkeypatch.setattr(ca, 'run_tool', lambda name, args, user, today: ran.append(name) or {'ok': 1})
    tool_turn = _FakeStream([], SimpleNamespace(stop_reason='tool_use', content=[
        SimpleNamespace(type='tool_use', id='t1', name='model_performance', input={'model': 'ml'})]))
    answer_turn = _FakeStream(['ML top ', 'picks win 30%.'], SimpleNamespace(stop_reason='end_turn', content=[]))
    client = _FakeClient([tool_turn, answer_turn])

    events = list(ca.stream_reply(client, 'm', [('user', 'how do ML picks go?')],
                                  SimpleNamespace(is_admin=False), TODAY))
    assert ran == ['model_performance']
    assert [e['type'] for e in events] == ['status', 'text', 'text']
    assert ''.join(e['text'] for e in events if e['type'] == 'text') == 'ML top picks win 30%.'
    # The tool result went back to the model on the second call.
    second = client.calls[1]['messages']
    assert second[-1]['content'][0]['tool_use_id'] == 't1'
    assert client.calls[0]['system'][0]['cache_control'] == {'type': 'ephemeral'}


def test_stream_reply_handles_a_refusal():
    client = _FakeClient([_FakeStream([], SimpleNamespace(stop_reason='refusal', content=[]))])
    events = list(ca.stream_reply(client, 'm', [('user', 'x')], SimpleNamespace(is_admin=False), TODAY))
    assert events and events[0]['type'] == 'text' and 'Sorry' in events[0]['text']


# ── Route ──────────────────────────────────────────────────────────────────

def test_chat_route_streams_and_saves_the_reply(monkeypatch):
    def fake_stream(client, model, history, user, today):
        assert history[-1] == ('user', 'hello there')
        yield {'type': 'status', 'text': 'Looking…'}
        yield {'type': 'text', 'text': '**Hi**'}

    monkeypatch.setattr(ca, 'stream_reply', fake_stream)
    with appmod.app.app_context():
        user = User(username=f'chat-{uuid.uuid4().hex[:8]}', email=f'{uuid.uuid4().hex[:8]}@example.com')
        user.set_password('x')
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    appmod.limiter.enabled = False
    try:
        with appmod.app.test_client() as c:
            with c.session_transaction() as sess:
                sess['_user_id'] = str(user_id)
            resp = c.post('/api/chat', json={'message': 'hello there'})
            body = resp.get_data(as_text=True)
            history = c.get('/api/chat/history').get_json()
        # A fresh login (no chat session in the cookie) still finds the conversation.
        with appmod.app.test_client() as c2:
            with c2.session_transaction() as sess:
                sess['_user_id'] = str(user_id)
            later = c2.get('/api/chat/history').get_json()
    finally:
        appmod.limiter.enabled = True

    assert resp.mimetype == 'text/event-stream'
    events = [json.loads(line[5:]) for line in body.split('\n\n') if line.startswith('data:')]
    assert [e['type'] for e in events] == ['status', 'text', 'done']
    assert [m['role'] for m in history['messages']] == ['user', 'assistant']
    assert history['messages'][1]['content'] == '**Hi**'
    assert later['messages'] == history['messages']
    with appmod.app.app_context():
        assert ChatMessage.query.filter_by(user_id=user_id).count() == 2
