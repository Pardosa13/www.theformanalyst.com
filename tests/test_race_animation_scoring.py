"""Weighting and blend guards for the Race Animations composite score.

The animation's finish order is whatever build_composite_scores() ranks, so a
silent drift in the weights would silently change every predicted result on the
page. These lock the published blend down.
"""

import race_animation_scoring as scoring


def test_weights_are_the_published_blend():
    assert scoring.WEIGHTS == {
        'speed_map': 0.50,
        'sectional': 0.10,
        'adjusted_time': 0.10,
        'assessment': 0.30,
    }


def test_weights_sum_to_one():
    assert round(sum(scoring.WEIGHTS.values()), 9) == 1.0


def test_speed_map_outweighs_the_assessment():
    """The half-weighted speed map beats a better race assessment on its own.

    A is top of the field on MAP and second on assessment; B is the reverse.
    Under the 50/30 split A has to come out in front — that is the whole point
    of the reweighting, and it is what the animation runs to.
    """
    runners = [
        {'horse_name': 'A', 'map_value': 95, 'sectional_rank': 4, 'adjusted_time': 33.4, 'assessment_score': 80},
        {'horse_name': 'B', 'map_value': 70, 'sectional_rank': 4, 'adjusted_time': 33.4, 'assessment_score': 92},
    ]
    ordered = scoring.build_composite_scores(runners)
    assert [r['horse_name'] for r in ordered] == ['A', 'B']
    assert ordered[0]['components']['speed_map']['weight_pct'] == 50
    assert ordered[0]['components']['assessment']['weight_pct'] == 30
