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
    def __init__(self, fetchone=(0,), fetchall=None):
        self.statements = []
        self.rowcount = 0
        self._fetchone = fetchone
        self._fetchall = list(fetchall or [])

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

    def fetchall(self):
        return self._fetchall


class _FakeConn:
    def __init__(self, fetchone=(0,), fetchall=None):
        self.cursor_obj = _FakeCursor(fetchone=fetchone, fetchall=fetchall)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _fight(uid, f1="A", f2="B", complete=True, status=""):
    return {
        "bout_uid": uid,
        "fighter_1": f1,
        "fighter_2": f2,
        "status": status,
        "_source_complete": complete,
    }


def test_empty_espn_response_does_not_deactivate_valid_fights():
    conn = _FakeConn(fetchone=(10,), fetchall=[(21,)])

    complete = mma_sync.event_card_fetch_is_complete(conn, "espn-event-1", [])
    deleted = mma_sync.deactivate_stale_event_bouts(conn, "espn-event-1", set(), complete)

    assert complete is False
    assert deleted == 0
    statements = "\n".join(sql for sql, _ in conn.cursor_obj.statements)
    assert "UPDATE mma_fights" not in statements


def test_partial_espn_response_does_not_deactivate_valid_fights():
    conn = _FakeConn(fetchone=(10,), fetchall=[(21,)])

    complete = mma_sync.event_card_fetch_is_complete(conn, "espn-event-1", [_fight("a")])
    deleted = mma_sync.deactivate_stale_event_bouts(conn, "espn-event-1", {"espn:espn-event-1:a"}, complete)

    assert complete is False
    assert deleted == 0
    statements = "\n".join(sql for sql, _ in conn.cursor_obj.statements)
    assert "UPDATE mma_fights" not in statements


def test_stale_fights_deactivate_after_complete_successful_sync():
    fights = [_fight(str(i), f"A{i}", f"B{i}") for i in range(1, 6)]
    conn = _FakeConn(fetchone=(5,), fetchall=[(21,), (22,), (23,)])

    complete = mma_sync.event_card_fetch_is_complete(conn, "espn-event-1", fights)
    deleted = mma_sync.deactivate_stale_event_bouts(
        conn,
        "espn-event-1",
        {"espn:espn-event-1:1", "espn:espn-event-1:2"},
        complete,
    )

    assert complete is True
    assert deleted == 3
    assert conn.committed is True
    statements = "\n".join(sql for sql, _ in conn.cursor_obj.statements)
    assert "WHERE event_id = %s" in statements
    assert "bout_uid <> ALL" in statements
    assert "DELETE FROM mma_predictions" in statements


def test_replacement_bouts_leave_exactly_one_active_matchup():
    conn = _FakeConn(fetchall=[(99,)])

    deactivated = mma_sync.deactivate_duplicate_active_matchups(
        conn,
        "espn-event-1",
        100,
        "espn:espn-event-1:new",
        "fid-a",
        "fid-b",
        "Fighter A",
        "Fighter B",
    )

    assert deactivated == 1
    statements = "\n".join(sql for sql, _ in conn.cursor_obj.statements)
    assert "id <> %s" in statements
    assert "bout_uid <> %s" in statements
    assert "COALESCE(is_active, TRUE) = TRUE" in statements
    assert "DELETE FROM mma_predictions" in statements


def test_no_prediction_for_tba_or_insufficient_data_fights():
    assert mma_sync.fight_status({"fighter_1": "TBA", "fighter_2": "Real Fighter"}) == "placeholder"
    assert mma_sync.has_sufficient_feature_data(None, {}) is False
    assert mma_sync.has_sufficient_feature_data("f1", {}) is False
    assert mma_sync.has_sufficient_feature_data("f1", {"f1": mma_sync.FighterStats()}) is False

    stats = mma_sync.FighterStats()
    stats.total_fights = 1
    assert mma_sync.has_sufficient_feature_data("f1", {"f1": stats}) is True


def test_espn_summary_404_is_warning_not_fatal(monkeypatch, caplog):
    def fake_get(*args, **kwargs):
        return SimpleNamespace(status_code=404)

    monkeypatch.setattr(mma_sync.requests, "get", fake_get)

    with caplog.at_level("WARNING", logger="mma_sync"):
        fights = mma_sync._fetch_espn_event_api("401999999")

    assert fights == []
    assert "returned 404" in caplog.text
    assert "continuing with fallback sources" in caplog.text


def test_canonical_bout_uid_prefers_official_identifier_over_names():
    uid1 = mma_sync.canonical_bout_uid("401", {"bout_uid": "abc", "fighter_1": "A", "fighter_2": "B"})
    uid2 = mma_sync.canonical_bout_uid("401", {"bout_uid": "abc", "fighter_1": "C", "fighter_2": "D"})

    assert uid1 == "espn:401:abc"
    assert uid2 == uid1



class _FakeTextNode:
    def __init__(self, text):
        self.text = text

    def strip(self):
        return self.text.strip()

    def __str__(self):
        return self.text


class _FakeHeading:
    def __init__(self, name, text, following='', href=''):
        self.name = name
        self._text = text
        self.next_siblings = [_FakeTextNode(following)] if following else []
        self._href = href

    def get_text(self, *args, **kwargs):
        return self._text

    def find(self, tag, href=False):
        if tag == 'a' and href and self._href:
            return {'href': self._href}
        return None


class _FakeSoup:
    def __init__(self, headings):
        self._headings = headings

    def find_all(self, tags):
        return [h for h in self._headings if h.name in tags]


def test_current_espn_fightcenter_heading_dom_parser():
    soup = _FakeSoup([
        _FakeHeading('h3', 'Main Card'),
        _FakeHeading('h2', 'Welterweight - Main Event'),
        _FakeHeading('h2', 'Conor McGregor', '22-6-0', '/mma/fighter/_/id/3022677/conor-mcgregor'),
        _FakeHeading('h2', 'Max Holloway', '27-9-0', 'https://www.espn.com/mma/fighter/_/id/2614933/max-holloway'),
        _FakeHeading('h2', 'Lightweight'),
        _FakeHeading('h2', 'Benoît Saint Denis', '17-3-0'),
        _FakeHeading('h2', 'Paddy Pimblett', '23-4-0'),
        _FakeHeading('h3', 'Prelims'),
        _FakeHeading('h2', 'Heavyweight'),
        _FakeHeading('h2', 'Gable Steveson', '3-0-0'),
        _FakeHeading('h2', 'Elisha Ellison', '5-2-0'),
    ])

    fights = mma_sync._parse_espn_fightcenter_heading_dom(soup)

    assert [(f["fighter_1"], f["fighter_2"]) for f in fights] == [
        ("Conor McGregor", "Max Holloway"),
        ("Benoît Saint Denis", "Paddy Pimblett"),
        ("Gable Steveson", "Elisha Ellison"),
    ]
    assert fights[0]["weight_class"] == "Welterweight - Main Event"
    assert fights[0]["fighter_1_url"] == "https://www.espn.com/mma/fighter/_/id/3022677/conor-mcgregor"
    assert fights[1]["is_main_card"] is True
    assert fights[2]["is_main_card"] is False
    assert all(f["_source_complete"] for f in fights)
