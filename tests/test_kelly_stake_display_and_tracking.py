"""Kelly-staked cohort tracking in ML Data (calculate_value_edge_performance).

The Kelly Stake column was removed from the race page on 27 Oct 2026, so its
display tests went with it.
"""

from types import SimpleNamespace

import app as appmod


# ── 2. Kelly-staked cohort tracking in ML Data ──────────────────────────────

def _row(edge, kelly, finish, sp):
    return (
        SimpleNamespace(value_edge_pct=edge, kelly_stake_pct=kelly),
        SimpleNamespace(finish_position=finish, sp=sp),
    )


def _performance(rows, monkeypatch):
    """Run calculate_value_edge_performance over a fixed row set.

    The function's own query is replaced rather than mocked at the DB layer, so
    the summarising — the part under test — runs exactly as it does in the app.
    """
    class _Query:
        def join(self, *a, **k): return self
        def filter(self, *a, **k): return self
        def order_by(self, *a, **k): return self
        def all(self): return rows

    monkeypatch.setattr(appmod.db.session, 'query', lambda *a, **k: _Query())
    return appmod.calculate_value_edge_performance()


def test_kelly_cohort_sits_beside_the_flat_cohort_over_identical_rows(monkeypatch):
    rows = [
        _row(25.0, 0.04, 1, 6.0),   # backed, won
        _row(22.0, 0.02, 4, 3.0),   # backed, lost
        _row(21.0, 0.0, 1, 2.0),    # positive edge, solver declined — winner Kelly skipped
        _row(7.0, 0.01, 5, 9.0),    # backed, lost
        _row(-3.0, 0.0, 2, 12.0),   # negative edge, no stake
    ]
    perf = _performance(rows, monkeypatch)

    flat = perf['overall']
    kelly = perf['overall']['kelly']
    bankroll = perf['kelly_bankroll']

    # The flat cohort is unchanged: every measured runner is a $10 bet.
    assert flat['bets'] == 5
    assert flat['total_staked'] == 50.0

    # Kelly bets only what the solver staked, and says how many it declined.
    assert kelly['bets'] == 3
    assert kelly['no_bet'] == 2
    assert kelly['wins'] == 1
    assert round(kelly['total_staked'], 6) == round((0.04 + 0.02 + 0.01) * bankroll, 6)
    assert round(kelly['total_return'], 6) == round(0.04 * bankroll * 6.0, 6)
    assert round(kelly['profit'], 6) == round(kelly['total_return'] - kelly['total_staked'], 6)
    assert round(kelly['roi'], 6) == round(kelly['profit'] / kelly['total_staked'] * 100, 6)
    assert round(kelly['avg_stake'], 6) == round(kelly['total_staked'] / 3, 6)


def test_a_declined_runner_is_a_no_bet_not_a_losing_bet(monkeypatch):
    """A zero stake must not be settled as a bet that lost — that would make
    Kelly look worse than it is, and the comparison worthless."""
    rows = [_row(30.0, 0.0, 5, 8.0), _row(30.0, 0.05, 1, 4.0)]
    kelly = _performance(rows, monkeypatch)['overall']['kelly']
    assert kelly['bets'] == 1
    assert kelly['wins'] == 1
    assert kelly['strike_rate'] == 100.0


def test_every_bucket_carries_its_own_kelly_numbers(monkeypatch):
    rows = [_row(25.0, 0.04, 1, 6.0), _row(7.0, 0.01, 5, 9.0), _row(-3.0, 0.0, 2, 12.0)]
    perf = _performance(rows, monkeypatch)

    by_key = {bucket['key']: bucket for bucket in perf['buckets']}
    assert set(by_key) == {'below_0', '0_5', '5_10', '10_15', '15_20', '20_plus'}
    for bucket in perf['buckets']:
        assert 'kelly' in bucket, bucket['key']

    assert by_key['20_plus']['kelly']['bets'] == 1
    assert by_key['5_10']['kelly']['bets'] == 1
    # The control group staked nothing, so it has no Kelly bets to compare.
    assert by_key['below_0']['kelly']['bets'] == 0
    assert by_key['below_0']['kelly']['roi'] == 0.0

    assert perf['at_best_bets_threshold']['kelly']['bets'] == 1
    assert perf['kelly_rows_staked'] == 2


def test_empty_kelly_cohort_is_reported_as_staking_not_as_a_loss(monkeypatch):
    """Nothing staked must not read as a 0% ROI result — it means the solver
    declined everything, which says nothing about how those runners ran."""
    rows = [_row(25.0, 0.0, 1, 6.0), _row(7.0, None, 5, 9.0)]
    perf = _performance(rows, monkeypatch)
    assert perf['kelly_rows_staked'] == 0
    assert perf['overall']['kelly']['bets'] == 0
    assert perf['overall']['kelly']['total_staked'] == 0.0
    assert perf['overall']['kelly']['roi'] == 0.0
    assert perf['overall']['bets'] == 2  # the flat cohort still tracks them


def test_sizing_gain_holds_selection_constant(monkeypatch):
    """Kelly's ROI must be compared against the SAME bets flat-staked, not
    against the flat cohort — that cohort also carries every runner Kelly
    declined, so the difference would mix selection in with sizing and answer a
    question nobody asked."""
    rows = [
        _row(30.0, 0.05, 1, 4.0),    # Kelly backed it, it won
        _row(6.0, 0.01, 5, 9.0),     # Kelly backed it, it lost
        _row(-20.0, 0.0, 1, 1.5),    # Kelly declined a winner — flat cohort only
    ]
    kelly = _performance(rows, monkeypatch)['overall']['kelly']

    # The baseline covers Kelly's two bets, not the cohort's three.
    assert kelly['bets'] == 2
    assert kelly['flat_staked_same_bets'] == 20.0
    # $10 x 2 staked, one winner at $4.00 -> $40 back -> +100% flat.
    assert round(kelly['flat_roi_same_bets'], 6) == 100.0

    # Kelly put 5% on the winner and 1% on the loser, so it beat flat here.
    assert kelly['roi'] > kelly['flat_roi_same_bets']
