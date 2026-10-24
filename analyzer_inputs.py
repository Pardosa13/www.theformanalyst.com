"""The analyzer's own view of a runner, as ML inputs.

Until Oct 2026 the ML never saw the analyzer: not its score, not the
components in its notes (the same components the Data page's component
analysis ranks). These inputs give it that view so the nightly ablation
(group 'analyzer') can judge whether it helps:

- an_score / an_raw_score: the blended score and the analyzer's own score
- an_score_race_rank / an_score_vs_race_avg: where that score sits in the race
- an_points_pos / an_points_neg: total plus and minus points in the notes
- an_n_negative: how many known negative components fired
- an_cat_<group>: how many components of each family fired (jockey,
  trainer, track, distance, ...)

Signal Agreement is left out: it can include the ML's own pick.

Training (backtest.add_race_relative_features) and live scoring
(ml_predict.add_race_relative_features) both call add_analyzer_features on
the same hidden columns (_an_score, _an_notes), so the values are identical.
Rows without them get blanks, never zeros.
"""
import re

import numpy as np
import pandas as pd

from notes_parsing import NEGATIVE_COMPONENTS, parse_analyzer_score, parse_notes_components

SCORE_KEY = '_an_score'
NOTES_KEY = '_an_notes'

# Component families, by name prefix (notes_parsing.SCORING_PREFIXES, grouped).
CATEGORIES = [
    ('jockey', ('Jockey',)),
    ('trainer', ('Trainer',)),
    ('track', ('Track Win Rate', 'Track Podium', 'Track Score', 'Track -')),
    ('track_distance', ('Track+Distance',)),
    ('distance', ('Distance Win', 'Distance Podium', 'Distance Change', 'Distance -', 'Distance Score')),
    ('condition', ('Condition', 'Track Condition Score')),
    ('class', ('Class Drop', 'Class Rise')),
    ('last_start', ('Last Start',)),
    ('days_since', ('Days Since Run',)),
    ('form_price', ('Form Price',)),
    ('first_second_up', ('First Up', 'Second Up')),
    ('weight', ('Weight vs Field', 'Weight Change')),
    ('career', ('Career Win Rate',)),
    ('age_sex', ('Age/Sex', 'Colt')),
    ('sire', ('Sire',)),
    ('sectional', ('Specialist', 'Sectional History', 'Sectional Consistency', 'API Sectional')),
    ('running_position', ('Running Position', 'Pace Angle')),
    ('market_expectation', ('Market Expectation',)),
    ('hidden_edge', ('Hidden Edge',)),
    ('interstate', ('Interstate State Move',)),
    ('ran_places', ('Ran Places',)),
]

ANALYZER_FEATURES = [
    'an_score', 'an_raw_score', 'an_score_race_rank', 'an_score_vs_race_avg',
    'an_points_pos', 'an_points_neg', 'an_n_negative',
] + [f'an_cat_{name}' for name, _ in CATEGORIES]

_POINTS_RE = re.compile(r'(?m)^\s*([+\-−])\s*(\d+(?:\.\d+)?)\s*:')


def _num(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return np.nan
    return out if np.isfinite(out) else np.nan


def runner_features(score, notes):
    """The per-runner analyzer inputs (no race-relative ones)."""
    out = {name: np.nan for name in ANALYZER_FEATURES}
    notes = notes if isinstance(notes, str) else ''
    if not notes.strip() or notes.strip() == 'Scratched':
        out['an_score'] = _num(score) if notes.strip() != 'Scratched' else np.nan
        return out
    out['an_score'] = _num(score)
    out['an_raw_score'] = _num(parse_analyzer_score(notes))
    pos = neg = 0.0
    lines = '\n'.join(l for l in notes.splitlines() if 'SIGNALS AGREE' not in l.upper())
    for sign, value in _POINTS_RE.findall(lines):
        if sign == '+':
            pos += float(value)
        else:
            neg += float(value)
    out['an_points_pos'] = pos
    out['an_points_neg'] = neg
    names = [n for n in parse_notes_components(notes) if not n.startswith('Signal Agreement')]
    out['an_n_negative'] = float(sum(1 for n in names if n in NEGATIVE_COMPONENTS))
    for cat, prefixes in CATEGORIES:
        out[f'an_cat_{cat}'] = float(sum(1 for n in names if n.startswith(prefixes)))
    return out


def add_analyzer_features(temp, race_key=None):
    """Append ANALYZER_FEATURES (in order) to `temp` in place and drop the
    hidden _an_* columns. race_key=None means temp is one race (live)."""
    scores = temp[SCORE_KEY] if SCORE_KEY in temp.columns else pd.Series(np.nan, index=temp.index)
    notes = temp[NOTES_KEY] if NOTES_KEY in temp.columns else pd.Series('', index=temp.index)
    rows = [runner_features(s, n) for s, n in zip(scores, notes)]
    feats = pd.DataFrame(rows, index=temp.index, columns=ANALYZER_FEATURES).astype(float)
    if race_key is None:
        grouped = feats['an_score']
        feats['an_score_race_rank'] = grouped.rank(method='min', ascending=False)
        feats['an_score_vs_race_avg'] = grouped - grouped.mean()
    else:
        grouped = feats['an_score'].groupby(temp[race_key])
        feats['an_score_race_rank'] = grouped.rank(method='min', ascending=False)
        feats['an_score_vs_race_avg'] = feats['an_score'] - grouped.transform('mean')
    for col in (SCORE_KEY, NOTES_KEY):
        if col in temp.columns:
            del temp[col]
    for col in ANALYZER_FEATURES:
        temp[col] = feats[col].values
    return temp
