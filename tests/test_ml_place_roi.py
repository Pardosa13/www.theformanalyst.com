import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as appmod


def _race(fin, sp, place_bsp, placed):
    return {'race_id': 1, 'top_analyzer_horse_id': 1, 'top_ml_horse_id': 1,
            'finish_position': fin, 'sp': sp, 'place_bsp': place_bsp, 'bf_placed': placed}


def test_top_pick_place_bet_settles_at_betfair_place_sp():
    stats = appmod._summarise_ml_performance_races([
        _race(1, 3.0, 1.5, True),     # placed: (1.5 - 1) * 0.92 * 10 = +4.60
        _race(3, 5.0, 2.0, False),    # 3rd in a 6-runner race: no place, -10
        _race(2, 4.0, None, True),    # no place price yet: not counted
    ])
    assert stats['place_priced_bets'] == 2
    assert stats['place_priced_placed'] == 1
    assert round(stats['place_profit'], 2) == -5.40
    assert round(stats['place_roi'], 1) == -27.0
    assert stats['avg_place_bsp'] == 1.75
    assert stats['avg_placed_place_bsp'] == 1.5


def test_no_place_prices_gives_no_place_roi():
    stats = appmod._summarise_ml_performance_races([_race(1, 3.0, None, True)])
    assert stats['place_priced_bets'] == 0
    assert stats['place_roi'] is None
