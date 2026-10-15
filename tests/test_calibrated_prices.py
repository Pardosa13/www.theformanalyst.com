"""Meeting pages show the calibrated fair price, falling back to the raw book."""
from types import SimpleNamespace

import app as appmod


def _pred(**kw):
    base = {'predicted_odds': '$2.50', 'win_probability': '44.0%', 'calibrated_win_prob': None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_calibrated_price_replaces_the_raw_book():
    assert appmod._analyzer_display_price(_pred(calibrated_win_prob=0.25)) == ('$4.00', '25.0%')


def test_raw_book_when_not_calibrated():
    assert appmod._analyzer_display_price(_pred()) == ('$2.50', '44.0%')
    assert appmod._analyzer_display_price(_pred(calibrated_win_prob=0)) == ('$2.50', '44.0%')


def test_no_prediction_shows_nothing():
    assert appmod._analyzer_display_price(None) == ('', '')
