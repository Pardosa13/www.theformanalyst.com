"""Pick types and the Best Bets record settled at bookie SP and Betfair SP."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import betfair_check

SCHEMA = [
    "CREATE TABLE meetings (id INTEGER PRIMARY KEY, date DATE)",
    "CREATE TABLE races (id INTEGER PRIMARY KEY, meeting_id INT, start_time_utc TIMESTAMP, results_incomplete BOOLEAN)",
    "CREATE TABLE horses (id INTEGER PRIMARY KEY, race_id INT)",
    "CREATE TABLE results (id INTEGER PRIMARY KEY, horse_id INT, finish_position INT, sp FLOAT, bsp FLOAT)",
    """CREATE TABLE predictions (id INTEGER PRIMARY KEY, horse_id INT, score FLOAT, ml_win_probability FLOAT,
        ml_scored_at TIMESTAMP, value_edge_pct FLOAT, value_edge_captured_at TIMESTAMP, kelly_stake_pct FLOAT,
        best_bet_flagged_at TIMESTAMP, best_bet_qualified_at TIMESTAMP, best_bet_routes TEXT)""",
]
START = '2026-08-01 05:00:00'
BEFORE = '2026-08-01 04:00:00'
AFTER = '2026-08-01 06:00:00'


def _session():
    eng = create_engine('sqlite://')
    with eng.begin() as c:
        for stmt in SCHEMA:
            c.execute(text(stmt))
        c.execute(text("INSERT INTO meetings VALUES (1, '2026-08-01')"))
        c.execute(text(f"INSERT INTO races VALUES (1, 1, '{START}', 0), (2, 1, '{START}', 0), (3, 1, '{START}', 1)"))
        # race 1: horse 1 fav wins (sp 2, bsp 2.5); horse 2 loses (sp 5, bsp 6)
        # race 2: horse 3 fav loses (sp 3, bsp 3.2); horse 4 wins (sp 10, bsp 15)
        # race 3 is incomplete and must be ignored
        c.execute(text("INSERT INTO horses VALUES (1,1),(2,1),(3,2),(4,2),(5,3)"))
        c.execute(text("""INSERT INTO results VALUES (1,1,1,2.0,2.5),(2,2,5,5.0,6.0),(3,3,2,3.0,3.2),
                          (4,4,1,10.0,15.0),(5,5,1,2.0,2.0)"""))
        c.execute(text(f"""INSERT INTO predictions VALUES
            (1,1,80,0.5,'{BEFORE}',2,'{BEFORE}',0.05,'{BEFORE}',NULL,'maiden'),
            (2,2,60,0.2,'{BEFORE}',NULL,NULL,0,NULL,NULL,NULL),
            (3,3,50,0.3,'{BEFORE}',12,'{BEFORE}',0,NULL,'{AFTER}','proven_edge'),
            (4,4,70,0.6,'{AFTER}',25,'{AFTER}',0,NULL,'{BEFORE}','proven_edge,value_top_pick'),
            (5,5,99,0.9,'{BEFORE}',30,'{BEFORE}',0.1,'{BEFORE}',NULL,'maiden')"""))
    return Session(eng)


def _row(out, key):
    return next(r for r in out['rows'] if r['key'] == key)


def test_pick_types_at_sp_and_betfair():
    out = betfair_check.pick_type_results(_session())
    every = _row(out, 'all')
    assert every['bets'] == 4 and every['wins'] == 2
    assert round(every['roi_sp'], 6) == round(100 * ((1 - 1 - 1 + 9) / 4), 6)
    assert round(every['roi_bsp'], 6) == round(100 * ((1.5 * 0.92 - 1 - 1 + 14 * 0.92) / 4), 6)
    fav = _row(out, 'favourite')
    assert fav['bets'] == 2 and fav['wins'] == 1
    # ML top pick: race 1 horse 1 (pre-race); race 2's best (horse 4) was scored after start,
    # so race 2's pick is horse 3.
    ml = _row(out, 'ml_top')
    assert ml['bets'] == 2 and ml['wins'] == 1
    # Value edge 20+ captured after the start does not count.
    assert _row(out, 'value_20')['bets'] == 0
    assert _row(out, 'value_10')['bets'] == 1
    assert _row(out, 'kelly')['bets'] == 1


def test_best_bets_record_counts_only_pre_race_picks():
    rec = betfair_check.best_bets_record(_session())
    # horse 1 (flagged before) and horse 4 (qualified before) count; horse 3 (after) does not;
    # horse 5 is in an incomplete race.
    assert rec['total']['bets'] == 2
    assert rec['total']['wins'] == 2
    assert rec['excluded_after_start'] == 1
    routes = {r['key']: r['bets'] for r in rec['by_route']}
    assert routes == {'maiden': 1, 'proven_edge': 1, 'value_top_pick': 1}
    assert rec['by_month'][0]['key'] == '2026-08'
