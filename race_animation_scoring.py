"""
race_animation_scoring.py — composite prediction score for the Race Animations page.

Pure Python. No Flask, no SQLAlchemy, no network. Everything in here takes plain
dicts/lists so the maths can be unit tested on its own, and so nothing that the
rest of the site depends on is imported (and therefore nothing existing can be
disturbed by this module).

COMPONENTS
The composite is a weighted blend of eight inputs. The first four are the
original published blend and still carry the whole default weighting between
them; the last four are newer inputs that default to zero weight, so adding
them changed no existing prediction. They are there to be dialled up on the
page or discovered by the weight tuner.

    Speed Map (MAP / mapA2E)                 50%   higher is better
    PFAI sectional heatmap rank              10%   LOWER is better  -> inverted
    Adjusted times (best recent, seconds)    10%   LOWER is better  -> inverted
    Overall race assessment (ML / PFAI)      30%   higher is better
    Jockey + trainer strike rate (A/E)        0%   higher is better
    Barrier draw, adjusted for the rail       0%   higher is better
    Pace fit (map role vs race tempo)         0%   higher is better
    Market (fair probability from price)      0%   higher is better

NORMALISATION
Each input is normalised to 0-100 *within the race field* before weighting,
because ranks, times and A/E ratios are only comparable against the other
runners in the same race. Two methods are available:

    'rank'   (default) where the runner sits in the field on that input,
             by average rank. One freak value cannot distort anybody else.
    'minmax' the original min-max stretch, kept so the two can be compared.

Both land on the NORM_FLOOR..NORM_CEILING band rather than the full 0-100:
handing the field's best runner a perfect 100 and its worst a flat 0 on every
component overstates small gaps in a tight race.

Missing components are imputed with the field mean of that component rather
than being scored 0, so a runner with (say) no sectional data is not punished
for it. Every component carries an `available` flag so the page can show what
was real and what was filled in.

BEYOND THE RANKING
build_composite_scores() produces an ordering. On its own an ordering is not a
prediction you can bet into, so this module also turns it into:

    win_probabilities()  composite -> win probability (Plackett-Luce strengths)
    value_edge()         model probability vs the market's, and a Kelly stake
    simulate_race()      the same probabilities run N times, for an honest
                         "wins 34% of the time" instead of one certain result

SHARED CONSTANTS
scoring_constants() hands the whole configuration to the page as JSON, so the
browser's copy of this arithmetic reads its numbers from here rather than
carrying its own hardcoded duplicates. tests/test_race_animation_parity.py runs
both implementations over the same fixture and fails if they disagree.
"""

from __future__ import annotations

import math
import random
import re
from typing import Any, Sequence

# ── Weights ───────────────────────────────────────────────────────────────
# This is the published default blend. It is what the page loads with and what
# its "Default 50/10/10/30" preset puts back; a viewer can dial their own split
# on the page instead, which arrives here through resolve_weights().
#
# The four newer components sit at zero on purpose. Adding an input to the
# blend is a change to every prediction on the site, so they arrive switched
# off and are turned on deliberately — by a preset, a slider, or the tuner
# proving they earn their place.
WEIGHTS = {
    'speed_map': 0.50,
    'sectional': 0.10,
    'adjusted_time': 0.10,
    'assessment': 0.30,
    'jockey_trainer': 0.0,
    'draw': 0.0,
    'pace_fit': 0.0,
    'market': 0.0,
}

# The original four, kept named so tests and presets can talk about "the
# published blend" without listing it again.
CORE_COMPONENT_KEYS = ('speed_map', 'sectional', 'adjusted_time', 'assessment')

COMPONENT_KEYS = (
    'speed_map', 'sectional', 'adjusted_time', 'assessment',
    'jockey_trainer', 'draw', 'pace_fit', 'market',
)

COMPONENT_LABELS = {
    'speed_map': 'Speed Map (MAP)',
    'sectional': 'PFAI Sectional Rank',
    'adjusted_time': 'Adjusted Time',
    'assessment': 'Race Assessment',
    'jockey_trainer': 'Jockey + Trainer',
    'draw': 'Barrier & Rail',
    'pace_fit': 'Pace Fit',
    'market': 'Market',
}

COMPONENT_SHORT_LABELS = {
    'speed_map': 'MAP',
    'sectional': 'Sectional',
    'adjusted_time': 'Adj Time',
    'assessment': 'Assessment',
    'jockey_trainer': 'Jky/Trn',
    'draw': 'Draw',
    'pace_fit': 'Pace Fit',
    'market': 'Market',
}

# How many decimals the page shows for each raw value.
COMPONENT_DECIMALS = {
    'speed_map': 2,
    'sectional': 1,
    'adjusted_time': 2,
    'assessment': 1,
    'jockey_trainer': 2,
    'draw': 1,
    'pace_fit': 1,
    'market': 3,
}

# Direction of each raw input: True when a LOWER raw value is the better one.
COMPONENT_LOWER_IS_BETTER = {
    'speed_map': False,
    'sectional': True,
    'adjusted_time': True,
    'assessment': False,
    'jockey_trainer': False,
    'draw': False,
    'pace_fit': False,
    'market': False,
}

# Normalised values are squeezed into this band instead of the full 0-100.
NORM_FLOOR = 5.0
NORM_CEILING = 95.0
NORM_NEUTRAL = 50.0

# Available normalisation methods. 'rank' is the default because min-max lets a
# single outlier at either end of the field compress everybody else into a
# narrow band, which hides real differences between the runners who matter.
NORM_METHODS = ('rank', 'minmax')
DEFAULT_NORM_METHOD = 'rank'

# Pace categories, keyed off the speed map `settle` value. These thresholds are
# copied from the speed map rendering in templates/view_meeting.html so the two
# pages always agree on who is a leader and who is a backmarker.
PACE_CATEGORIES = ('leader', 'onpace', 'midfield', 'back')
PACE_LABELS = {
    'leader': 'Leader',
    'onpace': 'On Pace',
    'midfield': 'Midfield',
    'back': 'Backmarker',
}

# ── Win probability ───────────────────────────────────────────────────────
# Composite points -> Plackett-Luce strength via exp(composite / TEMPERATURE).
# The composite runs on a 5-95 band, so at tau = 12 a 20-point edge is worth
# about 5x the strength of the runner it is beating, which lands favourites in
# the $2-$4 range that real fields produce. Lower tau = more opinionated.
PROBABILITY_TEMPERATURE = 12.0

# Kelly is shown at a fraction of full, because full Kelly on a model this
# young is a fast way to lose a bankroll. Matches the caution used elsewhere.
KELLY_FRACTION = 0.25
# Anything below this edge is noise, not value.
MIN_VALUE_EDGE = 0.02

# ── Finish margins ────────────────────────────────────────────────────────
MARGIN_MIN = 0.4
MARGIN_MAX = 3.2
MARGIN_TOTAL = 26.0

# ── Pace model ────────────────────────────────────────────────────────────
# How much of a race's tempo is decided by how many runners want the front.
# A field with four genuine leaders is a speed duel and the pace collapses; a
# field with one is a soft lead nobody runs down.
PACE_PRESSURE_SOFT = 1.0    # this many leaders or fewer = an uncontested lead
PACE_PRESSURE_HOT = 4.0     # this many or more = a genuine speed battle
# Pace fit is scored on a -1..+1 scale before normalisation. These are the
# swing each pace role takes between a soft tempo and a hot one.
PACE_ROLE_SWING = {
    'leader': -1.0,     # loves a soft lead, cooked in a speed duel
    'onpace': -0.35,
    'midfield': 0.35,
    'back': 1.0,        # needs the pace to fall over to be any good
}
# The meeting's own pace bias (-2 backmarkers .. +2 leaders), as set by hand on
# the meeting. Scaled to the same -1..+1 space and added on top.
PACE_BIAS_WEIGHT = 0.5


def round_half_up(value: Any, places: int = 0):
    """Round the way the browser does, so the two implementations agree.

    Python's built-in round() breaks a tie to the nearest EVEN digit; every
    JavaScript engine's Math.round() breaks it upwards. On a composite that
    lands exactly on a half-cent the two disagree by 0.01, which is invisible in
    the table and very visible by the time it has been through exp() into a win
    probability and out again as a $31.70-versus-$31.73 fair price.

    Every number this module hands to the page goes through here, and
    tests/test_race_animation_parity.py runs both sides over the same fixture to
    prove they still land on the same digit.
    """
    if value is None:
        return None
    factor = 10 ** places
    return math.floor(float(value) * factor + 0.5) / factor


def normalise_name(name: Any) -> str:
    """Lowercase, strip punctuation, collapse whitespace.

    Mirrors normalize_runner_name() in app.py and _norm() in ladbrokes.py so
    that names coming from PuntingForm, Ladbrokes and our own DB all collide on
    the same key.
    """
    if not name:
        return ''
    text = str(name).lower().strip()
    text = re.sub(r'[^a-z0-9\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def to_float(value: Any) -> float | None:
    """Best-effort float, tolerating '', None, '1,234', '$1234', '1400m'."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(',', '').replace('$', '').replace('m', '')
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def resolve_norm_method(requested: Any) -> str:
    """Pick a normalisation method, falling back to the published default."""
    text = str(requested or '').strip().lower()
    return text if text in NORM_METHODS else DEFAULT_NORM_METHOD


def resolve_weights(overrides: Any = None) -> dict[str, float]:
    """Turn a requested weight split into usable fractions that sum to 1.0.

    `overrides` is whatever came off the page or the query string — a dict keyed
    by component, holding either percentages (50) or fractions (0.5). Anything
    missing keeps its published default, negatives are floored at zero, and the
    result is rescaled so the weights always sum to exactly 1.0. That rescaling
    is what lets the sliders be moved freely: a viewer who asks for 60/20/20/20
    gets those proportions, not a composite that quietly runs to 120% and stops
    being comparable with the default blend.

    A request where everything is zero (or nothing parses) carries no ordering
    information at all, so the published defaults are handed back instead.
    """
    if not isinstance(overrides, dict):
        return dict(WEIGHTS)

    supplied: dict[str, float] = {}
    for key in COMPONENT_KEYS:
        value = to_float(overrides.get(key))
        if value is not None:
            supplied[key] = max(0.0, value)

    if not supplied:
        return dict(WEIGHTS)

    # Percentages and fractions are both accepted, because only the proportions
    # between the components survive the rescale below — 50/10/10/30 and
    # 0.5/0.1/0.1/0.3 are the same blend. The one place the scale does matter is
    # a partial request (?w_speed_map=60 on its own): the defaults filling the
    # gaps have to be put on the same scale as what was asked for, or a 60 would
    # be blended against 0.1s and swamp them.
    scale = 100.0 if max(supplied.values()) > 1.5 else 1.0
    requested = {
        key: supplied.get(key, WEIGHTS[key] * scale) for key in COMPONENT_KEYS
    }

    total = sum(requested.values())
    if total <= 1e-9:
        return dict(WEIGHTS)
    return {key: value / total for key, value in requested.items()}


def weights_as_percentages(weights: dict[str, float]) -> dict[str, float]:
    """Fractions -> percentages for display, rounded to one decimal."""
    return {key: round(weights.get(key, 0.0) * 100, 1) for key in COMPONENT_KEYS}


def pace_category_for_settle(settle: Any) -> str:
    """Speed map `settle` position -> pace bucket.

    settle <= 2 leader, <= 4 on pace, <= 8 midfield, otherwise backmarker —
    identical to renderSpeedMap() in view_meeting.html.
    """
    value = to_float(settle)
    if value is None:
        return 'midfield'
    if value <= 2:
        return 'leader'
    if value <= 4:
        return 'onpace'
    if value <= 8:
        return 'midfield'
    return 'back'


# ── Normalisation ─────────────────────────────────────────────────────────
def _minmax(raw_values: Sequence[float | None], lower_is_better: bool) -> list[float | None]:
    """Stretch the field between its best and worst raw value.

    Kept for comparison against the default. Honest about magnitude, but one
    outlier at either end squashes the rest of the field into a narrow band.
    """
    present = [v for v in raw_values if v is not None]
    if not present:
        return [None] * len(raw_values)

    low = min(present)
    high = max(present)
    if high - low < 1e-9:
        return [None if v is None else NORM_NEUTRAL for v in raw_values]

    span = high - low
    out: list[float | None] = []
    for value in raw_values:
        if value is None:
            out.append(None)
            continue
        fraction = (value - low) / span          # 0 at lowest raw, 1 at highest
        if lower_is_better:
            fraction = 1.0 - fraction
        out.append(NORM_FLOOR + fraction * (NORM_CEILING - NORM_FLOOR))
    return out


def _rank(raw_values: Sequence[float | None], lower_is_better: bool) -> list[float | None]:
    """Score each runner by where it sits in the field, not by how far apart.

    Ties share the average of the ranks they span, so a field where everybody
    posts the same number lands on NORM_NEUTRAL throughout and the component
    goes inert rather than inventing an order out of nothing.

    This is the default because it cannot be distorted: one horse with a wild
    MAP figure moves only its own score, where min-max would have it compress
    every other runner in the race.
    """
    indexed = [(i, v) for i, v in enumerate(raw_values) if v is not None]
    if not indexed:
        return [None] * len(raw_values)

    count = len(indexed)
    if count == 1:
        # One data point carries no ordering information about a field.
        out: list[float | None] = [None] * len(raw_values)
        out[indexed[0][0]] = NORM_NEUTRAL
        return out

    # Best first, so rank 1 is always the runner we like most on this input.
    indexed.sort(key=lambda pair: pair[1], reverse=not lower_is_better)

    out = [None] * len(raw_values)
    position = 0
    while position < count:
        # Everything sharing this value spans ranks [position+1 .. end].
        end = position
        while end + 1 < count and abs(indexed[end + 1][1] - indexed[position][1]) < 1e-12:
            end += 1
        average_rank = (position + 1 + end + 1) / 2.0
        fraction = (count - average_rank) / (count - 1)     # 1 = best, 0 = worst
        value = NORM_FLOOR + fraction * (NORM_CEILING - NORM_FLOOR)
        for slot in range(position, end + 1):
            out[indexed[slot][0]] = value
        position = end + 1
    return out


def normalise_component(raw_values: Sequence[float | None],
                        lower_is_better: bool,
                        method: str = DEFAULT_NORM_METHOD) -> list[float | None]:
    """Normalise one component across the field onto NORM_FLOOR..NORM_CEILING.

    Returns a list the same length as `raw_values`, with None kept as None so
    the caller can decide how to impute.
    """
    if resolve_norm_method(method) == 'minmax':
        return _minmax(raw_values, lower_is_better)
    return _rank(raw_values, lower_is_better)


def _impute(normalised: Sequence[float | None]) -> tuple[list[float], list[bool]]:
    """Fill gaps with the field mean of the present values.

    Returns (values, availability_flags). With nothing present at all the whole
    component falls back to NORM_NEUTRAL, which makes it inert in the blend:
    every runner gets the same contribution from it.
    """
    present = [v for v in normalised if v is not None]
    fill = sum(present) / len(present) if present else NORM_NEUTRAL
    values = [fill if v is None else v for v in normalised]
    flags = [v is not None for v in normalised]
    return values, flags


# ── Raw component extraction ──────────────────────────────────────────────
def sectional_rank_from_pfai(pfai: dict | None) -> float | None:
    """Average the 600m/400m/200m PFAI sectional ranks into one heatmap rank.

    This is the same 'Avg Rank' column the PFAI heatmap shows on the meeting
    page — the mean of whichever of the three ranks are present. Lower (i.e.
    closer to #1) is better.
    """
    if not isinstance(pfai, dict):
        return None
    ranks = []
    for key in ('last600_rank', 'last400_rank', 'last200_rank'):
        value = to_float(pfai.get(key))
        # Ranks are 1-based and the feeds use 99 as a "no data" sentinel.
        if value is not None and 0 < value < 90:
            ranks.append(value)
    if not ranks:
        return None
    return sum(ranks) / len(ranks)


def extract_best_adjusted_time(notes: str | None) -> dict | None:
    """Pull the best-recent adjusted time out of a Prediction.notes blob.

    analyzer.js writes a line of the form

        └─ 33.77s → 33.02s

    under a "best of last N (z=...)" heading. app.py's extract_sectional_history()
    parses the same thing; this is a trimmed copy so that the scoring maths has
    no dependency on app.py (importing app.py would pull in the whole site).
    """
    if not notes:
        return None
    text = str(notes).replace('\\n', '\n')
    match = re.search(
        r'best of last (\d+) \(z=([-\d.]+)\)\s+└─\s+([\d.]+)s\s*→\s*([\d.]+)s',
        text,
    )
    if match:
        return {
            'from_last': int(match.group(1)),
            'zscore': float(match.group(2)),
            'raw_time': float(match.group(3)),
            'adjusted_time': float(match.group(4)),
        }
    # Fallback: the HISTORY_ADJ array, best (fastest) entry.
    history = re.search(r'HISTORY_ADJ:\s*\[([\d.,\s]+)\]', text)
    if history:
        try:
            times = [float(x.strip()) for x in history.group(1).split(',') if x.strip()]
        except ValueError:
            times = []
        if times:
            return {
                'from_last': len(times),
                'zscore': None,
                'raw_time': None,
                'adjusted_time': min(times),
            }
    return None


def jockey_trainer_score(jockey_ae: Any, trainer_ae: Any) -> float | None:
    """Blend a jockey's and a trainer's actual-to-expected into one figure.

    A/E is wins divided by the wins their prices said they should have had, so
    1.0 is exactly as good as the market thought and anything above is a
    genuine edge. The jockey gets the larger share because the ride is the part
    that changes race to race.

    Either side may be missing; whatever is present is used on its own.
    """
    jockey = to_float(jockey_ae)
    trainer = to_float(trainer_ae)
    # A/E arrives as a ratio. Zero means "no wins recorded", which is real
    # information, but a negative or absurd figure is a broken feed row.
    if jockey is not None and not (0 <= jockey <= 5):
        jockey = None
    if trainer is not None and not (0 <= trainer <= 5):
        trainer = None
    if jockey is None and trainer is None:
        return None
    if jockey is None:
        return trainer
    if trainer is None:
        return jockey
    return jockey * 0.6 + trainer * 0.4


def draw_score(barrier: Any, field_size: int, rail_position: Any = 0) -> float | None:
    """Score a barrier, allowing for where the rail has been placed.

    An inside gate is normally worth having: less ground to cover and first
    call on the fence. That advantage shrinks as the rail is moved out, because
    the rail out means the inside runners are the ones giving away the extra
    metres, and past about seven metres the inside stops being an advantage at
    all.

    Returns roughly 0..1, higher is better, so it blends like every other
    higher-is-better input.
    """
    gate = to_float(barrier)
    if gate is None or gate <= 0:
        return None
    size = max(int(field_size or 0), 1)
    if size < 2:
        return 0.5

    # 1 at the rail, 0 at the widest gate in this field.
    inside = 1.0 - (min(gate, size) - 1) / (size - 1)

    rail = to_float(rail_position) or 0.0
    # 1.0 with the rail true, falling to 0 by seven metres out and going
    # slightly negative beyond that, where a wide gate is the one you want.
    advantage = max(-0.25, 1.0 - rail / 7.0)

    return 0.5 + (inside - 0.5) * advantage


def race_pace_profile(pace_categories: Sequence[str], pace_bias: Any = 0) -> dict:
    """Read the tempo of a race off its speed map.

    Counting how many runners genuinely want the front is the whole of it. One
    leader gets a soft, uncontested lead and is very hard to run down. Four
    leaders is a speed battle: they take each other on, the tempo collapses in
    the straight and the race falls to whatever is finishing off.

    Returns `pressure` on 0..1 (0 = soft lead, 1 = full speed duel), the raw
    counts behind it, and the meeting's own pace bias carried through.
    """
    counts = {category: 0 for category in PACE_CATEGORIES}
    for category in pace_categories:
        if category in counts:
            counts[category] += 1

    # On-pace runners contribute, but only half: they will sit off the speed
    # rather than fight for it.
    contenders = counts['leader'] + counts['onpace'] * 0.5
    span = PACE_PRESSURE_HOT - PACE_PRESSURE_SOFT
    pressure = (contenders - PACE_PRESSURE_SOFT) / span if span > 1e-9 else 0.0
    pressure = min(1.0, max(0.0, pressure))

    bias = to_float(pace_bias) or 0.0
    bias = max(-2.0, min(2.0, bias))

    if pressure >= 0.66:
        shape = 'hot'
        shape_label = 'Speed duel'
    elif pressure <= 0.33:
        shape = 'soft'
        shape_label = 'Soft lead'
    else:
        shape = 'even'
        shape_label = 'Even tempo'

    return {
        'pressure': round(pressure, 4),
        'counts': counts,
        'contenders': round(contenders, 2),
        'pace_bias': bias,
        'shape': shape,
        'shape_label': shape_label,
    }


def pace_fit_score(pace_category: str, profile: dict) -> float:
    """How well one runner's pace role suits this particular race.

    Positive is a runner the tempo helps, negative is one it hurts. Two things
    go into it: the pace pressure read off the speed map, and the meeting's own
    pace bias where somebody has set one.

    A leader is scored +1 in a soft race and -1 in a speed duel; a backmarker
    is the exact reverse. That is the mechanism that lets the map decide races
    rather than just decorate the middle of them.
    """
    swing = PACE_ROLE_SWING.get(pace_category, 0.0)
    pressure = to_float(profile.get('pressure')) or 0.0
    # pressure 0 (soft) -> -1, pressure 1 (hot) -> +1
    tempo = pressure * 2.0 - 1.0
    from_tempo = swing * tempo

    # Meeting bias: +2 means leaders have been winning all day.
    bias = (to_float(profile.get('pace_bias')) or 0.0) / 2.0
    # A leader gains from a positive bias, a backmarker loses. Reuse the same
    # role swing, negated, because swing is written from the tempo's point of
    # view (leaders dislike pressure) and bias runs the other way.
    from_bias = -swing * bias * PACE_BIAS_WEIGHT

    return max(-1.5, min(1.5, from_tempo + from_bias))


# ── The blend ─────────────────────────────────────────────────────────────
def build_composite_scores(runners: list[dict],
                           weights: dict[str, float] | None = None,
                           norm_method: str = DEFAULT_NORM_METHOD) -> list[dict]:
    """Blend the raw components into one 0-100 composite per runner.

    `runners` is a list of dicts, each carrying whatever raw values could be
    found for that horse (any of them may be None):

        map_value              float  speed map mapA2E, higher better
        sectional_rank         float  mean PFAI 600/400/200 rank, lower better
        adjusted_time          float  best recent adjusted seconds, lower better
        assessment_score       float  ML score, or the PFAI blend score
        jockey_trainer_ae      float  blended jockey/trainer A/E, higher better
        draw_value             float  barrier quality after the rail, higher better
        pace_fit_value         float  tempo suitability, higher better
        market_probability     float  fair win probability from the price

    `weights` is an optional custom split (fractions summing to 1.0, as
    resolve_weights() returns). Left out, the published WEIGHTS are used. The
    normalisation above it does not depend on the weights at all — it is purely
    a within-field ranking of each raw input — so reweighting only ever changes
    how those normalised values are blended, never the values themselves.

    Each runner dict is mutated in place with `components` (per-component raw /
    normalised / weighted / availability), `composite_score` and `rank`, and the
    same list is returned sorted by rank. Rank 1 is the predicted winner, and
    that ordering is what the animation's finish order is locked to.
    """
    if not runners:
        return []

    blend = dict(WEIGHTS) if weights is None else weights
    method = resolve_norm_method(norm_method)

    raw_by_component = {
        'speed_map': [to_float(r.get('map_value')) for r in runners],
        'sectional': [to_float(r.get('sectional_rank')) for r in runners],
        'adjusted_time': [to_float(r.get('adjusted_time')) for r in runners],
        'assessment': [to_float(r.get('assessment_score')) for r in runners],
        'jockey_trainer': [to_float(r.get('jockey_trainer_ae')) for r in runners],
        'draw': [to_float(r.get('draw_value')) for r in runners],
        'pace_fit': [to_float(r.get('pace_fit_value')) for r in runners],
        'market': [to_float(r.get('market_probability')) for r in runners],
    }

    normalised_by_component: dict[str, list[float]] = {}
    available_by_component: dict[str, list[bool]] = {}
    for key, raw_values in raw_by_component.items():
        scaled = normalise_component(raw_values, COMPONENT_LOWER_IS_BETTER[key], method)
        values, flags = _impute(scaled)
        normalised_by_component[key] = values
        available_by_component[key] = flags

    for index, runner in enumerate(runners):
        components = {}
        composite = 0.0
        for key in COMPONENT_KEYS:
            weight = blend.get(key, 0.0)
            # Round BEFORE blending, not after. The page is handed the rounded
            # value and re-blends the composite from it when a slider moves, so
            # blending the full-precision number here would make the server's
            # composite unreproducible from its own payload — a divergence of a
            # few hundredths, which is invisible in the table and very visible
            # once it has been through exp() into a fair price. It also makes
            # the table honest: the number shown times the weight shown really
            # is the contribution shown.
            normalised = round_half_up(normalised_by_component[key][index], 2)
            weighted = normalised * weight
            composite += weighted
            components[key] = {
                'label': COMPONENT_LABELS[key],
                'short_label': COMPONENT_SHORT_LABELS[key],
                'weight': weight,
                'weight_pct': round_half_up(weight * 100, 1),
                'raw': raw_by_component[key][index],
                'normalised': normalised,
                'weighted': round_half_up(weighted, 2),
                'available': available_by_component[key][index],
                'lower_is_better': COMPONENT_LOWER_IS_BETTER[key],
                'decimals': COMPONENT_DECIMALS[key],
            }
        runner['components'] = components
        runner['composite_score'] = round_half_up(composite, 2)
        # Only count a component against data quality when it is actually
        # carrying weight — an unused input being absent costs the reader
        # nothing and should not be flagged as a hole in the form.
        runner['components_available'] = sum(
            1 for key, c in components.items() if c['available'] and blend.get(key, 0.0) > 0
        )
        runner['components_weighted'] = sum(1 for key in COMPONENT_KEYS if blend.get(key, 0.0) > 0)

    # Rank: composite desc. Ties broken by the heaviest single input we trust
    # (the race assessment), then the speed map, then barrier — so the order is
    # deterministic and never depends on dict/query iteration order.
    ordered = sorted(
        runners,
        key=lambda r: (
            -r['composite_score'],
            -(to_float(r.get('assessment_score')) or 0.0),
            -(to_float(r.get('map_value')) or 0.0),
            to_float(r.get('barrier')) or 99.0,
            str(r.get('horse_name') or ''),
        ),
    )
    for position, runner in enumerate(ordered, start=1):
        runner['rank'] = position
    return ordered


def finish_margins(ordered_runners: list[dict],
                   min_margin: float = MARGIN_MIN,
                   max_margin: float = MARGIN_MAX,
                   max_total: float = MARGIN_TOTAL) -> list[float]:
    """Convert composite-score gaps into cumulative beaten margins, in lengths.

    The winner is 0. Every runner after that is the previous runner's margin
    plus a gap proportional to the drop in composite score, so a runner who
    scores a long way clear finishes a long way clear. Clamped at both ends so
    a dead-heat-tight field still separates visibly and a runaway score gap does
    not push the tail of the field off the screen.

    `max_total` then caps the whole field: a 24-runner race accumulating even
    the minimum gap would otherwise stretch the tail so far back that it is
    still on the turn as the winner hits the post. Once the last runner would be
    beaten further than this, every margin is scaled down to fit, which
    preserves the relative gaps and only compresses the picture.
    """
    if not ordered_runners:
        return []

    scores = [r.get('composite_score', 0.0) for r in ordered_runners]
    drops = [max(0.0, scores[i] - scores[i + 1]) for i in range(len(scores) - 1)]
    biggest_drop = max(drops) if drops else 0.0

    margins = [0.0]
    for drop in drops:
        share = (drop / biggest_drop) if biggest_drop > 1e-9 else 0.0
        margins.append(margins[-1] + min_margin + share * (max_margin - min_margin))

    if margins[-1] > max_total:
        scale = max_total / margins[-1]
        margins = [m * scale for m in margins]
    return margins


# ── Probability, price and value ──────────────────────────────────────────
def win_probabilities(ordered_runners: list[dict],
                      temperature: float = PROBABILITY_TEMPERATURE) -> list[float]:
    """Composite scores -> win probabilities that sum to 1.

    Each runner is given a Plackett-Luce strength of exp(composite / tau) and
    the probabilities are those strengths as a share of the field. That is the
    standard way to turn a rating into a price, and it has the property that
    matters here: a runner's chance depends on who else is in the race, so the
    same score in a strong field is worth less than in a weak one.

    `temperature` sets how opinionated the model is. Low tau spreads the field
    out and makes short favourites; high tau flattens it towards every runner
    having an equal chance.
    """
    if not ordered_runners:
        return []
    tau = max(1e-6, float(temperature))
    scores = [to_float(r.get('composite_score')) or 0.0 for r in ordered_runners]
    # Subtract the best score before exponentiating: mathematically identical
    # once normalised, but it keeps exp() away from overflow.
    best = max(scores)
    strengths = [math.exp((score - best) / tau) for score in scores]
    total = sum(strengths)
    if total <= 1e-12:
        share = 1.0 / len(ordered_runners)
        return [share] * len(ordered_runners)
    return [strength / total for strength in strengths]


def fair_odds(probability: Any) -> float | None:
    """Probability -> decimal price. None for anything that cannot be priced."""
    value = to_float(probability)
    if value is None or value <= 1e-9:
        return None
    return 1.0 / value


def _overround_free_market(prices: Sequence[float | None]) -> list[float | None]:
    """Prices -> probabilities with the bookmaker's margin taken back out.

    This is the plain proportional strip, which is enough when all we want is
    a like-for-like comparison against the model. The site's Shin correction in
    market_probability.py is the better tool and the routes hand this off to it
    where it is importable; this is the fallback so the maths module keeps
    standing on its own with no dependencies.
    """
    usable = [to_float(p) for p in prices]
    raw = [(1.0 / p) if (p is not None and p > 1.0) else None for p in usable]
    total = sum(v for v in raw if v is not None)
    if total <= 1e-9:
        return [None] * len(prices)
    return [None if v is None else v / total for v in raw]


def value_edge(model_probability: Any,
               price: Any,
               kelly_fraction: float = KELLY_FRACTION,
               market_probability: Any = None) -> dict:
    """Model probability against a real price: is this a bet, and how big?

    `price` is the decimal price on offer. `market_probability` is that price
    with the overround already removed where the caller has done it properly
    (Shin); left out, the raw implied probability is used for the comparison,
    which understates the edge slightly and is therefore the safe direction to
    be wrong in.

    Kelly is the classic f = (bp - q) / b, shown at a fraction of full because
    full Kelly on an unproven model is how bankrolls die.
    """
    probability = to_float(model_probability)
    decimal_price = to_float(price)
    out = {
        'price': decimal_price,
        'model_probability': probability,
        'market_probability': to_float(market_probability),
        'edge': None,
        'edge_pct': None,
        'expected_value': None,
        'kelly_pct': None,
        'is_value': False,
    }
    if probability is None or decimal_price is None or decimal_price <= 1.0:
        return out

    implied = out['market_probability']
    if implied is None:
        implied = 1.0 / decimal_price
        out['market_probability'] = implied

    out['edge'] = probability - implied
    out['edge_pct'] = round_half_up((probability - implied) * 100, 2)
    # Expected return per dollar staked at this price.
    out['expected_value'] = round_half_up(probability * decimal_price - 1.0, 4)

    b = decimal_price - 1.0
    kelly = (b * probability - (1.0 - probability)) / b if b > 1e-9 else 0.0
    kelly = max(0.0, kelly) * max(0.0, kelly_fraction)
    out['kelly_pct'] = round_half_up(kelly * 100, 2)
    out['is_value'] = bool(out['edge'] is not None
                           and out['edge'] >= MIN_VALUE_EDGE
                           and out['expected_value'] > 0)
    return out


def attach_market(ordered_runners: list[dict],
                  market_probabilities: Sequence[float | None] | None = None,
                  kelly_fraction: float = KELLY_FRACTION) -> None:
    """Hang win probability, fair price, edge and stake off each runner.

    `market_probabilities` is the field's prices with the overround already
    removed, in the same order as `ordered_runners`. Where it is not supplied,
    each runner's own price is stripped proportionally instead.
    """
    if not ordered_runners:
        return

    probabilities = win_probabilities(ordered_runners)
    if market_probabilities is None:
        market_probabilities = _overround_free_market(
            [r.get('price') for r in ordered_runners])

    for runner, probability, market in zip(ordered_runners, probabilities, market_probabilities):
        runner['win_probability'] = round_half_up(probability, 6)
        runner['win_probability_pct'] = round_half_up(probability * 100, 1)
        price = fair_odds(probability)
        runner['fair_odds'] = round_half_up(price, 2) if price else None
        runner['value'] = value_edge(probability, runner.get('price'),
                                     kelly_fraction, market)


def simulate_race(ordered_runners: list[dict],
                  runs: int = 2000,
                  seed: int = 20260902) -> dict:
    """Run the race `runs` times and report what actually happened.

    A single predicted order overstates what any model knows. Sampling from the
    win probabilities instead gives the honest version: this horse wins 34% of
    the time, is top three 68% of the time, and its average finish is 3.4.

    Sampling is Plackett-Luce — draw the winner in proportion to strength, take
    it out, draw again for second, and so on — which is the ordering model the
    probabilities came from, so the simulated win rate matches the probability
    it was built from rather than drifting away from it.

    `seed` is fixed so the same race always reports the same numbers. A figure
    that moved every refresh would be unusable for comparing weightings.
    """
    size = len(ordered_runners)
    if not size:
        return {'runs': 0, 'summary': []}
    if size == 1:
        return {
            'runs': runs,
            'summary': [{
                'horse_id': ordered_runners[0].get('horse_id'),
                'win_pct': 100.0, 'top3_pct': 100.0,
                'mean_finish': 1.0, 'best_finish': 1, 'worst_finish': 1,
            }],
        }

    probabilities = win_probabilities(ordered_runners)
    wins = [0] * size
    top3 = [0] * size
    finish_total = [0] * size
    best = [size] * size
    worst = [1] * size

    rng = random.Random(seed)
    runs = max(1, int(runs))

    for _ in range(runs):
        remaining = list(range(size))
        strengths = list(probabilities)
        pool = sum(strengths)
        for place in range(1, size + 1):
            if len(remaining) == 1:
                chosen_slot = 0
            else:
                target = rng.random() * pool
                running = 0.0
                chosen_slot = len(remaining) - 1
                for slot, index in enumerate(remaining):
                    running += strengths[index]
                    if running >= target:
                        chosen_slot = slot
                        break
            index = remaining.pop(chosen_slot)
            pool -= strengths[index]
            if place == 1:
                wins[index] += 1
            if place <= 3:
                top3[index] += 1
            finish_total[index] += place
            best[index] = min(best[index], place)
            worst[index] = max(worst[index], place)

    summary = []
    for index, runner in enumerate(ordered_runners):
        summary.append({
            'horse_id': runner.get('horse_id'),
            'horse_name': runner.get('horse_name'),
            'win_pct': round(wins[index] * 100.0 / runs, 1),
            'top3_pct': round(top3[index] * 100.0 / runs, 1),
            'mean_finish': round(finish_total[index] / runs, 2),
            'best_finish': best[index],
            'worst_finish': worst[index],
        })
    return {'runs': runs, 'summary': summary}


def sample_finish_order(ordered_runners: list[dict], seed: int) -> list[int]:
    """One Plackett-Luce draw: the finishing order of a single running.

    Returns a list of indexes into `ordered_runners`, winner first. This is
    what the animation plays when it is asked for a sampled race rather than
    the model's expected one, so what is on screen is a race that could
    genuinely happen instead of a foregone conclusion.
    """
    size = len(ordered_runners)
    if size <= 1:
        return list(range(size))

    probabilities = win_probabilities(ordered_runners)
    rng = random.Random(seed)
    remaining = list(range(size))
    pool = sum(probabilities)
    order = []
    while remaining:
        if len(remaining) == 1:
            order.append(remaining.pop(0))
            break
        target = rng.random() * pool
        running = 0.0
        chosen_slot = len(remaining) - 1
        for slot, index in enumerate(remaining):
            running += probabilities[index]
            if running >= target:
                chosen_slot = slot
                break
        index = remaining.pop(chosen_slot)
        pool -= probabilities[index]
        order.append(index)
    return order



# ── Track shape ───────────────────────────────────────────────────────────
# A nominal circuit, in metres, for turning a race distance into a share of the
# drawn oval. Australian tracks run from about 1,600m (Moonee Valley) to over
# 2,400m (Flemington), so this is a middle figure rather than any one track.
# Races longer than a circuit are drawn as their final lap, which is what race
# vision shows anyway.
NOMINAL_CIRCUIT_M = 1800

# How long a race takes to play, in seconds, before the speed control. Scaled
# with the distance so a sprint is over quickly and a staying race is a grind —
# a fixed fifteen seconds made a 1000m dash and the Melbourne Cup look exactly
# alike.
BASE_DURATION_SECONDS = 11.0
DURATION_PER_1000M = 5.0
MAX_DURATION_SECONDS = 26.0

# Australian racing runs clockwise in New South Wales and Queensland, and
# anti-clockwise everywhere else. This is a list of the tracks we see rather
# than a complete register: anything not named here falls back to
# anti-clockwise, which is the way most of the country races. Add to it freely —
# a wrong entry only ever mirrors the picture, never the result.
CLOCKWISE_TRACKS = {
    # NSW metropolitan and provincial
    'randwick', 'royal randwick', 'kensington', 'rosehill', 'rosehill gardens',
    'warwick farm', 'canterbury', 'canterbury park', 'kembla grange', 'kembla',
    'newcastle', 'broadmeadow', 'gosford', 'wyong', 'hawkesbury', 'scone',
    'muswellbrook', 'tamworth', 'dubbo', 'wagga', 'wagga wagga', 'albury',
    'goulburn', 'nowra', 'coffs harbour', 'grafton', 'port macquarie', 'taree',
    'bathurst', 'orange', 'mudgee', 'queanbeyan', 'wellington', 'cowra',
    'armidale', 'inverell', 'moree', 'narromine', 'parkes', 'forbes',
    'gunnedah', 'tuncurry', 'ballina', 'lismore', 'casino', 'murwillumbah',
    # QLD
    'eagle farm', 'doomben', 'sunshine coast', 'corbould park', 'gold coast',
    'ipswich', 'toowoomba', 'rockhampton', 'townsville', 'cairns', 'mackay',
    'bundaberg', 'gympie', 'warwick', 'dalby', 'roma', 'emerald',
    'beaudesert', 'kilcoy', 'esk',
}


def track_direction(track_name: Any) -> str:
    """'clockwise' or 'anticlockwise' for a track name.

    Anything not on the list runs anti-clockwise, which is the way most of the
    country races. Getting this wrong only mirrors the picture — it cannot move
    a horse in the result — so an unknown track is safe to guess on.
    """
    key = normalise_name(track_name)
    if not key:
        return 'anticlockwise'
    if key in CLOCKWISE_TRACKS:
        return 'clockwise'
    # Meeting names arrive as "260902_Randwick" and similar, so try the parts.
    parts = key.split()
    for size in (2, 1):
        for start in range(len(parts) - size + 1):
            if ' '.join(parts[start:start + size]) in CLOCKWISE_TRACKS:
                return 'clockwise'
    return 'anticlockwise'


def parse_distance_metres(distance: Any) -> int | None:
    """'1200m', '1200', 1200 -> 1200. None when there is nothing usable."""
    value = to_float(distance)
    if value is None or value <= 0:
        return None
    # Sanity: racing distances live between about 800m and 5000m. Anything
    # outside that is a parse accident, not a race.
    if not (400 <= value <= 6000):
        return None
    return int(round(value))


def race_shape(distance_m: Any) -> dict:
    """How much of the drawn oval this race covers, and how long it plays for.

    A race shorter than a circuit starts partway round and runs to the post. A
    race longer than a circuit is drawn as its final lap, with the full trip
    reported so the page can say so.
    """
    distance_m = parse_distance_metres(distance_m)
    if not distance_m:
        return {
            'distance_m': None,
            'lap_fraction': 0.75,          # the original three-quarter lap
            'laps': None,
            'duration_seconds': BASE_DURATION_SECONDS + DURATION_PER_1000M * 1.4,
            'shown_from_m': None,
        }

    laps = distance_m / float(NOMINAL_CIRCUIT_M)
    lap_fraction = min(1.0, laps)
    duration = BASE_DURATION_SECONDS + DURATION_PER_1000M * (distance_m / 1000.0)
    return {
        'distance_m': distance_m,
        'lap_fraction': round(lap_fraction, 4),
        'laps': round(laps, 3),
        'duration_seconds': round(min(MAX_DURATION_SECONDS, duration), 2),
        'shown_from_m': int(round(lap_fraction * NOMINAL_CIRCUIT_M)),
    }


# ── Configuration handed to the browser ───────────────────────────────────
def scoring_constants() -> dict:
    """Everything the page's copy of this arithmetic needs, as plain JSON.

    The browser re-blends the composite locally so the sliders respond without
    a round trip. That copy used to carry its own hardcoded weights and margin
    constants, which meant a change here silently made the page disagree with
    the server. Now it reads them from this, and a parity test runs both
    implementations over the same fixture to prove they still agree.
    """
    return {
        'component_keys': list(COMPONENT_KEYS),
        'core_component_keys': list(CORE_COMPONENT_KEYS),
        'labels': dict(COMPONENT_LABELS),
        'short_labels': dict(COMPONENT_SHORT_LABELS),
        'decimals': dict(COMPONENT_DECIMALS),
        'lower_is_better': dict(COMPONENT_LOWER_IS_BETTER),
        'default_weights': weights_as_percentages(WEIGHTS),
        'norm_methods': list(NORM_METHODS),
        'default_norm_method': DEFAULT_NORM_METHOD,
        'norm_floor': NORM_FLOOR,
        'norm_ceiling': NORM_CEILING,
        'norm_neutral': NORM_NEUTRAL,
        'margin_min': MARGIN_MIN,
        'margin_max': MARGIN_MAX,
        'margin_total': MARGIN_TOTAL,
        'probability_temperature': PROBABILITY_TEMPERATURE,
        'kelly_fraction': KELLY_FRACTION,
        'min_value_edge': MIN_VALUE_EDGE,
        'pace_labels': dict(PACE_LABELS),
        'pace_categories': list(PACE_CATEGORIES),
    }
