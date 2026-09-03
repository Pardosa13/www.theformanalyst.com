"""Solving a race backwards, from the runner that actually won.

race_animation_calibration.py answers a question the tuner cannot: not "which
weighting finds the most winners overall" but "what would the weighting have had
to be for THIS horse to rate top?". These tests hold the three things that make
that answer worth reading:

  * every weighting it hands back really does put the target on top — checked by
    re-scoring the field with it rather than by trusting the search,
  * the answer is the NEAREST such weighting, not the most extreme one, and
  * a runner that no weighting could have found is reported as exactly that,
    instead of being fitted to with a split that does not work.

The fixtures are built by hand so the right answer is known before the solver
sees it. A solver that cannot recover an answer planted where it can see it will
not find a real one either.
"""

import pytest

import race_animation_calibration as calibration
from race_animation_scoring import COMPONENT_KEYS, resolve_weights


# ── Fixtures ──────────────────────────────────────────────────────────────
def runner(**values):
    """One row of normalised component values. Anything unnamed sits at 50."""
    row = [50.0] * len(COMPONENT_KEYS)
    for key, value in values.items():
        row[COMPONENT_KEYS.index(key)] = float(value)
    return row


def weights(**values):
    """A full weighting in percentages, with everything unnamed at zero."""
    return resolve_weights({key: values.get(key, 0) for key in COMPONENT_KEYS})


DEFAULT = resolve_weights(None)


def composites(matrix, blend):
    vector = [blend[key] for key in COMPONENT_KEYS]
    return [sum(row[i] * vector[i] for i in range(len(vector))) for row in matrix]


def tops_the_field(matrix, target, percentages):
    """Re-score the field under a solved weighting and check the target leads.

    Deliberately independent of the solver's own arithmetic: it is the check
    that the answer means something on the page, not that the search agrees with
    itself.
    """
    blend = resolve_weights(percentages)
    scores = composites(matrix, blend)
    return all(scores[target] > scores[j] for j in range(len(scores)) if j != target)


# The classic miss: the winner is beaten on the two inputs carrying almost all
# the weight, and best in the race on one carrying none.
PACE_RACE = [
    runner(speed_map=90, assessment=85, pace_fit=10),
    runner(speed_map=80, assessment=70, pace_fit=25),
    runner(speed_map=40, assessment=45, pace_fit=95),      # index 2 — the winner
    runner(speed_map=30, assessment=30, pace_fit=35),
]


# ── The core answer ───────────────────────────────────────────────────────
def test_solves_a_missed_winner_and_the_answer_actually_works():
    outcome = calibration.solve_for_runner(PACE_RACE, 2, DEFAULT)

    assert outcome['ok'] and outcome['reachable']
    assert not outcome['already_top']
    assert outcome['start_rank'] > 1
    assert tops_the_field(PACE_RACE, 2, outcome['weights'])


def test_the_solved_weighting_sums_to_one_hundred():
    """It goes onto the sliders, so it has to be a split a person can set."""
    outcome = calibration.solve_for_runner(PACE_RACE, 2, DEFAULT)
    assert abs(sum(outcome['weights'].values()) - 100.0) < 0.15
    assert all(value >= 0 for value in outcome['weights'].values())


def test_it_finds_the_input_that_was_being_ignored():
    """The planted answer is pace fit, and nothing else should be pulled harder."""
    outcome = calibration.solve_for_runner(PACE_RACE, 2, DEFAULT)
    biggest = max(outcome['shifts'], key=lambda shift: shift['shift'])
    assert biggest['key'] == 'pace_fit'
    assert outcome['best_lever']['key'] == 'pace_fit'


def test_the_single_lever_is_enough_on_its_own():
    """"One slider and nothing else" has to be true, not just readable."""
    outcome = calibration.solve_for_runner(PACE_RACE, 2, DEFAULT)
    lever = outcome['best_lever']

    share = lever['to'] / 100.0
    others = sum(DEFAULT[key] for key in COMPONENT_KEYS if key != lever['key'])
    only = {}
    for key in COMPONENT_KEYS:
        only[key] = (share if key == lever['key']
                     else DEFAULT[key] * (1 - share) / others)

    assert tops_the_field(PACE_RACE, 2, {k: v * 100 for k, v in only.items()})


def test_the_answer_is_the_nearest_one_not_the_most_extreme():
    """A solver that just maximises the margin would hand back a corner.

    The point of the panel is "here is the least you had wrong", so the answer
    has to stay recognisably near the weighting it started from — and it has to
    beat what the maximum-margin split would have moved.
    """
    outcome = calibration.solve_for_runner(PACE_RACE, 2, DEFAULT)
    assert outcome['moved_points'] < 50
    # The published blend still shows through: MAP is the biggest single weight.
    assert outcome['weights']['speed_map'] >= outcome['weights']['assessment']


def test_solo_ranks_say_which_input_had_the_race_right():
    outcome = calibration.solve_for_runner(PACE_RACE, 2, DEFAULT)
    assert outcome['solo_ranks']['pace_fit'] == 1
    assert outcome['solo_ranks']['speed_map'] == 3
    assert outcome['solo_ties']['pace_fit'] == 0


def test_a_shared_first_is_reported_as_shared():
    """An input nobody in the race has data for is imputed to the field average
    for everybody, which makes the whole field equal first on it. Calling that a
    lead would point somebody at an empty column."""
    outcome = calibration.solve_for_runner(PACE_RACE, 2, DEFAULT)
    # `market` is untouched by the fixture, so every runner sits on 50.
    assert outcome['solo_ranks']['market'] == 1
    assert outcome['solo_ties']['market'] == len(PACE_RACE) - 1


def test_beaten_by_names_the_runners_that_were_in_front():
    outcome = calibration.solve_for_runner(
        PACE_RACE, 2, DEFAULT, labels=['Ceolwulf', 'Second', 'The Winner', 'Fourth'])
    names = [item['name'] for item in outcome['beaten_by']]
    assert names[0] == 'Ceolwulf'
    assert all(item['gap'] > 0 for item in outcome['beaten_by'])


# ── When the weighting already had it ─────────────────────────────────────
def test_a_winner_we_already_had_on_top_is_reported_as_such():
    outcome = calibration.solve_for_runner(PACE_RACE, 0, DEFAULT)
    assert outcome['already_top'] is True
    assert outcome['moved_points'] == 0.0
    assert outcome['headline_shifts'] == []


# ── When nothing would have worked ────────────────────────────────────────
def test_a_runner_worse_on_every_input_is_reported_unreachable():
    """Not "here is a weighting", but "nothing would have found this one".

    This is the honest answer and the useful one: it says the race was not
    missed because of the blend, so there is nothing here to learn from.
    """
    field = [
        runner(speed_map=90, assessment=90, pace_fit=90, sectional=90,
               adjusted_time=90, draw=90, jockey_trainer=90, market=90),
        runner(speed_map=70, assessment=70, pace_fit=70, sectional=70,
               adjusted_time=70, draw=70, jockey_trainer=70, market=70),
        runner(speed_map=10, assessment=10, pace_fit=10, sectional=10,
               adjusted_time=10, draw=10, jockey_trainer=10, market=10),
    ]
    outcome = calibration.solve_for_runner(field, 2, DEFAULT)

    assert outcome['ok'] is True
    assert outcome['reachable'] is False
    assert outcome['weights'] is None
    assert outcome['blocked_by']


def test_unreachable_when_a_mix_of_rivals_beats_it_everywhere():
    """The subtler case: no single rival dominates, but the pair of them do.

    A runner can be second-best on every input and still be unfindable, because
    the weighting has to be the same for all of them at once. A solver that only
    checked for a dominating rival would report this one as reachable and be
    wrong.
    """
    field = [
        runner(speed_map=95, assessment=20),
        runner(speed_map=20, assessment=95),
        runner(speed_map=52, assessment=52),        # never top under any split
    ]
    # Only these two inputs carry weight, so the field is genuinely two-dimensional.
    blend = weights(speed_map=50, assessment=50)
    outcome = calibration.solve_for_runner(field, 2, blend)
    assert outcome['reachable'] is False


# ── Locking components ────────────────────────────────────────────────────
def test_a_locked_component_is_left_exactly_where_it_was():
    outcome = calibration.solve_for_runner(PACE_RACE, 2, DEFAULT,
                                           locked_keys=['speed_map'])
    assert abs(outcome['weights']['speed_map'] - 50.0) < 0.05
    assert outcome['single_levers']['speed_map'] is None
    if outcome['reachable']:
        assert tops_the_field(PACE_RACE, 2, outcome['weights'])


# ── Reading a run of races ────────────────────────────────────────────────
def _prepared(index, matrix, winner, condition, day):
    return {
        'race_id': index,
        'sort_key': f'2026-01-{day:02d}',
        'matrix': matrix,
        'winner_index': winner,
        'finish_positions': [1 if i == winner else i + 2 for i in range(len(matrix))],
        'sps': [4.0] * len(matrix),
        'field_size': len(matrix),
        'context': {'condition': condition},
    }


def test_drift_finds_a_bias_planted_in_one_condition_only():
    """Twenty soft-track races where the tempo decided it, and dry races where
    the blend was already right. The reading has to separate the two."""
    races = []
    for index in range(20):
        races.append(_prepared(index, PACE_RACE, 2, 'soft', day=index + 1))
    for index in range(20, 32):
        races.append(_prepared(index, PACE_RACE, 0, 'good', day=index + 1))

    report = calibration.calibration_drift(races, DEFAULT, group_by='condition')

    assert report['ok']
    assert report['already_found'] == 12       # the dry races were already right
    assert report['solved'] == 20
    assert report['drift']['biggest_pull']['key'] == 'pace_fit'

    soft = next(group for group in report['groups'] if group['group'] == 'soft')
    assert soft['median_shift']['pace_fit'] > 5
    assert not any(group['group'] == 'good' for group in report['groups'])


def test_drift_reports_races_no_weighting_could_have_found():
    hopeless = [
        runner(speed_map=90, assessment=90, pace_fit=90, sectional=90,
               adjusted_time=90, draw=90, jockey_trainer=90, market=90),
        runner(speed_map=70, assessment=70, pace_fit=70, sectional=70,
               adjusted_time=70, draw=70, jockey_trainer=70, market=70),
        runner(speed_map=10, assessment=10, pace_fit=10, sectional=10,
               adjusted_time=10, draw=10, jockey_trainer=10, market=10),
    ]
    races = [_prepared(index, hopeless, 2, 'good', day=index + 1) for index in range(6)]
    races += [_prepared(index + 10, PACE_RACE, 2, 'good', day=index + 10)
              for index in range(6)]

    report = calibration.calibration_drift(races, DEFAULT)
    assert report['unreachable'] == 6
    assert report['solved'] == 6


def test_drift_needs_a_run_of_races_before_it_will_group_anything():
    """Two races cannot show a bias, and a page that prints one as if they could
    is a page that has started lying to whoever is reading it."""
    races = [_prepared(index, PACE_RACE, 2, 'heavy', day=index + 1) for index in range(2)]
    report = calibration.calibration_drift(races, DEFAULT)
    heavy = next(group for group in report['groups'] if group['group'] == 'heavy')
    assert heavy['enough'] is False
    assert 'median_shift' not in heavy


def test_drift_holds_races_back_to_test_on():
    """The medians are fitted to results already known. The holdout is the only
    figure on the panel that is allowed to be believed, so it has to exist."""
    races = [_prepared(index, PACE_RACE, 2, 'soft', day=index + 1) for index in range(24)]
    report = calibration.calibration_drift(races, DEFAULT)

    holdout = report['holdout']
    assert holdout['ok'] is True
    assert holdout['train_races'] + 1 <= report['solved']
    assert holdout['test_races'] >= 5
    # Every miss in this fixture wanted the same thing, so following it should
    # win — that is what makes the negative case in the next test meaningful.
    assert holdout['beats_baseline'] is True


def test_drift_does_not_call_a_wobble_a_finding():
    """Pure noise, and the holdout has to say so.

    A strike rate over eighty races moves a few points on nothing at all, so a
    weighting fitted to random results will land on the right side of the
    baseline about half the time. Reporting that as a win is how a page ends up
    with somebody betting on a coin toss, so the gap has to clear the wobble
    before it counts.
    """
    import random

    rng = random.Random(7)
    races = []
    for index in range(200):
        size = rng.randint(8, 14)
        matrix = [[rng.uniform(5, 95) for _ in COMPONENT_KEYS] for _ in range(size)]
        winner = rng.randrange(size)
        races.append({
            'race_id': index,
            'sort_key': '2026-%02d-%02d' % (1 + index // 28, 1 + index % 28),
            'matrix': matrix,
            'winner_index': winner,
            'finish_positions': [1 if i == winner else i + 2 for i in range(size)],
            'sps': [rng.uniform(2, 30) for _ in range(size)],
            'context': {'condition': 'good'},
        })

    holdout = calibration.calibration_drift(races, DEFAULT)['holdout']

    assert holdout['ok'] is True
    assert holdout['beats_baseline'] is False
    assert holdout['noise_band'] > 0


def test_drift_says_so_when_there_is_not_enough_to_test_on():
    races = [_prepared(index, PACE_RACE, 2, 'soft', day=index + 1) for index in range(4)]
    report = calibration.calibration_drift(races, DEFAULT)
    assert report['holdout']['ok'] is False
    assert report['holdout']['reason']


# ── Track condition buckets ───────────────────────────────────────────────
@pytest.mark.parametrize('text,expected', [
    ('Soft 6', 'soft'),
    ('Heavy 10', 'heavy'),
    ('Good 4', 'good'),
    ('Firm 2', 'firm'),
    ('Dead 5', 'soft'),
    ('Synthetic', 'synthetic'),
    ('', 'unknown'),
    (None, 'unknown'),
])
def test_condition_groups(text, expected):
    assert calibration.condition_group(text) == expected


# ── Degenerate input ──────────────────────────────────────────────────────
def test_an_empty_field_is_refused_rather_than_guessed_at():
    assert calibration.solve_for_runner([], 0, DEFAULT)['ok'] is False
    assert calibration.solve_for_runner([runner()], 0, DEFAULT)['ok'] is False
    assert calibration.solve_for_runner(PACE_RACE, 9, DEFAULT)['ok'] is False
