"""UFC value picks: saved once before the fight, settled from the stored result."""
from datetime import date, datetime, timedelta

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text

import mma_tracking


@pytest.fixture
def conn():
    engine = create_engine("sqlite://")
    with engine.begin() as c:
        c.execute(text("CREATE TABLE mma_events (id TEXT PRIMARY KEY, name TEXT, date DATE, is_completed BOOLEAN)"))
        c.execute(text("""CREATE TABLE mma_fights (id INTEGER PRIMARY KEY, event_id TEXT, fighter_1_name TEXT,
                          fighter_2_name TEXT, winner_name TEXT, is_active BOOLEAN)"""))
        c.execute(text("INSERT INTO mma_events VALUES ('e1', 'UFC Test', :d, 0)"), {"d": date(2026, 11, 14)})
        c.execute(text("INSERT INTO mma_fights VALUES (1, 'e1', 'Alpha One', 'Bravo Two', NULL, 1)"))
        c.execute(text("INSERT INTO mma_fights VALUES (2, 'e1', 'Charlie Three', 'Delta Four', NULL, 1)"))
        yield c


def _bet(fight_id, fighter, opponent, start, recommendation="value", odds=3.7):
    return {"fight_id": fight_id, "fighter": fighter, "opponent": opponent, "event_name": "UFC Test",
            "event_date": "2026-11-14", "odds": odds, "bookmaker": "Sportsbet", "model_prob": 28.0,
            "raw_model_prob": 41.2, "implied_prob": 26.0, "edge": 2.0, "devigged": True,
            "recommendation": recommendation, "_commence": start}


def test_only_value_picks_for_fights_still_to_start_are_saved(conn):
    now = datetime(2026, 11, 10, 12, 0)
    bets = [_bet(1, "Alpha One", "Bravo Two", now + timedelta(days=4)),
            _bet(2, "Charlie Three", "Delta Four", now - timedelta(minutes=1)),        # already started
            _bet(2, "Delta Four", "Charlie Three", now + timedelta(days=4), "skip")]   # not a value pick
    assert mma_tracking.record_value_edges(conn, bets, now=now) == 1
    # Saved once: a later, longer price does not replace the first one.
    assert mma_tracking.record_value_edges(conn, [dict(bets[0], odds=5.0)], now=now) == 0
    assert conn.execute(text("SELECT odds FROM mma_tracked_edges")).scalar() == 3.7


def test_picks_settle_from_the_stored_winner(conn):
    now = datetime(2026, 11, 10, 12, 0)
    mma_tracking.record_value_edges(conn, [_bet(1, "Alpha One", "Bravo Two", now + timedelta(days=4)),
                                           _bet(2, "Charlie Three", "Delta Four", now + timedelta(days=4))], now=now)
    rec = mma_tracking.edge_record(conn)
    assert rec["bets"] == 0 and rec["pending"] == 2

    conn.execute(text("UPDATE mma_events SET is_completed = 1"))
    conn.execute(text("UPDATE mma_fights SET winner_name = 'Alpha One' WHERE id = 1"))   # fight 2: no contest
    rec = mma_tracking.edge_record(conn)
    assert rec["bets"] == 1 and rec["wins"] == 1 and rec["void"] == 1 and rec["pending"] == 0
    assert abs(rec["profit_units"] - 2.7) < 1e-9
    assert abs(rec["roi_pct"] - 270.0) < 1e-9
    statuses = {r["fighter"]: r["status"] for r in rec["recent"]}
    assert statuses == {"Alpha One": "won", "Charlie Three": "void"}


def test_value_label_needs_a_proven_record():
    assert not mma_tracking.value_proven({'bets': 99, 'profit_units': 30})
    assert not mma_tracking.value_proven({'bets': 150, 'profit_units': -2})
    assert mma_tracking.value_proven({'bets': 100, 'profit_units': 0.5})


def test_edge_finder_cards_show_tracking_not_value_until_proven():
    from pathlib import Path
    page = Path("templates/mma.html").read_text(encoding="utf-8")
    start = page.index("function efCardHTML(")
    card = page[start:page.index("\n}\n", start)]
    assert "TRACKING" in card and "proven" in card
    assert "Confidence <strong>" not in card
    assert "🎯 Long Shot" not in card
