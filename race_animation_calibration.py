"""
race_animation_calibration.py — solve the race backwards, from the result.

Pure Python. No Flask, no SQLAlchemy, no network, so the maths can be unit
tested on its own.

WHY THIS EXISTS
race_animation_tuning.py asks "which weighting finds the most winners overall?"
That is the right question for the model, and the wrong one for a single race
that just got beaten. When Ceolwulf is the selection and something else wins,
the useful question is narrower and much more concrete:

    what would the weighting have had to be for THAT horse to top the field?

This module answers it. Feed it the normalised component values for a field and
the runner that actually won, and it hands back the nearest weighting to the one
you were using that puts the winner on top — or says, plainly, that no weighting
could have found it.

WHY THAT IS WORTH KNOWING
The answer is not a weighting to go and use. One race is one race. It is a
reading of WHY the race was missed, in the only units the page has: a winner
that needed Pace Fit lifted from nothing to a third of the blend was a race the
tempo decided, and a wet Saturday where eight of those in a row all say the same
thing is a track bias showing up in the arithmetic rather than in a hunch.

So there are two halves here:

    solve_for_runner()      one race: the smallest change that finds the winner
    calibration_drift()     many races: where those changes point, and whether
                            following them would actually have won anything

The second half exists because the first half is a trap on its own. Averaging
the per-race answers gives a weighting fitted to results already known, which
will look magnificent and predict nothing. calibration_drift() therefore takes
the direction those races point in, fixes a weighting off the EARLY races only,
and scores it on the later ones it has never seen. That number is the honest
one, and it is the one the page shows.

THE MATHS
Each runner's composite is a dot product: the weighting w against that runner's
normalised component values n. So "runner t beats runner j" is

    w · (n_t - n_j) > 0

which is linear in w. Wanting the target to beat every rival at once is a stack
of those, and the weightings that satisfy them are the intersection of a set of
half-spaces with the simplex (weights are non-negative and sum to 1). That is a
convex region, and three useful things follow from it:

  * Whether it is empty at all is decidable — and it IS empty for a winner that
    is worse than a mix of its rivals on every single input. "Nothing would have
    found this one" is a real answer, not a failure to search hard enough.
  * The region is convex, so the straight line from a weighting that works back
    towards the one you were using crosses the boundary exactly once. Walking
    that line to the boundary gives the SMALLEST change that would have done it.
  * Widening one slider and letting the rest fall away in proportion is also a
    straight line, so the same walk answers "what is the single lever?".

Feasibility is found by maximising the margin — the winner's composite minus the
best rival's — over the simplex. That is a linear program. Rather than take a
dependency for it, this uses projected subgradient ascent seeded from every
single-component corner, which is approximate: it is trusted to find a margin
when a comfortable one exists, and answers on the knife edge are reported as
knife-edge rather than as a clean yes. Every weighting it returns is verified by
re-scoring the field with it before it leaves this module, so a wrong answer
cannot get out even if the search falls short.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

from race_animation_scoring import (
    COMPONENT_KEYS,
    COMPONENT_LABELS,
    COMPONENT_SHORT_LABELS,
    WEIGHTS,
    round_half_up,
    to_float,
)

# How far clear the target has to finish before we call it "this weighting picks
# it". Composites are rounded to two decimals on the page, so anything under
# this is a dead heat that the tie-break decides rather than the weighting.
MARGIN_EPSILON = 0.05

# A weighting a person cannot set on the sliders is not an answer they can use,
# so solved splits are snapped onto a grid. Five-point steps first because they
# read like a real preset; single points if five is too coarse to stay on the
# right side of the boundary; unsnapped only as a last resort.
SNAP_GRIDS = (5.0, 1.0)

# Search budget for one race, and for a race inside a bulk run. Bulk runs solve
# hundreds of races inside one request, so they get the shorter search — the
# corner seeding below does most of the work in either case.
SOLVE_ITERATIONS = 1200
BULK_ITERATIONS = 220

# Bulk analysis caps, so an aggregate run finishes inside a normal request.
MAX_BULK_RACES = 220
# Below this many solved races a median is a coincidence, not a direction.
MIN_DRIFT_RACES = 8
# Share of the solved races used to fix a candidate weighting; the rest are kept
# back, unseen, to score it on.
HOLDOUT_TRAIN_SHARE = 0.6


# ── Small vector helpers ──────────────────────────────────────────────────
def _vector(weights: dict) -> list[float]:
    """Weight dict -> a list in COMPONENT_KEYS order."""
    return [max(0.0, float(weights.get(key, 0.0) or 0.0)) for key in COMPONENT_KEYS]


def _as_dict(vector: Sequence[float]) -> dict[str, float]:
    return {key: float(vector[index]) for index, key in enumerate(COMPONENT_KEYS)}


def _normalised_vector(vector: Sequence[float]) -> list[float]:
    """Rescale to sum to 1, falling back to the published blend if it is empty."""
    total = sum(max(0.0, v) for v in vector)
    if total <= 1e-9:
        return _vector(WEIGHTS)
    return [max(0.0, v) / total for v in vector]


def _dot(row: Sequence[float], vector: Sequence[float]) -> float:
    return sum(row[i] * vector[i] for i in range(len(vector)))


def _project_simplex(values: Sequence[float], total: float = 1.0) -> list[float]:
    """Nearest point to `values` with everything >= 0 and the sum equal to `total`.

    The standard sort-and-threshold projection. Every step of the search below
    lands somewhere that is not a valid weighting; this is what puts it back.
    """
    count = len(values)
    if count == 0:
        return []
    if total <= 0:
        return [0.0] * count

    descending = sorted(values, reverse=True)
    cumulative = 0.0
    theta = 0.0
    for index, value in enumerate(descending):
        cumulative += value
        candidate = (cumulative - total) / (index + 1)
        if value - candidate > 0:
            theta = candidate
    return [max(0.0, value - theta) for value in values]


# ── The field, as the solver sees it ──────────────────────────────────────
def _clean_matrix(matrix: Iterable[Sequence[float]]) -> list[list[float]]:
    """Coerce the normalised values into a rectangular float matrix.

    Rounded to two decimals to match build_composite_scores(), which rounds each
    normalised value BEFORE blending. Solving against unrounded numbers would
    hand back a weighting that does not reproduce on the page it came from.
    """
    rows = []
    for row in matrix or []:
        values = []
        for index in range(len(COMPONENT_KEYS)):
            value = to_float(row[index]) if index < len(row) else None
            values.append(round_half_up(value if value is not None else 0.0, 2))
        rows.append(values)
    return rows


def _differences(matrix: Sequence[Sequence[float]], target: int) -> list[list[float]]:
    """Target minus each rival, component by component.

    Every question this module asks is about the sign of w · d for these rows,
    so working them out once is most of the arithmetic done.
    """
    return [[matrix[target][c] - matrix[j][c] for c in range(len(COMPONENT_KEYS))]
            for j in range(len(matrix)) if j != target]


def _margin(differences: Sequence[Sequence[float]], vector: Sequence[float]) -> float:
    """How far clear the target finishes under this weighting. Negative = beaten."""
    if not differences:
        return float('inf')
    return min(_dot(row, vector) for row in differences)


# ── Finding a weighting that works ────────────────────────────────────────
def _seeds(start: Sequence[float], free: Sequence[int], budget: float) -> list[list[float]]:
    """Where to start the search from.

    Every single-component corner is tried, because a great many missed winners
    were simply the best runner in the race on one input that was carrying no
    weight — and for those the answer is found before any searching happens.
    The current weighting and an even split go in as well.
    """
    seeds: list[list[float]] = []
    if free:
        current = _project_simplex([start[i] for i in free], budget)
        seeds.append(current)
        seeds.append([budget / len(free)] * len(free))
        for position in range(len(free)):
            corner = [0.0] * len(free)
            corner[position] = budget
            seeds.append(corner)
    return seeds


def _maximise_margin(matrix: Sequence[Sequence[float]], target: int,
                     start: Sequence[float], free: Sequence[int],
                     iterations: int) -> tuple[list[float], float]:
    """Find the weighting that puts the target furthest clear of the field.

    Projected subgradient ascent on min_j w · d_j, which is concave and
    piecewise linear: at any weighting the rival that is closest to the target
    is the one that decides the margin, so the direction to move is that
    rival's difference row. Steps shrink as 1/sqrt(t) and each one is projected
    back onto the simplex.

    Components not in `free` keep the weight the caller gave them and the search
    shares out whatever is left, so a viewer can pin the market (or anything
    else) where it is and ask what the rest would have had to do.
    """
    differences = _differences(matrix, target)
    if not differences:
        return list(start), float('inf')

    free = list(free)
    fixed_total = sum(start[i] for i in range(len(COMPONENT_KEYS)) if i not in free)
    budget = max(0.0, 1.0 - fixed_total)

    def assemble(values: Sequence[float]) -> list[float]:
        full = list(start)
        for position, index in enumerate(free):
            full[index] = values[position]
        return full

    best_vector = list(start)
    best_margin = _margin(differences, best_vector)

    if not free or budget <= 1e-9:
        return best_vector, best_margin

    for seed in _seeds(start, free, budget):
        values = list(seed)
        for step_number in range(1, max(1, int(iterations)) + 1):
            full = assemble(values)
            worst_row = min(differences, key=lambda row: _dot(row, full))
            margin = _dot(worst_row, full)
            if margin > best_margin:
                best_margin, best_vector = margin, full

            gradient = [worst_row[index] for index in free]
            norm = math.sqrt(sum(g * g for g in gradient))
            if norm <= 1e-12:
                break
            step = budget / math.sqrt(step_number)
            values = _project_simplex(
                [values[p] + step * gradient[p] / norm for p in range(len(free))],
                budget)

        full = assemble(values)
        margin = _margin(differences, full)
        if margin > best_margin:
            best_margin, best_vector = margin, full

    return best_vector, best_margin


def _pull_back(differences: Sequence[Sequence[float]], solved: Sequence[float],
               start: Sequence[float], epsilon: float) -> float:
    """How far back towards the weighting in use can the answer be dragged?

    The weightings that pick the target form a convex region, so the straight
    line from one that works back to the one in use crosses its edge exactly
    once — bisection finds the crossing. Returns that crossing as a fraction:
    1.0 means the weighting in use already worked, 0.0 means nothing short of
    the extreme would have. Without this the answer would be the most extreme
    weighting that works, which tells a viewer nothing about what they had
    wrong.
    """
    if _margin(differences, start) >= epsilon:
        return 1.0

    low, high = 0.0, 1.0        # low: known good. high: known to fail.
    for _ in range(48):
        middle = (low + high) / 2.0
        if _margin(differences, _blend(solved, start, middle)) >= epsilon:
            low = middle
        else:
            high = middle
    return low


def _blend(solved: Sequence[float], start: Sequence[float], towards: float) -> list[float]:
    """`towards` of the way from the solved weighting back to the one in use."""
    return [solved[i] * (1 - towards) + start[i] * towards for i in range(len(solved))]


def _readable_answer(differences: Sequence[Sequence[float]], solved: Sequence[float],
                     start: Sequence[float], epsilon: float) -> list[float]:
    """The nearest weighting that works AND can be set on the sliders.

    The crossing point found above sits exactly on the boundary, so rounding it
    onto a slider grid falls off the wrong side of the line almost every time.
    Backing off the boundary in a few steps and trying the grids at each one
    buys the room the rounding needs, and costs a couple of percentage points of
    "nearest" to hand back a split a person can actually dial in.
    """
    edge = _pull_back(differences, solved, start, epsilon)
    for retreat in (1.0, 0.92, 0.8, 0.6, 0.4, 0.2, 0.0):
        candidate = _blend(solved, start, edge * retreat)
        snapped = _snap(candidate, differences, epsilon)
        if snapped is not None:
            return snapped
    return _blend(solved, start, edge)


def _snap(vector: Sequence[float], differences: Sequence[Sequence[float]],
          epsilon: float) -> list[float] | None:
    """Round onto a grid a person can actually set on the sliders.

    Largest-remainder so the percentages still sum to 100, and the rounded split
    is re-checked against the field: a grid that rounds the answer back over the
    line is rejected rather than handed out. `None` when no grid survives, which
    leaves the caller to back off the boundary and try again.
    """
    for grid in SNAP_GRIDS:
        raw = [value * 100.0 / grid for value in vector]
        floors = [math.floor(value) for value in raw]
        remainder = int(round(100.0 / grid)) - sum(floors)
        order = sorted(range(len(raw)), key=lambda i: (floors[i] - raw[i], i))
        for position in range(max(0, remainder)):
            floors[order[position % len(order)]] += 1
        candidate = [f * grid / 100.0 for f in floors]
        if _margin(differences, candidate) >= epsilon:
            return candidate
    return None


# ── Reading the answer back ───────────────────────────────────────────────
def _rank_of(matrix: Sequence[Sequence[float]], target: int,
             vector: Sequence[float]) -> int:
    """Where the target rates under this weighting. 1 = top of the field."""
    score = _dot(matrix[target], vector)
    return 1 + sum(1 for j in range(len(matrix))
                   if j != target and _dot(matrix[j], vector) > score)


def _solo_ranks(matrix: Sequence[Sequence[float]],
                target: int) -> tuple[dict[str, int], dict[str, int]]:
    """Where the target rates on each input taken entirely on its own.

    The quickest read on a missed winner there is: an input showing 1 here is an
    input that had the race right while the blend was looking elsewhere.

    Ties are counted alongside, because they are not the same finding at all. An
    input nobody in the field has data for is imputed to the field average for
    everyone, which makes every runner "equal first" on it — and calling that a
    lead would send somebody chasing a column that is empty.
    """
    ranks = {}
    ties = {}
    for index, key in enumerate(COMPONENT_KEYS):
        value = matrix[target][index]
        ranks[key] = 1 + sum(1 for j in range(len(matrix))
                             if j != target and matrix[j][index] > value)
        ties[key] = sum(1 for j in range(len(matrix))
                        if j != target and abs(matrix[j][index] - value) < 1e-9)
    return ranks, ties


def _single_lever(matrix: Sequence[Sequence[float]], target: int,
                  start: Sequence[float], index: int,
                  epsilon: float) -> float | None:
    """Smallest share of the blend this one input needs to find the target.

    Everything else keeps its current proportions and gives way as this rises,
    so the answer reads as one slider moved rather than eight. `None` when even
    handing this input the whole blend would not have found the winner.
    """
    differences = _differences(matrix, target)
    others = sum(start[i] for i in range(len(start)) if i != index)

    def at(share: float) -> list[float]:
        vector = [0.0] * len(start)
        vector[index] = share
        if others > 1e-9:
            for i in range(len(start)):
                if i != index:
                    vector[i] = start[i] * (1.0 - share) / others
        elif share < 1.0:
            # Nothing else is carrying weight, so there is no proportion to
            # keep: the rest is spread evenly.
            spread = (1.0 - share) / max(1, len(start) - 1)
            for i in range(len(start)):
                if i != index:
                    vector[i] = spread
        return vector

    if _margin(differences, at(1.0)) < epsilon:
        return None
    if _margin(differences, at(start[index])) >= epsilon:
        return start[index]

    # Concave along the line and it works at 100%, so the shares that find the
    # target are everything above one crossing: bisect for it.
    low, high = start[index], 1.0
    for _ in range(40):
        middle = (low + high) / 2.0
        if _margin(differences, at(middle)) >= epsilon:
            high = middle
        else:
            low = middle

    # Round UP to a whole percentage point. Everything above the crossing works,
    # so rounding up is always still an answer, and a slider only takes whole
    # numbers anyway.
    snapped = math.ceil(high * 100.0 - 1e-9) / 100.0
    return min(1.0, snapped)


def _percentages(vector: Sequence[float]) -> dict[str, float]:
    return {key: round(vector[index] * 100.0, 1)
            for index, key in enumerate(COMPONENT_KEYS)}


# ── One race ──────────────────────────────────────────────────────────────
def solve_for_runner(matrix: Iterable[Sequence[float]],
                     target_index: int,
                     start_weights: dict | None = None,
                     locked_keys: Sequence[str] = (),
                     epsilon: float = MARGIN_EPSILON,
                     iterations: int = SOLVE_ITERATIONS,
                     labels: Sequence[str] | None = None) -> dict:
    """What would the weighting have had to be for this runner to rate top?

    `matrix` is one row per runner, one column per component in COMPONENT_KEYS
    order, holding the normalised (0-100, within-field) values the composite is
    blended from — exactly what the race payload already carries on every
    runner. `target_index` is the row to lift to the top, normally the horse
    that actually won.

    Returns a dict carrying:

        reachable        was there ANY weighting that would have found it
        marginal         it is reachable, but only by a hair
        already_top      the weighting in use already had it on top
        weights          the nearest weighting to yours that finds it, as
                         percentages summing to 100
        shifts           per component: from, to, and the move between them
        headline_shifts  the same, biggest move first, nothing under 0.5pt
        single_levers    per component: the share it would need on its own
        best_lever       the smallest of those — one slider, and nothing else
        solo_ranks       where the target rates on each input by itself
        solo_ties        how many runners share that value — a "1st" with ties
                         is an input nobody had data for, not a lead
        start_rank       where the target rated on the weighting in use
        margin           how far clear it finishes on the solved weighting
        beaten_by        the rivals that were ahead of it before, and the gap
        blocked_by       when nothing works, the rivals that make it impossible
    """
    rows = _clean_matrix(matrix)
    if not rows or not (0 <= target_index < len(rows)):
        return {'ok': False, 'reason': 'No field to solve against.'}
    if len(rows) < 2:
        return {'ok': False, 'reason': 'A one-runner field has nothing to solve.'}

    names = list(labels or [])
    start = _normalised_vector(_vector(start_weights if isinstance(start_weights, dict)
                                       else WEIGHTS))
    locked = {key for key in (locked_keys or []) if key in COMPONENT_KEYS}
    free = [index for index, key in enumerate(COMPONENT_KEYS) if key not in locked]

    differences = _differences(rows, target_index)
    start_margin = _margin(differences, start)
    start_rank = _rank_of(rows, target_index, start)

    def name_of(row_index: int) -> str:
        return names[row_index] if row_index < len(names) else f'Runner {row_index + 1}'

    # Who was in front of it, and by how much, on the weighting in use. This is
    # the gap the answer below has to close.
    start_score = _dot(rows[target_index], start)
    beaten_by = sorted(
        [{'index': j, 'name': name_of(j),
          'gap': round(_dot(rows[j], start) - start_score, 2)}
         for j in range(len(rows))
         if j != target_index and _dot(rows[j], start) > start_score],
        key=lambda item: -item['gap'])

    solo_ranks, solo_ties = _solo_ranks(rows, target_index)

    if start_margin >= epsilon:
        return {
            'ok': True, 'reachable': True, 'marginal': False, 'already_top': True,
            'weights': _percentages(start), 'start_weights': _percentages(start),
            'shifts': _shifts(start, start), 'headline_shifts': [],
            # No lever is being pulled, because nothing needs to move.
            'single_levers': {key: None for key in COMPONENT_KEYS},
            'best_lever': None,
            'solo_ranks': solo_ranks, 'solo_ties': solo_ties,
            'start_rank': start_rank,
            'margin': round(start_margin, 2), 'moved_points': 0.0,
            'beaten_by': [], 'blocked_by': [],
            'locked_keys': sorted(locked),
        }

    solved, best_margin = _maximise_margin(rows, target_index, start, free, iterations)

    if best_margin < epsilon:
        # Nothing on the simplex gets there. That happens when the target is
        # worse than some mix of its rivals on every input at once, and it is a
        # real finding: this one was not missed by a bad weighting, it was
        # missed because nothing in the data liked it.
        blocked = sorted(
            [{'index': j, 'name': name_of(j),
              'gap': round(_dot(rows[j], solved) - _dot(rows[target_index], solved), 2)}
             for j in range(len(rows)) if j != target_index],
            key=lambda item: -item['gap'])[:3]
        return {
            'ok': True, 'reachable': False, 'marginal': False, 'already_top': False,
            'weights': None, 'start_weights': _percentages(start),
            'shifts': [], 'headline_shifts': [],
            'single_levers': {key: None for key in COMPONENT_KEYS},
            'best_lever': None,
            'solo_ranks': solo_ranks, 'solo_ties': solo_ties,
            'start_rank': start_rank,
            'margin': round(best_margin, 2), 'moved_points': None,
            'best_possible_margin': round(best_margin, 2),
            'beaten_by': beaten_by[:3], 'blocked_by': blocked,
            'locked_keys': sorted(locked),
        }

    nearest = _readable_answer(differences, solved, start, epsilon)
    final_margin = _margin(differences, nearest)

    shifts = _shifts(start, nearest)
    levers = {}
    for index, key in enumerate(COMPONENT_KEYS):
        share = None if key in locked else _single_lever(rows, target_index, start,
                                                         index, epsilon)
        levers[key] = None if share is None else round(share * 100.0, 1)

    lever_options = [(key, value) for key, value in levers.items() if value is not None]
    best_lever = None
    if lever_options:
        key, value = min(lever_options,
                         key=lambda item: abs(item[1] - _percentages(start)[item[0]]))
        best_lever = {
            'key': key,
            'label': COMPONENT_LABELS[key],
            'short_label': COMPONENT_SHORT_LABELS[key],
            'from': _percentages(start)[key],
            'to': value,
        }

    # One number for "how much did you have to change your mind": the total
    # weight that moved, which is half the sum of the absolute shifts because
    # every point taken off one input has to land on another.
    moved = round(sum(abs(shift['shift']) for shift in shifts) / 2.0, 1)

    return {
        'ok': True,
        'reachable': True,
        'marginal': final_margin < epsilon * 4,
        'already_top': False,
        'weights': _percentages(nearest),
        'start_weights': _percentages(start),
        'shifts': shifts,
        'headline_shifts': [s for s in sorted(shifts, key=lambda s: -abs(s['shift']))
                            if abs(s['shift']) >= 0.5],
        'single_levers': levers,
        'best_lever': best_lever,
        'solo_ranks': solo_ranks,
        'solo_ties': solo_ties,
        'start_rank': start_rank,
        'margin': round(final_margin, 2),
        'best_possible_margin': round(best_margin, 2),
        'moved_points': moved,
        'beaten_by': beaten_by[:3],
        'blocked_by': [],
        'locked_keys': sorted(locked),
    }


def _shifts(start: Sequence[float], solved: Sequence[float]) -> list[dict]:
    """Per component: where you were, where you would have needed to be."""
    out = []
    for index, key in enumerate(COMPONENT_KEYS):
        was = round(start[index] * 100.0, 1)
        now = round(solved[index] * 100.0, 1)
        out.append({
            'key': key,
            'label': COMPONENT_LABELS[key],
            'short_label': COMPONENT_SHORT_LABELS[key],
            'from': was,
            'to': now,
            'shift': round(now - was, 1),
        })
    return out


# ── Many races ────────────────────────────────────────────────────────────
def condition_group(track_condition) -> str:
    """'Soft 6' -> 'soft'. The four buckets punters actually talk about."""
    text = str(track_condition or '').strip().lower()
    if not text:
        return 'unknown'
    for name in ('heavy', 'soft', 'good', 'firm'):
        if name in text:
            return name
    if 'synthetic' in text or 'poly' in text or 'tapeta' in text:
        return 'synthetic'
    if 'slow' in text or 'dead' in text:      # the older Australian scale
        return 'soft'
    if 'fast' in text:
        return 'firm'
    return 'unknown'


def _median(values: Sequence[float]) -> float | None:
    usable = sorted(v for v in values if v is not None)
    if not usable:
        return None
    middle = len(usable) // 2
    if len(usable) % 2:
        return float(usable[middle])
    return (usable[middle - 1] + usable[middle]) / 2.0


def calibration_drift(prepared: Sequence[dict],
                      start_weights: dict | None = None,
                      epsilon: float = MARGIN_EPSILON,
                      iterations: int = BULK_ITERATIONS,
                      max_races: int = MAX_BULK_RACES,
                      group_by: str = 'condition') -> dict:
    """Where the missed winners were pulling the weighting, across many races.

    `prepared` is what race_animation_tuning.prepare_records() produces: each
    race carrying its normalised `matrix`, the `winner_index`, and whatever
    `context` the caller attached (track condition, distance, and so on).

    For every race the weighting in use did not win, this solves the smallest
    change that would have found the winner, and then reports where those
    changes point — overall and split by whatever `group_by` names. A wet day
    where the misses all wanted Pace Fit lifted is a track bias, stated in the
    only units this page has.

    THE TRAP, AND WHAT IS DONE ABOUT IT
    The median of those solved weightings is fitted to results already known. Run
    it back over the same races and it will look extraordinary and mean nothing.
    So `holdout` fixes that median off the EARLIEST solved races only and scores
    it against the published default over the LATER races, which it has never
    seen. When the two halves disagree, the drift was noise. That comparison is
    the whole point of the section, and `drift` on its own should never be read
    without it.
    """
    from race_animation_tuning import evaluate_weights      # local: avoids a cycle

    start = _normalised_vector(_vector(start_weights if isinstance(start_weights, dict)
                                       else WEIGHTS))
    races = list(prepared or [])[:max(1, int(max_races))]

    considered = 0
    already = 0
    solved_records: list[dict] = []
    unreachable = 0

    for race in races:
        matrix = _clean_matrix(race.get('matrix') or [])
        winner = race.get('winner_index')
        if not matrix or winner is None or not (0 <= winner < len(matrix)):
            continue
        if len(matrix) < 2:
            continue
        considered += 1

        differences = _differences(matrix, winner)
        if _margin(differences, start) >= epsilon:
            already += 1
            continue

        outcome = solve_for_runner(matrix, winner, _as_dict(start),
                                   epsilon=epsilon, iterations=iterations)
        if not outcome.get('reachable'):
            unreachable += 1
            continue

        solved_records.append({
            'race_id': race.get('race_id'),
            'sort_key': race.get('sort_key'),
            'context': race.get('context') or {},
            'weights': outcome['weights'],
            'shifts': {s['key']: s['shift'] for s in outcome['shifts']},
            'moved_points': outcome.get('moved_points'),
            'best_lever': outcome.get('best_lever'),
            'solo_ranks': outcome.get('solo_ranks') or {},
        })

    result = {
        'ok': bool(solved_records),
        'races': considered,
        'already_found': already,
        'missed': considered - already,
        'solved': len(solved_records),
        'unreachable': unreachable,
        'start_weights': _percentages(start),
        'group_by': group_by,
    }

    if not solved_records:
        result['reason'] = ('No settled races where the winner was missed and a '
                            'weighting existed that would have found it.')
        return result

    result['drift'] = _summarise(solved_records)
    result['groups'] = _group_summaries(solved_records, group_by)
    result['holdout'] = _holdout(prepared, solved_records, start, evaluate_weights)
    result['lever_counts'] = _lever_counts(solved_records)
    return result


def _summarise(records: Sequence[dict]) -> dict:
    """The middle of the solved weightings, and which way each input was pulled.

    Median rather than mean throughout. One race that needed an input taken to
    100% would drag a mean across the whole set; the median says what the
    typical missed winner wanted, which is the question being asked.
    """
    percentages = {}
    shifts = {}
    raised = {}
    lowered = {}
    for index, key in enumerate(COMPONENT_KEYS):
        weights = [record['weights'].get(key) for record in records]
        moves = [record['shifts'].get(key) for record in records]
        percentages[key] = round(_median(weights) or 0.0, 1)
        shifts[key] = round(_median(moves) or 0.0, 1)
        raised[key] = sum(1 for m in moves if m is not None and m > 0.5)
        lowered[key] = sum(1 for m in moves if m is not None and m < -0.5)

    # Medians do not sum to 100 — they are eight separate middles — so the
    # weighting handed back is rescaled. That is exactly what the sliders do to
    # anything a viewer types, so it is the same blend either way.
    median_vector = _normalised_vector([percentages[key] for key in COMPONENT_KEYS])

    return {
        'median_weights': percentages,
        'suggested_weights': _percentages(median_vector),
        'median_shift': shifts,
        'raised_in': raised,
        'lowered_in': lowered,
        'races': len(records),
        'median_moved_points': round(
            _median([r.get('moved_points') for r in records]) or 0.0, 1),
        'biggest_pull': max(
            ({'key': key, 'label': COMPONENT_LABELS[key],
              'short_label': COMPONENT_SHORT_LABELS[key], 'shift': shifts[key]}
             for key in COMPONENT_KEYS),
            key=lambda item: abs(item['shift'])),
    }


def _lever_counts(records: Sequence[dict]) -> list[dict]:
    """How often each input was the single slider that would have done it."""
    counts: dict[str, int] = {}
    for record in records:
        lever = record.get('best_lever') or {}
        key = lever.get('key')
        if key:
            counts[key] = counts.get(key, 0) + 1
    return sorted(
        [{'key': key, 'label': COMPONENT_LABELS[key],
          'short_label': COMPONENT_SHORT_LABELS[key], 'races': count}
         for key, count in counts.items()],
        key=lambda item: -item['races'])


def _group_summaries(records: Sequence[dict], group_by: str) -> list[dict]:
    """The same reading, split by track condition (or whatever was asked for).

    This is the part worth having on a wet Saturday: eight misses that all
    wanted the same input lifted is a bias, and eight that wanted different
    things is a normal day.
    """
    buckets: dict[str, list[dict]] = {}
    for record in records:
        value = str((record.get('context') or {}).get(group_by) or 'unknown')
        buckets.setdefault(value, []).append(record)

    out = []
    for name, group in buckets.items():
        if len(group) < 3:
            # Three races cannot show a bias, and printing one as if it could is
            # how a page starts lying to the person reading it.
            out.append({'group': name, 'races': len(group), 'enough': False})
            continue
        summary = _summarise(group)
        summary.update({'group': name, 'enough': True})
        out.append(summary)
    return sorted(out, key=lambda item: -item.get('races', 0))


def _order_key(race: dict) -> tuple[str, str]:
    """Date first, race id to break the tie — the same order prepare_records uses."""
    return (str(race.get('sort_key') or ''), str(race.get('race_id') or ''))


def _holdout(prepared: Sequence[dict], records: Sequence[dict],
             start: Sequence[float], evaluate_weights) -> dict:
    """Fix a weighting off the early misses, then score it on the later races.

    The only honest way to read the drift. If following where the misses pointed
    is worth anything, it shows up here on races the median never saw; if it is
    hindsight dressed as a finding, it shows up here too.

    "Better" is not simply a bigger number: a strike rate measured over eighty
    races wobbles by a few points on nothing at all, so the gap has to clear
    that wobble before this reports a win.
    """
    if len(records) < MIN_DRIFT_RACES:
        return {
            'ok': False,
            'reason': (f'Only {len(records)} solved races — at least '
                       f'{MIN_DRIFT_RACES} are needed before a split means anything.'),
        }

    ordered = sorted(records, key=_order_key)
    cut = max(1, int(len(ordered) * HOLDOUT_TRAIN_SHARE))
    train = ordered[:cut]
    if len(ordered) - cut < 3:
        return {'ok': False, 'reason': 'Not enough later races to test on yet.'}

    trained = _summarise(train)['suggested_weights']
    trained_fractions = {key: value / 100.0 for key, value in trained.items()}

    # Score on every race after the last training race — not just the misses,
    # because a weighting that finds new winners while losing the ones already
    # being found is not an improvement. The comparison is on (date, race id) so
    # that a date shared by a whole meeting still splits cleanly, and so that no
    # race used to fix the weighting can also be used to score it.
    boundary = _order_key(train[-1])
    test = [race for race in prepared if _order_key(race) > boundary]
    if len(test) < 5:
        return {'ok': False, 'reason': 'Not enough later races to test on yet.'}

    tuned = evaluate_weights(test, trained_fractions)
    baseline = evaluate_weights(test, _as_dict(start))

    # One extra winner in eighty races is not a finding, and a page that calls
    # it one will have somebody betting on it by Saturday. The gap has to clear
    # the noise a strike rate carries at this many races before it is reported
    # as a win: one standard error on the baseline rate, which for 10% over 90
    # races is about three points.
    rate = (baseline.get('strike_rate') or 0.0) / 100.0
    scored = max(1, baseline.get('races') or len(test))
    noise = 100.0 * math.sqrt(max(rate * (1 - rate), 1e-6) / scored)
    gap = (tuned.get('strike_rate') or 0.0) - (baseline.get('strike_rate') or 0.0)

    return {
        'ok': True,
        'train_races': len(train),
        'test_races': len(test),
        'weights': trained,
        'tuned': tuned,
        'baseline': baseline,
        'gap': round(gap, 2),
        'noise_band': round(noise, 2),
        'beats_baseline': gap > noise,
        'inside_noise': 0 < gap <= noise,
    }
