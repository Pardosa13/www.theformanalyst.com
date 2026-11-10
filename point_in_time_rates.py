"""Jockey and trainer form as it stood BEFORE each past race (training only).

PuntingForm only serves today's jockey/trainer numbers, and dated copies have
only been saved since 21 Jul 2026. Training used to fall back to today's
numbers for any older race, so a March race was told how its jockey was going
in November: the model saw the future. This module rebuilds those inputs from
our own results, using only runs on strictly earlier dates.

What each input becomes when no dated PuntingForm copy exists:
  jockey_sr / trainer_sr      win % over the last 100 runs in our results
                              before the race date (-1 with fewer than 10)
  *_l100_a2e                  wins / wins the market expected, same 100 runs
  *_career_runs, *_career_a2e today's career totals with every run we hold
                              on or after the race date taken back out

Live scoring is untouched: at race time today's numbers are the right ones.
"""
from bisect import bisect_left
from collections import defaultdict

import numpy as np

from strike_rate_matching import _coerce_date

LAST_N = 100
MIN_RUNS = 10             # same floor get_sr_win_pct uses before trusting a rate
MIN_EXPECTED_WINS = 1.0   # an A2E ratio over less than one expected win is noise
# A career A2E is only rolled back while most of that career still sits before
# the race; past that, today's totals say little about the horse's day.
MIN_CAREER_SHARE_LEFT = 0.25


def race_market_probabilities(race_ids, sp_values):
    """Each runner's share of its race's market (1/SP, summing to 1 per race)."""
    raw = np.array([1.0 / float(sp) if sp and float(sp) > 1.0 else 0.0 for sp in sp_values], dtype=float)
    totals = defaultdict(float)
    for race_id, value in zip(race_ids, raw):
        totals[race_id] += value
    return [value / totals[race_id] if totals[race_id] > 0 else 0.0
            for race_id, value in zip(race_ids, raw)]


class RunHistory:
    """Every run of each jockey (or trainer), oldest first, for dated lookups."""

    def __init__(self, names, dates, won, expected):
        runs = defaultdict(list)
        for name, date, w, e in zip(names, dates, won, expected):
            date = _coerce_date(date)
            if name and date is not None:
                runs[name].append((date, float(w), float(e)))
        self._dates, self._wins, self._expected = {}, {}, {}
        for name, items in runs.items():
            items.sort(key=lambda item: item[0])
            self._dates[name] = [d for d, _, _ in items]
            # Running totals with a leading 0, so any slice is one subtraction.
            self._wins[name] = np.concatenate(([0.0], np.cumsum([w for _, w, _ in items])))
            self._expected[name] = np.concatenate(([0.0], np.cumsum([e for _, _, e in items])))

    def _split(self, name, date):
        date = _coerce_date(date)
        dates = self._dates.get(name)
        if not dates or date is None:
            return None
        return bisect_left(dates, date)   # runs strictly before `date`

    def before(self, name, date, last=None):
        """(runs, wins, expected wins) on dates strictly before `date`."""
        cut = self._split(name, date)
        if cut is None:
            return 0, 0.0, 0.0
        start = max(0, cut - last) if last else 0
        return (cut - start, float(self._wins[name][cut] - self._wins[name][start]),
                float(self._expected[name][cut] - self._expected[name][start]))

    def since(self, name, date):
        """(runs, wins, expected wins) on `date` or later."""
        cut = self._split(name, date)
        if cut is None:
            return 0, 0.0, 0.0
        total = len(self._dates[name])
        return (total - cut, float(self._wins[name][total] - self._wins[name][cut]),
                float(self._expected[name][total] - self._expected[name][cut]))


def win_pct_before(history, name, date):
    """Win % over the last 100 earlier runs, or -1.0 (the live 'unknown' value)."""
    runs, wins, _ = history.before(name, date, last=LAST_N)
    return (wins / runs) * 100.0 if runs >= MIN_RUNS else -1.0


def l100_a2e_before(history, name, date):
    """Actual / expected wins over the last 100 earlier runs, or NaN."""
    runs, wins, expected = history.before(name, date, last=LAST_N)
    if runs < MIN_RUNS or expected < MIN_EXPECTED_WINS:
        return np.nan
    return wins / expected


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if np.isfinite(value) else None


def career_before(history, name, date, current):
    """(career_a2e, career_runs) as they stood before `date`.

    `current` holds today's PuntingForm career totals. Runs we hold on or
    after `date` are taken back out. Where that leaves too little of the
    career to trust, the A2E comes from our own earlier runs instead.
    """
    if _coerce_date(date) is None:
        return np.nan, np.nan   # no race date: nothing can be dated, so say unknown
    current = current or {}
    since_runs, since_wins, since_expected = history.since(name, date)
    before_runs, before_wins, before_expected = history.before(name, date)

    career_runs = np.nan
    total_runs = _number(current.get('career_runs'))
    if total_runs:
        left = max(total_runs - since_runs, float(before_runs))
        career_runs = left if left > 0 else np.nan

    career_a2e = np.nan
    total_wins = _number(current.get('career_wins'))
    total_expected = _number(current.get('career_expected_wins'))
    if total_wins is not None and total_expected and total_expected > 0:
        left_expected = total_expected - since_expected
        if left_expected >= max(MIN_EXPECTED_WINS, MIN_CAREER_SHARE_LEFT * total_expected):
            career_a2e = max(total_wins - since_wins, 0.0) / left_expected
    if not np.isfinite(career_a2e) and before_runs >= MIN_RUNS and before_expected >= MIN_EXPECTED_WINS:
        career_a2e = before_wins / before_expected
    return career_a2e, career_runs


def fill_point_in_time_rates(feature_rows, names, dates, won, market_probs, prefix,
                             used_dated_snapshot, current_extras):
    """Overwrite one entity type's leaky inputs on every training row, in place.

    prefix: 'jockey' or 'trainer'. names: normalised name per row.
    used_dated_snapshot: per row, True when {prefix}_sr already came from a
        dated PuntingForm copy (kept as is).
    current_extras: {normalised name: today's PuntingForm career totals}.
    Returns counts of how each row was filled, for the run log.
    """
    history = RunHistory(names, dates, won, market_probs)
    stats = {'rows': 0, 'sr_dated_snapshot': 0, 'sr_own_results': 0, 'sr_unknown': 0,
             'l100_a2e_filled': 0, 'career_a2e_filled': 0}
    for i, features in enumerate(feature_rows):
        name, date = names[i], dates[i]
        stats['rows'] += 1
        if used_dated_snapshot[i]:
            stats['sr_dated_snapshot'] += 1
        else:
            rate = win_pct_before(history, name, date)
            features[f'{prefix}_sr'] = rate
            stats['sr_own_results' if rate >= 0 else 'sr_unknown'] += 1
        l100 = l100_a2e_before(history, name, date)
        career_a2e, career_runs = career_before(history, name, date, (current_extras or {}).get(name))
        features[f'{prefix}_l100_a2e'] = l100
        features[f'{prefix}_career_a2e'] = career_a2e
        features[f'{prefix}_career_runs'] = career_runs
        stats['l100_a2e_filled'] += int(np.isfinite(l100))
        stats['career_a2e_filled'] += int(np.isfinite(career_a2e))
    return stats
