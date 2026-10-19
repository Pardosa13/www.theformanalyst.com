"""Features from each horse's previous runs (up to 10) in the PuntingForm form file.

The form file saved on every meeting (meetings.csv_data) has one row per past
run per horse. The analyzer and the ML model used to keep only the latest row
(last start), so runs 2-10 were thrown away apart from finish positions in the
'horse last10' string. This module turns all saved past runs into features.

Training (backtest.py) and live scoring (ml_predict.py) both call
past_run_features_for_meeting() on the same saved file, so the two always
compute identical values.

Notes on the source data:
- 'form time' and 'sectional' are the race's times (the winner's), not the
  horse's own: every runner from the same past race carries the same value.
  A horse's own time is estimated as race time + margin * SECONDS_PER_LENGTH.
- 'form margin' is lengths behind the winner, or for the winner lengths in
  front of second. Here a win is stored as a negative margin so lower is
  always better.
- Only runs dated strictly before the meeting date are used, so a re-saved
  file can never leak the race being predicted.
"""
import csv
import io
import math
import re
from datetime import datetime

import numpy as np

from strike_rate_matching import normalize_name

SECONDS_PER_LENGTH = 0.17
MAX_MARGIN = 20.0
DISTANCE_MATCH_METRES = 100
SPEED_DISTANCE_WINDOW_METRES = 300

# Column order is part of the training contract: backtest.extract_features and
# ml_predict.extract_features append these in exactly this order.
PAST_RUN_FEATURES = [
    'pr_n_runs',              # past runs saved for this horse (max 10)
    'pr_runs_365',            # of those, how many in the last 365 days
    'pr_avg_margin_l3',       # average margin last 3 (win = negative)
    'pr_avg_margin_l5',
    'pr_best_margin_l5',
    'pr_margin_trend',        # last margin minus average of runs 2-4 (negative = improving)
    'pr_win_rate_l5',
    'pr_place_rate_l5',       # top 3
    'pr_avg_pos_l5',
    'pr_avg_log_sp_l5',       # average log starting price: how the market rated it
    'pr_min_sp_l5',
    'pr_last_adj_speed',      # last start metres/second, adjusted for margin
    'pr_avg_adj_speed_l3',
    'pr_best_adj_speed_l5',   # best adjusted speed at a similar distance to today
    'pr_dist_runs_l10',       # runs within 100m of today's distance
    'pr_dist_avg_margin_l10',
    'pr_track_runs_l10',      # runs at today's track
    'pr_track_avg_margin_l10',
    'pr_wet_avg_margin_l10',  # soft/heavy runs
    'pr_dry_avg_margin_l10',  # firm/good runs
    'pr_days_l1_l2',          # days between last two runs
    'pr_weight_vs_l5',        # today's weight minus average weight carried last 5
    'pr_avg_class_l5',        # average class level of last 5 runs (see class_level)
    'pr_class_trend',         # last start class minus average of runs 2-5 (positive = stepping up)
    'pr_class_vs_today',      # today's class minus average of last 5 (positive = harder today)
    'pr_prep_runs',           # runs this preparation (a 60+ day gap starts a new prep)
    'pr_best_margin_prep',    # best margin this preparation
    'pr_jockey_runs_l10',     # past rides on this horse by today's jockey
    'pr_jockey_avg_margin_l10',
    'pr_late_speed_last',     # estimated last-600m speed at last start (see _parse_run)
    'pr_best_late_speed_l5',
]

# Race-relative rank/vs-average derivatives (same treatment as NEW_RELATIVE_COLS).
PAST_RUN_RELATIVE_COLS = [
    'pr_avg_margin_l3', 'pr_avg_log_sp_l5', 'pr_last_adj_speed', 'pr_best_adj_speed_l5',
    'pr_avg_class_l5', 'pr_best_late_speed_l5',
]
PAST_RUN_RELATIVE_LOWER_IS_BETTER = {'pr_avg_margin_l3', 'pr_avg_log_sp_l5'}


def empty_past_run_features():
    return {name: np.nan for name in PAST_RUN_FEATURES}


def _clean_keys(row):
    # The file has some headers with a leading space (' race id').
    return {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items() if k is not None}


def parse_date(value):
    text = str(value or '').strip().split(' ')[0]
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d', '%d-%b-%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _float(value):
    try:
        number = float(str(value).replace('$', '').replace(',', '').replace('m', '').strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _race_seconds(value):
    match = re.match(r'(\d+):(\d+(?:\.\d+)?)', str(value or '').strip())
    if not match:
        return None
    seconds = int(match.group(1)) * 60 + float(match.group(2))
    return seconds if seconds > 10 else None


def _track_key(name):
    # 'Randwick-Kensington' -> 'randwick', 'Sandown-Hillside' -> 'sandown'
    return re.split(r'[-(]', str(name or '').strip().lower())[0].strip()


def _condition_group(text_value):
    text_value = str(text_value or '').lower()
    if 'heavy' in text_value or 'soft' in text_value:
        return 'wet'
    if 'good' in text_value or 'firm' in text_value:
        return 'dry'
    match = re.search(r'(\d+)', text_value)
    if match:
        return 'wet' if int(match.group(1)) >= 5 else 'dry'
    return None


def class_level(text_value):
    """Class string -> rough ladder number (higher = stronger race). None if unknown.

    Maiden 20, Class 1-6 -> 30-55, Benchmark/Rating N -> N, Restricted/Open
    handicap 85-90, Listed 95, Group 3/2/1 100/105/110.
    """
    text_value = str(text_value or '').lower()
    if not text_value.strip():
        return None
    if 'group 1' in text_value or re.search(r'\bg1\b', text_value):
        return 110.0
    if 'group 2' in text_value or re.search(r'\bg2\b', text_value):
        return 105.0
    if 'group 3' in text_value or re.search(r'\bg3\b', text_value):
        return 100.0
    if 'listed' in text_value or re.search(r'\blr?\b', text_value):
        return 95.0
    match = re.search(r'(?:bm|benchmark|rating|rst|rs)\s*(\d{2,3})', text_value)
    if match:
        return float(match.group(1))
    match = re.search(r'(?:class|cl)\s*(\d)', text_value)
    if match:
        return 25.0 + 5.0 * int(match.group(1))
    if 'maiden' in text_value or 'mdn' in text_value:
        return 20.0
    if 'open' in text_value or 'quality' in text_value:
        return 90.0
    if 'restricted' in text_value:
        return 60.0
    return None


def _parse_run(row):
    """One past-run row -> dict, or None if it has no usable date/position."""
    run_date = parse_date(row.get('form meeting date'))
    position = _float(row.get('form position'))
    if run_date is None or position is None or position <= 0:
        return None
    margin = _float(row.get('form margin'))
    if margin is not None:
        margin = min(abs(margin), MAX_MARGIN)
        if position == 1:
            margin = -margin
    distance = _float(row.get('form distance'))
    race_seconds = _race_seconds(row.get('form time'))
    adj_speed = None
    if distance and race_seconds and margin is not None:
        own_seconds = race_seconds + max(margin, 0.0) * SECONDS_PER_LENGTH
        speed = distance / own_seconds
        if 10.0 <= speed <= 20.0:
            adj_speed = speed
    # 'sectional' is the race's last-600m time ('34.88sec 600m'), again the
    # leader's not the horse's. The horse's is estimated with the finishing
    # margin, which assumes it held its 600m position gap — rough, so the
    # back-test decides whether it earns its place.
    late_speed = None
    sectional = re.match(r'\s*(\d+(?:\.\d+)?)\s*sec\s*(\d+)\s*m', str(row.get('sectional') or ''), re.I)
    if sectional and margin is not None:
        sect_seconds, sect_metres = float(sectional.group(1)), float(sectional.group(2))
        if sect_seconds > 0 and sect_metres > 0:
            speed = sect_metres / (sect_seconds + max(margin, 0.0) * SECONDS_PER_LENGTH)
            if 12.0 <= speed <= 20.0:
                late_speed = speed
    price = _float(row.get('form price'))
    weight = _float(row.get('form weight'))
    return {
        'date': run_date,
        'position': position,
        'margin': margin,
        'distance': distance,
        'adj_speed': adj_speed,
        'price': price if price and price > 1.0 else None,
        'weight': weight if weight and 45 <= weight <= 70 else None,
        'track': _track_key(row.get('form track')),
        'condition': _condition_group(row.get('form track condition')),
        'race_id': str(row.get('race id') or '').strip(),
        'class': class_level(row.get('form class')),
        'jockey': normalize_name(str(row.get('form jockey') or '')),
        'late_speed': late_speed,
    }


def _mean(values):
    values = [v for v in values if v is not None]
    return float(np.mean(values)) if values else np.nan


def compute_past_run_features(runs, meeting_date, distance, track, weight,
                              today_class=None, today_jockey=None):
    """runs: parsed past runs for one horse (any order). Returns PAST_RUN_FEATURES dict."""
    features = empty_past_run_features()
    if meeting_date is not None:
        runs = [r for r in runs if r['date'] < meeting_date]
    # The same past race can appear twice if the file was assembled from
    # several requests; keep one row per date + race.
    seen, unique = set(), []
    for run in sorted(runs, key=lambda r: r['date'], reverse=True):
        key = (run['date'], run['race_id'] or run['track'])
        if key not in seen:
            seen.add(key)
            unique.append(run)
    runs = unique[:10]
    if not runs:
        features['pr_n_runs'] = 0.0
        return features

    l3, l5 = runs[:3], runs[:5]
    margins_l5 = [r['margin'] for r in l5 if r['margin'] is not None]

    features['pr_n_runs'] = float(len(runs))
    if meeting_date is not None:
        features['pr_runs_365'] = float(sum(1 for r in runs if (meeting_date - r['date']).days <= 365))
    features['pr_avg_margin_l3'] = _mean(r['margin'] for r in l3)
    features['pr_avg_margin_l5'] = _mean(margins_l5)
    features['pr_best_margin_l5'] = float(min(margins_l5)) if margins_l5 else np.nan
    if runs[0]['margin'] is not None:
        earlier = _mean(r['margin'] for r in runs[1:4])
        if not np.isnan(earlier):
            features['pr_margin_trend'] = runs[0]['margin'] - earlier
    features['pr_win_rate_l5'] = sum(1 for r in l5 if r['position'] == 1) / len(l5)
    features['pr_place_rate_l5'] = sum(1 for r in l5 if r['position'] <= 3) / len(l5)
    features['pr_avg_pos_l5'] = _mean(r['position'] for r in l5)
    prices = [r['price'] for r in l5 if r['price']]
    if prices:
        features['pr_avg_log_sp_l5'] = float(np.mean(np.log(prices)))
        features['pr_min_sp_l5'] = float(min(prices))
    if runs[0]['adj_speed'] is not None:
        features['pr_last_adj_speed'] = runs[0]['adj_speed']
    features['pr_avg_adj_speed_l3'] = _mean(r['adj_speed'] for r in l3)
    if distance:
        similar = [r['adj_speed'] for r in l5 if r['adj_speed'] is not None and r['distance']
                   and abs(r['distance'] - distance) <= SPEED_DISTANCE_WINDOW_METRES]
        if similar:
            features['pr_best_adj_speed_l5'] = float(max(similar))
        at_distance = [r for r in runs if r['distance'] and abs(r['distance'] - distance) <= DISTANCE_MATCH_METRES]
        features['pr_dist_runs_l10'] = float(len(at_distance))
        features['pr_dist_avg_margin_l10'] = _mean(r['margin'] for r in at_distance)
    today_track = _track_key(track)
    if today_track:
        at_track = [r for r in runs if r['track'] == today_track]
        features['pr_track_runs_l10'] = float(len(at_track))
        features['pr_track_avg_margin_l10'] = _mean(r['margin'] for r in at_track)
    features['pr_wet_avg_margin_l10'] = _mean(r['margin'] for r in runs if r['condition'] == 'wet')
    features['pr_dry_avg_margin_l10'] = _mean(r['margin'] for r in runs if r['condition'] == 'dry')
    if len(runs) >= 2:
        features['pr_days_l1_l2'] = float((runs[0]['date'] - runs[1]['date']).days)
    weights = [r['weight'] for r in l5 if r['weight']]
    if weight and 45 <= weight <= 70 and weights:
        features['pr_weight_vs_l5'] = weight - float(np.mean(weights))

    classes_l5 = [r['class'] for r in l5 if r['class'] is not None]
    if classes_l5:
        features['pr_avg_class_l5'] = float(np.mean(classes_l5))
        if today_class is not None:
            features['pr_class_vs_today'] = today_class - features['pr_avg_class_l5']
    if runs[0]['class'] is not None:
        earlier = _mean(r['class'] for r in runs[1:5])
        if not np.isnan(earlier):
            features['pr_class_trend'] = runs[0]['class'] - earlier

    # Current preparation: runs back to the first 60+ day gap. A horse
    # resuming today (60+ days since its last run) is on 0 prep runs.
    prep = []
    if meeting_date is None or (meeting_date - runs[0]['date']).days < 60:
        prep.append(runs[0])
        for previous, run in zip(runs, runs[1:]):
            if (previous['date'] - run['date']).days >= 60:
                break
            prep.append(run)
    features['pr_prep_runs'] = float(len(prep))
    prep_margins = [r['margin'] for r in prep if r['margin'] is not None]
    if prep_margins:
        features['pr_best_margin_prep'] = float(min(prep_margins))

    jockey = normalize_name(str(today_jockey or ''))
    if jockey:
        rides = [r for r in runs if r['jockey'] == jockey]
        features['pr_jockey_runs_l10'] = float(len(rides))
        features['pr_jockey_avg_margin_l10'] = _mean(r['margin'] for r in rides)

    if runs[0]['late_speed'] is not None:
        features['pr_late_speed_last'] = runs[0]['late_speed']
    late = [r['late_speed'] for r in l5 if r['late_speed'] is not None]
    if late:
        features['pr_best_late_speed_l5'] = float(max(late))
    return features


def horse_key(race_number, horse_name):
    try:
        race_no = str(int(float(str(race_number).strip())))
    except (TypeError, ValueError):
        race_no = str(race_number or '').strip()
    return race_no, normalize_name(str(horse_name or ''))


def past_run_features_for_meeting(csv_text):
    """Saved meeting form file -> {(race_no, normalized horse name): features}.

    Returns {} for anything that is not a readable form file.
    """
    if not csv_text or not isinstance(csv_text, str):
        return {}
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = [_clean_keys(row) for row in reader]
    except Exception:
        return {}
    grouped = {}
    for row in rows:
        name = row.get('horse name')
        race_no = row.get('race number')
        if not name or not race_no or str(name).lower() == 'horse name':
            continue
        key = horse_key(race_no, name)
        entry = grouped.setdefault(key, {'row': row, 'runs': []})
        run = _parse_run(row)
        if run:
            entry['runs'].append(run)
    out = {}
    for key, entry in grouped.items():
        row = entry['row']
        out[key] = compute_past_run_features(
            entry['runs'],
            meeting_date=parse_date(row.get('meeting date')),
            distance=_float(row.get('distance')),
            track=row.get('track'),
            weight=_float(row.get('horse weight')),
            today_class=class_level(row.get('class restrictions')),
            today_jockey=row.get('horse jockey'),
        )
    return out
