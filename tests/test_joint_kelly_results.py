"""ML Data's long-run table for the saved multi-horse (joint Kelly) stakes."""
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as appmod


def _row(race_id, start, kelly, finish, sp, sized_price=None):
    return (
        SimpleNamespace(kelly_stake_pct=kelly, value_edge_price=sized_price),
        SimpleNamespace(finish_position=finish, sp=sp),
        race_id,
        start,
    )


def _results(rows, monkeypatch):
    class _Query:
        def join(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def all(self): return rows

    monkeypatch.setattr(appmod.db.session, 'query', lambda *a, **k: _Query())
    return appmod.calculate_joint_kelly_results()


def test_races_are_settled_as_a_whole_with_a_fixed_bank(monkeypatch):
    bank = appmod.KELLY_TRACKING_BANKROLL
    old = datetime(2026, 9, 20, 5)
    new = datetime(2026, 10, 18, 5)
    rows = [
        # Race 1 (old prices): two runners backed, one wins at SP 5.0, sized at 6.0.
        _row(1, old, 0.02, 1, 5.0, sized_price=6.0),
        _row(1, old, 0.01, 3, 4.0, sized_price=4.0),
        # Race 2 (fresh prices): one runner backed, loses.
        _row(2, new, 0.03, 2, 3.0, sized_price=3.2),
    ]
    out = _results(rows, monkeypatch)
    all_time, fresh = out['summary']

    assert all_time['races'] == 2
    assert all_time['bets'] == 3
    assert all_time['races_won'] == 1
    staked = (0.02 + 0.01 + 0.03) * bank
    assert round(all_time['staked'], 6) == round(staked, 6)
    assert round(all_time['profit_sp'], 6) == round(0.02 * bank * 5.0 - staked, 6)
    assert round(all_time['profit_sized'], 6) == round(0.02 * bank * 6.0 - staked, 6)
    assert all_time['longest_losing_run'] == 1
    # Profit after race 1 is the peak; race 2's loss is the drawdown.
    assert round(all_time['max_drawdown'], 6) == round(0.03 * bank, 6)

    # Only race 2 is on or after the fresh-price start date.
    assert fresh['races'] == 1
    assert fresh['races_won'] == 0

    assert [m['month'] for m in out['months']] == ['2026-09', '2026-10']
    assert len(out['history']) == 2
    assert out['history'][-1]['cumulative'] == round(all_time['profit_sp'], 2)


def test_no_stakes_reads_as_empty_not_as_a_loss(monkeypatch):
    out = _results([], monkeypatch)
    assert out['summary'][0]['races'] == 0
    assert out['summary'][0]['roi_sp'] == 0.0
    assert out['months'] == []


def test_ml_data_page_shows_the_table():
    source = Path('app.py').read_text()
    start = source.index('def ml_data_analytics(')
    end = source.index('\n@app.route(', start)
    assert 'calculate_joint_kelly_results(' in source[start:end]
    template = Path('templates/ml_data.html').read_text()
    assert 'Live Kelly Stakes: Multi-Horse Results' in template
    assert 'jointKellyChart' in template
