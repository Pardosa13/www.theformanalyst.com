"""
ml_predict.py - Shadow ML scoring using the active champion model artifact.

Usage:
    from ml_predict import predict_meeting
    scores = predict_meeting(meeting_id, db_session)
    # Returns: {horse_id: ml_score, ...}

Drop this file in the same directory as app.py.
It uses the exact same extract_features() logic as backtest.py.
"""

import os
import json
import re
import logging
from collections import Counter
import numpy as np
from strike_rate_matching import get_sr_win_pct, normalize_name
import pandas as pd
from datetime import datetime

log = logging.getLogger(__name__)

# ── Feature extraction (mirrors backtest.py exactly) ──────────────────────────

def parse_record(record_str):
    if not record_str or not isinstance(record_str, str):
        return 0, 0, 0, 0
    record_str = str(record_str).strip().replace(' ', '')
    match = re.match(r'(\d+):(\d+)-(\d+)-(\d+)', record_str)
    if match:
        runs, wins, seconds, thirds = [int(x) for x in match.groups()]
        return runs, wins, seconds, thirds
    return 0, 0, 0, 0

def win_rate(record_str):
    runs, wins, _, _ = parse_record(record_str)
    return wins / runs if runs > 0 else 0.0

def podium_rate(record_str):
    runs, wins, seconds, thirds = parse_record(record_str)
    return (wins + seconds + thirds) / runs if runs > 0 else 0.0

def parse_last10(last10_str):
    if not last10_str:
        return {
            'l10_runs': 0, 'l10_wins': 0, 'l10_win_rate': 0,
            'l10_places': 0, 'l10_place_rate': 0, 'l5_win_rate': 0,
            'l5_place_rate': 0, 'is_first_up': 0, 'is_second_up': 0,
            'last_position': 9, 'form_trend': 0
        }
    s = str(last10_str).strip()
    runs = [c for c in s if c.lower() != 'x' and c.isdigit()]
    if not runs:
        return {
            'l10_runs': 0, 'l10_wins': 0, 'l10_win_rate': 0,
            'l10_places': 0, 'l10_place_rate': 0, 'l5_win_rate': 0,
            'l5_place_rate': 0, 'is_first_up': 0, 'is_second_up': 0,
            'last_position': 9, 'form_trend': 0
        }
    runs_list = [int(c) for c in runs]
    last5  = runs_list[-5:]  if len(runs_list) >= 5  else runs_list
    last10 = runs_list[-10:] if len(runs_list) >= 10 else runs_list
    l10_wins   = sum(1 for x in last10 if x == 1)
    l10_places = sum(1 for x in last10 if x in [1, 2, 3])
    l5_wins    = sum(1 for x in last5  if x == 1)
    l5_places  = sum(1 for x in last5  if x in [1, 2, 3])
    if len(runs_list) >= 4:
        mid = len(runs_list) // 2
        early_wr  = sum(1 for x in runs_list[:mid] if x == 1) / mid
        recent_wr = sum(1 for x in runs_list[mid:] if x == 1) / (len(runs_list) - mid)
        trend = recent_wr - early_wr
    else:
        trend = 0.0
    is_first_up  = 1 if s.lower().endswith('x') else 0
    is_second_up = 0
    if len(s) >= 2 and s[-2].lower() == 'x' and s[-1].isdigit():
        is_second_up = 1
    last_pos = runs_list[-1] if runs_list else 9
    return {
        'l10_runs': len(last10), 'l10_wins': l10_wins,
        'l10_win_rate': l10_wins / len(last10) if last10 else 0,
        'l10_places': l10_places,
        'l10_place_rate': l10_places / len(last10) if last10 else 0,
        'l5_win_rate': l5_wins / len(last5) if last5 else 0,
        'l5_place_rate': l5_places / len(last5) if last5 else 0,
        'is_first_up': is_first_up, 'is_second_up': is_second_up,
        'last_position': last_pos, 'form_trend': trend,
    }

def parse_date_str(date_str):
    if not date_str:
        return None
    for fmt in ['%d/%m/%Y %H:%M:%S', '%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d']:
        try:
            return datetime.strptime(str(date_str).split(' ')[0], fmt.split(' ')[0])
        except Exception:
            continue
    return None

def days_since_run(meeting_date_str, form_date_str):
    try:
        race_date = parse_date_str(meeting_date_str)
        last_date = parse_date_str(form_date_str)
        if race_date and last_date:
            return (race_date - last_date).days
    except Exception:
        pass
    return -1

def calculate_class_score(class_string, prize_string):
    if not class_string:
        return 50.0
    s = str(class_string).strip()
    gm = re.search(r'Group\s*([123])', s, re.IGNORECASE)
    if gm:
        return {1: 130, 2: 122, 3: 115}.get(int(gm.group(1)), 100)
    if re.search(r'Listed', s, re.IGNORECASE):
        return 108.0
    bm = re.search(r'(?:Benchmark|Bench.?|BM)\s*(\d+)', s, re.IGNORECASE)
    if bm:
        return min(100, max(1, int(bm.group(1))))
    cm = re.search(r'(?:Class|Cls)\s*(\d+)', s, re.IGNORECASE)
    if cm:
        return {1: 40, 2: 55, 3: 65, 4: 75, 5: 85, 6: 92}.get(int(cm.group(1)), 60)
    if re.search(r'Maiden|Mdn', s, re.IGNORECASE):
        return 50.0
    if prize_string:
        pm = re.search(r'1st\s+\$([0-9,]+)', str(prize_string), re.IGNORECASE)
        if pm:
            prize = int(pm.group(1).replace(',', ''))
            if prize >= 100000: return 100.0
            if prize >= 60000:  return 88.0
            if prize >= 35000:  return 72.0
            if prize >= 18000:  return 56.0
            if prize >= 8000:   return 40.0
            return 32.0
    return 50.0


def extract_features(cd, track_condition, jockey_sr_lookup=None, trainer_sr_lookup=None):
    """
    Extract the 61 features the pkl was trained on.
    cd = horse.csv_data dict
    track_condition = race.track_condition string
    """
    features = {}

    try:    features['horse_age']    = float(cd.get('horse age', 0) or 0)
    except: features['horse_age']    = 0.0

    sex_map = {'Gelding': 0, 'Mare': 1, 'Horse': 2, 'Colt': 3, 'Filly': 4, 'Rig': 0}
    features['horse_sex'] = sex_map.get(str(cd.get('horse sex', '')).strip(), 0)

    try:    features['horse_weight'] = float(cd.get('horse weight', 57) or 57)
    except: features['horse_weight'] = 57.0

    try:    features['horse_claim']  = float(cd.get('horse claim', 0) or 0)
    except: features['horse_claim']  = 0.0

    try:
        lw = float(cd.get('form weight', 0) or 0)
        cw = features['horse_weight']
        features['weight_change'] = (cw - lw) if (49 <= lw <= 65 and 49 <= cw <= 65) else 0.0
    except:
        features['weight_change'] = 0.0

    features['weight_vs_avg'] = 0.0  # filled below after race avg computed

    try:    features['distance'] = float(str(cd.get('distance', 1400) or 1400).replace('m', ''))
    except: features['distance'] = 1400.0

    condition_map = {'good': 0, 'soft': 1, 'heavy': 2, 'firm': 3, 'synthetic': 4}
    tc = str(track_condition or '').lower().strip()
    for k, v in condition_map.items():
        if k in tc:
            features['track_condition'] = v
            break
    else:
        features['track_condition'] = 0

    features['career_win_rate']    = win_rate(cd.get('horse record', ''))
    features['career_podium_rate'] = podium_rate(cd.get('horse record', ''))
    try:
        r, _, _, _ = parse_record(cd.get('horse record', ''))
        features['career_runs'] = float(r)
    except:
        features['career_runs'] = 0.0

    features['distance_win_rate']       = win_rate(cd.get('horse record distance', ''))
    features['track_win_rate']          = win_rate(cd.get('horse record track', ''))
    features['track_distance_win_rate'] = win_rate(cd.get('horse record track distance', ''))
    cond_key = tc.split()[0] if tc else 'good'
    features['condition_win_rate']  = win_rate(cd.get(f'horse record {cond_key}', ''))
    features['first_up_win_rate']   = win_rate(cd.get('horse record first up', ''))
    features['second_up_win_rate']  = win_rate(cd.get('horse record second up', ''))

    features.update(parse_last10(cd.get('horse last10', '')))

    try:    features['last_position'] = float(cd.get('form position', 5) or 5)
    except: features['last_position'] = 5.0
    try:    features['last_margin']   = float(cd.get('form margin', 10) or 10)
    except: features['last_margin']   = 10.0
    try:    features['last_sp']       = float(cd.get('form price', 10) or 10)
    except: features['last_sp']       = 10.0

    try:
        ld = float(cd.get('form distance', 0) or 0)
        features['distance_change'] = features['distance'] - ld if ld > 0 else 0.0
    except:
        features['distance_change'] = 0.0

    try:
        today_class = calculate_class_score(cd.get('class restrictions', ''), cd.get('race prizemoney', ''))
        last_class  = calculate_class_score(cd.get('form class', ''), cd.get('prizemoney', ''))
        features['class_change'] = today_class - last_class
    except:
        features['class_change'] = 0.0

    features['days_since_run'] = float(days_since_run(
        cd.get('meeting date', ''), cd.get('form meeting date', '')
    ))

    try:    features['pfai_score'] = float(cd.get('pfaiscore', 0) or cd.get('pfaiScore', 0) or 0)
    except: features['pfai_score'] = 0.0

    try:    features['last200_rank'] = float(cd.get('last200timerank', cd.get('last200TimeRank', 99)) or 99)
    except: features['last200_rank'] = 99.0
    try:    features['last400_rank'] = float(cd.get('last400timerank', cd.get('last400TimeRank', 99)) or 99)
    except: features['last400_rank'] = 99.0
    try:    features['last600_rank'] = float(cd.get('last600timerank', cd.get('last600TimeRank', 99)) or 99)
    except: features['last600_rank'] = 99.0

    country = str(cd.get('country', 'AUS') or 'AUS').strip().upper()
    country_score_map = {'AUS': 0, 'NZ': -1, 'IRE': -0.5, 'GB': 1, 'FR': -2, 'JPN': -2, 'GER': -2, 'USA': 0}
    features['country_score'] = float(country_score_map.get(country, -0.5))
    features['is_aus_bred']   = 1.0 if country == 'AUS' else 0.0

    pos_map = {'LEADER': 3, 'ONPACE': 2, 'MIDFIELD': 1, 'BACKMARKER': 0}
    run_pos = str(cd.get('runningposition', cd.get('runningPosition', '')) or '').upper().strip()
    features['running_position'] = float(pos_map.get(run_pos, 1))

    dist = features['distance']
    is_sprint  = dist <= 1200
    is_mile    = 1300 <= dist <= 1700
    is_middle  = 1800 <= dist <= 2200
    is_staying = dist > 2200

    features['leader_sprint']      = 1.0 if run_pos == 'LEADER'     and is_sprint  else 0.0
    features['leader_mile']        = 1.0 if run_pos == 'LEADER'     and is_mile    else 0.0
    features['leader_middle']      = 1.0 if run_pos == 'LEADER'     and is_middle  else 0.0
    features['leader_staying']     = 1.0 if run_pos == 'LEADER'     and is_staying else 0.0
    features['onpace_sprint']      = 1.0 if run_pos == 'ONPACE'     and is_sprint  else 0.0
    features['onpace_mile']        = 1.0 if run_pos == 'ONPACE'     and is_mile    else 0.0
    features['backmarker_sprint']  = 1.0 if run_pos == 'BACKMARKER' and is_sprint  else 0.0
    features['backmarker_staying'] = 1.0 if run_pos == 'BACKMARKER' and is_staying else 0.0

    jockey_name = str(cd.get('horse jockey', '') or '').strip()
    features['jockey_sr'] = get_sr_win_pct(jockey_name, jockey_sr_lookup or {})

    trainer_name = str(cd.get('horse trainer', '') or '').strip()
    features['trainer_sr'] = get_sr_win_pct(trainer_name, trainer_sr_lookup or {})

    try:    features['horse_barrier'] = float(cd.get('horse barrier', 0) or 0)
    except: features['horse_barrier'] = 0.0
    try:    features['form_barrier']  = float(cd.get('form barrier', 0) or 0)
    except: features['form_barrier']  = 0.0

    bc = features['horse_barrier']
    bl = features['form_barrier']
    features['barrier_change'] = (bc - bl) if (bc > 0 and bl > 0) else 0.0

    try:
        prize = float(str(cd.get('horse prize money', 0) or 0).replace(',', '').replace('$', ''))
        features['horse_career_prize'] = float(np.log1p(prize))
    except:
        features['horse_career_prize'] = 0.0

    try:
        pwon = float(str(cd.get('prizemoney won', 0) or 0).replace(',', '').replace('$', ''))
        features['prizemoney_won'] = float(np.log1p(pwon))
    except:
        features['prizemoney_won'] = 0.0

    form_cond = str(cd.get('form track condition', '') or '').lower().strip()
    for k, v in condition_map.items():
        if k in form_cond:
            features['form_track_condition'] = float(v)
            break
    else:
        features['form_track_condition'] = features['track_condition']

    features['track_condition_change'] = features['track_condition'] - features['form_track_condition']

    try:    features['form_field_size'] = float(cd.get('form other runners', 0) or 0)
    except: features['form_field_size'] = 0.0

    form_jockey = str(cd.get('form jockey', '') or '').strip().lower()
    features['same_jockey'] = 1.0 if (form_jockey and form_jockey == jockey_name.lower()) else 0.0

    today_track = str(cd.get('track', '') or '').strip().lower()
    form_track  = str(cd.get('form track', '') or '').strip().lower()
    features['same_track'] = 1.0 if (today_track and today_track == form_track) else 0.0

    can_claim_raw = str(cd.get('jockeys can claim', '') or '').strip().lower()
    features['jockeys_can_claim'] = 1.0 if can_claim_raw in ('yes', 'true', '1', 'y') else 0.0

    return features

def add_race_relative_features(feature_rows):
    temp = pd.DataFrame(feature_rows)

    relative_cols = [
        'pfai_score', 'last_sp', 'career_win_rate', 'career_podium_rate',
        'distance_win_rate', 'track_win_rate', 'track_distance_win_rate',
        'condition_win_rate', 'last_position', 'last_margin', 'horse_weight',
        'weight_vs_avg', 'jockey_sr', 'trainer_sr', 'last200_rank',
        'last400_rank', 'last600_rank', 'running_position',
        'horse_career_prize', 'prizemoney_won', 'barrier_change',
    ]

    lower_is_better = {
        'last_position', 'last_margin', 'last_sp',
        'last200_rank', 'last400_rank', 'last600_rank',
    }

    temp['field_size'] = len(temp)

    for col in relative_cols:
        if col not in temp.columns:
            continue

        ascending = col in lower_is_better

        temp[f'{col}_race_rank'] = temp[col].rank(method='min', ascending=ascending)
        temp[f'{col}_vs_race_avg'] = temp[col] - temp[col].mean()

        if ascending:
            temp[f'{col}_vs_race_best'] = temp[col] - temp[col].min()
        else:
            temp[f'{col}_vs_race_best'] = temp[col] - temp[col].max()

        denom = (temp['field_size'] - 1).replace(0, np.nan)
        temp[f'{col}_race_percentile'] = 1.0 - ((temp[f'{col}_race_rank'] - 1) / denom)
        temp[f'{col}_race_percentile'] = temp[f'{col}_race_percentile'].fillna(1.0)

    return temp.to_dict('records')

# ── Expected feature order (must match pkl training order) ────────────────────

FEATURE_NAMES = [
    'horse_age', 'horse_sex', 'horse_weight', 'horse_claim', 'weight_change',
    'weight_vs_avg', 'distance', 'track_condition', 'career_win_rate',
    'career_podium_rate', 'career_runs', 'distance_win_rate', 'track_win_rate',
    'track_distance_win_rate', 'condition_win_rate', 'first_up_win_rate',
    'second_up_win_rate', 'l10_runs', 'l10_wins', 'l10_win_rate', 'l10_places',
    'l10_place_rate', 'l5_win_rate', 'l5_place_rate', 'is_first_up', 'is_second_up',
    'last_position', 'form_trend', 'last_margin', 'last_sp', 'distance_change',
    'class_change', 'days_since_run', 'pfai_score', 'last200_rank', 'last400_rank',
    'last600_rank', 'country_score', 'is_aus_bred', 'running_position',
    'leader_sprint', 'leader_mile', 'leader_middle', 'leader_staying',
    'onpace_sprint', 'onpace_mile', 'backmarker_sprint', 'backmarker_staying',
    'jockey_sr', 'trainer_sr', 'horse_barrier', 'form_barrier', 'barrier_change',
    'horse_career_prize', 'prizemoney_won', 'form_track_condition',
    'track_condition_change', 'form_field_size', 'same_jockey', 'same_track',
    'jockeys_can_claim'
]

RACE_RELATIVE_BASE_COLS = [
    'pfai_score', 'last_sp', 'career_win_rate', 'career_podium_rate',
    'distance_win_rate', 'track_win_rate', 'track_distance_win_rate',
    'condition_win_rate', 'last_position', 'last_margin', 'horse_weight',
    'weight_vs_avg', 'jockey_sr', 'trainer_sr', 'last200_rank',
    'last400_rank', 'last600_rank', 'running_position',
    'horse_career_prize', 'prizemoney_won', 'barrier_change',
]

RACE_RELATIVE_FEATURES = []
for col in RACE_RELATIVE_BASE_COLS:
    RACE_RELATIVE_FEATURES.extend([
        f'{col}_race_rank',
        f'{col}_vs_race_avg',
        f'{col}_vs_race_best',
        f'{col}_race_percentile',
    ])

FEATURE_NAMES = FEATURE_NAMES + RACE_RELATIVE_FEATURES + ['field_size']


def _model_feature_names(model):
    """Return feature names persisted on the trained estimator, if available."""
    names = getattr(model, 'feature_names_in_', None)
    if names is None:
        names = getattr(model, '_form_analyst_expected_features', None)
    if names is None:
        return None
    return [str(name) for name in list(names)]


def _feature_defaulted_to_zero_summary(expected_features, raw_X):
    """Return feature names/counts that live scoring will fill with zero."""
    if not expected_features:
        return {}

    defaulted_columns = Counter(name for name in expected_features if name not in raw_X.columns)
    null_defaulted = raw_X.reindex(columns=expected_features).isna().sum()
    for col, count in null_defaulted.items():
        if count:
            defaulted_columns[col] += int(count)

    return dict(defaulted_columns)


def _log_prediction_feature_diagnostics(model, meeting_id, race, raw_X):
    """Log the live feature contract immediately before model scoring."""
    model_features = _model_feature_names(model)
    generated_features = [str(name) for name in list(raw_X.columns)]
    expected_feature_count = len(model_features) if model_features is not None else None
    generated_feature_count = len(generated_features)

    if model_features is None:
        missing_feature_names = []
        extra_feature_names = []
        feature_counts_match = False
        feature_order_matches = False
        features_defaulted_to_zero = {}
        model_first_10_features = []
    else:
        missing_feature_names = [name for name in model_features if name not in raw_X.columns]
        extra_feature_names = [name for name in generated_features if name not in model_features]
        feature_counts_match = expected_feature_count == generated_feature_count
        feature_order_matches = generated_features == model_features
        features_defaulted_to_zero = _feature_defaulted_to_zero_summary(model_features, raw_X)
        model_first_10_features = model_features[:10]

    predict_method = 'predict_proba' if hasattr(model, 'predict_proba') else 'predict'

    log.info(
        "ML_PREDICTION_FEATURE_DIAGNOSTICS "
        "meeting=%s race=%s model_id=%s model_type=%s model_class=%s "
        "predict_method=%s expected_feature_count=%s generated_feature_count=%s "
        "feature_counts_match=%s feature_order_matches=%s missing_feature_names=%s "
        "extra_feature_names=%s features_defaulted_to_zero=%s "
        "model_first_10_feature_names=%s generated_first_10_feature_names=%s",
        meeting_id,
        getattr(race, 'race_number', None),
        getattr(model, '_form_analyst_model_id', None),
        getattr(model, '_form_analyst_model_type', None),
        type(model).__name__,
        predict_method,
        expected_feature_count,
        generated_feature_count,
        feature_counts_match,
        feature_order_matches,
        missing_feature_names,
        extra_feature_names,
        features_defaulted_to_zero,
        model_first_10_features,
        generated_features[:10],
    )

def _feature_contract_hash(feature_names):
    """Return a stable hash for an ordered feature-name contract."""
    import hashlib

    return hashlib.sha256(json.dumps(list(feature_names), separators=(',', ':')).encode('utf-8')).hexdigest()


def _live_feature_contract_predicates(model, raw_X, final_X):
    """Return individually named live feature-contract predicate values."""
    stored_features = _model_feature_names(model)
    final_features = [str(name) for name in list(final_X.columns)]
    raw_features = [str(name) for name in list(raw_X.columns)]
    expected_feature_count = getattr(model, '_form_analyst_expected_feature_count', None)
    if expected_feature_count is None and stored_features is not None:
        expected_feature_count = len(stored_features)
    try:
        expected_feature_count_int = int(expected_feature_count) if expected_feature_count is not None else None
    except (TypeError, ValueError):
        expected_feature_count_int = None

    model_n_features_in = getattr(model, 'n_features_in_', None)
    try:
        model_n_features_in_int = int(model_n_features_in) if model_n_features_in is not None else None
    except (TypeError, ValueError):
        model_n_features_in_int = None

    if stored_features is None:
        stored_features = []
        missing_features = []
        extra_features = raw_features
        names_match = False
        order_matches = False
    else:
        stored_features = [str(name) for name in stored_features]
        missing_features = [name for name in stored_features if name not in raw_X.columns]
        extra_features = [name for name in raw_features if name not in stored_features]
        names_match = not missing_features and not extra_features and set(final_features) == set(stored_features)
        order_matches = final_features == stored_features

    duplicate_features = [name for name, count in Counter(final_features).items() if count > 1]
    stored_count_matches = len(stored_features) == expected_feature_count_int if expected_feature_count_int is not None else bool(stored_features)
    final_count_matches = len(final_features) == len(stored_features) if stored_features else False
    missing_features_empty = not missing_features
    extra_features_empty = not extra_features
    duplicate_features_empty = not duplicate_features
    model_n_features_matches = model_n_features_in_int is None or model_n_features_in_int == len(final_features)
    expected_feature_count_matches = expected_feature_count_int is None or expected_feature_count_int == len(final_features)
    expected_feature_count_matches_stored = expected_feature_count_int is None or expected_feature_count_int == len(stored_features)
    feature_hash_matches = bool(stored_features) and _feature_contract_hash(stored_features) == _feature_contract_hash(final_features)
    metadata_version_matches = getattr(model, '_form_analyst_model_version', None) == globals().get('MODEL_VERSION')
    contains_nan = bool(final_X.isna().any().any())
    numeric_values = final_X.select_dtypes(include=[np.number])
    contains_infinity = bool(np.isinf(numeric_values.to_numpy(dtype=float)).any()) if not numeric_values.empty else False
    dtype_check_passes = all(pd.api.types.is_numeric_dtype(dtype) for dtype in final_X.dtypes)

    genuine_contract_matches = (
        final_count_matches
        and names_match
        and order_matches
        and model_n_features_matches
        and expected_feature_count_matches
    )
    legacy_failure_expression = "not live_count_matches or not order_matches or missing_from_live or extra_live or not stored_matches_code"

    return {
        'stored_features': stored_features,
        'final_features': final_features,
        'expected_feature_count': expected_feature_count,
        'expected_feature_count_int': expected_feature_count_int,
        'model_n_features_in': model_n_features_in,
        'missing_features': missing_features,
        'extra_features': extra_features,
        'duplicate_features': duplicate_features,
        'stored_count_matches': stored_count_matches,
        'final_count_matches': final_count_matches,
        'names_match': names_match,
        'order_matches': order_matches,
        'missing_features_empty': missing_features_empty,
        'extra_features_empty': extra_features_empty,
        'duplicate_features_empty': duplicate_features_empty,
        'model_n_features_matches': model_n_features_matches,
        'expected_feature_count_matches': expected_feature_count_matches,
        'expected_feature_count_matches_stored': expected_feature_count_matches_stored,
        'feature_hash_matches': feature_hash_matches,
        'metadata_version_matches': metadata_version_matches,
        'contains_nan': contains_nan,
        'contains_infinity': contains_infinity,
        'dtype_check_passes': dtype_check_passes,
        'genuine_contract_matches': genuine_contract_matches,
        'failure_expression': "not (final_count_matches and names_match and order_matches and model_n_features_matches and expected_feature_count_matches)",
        'legacy_failure_expression': legacy_failure_expression,
        'legacy_stored_matches_code': stored_features == FEATURE_NAMES,
    }


def _log_live_feature_audit(model, meeting_id, race, feature_rows, raw_X, final_X):
    """
    Compare live scoring features against the active Champion model contract.

    Training builds the same raw/race-relative feature columns and applies only
    pandas median imputation before fitting; live scoring must therefore pass the
    same named columns in the same order and only default missing/null values.
    """
    model_id = getattr(model, '_form_analyst_model_id', None)
    contract = _live_feature_contract_predicates(model, raw_X, final_X)

    if not contract['stored_features']:
        log.error(
            "ML_FEATURE_AUDIT meeting=%s race=%s model_id=%s status=failed reason=missing_model_feature_names expected_features=%s",
            meeting_id, getattr(race, 'race_number', None), model_id, contract['expected_feature_count'],
        )
        raise RuntimeError("ML feature contract failed: model artifact has no persisted feature list")

    log.info(
        "ML_FEATURE_CONTRACT_PREDICATES meeting=%s race=%s model_id=%s "
        "stored_count_matches=%s final_count_matches=%s names_match=%s order_matches=%s "
        "missing_features_empty=%s extra_features_empty=%s duplicate_features_empty=%s "
        "model_n_features_matches=%s expected_feature_count_matches=%s feature_hash_matches=%s "
        "metadata_version_matches=%s contains_nan=%s contains_infinity=%s dtype_check_passes=%s "
        "model_n_features_in=%s expected_feature_count=%s failure_expression=%s legacy_failure_expression=%s "
        "legacy_stored_matches_code=%s stored_features=%s final_features=%s",
        meeting_id, getattr(race, 'race_number', None), model_id,
        contract['stored_count_matches'], contract['final_count_matches'], contract['names_match'], contract['order_matches'],
        contract['missing_features_empty'], contract['extra_features_empty'], contract['duplicate_features_empty'],
        contract['model_n_features_matches'], contract['expected_feature_count_matches'], contract['feature_hash_matches'],
        contract['metadata_version_matches'], contract['contains_nan'], contract['contains_infinity'], contract['dtype_check_passes'],
        contract['model_n_features_in'], contract['expected_feature_count'], contract['failure_expression'], contract['legacy_failure_expression'],
        contract['legacy_stored_matches_code'], contract['stored_features'], contract['final_features'],
    )

    if not contract['genuine_contract_matches']:
        failed_predicates = [
            name for name in (
                'stored_count_matches', 'final_count_matches', 'names_match', 'order_matches',
                'missing_features_empty', 'extra_features_empty', 'duplicate_features_empty',
                'model_n_features_matches', 'expected_feature_count_matches', 'feature_hash_matches',
                'metadata_version_matches', 'dtype_check_passes',
            )
            if not contract[name]
        ]
        failed_predicates.extend(
            name for name in ('contains_nan', 'contains_infinity') if contract[name]
        )
        log.error(
            "ML_FEATURE_AUDIT meeting=%s race=%s model_id=%s status=failed failed_predicates=%s missing_features=%s extra_features=%s duplicate_features=%s",
            meeting_id, getattr(race, 'race_number', None), model_id, failed_predicates,
            contract['missing_features'], contract['extra_features'], contract['duplicate_features'],
        )
        raise RuntimeError(
            f"ML feature contract failed for meeting={meeting_id} race={getattr(race, 'race_number', None)}: "
            f"failed_predicates={failed_predicates} stored={len(contract['stored_features'])} final={len(contract['final_features'])} "
            f"order_matches={contract['order_matches']} names_match={contract['names_match']} "
            f"model_n_features_matches={contract['model_n_features_matches']} "
            f"expected_feature_count_matches={contract['expected_feature_count_matches']} "
            f"missing={contract['missing_features']} extra={contract['extra_features']} duplicate={contract['duplicate_features']}"
        )

    log.info(
        "ML_FEATURE_AUDIT meeting=%s race=%s model_id=%s status=passed feature_count=%s order_matches=%s preprocessing=training_fillna_median live_fillna_zero scaling=none",
        meeting_id, getattr(race, 'race_number', None), model_id, final_X.shape[1], contract['order_matches'],
    )

    defaulted_columns = Counter(contract['missing_features'])
    null_defaulted = raw_X.reindex(columns=contract['stored_features']).isna().sum()
    for col, count in null_defaulted.items():
        if count:
            defaulted_columns[col] += int(count)

    defaulted_summary = dict(defaulted_columns)
    if defaulted_summary:
        log.warning(
            "ML_FEATURE_DEFAULTS meeting=%s race=%s model_id=%s default_value=0 columns=%s",
            meeting_id, getattr(race, 'race_number', None), model_id, defaulted_summary,
        )

    source_missing_summary = {}
    for feature_name in FEATURE_NAMES:
        count = sum(1 for row in feature_rows if feature_name not in row)
        if count:
            source_missing_summary[feature_name] = count
    if source_missing_summary:
        log.warning(
            "ML_FEATURE_MISSING_SOURCE meeting=%s race=%s model_id=%s columns=%s",
            meeting_id, getattr(race, 'race_number', None), model_id, source_missing_summary,
        )

def load_model():
    """Load only the active Champion model from DB, with filesystem fallback for local dev."""
    try:
        from sqlalchemy import create_engine, text
        import io, joblib
        db_url = os.environ.get('DATABASE_URL', '')
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        eng = create_engine(db_url, pool_pre_ping=True)
        with eng.connect() as conn:
            row = conn.execute(text(
                """
                SELECT id, run_id, run_date, combined_score, updated_at, pkl_data,
                       model_type, model_name, is_active, model_version,
                       artifact_filename, expected_feature_count, selection_metrics
                FROM backtest_best_model
                WHERE is_active = TRUE
                ORDER BY promoted_at DESC NULLS LAST, updated_at DESC, id DESC
                LIMIT 1
                """
            )).fetchone()
            if row and row[5]:
                model = joblib.load(io.BytesIO(bytes(row[5])))
                model._form_analyst_model_id = row[0]
                model._form_analyst_run_id = row[1]
                model._form_analyst_model_type = row[6]
                model._form_analyst_model_name = row[7]
                model._form_analyst_training_date = row[2]
                model._form_analyst_is_active = row[8]
                model._form_analyst_model_version = row[9] or getattr(model, '_form_analyst_model_version', None)
                model._form_analyst_artifact_path = f"db://backtest_best_model/{row[0]}"
                model._form_analyst_artifact_filename = row[10] or getattr(model, '_form_analyst_artifact_filename', None)
                model._form_analyst_expected_feature_count = row[11]
                try:
                    model._form_analyst_selection_metrics = json.loads(row[12]) if row[12] else getattr(model, '_form_analyst_selection_metrics', {})
                except Exception:
                    model._form_analyst_selection_metrics = getattr(model, '_form_analyst_selection_metrics', {})
                feature_count = len(_model_feature_names(model) or [])
                if row[11] and feature_count and int(row[11]) != feature_count:
                    raise RuntimeError(f"Active ML model feature count mismatch: db_expected={row[11]} artifact={feature_count}")
                log.info(
                    "ML_ACTIVE_MODEL_LOADED source=db active_algorithm=%s artifact_path=%s artifact_filename=%s training_run_id=%s training_date=%s model_version=%s feature_count=%s selected_overall_champion=%s selection_metrics=%s class=%s",
                    row[6], model._form_analyst_artifact_path, model._form_analyst_artifact_filename,
                    row[1], row[2], model._form_analyst_model_version, feature_count, bool(row[8]),
                    model._form_analyst_selection_metrics, type(model).__name__,
                )
                return model
    except Exception as e:
        log.warning(f"Could not load model from DB: {e}")

    model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models', 'form_analyst_best_random_forest.pkl')
    if os.path.exists(model_path):
        import joblib
        model = joblib.load(model_path)
        model._form_analyst_artifact_path = model_path
        model._form_analyst_artifact_filename = os.path.basename(model_path)
        log.warning(
            "ML_ACTIVE_MODEL_LOADED source=filesystem_fallback active_algorithm=%s artifact_path=%s artifact_filename=%s training_run_id=%s training_date=%s model_version=%s feature_count=%s selected_overall_champion=%s selection_metrics=%s class=%s",
            getattr(model, '_form_analyst_model_type', type(model).__name__), model_path,
            os.path.basename(model_path), getattr(model, '_form_analyst_training_run_id', None),
            getattr(model, '_form_analyst_training_date', None), getattr(model, '_form_analyst_model_version', None),
            len(_model_feature_names(model) or []), getattr(model, '_form_analyst_is_active', False),
            getattr(model, '_form_analyst_selection_metrics', {}), type(model).__name__,
        )
        return model

    raise FileNotFoundError("No trained model found. Run backtest.py first.")


def _display_algorithm(model):
    """Return the production algorithm label from active model metadata."""
    raw_name = getattr(model, '_form_analyst_model_name', None)
    if raw_name:
        return raw_name

    raw_type = str(getattr(model, '_form_analyst_model_type', '') or '').lower()
    labels = {
        'random_forest': 'Random Forest',
        'catboost': 'CatBoost',
        'lightgbm': 'LightGBM',
        'xgboost': 'XGBoost',
        'ensemble': 'Ensemble',
    }
    for key, label in labels.items():
        if key in raw_type:
            return label

    class_name = type(model).__name__.lower()
    if 'catboost' in class_name:
        return 'CatBoost'
    if 'lgbm' in class_name or 'lightgbm' in class_name:
        return 'LightGBM'
    if 'xgb' in class_name or 'xgboost' in class_name:
        return 'XGBoost'
    if 'ensemble' in class_name or 'voting' in class_name or 'stacking' in class_name:
        return 'Ensemble'
    if 'forest' in class_name:
        return 'Random Forest'
    return type(model).__name__


def active_production_model_metadata(emit_log=False):
    """Inspect the exact artifact loaded by production ML inference.

    This deliberately calls ``load_model()`` so the website and startup audit use
    the same DB-first / filesystem-fallback path as live prediction scoring.
    """
    model = load_model()
    feature_names = _model_feature_names(model) or []
    expected_feature_count = getattr(model, '_form_analyst_expected_feature_count', None) or len(feature_names) or None
    selected_overall = bool(getattr(model, '_form_analyst_is_active', False))
    metadata = {
        'active_algorithm': _display_algorithm(model),
        'model_type': getattr(model, '_form_analyst_model_type', None),
        'model_artifact_filename': getattr(model, '_form_analyst_artifact_filename', None),
        'model_artifact_path': getattr(model, '_form_analyst_artifact_path', None),
        'training_backtest_run_id': getattr(model, '_form_analyst_run_id', None) or getattr(model, '_form_analyst_training_run_id', None),
        'training_date': str(getattr(model, '_form_analyst_training_date', '') or '') or None,
        'expected_feature_count': expected_feature_count,
        'model_version': getattr(model, '_form_analyst_model_version', None),
        'selected_overall_champion': selected_overall,
        'champion_status': 'Selected overall champion' if selected_overall else 'Best model for its algorithm type / fallback artifact',
        'model_class': type(model).__name__,
    }
    if emit_log:
        log.info(
            "ML_ACTIVE_PRODUCTION_MODEL_AUDIT active_algorithm=%s artifact_filename=%s artifact_path=%s training_run_id=%s training_date=%s expected_feature_count=%s model_version=%s selected_overall_champion=%s champion_status=%s model_class=%s",
            metadata['active_algorithm'], metadata['model_artifact_filename'], metadata['model_artifact_path'],
            metadata['training_backtest_run_id'], metadata['training_date'], metadata['expected_feature_count'],
            metadata['model_version'], metadata['selected_overall_champion'], metadata['champion_status'],
            metadata['model_class'],
        )
    return metadata


def _predict_raw_scores(model, X):
    """Return raw model scores and the prediction method used."""
    if hasattr(model, 'predict_proba'):
        probabilities = model.predict_proba(X)
        return probabilities[:, 1], 'predict_proba'

    return model.predict(X), 'predict'


def predict_meeting(meeting_id, db_session, strike_rate_data=None):
    """
    Generate ML scores for all non-scratched horses in a meeting.

    Args:
        meeting_id: int
        db_session: SQLAlchemy session
        strike_rate_data: optional dict {'jockeys': {...}, 'trainers': {...}}

    Returns:
        dict {horse_id: ml_score}  — higher = model likes this horse more
        Also returns {race_id: {horse_id: ml_score}} for per-race ranking
    """
    from models import Meeting, Race, Horse

    meeting = db_session.query(Meeting).get(meeting_id)
    if not meeting:
        log.error(f"Meeting {meeting_id} not found.")
        return {}, {}

    try:
        model = load_model()
    except FileNotFoundError as e:
        log.error(str(e))
        return {}, {}
    model_features = _model_feature_names(model) or FEATURE_NAMES
    log.info(
        "ML_PREDICTION_ACTIVE_MODEL meeting=%s model_id=%s training_run_id=%s active_algorithm=%s model_name=%s active=%s class=%s has_predict_proba=%s feature_count=%s artifact_path=%s model_version=%s selection_metrics=%s",
        meeting_id,
        getattr(model, '_form_analyst_model_id', None),
        getattr(model, '_form_analyst_run_id', None),
        getattr(model, '_form_analyst_model_type', None),
        getattr(model, '_form_analyst_model_name', None),
        getattr(model, '_form_analyst_is_active', None),
        type(model).__name__,
        hasattr(model, 'predict_proba'),
        len(model_features),
        getattr(model, '_form_analyst_artifact_path', None),
        getattr(model, '_form_analyst_model_version', None),
        getattr(model, '_form_analyst_selection_metrics', {}),
    )

    jockey_sr  = (strike_rate_data or {}).get('jockeys', {})
    trainer_sr = (strike_rate_data or {}).get('trainers', {})

    all_scores   = {}   # horse_id -> ml_score
    by_race      = {}   # race_id  -> {horse_id: ml_score}

    races = db_session.query(Race).filter_by(meeting_id=meeting_id).all()

    for race in races:
        active_horses = [h for h in race.horses if not h.is_scratched]
        if not active_horses:
            continue

        weights = []
        for h in active_horses:
            cd = h.csv_data or {}
            try:
                w = float(cd.get('horse weight', 0) or 0)
                if 49 <= w <= 65:
                    weights.append(w)
            except Exception:
                pass
        race_avg_weight = sum(weights) / len(weights) if weights else 55.0

        feature_rows = []
        horse_ids    = []

        for horse in active_horses:
            cd = horse.csv_data or {}
            try:
                feats = extract_features(cd, race.track_condition, jockey_sr, trainer_sr)

                curr_w = feats['horse_weight']
                feats['weight_vs_avg'] = (race_avg_weight - curr_w) if 49 <= curr_w <= 65 else 0.0

                feature_rows.append(feats)
                horse_ids.append(horse.id)
            except Exception as ex:
                log.warning(f"Feature extraction failed for horse {horse.id}: {ex}")
                continue

        if not feature_rows:
            continue

        feature_rows = add_race_relative_features(feature_rows)
        X_raw = pd.DataFrame(feature_rows)
        X = X_raw.reindex(columns=model_features, fill_value=0)
        _log_live_feature_audit(model, meeting_id, race, feature_rows, X_raw, X)
        X = X.fillna(0)

        try:
            _log_prediction_feature_diagnostics(model, meeting_id, race, X_raw)
            raw_preds, prediction_method = _predict_raw_scores(model, X)
        except Exception as ex:
            log.error(f"Model prediction failed for race {race.race_number}: {ex}")
            continue

        min_p = raw_preds.min()
        max_p = raw_preds.max()
        if max_p > min_p:
            normalised = ((raw_preds - min_p) / (max_p - min_p)) * 100
        else:
            normalised = np.full_like(raw_preds, 50.0)
            log.warning(
                "ML predict race %s meeting %s assigned constant 50.0 scores because raw prediction min equals max (%s)",
                race.race_number, meeting_id, min_p,
            )
        log.info(
            "ML predict race %s meeting %s method=%s raw_min=%s raw_max=%s normalised_min=%s normalised_max=%s runners=%s",
            race.race_number,
            meeting_id,
            prediction_method,
            float(min_p),
            float(max_p),
            float(normalised.min()),
            float(normalised.max()),
            len(horse_ids),
        )

        race_scores = {}
        for horse_id, score in zip(horse_ids, normalised):
            ml_score = round(float(score), 2)
            all_scores[horse_id]  = ml_score
            race_scores[horse_id] = ml_score

        by_race[race.id] = race_scores

    return all_scores, by_race
