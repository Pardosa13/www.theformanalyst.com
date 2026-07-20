from pathlib import Path
from types import SimpleNamespace

import app as appmod

APP_SOURCE = Path('app.py').read_text()


def pred(score, ml):
    return SimpleNamespace(score=score, ml_score=ml, notes='')


def horse(i, name, analyzer, ml, scratched=False):
    return SimpleNamespace(id=i, horse_name=name, is_scratched=scratched, csv_data={}, prediction=pred(analyzer, ml))


def evaluate_pair(price_a=5.0, price_b=1.5, ml_a=80, ml_b=20, status='Open', age=0):
    a = horse(1, 'Alpha', 100, ml_a)
    b = horse(2, 'Beta', 90, ml_b)
    race = SimpleNamespace(horses=[a, b])
    odds = {'status': status, 'fetched_at': '2026-07-20T00:00:00Z', 'age_seconds': age, 'odds': {
        appmod.normalize_runner_name('Alpha'): {'name': 'Alpha', 'win': price_a, 'is_scratched': False, 'is_available': True},
        appmod.normalize_runner_name('Beta'): {'name': 'Beta', 'win': price_b, 'is_scratched': False, 'is_available': True},
    }}
    return appmod.evaluate_ladbrokes_best_bet_signals(race, SimpleNamespace(), odds)


def test_positive_edge_above_threshold_qualifies():
    out = evaluate_pair(price_a=5.0, price_b=1.5, ml_a=80, ml_b=20)
    alpha = out[1]
    # model fair prob = 80/100 = 80%, market implied = 100/5 = 20% -> edge = 60pp
    assert alpha['ml_fair_probability_pct'] == 80.0
    assert alpha['market_implied_probability_pct'] == 20.0
    assert alpha['value_edge_pct'] == 60.0
    assert alpha['is_value_edge_bet'] is True


def test_negative_edge_does_not_qualify():
    out = evaluate_pair(price_a=5.0, price_b=1.5, ml_a=80, ml_b=20)
    beta = out[2]
    # model fair prob = 20/100 = 20%, market implied = 100/1.5 = 66.67% -> edge negative
    assert beta['value_edge_pct'] < 0
    assert beta['is_value_edge_bet'] is False


def test_edge_threshold_boundary():
    threshold = appmod.VALUE_EDGE_MIN_THRESHOLD_PCT
    # ml_a/ml_b sum to 100 so fair probability (as a %) equals ml_a directly.
    # market implied = 100/4 = 25%; want fair - 25 == threshold -> fair = 25+threshold
    fair_needed = 25.0 + threshold
    out = evaluate_pair(price_a=4.0, price_b=100.0, ml_a=fair_needed, ml_b=100.0 - fair_needed)
    alpha = out[1]
    assert alpha['is_value_edge_bet'] is True
    below = evaluate_pair(price_a=4.0, price_b=100.0, ml_a=fair_needed - 0.5, ml_b=100.0 - (fair_needed - 0.5))[1]
    assert below['is_value_edge_bet'] is False


def test_missing_price_or_ml_score_never_qualifies():
    a = horse(1, 'Alpha', 100, None)  # no ml_score -> no book entry
    b = horse(2, 'Beta', 90, 20)
    race = SimpleNamespace(horses=[a, b])
    odds = {'status': 'Open', 'fetched_at': 't', 'age_seconds': 0, 'odds': {
        appmod.normalize_runner_name('Alpha'): {'name': 'Alpha', 'win': 5.0, 'is_scratched': False, 'is_available': True},
        appmod.normalize_runner_name('Beta'): {'name': 'Beta', 'win': 1.5, 'is_scratched': False, 'is_available': True},
    }}
    out = appmod.evaluate_ladbrokes_best_bet_signals(race, SimpleNamespace(), odds)
    assert out[1]['ml_fair_probability_pct'] is None
    assert out[1]['value_edge_pct'] is None
    assert out[1]['is_value_edge_bet'] is False


def test_closed_market_never_qualifies():
    out = evaluate_pair(status='Closed')
    assert out[1]['value_edge_pct'] is None
    assert out[1]['is_value_edge_bet'] is False


def test_value_edge_does_not_change_existing_badge_counting():
    # A horse with a positive edge alongside existing sweet-spot/consensus/gap
    # signals must not push best_bet_signal_count or best_bet_confidence_level
    # beyond what the pre-existing three qualitative badges produce.
    a = horse(1, 'Alpha', 100, 80)
    a.csv_data = {'pfaiScore': 100}  # also ranks #1 on PFAI so Full Consensus fires
    b = horse(2, 'Beta', 90, 55)  # 25-point ml gap, both favour Alpha at $3 as ML top pick
    b.csv_data = {'pfaiScore': 90}
    race = SimpleNamespace(horses=[a, b])
    odds = {'status': 'Open', 'fetched_at': 't', 'age_seconds': 0, 'odds': {
        appmod.normalize_runner_name('Alpha'): {'name': 'Alpha', 'win': 3.0, 'is_scratched': False, 'is_available': True},
        appmod.normalize_runner_name('Beta'): {'name': 'Beta', 'win': 5.0, 'is_scratched': False, 'is_available': True},
    }}
    out = appmod.evaluate_ladbrokes_best_bet_signals(race, SimpleNamespace(), odds)
    alpha = out[1]
    assert alpha['best_bet_signal_count'] == 3
    assert alpha['best_bet_confidence_level'] == 'Elite Consensus Best Bet'
    # Additive field present alongside, unaffected by/not affecting the badge count
    assert alpha['is_value_edge_bet'] is True


def test_value_edge_threshold_is_a_single_module_constant():
    # Used by Best Bets section, pre-race capture, and the ML Data page — not
    # duplicated as separate hardcoded numbers.
    assert APP_SOURCE.count('VALUE_EDGE_MIN_THRESHOLD_PCT = 8.0') == 1
    assert 'value_edge_min_threshold_pct=VALUE_EDGE_MIN_THRESHOLD_PCT' in APP_SOURCE


def test_best_bets_route_captures_value_edge_snapshot_once():
    source = APP_SOURCE[APP_SOURCE.index('def best_bets('):]
    source = source[:source.index('\n@app.route(', 1)]
    assert 'value_edge_captured_at' in source
    assert 'value_edge_pct' in source
    assert 'value_edge_ml_win_prob_pct' in source
    assert 'value_edge_price' in source
    assert 'if horse.prediction.value_edge_captured_at is None' in source


def test_calculate_value_edge_performance_buckets_and_stake():
    source = APP_SOURCE[APP_SOURCE.index('def calculate_value_edge_performance('):]
    source = source[:source.index('\n\n\n', 1)]
    assert 'stake=10.0' in source
    assert 'Prediction.value_edge_captured_at.isnot(None)' in source
    assert 'avg_edge_pct' in source


def test_ml_data_route_wires_value_edge_performance():
    start = APP_SOURCE.index('def ml_data_analytics(')
    end = APP_SOURCE.index('\n@app.route(', start)
    source = APP_SOURCE[start:end]
    assert 'calculate_value_edge_performance(' in source
    assert 'value_edge_performance=value_edge_performance' in source


def test_ml_data_template_has_value_edge_section():
    template = Path('templates/ml_data.html').read_text()
    assert 'ML Value Edge Bets' in template
    assert 'value_edge_performance.overall' in template
    assert 'value_edge_performance.buckets' in template


def test_best_bets_template_has_value_edge_section():
    template = Path('templates/best_bets.html').read_text()
    assert 'ML Value Edge Bets' in template
    assert 'value_edge_bets' in template
