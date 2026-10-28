"""Place rules: the only Best Bets. Each is settled on the place, never the win.

Notes below are the exact lines the analyzer writes (analyzer.js), so these
tests also prove the parser turns them into the component names the rules
need.
"""
import os
from datetime import datetime

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as appmod
import place_bet
from models import db, User, Meeting, Race, Horse, Prediction, Result

LIGHT = "+10.0 : Weight 54.0kg is 2.5kg below race avg (56.5kg)"
HEAVY = "-10.0 : Weight 60.0kg is 2.5kg above race avg (57.5kg)"
TRACK_PODIUM = "+ 4.0 : Strong podium rate (60%) at this track"
PLACES_32 = "+4.0 : Ran places: 3rd 2nd"
PLACES_23 = "+4.0 : Ran places: 2nd 3rd"
TD_NO_WINS = "+ 0.0 : No wins at this track+distance"
POOR_TRACK = "- 5.0 : Poor performance at this track (5 runs, 0 wins, <20% podium)"
GOING_PODIUM = "+ 9.0 : Strong podium rate (60%) on good"
BACK_SPRINT = "- 8.0 : BACKMARKER in Sprint"
ONPACE_MILE = "+ 8.0 : ONPACE in Mile - sweet spot"


def keys(csv_data, *lines, scratched=False):
    """Rule keys a runner with this form and these analyzer notes fits."""
    from types import SimpleNamespace
    horse = SimpleNamespace(csv_data=csv_data, is_scratched=scratched,
                            prediction=SimpleNamespace(notes='\n'.join(lines)))
    return [rule.key for rule in place_bet.matching_rules(horse)]


def test_each_rule_needs_every_part():
    # 2nd last start + 2-3kg light
    assert keys({'horse last10': '5432'}, LIGHT) == ['place_2nd_light']
    assert keys({'horse last10': '5431'}, LIGHT) == []
    assert keys({'horse last10': '5432'}, HEAVY) == []
    # within 1 length last start + strong place record at the track
    assert keys({'horse last10': '54', 'form margin': '0.8'}, TRACK_PODIUM) == ['place_close_trackpod']
    assert keys({'horse last10': '54', 'form margin': '1.0'}, TRACK_PODIUM) == []
    assert keys({'horse last10': '54', 'form margin': ''}, TRACK_PODIUM) == []
    assert keys({'horse last10': '54', 'form margin': '0.3'}) == []
    # last-5 placings start 3rd then 2nd + no track+distance win / poor at track
    assert keys({'horse last10': '3x52'}, PLACES_32, TD_NO_WINS) == ['place_32_tdnowin']
    assert keys({'horse last10': '3x52'}, PLACES_32, POOR_TRACK) == ['place_32_poortrack']
    assert keys({'horse last10': '2x53'}, PLACES_23, TD_NO_WINS) == []
    assert keys({'horse last10': '3x52'}, PLACES_32) == []
    # going placer + backmarker in a sprint
    assert keys({'horse last10': '66'}, GOING_PODIUM, BACK_SPRINT) == ['place_goingpod_backsprint']
    assert keys({'horse last10': '66'}, BACK_SPRINT) == []
    # won last start + on pace in a mile
    assert keys({'horse last10': '41'}, ONPACE_MILE) == ['place_won_onpace_mile']
    assert keys({'horse last10': '14'}, ONPACE_MILE) == []


def test_a_runner_can_fit_several_rules_and_scratched_fits_none():
    csv_data = {'horse last10': '3x52', 'form margin': '0.5'}
    assert keys(csv_data, LIGHT, PLACES_32, TD_NO_WINS, POOR_TRACK, TRACK_PODIUM) == [
        'place_2nd_light', 'place_close_trackpod', 'place_32_tdnowin', 'place_32_poortrack']
    assert keys(csv_data, LIGHT, scratched=True) == []


def test_saved_form_position_is_never_used_for_last_start():
    # The saved 'form position' held an older run until 28 Oct 2026; this is the morning's bug.
    assert keys({'horse last10': '5431', 'form position': '2'}, LIGHT) == []
    assert keys({'form position': '2'}, LIGHT) == []
    assert place_bet.last_start_position({'form position': '2'}) is None


def test_last_start_comes_from_the_right_end_of_the_form_string():
    # Right-most digit is the latest run; 'x' marks a spell; '0' is 10th or worse.
    assert place_bet.last_start_position({'horse last10': '2x3173534', 'form position': '2'}) == 4
    assert place_bet.last_start_position({'horse last10': '22315x20262'}) == 2
    assert place_bet.last_start_position({'horse last10': '12x'}) == 2
    assert place_bet.last_start_position({'horse last10': '310'}) == 10
    assert place_bet.last_start_position({'horse last10': ''}) is None
    assert place_bet.last_start_position('{"horse last10": "41"}') == 1


def test_margin_reading():
    assert place_bet.last_start_margin({'form margin': '0.75'}) == 0.75
    assert place_bet.last_start_margin({'form margin': '2.5L'}) == 2.5
    assert place_bet.last_start_margin({'form margin': None}) is None
    assert place_bet.last_start_margin({}) is None


def test_rule_keys_are_unique_and_never_inside_each_other():
    ks = [rule.key for rule in place_bet.RULES]
    assert len(set(ks)) == len(ks) == 6
    for a in ks:
        assert all(a == b or a not in b for b in ks)
    assert place_bet.ROUTE == 'place_2nd_light'   # the key already saved on live picks


def _setup():
    user = User(username='pb', email='pb@example.com', password_hash='x')
    db.session.add(user)
    db.session.flush()
    meeting = Meeting(user_id=user.id, meeting_name='Test', uploaded_at=datetime(2026, 10, 28),
                      date=datetime(2026, 10, 28).date())
    db.session.add(meeting)
    db.session.flush()
    race = Race(meeting_id=meeting.id, race_number=1, race_class='BM64',
                start_time_utc=datetime(2026, 10, 28, 5, 0))
    db.session.add(race)
    db.session.flush()
    return race


def _runner(race, name, csv_data, notes, fin=None, place_bsp=None):
    horse = Horse(race_id=race.id, horse_name=name, csv_data=csv_data)
    db.session.add(horse)
    db.session.flush()
    db.session.add(Prediction(horse_id=horse.id, score=50.0, notes=notes))
    if fin is not None:
        db.session.add(Result(horse_id=horse.id, finish_position=fin, sp=4.0))
    return horse


def test_best_bets_routes_are_only_place_rules():
    with appmod.app.app_context():
        db.create_all()
        try:
            race = _setup()
            yes = _runner(race, 'Yes', {'horse last10': '52'}, LIGHT)
            other = _runner(race, 'Other', {'horse last10': '41'}, ONPACE_MILE)
            none = _runner(race, 'None', {'horse last10': '11'}, HEAVY)
            db.session.commit()
            assert appmod.best_bet_routes_for_horse(yes, race)[0] == ['place_2nd_light']
            assert appmod.best_bet_routes_for_horse(other, race)[0] == ['place_won_onpace_mile']
            assert appmod.best_bet_routes_for_horse(none, race)[0] == []
        finally:
            db.session.remove()
            db.drop_all()


def test_daily_run_records_the_place_routes():
    with appmod.app.app_context():
        db.create_all()
        try:
            race = _setup()
            yes = _runner(race, 'Yes', {'horse last10': '3x52', 'form margin': '0.4'}, LIGHT + '\n' + TRACK_PODIUM)
            _runner(race, 'No', {'horse last10': '55'}, LIGHT)
            db.session.commit()
            assert appmod.record_best_bet_routes([race]) == 1
            assert set(yes.prediction.best_bet_routes.split(',')) == {'place_2nd_light', 'place_close_trackpod'}
        finally:
            db.session.remove()
            db.drop_all()


def test_performance_settles_on_the_place_not_the_win():
    with appmod.app.app_context():
        db.create_all()
        try:
            place_bet.clear_cache()
            race = _setup()
            # 8 starters, so 3 places pay. The rule's horse runs 3rd: a placed bet.
            _runner(race, 'Pick', {'horse last10': '52'}, LIGHT, fin=3)
            for i in range(7):
                _runner(race, f'O{i}', {'horse last10': '66'}, HEAVY, fin=[1, 2, 4, 5, 6, 7, 8][i])
            db.session.commit()
            rec = place_bet.performance_all(db.session)
            by_key = {r['key']: r for r in rec['rules']}
            history = by_key['place_2nd_light']['history']
            assert history['bets'] == 1 and history['placed'] == 1
            # No Betfair place price stored yet: a place % but no profit line, nothing charted.
            assert history['priced_bets'] == 0 and history['roi'] is None
            assert by_key['place_2nd_light']['chart']['history'] == []
            assert all(by_key[k]['history']['bets'] == 0 for k in by_key if k != 'place_2nd_light')
        finally:
            place_bet.clear_cache()
            db.session.remove()
            db.drop_all()


def test_places_paid_follow_betfair_rules():
    assert place_bet.places_paid(12) == 3
    assert place_bet.places_paid(8) == 3
    assert place_bet.places_paid(7) == 2
    assert place_bet.places_paid(5) == 2
    assert place_bet.places_paid(4) == 1


def test_win_track_record_leaves_place_bets_out():
    import betfair_check
    assert "NOT LIKE 'place%'" in betfair_check._BEST_BET_BASE


def test_scratchings_refresh_keeps_the_latest_past_start():
    old = {'form meeting date': '07/11/25', 'form position': '2'}
    new = {'form meeting date': '08/10/26', 'form position': '4'}
    assert appmod._is_later_form_row(new, old)
    assert not appmod._is_later_form_row(old, new)
    assert appmod._is_later_form_row(old, None)


def test_chart_counts_losses_now_and_estimates_places_until_betfair_arrives():
    base = {'place_bsp': None, 'placed': False, 'ladbrokes_place': None}
    # A loss is -$10 whatever the price.
    assert place_bet._chart_profit(dict(base)) == (-10.0, False)
    # A place with a Betfair price uses it, after commission.
    profit, est = place_bet._chart_profit(dict(base, placed=True, place_bsp=1.5))
    assert round(profit, 2) == 4.60 and est is False
    # No Betfair price yet: Ladbrokes stands in, marked as an estimate.
    assert place_bet._chart_profit(dict(base, placed=True, ladbrokes_place=1.8)) == (8.0, True)
    # No price at all: left off the chart for now.
    assert place_bet._chart_profit(dict(base, placed=True)) is None
