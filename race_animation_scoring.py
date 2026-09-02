"""
race_animation_scoring.py — composite prediction score for the Race Animations page.

Pure Python. No Flask, no SQLAlchemy, no network. Everything in here takes plain
dicts/lists so the maths can be unit tested on its own, and so nothing that the
rest of the site depends on is imported (and therefore nothing existing can be
disturbed by this module).

The composite is a weighted blend of four inputs that are already computed
elsewhere in the system:

    Speed Map (MAP / mapA2E)                 50%   higher is better
    PFAI sectional heatmap rank              10%   LOWER is better  -> inverted
    Adjusted times (best recent, seconds)    10%   LOWER is better  -> inverted
    Overall race assessment (ML / PFAI)      30%   higher is better

Each input is normalised to 0-100 *within the race field* before weighting,
because ranks and times are only comparable against the other runners in the
same race. Normalisation is min-max onto a 5-95 band (see NORM_FLOOR/CEILING):
using the full 0-100 range would hand the field's best runner a perfect 100 and
its worst a flat 0 on every single component, which overstates small gaps in a
tight race.

Missing components are imputed with the field mean of that component rather than
being scored 0, so a runner with (say) no sectional data is not punished for it.
Every component carries an `available` flag so the page can show what was real
and what was filled in.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

# ── Weights (must sum to 1.0) ─────────────────────────────────────────────
WEIGHTS = {
    'speed_map': 0.50,
    'sectional': 0.10,
    'adjusted_time': 0.10,
    'assessment': 0.30,
}

COMPONENT_LABELS = {
    'speed_map': 'Speed Map (MAP)',
    'sectional': 'PFAI Sectional Rank',
    'adjusted_time': 'Adjusted Time',
    'assessment': 'Race Assessment',
}

# Direction of each raw input: True when a LOWER raw value is the better one.
COMPONENT_LOWER_IS_BETTER = {
    'speed_map': False,
    'sectional': True,
    'adjusted_time': True,
    'assessment': False,
}

# Normalised values are squeezed into this band instead of the full 0-100.
NORM_FLOOR = 5.0
NORM_CEILING = 95.0
NORM_NEUTRAL = 50.0

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
def normalise_component(raw_values: Sequence[float | None], lower_is_better: bool) -> list[float | None]:
    """Min-max a component across the field onto the NORM_FLOOR..NORM_CEILING band.

    Returns a list the same length as `raw_values`, with None kept as None so
    the caller can decide how to impute. A field where every runner shares the
    same raw value (or where only one runner has data) is degenerate — there is
    no ordering information in it, so everyone gets NORM_NEUTRAL.
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
        fraction = (value - low) / span          # 0 at worst-raw, 1 at highest-raw
        if lower_is_better:
            fraction = 1.0 - fraction
        out.append(NORM_FLOOR + fraction * (NORM_CEILING - NORM_FLOOR))
    return out


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


# ── The blend ─────────────────────────────────────────────────────────────
def build_composite_scores(runners: list[dict]) -> list[dict]:
    """Blend the four raw components into one 0-100 composite per runner.

    `runners` is a list of dicts, each carrying whatever raw values could be
    found for that horse (any of them may be None):

        map_value              float  speed map mapA2E, higher better
        sectional_rank         float  mean PFAI 600/400/200 rank, lower better
        adjusted_time          float  best recent adjusted seconds, lower better
        assessment_score       float  ML score, or the PFAI blend score

    Each runner dict is mutated in place with `components` (per-component raw /
    normalised / weighted / availability), `composite_score` and `rank`, and the
    same list is returned sorted by rank. Rank 1 is the predicted winner, and
    that ordering is what the animation's finish order is locked to.
    """
    if not runners:
        return []

    raw_by_component = {
        'speed_map': [to_float(r.get('map_value')) for r in runners],
        'sectional': [to_float(r.get('sectional_rank')) for r in runners],
        'adjusted_time': [to_float(r.get('adjusted_time')) for r in runners],
        'assessment': [to_float(r.get('assessment_score')) for r in runners],
    }

    normalised_by_component: dict[str, list[float]] = {}
    available_by_component: dict[str, list[bool]] = {}
    for key, raw_values in raw_by_component.items():
        scaled = normalise_component(raw_values, COMPONENT_LOWER_IS_BETTER[key])
        values, flags = _impute(scaled)
        normalised_by_component[key] = values
        available_by_component[key] = flags

    for index, runner in enumerate(runners):
        components = {}
        composite = 0.0
        for key, weight in WEIGHTS.items():
            normalised = normalised_by_component[key][index]
            weighted = normalised * weight
            composite += weighted
            components[key] = {
                'label': COMPONENT_LABELS[key],
                'weight': weight,
                'weight_pct': round(weight * 100),
                'raw': raw_by_component[key][index],
                'normalised': round(normalised, 2),
                'weighted': round(weighted, 2),
                'available': available_by_component[key][index],
                'lower_is_better': COMPONENT_LOWER_IS_BETTER[key],
            }
        runner['components'] = components
        runner['composite_score'] = round(composite, 2)
        runner['components_available'] = sum(1 for c in components.values() if c['available'])

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
                   min_margin: float = 0.4,
                   max_margin: float = 3.2,
                   max_total: float = 26.0) -> list[float]:
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
