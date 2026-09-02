"""The newer half of the Race Animations scoring: scaling, pace, price, shape.

The original tests cover the published blend and the sliders. These cover what
was added afterwards — the things that turn an ordering into a prediction you
can check and bet into — and the specific failures each one was written to fix.
"""

import pytest

import race_animation_scoring as scoring


# ── Scaling: one odd horse must not distort the field ─────────────────────
def test_rank_scaling_is_not_moved_by_an_outlier():
    """The reason 'rank' is the default.

    Four runners are evenly spread and a fifth posts an absurd figure. Under the
    old best-to-worst stretch that one number squashes the other four into the
    bottom of the range and their real differences vanish. Scoring by where each
    runner sits in the field cannot be distorted that way: the outlier moves only
    itself.
    """
    ordinary = [10.0, 20.0, 30.0, 40.0]
    with_outlier = ordinary + [4000.0]

    stretched = scoring.normalise_component(with_outlier, False, 'minmax')
    ranked = scoring.normalise_component(with_outlier, False, 'rank')

    # Under min-max the four ordinary runners end up within a couple of points
    # of each other — a real 4x difference in the raw input, rendered invisible.
    assert max(stretched[:4]) - min(stretched[:4]) < 1.0
    # Under rank scaling they keep the full spread of the band.
    assert max(ranked[:4]) - min(ranked[:4]) > 60.0
    # And both still agree on who is best.
    assert stretched.index(max(stretched)) == 4
    assert ranked.index(max(ranked)) == 4


def test_rank_scaling_shares_ties_and_respects_direction():
    values = [3.0, 1.0, 1.0, 5.0]
    # Lower is better here, so the two 1.0s are the joint best.
    scaled = scoring.normalise_component(values, True, 'rank')
    assert scaled[1] == scaled[2]
    assert scaled[1] > scaled[0] > scaled[3]
    assert scaled[3] == pytest.approx(scoring.NORM_FLOOR)


def test_a_field_that_all_posts_the_same_number_goes_inert():
    """No ordering information means no opinion, not an invented one."""
    scaled = scoring.normalise_component([7.0, 7.0, 7.0], False, 'rank')
    assert scaled == [scoring.NORM_NEUTRAL] * 3


def test_one_lone_data_point_carries_no_ordering():
    scaled = scoring.normalise_component([None, 4.2, None], False, 'rank')
    assert scaled == [None, scoring.NORM_NEUTRAL, None]


# ── Pace: the map has to be able to decide a race ─────────────────────────
def test_a_lone_leader_is_a_soft_lead_and_a_crowd_is_a_speed_duel():
    soft = scoring.race_pace_profile(['leader', 'midfield', 'midfield', 'back'])
    hot = scoring.race_pace_profile(['leader', 'leader', 'leader', 'leader', 'onpace'])

    assert soft['shape'] == 'soft'
    assert hot['shape'] == 'hot'
    assert hot['pressure'] > soft['pressure']
    assert 0.0 <= soft['pressure'] <= 1.0 and 0.0 <= hot['pressure'] <= 1.0


def test_pace_fit_turns_the_tempo_against_the_leaders():
    """The whole point of the pace component.

    In a speed duel the leaders take each other on and the backmarkers profit;
    with one horse alone in front the reverse. If this ever stops holding, the
    speed map is back to decorating the middle of the race instead of deciding
    the end of it.
    """
    hot = scoring.race_pace_profile(['leader'] * 4 + ['back'] * 4)
    soft = scoring.race_pace_profile(['leader'] + ['midfield'] * 6)

    assert scoring.pace_fit_score('leader', hot) < 0
    assert scoring.pace_fit_score('back', hot) > 0
    assert scoring.pace_fit_score('leader', soft) > 0
    assert scoring.pace_fit_score('back', soft) < 0


def test_the_meetings_own_pace_bias_is_carried_through():
    """A track where the leaders have won all day should say so."""
    neutral = scoring.race_pace_profile(['leader', 'midfield', 'back'], pace_bias=0)
    leaders_day = scoring.race_pace_profile(['leader', 'midfield', 'back'], pace_bias=2)
    assert (scoring.pace_fit_score('leader', leaders_day)
            > scoring.pace_fit_score('leader', neutral))
    assert (scoring.pace_fit_score('back', leaders_day)
            < scoring.pace_fit_score('back', neutral))


def test_pace_changes_the_result_once_it_carries_weight():
    """Pace has to reach the finishing order, not just the picture.

    Two runners identical on every other input, one a leader and one a
    backmarker, in a race with four horses fighting for the front. With the pace
    component switched on the backmarker has to come out in front.
    """
    hot = scoring.race_pace_profile(['leader'] * 4 + ['back'])

    def field():
        return [
            {'horse_name': 'Front Runner', 'map_value': 80, 'assessment_score': 70,
             'pace_fit_value': scoring.pace_fit_score('leader', hot)},
            {'horse_name': 'Backmarker', 'map_value': 80, 'assessment_score': 70,
             'pace_fit_value': scoring.pace_fit_score('back', hot)},
        ]

    without = scoring.build_composite_scores(field())
    assert without[0]['composite_score'] == without[1]['composite_score']

    weights = scoring.resolve_weights({'speed_map': 40, 'assessment': 30, 'pace_fit': 30})
    with_pace = scoring.build_composite_scores(field(), weights)
    assert [r['horse_name'] for r in with_pace] == ['Backmarker', 'Front Runner']


# ── Draw and the rail ─────────────────────────────────────────────────────
def test_an_inside_gate_is_worth_having_until_the_rail_goes_out():
    true_rail_inside = scoring.draw_score(1, 12, rail_position=0)
    true_rail_wide = scoring.draw_score(12, 12, rail_position=0)
    assert true_rail_inside > true_rail_wide

    # Move the rail a long way out and the inside advantage is given away.
    wide_rail_inside = scoring.draw_score(1, 12, rail_position=10)
    wide_rail_wide = scoring.draw_score(12, 12, rail_position=10)
    assert wide_rail_inside < true_rail_inside
    assert wide_rail_inside < wide_rail_wide


def test_a_missing_barrier_scores_nothing_rather_than_zero():
    assert scoring.draw_score(None, 10) is None
    assert scoring.draw_score(0, 10) is None


# ── Jockey and trainer ────────────────────────────────────────────────────
def test_jockey_and_trainer_blend_and_survive_a_missing_half():
    both = scoring.jockey_trainer_score(1.5, 1.0)
    assert both == pytest.approx(1.5 * 0.6 + 1.0 * 0.4)
    assert scoring.jockey_trainer_score(1.2, None) == 1.2
    assert scoring.jockey_trainer_score(None, 0.8) == 0.8
    assert scoring.jockey_trainer_score(None, None) is None
    # A broken feed row is not information.
    assert scoring.jockey_trainer_score(-3, None) is None
    assert scoring.jockey_trainer_score(99, None) is None


# ── Probability, price and value ──────────────────────────────────────────
def test_win_probabilities_form_a_book_and_follow_the_scores():
    runners = [{'composite_score': score} for score in (80, 70, 60, 50, 40)]
    probabilities = scoring.win_probabilities(runners)
    assert sum(probabilities) == pytest.approx(1.0)
    assert probabilities == sorted(probabilities, reverse=True)
    assert all(p > 0 for p in probabilities)


def test_a_flatter_temperature_makes_a_flatter_market():
    runners = [{'composite_score': score} for score in (80, 50)]
    opinionated = scoring.win_probabilities(runners, temperature=4)
    cautious = scoring.win_probabilities(runners, temperature=40)
    # A low temperature makes short favourites; a high one flattens the field
    # towards every runner having an equal chance. Neither may reorder it.
    assert opinionated[0] > cautious[0] > 0.5
    assert (opinionated[0] - opinionated[1]) > (cautious[0] - cautious[1])


def test_value_edge_only_calls_a_bet_when_the_price_is_wrong():
    # We say 40%; the market says 25%. That is a bet.
    good = scoring.value_edge(0.40, 4.00)
    assert good['is_value'] is True
    assert good['expected_value'] > 0
    assert good['kelly_pct'] > 0

    # We say 20%; the market says 50%. That is not.
    bad = scoring.value_edge(0.20, 2.00)
    assert bad['is_value'] is False
    assert bad['expected_value'] < 0
    assert bad['kelly_pct'] == 0

    # No price, no opinion.
    assert scoring.value_edge(0.4, None)['is_value'] is False


def test_kelly_is_shown_at_a_fraction_of_full():
    full = scoring.value_edge(0.40, 4.00, kelly_fraction=1.0)['kelly_pct']
    quarter = scoring.value_edge(0.40, 4.00, kelly_fraction=0.25)['kelly_pct']
    assert quarter == pytest.approx(full * 0.25, abs=0.02)


# ── Honest uncertainty ────────────────────────────────────────────────────
def test_the_simulation_agrees_with_the_probabilities_it_came_from():
    """Simulated win rates must land on the probabilities, not drift off them.

    The whole value of "wins 34% of the time" is that it is the same 34% the
    fair price was built from. Plackett-Luce sampling gives that; anything
    looser would quietly show one number and price off another.
    """
    runners = [{'horse_id': i, 'composite_score': score}
               for i, score in enumerate((85, 70, 62, 55, 40))]
    probabilities = scoring.win_probabilities(runners)
    outcome = scoring.simulate_race(runners, runs=4000)

    assert outcome['runs'] == 4000
    assert len(outcome['summary']) == len(runners)
    for row, probability in zip(outcome['summary'], probabilities):
        assert row['win_pct'] / 100.0 == pytest.approx(probability, abs=0.03)
    assert sum(row['win_pct'] for row in outcome['summary']) == pytest.approx(100.0, abs=0.5)
    # Top three shares add to three placings' worth.
    assert sum(row['top3_pct'] for row in outcome['summary']) == pytest.approx(300.0, abs=0.5)


def test_the_simulation_is_stable_across_runs():
    """A figure that moved on every refresh could not be compared to anything."""
    runners = [{'horse_id': i, 'composite_score': s} for i, s in enumerate((80, 70, 60))]
    first = scoring.simulate_race(runners, runs=500)
    second = scoring.simulate_race(runners, runs=500)
    assert first == second


def test_a_sampled_race_is_a_permutation_of_the_field():
    runners = [{'horse_id': i, 'composite_score': s} for i, s in enumerate((80, 70, 60, 50))]
    order = scoring.sample_finish_order(runners, seed=7)
    assert sorted(order) == list(range(len(runners)))
    assert scoring.sample_finish_order(runners, seed=7) == order      # deterministic


# ── Rounding parity ───────────────────────────────────────────────────────
def test_rounding_matches_the_browser_not_bankers():
    """Python's round() breaks ties to even; every JavaScript engine rounds up."""
    assert scoring.round_half_up(2.5) == 3
    assert scoring.round_half_up(3.5) == 4          # round() would give 4 as well
    assert scoring.round_half_up(0.125, 2) == 0.13  # round() gives 0.12
    assert scoring.round_half_up(None) is None


def test_the_weighted_contributions_reproduce_the_composite_exactly():
    """The payload has to be reproducible from the numbers it publishes.

    The page re-blends from the rounded component values it was sent, so the
    server has to blend from those same rounded values or the two disagree.
    """
    runners = [
        {'horse_name': 'A', 'map_value': 91.37, 'sectional_rank': 2.33,
         'adjusted_time': 33.417, 'assessment_score': 80.09},
        {'horse_name': 'B', 'map_value': 70.11, 'sectional_rank': 6.67,
         'adjusted_time': 34.183, 'assessment_score': 92.41},
        {'horse_name': 'C', 'map_value': 83.29, 'sectional_rank': 4.01,
         'adjusted_time': 33.902, 'assessment_score': 61.55},
    ]
    weights = scoring.resolve_weights({'speed_map': 37, 'sectional': 21,
                                       'adjusted_time': 19, 'assessment': 23})
    for runner in scoring.build_composite_scores(runners, weights, 'minmax'):
        rebuilt = sum(c['normalised'] * c['weight'] for c in runner['components'].values())
        assert abs(rebuilt - runner['composite_score']) < 0.005


# ── Track shape ───────────────────────────────────────────────────────────
def test_new_south_wales_and_queensland_run_clockwise():
    assert scoring.track_direction('Randwick') == 'clockwise'
    assert scoring.track_direction('Eagle Farm') == 'clockwise'
    assert scoring.track_direction('260902_Rosehill') == 'clockwise'
    assert scoring.track_direction('Flemington') == 'anticlockwise'
    assert scoring.track_direction('Morphettville') == 'anticlockwise'
    # An unknown track guesses the more common direction rather than failing.
    assert scoring.track_direction('Somewhere Nobody Has Heard Of') == 'anticlockwise'
    assert scoring.track_direction(None) == 'anticlockwise'


def test_distance_parsing_rejects_nonsense():
    assert scoring.parse_distance_metres('1200m') == 1200
    assert scoring.parse_distance_metres(2040) == 2040
    assert scoring.parse_distance_metres('') is None
    assert scoring.parse_distance_metres('12') is None       # too short to be a race
    assert scoring.parse_distance_metres(99999) is None


def test_a_sprint_and_a_staying_race_are_drawn_differently():
    """The fault this fixes: every race used to be the same picture."""
    sprint = scoring.race_shape(1000)
    staying = scoring.race_shape(3200)

    assert sprint['lap_fraction'] < staying['lap_fraction']
    assert sprint['duration_seconds'] < staying['duration_seconds']
    # A trip longer than the circuit is drawn as its final lap and says so.
    assert staying['lap_fraction'] == 1.0
    assert staying['laps'] > 1
    # And nothing runs so long that a viewer gives up on it.
    assert staying['duration_seconds'] <= scoring.MAX_DURATION_SECONDS


def test_an_unknown_distance_falls_back_to_the_old_three_quarter_lap():
    shape = scoring.race_shape(None)
    assert shape['lap_fraction'] == 0.75
    assert shape['distance_m'] is None
