"""
race_animation_tuning.py — measure a weighting, and let history choose one.

Pure Python. No Flask, no SQLAlchemy, no network — it takes plain dicts, so the
search can be unit tested without standing up the app.

WHY THIS EXISTS
The composite's 50/10/10/30 split was chosen, not measured. Nothing anywhere
showed it beat an even split, or a coin toss. This module is the answer to
that: it replays every past race that has a recorded result, scores it under a
given weighting, and reports how often that weighting actually found the
winner.

    evaluate_weights()   one weighting, scored over a set of races
    optimise_weights()   search for a better one, walk-forward

WALK-FORWARD, ALWAYS
Tuning four-to-eight weights against a few hundred races will happily find a
split that "wins" on the races it was tuned on and nothing else. So the search
never reports an in-sample number as if it meant something. Races are ordered
by date and cut into consecutive folds; each fold is scored by a weighting
tuned only on the races BEFORE it. That out-of-sample figure is the honest one,
and it is what the page shows.

The same discipline the backtest pipeline already uses, applied to a much
smaller problem.

PRECOMPUTED NORMALISATION
Normalising a component is a within-field ranking, so it does not depend on the
weights at all. prepare_records() does it once per race up front, which turns
scoring one candidate weighting into a dot product per runner. That is what
makes a search over thousands of candidates finish in a page load.
"""

from __future__ import annotations

import math
import random
from typing import Iterable, Sequence

from race_animation_scoring import (
    COMPONENT_KEYS,
    COMPONENT_LOWER_IS_BETTER,
    DEFAULT_NORM_METHOD,
    PROBABILITY_TEMPERATURE,
    WEIGHTS,
    _impute,
    normalise_component,
    resolve_norm_method,
    to_float,
)

# A race with fewer than this many runners tells us almost nothing about a
# weighting, and match races distort strike rate badly.
MIN_FIELD_SIZE = 4

# Search budget. These are sized so a tune over a few hundred races finishes
# inside a normal request rather than needing a job queue.
DEFAULT_RANDOM_CANDIDATES = 600
DEFAULT_REFINE_ROUNDS = 6
DEFAULT_FOLDS = 4

# The weights the search is allowed to move. Anything outside this stays at
# whatever the caller passed in, so a tune can be restricted to (say) the four
# published components without the newer ones creeping in.
DEFAULT_SEARCH_KEYS = tuple(COMPONENT_KEYS)

# Candidate weights are drawn on this grid (in percentage points) so the
# answer is a split a person can actually read off a slider.
WEIGHT_GRID = 5.0


# ── Preparing races ───────────────────────────────────────────────────────
def prepare_records(races: Iterable[dict], norm_method: str = DEFAULT_NORM_METHOD) -> list[dict]:
    """Normalise every race once, ready for repeated scoring.

    Each input race is a dict of:

        race_id       anything hashable, for reporting
        sort_key      what to order races by for the walk-forward split
                      (a date, a timestamp, or the race id as a last resort)
        runners       list of dicts carrying the same raw component values
                      build_composite_scores() takes, plus:
                          finish_position  1 = won, 0/None = did not run
                          sp               starting price, for the ROI figure

    Races with no recorded winner, or too small a field, are dropped — they
    cannot tell a good weighting from a bad one.

    Returns a list of prepared races, each holding a `matrix` of normalised
    values (one row per runner, one column per component, in COMPONENT_KEYS
    order) and the index of the runner that actually won.
    """
    method = resolve_norm_method(norm_method)
    prepared = []

    for race in races or []:
        runners = [r for r in (race.get('runners') or [])
                   if (to_float(r.get('finish_position')) or 0) > 0]
        if len(runners) < MIN_FIELD_SIZE:
            continue

        winner_index = None
        for index, runner in enumerate(runners):
            if (to_float(runner.get('finish_position')) or 0) == 1:
                winner_index = index
                break
        if winner_index is None:
            continue

        columns = []
        for key in COMPONENT_KEYS:
            raw = [to_float(runner.get(_RAW_FIELD[key])) for runner in runners]
            scaled = normalise_component(raw, COMPONENT_LOWER_IS_BETTER[key], method)
            values, _flags = _impute(scaled)
            columns.append(values)

        matrix = [[columns[c][r] for c in range(len(COMPONENT_KEYS))]
                  for r in range(len(runners))]

        prepared.append({
            'race_id': race.get('race_id'),
            'sort_key': race.get('sort_key') or race.get('race_id') or 0,
            'matrix': matrix,
            'winner_index': winner_index,
            'finish_positions': [int(to_float(r.get('finish_position')) or 0) for r in runners],
            'sps': [to_float(r.get('sp')) for r in runners],
            'field_size': len(runners),
        })

    prepared.sort(key=lambda race: (str(race['sort_key']), str(race['race_id'])))
    return prepared


# Which raw field on a runner dict feeds each component. Mirrors the mapping in
# build_composite_scores() — kept here rather than imported so the two stay
# readable side by side, and the parity is covered by a test.
_RAW_FIELD = {
    'speed_map': 'map_value',
    'sectional': 'sectional_rank',
    'adjusted_time': 'adjusted_time',
    'assessment': 'assessment_score',
    'jockey_trainer': 'jockey_trainer_ae',
    'draw': 'draw_value',
    'pace_fit': 'pace_fit_value',
    'market': 'market_probability',
}


# ── Scoring one weighting ─────────────────────────────────────────────────
def _weight_vector(weights: dict[str, float]) -> list[float]:
    return [float(weights.get(key, 0.0)) for key in COMPONENT_KEYS]


def _composites(matrix: list[list[float]], vector: Sequence[float]) -> list[float]:
    return [sum(row[c] * vector[c] for c in range(len(vector))) for row in matrix]


def evaluate_weights(prepared: Sequence[dict],
                     weights: dict[str, float],
                     temperature: float = PROBABILITY_TEMPERATURE) -> dict:
    """Score one weighting over a prepared set of races.

    Returns, over the races supplied:

        races                    how many were scored
        strike_rate              share where the top-rated runner won
        top3_rate                share where the winner was in our top three
        mean_placing_error       average |predicted rank - actual finish| for
                                 the runner that actually won
        log_loss                 mean -log(probability given to the winner);
                                 lower is better, and unlike strike rate it
                                 rewards being confident for the right reasons
        roi_pct                  flat win bet on the top pick, at starting
                                 price, as a percentage return
        priced_bets              how many of those bets had a usable price

    Strike rate is the headline, but log loss is the one to tune on: a
    weighting can lift strike rate by getting a handful of short-priced
    favourites right while being wrong about everything else.
    """
    vector = _weight_vector(weights)
    tau = max(1e-6, float(temperature))

    races = 0
    hits = 0
    top3 = 0
    placing_error = 0.0
    log_loss_total = 0.0
    staked = 0
    returned = 0.0

    for race in prepared:
        composites = _composites(race['matrix'], vector)
        size = len(composites)
        if size < MIN_FIELD_SIZE:
            continue
        races += 1

        # Rank by composite, best first. Index order breaks ties, which is
        # arbitrary but stable, and the same for every candidate weighting.
        order = sorted(range(size), key=lambda i: (-composites[i], i))
        winner = race['winner_index']
        predicted_place = order.index(winner) + 1

        if order[0] == winner:
            hits += 1
        if predicted_place <= 3:
            top3 += 1
        placing_error += abs(predicted_place - 1)

        best = max(composites)
        strengths = [math.exp((c - best) / tau) for c in composites]
        total = sum(strengths)
        probability = (strengths[winner] / total) if total > 1e-12 else 1.0 / size
        log_loss_total += -math.log(max(probability, 1e-12))

        # Flat $1 on our top pick, settled at its starting price.
        top_pick = order[0]
        price = race['sps'][top_pick]
        if price and price > 1.0:
            staked += 1
            if race['finish_positions'][top_pick] == 1:
                returned += price

    if not races:
        return {
            'races': 0, 'strike_rate': None, 'top3_rate': None,
            'mean_placing_error': None, 'log_loss': None,
            'roi_pct': None, 'priced_bets': 0, 'wins': 0,
        }

    return {
        'races': races,
        'wins': hits,
        'strike_rate': round(hits * 100.0 / races, 2),
        'top3_rate': round(top3 * 100.0 / races, 2),
        'mean_placing_error': round(placing_error / races, 3),
        'log_loss': round(log_loss_total / races, 4),
        'roi_pct': round((returned - staked) * 100.0 / staked, 2) if staked else None,
        'priced_bets': staked,
    }


# ── The search ────────────────────────────────────────────────────────────
def _normalise_vector(raw: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, v) for v in raw.values())
    if total <= 1e-9:
        return dict(WEIGHTS)
    return {key: max(0.0, raw.get(key, 0.0)) / total for key in COMPONENT_KEYS}


def _snap(value: float) -> float:
    """Round a percentage onto the slider grid, so answers stay readable."""
    return round(value / WEIGHT_GRID) * WEIGHT_GRID


def _random_candidate(rng: random.Random, search_keys: Sequence[str],
                      fixed: dict[str, float]) -> dict[str, float]:
    """A random split over the searchable keys, on the slider grid.

    Drawing each weight from an exponential and normalising gives a uniform
    draw over the simplex — every split is as likely as every other, rather
    than clustering around the even one the way independent uniforms do.
    """
    draw = {key: rng.expovariate(1.0) for key in search_keys}
    total = sum(draw.values()) or 1.0
    candidate = dict(fixed)
    for key in search_keys:
        candidate[key] = _snap(draw[key] / total * 100.0)
    return _normalise_vector(candidate)


def _objective(metrics: dict, criterion: str) -> float:
    """Turn a metrics dict into one number to minimise."""
    if not metrics or not metrics.get('races'):
        return float('inf')
    if criterion == 'strike_rate':
        # Negated: the search always minimises.
        return -(metrics.get('strike_rate') or 0.0)
    if criterion == 'top3_rate':
        return -(metrics.get('top3_rate') or 0.0)
    if criterion == 'roi':
        roi = metrics.get('roi_pct')
        return -roi if roi is not None else float('inf')
    # Default: log loss. Rewards being right AND being appropriately confident,
    # which is what a probability that will later be bet into has to be.
    return metrics.get('log_loss') if metrics.get('log_loss') is not None else float('inf')


def search_weights(prepared: Sequence[dict],
                   criterion: str = 'log_loss',
                   search_keys: Sequence[str] = DEFAULT_SEARCH_KEYS,
                   candidates: int = DEFAULT_RANDOM_CANDIDATES,
                   refine_rounds: int = DEFAULT_REFINE_ROUNDS,
                   seed: int = 20260902,
                   start: dict[str, float] | None = None) -> tuple[dict[str, float], dict]:
    """Find the best weighting over exactly the races handed in.

    Random search over the simplex first, to get away from whatever the
    starting split happens to be, then coordinate refinement — nudge one weight
    at a time and keep the move if it helps. Random search alone lands near the
    answer; the refinement walks it in.

    This is IN-SAMPLE by construction. Callers wanting a number they can trust
    use optimise_weights(), which wraps this in a walk-forward split.
    """
    if not prepared:
        return dict(WEIGHTS), {}

    search_keys = [key for key in search_keys if key in COMPONENT_KEYS] or list(COMPONENT_KEYS)
    base = dict(start or WEIGHTS)
    # Weights outside the search stay exactly where the caller left them.
    fixed = {key: base.get(key, 0.0) * 100.0
             for key in COMPONENT_KEYS if key not in search_keys}

    rng = random.Random(seed)

    best_weights = _normalise_vector({key: base.get(key, 0.0) * 100.0 for key in COMPONENT_KEYS})
    best_metrics = evaluate_weights(prepared, best_weights)
    best_score = _objective(best_metrics, criterion)

    # Always try the even split too: it is the honest null hypothesis, and if
    # nothing beats it that is the finding.
    even = _normalise_vector({**fixed, **{key: 100.0 / len(search_keys) for key in search_keys}})
    even_metrics = evaluate_weights(prepared, even)
    if _objective(even_metrics, criterion) < best_score:
        best_weights, best_metrics = even, even_metrics
        best_score = _objective(even_metrics, criterion)

    for _ in range(max(0, int(candidates))):
        candidate = _random_candidate(rng, search_keys, fixed)
        metrics = evaluate_weights(prepared, candidate)
        score = _objective(metrics, criterion)
        if score < best_score:
            best_weights, best_metrics, best_score = candidate, metrics, score

    # Coordinate refinement, with a shrinking step.
    step = 20.0
    for _ in range(max(0, int(refine_rounds))):
        improved = False
        for key in search_keys:
            for direction in (1, -1):
                trial = {k: best_weights[k] * 100.0 for k in COMPONENT_KEYS}
                trial[key] = max(0.0, trial[key] + direction * step)
                candidate = _normalise_vector(trial)
                metrics = evaluate_weights(prepared, candidate)
                score = _objective(metrics, criterion)
                if score < best_score - 1e-9:
                    best_weights, best_metrics, best_score = candidate, metrics, score
                    improved = True
        if not improved:
            step /= 2.0
            if step < WEIGHT_GRID / 2:
                break

    return best_weights, best_metrics


def optimise_weights(prepared: Sequence[dict],
                     criterion: str = 'log_loss',
                     search_keys: Sequence[str] = DEFAULT_SEARCH_KEYS,
                     folds: int = DEFAULT_FOLDS,
                     candidates: int = DEFAULT_RANDOM_CANDIDATES,
                     seed: int = 20260902) -> dict:
    """Tune a weighting walk-forward, and report what it is worth out of sample.

    Races are already in date order. They are cut into `folds` consecutive
    blocks; each block after the first is scored under a weighting tuned only on
    the races before it. Averaging those gives an honest estimate of what this
    tuning is worth on races it has never seen.

    The returned `weights` are then tuned on everything, because that is the
    split you would actually run tomorrow — but the number attached to it is
    the out-of-sample one, never the in-sample one it was fitted to.

    Also returns the same out-of-sample measurement for the published default
    blend, so the answer to "is this actually better?" is on the page rather
    than left to faith.
    """
    total = len(prepared)
    if total < MIN_FIELD_SIZE * 2:
        return {
            'ok': False,
            'reason': 'Not enough finished races with data to tune on yet.',
            'races': total,
        }

    folds = max(2, min(int(folds), max(2, total // 20) if total >= 40 else 2))
    edges = [round(total * i / folds) for i in range(folds + 1)]

    default_weights = dict(WEIGHTS)
    out_of_sample: list[dict] = []
    default_out_of_sample: list[dict] = []
    fold_reports = []

    for index in range(1, folds):
        train = prepared[:edges[index]]
        test = prepared[edges[index]:edges[index + 1]]
        if not train or not test:
            continue

        fold_weights, _train_metrics = search_weights(
            train, criterion=criterion, search_keys=search_keys,
            candidates=candidates, seed=seed + index)
        tuned_metrics = evaluate_weights(test, fold_weights)
        base_metrics = evaluate_weights(test, default_weights)

        if tuned_metrics.get('races'):
            out_of_sample.append(tuned_metrics)
            default_out_of_sample.append(base_metrics)
            fold_reports.append({
                'fold': index,
                'train_races': len(train),
                'test_races': len(test),
                'tuned': tuned_metrics,
                'default': base_metrics,
            })

    if not out_of_sample:
        return {
            'ok': False,
            'reason': 'Not enough history to split into training and testing races.',
            'races': total,
        }

    final_weights, in_sample = search_weights(
        prepared, criterion=criterion, search_keys=search_keys,
        candidates=candidates, seed=seed)

    return {
        'ok': True,
        'races': total,
        'criterion': criterion,
        'folds': len(fold_reports),
        'weights': final_weights,
        'in_sample': in_sample,
        'out_of_sample': _pool(out_of_sample),
        'default_out_of_sample': _pool(default_out_of_sample),
        'default_weights': default_weights,
        'fold_reports': fold_reports,
        'beats_default': _beats(_pool(out_of_sample), _pool(default_out_of_sample), criterion),
    }


def _pool(metrics_list: Sequence[dict]) -> dict:
    """Combine fold metrics, weighting each fold by the races in it.

    A straight mean of fold percentages would let a fold of twelve races count
    as much as a fold of two hundred.
    """
    races = sum(m.get('races') or 0 for m in metrics_list)
    if not races:
        return {'races': 0}

    def weighted(field):
        total = 0.0
        seen = 0
        for m in metrics_list:
            value = m.get(field)
            count = m.get('races') or 0
            if value is not None and count:
                total += value * count
                seen += count
        return round(total / seen, 4) if seen else None

    bets = sum(m.get('priced_bets') or 0 for m in metrics_list)
    roi = None
    if bets:
        # ROI has to be pooled off the underlying stakes, not averaged.
        profit = 0.0
        for m in metrics_list:
            if m.get('roi_pct') is not None and m.get('priced_bets'):
                profit += m['roi_pct'] / 100.0 * m['priced_bets']
        roi = round(profit * 100.0 / bets, 2)

    return {
        'races': races,
        'wins': sum(m.get('wins') or 0 for m in metrics_list),
        'strike_rate': weighted('strike_rate'),
        'top3_rate': weighted('top3_rate'),
        'mean_placing_error': weighted('mean_placing_error'),
        'log_loss': weighted('log_loss'),
        'roi_pct': roi,
        'priced_bets': bets,
    }


def _beats(tuned: dict, default: dict, criterion: str) -> bool:
    """Did the tuned split actually beat the published one, out of sample?"""
    if not tuned.get('races') or not default.get('races'):
        return False
    return _objective(tuned, criterion) < _objective(default, criterion)
