import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as appmod


def _race(fin, place_bsp, placed, ladbrokes=None):
    return {'race_id': 1, 'top_analyzer_horse_id': 1, 'top_ml_horse_id': 1, 'finish_position': fin,
            'sp': 3.0, 'place_bsp': place_bsp, 'bf_placed': placed, 'ladbrokes_place': ladbrokes}


def test_top_pick_place_bet_uses_betfair_then_ladbrokes():
    stats = appmod._summarise_ml_performance_races([
        _race(1, 1.5, True),            # Betfair: (1.5 - 1) * 0.92 * 10 = +4.60
        _race(3, None, False, 2.0),     # 3rd in a 6-runner race: a loss, -10
        _race(2, None, True, 1.8),      # Ladbrokes stand-in: +8.00
        _race(2, None, True),           # placed, no price at all: left out
    ])
    assert stats['place_bets'] == 3
    assert stats['place_waiting'] == 1
    assert round(stats['place_profit'], 2) == 2.60
    assert round(stats['place_roi'], 1) == 8.7
    assert round(stats['avg_place_price'], 2) == round((1.5 + 2.0 + 1.8) / 3, 2)


def test_no_place_prices_still_counts_losses():
    stats = appmod._summarise_ml_performance_races([_race(5, None, False)])
    assert stats['place_bets'] == 1
    assert stats['place_profit'] == -10.0
    assert stats['avg_place_price'] is None
