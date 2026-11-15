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


def test_the_lay_rule_is_kept_apart_from_the_picks():
    wary = _sig(a=True, pf=True, t=True, ml_other=True)
    assert bbr.rules_for(wary, True, 2.9, 9) == []                    # never a horse to back
    lay = bbr.lay_rules_for(wary, True)
    assert _keys(lay) == ['lay_fav_ml_other_3of4']
    assert lay[0].bet == 'lay' and lay[0].name.startswith('LAY:')
    assert bbr.lay_rules_for(wary, False) == []                        # not the favourite
    assert bbr.lay_rules_for(wary, True, has_ratings=False) == []      # no saved ratings
    assert bbr.lay_rules_for(_sig(a=True, pf=True, ml_other=True), True) == []      # only two ratings
    assert bbr.lay_rules_for(_sig(a=True, pf=True, t=True, ml=True), True) == []    # the ML picks it
    assert bbr.lay_rules_for(_sig(a=True, pf=True, t=True), True) == []             # no ML pick in the race
    # No rule key is shared, and no lay key can be counted as a win bet.
    assert not set(_keys(bbr.RULES)) & set(_keys(bbr.LAY_RULES))
    for rule in bbr.LAY_RULES:
        assert rule.key.startswith('lay_') and rule.bet == 'lay'
    for rule in bbr.RULES:
        assert rule.bet != 'lay'


def test_a_lay_is_never_posted_as_a_bet_to_back():
    lay = bbr.recorded_rules('lay_fav_ml_other_3of4')
    assert bbr.bet_type(lay) == 'lay'
    # Even if the same horse was once listed as a pick, lay wins.
    both = bbr.recorded_rules('agree_ml_fav_quiet,lay_fav_ml_other_3of4')
    assert _keys(both) == ['agree_ml_fav_quiet', 'lay_fav_ml_other_3of4']
    assert bbr.bet_type(both) == 'lay'
    import betfair_check
    assert "NOT LIKE 'lay%'" in betfair_check._BEST_BET_BASE


def test_lay_return_risks_one_unit():
    assert bbr.lay_return(True, 3.0) == -1.0                 # the horse won: the unit risked is lost
    assert round(bbr.lay_return(False, 3.0), 4) == 0.46      # it lost: 0.92 / (3.0 - 1)
    assert round(bbr.lay_return(False, 1.5), 4) == 1.84


def test_recorded_rules_and_bet_type():
    rules = bbr.recorded_rules('agree_ml_fav_quiet,place_32_tdnowin')
    assert _keys(rules) == ['agree_ml_fav_quiet']
    assert bbr.bet_type(rules) == 'win'
    # Routes saved by rules that have since been removed are ignored.
    assert bbr.recorded_rules('agree_ml_pfai_fav,agree_fav_3of4_mid,agree_fav_3of4_odds_on') == []
    assert bbr.recorded_rules(None) == [] and bbr.recorded_rules('') == []


def test_chart_runs_a_separate_line_for_picks_and_lay_bets():
    """The Best Bets graph: $10 win bets for the picks, $10 risked for the lay bets,
    counting only bets listed before the jump."""
    import sqlite3
    from types import SimpleNamespace

    class Session:
        def __init__(self):
            self.c = sqlite3.connect(':memory:')

        def execute(self, sql, params=None):
            rows = self.c.execute(str(sql), params or {}).fetchall()
            return SimpleNamespace(fetchall=lambda: rows)

    s = Session()
    s.c.executescript("""
        create table meetings(id integer primary key, date text);
        create table races(id integer primary key, meeting_id int, start_time_utc text, results_incomplete int);
        create table horses(id integer primary key, race_id int);
        create table predictions(id integer primary key, horse_id int, best_bet_routes text, best_bet_qualified_at text);
        create table results(id integer primary key, horse_id int, finish_position int, sp real, bsp real);
        insert into meetings values(1,'2026-11-16'),(2,'2026-11-17');
        insert into races values(1,1,'2026-11-16 05:00:00',null),(2,2,'2026-11-17 05:00:00',null);
        insert into horses values(1,1),(2,1),(3,2),(4,2),(5,2);
        insert into predictions values
          (1,1,'agree_ml_fav_quiet','2026-11-16 03:00:00'),
          (2,2,'lay_fav_ml_other_3of4','2026-11-16 03:00:00'),
          (3,3,'agree_ml_fav_quiet','2026-11-17 03:00:00'),
          (4,4,'lay_fav_ml_other_3of4','2026-11-17 03:00:00'),
          (5,5,'agree_ml_fav_quiet','2026-11-17 09:00:00');
        insert into results(horse_id,finish_position,sp,bsp) values
          (1,1,3.0,3.4),(2,5,3.0,3.0),(3,5,2.5,null),(4,1,2.0,null),(5,1,9.0,9.0);
    """)
    out = bbr.chart(s)
    assert out['labels'] == ['2026-11-16', '2026-11-17']
    picks, lay = out['series']
    # Picks: +$20 (won at $3), then -$10. The pick listed after the jump is left out.
    assert (picks['kind'], picks['bets'], picks['won'], picks['profit'], picks['points']) == ('pick', 2, 1, 10.0, [20.0, 10.0])
    # Lay: the horse lost at Betfair $3 -> +$4.60. Then a horse won -> -$10, priced at the
    # starting price because the Betfair price has not loaded (an estimate).
    assert (lay['kind'], lay['bets'], lay['won'], lay['profit'], lay['points']) == ('lay', 2, 1, -5.4, [4.6, -5.4])
    assert lay['estimated'] == 1 and picks['estimated'] == 0
    s.c.execute("delete from predictions")
    assert bbr.chart(s)['labels'] == []
