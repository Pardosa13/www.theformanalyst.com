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


def test_promote_threshold_boundary():
    threshold = appmod.VALUE_EDGE_PROMOTE_TO_NORMAL_THRESHOLD_PCT
    # market implied = 100/4 = 25%; want fair - 25 == threshold -> fair = 25+threshold
    fair_needed = 25.0 + threshold
    out = evaluate_pair(price_a=4.0, price_b=100.0, ml_a=fair_needed, ml_b=100.0 - fair_needed)
    alpha = out[1]
    assert alpha['is_value_edge_promoted'] is True
    assert any('ML Value Edge' in badge for badge in alpha['best_bet_badges'])
    below = evaluate_pair(price_a=4.0, price_b=100.0, ml_a=fair_needed - 0.5, ml_b=100.0 - (fair_needed - 0.5))[1]
    assert below['is_value_edge_promoted'] is False
    assert not any('ML Value Edge' in badge for badge in below['best_bet_badges'])


def test_promote_does_not_change_qualitative_badge_counting():
    # A promoted horse alongside existing sweet-spot/consensus/gap signals must
    # not push best_bet_signal_count or best_bet_confidence_level beyond what
    # the pre-existing three qualitative badges produce.
    a = horse(1, 'Alpha', 100, 95)
    a.csv_data = {'pfaiScore': 100}  # also ranks #1 on PFAI so Full Consensus fires
    b = horse(2, 'Beta', 90, 5)  # 90-point ml gap, both favour Alpha as ML top pick
    b.csv_data = {'pfaiScore': 90}
    race = SimpleNamespace(horses=[a, b])
    odds = {'status': 'Open', 'fetched_at': 't', 'age_seconds': 0, 'odds': {
        appmod.normalize_runner_name('Alpha'): {'name': 'Alpha', 'win': 3.0, 'is_scratched': False, 'is_available': True},
        appmod.normalize_runner_name('Beta'): {'name': 'Beta', 'win': 5.0, 'is_scratched': False, 'is_available': True},
    }}
    out = appmod.evaluate_ladbrokes_best_bet_signals(race, SimpleNamespace(), odds)
    alpha = out[1]
    assert alpha['is_value_edge_promoted'] is True
    assert alpha['best_bet_signal_count'] == 3
    assert alpha['best_bet_confidence_level'] == 'Elite Consensus Best Bet'


def test_promote_threshold_is_a_single_module_constant():
    assert APP_SOURCE.count('VALUE_EDGE_PROMOTE_TO_NORMAL_THRESHOLD_PCT = 20.0') == 1


def test_best_bets_route_shows_only_horses_at_or_above_the_promote_threshold():
    """Value edge is the gate for the Best Bets page, not one qualifier among
    several. A component match, an 80% win probability, a sole ride or a
    consensus badge no longer puts a horse on the page by itself — only an edge
    of at least VALUE_EDGE_PROMOTE_TO_NORMAL_THRESHOLD_PCT does. Smaller edges
    are still captured on `predictions` and reported by the ML Data buckets."""
    source = APP_SOURCE[APP_SOURCE.index('def best_bets('):]
    source = source[:source.index('\n@app.route(', 1)]
    assert "value_edge_promoted = bool(lb_fields.get('is_value_edge_promoted'))" in source
    assert 'if value_edge_promoted:' in source
    # The old "any one of these qualifies" gate must be gone.
    assert 'or value_edge_promoted:' not in source
    assert 'if matched_components or wp >= 80' not in source


def test_best_bets_falls_back_to_the_stored_edge_when_the_live_fetch_is_empty():
    """The in-request Ladbrokes fetch is not the only source of an edge: the ML
    scoring run persists one computed from live_odds_snapshots. Gating the page
    on the edge would hide everything whenever that fetch fails, so the stored
    edge stands in."""
    source = APP_SOURCE[APP_SOURCE.index('def best_bets('):]
    source = source[:source.index('\n@app.route(', 1)]
    assert '_value_edge_fields_with_stored_fallback(' in source

    prediction = SimpleNamespace(
        value_edge_pct=25.0, value_edge_ml_win_prob_pct=45.0, value_edge_price=4.0,
    )
    out = appmod._value_edge_fields_with_stored_fallback({'value_edge_pct': None}, prediction)
    assert out['value_edge_pct'] == 25.0
    assert out['is_value_edge_promoted'] is True
    assert out['ml_fair_probability_pct'] == 45.0
    assert out['ladbrokes_fixed_win_price'] == 4.0
    assert out['market_implied_probability_pct'] == 25.0


def test_stored_edge_never_overwrites_a_live_one():
    prediction = SimpleNamespace(
        value_edge_pct=25.0, value_edge_ml_win_prob_pct=45.0, value_edge_price=4.0,
    )
    live = {'value_edge_pct': 3.0, 'ml_fair_probability_pct': 20.0}
    out = appmod._value_edge_fields_with_stored_fallback(live, prediction)
    assert out['value_edge_pct'] == 3.0
    assert live['value_edge_pct'] == 3.0  # input untouched


def test_value_edge_buckets_cover_every_edge_level():
    """ML Data tracks outcomes at every edge level, not only the levels that
    qualify as a Best Bet — that is how the 20pp cutoff gets tested."""
    buckets = appmod.VALUE_EDGE_BUCKETS
    keys = [key for key, _label, _lower, _upper in buckets]
    assert keys == ['below_0', '0_5', '5_10', '10_15', '15_20', '20_plus']

    # Contiguous and total: every possible edge lands in exactly one bucket.
    assert buckets[0][2] is None
    assert buckets[-1][3] is None
    for (_k, _l, _lower, upper), (_k2, _l2, next_lower, _u2) in zip(buckets, buckets[1:]):
        assert upper == next_lower

    for edge in (-40.0, -0.01, 0.0, 4.9, 5.0, 14.99, 19.99, 20.0, 250.0):
        matched = [
            key for key, _label, lower, upper in buckets
            if (lower is None or edge >= lower) and (upper is None or edge < upper)
        ]
        assert matched == [matched[0]], f'edge {edge} matched {matched}'

    # The bettable bucket starts exactly where the Best Bets page's gate does.
    assert buckets[-1][2] == appmod.VALUE_EDGE_PROMOTE_TO_NORMAL_THRESHOLD_PCT


def test_best_bets_route_only_displays_promoted_edge_bets_but_tracks_all():
    # The ML Value Edge Bets panel on the Best Bets page should only list
    # horses that clear the 20pp promotion threshold, while the DB snapshot
    # capture (used by the ML Data page's 8-12/12-20/20+ buckets) still fires
    # for every horse at/above the lower 8pp threshold.
    source = APP_SOURCE[APP_SOURCE.index('def best_bets('):]
    source = source[:source.index('\n@app.route(', 1)]
    assert "if edge_fields.get('is_value_edge_bet'):" in source
    assert "if edge_fields.get('is_value_edge_promoted'):\n                        value_edge_bets.append(" in source
    assert 'value_edge_min_threshold_pct=VALUE_EDGE_PROMOTE_TO_NORMAL_THRESHOLD_PCT' in source
    assert 'value_edge_track_min_threshold_pct=VALUE_EDGE_MIN_THRESHOLD_PCT' in source


def test_best_bets_template_shows_both_thresholds():
    template = Path('templates/best_bets.html').read_text()
    assert 'value_edge_min_threshold_pct' in template
    assert 'value_edge_track_min_threshold_pct' in template
