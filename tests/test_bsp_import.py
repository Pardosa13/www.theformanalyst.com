"""Betfair BSP file import: names, times, matching and the derived columns."""
import os
from datetime import date, datetime

import pytest

import bsp_import as bi


def test_clean_name_matches_betfair_style():
    assert bi.clean_name("Milly's Clever Too") == 'millys clever too'
    assert bi.clean_name('Dash Of Sun (NZ)') == 'dash of sun'
    assert bi.clean_name('  Mr.  Bigglesworth ') == 'mr bigglesworth'


def test_times_are_read_as_sydney_time():
    # 13:03 AEST on 1 Sep 2026 = 03:03 UTC
    assert bi.to_utc('2026-09-01', '13:03:06.000') == datetime(2026, 9, 1, 3, 3, 6)
    # 13:03 AEDT on 1 Nov 2026 = 02:03 UTC
    assert bi.to_utc('2026-11-01', '13:03:06.000') == datetime(2026, 11, 1, 2, 3, 6)
    assert bi.to_utc('2026-09-01', '') is None


def test_same_horse_needs_a_close_name():
    assert bi.same_horse('thunder shoc', 'thunder shock')
    assert not bi.same_horse('thunder shock', 'golden rule')


def test_months_to_load_runs_from_february():
    assert list(bi.months_to_load(date(2026, 4, 3))) == [(2026, 2), (2026, 3), (2026, 4)]


def _row(market, sel, tab, name, bsp, won=False, race_no=1, track='Scone', state='NSW', day='2026-09-01'):
    return {
        'LOCAL_MEETING_DATE': day, 'SCHEDULED_RACE_TIME': '13:00:00.000', 'ACTUAL_OFF_TIME': '13:03:06.000',
        'TRACK': track, 'STATE_CODE': state, 'RACE_NO': str(race_no), 'WIN_MARKET_ID': market,
        'SELECTION_ID': str(sel), 'TAB_NUMBER': str(tab), 'SELECTION_NAME': name,
        'WIN_RESULT': 'WINNER' if won else 'LOSER', 'WIN_BSP': str(bsp), 'WIN_BSP_VOLUME': '100',
        'WIN_PREPLAY_LAST_PRICE_TAKEN': str(bsp), 'WIN_PREPLAY_WEIGHTED_AVERAGE_PRICE_TAKEN': str(bsp),
        'WIN_PREPLAY_VOLUME': '1000', 'BEST_AVAIL_BACK_AT_SCHEDULED_OFF': str(bsp),
        'BEST_AVAIL_LAY_AT_SCHEDULED_OFF': str(bsp),
    }


PG_URL = os.environ.get('TEST_POSTGRES_URL')


@pytest.mark.skipif(not PG_URL, reason='set TEST_POSTGRES_URL to run against Postgres')
def test_import_end_to_end_on_postgres():
    import psycopg2
    if not hasattr(psycopg2, 'paramstyle'):
        pytest.skip('psycopg2 is stubbed out by another test module in this run')
    import json
    from sqlalchemy import create_engine, text
    from models import db

    engine = create_engine(PG_URL)
    db.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE IF EXISTS betfair_prices, betfair_import_files'))
    db.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, username, email, password_hash) VALUES (1,'u','u@x','x')"))
        conn.execute(text("INSERT INTO meetings (id, user_id, meeting_name, auto_imported) VALUES (1, 1, '260901_Scone', true)"))
        conn.execute(text("INSERT INTO races (id, meeting_id, race_number) VALUES (10, 1, 1)"))
        horses = [  # id, name, tab, finish, sp
            (1, "Milly's Clever Too", 1, 1, 3.0),     # apostrophe: Betfair drops it
            (2, 'Thunder Shock', 2, 0, None),         # truncated on Betfair; ours says scratched
            (3, 'So You Ready', 3, 5, 0.0),           # ran, but our SP is missing
            (4, 'Ghost Runner', 4, 5, 20.0),          # not on Betfair at all
        ]
        for hid, name, tab, finish, sp in horses:
            conn.execute(text("INSERT INTO horses (id, race_id, horse_name, csv_data) VALUES (:h, 10, :n, :cd)"),
                         {'h': hid, 'n': name, 'cd': json.dumps({'horse number': str(tab)})})
            conn.execute(text("INSERT INTO results (horse_id, finish_position, sp) VALUES (:h, :f, :sp)"),
                         {'h': hid, 'f': finish, 'sp': sp})

    rows = [
        _row('1.1', 101, 1, 'Millys Clever Too', 2.8, won=True),
        _row('1.1', 102, 2, 'Thunder Shoc', 6.5),
        _row('1.1', 103, 3, 'So You Ready', 12.0),
        _row('1.1', 104, 5, 'Unknown Horse', 40.0),
        _row('2.2', 201, 1, 'Somebody Else', 3.0, track='Ascot', state='WA', race_no=1),  # our race? no
        _row('3.3', 301, 1, 'Kiwi Horse', 3.0, track='Ellerslie', state='NZ', race_no=2),  # NZ, unmatched
    ]
    import csv, io
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    csv_text = buffer.getvalue()

    fetch = lambda y, m: csv_text if (y, m) == (2026, 9) else None
    report = bi.run(engine, today=date(2026, 9, 30), fetch=fetch)

    with engine.connect() as conn:
        linked = dict(conn.execute(text('SELECT selection_id, horse_id FROM betfair_prices')).fetchall())
        assert linked[101] == 1 and linked[102] == 2 and linked[103] == 3 and linked[104] is None
        assert 301 not in linked  # unmatched NZ is not stored
        assert linked[201] is None  # unmatched AU is stored, unlinked
        bsp = dict(conn.execute(text('SELECT horse_id, bsp FROM results')).fetchall())
        assert bsp == {1: 2.8, 2: 6.5, 3: 12.0, 4: None}
        sp = dict(conn.execute(text('SELECT horse_id, sp FROM results')).fetchall())
        assert sp[1] == 3.0  # bookmaker SP untouched
        race = conn.execute(text('SELECT market_id, start_time_utc FROM races WHERE id = 10')).one()
        assert race.market_id == '1.1' and race.start_time_utc == datetime(2026, 9, 1, 3, 3, 6)
        assert conn.execute(text('SELECT betfair_selection_id FROM horses WHERE id = 1')).scalar() == 101
        ranks = dict(conn.execute(text("SELECT selection_id, bsp_rank FROM betfair_prices WHERE market_id = '1.1'")).fetchall())
        assert ranks[101] == 1

    assert report['our_races_matched'] == 1
    assert report['methods'] == {'name': 2, 'tab+similar': 1}
    assert report['ran_but_ours_scratched'] == 1
    assert report['ran_but_ours_no_sp'] == 1
    assert report['runners_unmatched'] == 1
    assert len(report['ours_missing_on_betfair']) == 1

    # Re-running skips the loaded file and changes nothing.
    again = bi.run(engine, today=date(2026, 9, 30), fetch=fetch)
    assert again['our_races_matched'] == 0
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE IF EXISTS betfair_prices, betfair_import_files'))
    db.metadata.drop_all(engine)
