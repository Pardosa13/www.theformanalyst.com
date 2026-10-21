"""Pattern Finder: the maths, the search, saving rules and the page.

Made-up races with a known answer: in some, the Analyzer top pick in maiden
races truly wins more often than its price says. The finder must find that,
find nothing when there is no edge, and retire a rule that loses.
"""
import json
from datetime import date, datetime, timedelta

import numpy as np
import pytest

import pattern_finder as pf


# ── Made-up races ─────────────────────────────────────────────────────────
def make_runners(n_races=4000, edge=2.0, seed=1):
    """Each race: 6-12 runners priced fairly with a 15% margin. With edge > 1,
    the Analyzer top pick in maiden races wins `edge` times more often."""
    rng = np.random.default_rng(seed)
    runners, horse_id = [], 0
    for race_id in range(n_races):
        day = date(2024, 1, 1) + timedelta(days=race_id // 10)
        k = int(rng.integers(6, 13))
        strength = rng.gamma(2, 1, k)
        maiden = rng.random() < 0.3
        fair = strength / strength.sum()
        true = fair.copy()
        score = strength + rng.normal(0, 0.8, k)
        if maiden and edge != 1.0:
            true[int(np.argmax(score))] *= edge
            true /= true.sum()
        winner = rng.choice(k, p=true)
        for i in range(k):
            horse_id += 1
            runners.append({
                'horse_id': horse_id, 'race_id': race_id, 'meeting_id': race_id // 10,
                'horse_name': f'Horse {horse_id}', 'meeting_name': 'm', 'track': 't', 'race_number': 1,
                'date': day, 'start_time_utc': None, 'race_class': 'Maiden' if maiden else 'BM64',
                'distance': '1200m', 'track_condition': 'Good 4', 'results_incomplete': False,
                'barrier': float(i + 1), 'age': float(rng.integers(2, 8)), 'sex': 'gelding', 'notes': '',
                'score': float(score[i]), 'pfai': float(score[i] + rng.normal(0, 1)),
                'ml_score': None, 'ml_scored_at': None, 'has_result': True,
                'finish_position': 1 if i == winner else 5,
                'sp': float(round(1 / (fair[i] * 1.15), 2)), 'bsp': None,
            })
    for race in pf._group(runners).values():
        pf._add_race_ranks(race, forward_only=True)
    return runners


def dataset(runners):
    return pf.Dataset(runners, [pf.runner_conditions(r, {}) for r in runners])


# ── Maths ─────────────────────────────────────────────────────────────────
def test_bet_stats_flat_win_bets():
    stats = pf.bet_stats([True, False, False, False], [4.0, 4.0, 4.0, 4.0])
    assert stats['bets'] == 4 and stats['wins'] == 1
    assert stats['profit'] == 0.0 and stats['roi'] == 0.0
    stats = pf.bet_stats([True, False], [5.0, 3.0])
    assert stats['profit'] == 3.0
    assert stats['z'] == round(3.0 / np.sqrt(4.0 + 2.0), 2)


def test_bet_stats_betfair_after_commission():
    stats = pf.bet_stats([True] + [False] * 39, [3.0] * 40, [5.0] * 40)
    assert stats['bsp_bets'] == 40
    assert stats['bsp_roi'] == round(100 * ((4.0 * 0.92) - 39) / 40, 1)


def test_monte_carlo_p_value():
    prices = np.full(200, 4.0)
    assert pf.monte_carlo_p(prices, 150.0, sims=2000) < 0.01   # far more than luck gives
    assert pf.monte_carlo_p(prices, -50.0, sims=2000) > 0.8    # a loss is no evidence


def test_benjamini_hochberg():
    assert pf.benjamini_hochberg([0.001, 0.02, 0.5, 0.9], 0.10) == {0, 1}
    assert pf.benjamini_hochberg([0.3, 0.5], 0.10) == set()


# ── Conditions ────────────────────────────────────────────────────────────
def test_runner_conditions():
    runner = {
        'sp': 4.2, 'mkt_rank': 2, 'an_rank': 1, 'pf_rank': 1, 'ml_rank': 1, 'field': 10,
        'race_class': 'Maiden Plate', 'distance': '1400m', 'track_condition': 'Soft 6',
        'barrier': 2.0, 'age': 3.0, 'sex': 'filly',
    }
    keys = pf.runner_conditions(runner, {'Weight Change - Dropped 3kg+': 1.0, '_internal': 1})
    assert {'price:3-5', 'mkt:2-3', 'an:1', 'pf:1', 'ml:1', 'agree:an+pf', 'agree:all3', 'field:9-12',
            'class:maiden', 'dist:1200-1599', 'going:soft', 'barrier:1-3', 'age:3', 'sex:female',
            'c:Weight Change - Dropped 3kg+'} <= keys
    assert 'c:_internal' not in keys


def test_class_and_going_parsing():
    assert pf._class_key('BM64 Handicap') == 'class:bm'
    assert pf._class_key('Group 1') == 'class:group'
    assert pf._class_key('Class 3') == 'class:class'
    assert pf._class_key('Open Handicap') == 'class:other'
    assert pf._going_key('Heavy 8') == 'going:heavy'
    assert pf._going_key('synthetic') == 'going:synthetic'
    assert pf._going_key('') is None


def test_ml_rank_only_counts_when_scored_before_the_start():
    start = datetime(2026, 10, 1, 5, 0)
    race = [
        {'score': 1.0, 'pfai': 1.0, 'sp': 2.0, 'ml_score': 90.0, 'start_time_utc': start,
         'ml_scored_at': start - timedelta(minutes=10)},
        {'score': 2.0, 'pfai': 2.0, 'sp': 3.0, 'ml_score': 95.0, 'start_time_utc': start,
         'ml_scored_at': start + timedelta(hours=1)},     # scored after the race: not used
    ]
    pf._add_race_ranks(race, forward_only=True)
    assert race[0]['ml_rank'] == 1 and race[1]['ml_rank'] is None
    assert race[1]['an_rank'] == 1 and race[0]['mkt_rank'] == 1


def test_conflicting_conditions_are_never_combined():
    ds = dataset(make_runners(n_races=600, edge=1.0))
    a, b = ds.index['an:1'], ds.index['agree:an+pf']
    assert ds.conflict[a, b]
    assert ds.conflict[ds.index['price:3-5'], ds.index['mkt:fav']]


# ── Search ────────────────────────────────────────────────────────────────
def test_finds_a_real_edge_in_its_simplest_form():
    ds = dataset(make_runners(edge=2.0))
    proven, _near, tested, _cands = pf.find_patterns(ds)
    assert tested > 100
    found = [set(row['conditions']) for row in proven]
    assert {'an:1', 'class:maiden'} in found
    # A slice of a proven pattern (e.g. + a price band) is not listed again.
    assert not any({'an:1', 'class:maiden'} < f for f in found)
    row = next(r for r in proven if set(r['conditions']) == {'an:1', 'class:maiden'})
    assert row['search']['roi'] > 0 and row['test']['roi'] > 0


def test_finds_nothing_when_there_is_no_edge():
    ds = dataset(make_runners(edge=1.0, seed=3))
    proven, _near, _tested, _cands = pf.find_patterns(ds)
    assert proven == []


def test_luck_benchmark_proves_nothing():
    ds = dataset(make_runners(n_races=2000, edge=2.0))
    luck = pf.luck_benchmark(ds, runs=2)
    assert len(luck) == 2
    assert all(run['proven'] == 0 for run in luck)


def test_unseen_races_are_newer_than_search_races():
    ds = dataset(make_runners(n_races=1000, edge=1.0))
    search_dates = [d for d, s in zip(ds.dates, ds.is_search) if s]
    unseen_dates = [d for d, s in zip(ds.dates, ds.is_search) if not s]
    assert max(search_dates) < min(unseen_dates)
    assert 0.6 < len(search_dates) / ds.n < 0.8


def test_retire_rules():
    assert pf.retire_reason({'bets': 60, 'z': -2.5, 'roi': -40.0}) is not None
    assert pf.retire_reason({'bets': 30, 'z': -3.0, 'roi': -60.0}) is None          # too few bets
    assert pf.retire_reason({'bets': 400, 'z': -0.5, 'roi': -3.0,
                             'recent_bets': 300, 'recent_roi': -4.0}) is not None
    assert pf.retire_reason({'bets': 400, 'z': 0.5, 'roi': 4.0,
                             'recent_bets': 300, 'recent_roi': 6.0}) is None


# ── Saving rules and the page ─────────────────────────────────────────────
flask = pytest.importorskip('flask')
pytest.importorskip('flask_sqlalchemy')


@pytest.fixture
def app():
    from flask import Flask
    from flask_login import LoginManager
    from models import db, User
    from pattern_routes import register_pattern_routes

    application = Flask(__name__, template_folder='../templates', static_folder='../static')
    application.config.update(SQLALCHEMY_DATABASE_URI='sqlite:///:memory:', SQLALCHEMY_TRACK_MODIFICATIONS=False,
                              TESTING=True, SECRET_KEY='test')
    db.init_app(application)
    login_manager = LoginManager()
    login_manager.init_app(application)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    register_pattern_routes(application, db)

    @application.route('/history')
    def history():
        return 'history'

    @application.context_processor
    def base_template_values():   # app.py fills these for the real site
        return {'default_landing_endpoint': 'history', 'budget_tracker_allowed': False,
                'julz_restricted': False}

    # base.html links to many pages; give every unknown endpoint a dummy URL.
    def dummy_url(error, endpoint, values):
        return '#'
    application.url_build_error_handlers.append(dummy_url)

    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


def _fill_database(runners):
    from models import db, User, Meeting, Race, Horse, Prediction, Result
    user = User(username='admin', email='a@example.com', password_hash='x', is_admin=True)
    db.session.add(user)
    db.session.flush()
    meetings, races = {}, {}
    for r in runners:
        if r['meeting_id'] not in meetings:
            meetings[r['meeting_id']] = Meeting(user_id=user.id, meeting_name=f"m{r['meeting_id']}",
                                                track='t', date=r['date'])
            db.session.add(meetings[r['meeting_id']])
    db.session.flush()
    for r in runners:
        if r['race_id'] not in races:
            races[r['race_id']] = Race(meeting_id=meetings[r['meeting_id']].id, race_number=1,
                                       distance=r['distance'], race_class=r['race_class'],
                                       track_condition=r['track_condition'])
            db.session.add(races[r['race_id']])
    db.session.flush()
    for r in runners:
        horse = Horse(race_id=races[r['race_id']].id, horse_name=r['horse_name'], barrier=int(r['barrier']),
                      csv_data={'horse age': r['age'], 'horse sex': r['sex']})
        db.session.add(horse)
        db.session.flush()
        db.session.add(Prediction(horse_id=horse.id, score=r['score'], pfai_score=r['pfai'], notes=''))
        db.session.add(Result(horse_id=horse.id, finish_position=r['finish_position'], sp=r['sp']))
    db.session.commit()
    return user


@pytest.fixture
def small_settings(monkeypatch):
    for name, value in (('MIN_SEARCH_BETS', 60), ('MIN_TEST_BETS', 30), ('MIN_CONDITION_BETS', 40),
                        ('MONTE_CARLO_SIMS', 1000), ('LUCK_RUNS', 1)):
        monkeypatch.setattr(pf, name, value)


def test_run_saves_rules_and_retires_a_loser(app, small_settings):
    from models import db, PatternRule, PatternFinderRun
    runners = make_runners(n_races=1500, edge=2.2, seed=2)
    _fill_database(runners)
    # A rule "proven" long ago that has lost since: backing the 4th Analyzer pick or worse.
    db.session.add(PatternRule(rule_key='an:4+', conditions_json=json.dumps(['an:4+']),
                               label='Analyzer 4th pick or lower', status='proven',
                               proven_through=date(2023, 12, 31)))
    db.session.commit()

    report = pf.run_and_save(db.engine)

    assert report['races'] == 1500
    assert report['proven_this_run'] >= 1
    rules = {r.rule_key: r for r in PatternRule.query.all()}
    assert 'an:1 & class:maiden' in rules or any('class:maiden' in k for k in rules)
    loser = rules['an:4+']
    assert loser.status == 'retired' and loser.retire_reason
    assert json.loads(loser.live_json)['bets'] > 0
    assert PatternFinderRun.query.count() == 1

    # A second run keeps rules and does not add duplicates or revive the loser.
    count = PatternRule.query.count()
    pf.run_and_save(db.engine)
    db.session.expire_all()
    assert PatternRule.query.count() == count
    assert db.session.get(PatternRule, loser.id).status == 'retired'


def test_page_renders_with_rules_and_todays_matches(app, small_settings):
    from models import db, User, Meeting, Race, Horse, Prediction
    runners = make_runners(n_races=1500, edge=2.2, seed=2)
    user = _fill_database(runners)
    pf.run_and_save(db.engine)

    # A race still to run today where the Analyzer top pick is in a maiden.
    meeting = Meeting(user_id=user.id, meeting_name='today', track='t', date=pf._melbourne_today())
    db.session.add(meeting)
    db.session.flush()
    race = Race(meeting_id=meeting.id, race_number=3, distance='1200m', race_class='Maiden',
                track_condition='Good 4')
    db.session.add(race)
    db.session.flush()
    for name, score in (('Today Star', 9.0), ('Other One', 2.0), ('Third Horse', 1.0)):
        horse = Horse(race_id=race.id, horse_name=name, barrier=1, csv_data={'horse age': 3})
        db.session.add(horse)
        db.session.flush()
        db.session.add(Prediction(horse_id=horse.id, score=score, pfai_score=score, notes=''))
    db.session.commit()

    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user.id)
        session['_fresh'] = True
    page = client.get('/pattern-finder')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'Proven patterns' in html
    assert 'Maiden race' in html
    assert 'Today Star' in html
    assert 'Luck check' in html


def test_page_without_any_run(app):
    from models import db, User
    user = User(username='admin', email='a@example.com', password_hash='x', is_admin=True)
    db.session.add(user)
    db.session.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user.id)
    html = client.get('/pattern-finder').get_data(as_text=True)
    assert 'No runs yet' in html
