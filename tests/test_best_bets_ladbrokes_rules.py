from types import SimpleNamespace

import app as appmod


def pred(score, ml):
    return SimpleNamespace(score=score, ml_score=ml, notes='PFAI Score: %.1f' % score)


def horse(i, name, analyzer, pfai, ml, scratched=False):
    return SimpleNamespace(id=i, horse_name=name, is_scratched=scratched, csv_data={'pfaiScore': pfai}, prediction=pred(analyzer, ml))


def evaluate(price=3.0, other_price=5.0, gap=20, fav='A', scratched_b=False, status='Open', age=0, missing=False):
    a = horse(1, 'Alpha Star', 100, 100, 80)
    b = horse(2, 'Beta Boy', 90, 90, 80-gap, scratched_b)
    race = SimpleNamespace(horses=[a, b])
    odds = {'status': status, 'fetched_at': '2026-07-13T00:00:00Z', 'age_seconds': age, 'odds': {
        appmod.normalize_runner_name('Alpha Star'): {'name':'Alpha Star','win': None if missing else price, 'is_scratched': False, 'is_available': True},
        appmod.normalize_runner_name('Beta Boy'): {'name':'Beta Boy','win': other_price, 'is_scratched': scratched_b, 'is_available': True},
    }}
    if fav == 'B':
        odds['odds'][appmod.normalize_runner_name('Beta Boy')]['win'] = 2.0
    return appmod.evaluate_ladbrokes_best_bet_signals(race, SimpleNamespace(), odds)[1]


def test_favourite_badges_are_gone():
    """Sweet Spot, Full Model + Market Consensus, 20 Gap and Elite were removed
    on 16 Oct 2026: over all history they lost 7-12%, no better than backing
    every favourite. The evaluator now returns market context only."""
    got = evaluate(price=3.0, gap=25)
    for key in ('is_ml_market_sweet_spot', 'is_full_model_market_consensus', 'is_ml_market_gap_20',
                'best_bet_signal_count', 'best_bet_confidence_level'):
        assert key not in got
    assert got['best_bet_badges'] == []
    assert got['ladbrokes_fixed_win_price'] == 3.0
    assert got['is_ladbrokes_favourite']
    assert got['analyzer_rank'] == got['pfai_rank'] == got['ml_rank'] == 1
    assert got['ml_score_gap'] == 25


def test_joint_favourites_and_scratched_removed_before_ranking():
    tied = evaluate(price=3.0, other_price=3.0)
    assert tied['is_ladbrokes_favourite'] and tied['is_joint_ladbrokes_favourite']
    scratched_other = evaluate(price=3.0, other_price=2.0, scratched_b=True)
    assert scratched_other['ladbrokes_market_rank'] == 1


def test_missing_stale_unmatched_and_closed_market_report_why():
    assert evaluate(missing=True)['ladbrokes_fixed_win_price'] is None
    assert evaluate(age=appmod.BEST_BETS_LADBROKES_STALE_SECONDS + 1)['best_bet_reasons']
    assert evaluate(status='Closed')['best_bet_reasons']
    a = horse(1, 'Alpha Star', 100, 100, 80); b = horse(2, 'Beta Boy', 90, 90, 60)
    race = SimpleNamespace(horses=[a,b])
    odds = {'status':'Open','odds': {appmod.normalize_runner_name('Other Horse'):{'name':'Other Horse','win':2.0}}}
    assert appmod.evaluate_ladbrokes_best_bet_signals(race, SimpleNamespace(), odds)[1]['ladbrokes_fixed_win_price'] is None


def test_evaluator_never_reads_results_sp():
    import inspect
    src = inspect.getsource(appmod.evaluate_ladbrokes_best_bet_signals)
    assert 'results.sp' not in src and '.sp' not in src
