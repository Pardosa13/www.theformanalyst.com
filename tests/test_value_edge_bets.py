from pathlib import Path

import app as appmod

APP_SOURCE = Path('app.py').read_text()


def test_value_edge_threshold_is_a_single_module_constant():
    # Used by the ML Data page and the scoring run — not
    # duplicated as separate hardcoded numbers.
    assert APP_SOURCE.count('VALUE_EDGE_MIN_THRESHOLD_PCT = 8.0') == 1
    assert 'value_edge_min_threshold_pct=VALUE_EDGE_MIN_THRESHOLD_PCT' in APP_SOURCE


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


def test_promote_threshold_is_a_single_module_constant():
    assert APP_SOURCE.count('VALUE_EDGE_PROMOTE_TO_NORMAL_THRESHOLD_PCT = 10.0') == 1


def test_value_edge_buckets_cover_every_edge_level():
    """ML Data tracks outcomes at every edge level, not only the levels that
    qualify as a Best Bet — that is how the promote cutoff gets tested."""
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

    # The bettable band starts exactly where the Best Bets page's gate does:
    # some bucket's lower bound is the promote threshold, so the "we bet this"
    # marking never cuts a bucket in half.
    threshold = appmod.VALUE_EDGE_PROMOTE_TO_NORMAL_THRESHOLD_PCT
    assert threshold in [lower for _k, _l, lower, _u in buckets]


def test_best_bets_route_also_shows_maiden_triple_agreement():
    """Analyzer + PFAI + ML agreement in a maiden race qualifies on its own."""
    source = APP_SOURCE[APP_SOURCE.index('def best_bets('):]
    source = source[:source.index('\n@app.route(', 1)]
    assert 'maiden_agreement = signal_agreement and is_maiden_race(race.race_class)' in source
    assert 'MAIDEN_AGREEMENT_BADGE' in source


def test_best_bets_route_also_shows_favourite_triple_agreement():
    """Analyzer + PFAI + ML agreement on the Ladbrokes favourite qualifies too."""
    source = APP_SOURCE[APP_SOURCE.index('def best_bets('):]
    source = source[:source.index('\n@app.route(', 1)]
    assert "favourite_agreement = signal_agreement and bool(lb_fields.get('is_full_model_market_consensus'))" in source


def test_is_maiden_race_matches_maiden_and_mdn():
    from app import is_maiden_race
    assert is_maiden_race('Maiden Plate')
    assert is_maiden_race('MDN-SW')
    assert is_maiden_race('3yo mdn')
    assert not is_maiden_race('Benchmark 64')
    assert not is_maiden_race(None)


def test_best_bets_page_has_no_value_edge():
    """Value edge is gone from the Best Bets page: no panel, no gate, no badge,
    no capture. Only maiden, favourite or Proven Edge agreement qualifies a horse."""
    source = APP_SOURCE[APP_SOURCE.index('def best_bets('):]
    source = source[:source.index('\n@app.route(', 1)]
    assert 'value_edge' not in source
    assert "('maiden', maiden_agreement)" in source
    assert "('favourite', favourite_agreement)" in source
    assert "('proven_edge', proven_edge_agreement)" in source
    assert 'if routes:' in source
    signals = APP_SOURCE[APP_SOURCE.index('def evaluate_ladbrokes_best_bet_signals('):]
    signals = signals[:signals.index('\n\n\n', 1)]
    assert 'value_edge' not in signals
    assert 'Value Edge' not in signals
    assert '_value_edge_fields_with_stored_fallback' not in APP_SOURCE
    template = Path('templates/best_bets.html').read_text().lower()
    assert 'value edge' not in template
    assert 'value_edge' not in template
