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
    data = replay_staking_strategies([_sel(2.0, .4, True, '2024-01-01')], 1000)
    full = next(r for r in data['strategies'] if r['key'] == 'full_kelly')
    assert full['curve'][0]['stake'] == 0
    assert full['final_bankroll'] == 1000


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
    assert all(r['number_of_bets'] == 2 for r in data['strategies'])
    assert {'highest_final_bankroll','highest_profit','best_risk_adjusted_return','smallest_maximum_drawdown'} <= set(data['winners'])


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
