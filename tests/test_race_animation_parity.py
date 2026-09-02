"""The browser's copy of the blend must agree with the server's.

The Race Animations page re-blends the composite locally so the weight sliders
respond without a round trip. That is a second implementation of the same
arithmetic, and a second implementation is a promise you have to keep: if
race_animation_scoring.py changes and static/js/race-animation-scoring.js does
not, the page quietly shows a different winner from the one the API would give.

This runs both over the same fixture — several weightings, both scaling methods
— and fails on any disagreement beyond rounding. It is the reason the JavaScript
no longer carries its own hardcoded copies of the weights and margin rules: it
reads them out of scoring_constants(), and this proves it uses them the same way.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import race_animation_scoring as scoring

ROOT = Path(__file__).resolve().parents[1]
JS_MODULE = ROOT / 'static' / 'js' / 'race-animation-scoring.js'

# Tolerances. Both sides round to two decimals at the same points, so anything
# beyond a rounding step apart is a real divergence, not float noise.
SCORE_TOLERANCE = 0.011
PROBABILITY_TOLERANCE = 1e-4


def _node():
    path = shutil.which('node')
    if not path:
        pytest.skip('node is not available to run the browser-side module')
    return path


def _field():
    """A deliberately awkward field: gaps, ties, and a runner with no price."""
    return [
        {'horse_id': 1, 'horse_name': 'Ardent Lane', 'barrier': 3,
         'map_value': 95.0, 'sectional_rank': 2.0, 'adjusted_time': 33.40,
         'assessment_score': 80.0, 'jockey_trainer_ae': 1.24, 'draw_value': 0.86,
         'pace_fit_value': -0.4, 'market_probability': 0.28, 'price': 3.40},
        {'horse_id': 2, 'horse_name': 'Bold Reckoning', 'barrier': 11,
         'map_value': 70.0, 'sectional_rank': 6.0, 'adjusted_time': 34.10,
         'assessment_score': 92.0, 'jockey_trainer_ae': 0.91, 'draw_value': 0.24,
         'pace_fit_value': 0.9, 'market_probability': 0.21, 'price': 4.60},
        {'horse_id': 3, 'horse_name': 'Cold Harbour', 'barrier': 1,
         'map_value': 80.0, 'sectional_rank': 4.0, 'adjusted_time': 33.90,
         'assessment_score': 60.0, 'jockey_trainer_ae': None, 'draw_value': 1.0,
         'pace_fit_value': -0.4, 'market_probability': 0.14, 'price': 7.00},
        {'horse_id': 4, 'horse_name': 'Drift Wood', 'barrier': 7,
         'map_value': 80.0, 'sectional_rank': None, 'adjusted_time': 34.10,
         'assessment_score': 60.0, 'jockey_trainer_ae': 1.05, 'draw_value': 0.52,
         'pace_fit_value': 0.35, 'market_probability': None, 'price': None},
        {'horse_id': 5, 'horse_name': 'Even Money', 'barrier': 5,
         'map_value': 62.0, 'sectional_rank': 9.0, 'adjusted_time': 35.00,
         'assessment_score': 44.0, 'jockey_trainer_ae': 0.62, 'draw_value': 0.69,
         'pace_fit_value': 0.9, 'market_probability': 0.37, 'price': 2.60},
    ]


WEIGHTINGS = [
    # The published default.
    None,
    # The four core inputs, evenly.
    {'speed_map': 25, 'sectional': 25, 'adjusted_time': 25, 'assessment': 25},
    # A split that turns the newer inputs on.
    {'speed_map': 30, 'sectional': 5, 'adjusted_time': 5, 'assessment': 20,
     'jockey_trainer': 15, 'draw': 10, 'pace_fit': 10, 'market': 5},
    # One input carrying everything.
    {'assessment': 100},
    # A request that does not add up to 100 — both sides must rescale it.
    {'speed_map': 60, 'sectional': 20, 'adjusted_time': 20, 'assessment': 20},
]


def _server_side(weight_request, method):
    """What the API would send for this weighting."""
    weights = scoring.resolve_weights(weight_request)
    runners = _field()
    ordered = scoring.build_composite_scores(runners, weights, method)
    for runner, margin in zip(ordered, scoring.finish_margins(ordered)):
        runner['beaten_margin'] = round(margin, 2)
    scoring.attach_market(ordered, [r['market_probability'] for r in ordered])
    return ordered


def _browser_side(payload_runners, weight_percentages, node):
    """What the page computes locally from the payload it was handed."""
    script = f"""
        const scoring = require({json.dumps(str(JS_MODULE))});
        const input = JSON.parse(process.argv[1]);
        const outcome = scoring.rescore(input.runners, input.weights, input.config);
        process.stdout.write(JSON.stringify(outcome.runners));
    """
    arguments = json.dumps({
        'runners': payload_runners,
        'weights': weight_percentages,
        'config': scoring.scoring_constants(),
    })
    completed = subprocess.run(
        [node, '-e', script, '--', arguments],
        capture_output=True, text=True, check=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize('weight_request', WEIGHTINGS)
@pytest.mark.parametrize('method', scoring.NORM_METHODS)
def test_browser_and_server_agree_on_the_blend(weight_request, method):
    """Same fixture, same weighting: same winner, same scores, same prices."""
    node = _node()

    # The payload the page actually receives always arrives on the DEFAULT
    # blend; the sliders then re-score it in the browser. Reproduce that
    # exactly, rather than handing the browser a payload already built on the
    # weighting under test — the point is that the re-blend gets there on its own.
    payload = _server_side(None, method)

    percentages = (scoring.weights_as_percentages(scoring.WEIGHTS)
                   if weight_request is None else weight_request)
    # A partial request means "defaults for the rest" on the server; the page's
    # sliders always send a full set, so fill it out the same way here.
    full_request = dict(scoring.weights_as_percentages(scoring.resolve_weights(weight_request)))

    browser = _browser_side(payload, full_request if weight_request else percentages, node)
    server = _server_side(full_request, method)

    assert [r['horse_id'] for r in browser] == [r['horse_id'] for r in server], (
        'the two implementations put the field in a different order'
    )

    for mine, theirs in zip(browser, server):
        assert mine['rank'] == theirs['rank']
        assert abs(mine['composite_score'] - theirs['composite_score']) < SCORE_TOLERANCE
        assert abs(mine['beaten_margin'] - theirs['beaten_margin']) < SCORE_TOLERANCE
        assert abs(mine['win_probability'] - theirs['win_probability']) < PROBABILITY_TOLERANCE

        if theirs['fair_odds'] is None:
            assert mine['fair_odds'] is None
        else:
            assert abs(mine['fair_odds'] - theirs['fair_odds']) < 0.02

        mine_value, their_value = mine['value'], theirs['value']
        assert mine_value['is_value'] == their_value['is_value']
        if their_value['edge_pct'] is None:
            assert mine_value['edge_pct'] is None
        else:
            assert abs(mine_value['edge_pct'] - their_value['edge_pct']) < 0.02
            assert abs(mine_value['kelly_pct'] - their_value['kelly_pct']) < 0.02


def test_the_browser_reads_its_constants_from_python():
    """No hardcoded weights or margin rules left in the browser module.

    The whole reason the two used to drift is that the page carried its own
    copies. If one reappears, this fails — and it is much easier to fix here
    than to notice on a race day.
    """
    source = JS_MODULE.read_text()
    for forbidden in ('0.50', '50, ', 'margin_min =', 'MARGIN_MIN', 'DEFAULT_WEIGHTS ='):
        assert forbidden not in source, (
            f'{forbidden!r} looks like a hardcoded copy of a server constant'
        )
    # And it must actually reach for the injected config.
    for expected in ('config.margin_min', 'config.margin_total',
                     'config.probability_temperature', 'config.kelly_fraction',
                     'config.default_weights', 'component_keys'):
        assert expected in source, f'the browser module never reads {expected}'


def test_every_constant_the_browser_needs_is_published():
    """scoring_constants() has to carry everything the page reads off it."""
    constants = scoring.scoring_constants()
    for key in ('component_keys', 'core_component_keys', 'labels', 'short_labels',
                'decimals', 'default_weights', 'norm_methods', 'default_norm_method',
                'margin_min', 'margin_max', 'margin_total',
                'probability_temperature', 'kelly_fraction', 'min_value_edge'):
        assert key in constants, f'{key} is missing from scoring_constants()'
    # It has to survive the trip to the browser as JSON.
    assert json.loads(json.dumps(constants)) == constants
