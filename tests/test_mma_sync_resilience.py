import sys
import types
from types import SimpleNamespace


def _install_import_stubs():
    for name in ["requests", "bs4", "pandas", "numpy", "joblib", "psycopg2"]:
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["requests"].get = lambda *args, **kwargs: None
    sys.modules["bs4"].BeautifulSoup = lambda *args, **kwargs: None
    sys.modules["psycopg2"].connect = lambda *args, **kwargs: None
    extras = types.ModuleType("psycopg2.extras")
    extras.execute_values = lambda *args, **kwargs: None
    sys.modules.setdefault("psycopg2.extras", extras)


_install_import_stubs()
import mma_sync


class _FakeCursor:
    def __init__(self):
        self.statements = []
        self.rowcount = 0
        self._fetchone = (2,)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if sql.lstrip().upper().startswith("DELETE"):
            self.rowcount = 3

    def fetchone(self):
        return self._fetchone


class _FakeConn:
    def __init__(self):
        self.cursor_obj = _FakeCursor()
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


def test_prune_event_fights_skips_fights_with_predictions():
    conn = _FakeConn()

    deleted = mma_sync.prune_event_fights(conn, "espn-event-1", [10, 11])

    assert deleted == 3
    assert conn.committed is True
    statements = "\n".join(sql for sql, _ in conn.cursor_obj.statements)
    assert "EXISTS" in statements
    assert "mma_predictions" in statements
    assert "NOT EXISTS" in statements
    assert conn.cursor_obj.statements[-1][1] == ("espn-event-1", [10, 11])


def test_prune_event_fights_preserves_existing_card_when_source_returns_zero():
    conn = _FakeConn()

    deleted = mma_sync.prune_event_fights(conn, "espn-event-1", [])

    assert deleted == 0
    assert conn.cursor_obj.statements == []
    assert conn.committed is False


def test_espn_summary_404_is_warning_not_fatal(monkeypatch, caplog):
    def fake_get(*args, **kwargs):
        return SimpleNamespace(status_code=404)

    monkeypatch.setattr(mma_sync.requests, "get", fake_get)

    with caplog.at_level("WARNING", logger="mma_sync"):
        fights = mma_sync._fetch_espn_event_api("401999999")

    assert fights == []
    assert "returned 404" in caplog.text
    assert "continuing with fallback sources" in caplog.text
