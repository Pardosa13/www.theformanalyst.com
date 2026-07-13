import math
from app import calculate_kelly_fraction, replay_staking_strategies


def _sel(sp, prob, won, key):
    return {'sp': sp, 'probability': prob, 'finish_position': 1 if won else 2, 'sort_key': key, 'race_number': 1, 'race_id': int(key[-1])}


def test_bankroll_compounds_correctly_for_percent_staking():
    data = replay_staking_strategies([_sel(2.0, .6, True, '2024-01-01'), _sel(2.0, .6, False, '2024-01-02')], 1000)
    flat_10 = next(r for r in data['strategies'] if r['key'] == 'flat_1_pct')
    assert flat_10['curve'][0]['stake'] == 10
    assert flat_10['curve'][1]['stake'] == 10.1
    assert flat_10['final_bankroll'] == 999.9


def test_kelly_fraction_calculated_correctly_and_negative_no_bet():
    assert round(calculate_kelly_fraction(.6, 3.0), 4) == 0.4
    assert round(calculate_kelly_fraction(60, 3.0), 4) == 0.4
    assert round(calculate_kelly_fraction('45.4%', 3.0), 4) == 0.181
    data = replay_staking_strategies([_sel(2.0, .4, True, '2024-01-01')], 1000)
    full = next(r for r in data['strategies'] if r['key'] == 'full_kelly')
    assert full['curve'][0]['stake'] == 0
    assert full['final_bankroll'] == 1000
    assert full['number_of_bets'] == 0
    assert full['number_of_winning_bets'] == 0


def test_capped_kelly_never_exceeds_cap():
    data = replay_staking_strategies([_sel(5.0, .9, True, '2024-01-01')], 1000)
    quarter_cap = next(r for r in data['strategies'] if r['key'] == 'quarter_kelly_cap_2')
    full_cap = next(r for r in data['strategies'] if r['key'] == 'full_kelly_cap_5')
    assert quarter_cap['largest_individual_stake'] <= 20
    assert full_cap['largest_individual_stake'] <= 50


def test_chronological_ordering_and_chart_totals_match_table_totals():
    data = replay_staking_strategies([_sel(2.0, .6, False, '2024-01-02'), _sel(2.0, .6, True, '2024-01-01')], 1000)
    flat = next(r for r in data['strategies'] if r['key'] == 'flat_10')
    assert flat['curve'][0]['profit'] == 10
    assert flat['curve'][-1]['bankroll'] == flat['final_bankroll']


def test_flat_10_reconciles_existing_baseline():
    sels = [_sel(3.0, .5, True, '2024-01-01'), _sel(2.0, .5, False, '2024-01-02')]
    data = replay_staking_strategies(sels, 1000)
    flat = next(r for r in data['strategies'] if r['key'] == 'flat_10')
    assert flat['total_profit'] == 10
    assert flat['total_staked'] == 20
    assert flat['roi'] == 50


def test_all_staking_methods_reconcile_count_and_winners():
    data = replay_staking_strategies([_sel(3.0, .5, True, '2024-01-01'), _sel(2.0, .5, False, '2024-01-02')], 1000)
    assert len(data['strategies']) == 11
    assert all(r['number_of_bets'] == 2 for r in data['strategies'] if 'kelly' not in r['key'])
    assert {'highest_final_bankroll','highest_profit','best_risk_adjusted_return','smallest_maximum_drawdown'} <= set(data['winners'])


def test_bankroll_audit_invariants_and_largest_stakes():
    sels = [
        _sel(101.0, .99, True, '2024-01-01'),
        _sel(2.0, .6, False, '2024-01-02'),
        _sel(4.0, .1, True, '2024-01-03'),
    ]
    data = replay_staking_strategies(sels, 1000)
    flat_1 = next(r for r in data['strategies'] if r['key'] == 'flat_1_pct')
    flat_2 = next(r for r in data['strategies'] if r['key'] == 'flat_2_pct')
    quarter_kelly = next(r for r in data['strategies'] if r['key'] == 'quarter_kelly')

    assert flat_1['curve'][0]['stake'] == 10
    assert flat_1['curve'][1]['stake'] == flat_1['curve'][1]['bankroll_before'] * 0.01
    assert flat_2['curve'][0]['stake'] == 20
    assert flat_2['curve'][1]['stake'] == flat_2['curve'][1]['bankroll_before'] * 0.02

    for strategy in data['strategies']:
        assert len(strategy['largest_stakes']) <= 5
        for point in strategy['curve']:
            assert point['bankroll'] >= 0
            assert point['stake'] <= point['bankroll_before']
        assert strategy['peak_bankroll'] == round(max([1000] + [point['bankroll'] for point in strategy['curve']]), 2)
        for row in strategy['largest_stakes']:
            assert row['stake'] <= row['bankroll']
            assert row['stake_bankroll_pct'] == round(row['stake'] / row['bankroll'] * 100, 4)
            if row['kelly_fraction'] is not None:
                assert 0 <= row['kelly_fraction'] <= 1
            assert {'race_id', 'race_number', 'sort_key', 'sp', 'probability', 'finish_position'} <= set(row)

    assert quarter_kelly['number_of_bets'] < len(sels)


def test_summary_metrics_are_derived_from_actual_replay_stakes():
    sels = [
        _sel(2.0, .6, False, '2024-01-01'),
        _sel(2.0, .6, False, '2024-01-02'),
        _sel(2.0, .6, True, '2024-01-03'),
    ]
    data = replay_staking_strategies(sels, 10000)
    for strategy in data['strategies']:
        stake_history = strategy['stake_history']
        replay_stakes = [row['stake'] for row in stake_history]
        realised_profits = [row['profit_loss'] for row in stake_history]

        assert strategy['total_staked'] == round(sum(replay_stakes), 2)
        assert strategy['average_stake'] == round(sum(replay_stakes) / strategy['number_of_bets'], 2)
        assert strategy['largest_individual_stake'] == round(max(replay_stakes), 2)
        assert strategy['average_stake'] <= strategy['largest_individual_stake']
        assert strategy['largest_individual_stake'] <= max(row['bankroll_before'] for row in stake_history)
        assert strategy['peak_bankroll'] == round(max([10000] + [point['bankroll'] for point in strategy['curve']]), 2)
        assert all({'stake', 'profit_loss', 'bankroll_before', 'bankroll_after'} <= set(row) for row in stake_history)

        avg_profit = sum(realised_profits) / len(realised_profits)
        expected_volatility = math.sqrt(sum((profit - avg_profit) ** 2 for profit in realised_profits) / len(realised_profits))
        assert strategy['volatility'] == round(expected_volatility, 2)

    flat_1 = next(r for r in data['strategies'] if r['key'] == 'flat_1_pct')
    flat_2 = next(r for r in data['strategies'] if r['key'] == 'flat_2_pct')
    assert flat_1['largest_individual_stake'] <= 100
    assert flat_2['largest_individual_stake'] <= 200


def test_probability_source_contracts_are_documented_in_source():
    from pathlib import Path
    source = Path('app.py').read_text()
    data_tpl = Path('templates/data.html').read_text()
    ml_tpl = Path('templates/ml_data.html').read_text()
    assert "probability_source_field='predictions.win_probability'" in source
    assert 'ML_STORED_PROBABILITY_COLUMNS' in source
    assert 'derived from ML 110% market probabilities' in source
    assert 'Probability Source field' in data_tpl
    assert 'Probability Source field' in ml_tpl
    assert 'Recommended Historical Strategy' in data_tpl
    assert 'Recommended Historical Strategy' in ml_tpl
