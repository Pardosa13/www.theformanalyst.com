"""AFL ML picks only show when the model beat the plain Value Finder on unseen picks."""
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")

import afl_backtest


def test_afl_ml_must_beat_the_value_finder_on_unseen_picks():
    df = pd.DataFrame({
        "ml_bet_probability": [0.7] * 60 + [0.3] * 60,
        "profit_units": [1.0] * 40 + [-1.0] * 20 + [-1.0] * 60,
    })
    vs = afl_backtest.compare_with_value_finder(df, 0.5)
    assert vs["ml_picks"] == 60 and vs["beats_value_finder"] is True
    worse = afl_backtest.compare_with_value_finder(df.assign(profit_units=-1.0), 0.5)
    assert worse["beats_value_finder"] is False
    small = afl_backtest.compare_with_value_finder(df.iloc[:40].assign(ml_bet_probability=0.9), 0.5)
    assert small["beats_value_finder"] is False          # under 50 picks
    src = Path("afl_routes.py").read_text(encoding="utf-8")
    assert '"status": "not_better"' in src
    train_src = Path("afl_backtest.py").read_text(encoding="utf-8")
    assert "threshold = threshold_search(pick_df)" in train_src
    assert "threshold = threshold_search(test_df)" not in train_src
