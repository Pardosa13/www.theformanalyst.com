"""End-to-end checks on the Race Animations endpoints.

The scoring tests prove the maths. These prove the wiring: that a real race in
a real database comes back as a payload the page can draw, that the actual
result is carried with it, and — the reason half of this exists — that the
endpoints do not go back to the database once per horse.

Everything runs against an in-memory SQLite database built here, so nothing
touches a real environment and the whole file is a few hundredths of a second.
"""

import json
from datetime import date

import pytest

pytest.importorskip('flask', reason='Flask is not installed in this environment')
pytest.importorskip('flask_sqlalchemy')

from flask import Flask
from flask_login import LoginManager
from sqlalchemy import event

from models import db, User, Meeting, Race, Horse, Prediction, Result
from race_animation_routes import register_race_animation_routes


# ── A small meeting, built by hand ────────────────────────────────────────
FIELD = [
    # name,          barrier, jockey,        settle, mapA2E, ml,   sp,   finish
    ('Ardent Lane',      3, 'A Apprentice',      2,  1.24,  82.0,  3.40, 1),
    ('Bold Reckoning',  11, 'B Rider',           9,  0.88,  74.0,  4.60, 3),
    ('Cold Harbour',     1, 'C Hoop',            1,  1.11,  61.0,  7.00, 2),
    ('Drift Wood',       7, 'D Jockey',          6,  0.95,  58.0, 12.00, 5),
    ('Even Money',       5, 'E Pilot',           4,  1.02,  70.0,  2.60, 4),
    ('Far Country',      9, 'F Rider',          12,  0.71,  44.0, 26.00, 5),
]


@pytest.fixture
def app():
    application = Flask(__name__, template_folder='../templates', static_folder='../static')
    application.config.update(
        SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=True,
        SECRET_KEY='test',
        # Flask-Login honours this, which keeps the tests about the payloads
        # rather than about signing in.
        LOGIN_DISABLED=True,
    )
    db.init_app(application)

    login_manager = LoginManager()
    login_manager.init_app(application)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    register_race_animation_routes(application, db)

    with application.app_context():
        db.create_all()
        _seed()
        yield application
        db.session.remove()
        db.drop_all()


def _seed():
    user = User(username='tester', email='t@example.com', password_hash='x')
    db.session.add(user)
    db.session.flush()

    meeting = Meeting(user_id=user.id, meeting_name='260902_Randwick', track='Randwick',
                      date=date(2026, 9, 2), rail_position=4, pace_bias=1)
    db.session.add(meeting)
    db.session.flush()

    race = Race(meeting_id=meeting.id, race_number=5, distance='1200m',
                race_class='BM78', track_condition='Good 4')
    # The speed map and the sectionals are stored exactly as the feeds write
    # them, so the extraction in the routes is genuinely exercised.
    race.speed_maps_json = {'payLoad': [{'items': [
        {'runnerName': name, 'tabNo': index + 1, 'settle': settle,
         'speed': 100 - settle, 'mapA2E': map_value, 'pfaiScore': 60 + index,
         'jockeyA2E': 1.0}
        for index, (name, _b, _j, settle, map_value, _ml, _sp, _f) in enumerate(FIELD)
    ]}]}
    race.sectionals_json = {'payLoad': [
        {'runnerName': name, 'raceNo': 5,
         'last600TimeRank': index + 1, 'last400TimeRank': index + 1,
         'last200TimeRank': index + 1}
        for index, (name, *_rest) in enumerate(FIELD)
    ]}
    db.session.add(race)
    db.session.flush()

    for index, (name, barrier, jockey, _settle, _map, ml, sp, finish) in enumerate(FIELD):
        horse = Horse(race_id=race.id, horse_name=name, barrier=barrier,
                      jockey=jockey, trainer=f'Trainer {index}',
                      csv_data={'horse number': index + 1}, is_scratched=False)
        db.session.add(horse)
        db.session.flush()
        db.session.add(Prediction(horse_id=horse.id, score=50.0 + index, ml_score=ml,
                                  notes='best of last 5 (z=1.20)\n└─ 33.77s → 33.0%ds'
                                        % index))
        db.session.add(Result(horse_id=horse.id, finish_position=finish, sp=sp,
                              recorded_by=user.id))

    # A scratched runner, which must never appear in a payload.
    scratched = Horse(race_id=race.id, horse_name='Gone Fishing', barrier=2,
                      jockey='G Rider', is_scratched=True)
    db.session.add(scratched)

    # A second race with no result yet, for the not-run-yet path.
    unrun = Race(meeting_id=meeting.id, race_number=6, distance='2400m')
    db.session.add(unrun)
    db.session.flush()
    for index in range(5):
        horse = Horse(race_id=unrun.id, horse_name=f'Maiden {index}', barrier=index + 1,
                      jockey=f'J {index}', is_scratched=False)
        db.session.add(horse)
        db.session.flush()
        db.session.add(Prediction(horse_id=horse.id, score=40.0 + index))
    db.session.commit()


@pytest.fixture
def client(app):
    return app.test_client()


def _race_id(app, number):
    with app.app_context():
        return Race.query.filter_by(race_number=number).first().id


def _get(client, url):
    response = client.get(url)
    assert response.status_code == 200, response.data
    return json.loads(response.data)


# ── Counting queries ──────────────────────────────────────────────────────
class _QueryCounter:
    """Count SQL statements while a block runs.

    The point of the eager loading is that a bigger field does not mean more
    round trips. Counting is the only way to hold that: the payload looks
    identical either way, right up until a sixteen-runner race takes a second
    to load.
    """

    def __init__(self, engine):
        self.engine = engine
        self.statements = []

    def __enter__(self):
        event.listen(self.engine, 'before_cursor_execute', self._record)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, 'before_cursor_execute', self._record)

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(statement)

    def __len__(self):
        return len(self.statements)


# ── The race payload ──────────────────────────────────────────────────────
def test_the_payload_carries_the_field_in_predicted_order(client, app):
    payload = _get(client, f'/api/race-animation/race/{_race_id(app, 5)}?prices=0')

    assert payload['success'] is True
    assert payload['race']['field_size'] == len(FIELD)
    # The scratched runner is not in it.
    names = [r['horse_name'] for r in payload['runners']]
    assert 'Gone Fishing' not in names
    # Ranks are 1..n, in order.
    assert [r['rank'] for r in payload['runners']] == list(range(1, len(FIELD) + 1))
    # Every runner has every component, and a composite on the 0-100 scale.
    for runner in payload['runners']:
        assert set(runner['components']) == set(payload['default_weights'])
        assert 0 <= runner['composite_score'] <= 100
        assert runner['lane'] is not None


def test_the_payload_knows_what_actually_happened(client, app):
    """The upgrade this page most needed: does the prediction check itself?"""
    payload = _get(client, f'/api/race-animation/race/{_race_id(app, 5)}?prices=0')
    summary = payload['result_summary']

    assert summary['has_results'] is True
    assert summary['winner_name'] == 'Ardent Lane'
    assert summary['winner_sp'] == 3.40
    assert summary['predicted_place_of_winner'] >= 1
    assert isinstance(summary['found_winner'], bool)
    assert summary['mean_placing_error'] is not None

    winner = [r for r in payload['runners'] if r['horse_name'] == 'Ardent Lane'][0]
    assert winner['result']['won'] is True
    assert winner['result']['finish_position'] == 1


def test_a_race_not_yet_run_says_so_instead_of_pretending(client, app):
    payload = _get(client, f'/api/race-animation/race/{_race_id(app, 6)}?prices=0')
    assert payload['result_summary'] == {'has_results': False}
    assert all(runner['result'] is None for runner in payload['runners'])


def test_the_payload_prices_the_field(client, app):
    payload = _get(client, f'/api/race-animation/race/{_race_id(app, 5)}?prices=0')

    total = sum(r['win_probability'] for r in payload['runners'])
    assert total == pytest.approx(1.0, abs=1e-6)
    for runner in payload['runners']:
        assert runner['fair_odds'] > 1.0
        # Every runner had a starting price recorded, so every runner has a
        # market comparison to go with the model's own opinion.
        assert runner['value']['price'] is not None
        assert runner['value']['edge_pct'] is not None
    assert payload['race']['has_prices'] is True


def test_the_payload_reads_the_tempo_and_the_track(client, app):
    payload = _get(client, f'/api/race-animation/race/{_race_id(app, 5)}?prices=0')

    assert payload['pace']['shape'] in ('hot', 'even', 'soft')
    assert payload['pace']['counts']['leader'] >= 1
    assert payload['pace']['pace_bias'] == 1          # off the meeting
    assert payload['meeting']['rail_position'] == 4

    # Randwick is a Sydney track, so the field runs clockwise, and a 1200m race
    # is less than a full circuit.
    assert payload['race']['direction'] == 'clockwise'
    assert 0 < payload['race']['lap_fraction'] < 1
    assert payload['race']['duration_seconds'] > 0

    # A 2400m race is drawn as a longer trip than a 1200m one.
    staying = _get(client, f'/api/race-animation/race/{_race_id(app, 6)}?prices=0')
    assert staying['race']['duration_seconds'] > payload['race']['duration_seconds']


def test_the_payload_runs_the_race_a_thousand_times(client, app):
    payload = _get(client, f'/api/race-animation/race/{_race_id(app, 5)}?prices=0')
    simulation = payload['simulation']

    assert simulation['runs'] > 100
    assert len(simulation['summary']) == len(FIELD)
    assert sum(row['win_pct'] for row in simulation['summary']) == pytest.approx(100.0, abs=0.5)
    # And one sampled running, for an animation that is not a foregone conclusion.
    assert sorted(payload['sample_order']) == sorted(r['horse_id'] for r in payload['runners'])


def test_a_custom_weighting_reorders_the_payload(client, app):
    race_id = _race_id(app, 5)
    default = _get(client, f'/api/race-animation/race/{race_id}?prices=0')
    reweighted = _get(
        client,
        f'/api/race-animation/race/{race_id}?prices=0'
        '&w_speed_map=0&w_sectional=0&w_adjusted_time=0&w_assessment=0&w_draw=100')

    assert reweighted['weights']['draw'] == 100.0
    assert reweighted['weights_are_default'] is False
    # Barrier 1 has to come out on top when the draw is the only thing scored.
    assert reweighted['runners'][0]['barrier'] == 1
    assert ([r['horse_id'] for r in reweighted['runners']]
            != [r['horse_id'] for r in default['runners']])


def test_the_scaling_method_can_be_chosen(client, app):
    race_id = _race_id(app, 5)
    assert _get(client, f'/api/race-animation/race/{race_id}?prices=0')['norm_method'] == 'rank'
    assert _get(client, f'/api/race-animation/race/{race_id}?prices=0&norm=minmax'
                )['norm_method'] == 'minmax'
    # Anything unrecognised falls back rather than failing the request.
    assert _get(client, f'/api/race-animation/race/{race_id}?prices=0&norm=nonsense'
                )['norm_method'] == 'rank'


def test_a_missing_race_is_a_404_not_a_500(client):
    assert client.get('/api/race-animation/race/999999').status_code == 404


# ── The N+1 fixes ─────────────────────────────────────────────────────────
def test_the_meeting_list_does_not_query_once_per_meeting(client, app):
    """It used to call len(m.races) — 120 meetings, 121 queries."""
    with app.app_context():
        with _QueryCounter(db.engine) as counter:
            data = _get(client, '/api/race-animation/meetings')

    assert data['meetings'][0]['race_count'] == 2
    # One for the meetings, one grouped count for all of them. A couple of
    # spare is fine; one per meeting is not, and that is what this catches.
    assert len(counter) <= 4, f'{len(counter)} queries: {counter.statements}'


def test_the_race_payload_does_not_query_once_per_horse(client, app):
    """It used to fetch each horse's prediction and result separately."""
    race_id = _race_id(app, 5)
    with app.app_context():
        with _QueryCounter(db.engine) as counter:
            _get(client, f'/api/race-animation/race/{race_id}?prices=0')

    # Race + horses + predictions + results + meeting + two strike-rate lookups.
    # Well under one per runner, which is the point.
    assert len(counter) <= 10, f'{len(counter)} queries: {counter.statements}'


def test_the_race_payload_does_not_call_a_bookmaker(client, app, monkeypatch):
    """Silks were the reason every page load waited on the network.

    They have their own endpoint now. If anything ever puts that call back into
    the payload, this fails.
    """
    import race_animation_routes as routes

    def explode(*args, **kwargs):
        raise AssertionError('the race payload must not fetch silks')

    monkeypatch.setattr(routes, '_ladbrokes_silks', explode)
    payload = _get(client, f'/api/race-animation/race/{_race_id(app, 5)}?prices=0')
    assert payload['success'] is True
    # The runners still have something to be drawn with.
    assert payload['runners'][0]['silk']['fallback']['primary'].startswith('#')


def test_a_settled_race_never_asks_a_bookmaker_for_a_price(client, app, monkeypatch):
    """A race that has been run prices itself off its own starting prices.

    The live market is the one network call the payload is still allowed to
    make, and only for a race that has not been run. Reaching for it on a
    historical meeting is the waste this page used to be full of.
    """
    import race_animation_routes as routes

    def explode(*args, **kwargs):
        raise AssertionError('a settled race must price itself off its own result')

    monkeypatch.setattr(routes, '_live_prices', explode)
    payload = _get(client, f'/api/race-animation/race/{_race_id(app, 5)}')
    assert payload['race']['has_prices'] is True
    # The prices used are the recorded starting prices, not anything live.
    winner = [r for r in payload['runners'] if r['horse_name'] == 'Ardent Lane'][0]
    assert winner['price'] == 3.40


def test_an_unrun_race_may_ask_for_a_live_price(client, app, monkeypatch):
    """...and where it can actually succeed, it is worth having.

    The market is a scoring component and the whole value column hangs off it,
    so for a race still to be run this is real information rather than
    decoration.
    """
    import race_animation_routes as routes

    asked = []

    def prices(meeting, race):
        asked.append(race.id)
        return {'maiden 0': 2.50, 'maiden 1': 4.00, 'maiden 2': 6.00,
                'maiden 3': 9.00, 'maiden 4': 15.00}

    monkeypatch.setattr(routes, '_live_prices', prices)
    payload = _get(client, f'/api/race-animation/race/{_race_id(app, 6)}')
    assert asked, 'an upcoming race should look for a live market'
    assert payload['race']['has_prices'] is True
    assert all(runner['value']['price'] for runner in payload['runners'])

    # And it can always be switched off.
    asked.clear()
    _get(client, f'/api/race-animation/race/{_race_id(app, 6)}?prices=0')
    assert not asked


def test_silks_have_their_own_endpoint(client, app, monkeypatch):
    import race_animation_routes as routes

    monkeypatch.setattr(routes, '_ladbrokes_silks', lambda meeting, race: {
        'sprite_url': 'https://example.test/silks.png',
        'runner_numbers': {'ardent lane': 1, 'bold reckoning': 2},
        'prices': {},
    })
    data = _get(client, f'/api/race-animation/race/{_race_id(app, 5)}/silks')
    assert data['has_silks'] is True
    assert len(data['runners']) == 2
    assert data['runners'][0]['sprite_url'] == 'https://example.test/silks.png'
    assert data['runners'][1]['tile_offset_px'] == -32


def test_a_silk_feed_that_is_down_is_not_an_error(client, app, monkeypatch):
    import race_animation_routes as routes
    monkeypatch.setattr(routes, '_ladbrokes_silks',
                        lambda meeting, race: {'sprite_url': '', 'runner_numbers': {}})
    data = _get(client, f'/api/race-animation/race/{_race_id(app, 5)}/silks')
    assert data['has_silks'] is False
    assert data['runners'] == []


# ── Scoring the weighting against history ─────────────────────────────────
def test_the_accuracy_endpoint_scores_the_history(client):
    data = _get(client, '/api/race-animation/accuracy')
    assert data['success'] is True
    if data['has_history']:
        assert data['current']['races'] >= 1
        assert data['default']['races'] == data['current']['races']
    else:
        # One race is not a history; saying so is the right answer.
        assert 'reason' in data


def test_the_tuning_endpoint_refuses_when_there_is_nothing_to_learn_from(client):
    """One settled race cannot tune anything, and must not pretend otherwise."""
    data = _get(client, '/api/race-animation/tune')
    assert data['success'] is True
    assert data['ok'] is False
    assert 'reason' in data


# ── The page itself ───────────────────────────────────────────────────────
def test_the_page_hands_its_constants_to_the_browser(client, app, monkeypatch):
    """The fix for the two implementations drifting apart.

    The page is not rendered here — it extends the whole site's base template,
    which wants every other route in the app to exist. What matters is the
    contract: the view hands the template the scoring constants straight out of
    the Python module, so the browser never retypes a weight of its own.
    """
    import race_animation_routes as routes
    import race_animation_scoring as scoring

    captured = {}

    def fake_render(template_name, **context):
        captured['template'] = template_name
        captured['context'] = context
        return 'rendered'

    monkeypatch.setattr(routes, 'render_template', fake_render)
    assert client.get('/race-animations-predictions').status_code == 200

    assert captured['template'] == 'race-animations-predictions.html'
    config = captured['context']['scoring_config']
    assert config == scoring.scoring_constants()
    assert config['default_weights']['speed_map'] == 50.0


def test_the_template_reads_those_constants_rather_than_its_own(app):
    """No second copy of the weights hiding in the page's own script."""
    from pathlib import Path

    template = (Path(__file__).resolve().parents[1]
                / 'templates' / 'race-animations-predictions.html').read_text()

    assert 'scoring_config | tojson' in template
    assert 'race-animation-scoring.js' in template
    assert 'CONFIG.default_weights' in template
    # The numbers that used to be duplicated here.
    for forbidden in ('MARGIN_MIN', 'MARGIN_TOTAL', 'speed_map: 50'):
        assert forbidden not in template, f'{forbidden!r} is a retyped server constant'
