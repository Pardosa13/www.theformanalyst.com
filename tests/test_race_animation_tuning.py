"""Measuring a weighting, and letting the history choose one.

The composite's 50/10/10/30 split was picked rather than measured, and nothing
in the codebase showed it beat an even split. race_animation_tuning.py is the
answer to that, so these tests check the two things that make its answers worth
having:

  * a weighting is scored against real finishing positions, and
  * the search never reports an in-sample figure as if it meant something.

The fixtures are synthetic on purpose. A search that cannot recover a signal
planted where it can see it will not find a real one either, and a search that
"finds" a signal in pure noise is the failure mode that makes tuning dangerous —
both are checked below.
"""

import math
import random

import pytest

import race_animation_scoring as scoring
import race_animation_tuning as tuning


# ── Fixtures ──────────────────────────────────────────────────────────────
def _only(key):
    """A weighting that is entirely one input.

    resolve_weights() deliberately fills anything omitted from the published
    defaults — right for a partial query string, wrong for "score me a pure
    speed-map blend" — so every key is named explicitly.
    """
    return scoring.resolve_weights(
        {k: (100 if k == key else 0) for k in scoring.COMPONENT_KEYS})


def _race(race_id, winner_index, runners, day=1):
    for index, runner in enumerate(runners):
        runner['finish_position'] = 1 if index == winner_index else (index + 2)
        runner.setdefault('sp', 4.0)
    return {
        'race_id': race_id,
        'sort_key': f'2026-01-{day:02d}',
        'runners': runners,
    }


def _map_decides_races(count=80, field=8, seed=1):
    """A world where the speed map is the only thing that matters.

    Every other input is noise. A tuner worth having must put its weight on
    `map_value` and nowhere else.
    """
    rng = random.Random(seed)
    races = []
    for race_id in range(count):
        runners = []
        maps = rng.sample(range(40, 40 + field * 5, 5), field)
        for value in maps:
            runners.append({
                'map_value': float(value),
                'sectional_rank': float(rng.randint(1, field)),
                'adjusted_time': round(rng.uniform(33.0, 36.0), 2),
                'assessment_score': float(rng.randint(30, 95)),
            })
        winner = max(range(field), key=lambda i: runners[i]['map_value'])
        races.append(_race(race_id, winner, runners, day=1 + race_id % 28))
    return races


def _nobody_can_predict_anything(count=60, field=8, seed=3):
    """Pure noise: the winner is drawn at random, unrelated to any input."""
    rng = random.Random(seed)
    races = []
    for race_id in range(count):
        runners = [{
            'map_value': float(rng.randint(40, 95)),
            'sectional_rank': float(rng.randint(1, field)),
            'adjusted_time': round(rng.uniform(33.0, 36.0), 2),
            'assessment_score': float(rng.randint(30, 95)),
        } for _ in range(field)]
        races.append(_race(race_id, rng.randrange(field), runners, day=1 + race_id % 28))
    return races


# ── Preparing races ───────────────────────────────────────────────────────
def test_races_without_a_winner_or_a_field_are_dropped():
    """A race we cannot score teaches a weighting nothing."""
    no_winner = {'race_id': 1, 'sort_key': 'a', 'runners': [
        {'map_value': 50, 'finish_position': 2} for _ in range(8)]}
    too_small = _race(2, 0, [{'map_value': 50}, {'map_value': 60}])
    scratched_out = {'race_id': 3, 'sort_key': 'c', 'runners': [
        {'map_value': 50, 'finish_position': 0} for _ in range(8)]}

    assert tuning.prepare_records([no_winner, too_small, scratched_out]) == []
    # A perfectly good race survives.
    assert len(tuning.prepare_records(_map_decides_races(count=3))) == 3


def test_prepared_races_come_back_in_date_order():
    """The walk-forward split is only honest if time actually runs forwards."""
    races = _map_decides_races(count=40)
    random.Random(9).shuffle(races)
    prepared = tuning.prepare_records(races)
    keys = [race['sort_key'] for race in prepared]
    assert keys == sorted(keys)


def test_every_component_has_a_raw_field_to_read():
    """The tuner and the blend must agree on where each input comes from.

    If a component were ever added to the scoring module without telling the
    tuner which field feeds it, the tuner would silently score that input as
    missing for every runner — and quietly conclude it was worthless.
    """
    assert set(tuning._RAW_FIELD) == set(scoring.COMPONENT_KEYS)


def test_the_prepared_matrix_matches_what_the_blend_would_build():
    """One race, scored two ways, has to come out the same.

    The tuner normalises once up front so a candidate weighting costs a dot
    product instead of a full rebuild. That shortcut is only safe while it
    produces exactly what build_composite_scores() would have.
    """
    race = _map_decides_races(count=1, field=6)[0]
    prepared = tuning.prepare_records([race])[0]

    weights = scoring.resolve_weights({'speed_map': 40, 'sectional': 20,
                                       'adjusted_time': 15, 'assessment': 25})
    blended = scoring.build_composite_scores([dict(r) for r in race['runners']], weights)

    by_name = {}
    for runner in blended:
        by_name[runner['map_value']] = runner['composite_score']

    for row, original in zip(prepared['matrix'], race['runners']):
        composite = sum(row[i] * weights[key]
                        for i, key in enumerate(scoring.COMPONENT_KEYS))
        assert composite == pytest.approx(by_name[original['map_value']], abs=0.02)


# ── Scoring a weighting ───────────────────────────────────────────────────
def test_a_weighting_that_knows_the_answer_scores_better_than_one_that_does_not():
    prepared = tuning.prepare_records(_map_decides_races())

    knows = tuning.evaluate_weights(prepared, _only('speed_map'))
    blind = tuning.evaluate_weights(prepared, _only('assessment'))

    assert knows['strike_rate'] == 100.0
    assert knows['top3_rate'] == 100.0
    assert knows['mean_placing_error'] == 0.0
    assert knows['log_loss'] < blind['log_loss']
    assert blind['strike_rate'] < 50.0


def test_scoring_an_empty_history_says_so_rather_than_inventing_a_number():
    empty = tuning.evaluate_weights([], dict(scoring.WEIGHTS))
    assert empty['races'] == 0
    assert empty['strike_rate'] is None
    assert empty['log_loss'] is None


def test_roi_is_measured_on_the_top_pick_at_its_starting_price():
    """Flat $1 win bet on whatever the weighting rates best."""
    runners = [
        {'map_value': 90, 'assessment_score': 50, 'sp': 3.0},
        {'map_value': 60, 'assessment_score': 90, 'sp': 5.0},
        {'map_value': 55, 'assessment_score': 40, 'sp': 9.0},
        {'map_value': 50, 'assessment_score': 30, 'sp': 15.0},
    ]
    # The map favourite wins, so backing it returns 3.0 on a 1.0 stake: +200%.
    prepared = tuning.prepare_records([_race(1, 0, [dict(r) for r in runners])])
    on_the_map = tuning.evaluate_weights(prepared, _only('speed_map'))
    assert on_the_map['priced_bets'] == 1
    assert on_the_map['roi_pct'] == pytest.approx(200.0)

    # Backing the assessment favourite instead loses the lot: -100%.
    on_assessment = tuning.evaluate_weights(prepared, _only('assessment'))
    assert on_assessment['roi_pct'] == pytest.approx(-100.0)


# ── The search ────────────────────────────────────────────────────────────
def test_the_search_recovers_a_signal_planted_where_it_can_see_it():
    prepared = tuning.prepare_records(_map_decides_races(count=60))
    weights, metrics = tuning.search_weights(
        prepared, criterion='strike_rate', candidates=200, seed=5)

    assert metrics['strike_rate'] == 100.0
    # It has to actually put the weight on the input that decides the races.
    # Not necessarily ALL of it: once the strike rate is perfect there is
    # nothing left for the search to improve, so it stops at the first split
    # that gets there. What matters is that speed map is the one carrying it.
    assert weights['speed_map'] == max(weights.values())
    assert weights['speed_map'] > 0.4
    assert sum(weights.values()) == pytest.approx(1.0)


def test_the_search_leaves_components_outside_its_scope_alone():
    """A tune restricted to the published four must not switch the others on."""
    prepared = tuning.prepare_records(_map_decides_races(count=40))
    weights, _ = tuning.search_weights(
        prepared, search_keys=scoring.CORE_COMPONENT_KEYS, candidates=80, seed=2)

    for key in scoring.COMPONENT_KEYS:
        if key not in scoring.CORE_COMPONENT_KEYS:
            assert weights[key] == 0.0


# ── Walk-forward: the honest number ───────────────────────────────────────
def test_tuning_reports_an_out_of_sample_result():
    outcome = tuning.optimise_weights(
        tuning.prepare_records(_map_decides_races(count=100)),
        criterion='strike_rate', candidates=120)

    assert outcome['ok'] is True
    assert outcome['folds'] >= 1
    # The headline number is measured on races the search never saw.
    assert outcome['out_of_sample']['races'] > 0
    assert outcome['out_of_sample']['races'] < outcome['races']
    assert outcome['out_of_sample']['strike_rate'] > 90.0
    assert outcome['beats_default'] is True
    # And the default is scored over exactly the same races, for comparison.
    assert outcome['default_out_of_sample']['races'] == outcome['out_of_sample']['races']


def test_tuning_on_noise_does_not_claim_to_have_found_anything():
    """The failure mode that makes tuning dangerous.

    Given races nobody could predict, the search will still find a split that
    looks good on the races it was fitted to. The walk-forward split is what
    stops that reaching the page as a claim: out of sample it must not beat the
    default by any real margin.
    """
    prepared = tuning.prepare_records(_nobody_can_predict_anything(count=80))
    outcome = tuning.optimise_weights(prepared, criterion='log_loss', candidates=150)

    assert outcome['ok'] is True
    tuned = outcome['out_of_sample']
    default = outcome['default_out_of_sample']

    # An eight-runner field picked at random is a 12.5% strike rate. Nothing
    # here should be finding much more than that on races it never saw.
    assert tuned['strike_rate'] < 35.0

    # The best any model can do on genuinely unpredictable races is to say so:
    # give every runner an equal chance, which costs -ln(1/8) in log loss. That
    # is an expectation, not a hard bound on any finite sample — a fitted split
    # can land a hair under it by luck — so the guard is that it cannot get
    # MATERIALLY under. Anything well below this floor would mean the training
    # and testing races were leaking into each other.
    uniform_floor = math.log(8)
    assert tuned['log_loss'] > uniform_floor - 0.05

    # Landing ON the floor is the right answer, and it is allowed to beat the
    # published default — which is confidently wrong here, and pays for it.
    assert tuned['log_loss'] <= default['log_loss'] + 1e-9


def test_too_little_history_refuses_rather_than_guessing():
    outcome = tuning.optimise_weights(tuning.prepare_records(_map_decides_races(count=2)))
    assert outcome['ok'] is False
    assert 'reason' in outcome


def test_folds_are_pooled_by_the_races_in_them():
    """A fold of twelve races must not count as much as a fold of two hundred."""
    pooled = tuning._pool([
        {'races': 10, 'strike_rate': 50.0, 'wins': 5, 'log_loss': 1.0,
         'top3_rate': 80.0, 'mean_placing_error': 1.0, 'roi_pct': 10.0, 'priced_bets': 10},
        {'races': 90, 'strike_rate': 10.0, 'wins': 9, 'log_loss': 2.0,
         'top3_rate': 40.0, 'mean_placing_error': 3.0, 'roi_pct': -10.0, 'priced_bets': 90},
    ])
    assert pooled['races'] == 100
    assert pooled['wins'] == 14
    # Weighted, not averaged: (50*10 + 10*90) / 100 = 14, not 30.
    assert pooled['strike_rate'] == pytest.approx(14.0)
    assert pooled['log_loss'] == pytest.approx(1.9)
    assert pooled['roi_pct'] == pytest.approx(-8.0)
