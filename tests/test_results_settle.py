"""Automatic results: matching, saving, and marking non-runners scratched."""
import json
import os
from datetime import date

import pytest

import results_settle as rs


def _ours(horse_id, name, tab, result_id=None):
    return {'horse_id': horse_id, 'name': name, 'clean': rs.clean_name(name), 'tab': tab,
            'is_scratched': False, 'result_id': result_id, 'finish_position': None, 'sp': None}


def test_match_prefers_tab_when_name_agrees():
    ours = [_ours(1, "Milly's Clever Too", 1), _ours(2, 'Other', 2)]
    runner, how = rs.match({'name': 'Millys Clever Too', 'tab': 1}, ours)
    assert runner['horse_id'] == 1 and how == 'tab'


def test_match_refuses_tab_when_name_disagrees_then_tries_name():
    ours = [_ours(1, 'Alpha', 1), _ours(2, 'Beta', 2)]
    runner, how = rs.match({'name': 'Beta', 'tab': 1}, ours)
    assert runner['horse_id'] == 2 and how == 'name'
    assert rs.match({'name': 'Gamma', 'tab': 1}, ours) == (None, None)


def test_missing_or_zero_price_is_none():
    assert rs._price(0) is None and rs._price('') is None and rs._price('4.5') == 4.5


class FakePF:
    api_key = 'k'

    def __init__(self, races):
        self.races = races

    def get_meetings_list(self, day):
        return {'meetings': []}  # forces the V1 path

    def get_results(self, track, day):
        return {'RaceDetails': self.races}


PG_URL = os.environ.get('TEST_POSTGRES_URL')


@pytest.mark.skipif(not PG_URL, reason='set TEST_POSTGRES_URL to run against Postgres')
def test_settle_meeting_on_postgres():
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
        conn.execute(text("INSERT INTO meetings (id, user_id, meeting_name, puntingform_id, auto_imported) "
                          "VALUES (1, 1, '261001_Scone', 'Scone', true)"))
        conn.execute(text("INSERT INTO races (id, meeting_id, race_number) VALUES (10, 1, 1), (11, 1, 2)"))
        for hid, race, name, tab in ((1, 10, "Milly's Clever Too", 1), (2, 10, 'Thunder Shock', 2),
                                     (3, 10, 'So You Ready', 3), (4, 10, 'Late Scratching', 4),
                                     (5, 11, 'Not Run Yet', 1)):
            conn.execute(text("INSERT INTO horses (id, race_id, horse_name, csv_data) VALUES (:h, :r, :n, :cd)"),
                         {'h': hid, 'r': race, 'n': name, 'cd': json.dumps({'horse number': str(tab)})})
        # A manual SP that must not be wiped by a missing feed price.
        conn.execute(text("INSERT INTO results (horse_id, finish_position, sp) VALUES (3, 5, 15.0)"))

    pf = FakePF([
        {'RaceNumber': 1, 'Runners': [
            {'Name': 'Millys Clever Too', 'TabNo': 1, 'Position': 1, 'Price_SP': 3.2},
            {'Name': 'Thunder Shock', 'TabNo': 2, 'Position': 7, 'Price_SP': 0},
            {'Name': 'So You Ready', 'TabNo': 3, 'Position': 2, 'Price_SP': None},
            {'Name': 'Stranger', 'TabNo': 9, 'Position': 3, 'Price_SP': 9.0},
        ]},
        {'RaceNumber': 2, 'Runners': [{'Name': 'Not Run Yet', 'TabNo': 1, 'Position': 0, 'Price_SP': 0}]},
    ])
    with engine.begin() as conn:
        meeting = rs.meetings_to_settle(conn, days=10000)[0]
        summary = rs.settle_meeting(conn, meeting, pf)

    assert summary['source'] == 'v1' and summary['races_resulted'] == 1
    assert summary['created'] == 3 and summary['updated'] == 1
    assert summary['no_sp'] == 2
    assert summary['unmatched'] == ["R1 #9 'Stranger'"]
    assert summary['marked_scratched'] == ['R1 #4 Late Scratching (was not marked scratched)']
    with engine.connect() as conn:
        res = {r.horse_id: (r.finish_position, r.sp) for r in conn.execute(text('SELECT * FROM results'))}
    assert res == {1: (1, 3.2), 2: (5, None), 3: (2, 15.0), 4: (0, None)}
    assert 5 not in res  # race 2 has not been run

    # Re-running changes nothing new.
    with engine.begin() as conn:
        again = rs.settle_meeting(conn, meeting, pf)
    assert again['created'] == 0 and again['marked_scratched'] == []
    db.metadata.drop_all(engine)


@pytest.mark.skipif(not PG_URL, reason='set TEST_POSTGRES_URL to run against Postgres')
def test_settle_never_writes_a_second_result_row():
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
        conn.execute(text("INSERT INTO meetings (id, user_id, meeting_name, puntingform_id) "
                          "VALUES (1, 1, '261001_Scone', 'Scone')"))
        conn.execute(text("INSERT INTO races (id, meeting_id, race_number) VALUES (10, 1, 1)"))
        for hid, name, tab in ((1, 'Alpha', 1), (2, 'Beta', 2), (3, 'Emergency', 15), (4, 'Old Dupes', 4)):
            conn.execute(text("INSERT INTO horses (id, race_id, horse_name, csv_data) VALUES (:h, 10, :n, :cd)"),
                         {'h': hid, 'n': name, 'cd': json.dumps({'horse number': str(tab)})})
        # A horse that already has two result rows from the old buttons.
        conn.execute(text("INSERT INTO results (horse_id, finish_position, sp) VALUES (4, 5, 30.0), (4, 5, 30.0)"))

    race = {'RaceNumber': 1, 'Runners': [
        {'Name': 'Alpha', 'TabNo': 1, 'Position': 1, 'Price_SP': 2.0},
        {'Name': 'Beta', 'TabNo': 2, 'Position': 2, 'Price_SP': 5.0},
        {'Name': 'Old Dupes', 'TabNo': 4, 'Position': 3, 'Price_SP': 30.0},
    ]}
    pf = FakePF([race, race])  # the feed repeats the race
    meeting = {'id': 1, 'meeting_name': '261001_Scone', 'date': None, 'puntingform_id': 'Scone'}
    for _ in range(3):  # and the job runs three times
        with engine.begin() as conn:
            rs.settle_meeting(conn, meeting, pf)

    with engine.connect() as conn:
        counts = dict(conn.execute(text('SELECT horse_id, count(*) FROM results GROUP BY horse_id')).fetchall())
    assert counts == {1: 1, 2: 1, 3: 1, 4: 2}  # nothing new duplicated; old dupes left as they were
    db.metadata.drop_all(engine)
