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
    ratings under $2 were removed. 21 Nov 2026: the $2-$3 ML + PFAI trial was added."""
    assert _keys(bbr.RULES) == ['agree_ml_fav_quiet', 'agree_fav_ml_pf_2to3']
    # $3.00 is outside the trial's $2-$3 band, so only the quiet rule fits.
    assert _keys(bbr.rules_for(_sig(ml=True, pf=True), True, 3.0, 12)) == ['agree_ml_fav_quiet']
    assert _keys(bbr.rules_for(_sig(ml=True, pf=True, t=True), True, 3.0, 12)) == []
    three = _sig(a=True, pf=True, t=True)
    assert bbr.rules_for(three, True, 2.6, 9) == []
    assert bbr.rules_for(three, True, 1.9, 12) == []
    assert not hasattr(bbr, 'to_avoid') and not hasattr(bbr, 'AVOID_NOTE')


def test_ml_and_pfai_favourite_at_two_to_three_dollars():
    """21 Nov 2026 trial: the favourite at $2-$3, top pick of the ML and PFAI, not of the analyzer."""
    both = _sig(ml=True, pf=True)
    assert _keys(bbr.rules_for(both, True, 2.0, 9)) == ['agree_ml_fav_quiet', 'agree_fav_ml_pf_2to3']
    assert _keys(bbr.rules_for(both, True, 2.99, 9)) == ['agree_ml_fav_quiet', 'agree_fav_ml_pf_2to3']
    # Time and class ratings also on top: no longer "quiet", but the trial still fits.
    assert _keys(bbr.rules_for(_sig(ml=True, pf=True, t=True, w=True), True, '2.40', 9)) == ['agree_fav_ml_pf_2to3']
    # Outside $2-$3, or no readable price: not the trial.
    for price in (1.99, 3.0, 4.5, None, '', 'SP'):
        assert 'agree_fav_ml_pf_2to3' not in _keys(bbr.rules_for(both, True, price, 9))
    # The analyzer also has it on top, PFAI does not, the ML does not, or not the favourite: not the trial.
    assert 'agree_fav_ml_pf_2to3' not in _keys(bbr.rules_for(_sig(ml=True, pf=True, a=True), True, 2.5, 9))
    assert 'agree_fav_ml_pf_2to3' not in _keys(bbr.rules_for(_sig(ml=True), True, 2.5, 9))
    assert bbr.rules_for(_sig(pf=True, ml_other=True), True, 2.5, 9) == []
    assert bbr.rules_for(both, False, 2.5, 9) == []
    assert bbr.rules_for(both, True, 2.5, 9, has_ratings=False) == []
    # Saved routes for both rules read back as two win rules.
    rules = bbr.recorded_rules('agree_ml_fav_quiet,agree_fav_ml_pf_2to3')
    assert _keys(rules) == ['agree_ml_fav_quiet', 'agree_fav_ml_pf_2to3'] and bbr.bet_type(rules) == 'win'


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


def test_chart_runs_a_line_per_rule_with_one_point_per_bet():
    """The Best Bets graph: $10 win bets for the picks, $10 risked for the lay bets,
    counting only bets listed before the jump, one point per bet in race order."""
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
        create table meetings(id integer primary key, date text, meeting_name text);
        create table races(id integer primary key, meeting_id int, race_number int, start_time_utc text, results_incomplete int);
        create table horses(id integer primary key, race_id int, horse_name text);
        create table predictions(id integer primary key, horse_id int, best_bet_routes text, best_bet_qualified_at text);
        create table results(id integer primary key, horse_id int, finish_position int, sp real, bsp real);
        insert into meetings values(1,'2026-11-16','Moe'),(2,'2026-11-17','Kyneton');
        insert into races values(1,1,4,'2026-11-16 05:00:00',null),(2,2,7,'2026-11-17 05:00:00',null);
        insert into horses values(1,1,'Alpha'),(2,1,'Bravo'),(3,2,'Charlie'),(4,2,'Delta'),(5,2,'Echo');
        insert into predictions values
          (1,1,'agree_ml_fav_quiet,agree_fav_ml_pf_2to3','2026-11-16 03:00:00'),
          (2,2,'lay_fav_ml_other_3of4','2026-11-16 03:00:00'),
          (3,3,'agree_ml_fav_quiet','2026-11-17 03:00:00'),
          (4,4,'lay_fav_ml_other_3of4','2026-11-17 03:00:00'),
          (5,5,'agree_ml_fav_quiet','2026-11-17 09:00:00');
        insert into results(horse_id,finish_position,sp,bsp) values
          (1,1,3.0,3.4),(2,5,3.0,3.0),(3,5,2.5,null),(4,1,2.0,null),(5,1,9.0,9.0);
    """)
    out = bbr.chart(s)
    quiet, trial, lay = out['series']
    assert [x['key'] for x in out['series']] == ['agree_ml_fav_quiet', 'agree_fav_ml_pf_2to3', 'lay_fav_ml_other_3of4']
    # Quiet rule: +$20 (won at $3), then -$10. The pick listed after the jump is left out.
    assert (quiet['kind'], quiet['bets'], quiet['won'], quiet['profit']) == ('pick', 2, 1, 10.0)
    assert [h['cumulative'] for h in quiet['history']] == [20.0, 10.0]
    assert [h['profit'] for h in quiet['history']] == [20.0, -10.0]
    first = quiet['history'][0]
    assert (first['date'], first['meeting'], first['race'], first['horse'], first['price'], first['won']) == \
        ('2026-11-16', 'Moe', 4, 'Alpha', 3.0, True)
    # A horse that fits both rules is a bet on each line.
    assert (trial['bets'], trial['won'], trial['profit']) == (1, 1, 20.0)
    assert trial['short'] == 'ML + PFAI favourite $2-3'
    # Lay: the horse lost at Betfair $3 -> +$4.60. Then a horse won -> -$10, priced at the
    # starting price because the Betfair price has not loaded (an estimate).
    assert (lay['kind'], lay['bets'], lay['won'], lay['profit']) == ('lay', 2, 1, -5.4)
    assert [h['cumulative'] for h in lay['history']] == [4.6, -5.4]
    assert [h['estimate'] for h in lay['history']] == [False, True]
    assert lay['estimated'] == 1 and quiet['estimated'] == 0
    s.c.execute("delete from predictions")
    assert all(x['bets'] == 0 and x['history'] == [] for x in bbr.chart(s)['series'])
