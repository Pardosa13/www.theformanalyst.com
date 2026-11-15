"""Best Bets rule from 15 Nov 2026: a favourite the ML also picks, where at
most one other rating agrees (best_bet_rules.py)."""
import json
from types import SimpleNamespace

import best_bet_rules as bbr


def _race(runners, ratings=None, race_number=3):
    """runners: (id, tab, analyzer score, ml score, scratched)."""
    horses = [SimpleNamespace(id=hid, csv_data={'horse number': tab}, is_scratched=scratched,
                              prediction=SimpleNamespace(score=score, ml_score=ml))
              for hid, tab, score, ml, scratched in runners]
    return SimpleNamespace(race_number=race_number, horses=horses, ratings_json=ratings)


def _ratings(rows, race_no=3):
    """rows: (tab, pfai rank, time rank, class rank)."""
    return {'payLoad': [{'raceNo': race_no, 'tabNo': tab, 'pfaiRank': pf, 'timeRank': t, 'weightClassRank': w}
                        for tab, pf, t, w in rows]}


def _sig(**on):
    s = {'a': False, 'ml': False, 'pf': False, 't': False, 'w': False, 'ml_other': False}
    s.update(on)
    s['n4'] = sum(1 for key in ('a', 'pf', 't', 'w') if s[key])
    return s


def _keys(rules):
    return [rule.key for rule in rules]


def test_rule_keys_are_unique_never_inside_each_other_and_not_place_routes():
    keys = [rule.key for rule in bbr.RULES]
    assert len(keys) == len(set(keys))
    for key in keys:
        # betfair_check counts every route that does not start with "place" as a win bet.
        assert key.startswith('agree_')
        assert len(key) <= 30                      # several must fit Prediction.best_bet_routes (100 chars)
        assert not any(key != other and key in other for other in keys)
    assert len(','.join(keys)) <= 100
    for rule in bbr.RULES:
        assert rule.bet in ('win', 'place')


def test_only_a_favourite_is_ever_picked():
    assert bbr.rules_for(_sig(ml=True), False, 2.8, 9) == []
    assert bbr.rules_for(_sig(ml=True), True, 2.8, 9) != []
    assert bbr.rules_for(None, True, 2.8, 9) == []


def test_ml_favourite_with_few_other_ratings():
    assert _keys(bbr.rules_for(_sig(ml=True), True, 3.0, 12)) == ['agree_ml_fav_quiet']
    assert _keys(bbr.rules_for(_sig(ml=True, a=True), True, 3.0, 12)) == ['agree_ml_fav_quiet']
    # Two other ratings on top: no longer "quiet".
    assert _keys(bbr.rules_for(_sig(ml=True, a=True, t=True), True, 3.0, 12)) == []
    # The ML picks another horse: not this rule.
    assert _keys(bbr.rules_for(_sig(a=True, ml_other=True), True, 3.0, 12)) == []


def test_rules_taken_off_the_page_no_longer_pick():
    """15 Nov 2026: ML + PFAI + favourite, 3+ ratings in 8-11 runners and 3+
    ratings under $2 were removed. Only the quiet ML favourite is left."""
    assert _keys(bbr.RULES) == ['agree_ml_fav_quiet']
    assert _keys(bbr.rules_for(_sig(ml=True, pf=True), True, 3.0, 12)) == ['agree_ml_fav_quiet']
    assert _keys(bbr.rules_for(_sig(ml=True, pf=True, t=True), True, 3.0, 12)) == []
    three = _sig(a=True, pf=True, t=True)
    assert bbr.rules_for(three, True, 2.6, 9) == []
    assert bbr.rules_for(three, True, 1.9, 12) == []
    assert not hasattr(bbr, 'to_avoid') and not hasattr(bbr, 'AVOID_NOTE')


def test_no_saved_ratings_means_no_picks():
    assert bbr.rules_for(_sig(ml=True), True, 3.0, 12, has_ratings=False) == []


def test_race_signals_read_the_top_picks_and_the_saved_ratings():
    race = _race([(1, 1, 90, 10, False), (2, 2, 50, 80, False), (3, '3.0', 40, None, False), (4, 4, 99, 99, True)],
                 ratings=_ratings([(1, 2, 1, 1), (2, 1, 3, 2), (3, 3, 2, 3), (4, 1, 1, 1)]))
    signals, has_ratings = bbr.race_signals(race)
    assert has_ratings
    assert set(signals) == {1, 2, 3}                       # the scratched runner is left out
    assert signals[1] == {'a': True, 'ml': False, 'pf': False, 't': True, 'w': True, 'n4': 3, 'ml_other': True}
    assert signals[2] == {'a': False, 'ml': True, 'pf': True, 't': False, 'w': False, 'n4': 1, 'ml_other': False}
    assert signals[3]['n4'] == 0 and signals[3]['ml_other']


def test_ratings_are_this_race_only_and_survive_being_saved_as_text():
    blob = _ratings([(1, 1, 1, 1)], race_no=3)
    blob['payLoad'] += _ratings([(1, 5, 5, 5)], race_no=4)['payLoad']
    race = _race([(1, 1, 90, 10, False)], ratings=json.dumps(json.dumps(blob)))
    assert bbr.ratings_ranks(race) == {1: {'pf': 1, 't': 1, 'w': 1}}
    assert bbr.ratings_ranks(_race([], ratings=None)) == {}
    assert bbr.ratings_ranks(_race([], ratings='not json')) == {}
    signals, has_ratings = bbr.race_signals(_race([(1, 1, 90, 10, False)], ratings=None))
    assert not has_ratings and signals[1]['n4'] == 1


def test_no_ml_score_in_the_race():
    race = _race([(1, 1, 90, None, False), (2, 2, 50, None, False)], ratings=_ratings([(1, 1, 1, 2), (2, 2, 2, 1)]))
    signals, _has = bbr.race_signals(race)
    assert not signals[1]['ml'] and not signals[1]['ml_other']
    # The rule needs the ML's pick, so a race the ML has not scored has no picks.
    assert bbr.rules_for(signals[1], True, 2.4, 9) == []
    assert bbr.rules_for(signals[2], True, 2.4, 9) == []


def test_recorded_rules_and_bet_type():
    rules = bbr.recorded_rules('agree_ml_fav_quiet,place_32_tdnowin')
    assert _keys(rules) == ['agree_ml_fav_quiet']
    assert bbr.bet_type(rules) == 'win'
    # Routes saved by rules that have since been removed are ignored.
    assert bbr.recorded_rules('agree_ml_pfai_fav,agree_fav_3of4_mid,agree_fav_3of4_odds_on') == []
    assert bbr.recorded_rules(None) == [] and bbr.recorded_rules('') == []
