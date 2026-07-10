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


def test_aws_waf_challenge_detection():
    html = "<html><script>window.awsWafCookieDomainList=[]; window.gokuProps={}</script>Please enable JavaScript</html>"
    assert mma_sync.is_espn_waf_challenge(html, {"status": 202}) is True


def test_scrape_event_details_aborts_dom_parsers_on_waf(monkeypatch, caplog):
    monkeypatch.setattr(mma_sync, "_fetch_espn_scoreboard_card", lambda event_id: [])
    monkeypatch.setattr(mma_sync, "_fetch_espn_event_api", lambda event_id: [])

    def fail_heading(_soup):
        raise AssertionError("heading parser must not run for AWS WAF challenge HTML")

    monkeypatch.setattr(mma_sync, "_parse_espn_fightcenter_heading_dom", fail_heading)

    class _FakeResponse:
        status_code = 202
        url = "https://www.espn.com/mma/fightcenter/_/id/401"
        headers = {"content-type": "text/html"}
        content = b"<html><script>window.awsWafCookieDomainList=[]; window.gokuProps={}</script>Please enable JavaScript</html>"
        encoding = "utf-8"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(mma_sync.requests, "get", lambda *args, **kwargs: _FakeResponse())

    with caplog.at_level("WARNING", logger="mma_sync"):
        fights = mma_sync.scrape_event_details("https://www.espn.com/mma/fightcenter/_/id/401", "401")

    assert fights == []
    assert "ESPN_WAF_CHALLENGE=true" in caplog.text
    assert "aborting all Fightcenter DOM parsers" in caplog.text


def test_scoreboard_json_bout_extraction(monkeypatch):
    event = {
        "id": "401",
        "date": "2026-11-14T00:00Z",
        "status": {"type": {"state": "pre"}},
        "links": [{"href": "https://www.espn.com/mma/fightcenter/_/id/401"}],
        "competitions": [
            {
                "id": "9001",
                "competitors": [
                    {"homeAway": "away", "athlete": {"id": "1", "displayName": "Fighter A", "links": [{"href": "https://www.espn.com/mma/fighter/_/id/1/a"}]}},
                    {"homeAway": "home", "athlete": {"id": "2", "displayName": "Fighter B", "links": [{"href": "https://www.espn.com/mma/fighter/_/id/2/b"}]}},
                ],
                "status": {"type": {"state": "pre", "name": "STATUS_SCHEDULED"}},
                "type": {"text": "Main Card"},
                "weightClass": {"displayName": "Lightweight"},
            }
        ],
    }
    monkeypatch.setattr(mma_sync, "_fetch_espn_scoreboard_event", lambda event_id: (event, "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard?limit=100&dates=20260101-20261231"))
    monkeypatch.setattr(mma_sync, "_enrich_fights_with_profiles", lambda fights: fights)

    fights = mma_sync._fetch_espn_scoreboard_card("401")

    assert [(f["fighter_1"], f["fighter_2"]) for f in fights] == [("Fighter A", "Fighter B")]
    assert fights[0]["bout_uid"] == "9001"
    assert fights[0]["card_source"] == "espn_scoreboard_json"
    assert fights[0]["verified"] is True


def test_no_odds_api_only_active_card_creation_from_empty_canonical_sources(monkeypatch):
    monkeypatch.setattr(mma_sync, "_fetch_espn_scoreboard_card", lambda event_id: [])
    monkeypatch.setattr(mma_sync, "_fetch_espn_event_api", lambda event_id: [])
    monkeypatch.setattr(mma_sync, "_fetch_espn_event_html", lambda event_url: (None, "", {"status": None}))

    fights = mma_sync.scrape_event_details("https://www.espn.com/mma/fightcenter/_/id/401", "401")

    assert fights == []


def test_upsert_fight_marks_canonical_bouts_verified():
    conn = _FakeConn(fetchone=(123,))
    fight_id = mma_sync.upsert_fight(conn, "401", _fight("official-1"), commit=False)

    assert fight_id == 123
    sql, params = conn.cursor_obj.statements[-1]
    assert "card_source, verified" in sql
    assert "card_source = EXCLUDED.card_source" in sql
    assert params[4] == "espn_scoreboard_json"
    assert params[5] is True


def test_provenance_cleanup_only_targets_unverified_or_odds_rows():
    conn = _FakeConn(fetchall=[(10,), (11,)])

    count = mma_sync.deactivate_unverified_event_bouts(conn, "401")

    assert count == 2
    statements = "\n".join(sql for sql, _ in conn.cursor_obj.statements)
    assert "card_source = 'odds_api'" in statements
    assert "COALESCE(verified, FALSE) = FALSE" in statements
    assert "fighter_1_name" not in statements
    assert "fighter_2_name" not in statements
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

    def get(self, key, default=None):
        return default


class _FakeSoup:
    def __init__(self, headings):
        self._headings = headings

    def find_all(self, tags):
        if tags == "script":
            return []
        if tags == "title":
            return []
        if isinstance(tags, str):
            tags = [tags]
        return [h for h in self._headings if h.name in tags]

    def find(self, tag):
        return None

    def select(self, selector):
        return []

    def get_text(self, *args, **kwargs):
        return " ".join(h.get_text() for h in self._headings)


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


def test_scrape_event_details_reaches_heading_parser_after_legacy_zero(monkeypatch, caplog):
    soup = _FakeSoup([
        _FakeHeading('h3', 'Main Card'),
        _FakeHeading('h2', 'Welterweight - Main Event'),
        _FakeHeading('h2', 'Conor McGregor', '22-6-0'),
        _FakeHeading('h2', 'Max Holloway', '27-9-0'),
    ])

    class _FakeResponse:
        status_code = 200
        url = "https://www.espn.com/mma/fightcenter/_/id/401"
        headers = {"content-type": "text/html; charset=utf-8"}
        content = b"<html><body>fightcenter</body></html>"
        encoding = "utf-8"

        def raise_for_status(self):
            return None

        def json(self):
            return {}

    monkeypatch.setattr(mma_sync, "_fetch_espn_scoreboard_card", lambda event_id: [])
    monkeypatch.setattr(mma_sync, "_fetch_espn_event_api", lambda event_id: [])
    monkeypatch.setattr(mma_sync.requests, "get", lambda *args, **kwargs: _FakeResponse())
    monkeypatch.setattr(mma_sync, "BeautifulSoup", lambda *args, **kwargs: soup)

    with caplog.at_level("INFO", logger="mma_sync"):
        fights = mma_sync.scrape_event_details(
            "https://www.espn.com/mma/fightcenter/_/id/401", "401"
        )

    assert [(f["fighter_1"], f["fighter_2"]) for f in fights] == [
        ("Conor McGregor", "Max Holloway")
    ]
    assert "ESPN_PARSER parser=legacy_dom invoked=true" in caplog.text
    assert "ESPN_HEADING_PARSER invoked=true" in caplog.text
    assert "bouts=1" in caplog.text


def test_ufc329_scoreboard_array_index_order_stores_main_event_last_as_highest_order():
    competitions = [
        {
            "id": "prelim-1",
            "competitors": [
                {"homeAway": "away", "athlete": {"displayName": "Prelim Fighter A"}},
                {"homeAway": "home", "athlete": {"displayName": "Prelim Fighter B"}},
            ],
            "type": {"text": "Prelims"},
        },
        {
            "id": "main-1",
            "competitors": [
                {"homeAway": "away", "athlete": {"displayName": "Co Main A"}},
                {"homeAway": "home", "athlete": {"displayName": "Co Main B"}},
            ],
            "type": {"text": "Main Card"},
        },
        {
            "id": "ufc329-main-event",
            "competitors": [
                {"homeAway": "away", "athlete": {"displayName": "Max Holloway"}},
                {"homeAway": "home", "athlete": {"displayName": "Conor McGregor"}},
            ],
            "type": {"text": "Main Card"},
            "weightClass": {"displayName": "Welterweight - Main Event"},
        },
    ]

    fights = mma_sync._parse_espn_competitions(competitions)

    assert [(f["fighter_1"], f["fighter_2"], f["bout_order"]) for f in fights] == [
        ("Prelim Fighter A", "Prelim Fighter B", 0),
        ("Co Main A", "Co Main B", 1),
        ("Max Holloway", "Conor McGregor", 2),
    ]
    assert sorted(fights, key=lambda f: f["bout_order"], reverse=True)[0]["fighter_1"] == "Max Holloway"


def test_mma_events_api_uses_card_section_then_bout_order_desc_for_all_frontends():
    route_source = open("mma_routes.py", encoding="utf-8").read()

    assert "f.card_section, f.bout_order" in route_source
    assert "WHEN 'main_card' THEN 1" in route_source
    assert "WHEN 'prelims' THEN 2" in route_source
    assert "WHEN 'early_prelims' THEN 3" in route_source
    assert "f.bout_order DESC NULLS LAST" in route_source
    assert "f.id ASC" in route_source


def test_mma_template_preserves_api_order_within_card_sections():
    template = open("templates/mma.html", encoding="utf-8").read()

    assert ".sort(" not in template
    assert ".reverse(" not in template
    assert "mainCard.map(f => fightCardHTML(f))" in template
    assert "prelims.map(f => fightCardHTML(f))" in template
    assert "earlyPrelims.map(f => fightCardHTML(f))" in template


def test_suffix_accent_and_common_aliases_match_existing_fighters():
    name_to_id = {
        mma_sync.normalize_name("Kai Kamaka"): "kai-id",
        mma_sync.normalize_name("Zachary Reese"): "reese-id",
        mma_sync.normalize_name("Benoit Saint-Denis"): "bsd-id",
        mma_sync.normalize_name("Bobby Green"): "green-id",
        mma_sync.normalize_name("Loneer Kavanagh"): "kav-id",
    }

    assert mma_sync.resolve_fighter_id("Kai Kamaka III", name_to_id) == "kai-id"
    assert mma_sync.resolve_fighter_id("Zach Reese", name_to_id) == "reese-id"
    assert mma_sync.resolve_fighter_id("Benoît Saint Denis", name_to_id) == "bsd-id"
    assert mma_sync.resolve_fighter_id("King Green", name_to_id) == "green-id"
    assert mma_sync.resolve_fighter_id("Lone’er Kavanagh", name_to_id) == "kav-id"


def test_prediction_gate_reports_each_failed_condition():
    reasons = mma_sync.prediction_gate_reasons(
        {"fighter_1": "TBA", "fighter_2": "New Fighter"}, None, None, {}
    )
    assert "TBA fighter" in reasons
    assert "inactive fight" in reasons
    assert any(r.startswith("missing fighter database row") for r in reasons)


def test_valid_historical_stats_produce_prediction_gate_eligibility():
    s1 = mma_sync.FighterStats(); s1.total_fights = 2
    s2 = mma_sync.FighterStats(); s2.total_fights = 3
    fight = {"fighter_1": "A", "fighter_2": "B", "fighter_1_espn_id": "1", "fighter_2_espn_id": "2"}
    assert mma_sync.prediction_gate_reasons(fight, "a", "b", {"a": s1, "b": s2}) == []


def test_truly_new_fighters_remain_prediction_unavailable():
    fight = {"fighter_1": "New A", "fighter_2": "New B"}
    reasons = mma_sync.prediction_gate_reasons(fight, None, None, {})
    assert any("missing fighter database row" in r for r in reasons)


def test_reversed_fighter_order_still_matches_odds_pair():
    from mma_data import pairs_match
    assert pairs_match("Fighter A", "Fighter B", "Fighter B", "Fighter A") is True


def test_canonical_espn_fights_can_receive_odds_prices_by_alias_pair():
    from mma_data import pairs_match, names_match
    assert pairs_match("Benoît Saint Denis", "King Green", "Bobby Green", "Benoit Saint-Denis") is True
    assert names_match("Kai Kamaka III", "Kai Kamaka") is True


def test_odds_api_never_creates_standalone_active_fight_contract():
    from pathlib import Path
    src = Path("mma_models.py").read_text()
    fn = src[src.index("def upsert_mma_fight_odds"): ]
    assert "INSERT INTO mma_fight_odds" in fn
    assert "INSERT INTO mma_fights" not in fn
