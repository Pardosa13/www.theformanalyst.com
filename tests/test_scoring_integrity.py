"""Scoring integrity: no rescoring after the jump, prices follow scores,
every score change is logged, and incomplete-result races are flagged."""
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import scoring_integrity as si


def _meeting(day, name=None):
    return SimpleNamespace(date=day, meeting_name=name or f"{day:%y%m%d}_Flemington", track=None)


def _race(start_time='14:35', start_time_utc=None):
    horse = SimpleNamespace(csv_data={'start time': start_time} if start_time else {})
    return SimpleNamespace(id=1, race_number=3, horses=[horse], start_time_utc=start_time_utc)


# 1 Nov 2026 01:00 UTC = 12:00 Melbourne (AEDT, UTC+11)
NOON_MELBOURNE = datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc)
RACE_DAY = date(2026, 11, 1)


# ── Has the race started? ────────────────────────────────────────────────
@pytest.mark.parametrize('value, expected', [
    ('14:35', (14, 35)),
    ('2:35 PM', (14, 35)),
    ('12:05 am', (0, 5)),
    ('', None),
    ('TBA', None),
])
def test_parse_csv_start_time(value, expected):
    parsed = si.parse_csv_start_time(value)
    assert (parsed.hour, parsed.minute) == expected if expected else parsed is None


def test_past_meeting_counts_as_started():
    started, reason = si.race_has_started(_race(), _meeting(RACE_DAY - timedelta(days=1)),
                                          now_utc=NOON_MELBOURNE, allow_network=False)
    assert started and 'passed' in reason


def test_future_meeting_has_not_started():
    started, _ = si.race_has_started(_race(), _meeting(RACE_DAY + timedelta(days=1)),
                                     now_utc=NOON_MELBOURNE, allow_network=False)
    assert not started


def test_today_before_and_after_the_jump():
    meeting = _meeting(RACE_DAY)
    assert not si.race_has_started(_race('14:35'), meeting, now_utc=NOON_MELBOURNE, allow_network=False)[0]
    assert si.race_has_started(_race('11:30'), meeting, now_utc=NOON_MELBOURNE, allow_network=False)[0]


def test_safety_margin_blocks_a_rescore_just_before_the_jump():
    assert si.race_has_started(_race('12:01'), _meeting(RACE_DAY),
                               now_utc=NOON_MELBOURNE, allow_network=False)[0]


def test_unknown_start_time_today_counts_as_started():
    started, reason = si.race_has_started(_race(start_time=None), _meeting(RACE_DAY),
                                          now_utc=NOON_MELBOURNE, allow_network=False)
    assert started and 'unknown' in reason


def test_stored_start_time_wins_and_csv_time_is_cached_on_the_race():
    race = _race('14:35')
    si.race_has_started(race, _meeting(RACE_DAY), now_utc=NOON_MELBOURNE, allow_network=False)
    assert race.start_time_utc == datetime(2026, 11, 1, 3, 35)  # 14:35 AEDT

    race.start_time_utc = datetime(2026, 11, 1, 0, 30)  # already run
    assert si.race_has_started(race, _meeting(RACE_DAY), now_utc=NOON_MELBOURNE, allow_network=False)[0]


def test_meeting_date_falls_back_to_meeting_name():
    meeting = SimpleNamespace(date=None, meeting_name='261101_Flemington', track=None)
    assert si.meeting_date(meeting) == RACE_DAY


# ── Prices follow scores ─────────────────────────────────────────────────
def _pred(score, horse_id=1, price='', ml=None):
    return SimpleNamespace(score=score, horse_id=horse_id, predicted_odds=price,
                           win_probability='', ml_score=ml)


def test_reprice_matches_analyzer_formula():
    preds = [_pred(70), _pred(55), _pred(40)]
    si.reprice_race(preds)
    weights = [math.exp(s / 15) for s in (70, 55, 40)]
    total = sum(weights)
    for pred, weight in zip(preds, weights):
        probability = weight / total
        assert pred.predicted_odds == f"${1 / (probability * 1.10):.2f}"
        assert pred.win_probability == f"{probability * 110:.1f}%"


def test_reprice_keeps_price_order_in_line_with_score_order():
    preds = [_pred(48.2), _pred(51.7), _pred(50.1), _pred(12.0)]
    si.reprice_race(preds)
    by_score = sorted(preds, key=lambda p: -p.score)
    prices = [si.parse_price(p.predicted_odds) for p in by_score]
    assert prices == sorted(prices)


def test_reprice_survives_huge_scores():
    preds = [_pred(5000), _pred(4990)]
    si.reprice_race(preds)
    assert all(si.parse_price(p.predicted_odds) for p in preds)


# ── score_history ────────────────────────────────────────────────────────
class _Session:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)


def test_log_score_change_records_old_and_new_values():
    session = _Session()
    pred = _pred(50.0, horse_id=7, price='$4.00')
    before = si.snapshot(pred)
    pred.score, pred.predicted_odds = 53.5, '$3.40'
    assert si.log_score_change(session, pred, 11, 'pace_bias', before, detail='0 -> 1')
    row = session.added[0]
    assert (row.horse_id, row.race_id, row.reason) == (7, 11, 'pace_bias')
    assert (row.old_score, row.new_score, row.old_price, row.new_price) == (50.0, 53.5, 4.0, 3.4)


def test_log_score_change_skips_when_nothing_moved():
    session = _Session()
    pred = _pred(50.0, price='$4.00', ml=60.0)
    assert not si.log_score_change(session, pred, 1, 'ml', si.snapshot(pred))
    assert session.added == []


def test_log_score_change_rejects_unknown_reason():
    with pytest.raises(ValueError):
        si.log_score_change(_Session(), _pred(1.0), 1, 'whatever', si.snapshot(None))


# ── Every stats query skips incomplete races ─────────────────────────────
def test_every_results_join_in_app_filters_incomplete_races():
    source = Path('app.py').read_text()
    lines = source.splitlines()
    missing = []
    for i, line in enumerate(lines):
        if re.search(r'\.join\(\s*Result\b|\.outerjoin\(\s*$|JOIN results ', line) or (
            line.strip() == 'Result, Horse.id == Result.horse_id' or line.strip() == 'Result, Result.horse_id == Horse.id'
        ):
            window = '\n'.join(lines[i:i + 12])
            if 'Result' not in window and 'results' not in window:
                continue
            # Not stats: the pending/complete results lists and the count of
            # best bets still waiting on a result.
            if 'Result.id.is_(None)' in window or 'func.count(Result.id)' in window:
                continue
            if 'results_incomplete' not in window:
                missing.append(i + 1)
    assert missing == [], f'results joins without the incomplete-race filter at app.py lines {missing}'


def test_backtest_training_query_filters_incomplete_races():
    source = Path('backtest.py').read_text()
    start = source.index('def load_historical_data')
    assert 'rc.results_incomplete IS NOT TRUE' in source[start:start + 2500]


def test_update_scratchings_never_deletes_predictions():
    source = Path('app.py').read_text()
    start = source.index('def update_scratchings')
    end = source.index('def ', start + 30)
    body = source[start:end]
    assert 'db.session.delete' not in body
    assert 'race_has_started(race, meeting)' in body


# ── Postgres: the incomplete-results SQL itself ──────────────────────────
PG_URL = os.environ.get('TEST_POSTGRES_URL')


@pytest.mark.skipif(not PG_URL, reason='set TEST_POSTGRES_URL to run against Postgres')
def test_results_incomplete_flag_on_postgres():
    import psycopg2
    if not hasattr(psycopg2, 'paramstyle'):
        pytest.skip('psycopg2 is stubbed out by another test module in this run')
    from sqlalchemy import create_engine, text
    from models import db

    engine = create_engine(PG_URL)
    db.metadata.drop_all(engine)
    db.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, username, email, password_hash) VALUES (1,'u','u@x','x')"))
        conn.execute(text("INSERT INTO meetings (id, user_id, meeting_name) VALUES (1, 1, '261101_Test')"))
        # race 1: full field, 8 runners at $8 each = 100% market -> clean
        # race 2: 8 starters, only 4 placegetters recorded -> incomplete
        # race 3: no results yet -> left alone
        # race 4: full field but SPs sum to 80% -> incomplete
        for race_id in (1, 2, 3, 4):
            conn.execute(text("INSERT INTO races (id, meeting_id, race_number) VALUES (:r, 1, :r)"), {'r': race_id})
        horse_id = 0
        for race_id, recorded, sp in ((1, 8, 8.0), (2, 4, 3.0), (3, 0, None), (4, 8, 10.0)):
            for n in range(8):
                horse_id += 1
                conn.execute(text("INSERT INTO horses (id, race_id, horse_name, is_scratched) VALUES (:h, :r, :n, FALSE)"),
                             {'h': horse_id, 'r': race_id, 'n': f'H{horse_id}'})
                if n < recorded:
                    conn.execute(text("INSERT INTO results (horse_id, finish_position, sp) VALUES (:h, :f, :sp)"),
                                 {'h': horse_id, 'f': min(n + 1, 5), 'sp': sp})
        changed = si.refresh_results_incomplete_flags(conn)
        flags = dict(conn.execute(text('SELECT id, results_incomplete FROM races ORDER BY id')).fetchall())
        assert flags == {1: False, 2: True, 3: False, 4: True}
        assert changed == 2
        # Running it again changes nothing.
        assert si.refresh_results_incomplete_flags(conn) == 0
        assert si.refresh_results_incomplete_flags(conn, race_ids=[2]) == 0
    db.metadata.drop_all(engine)
