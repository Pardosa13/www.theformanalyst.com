"""
tests/test_ml_model_cache.py

Unit tests for the in-memory model cache in ml_predict.load_model().

The tests mock out filesystem access and the DB so no real model file or
database connection is required.
"""
import importlib
import io
import os
import sys
import types
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Minimal stub for a scikit-learn style model used as a stand-in .pkl object
# ---------------------------------------------------------------------------
class _FakeModel:
    """Thin stand-in for a trained sklearn estimator."""
    pass


# ---------------------------------------------------------------------------
# Helper – reset module-level cache between tests
# ---------------------------------------------------------------------------
def _reset_cache():
    import ml_predict
    ml_predict._cached_model = None
    ml_predict._cache_fingerprint = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestFilesystemCache(unittest.TestCase):
    """Model loaded from the local .pkl file (filesystem path)."""

    def setUp(self):
        _reset_cache()

    def _make_joblib_mock(self, model):
        jl = MagicMock()
        jl.load.return_value = model
        return jl

    def test_first_call_loads_and_caches(self):
        import ml_predict
        model_obj = _FakeModel()

        with patch('os.path.exists', return_value=True), \
             patch('os.path.getmtime', return_value=1000.0), \
             patch.dict(sys.modules, {'joblib': self._make_joblib_mock(model_obj)}):
            result = ml_predict.load_model()

        self.assertIs(result, model_obj)
        self.assertIs(ml_predict._cached_model, model_obj)
        self.assertEqual(ml_predict._cache_fingerprint, 1000.0)

    def test_second_call_with_same_mtime_returns_cache(self):
        import ml_predict
        model_obj = _FakeModel()
        jl_mock = self._make_joblib_mock(model_obj)

        with patch('os.path.exists', return_value=True), \
             patch('os.path.getmtime', return_value=1000.0), \
             patch.dict(sys.modules, {'joblib': jl_mock}):
            ml_predict.load_model()
            result = ml_predict.load_model()

        self.assertIs(result, model_obj)
        # joblib.load should have been called only once
        self.assertEqual(jl_mock.load.call_count, 1)

    def test_mtime_change_triggers_reload(self):
        import ml_predict
        old_model = _FakeModel()
        new_model = _FakeModel()

        mtimes = [1000.0, 2000.0]
        loaded = [old_model, new_model]
        call_count = [0]

        def fake_load(_path):
            idx = call_count[0]
            call_count[0] += 1
            return loaded[idx]

        jl_mock = MagicMock()
        jl_mock.load.side_effect = fake_load

        with patch('os.path.exists', return_value=True), \
             patch('os.path.getmtime', side_effect=mtimes), \
             patch.dict(sys.modules, {'joblib': jl_mock}):
            first = ml_predict.load_model()
            second = ml_predict.load_model()

        self.assertIs(first, old_model)
        self.assertIs(second, new_model)
        self.assertEqual(jl_mock.load.call_count, 2)


class TestDBCache(unittest.TestCase):
    """Model loaded from Postgres (no filesystem .pkl present)."""

    def setUp(self):
        _reset_cache()

    def _make_engine_mock(self, pkl_bytes, run_date, updated_at):
        """Build a minimal SQLAlchemy engine mock that returns the given data."""
        conn_mock = MagicMock()
        # fingerprint query (run_date, updated_at)
        fp_row = MagicMock()
        fp_row.__getitem__ = lambda self, i: (run_date, updated_at)[i]

        # data query (pkl_data)
        data_row = MagicMock()
        data_row.__getitem__ = lambda self, i: pkl_bytes

        # execute() is called twice: once for fingerprint, once for data
        conn_mock.execute.side_effect = [
            MagicMock(fetchone=MagicMock(return_value=fp_row)),
            MagicMock(fetchone=MagicMock(return_value=data_row)),
        ]

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn_mock)
        ctx.__exit__ = MagicMock(return_value=False)

        eng = MagicMock()
        eng.connect.return_value = ctx
        return eng, conn_mock

    def _pickle_model(self, model):
        """Serialize a model object to bytes using joblib."""
        try:
            import joblib
            buf = io.BytesIO()
            joblib.dump(model, buf)
            return buf.getvalue()
        except Exception:
            return b'fake_pkl_bytes'

    def test_first_call_loads_from_db_and_caches(self):
        import ml_predict
        model_obj = _FakeModel()
        pkl = self._pickle_model(model_obj)
        rd = date(2025, 1, 1)
        ua = datetime(2025, 1, 1, 12, 0, 0)

        # Patch the entire _db_fingerprint + engine to avoid real DB calls
        fp = (rd, ua)
        with patch('os.path.exists', return_value=False), \
             patch.dict(os.environ, {'DATABASE_URL': 'postgresql://fake'}), \
             patch('ml_predict._db_fingerprint', return_value=fp):

            # Patch create_engine + joblib to fake the full load path
            eng_mock = MagicMock()
            conn_mock = MagicMock()
            data_row = MagicMock()
            data_row.__getitem__ = lambda self, i: pkl
            conn_mock.execute.return_value = MagicMock(
                fetchone=MagicMock(return_value=data_row)
            )
            ctx = MagicMock()
            ctx.__enter__ = MagicMock(return_value=conn_mock)
            ctx.__exit__ = MagicMock(return_value=False)
            eng_mock.connect.return_value = ctx

            jl_mock = MagicMock()
            jl_mock.load.return_value = model_obj

            sa_mock = MagicMock()
            sa_mock.create_engine.return_value = eng_mock
            sa_mock.text.return_value = 'sql'

            with patch.dict(sys.modules, {'sqlalchemy': sa_mock, 'joblib': jl_mock}):
                result = ml_predict.load_model()

        self.assertIs(result, model_obj)
        self.assertIs(ml_predict._cached_model, model_obj)
        self.assertEqual(ml_predict._cache_fingerprint, fp)

    def test_second_call_same_fingerprint_returns_cache_without_db_hit(self):
        import ml_predict
        model_obj = _FakeModel()
        rd = date(2025, 1, 1)
        ua = datetime(2025, 1, 1, 12, 0, 0)
        fp = (rd, ua)

        # Pre-populate cache
        ml_predict._cached_model = model_obj
        ml_predict._cache_fingerprint = fp

        call_count = [0]

        def fake_db_fingerprint(_db_url):
            call_count[0] += 1
            return fp

        with patch('os.path.exists', return_value=False), \
             patch.dict(os.environ, {'DATABASE_URL': 'postgresql://fake'}), \
             patch('ml_predict._db_fingerprint', side_effect=fake_db_fingerprint):
            result = ml_predict.load_model()

        self.assertIs(result, model_obj)
        # _db_fingerprint was called to check freshness but no full reload
        self.assertEqual(call_count[0], 1)

    def test_fingerprint_change_triggers_db_reload(self):
        import ml_predict
        old_model = _FakeModel()
        new_model = _FakeModel()
        old_fp = (date(2025, 1, 1), datetime(2025, 1, 1, 12, 0, 0))
        new_fp = (date(2025, 1, 2), datetime(2025, 1, 2, 8, 0, 0))

        # Pre-populate cache with old model
        ml_predict._cached_model = old_model
        ml_predict._cache_fingerprint = old_fp

        pkl = self._pickle_model(new_model)

        eng_mock = MagicMock()
        conn_mock = MagicMock()
        data_row = MagicMock()
        data_row.__getitem__ = lambda self, i: pkl
        conn_mock.execute.return_value = MagicMock(
            fetchone=MagicMock(return_value=data_row)
        )
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn_mock)
        ctx.__exit__ = MagicMock(return_value=False)
        eng_mock.connect.return_value = ctx

        jl_mock = MagicMock()
        jl_mock.load.return_value = new_model

        sa_mock = MagicMock()
        sa_mock.create_engine.return_value = eng_mock
        sa_mock.text.return_value = 'sql'

        with patch('os.path.exists', return_value=False), \
             patch.dict(os.environ, {'DATABASE_URL': 'postgresql://fake'}), \
             patch('ml_predict._db_fingerprint', return_value=new_fp), \
             patch.dict(sys.modules, {'sqlalchemy': sa_mock, 'joblib': jl_mock}):
            result = ml_predict.load_model()

        self.assertIs(result, new_model)
        self.assertEqual(ml_predict._cache_fingerprint, new_fp)

    def test_raises_when_no_model_available(self):
        import ml_predict

        with patch('os.path.exists', return_value=False), \
             patch.dict(os.environ, {'DATABASE_URL': 'postgresql://fake'}), \
             patch('ml_predict._db_fingerprint', return_value=None):

            sa_mock = MagicMock()
            sa_mock.create_engine.side_effect = Exception("no DB")

            with patch.dict(sys.modules, {'sqlalchemy': sa_mock}):
                with self.assertRaises(FileNotFoundError):
                    ml_predict.load_model()


if __name__ == '__main__':
    unittest.main()
