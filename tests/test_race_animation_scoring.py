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


# ── Adjustable weighting ──────────────────────────────────────────────────
# The page's sliders send a custom split through resolve_weights(); these lock
# down that the defaults still stand when nothing is asked for, that any split
# is rescaled to 100%, and that reweighting genuinely reorders the field.

def test_no_override_keeps_the_published_blend():
    assert scoring.resolve_weights(None) == scoring.WEIGHTS
    assert scoring.resolve_weights({}) == scoring.WEIGHTS


def test_partial_override_keeps_defaults_for_the_rest():
    weights = scoring.resolve_weights({'speed_map': 0})
    assert weights['speed_map'] == 0.0
    # 10/10/30 of the original, rescaled to sum to 1.
    assert round(weights['sectional'], 6) == 0.2
    assert round(weights['assessment'], 6) == 0.6
    assert round(sum(weights.values()), 9) == 1.0


def test_any_split_is_rescaled_to_one():
    weights = scoring.resolve_weights({
        'speed_map': 60, 'sectional': 20, 'adjusted_time': 20, 'assessment': 20,
    })
    assert round(sum(weights.values()), 9) == 1.0
    assert round(weights['speed_map'], 6) == 0.5


def test_percentages_and_fractions_land_on_the_same_blend():
    as_pct = scoring.resolve_weights({'speed_map': 60, 'sectional': 20, 'adjusted_time': 10, 'assessment': 10})
    as_fraction = scoring.resolve_weights({'speed_map': 0.6, 'sectional': 0.2, 'adjusted_time': 0.1, 'assessment': 0.1})
    assert as_pct == as_fraction


def test_negative_and_all_zero_requests_are_survivable():
    assert scoring.resolve_weights({'speed_map': -5})['speed_map'] == 0.0
    all_zero = {'speed_map': 0, 'sectional': 0, 'adjusted_time': 0, 'assessment': 0}
    assert scoring.resolve_weights(all_zero) == scoring.WEIGHTS


def test_reweighting_reorders_the_field():
    """The same two runners flip when the assessment carries the whole blend.

    A wins on the default 50/30 split because of its speed map; on an
    assessment-only weighting B has to come out in front. That flip is exactly
    what the sliders on the page are for, and the animation runs to it.
    """
    def field():
        return [
            {'horse_name': 'A', 'map_value': 95, 'sectional_rank': 4, 'adjusted_time': 33.4, 'assessment_score': 80},
            {'horse_name': 'B', 'map_value': 70, 'sectional_rank': 4, 'adjusted_time': 33.4, 'assessment_score': 92},
        ]

    default_order = scoring.build_composite_scores(field())
    assert [r['horse_name'] for r in default_order] == ['A', 'B']

    assessment_only = scoring.resolve_weights(
        {'speed_map': 0, 'sectional': 0, 'adjusted_time': 0, 'assessment': 100})
    reweighted = scoring.build_composite_scores(field(), assessment_only)
    assert [r['horse_name'] for r in reweighted] == ['B', 'A']
    assert reweighted[0]['components']['assessment']['weight_pct'] == 100


def test_normalised_values_do_not_move_with_the_weights():
    """Reweighting must only change the blend, never the inputs to it.

    This is what lets the page re-score in the browser without another API call.
    """
    def field():
        return [
            {'horse_name': 'A', 'map_value': 95, 'sectional_rank': 2, 'adjusted_time': 33.4, 'assessment_score': 80},
            {'horse_name': 'B', 'map_value': 70, 'sectional_rank': 6, 'adjusted_time': 34.1, 'assessment_score': 92},
            {'horse_name': 'C', 'map_value': 80, 'sectional_rank': 4, 'adjusted_time': 33.9, 'assessment_score': 60},
        ]

    def normalised_by_horse(ordered):
        return {
            r['horse_name']: {k: c['normalised'] for k, c in r['components'].items()}
            for r in ordered
        }

    default_values = normalised_by_horse(scoring.build_composite_scores(field()))
    custom = scoring.resolve_weights({'speed_map': 10, 'sectional': 40, 'adjusted_time': 30, 'assessment': 20})
    custom_values = normalised_by_horse(scoring.build_composite_scores(field(), custom))
    assert default_values == custom_values


def test_weighted_contributions_add_up_to_the_composite():
    runners = [
        {'horse_name': 'A', 'map_value': 95, 'sectional_rank': 2, 'adjusted_time': 33.4, 'assessment_score': 80},
        {'horse_name': 'B', 'map_value': 70, 'sectional_rank': 6, 'adjusted_time': 34.1, 'assessment_score': 92},
    ]
    weights = scoring.resolve_weights({'speed_map': 25, 'sectional': 25, 'adjusted_time': 25, 'assessment': 25})
    for runner in scoring.build_composite_scores(runners, weights):
        total = sum(c['weighted'] for c in runner['components'].values())
        assert abs(total - runner['composite_score']) < 0.05


def test_weights_as_percentages_reads_back_the_split():
    weights = scoring.resolve_weights({'speed_map': 60, 'sectional': 20, 'adjusted_time': 10, 'assessment': 10})
    assert scoring.weights_as_percentages(weights) == {
        'speed_map': 60.0, 'sectional': 20.0, 'adjusted_time': 10.0, 'assessment': 10.0,
    }


def test_partial_percentage_request_is_not_swamped_by_fraction_defaults():
    """?w_speed_map=60 on its own must not read as 60 against 0.1/0.1/0.3.

    The defaults filling the gaps are put on the same scale as what was asked
    for, so a lone percentage lands on 60/10/10/30 rescaled — not on a blend
    that is 99% speed map.
    """
    as_pct = scoring.resolve_weights({'speed_map': 60})
    as_fraction = scoring.resolve_weights({'speed_map': 0.6})
    # Rounded: the two routes differ only in float noise from a x100 rescale.
    assert ({k: round(v, 9) for k, v in as_pct.items()}
            == {k: round(v, 9) for k, v in as_fraction.items()})
    assert round(as_pct['speed_map'], 4) == round(60 / 110, 4)
