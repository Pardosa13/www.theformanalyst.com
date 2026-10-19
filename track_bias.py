"""Track bias learned from your own results: barrier and run style, per track.

Answers "does Caulfield suit leaders more than Flemington?" and "is a wide
draw worse here?". For each runner it looks up how runners from the same
barrier group (and, separately, the same run style) have gone at this track
over a similar distance, in races BEFORE this meeting.

Small samples are pulled towards the all-tracks rate for the same barrier
group / run style and distance (shrinkage), so a track with three races of
history doesn't produce wild numbers.

Training (backtest.build_training_set) walks history in date order and only
adds a day's results after that day is finished. Live scoring (ml_predict)
builds the same counts from every result dated before the meeting.
"""
import math
import re

import numpy as np

SHRINK_STARTS = 60.0

TRACK_BIAS_FEATURES = [
    'tb_barrier_win_rate',    # shrunk win rate for this barrier group at this track + distance band
    'tb_barrier_place_rate',
    'tb_barrier_win_edge',    # that rate divided by the all-tracks rate (1.0 = no bias)
    'tb_barrier_starts',      # how much history it is based on
    'tb_style_win_rate',      # same for run style (leader / on pace / midfield / backmarker)
    'tb_style_place_rate',
    'tb_style_win_edge',
    'tb_style_starts',
]


def empty_track_bias_features():
    return {name: np.nan for name in TRACK_BIAS_FEATURES}


def track_key(name):
    # 'Randwick-Kensington' -> 'randwick'; 'Sandown-Hillside' -> 'sandown'
    return re.split(r'[-(]', str(name or '').strip().lower())[0].strip()


def distance_band(distance):
    try:
        d = float(str(distance).lower().replace('m', '').strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(d) or d <= 0:
        return None
    if d <= 1100:
        return 'sprint'
    if d <= 1300:
        return 'short'
    if d <= 1600:
        return 'mile'
    if d <= 2000:
        return 'middle'
    return 'staying'


def barrier_group(barrier):
    try:
        b = int(float(str(barrier).strip()))
    except (TypeError, ValueError):
        return None
    if b <= 0:
        return None
    if b <= 4:
        return str(b)
    if b <= 6:
        return '5-6'
    if b <= 8:
        return '7-8'
    if b <= 11:
        return '9-11'
    return '12+'


def run_style(running_position):
    text_value = str(running_position or '').strip().upper()
    for style in ('LEADER', 'ONPACE', 'MIDFIELD', 'BACKMARKER'):
        if style in text_value:
            return style
    return None


def runner_keys(track, distance, barrier, running_position):
    """(barrier_key, barrier_global_key, style_key, style_global_key); parts may be None."""
    t, band = track_key(track), distance_band(distance)
    b, s = barrier_group(barrier), run_style(running_position)
    if not band:
        return None, None, None, None
    return (
        ('b', t, band, b) if t and b else None,
        ('b', '*', band, b) if b else None,
        ('s', t, band, s) if t and s else None,
        ('s', '*', band, s) if s else None,
    )


class TrackBiasStats:
    """Running starts / wins / places per key."""

    def __init__(self):
        self.counts = {}
        self.total = [0, 0, 0]

    def add(self, track, distance, barrier, running_position, finish_position):
        try:
            finish = int(finish_position)
        except (TypeError, ValueError):
            return
        if finish <= 0:
            return
        self.add_counts(track, distance, barrier, running_position,
                        1, int(finish == 1), int(finish <= 3))

    def add_counts(self, track, distance, barrier, running_position, starts, wins, places):
        starts, wins, places = int(starts or 0), int(wins or 0), int(places or 0)
        if starts <= 0:
            return
        self.total[0] += starts
        self.total[1] += wins
        self.total[2] += places
        for key in runner_keys(track, distance, barrier, running_position):
            if key is None:
                continue
            c = self.counts.setdefault(key, [0, 0, 0])
            c[0] += starts
            c[1] += wins
            c[2] += places

    def _shrunk(self, key, global_key):
        if not self.total[0]:
            return None
        base_win = self.total[1] / self.total[0]
        base_place = self.total[2] / self.total[0]
        g = self.counts.get(global_key) if global_key else None
        prior_win = (g[1] + SHRINK_STARTS * base_win) / (g[0] + SHRINK_STARTS) if g else base_win
        prior_place = (g[2] + SHRINK_STARTS * base_place) / (g[0] + SHRINK_STARTS) if g else base_place
        c = self.counts.get(key) if key else None
        starts = c[0] if c else 0
        win = ((c[1] if c else 0) + SHRINK_STARTS * prior_win) / (starts + SHRINK_STARTS)
        place = ((c[2] if c else 0) + SHRINK_STARTS * prior_place) / (starts + SHRINK_STARTS)
        edge = win / prior_win if prior_win > 0 else np.nan
        return win, place, edge, float(starts)

    def features(self, track, distance, barrier, running_position):
        out = empty_track_bias_features()
        b_key, b_global, s_key, s_global = runner_keys(track, distance, barrier, running_position)
        if b_global:
            vals = self._shrunk(b_key, b_global)
            if vals:
                (out['tb_barrier_win_rate'], out['tb_barrier_place_rate'],
                 out['tb_barrier_win_edge'], out['tb_barrier_starts']) = vals
        if s_global:
            vals = self._shrunk(s_key, s_global)
            if vals:
                (out['tb_style_win_rate'], out['tb_style_place_rate'],
                 out['tb_style_win_edge'], out['tb_style_starts']) = vals
        return out


def runner_inputs(cd):
    """The four inputs, read from a horse's csv_data the same way everywhere."""
    cd = cd or {}
    return (cd.get('track'), cd.get('distance'), cd.get('horse barrier'), cd.get('runningposition'))


def point_in_time_features(rows):
    """rows: iterable of (date, cd, finish_position). Returns a list of feature
    dicts in the same order, each built only from results on EARLIER dates."""
    rows = list(rows)
    order = sorted(range(len(rows)), key=lambda i: (rows[i][0] is None, rows[i][0]))
    stats = TrackBiasStats()
    out = [None] * len(rows)
    pending, current = [], object()
    for i in order:
        date, cd, finish = rows[i]
        if date != current:
            for j in pending:
                stats.add(*runner_inputs(rows[j][1]), rows[j][2])
            pending, current = [], date
        out[i] = stats.features(*runner_inputs(cd))
        pending.append(i)
    return out
