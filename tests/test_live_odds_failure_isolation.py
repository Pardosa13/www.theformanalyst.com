"""A failed optional lookup must cost that lookup and nothing else.

Meeting 1962 produced ZERO picks because live_odds_snapshots did not exist on
the production database. The lookup itself already caught the error and
returned {} — the degraded path the design intended. What it did not do was
roll back, so the aborted Postgres transaction stayed aborted and every query
after it ("load this race's horses" among them) came back with

    current transaction is aborted, commands ignored until end of transaction
    block

Live odds are optional; the horses are not. These tests pin the isolation
rather than the specific error: the odds query is made to raise an ARBITRARY
exception (a timeout, a dropped connection and a permissions error poison a
transaction exactly like a missing table does), and predict_meeting must still
return picks for the meeting.
"""

import os
import sys
import types

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

pytest.importorskip("numpy")
pytest.importorskip("pandas")

# ml_predict only needs sklearn.base at import time (via model_classes); stub
# it so this test runs without the heavy ML stack, like the other unit tests.
if "sklearn" not in sys.modules:
    sys.modules["sklearn"] = types.ModuleType("sklearn")
    base = types.ModuleType("sklearn.base")
    base.BaseEstimator = type("BaseEstimator", (), {})
    base.RegressorMixin = type("RegressorMixin", (), {})
    base.clone = lambda estimator: estimator
    sys.modules["sklearn.base"] = base

# predict_meeting imports the Flask-SQLAlchemy ORM classes purely as query
# keys, and this test never touches a real ORM. Stub them when Flask is not
# installed so the isolation is testable without the web stack; a full
# environment keeps the real classes.
try:  # pragma: no cover - depends only on what is installed
    import models  # noqa: F401
except Exception:  # pragma: no cover
    _models = types.ModuleType("models")
    for _name in ("Meeting", "Race", "Horse"):
        setattr(_models, _name, type(_name, (), {}))
    sys.modules["models"] = _models

import numpy as np

import ml_predict


class OddsQueryFailure(RuntimeError):
    """Deliberately NOT an UndefinedTable. The isolation must not be keyed to
    one error: a statement timeout or a revoked SELECT grant leaves the same
    aborted transaction behind."""


class _Meeting:
    id = 1962
    rail_position = None


class _Horse:
    def __init__(self, horse_id, session, weight):
        self.id = horse_id
        self.horse_name = f"Horse {horse_id}"
        self.is_scratched = False
        self._session = session
        self._csv_data = {'horse weight': weight, 'barrier': horse_id}

    @property
    def csv_data(self):
        # Reading a horse row is a real query, so it fails exactly like every
        # other statement would while the transaction is still aborted.
        self._session.check_usable("horses.csv_data")
        return self._csv_data


class _Race:
    def __init__(self, race_id, session):
        self.id = race_id
        self.race_number = race_id
        self.track_condition = 'Good'
        self.ratings_json = None
        self.speed_maps_json = None
        self._session = session
        self._horses = [_Horse(race_id * 10 + n, session, 54.0 + n) for n in range(1, 5)]

    @property
    def horses(self):
        # The lazy load of a race's runners: the mandatory query that inherited
        # the odds lookup's failure on meeting 1962.
        self._session.check_usable("race.horses")
        return self._horses


class _Query:
    def __init__(self, session, entity):
        self._session = session
        self._entity = entity

    def get(self, _pk):
        self._session.check_usable("Meeting.get")
        return _Meeting()

    def filter_by(self, **_kwargs):
        return self

    def all(self):
        self._session.check_usable("Race.all")
        return self._session.races


class _Session:
    """A session that behaves like Postgres: once a statement raises, every
    later statement fails until someone rolls back."""

    def __init__(self, fail_on):
        self.fail_on = fail_on
        self.aborted = False
        self.rollbacks = 0
        self.races = [_Race(1, self), _Race(2, self)]

    def check_usable(self, what):
        if self.aborted:
            raise RuntimeError(
                "current transaction is aborted, commands ignored until end of "
                f"transaction block (while running {what})"
            )

    def query(self, entity):
        return _Query(self, entity)

    def execute(self, statement, params=None):
        self.check_usable("execute")
        sql = str(statement)
        if self.fail_on in sql:
            self.aborted = True
            raise OddsQueryFailure("odds lookup blew up")
        return _EmptyResult()

    def rollback(self):
        self.rollbacks += 1
        self.aborted = False


class _EmptyResult:
    def fetchall(self):
        return []

    def mappings(self):
        return self

    def all(self):
        return []


class _Model:
    """Ranks runners by a single feature so the scores are deterministic."""
    _form_analyst_model_id = 158
    _form_analyst_feature_names = ['horse_weight']

    def predict_proba(self, X):
        p = np.linspace(0.1, 0.4, len(X))
        return np.column_stack([1.0 - p, p])


@pytest.fixture
def scored_meeting(monkeypatch):
    monkeypatch.setattr(ml_predict, 'load_model', lambda: _Model())
    monkeypatch.setattr(ml_predict, '_model_feature_names', lambda _model: ['horse_weight'])

    def score(fail_on):
        session = _Session(fail_on)
        scores, by_race = ml_predict.predict_meeting(1962, session)
        return session, scores, by_race

    return score


def test_a_failing_odds_lookup_still_leaves_the_meeting_with_picks(scored_meeting):
    session, scores, by_race = scored_meeting('live_odds_snapshots')

    assert session.rollbacks >= 1, "the failed odds query must be rolled off the session"
    assert not session.aborted, "the session must be usable again before scoring continues"
    assert by_race, "every race lost its picks — the outage, not the degraded path"
    assert len(by_race) == 2
    assert len(scores) == 8


def test_the_degraded_run_matches_a_healthy_one(scored_meeting):
    """Degrading gracefully means the same picks, minus the live-odds blend —
    not fewer picks."""
    _healthy_session, healthy_scores, _ = scored_meeting('no such table')
    _broken_session, broken_scores, _ = scored_meeting('live_odds_snapshots')

    assert broken_scores == healthy_scores


@pytest.mark.parametrize('table', [
    'live_odds_snapshots',
    'strike_rates',
    'horse_form_scores',
    "csv_data->>'horse sire'",
])
def test_no_optional_lookup_can_poison_the_session(scored_meeting, table):
    """The odds table is the one that broke meeting 1962, but every optional
    lookup in predict_meeting shares the shape and would do the same."""
    session, scores, by_race = scored_meeting(table)

    assert session.rollbacks >= 1
    assert by_race, f"a failing {table} lookup took the whole meeting down"
    assert len(scores) == 8


def test_the_lookup_rolls_back_for_any_exception_not_just_a_missing_table():
    """_load_latest_live_odds_for_meeting is the unit under the outage."""

    class _Race101:
        id = 101

    class _Broken:
        def __init__(self):
            self.rollbacks = 0

        def execute(self, *_args, **_kwargs):
            raise OddsQueryFailure("statement timeout")

        def rollback(self):
            self.rollbacks += 1

    session = _Broken()
    assert ml_predict._load_latest_live_odds_for_meeting([_Race101()], session) == {}
    assert session.rollbacks == 1


def test_a_session_without_rollback_is_tolerated():
    """Plain objects (and already-dead connections) must not turn a degraded
    lookup back into an exception."""

    class _Race101:
        id = 101

    class _NoRollback:
        def execute(self, *_args, **_kwargs):
            raise OddsQueryFailure("boom")

    assert ml_predict._load_latest_live_odds_for_meeting([_Race101()], _NoRollback()) == {}
