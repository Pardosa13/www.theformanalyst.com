"""The ML scoring run must write value_edge_pct and kelly_stake_pct, not just ml_score.

Live odds were flowing into `live_odds_snapshots` and the model/market blend was
applying, yet `predictions.value_edge_pct` was NULL and `kelly_stake_pct` was 0
for every runner in every live race. Neither number was ever computed on the
scoring path: the value edge existed only inside the Best Bets request handler
and the Kelly stake only inside the ML meeting view, and BOTH derived their
market from a bookmaker fetched live in-request rather than from the snapshots
table. When that fetch returned nothing the columns silently stayed empty and
the pages rendered as though the market simply had no opinion.

These tests pin the fix at the level that failed: scoring a meeting prices it,
off the stored snapshots, and persists both numbers per runner.
"""

import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

pytest.importorskip("numpy")
pytest.importorskip("pandas")

if "sklearn" not in sys.modules:
    sys.modules["sklearn"] = types.ModuleType("sklearn")
    base = types.ModuleType("sklearn.base")
    base.BaseEstimator = type("BaseEstimator", (), {})
    base.RegressorMixin = type("RegressorMixin", (), {})
    base.clone = lambda estimator: estimator
    sys.modules["sklearn.base"] = base

try:  # pragma: no cover - depends only on what is installed
    import models  # noqa: F401
except Exception:  # pragma: no cover
    _models = types.ModuleType("models")
    for _name in ("Meeting", "Race", "Horse", "Prediction"):
        setattr(_models, _name, type(_name, (), {}))
    sys.modules["models"] = _models

import ml_predict


# ── derive_ml_fair_probabilities ─────────────────────────────────────────────

def test_fair_probabilities_are_each_runners_share_of_the_race():
    assert ml_predict.derive_ml_fair_probabilities([40.0, 30.0, 20.0, 10.0]) == [0.4, 0.3, 0.2, 0.1]


def test_a_runner_the_model_cannot_quote_is_none_and_leaves_the_book_alone():
    """ml_score is min-max normalised per race, so the bottom runner scores 0.
    It has no fair price, and it must not sit in the denominator either — the
    other runners' probabilities have to still sum to 1."""
    out = ml_predict.derive_ml_fair_probabilities([60.0, 40.0, 0.0, None, 'x'])
    assert out[2] is None and out[3] is None and out[4] is None
    assert out[0] == 0.6 and out[1] == 0.4
    assert sum(p for p in out if p is not None) == pytest.approx(1.0)


def test_a_race_with_no_usable_scores_yields_no_probabilities():
    assert ml_predict.derive_ml_fair_probabilities([0.0, None]) == [None, None]


# ── Fakes: just enough ORM surface for the pricing pass ──────────────────────

class _Prediction:
    def __init__(self, horse_id, ml_score):
        self.horse_id = horse_id
        self.ml_score = ml_score
        self.value_edge_pct = None
        self.value_edge_ml_win_prob_pct = None
        self.value_edge_price = None
        self.value_edge_captured_at = None
        self.kelly_stake_pct = None


class _Horse:
    def __init__(self, horse_id, ml_score, is_scratched=False):
        self.id = horse_id
        self.horse_name = f"Horse {horse_id}"
        self.is_scratched = is_scratched
        self.prediction = _Prediction(horse_id, ml_score)


class _Race:
    def __init__(self, race_id, horses):
        self.id = race_id
        self.race_number = race_id
        self.horses = horses


class _Query:
    def __init__(self, session, entity):
        self._session = session
        self._entity = entity
        self._filtered = None

    def filter_by(self, **_kwargs):
        return self

    def filter(self, criterion):
        self._filtered = criterion
        return self

    def all(self):
        if self._entity is _RaceEntity:
            return self._session.races
        return self._session.predictions


class _RaceEntity:
    pass


class _Column:
    """Just enough of a SQLAlchemy column to record an .in_() filter."""

    def in_(self, values):
        return set(values)


class _PredictionEntity:
    horse_id = _Column()


class _Session:
    def __init__(self, races, rows, predictions=None):
        self.races = races
        self.rows = rows
        if predictions is None:
            predictions = [horse.prediction for race in races for horse in race.horses]
        self.predictions = predictions

    def query(self, entity):
        return _Query(self, entity)

    def execute(self, _statement, _params=None):
        class _Result:
            def __init__(self, rows):
                self._rows = rows

            def mappings(self):
                return self

            def all(self):
                return self._rows

        return _Result(self.rows)


def _snapshot(horse_id, odds, age_seconds=60, is_scratched=False):
    return {
        'horse_id': horse_id,
        'odds': odds,
        'source': 'ladbrokes',
        'captured_at': datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        'is_scratched': is_scratched,
    }


def _one_race_meeting(monkeypatch, scores_and_prices, **kwargs):
    horses = [_Horse(i + 1, score) for i, (score, _price) in enumerate(scores_and_prices)]
    race = _Race(1, horses)
    rows = [
        _snapshot(i + 1, price, **kwargs)
        for i, (_score, price) in enumerate(scores_and_prices)
        if price is not None
    ]
    session = _Session([race], rows)
    monkeypatch.setattr(ml_predict, 'Race', _RaceEntity, raising=False)

    fake_models = types.ModuleType('models')
    fake_models.Race = _RaceEntity
    fake_models.Prediction = _PredictionEntity
    monkeypatch.setitem(sys.modules, 'models', fake_models)
    return session, race


# ── The pricing pass ─────────────────────────────────────────────────────────

def test_every_priced_runner_gets_an_edge_and_a_stake(monkeypatch):
    # 40/30/20/10 of a 100-point book -> fair 40/30/20/10%.
    # Prices imply 20/40/8.3/28.6% -> edges +20.0 / -10.0 / +11.67 / -18.57pp.
    session, _race = _one_race_meeting(monkeypatch, [
        (40.0, 5.00), (30.0, 2.50), (20.0, 12.00), (10.0, 3.50),
    ])
    edges, diagnostics = ml_predict.compute_live_market_edges_for_meeting(1, session)

    assert diagnostics['races_priced'] == 1
    assert diagnostics['runners_priced'] == 4
    assert diagnostics['runners_with_edge'] == 4
    assert diagnostics['races_failed'] == 0

    assert edges[1]['value_edge_pct'] == 20.0
    assert edges[2]['value_edge_pct'] == -10.0
    assert edges[3]['value_edge_pct'] == 11.67
    assert edges[4]['value_edge_pct'] == -18.57

    # The two runners the model rates above the market are the ones staked.
    assert edges[1]['kelly_stake_pct'] > 0
    assert edges[3]['kelly_stake_pct'] > 0
    assert edges[2]['kelly_stake_pct'] == 0.0
    assert edges[4]['kelly_stake_pct'] == 0.0


def test_a_negative_edge_is_recorded_rather_than_dropped(monkeypatch):
    """"The model rates this one below the market" is a real answer the ML Data
    buckets need — it is the control group the 20pp cutoff is judged against."""
    session, _race = _one_race_meeting(monkeypatch, [(30.0, 2.50), (70.0, 1.20)])
    edges, _diagnostics = ml_predict.compute_live_market_edges_for_meeting(1, session)
    assert edges[1]['value_edge_pct'] < 0
    assert edges[1]['kelly_stake_pct'] == 0.0


def test_a_runner_with_no_live_price_is_absent_not_zero(monkeypatch):
    """Absent must stay distinguishable from "priced, and worth nothing": the
    poller may simply not have reached this runner."""
    session, _race = _one_race_meeting(monkeypatch, [(40.0, 5.00), (30.0, None), (30.0, 4.00)])
    edges, diagnostics = ml_predict.compute_live_market_edges_for_meeting(1, session)
    assert 2 not in edges
    assert diagnostics['runners_priced'] == 2


def test_stale_snapshots_are_not_priced(monkeypatch):
    """A price older than LIVE_ODDS_MAX_AGE_SECONDS is not a live market, and
    pricing off one would stamp a stale edge onto the prediction row."""
    session, _race = _one_race_meeting(
        monkeypatch, [(40.0, 5.00), (60.0, 2.00)],
        age_seconds=ml_predict.LIVE_ODDS_MAX_AGE_SECONDS + 60,
    )
    edges, diagnostics = ml_predict.compute_live_market_edges_for_meeting(1, session)
    assert edges == {}
    assert diagnostics['races_priced'] == 0


def test_a_meeting_with_no_snapshots_prices_nothing_and_says_so(monkeypatch):
    session, _race = _one_race_meeting(monkeypatch, [(40.0, None), (60.0, None)])
    edges, diagnostics = ml_predict.compute_live_market_edges_for_meeting(1, session)
    assert edges == {}
    assert diagnostics['runners_priced'] == 0


def test_one_broken_race_does_not_cost_the_rest_of_the_card(monkeypatch):
    """A single unpriceable race is counted and logged, never allowed to abort
    the meeting — the failure mode that hid this bug was a swallowed exception,
    so the count has to come back to the caller."""
    good = _Race(1, [_Horse(1, 40.0), _Horse(2, 60.0)])

    class _ExplodingRace:
        id = 2
        race_number = 2

        @property
        def horses(self):
            raise RuntimeError('race row is unreadable')

    session = _Session(
        [good, _ExplodingRace()],
        [_snapshot(1, 5.00), _snapshot(2, 1.50)],
        predictions=[horse.prediction for horse in good.horses],
    )
    fake_models = types.ModuleType('models')
    fake_models.Race = _RaceEntity
    fake_models.Prediction = _PredictionEntity
    monkeypatch.setitem(sys.modules, 'models', fake_models)

    edges, diagnostics = ml_predict.compute_live_market_edges_for_meeting(1, session)
    assert diagnostics['races_failed'] == 1
    assert diagnostics['races_priced'] == 1
    assert set(edges) == {1, 2}


def test_scratched_runners_are_not_priced(monkeypatch):
    horses = [_Horse(1, 40.0), _Horse(2, 60.0), _Horse(3, 0.0, is_scratched=True)]
    race = _Race(1, horses)
    session = _Session([race], [_snapshot(1, 5.00), _snapshot(2, 1.50), _snapshot(3, 9.00)])
    fake_models = types.ModuleType('models')
    fake_models.Race = _RaceEntity
    fake_models.Prediction = _PredictionEntity
    monkeypatch.setitem(sys.modules, 'models', fake_models)

    edges, _diagnostics = ml_predict.compute_live_market_edges_for_meeting(1, session)
    assert 3 not in edges


# ── Persistence ──────────────────────────────────────────────────────────────

def test_persist_writes_both_columns_onto_the_prediction_rows(monkeypatch):
    session, race = _one_race_meeting(monkeypatch, [(40.0, 5.00), (30.0, 2.50), (30.0, 4.00)])
    edges, _diagnostics = ml_predict.compute_live_market_edges_for_meeting(1, session)
    updated = ml_predict.persist_live_market_edges(edges, session)

    assert updated == 3
    by_id = {p.horse_id: p for p in session.predictions}
    assert by_id[1].value_edge_pct == 20.0
    assert by_id[1].value_edge_price == 5.00
    assert by_id[1].value_edge_ml_win_prob_pct == 40.0
    assert by_id[1].value_edge_captured_at is not None
    assert by_id[1].kelly_stake_pct > 0
    # A priced runner the solver declines still gets an explicit zero, so a
    # stake struck at an older, longer price cannot linger on it.
    assert by_id[2].kelly_stake_pct == 0.0


def test_persist_is_a_no_op_when_nothing_was_priced(monkeypatch):
    session, _race = _one_race_meeting(monkeypatch, [(40.0, None)])
    assert ml_predict.persist_live_market_edges({}, session) == 0


# ── The scoring path actually calls it ───────────────────────────────────────

def test_the_ml_scoring_run_prices_and_persists_in_the_same_pass():
    """The bug was not a broken calculation, it was a calculation nobody ran:
    _score_meeting_ml persisted ml_score and stopped there."""
    source = Path('ml_shadow_routes.py').read_text()
    start = source.index('def _score_meeting_ml')
    end = source.index('def _reprice_meeting_market', start)
    scorer = source[start:end]

    assert 'compute_live_market_edges_for_meeting(' in scorer
    assert 'persist_live_market_edges(' in scorer
    assert 'scores_by_race=by_race' in scorer
    # Reported back to the caller, not just logged: "scored 96, priced 0" is the
    # exact state that went unnoticed, so the button has to be able to show it.
    assert "'priced': priced" in scorer
    assert "'market_error': market_error" in scorer


def test_a_pricing_failure_cannot_take_the_ml_scores_with_it():
    """On Postgres one failed statement aborts the whole transaction. Committing
    the scores and the pricing together would let a market read that could not
    complete destroy the scores too — the same class of failure that produced
    the empty columns in the first place."""
    source = Path('ml_shadow_routes.py').read_text()
    start = source.index('def _score_meeting_ml')
    end = source.index('def _reprice_meeting_market', start)
    scorer = source[start:end]

    scores_committed = scorer.index('db.session.commit()')
    pricing = scorer.index('compute_live_market_edges_for_meeting(')
    assert scores_committed < pricing, 'ml_score must be committed before pricing is attempted'
    # And a failed pricing pass must not hand the caller an aborted transaction.
    assert 'db.session.rollback()' in scorer[pricing:]


def test_an_already_scored_meeting_still_gets_repriced():
    """Prices move. A meeting the bulk pass skips because it already has
    ml_scores would otherwise keep the edge and stake it had (or never had)
    when it was first scored."""
    source = Path('ml_shadow_routes.py').read_text()
    start = source.index('def ml_shadow_score_visible')
    end = source.index("@app.route('/api/ml-shadow/results", start)
    bulk = source[start:end]
    assert '_reprice_meeting_market(db, meeting.id)' in bulk


def test_the_ml_meeting_view_prices_from_the_stored_snapshots_first():
    """The in-request bookmaker fetch is now the fallback, not the only path."""
    source = Path('app.py').read_text()
    start = source.index('def ml_view_meeting(')
    end = source.index('\nJURISDICTION_TRACKS', start)
    view = source[start:end]
    assert '_apply_stored_market_edges(results, meeting_id)' in view
    assert 'if race.get(\'race_number\') not in priced_race_numbers:' in view


def test_the_meeting_page_renders_the_stored_edge_rather_than_waiting_on_a_poll():
    template = Path('templates/MLRaceMeetings.html').read_text()
    assert 'horse.value_edge_pct is not none' in template
    assert 'data-edge="{{ horse.value_edge_pct }}"' in template
    # And the live-odds JS must not blank that server value when a poll returns
    # no price — doing so is what made the column look permanently empty.
    start = template.index('function updateEdgeCell(')
    end = template.index('function toggleEdgeHighlight(', start)
    no_price_branch = template[start:end]
    assert 'delete row.dataset.edge;' not in no_price_branch
