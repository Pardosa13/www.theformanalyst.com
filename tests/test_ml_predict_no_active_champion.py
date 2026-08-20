"""Live scoring must show no picks — never fall back to an unvalidated model.

A champion can be deactivated deliberately (model 81 was, after its headline
holdout ROI turned out to be contradicted by every one of its walk-forward
folds), and the nightly pipeline can legitimately end a run with no active
champion at all. What must NOT happen in that state is live scoring quietly
loading whatever pkl is sitting on disk and putting picks in front of a user:
that artifact never passed the promotion bar and may be the very model just
deactivated. No picks is a visible, harmless state; picks from an unvalidated
model look exactly like good ones until money is on them.
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

import ml_predict


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row):
        self._row = row
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, statement, params=None):
        self.queries.append(str(statement))
        return _Result(self._row)


class _Engine:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn


def _install_fake_db(monkeypatch, row):
    """Make load_model's `from sqlalchemy import ...` resolve to a fake DB
    that answers the active-champion query with `row`."""
    conn = _Connection(row)
    fake = types.ModuleType("sqlalchemy")
    fake.create_engine = lambda *a, **kw: _Engine(conn)
    fake.text = lambda sql: sql
    monkeypatch.setitem(sys.modules, "sqlalchemy", fake)
    return conn


def _forbid_artifact_load(monkeypatch):
    """Trip loudly if anything tries to unpickle an artifact."""
    fake_joblib = types.ModuleType("joblib")

    def _explode(*args, **kwargs):
        raise AssertionError("must not load any model artifact when there is no active champion")

    fake_joblib.load = _explode
    monkeypatch.setitem(sys.modules, "joblib", fake_joblib)


def test_load_model_raises_rather_than_falling_back_when_no_champion_is_active(monkeypatch):
    """The DB is reachable and simply has no active champion. That is a real,
    intended state — not a DB problem — so it must stop scoring outright."""
    conn = _install_fake_db(monkeypatch, row=None)
    _forbid_artifact_load(monkeypatch)

    with pytest.raises(ml_predict.NoActiveChampionError) as excinfo:
        ml_predict.load_model()

    assert conn.queries, "the active-champion query must actually have been run"
    assert "is_active = TRUE" in conn.queries[0]
    message = str(excinfo.value)
    assert "No active champion" in message
    assert "on-disk artifact" in message, "the message must say why it did not fall back"


def test_load_model_raises_when_the_active_row_has_no_stored_artifact(monkeypatch):
    """An active row whose pkl_data is NULL cannot score a race either — the
    same no-picks answer, not a filesystem fallback."""
    row = [42, 900, "2026-08-01", 12.0, None, None, "xgboost", "XGB", True, "20260801", "a.pkl", 207, None]
    _install_fake_db(monkeypatch, row=row)
    _forbid_artifact_load(monkeypatch)

    with pytest.raises(ml_predict.NoActiveChampionError):
        ml_predict.load_model()


def test_a_genuinely_unreachable_db_still_falls_back_for_local_dev(monkeypatch):
    """The filesystem fallback exists so local dev works without a DB. That
    path is untouched: an unreachable DB is a different condition from a
    reachable DB that reports no champion, and only the latter means no picks.
    """
    fake = types.ModuleType("sqlalchemy")

    def _boom(*args, **kwargs):
        raise RuntimeError("could not connect")

    fake.create_engine = _boom
    fake.text = lambda sql: sql
    monkeypatch.setitem(sys.modules, "sqlalchemy", fake)
    monkeypatch.setattr(ml_predict.os.path, "exists", lambda path: False)

    # FileNotFoundError, not NoActiveChampionError: nothing has told us the
    # champion is absent — we simply could not ask.
    with pytest.raises(FileNotFoundError):
        ml_predict.load_model()


def test_predict_meeting_propagates_the_no_champion_state_to_its_caller():
    """predict_meeting swallows a missing artifact ({} scores) but must NOT
    swallow this: callers have to be able to tell "no champion, show no picks"
    apart from "scored zero horses", because they look identical downstream."""
    source = ml_predict.__file__ and open(ml_predict.__file__).read()
    start = source.index("def predict_meeting(")
    end = source.index("\ndef ", start + 1)
    body = source[start:end]

    assert "except NoActiveChampionError:" in body
    assert "raise" in body.split("except NoActiveChampionError:")[1].split("except FileNotFoundError")[0]
    assert "ML_NO_ACTIVE_CHAMPION" in body


def test_a_champion_whose_feature_count_disagrees_with_its_artifact_stops_scoring(monkeypatch):
    """The stored expected_feature_count and the artifact's own feature list
    must agree. When they don't, the champion is unusable — and the answer is
    no picks, not the on-disk artifact, which has passed nothing either."""
    class _Artifact:
        feature_names_in_ = ["horse_age", "horse_weight"]

    row = [7, 900, "2026-08-01", 12.0, None, b"artifact-bytes", "xgboost", "XGB",
           True, "20260801", "a.pkl", 207, None]
    _install_fake_db(monkeypatch, row=row)

    fake_joblib = types.ModuleType("joblib")
    fake_joblib.load = lambda buf: _Artifact()
    monkeypatch.setitem(sys.modules, "joblib", fake_joblib)
    monkeypatch.setattr(
        ml_predict.os.path, "exists",
        lambda path: (_ for _ in ()).throw(AssertionError("must not reach the filesystem fallback")),
    )

    with pytest.raises(ml_predict.UnusableActiveChampionError) as excinfo:
        ml_predict.load_model()

    # It is a NoActiveChampionError too, so every caller that already handles
    # "no champion, show no picks" handles this without a second code path.
    assert isinstance(excinfo.value, ml_predict.NoActiveChampionError)
    assert "feature count mismatch" in str(excinfo.value)
