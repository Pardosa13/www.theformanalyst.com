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
from collections import Counter, defaultdict
import numpy as np
from strike_rate_matching import build_strike_rate_lookup, get_sr_win_pct, normalize_name
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
    # Mirror backtest.parse_last10 exactly: a MISSING last10 string returns {}
    # so the l10_*/l5_* features are simply absent from that horse's row and
    # get the training-median fill downstream — the same treatment training
    # rows get — rather than hardcoded zeros the model never saw at fit time.
    if not last10_str:
        return {}
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
    bm = re.search(r'(?:Benchmark|Bench\.?|BM)\s*(\d+)', s, re.IGNORECASE)
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


# ── Helpers for the 2026-07 audit features (mirror backtest.py exactly) ──────
# PuntingForm uses 900/25 as "no data" sentinels for prices/ranks.
PF_SENTINEL_PRICE = 900.0
PF_SENTINEL_RANK = 25.0
# runStyle strings in ratings_json, mapped front-runner-high like the existing
# running_position feature ('ldr' leader ... 'bm' backmarker).
PF_RUN_STYLE_MAP = {'ldr': 5.0, 'ld': 5.0, 'onp': 4.0, 'mid': 3.0, 'off': 2.0, 'bm': 1.0}

# A sire/dam needs at least this many prior runs before its win rate is used.
MIN_BREEDING_RUNS_FOR_RATE = 3


def _pf_price(value):
    """PuntingForm price, or NaN for the 900 'no data' sentinel/invalid."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return np.nan
    return np.nan if (v <= 0 or v >= PF_SENTINEL_PRICE) else v


def _pf_rank(value):
    """PuntingForm rank, or NaN for the 25 'no data' sentinel/invalid."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return np.nan
    return np.nan if (v <= 0 or v >= PF_SENTINEL_RANK) else v


def parse_form_time_seconds(value):
    """Parse a race time like '01:23.45' into seconds. None when unusable."""
    if not value:
        return None
    m = re.match(r'(\d+):(\d+(?:\.\d+)?)', str(value).strip())
    if not m:
        return None
    seconds = int(m.group(1)) * 60 + float(m.group(2))
    return seconds if seconds > 10 else None


def extract_features(cd, track_condition, jockey_sr_lookup=None, trainer_sr_lookup=None,
                     rail_position=None, pf_ratings_lookup=None, pf_speedmaps_lookup=None,
                     jockey_extra_lookup=None, trainer_extra_lookup=None,
                     sire_rates=None, dam_rates=None):
    """
    Extract the same raw features backtest.py trains on.
    cd = horse.csv_data dict
    track_condition = race.track_condition string
    rail_position = meetings.rail_position for this meeting (metres out, 0 = true)
    pf_ratings_lookup / pf_speedmaps_lookup = per-runner dicts keyed by
        (PuntingForm raceId, tabNo), parsed from races.ratings_json /
        races.speed_maps_json
    jockey_extra_lookup / trainer_extra_lookup = normalised-name dicts of
        career/L100 actual-vs-expected extras from strike_rates
    sire_rates / dam_rates = normalised-name -> historical progeny win rate
        (only names with >= MIN_BREEDING_RUNS_FOR_RATE prior runs are present)

    The 2026-07 audit features default to NaN — not 0 — when their source is
    missing; _fill_missing_features() then imputes them with the training-split
    medians persisted on the model artifact, matching training behaviour.
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
        # Same guard as backtest.extract_features (no upper bound on current
        # weight) so training and live values match on heavy-weight carriers.
        features['weight_change'] = (cw - lw) if (49 <= lw <= 65 and cw >= 49) else 0.0
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

    # ═══ 2026-07 audit features (mirrors backtest.py extract_features) ═══

    # ── Last-start speed from 'form time' + 'form distance' ──
    form_seconds = parse_form_time_seconds(cd.get('form time'))
    try:
        form_dist = float(cd.get('form distance', 0) or 0)
    except Exception:
        form_dist = 0.0
    features['form_speed_mps'] = np.nan
    if form_seconds and 400 <= form_dist <= 4000:
        metres_per_second = form_dist / form_seconds
        if 12 <= metres_per_second <= 22:
            features['form_speed_mps'] = metres_per_second

    # ── Race-card context (race number, start hour, weight type) ──
    try:
        features['race_number'] = float(cd.get('race number') or np.nan)
    except Exception:
        features['race_number'] = np.nan
    start_time_match = re.search(r'(\d{1,2}):(\d{2})', str(cd.get('start time') or ''))
    features['start_hour'] = (
        float(start_time_match.group(1)) + float(start_time_match.group(2)) / 60.0
        if start_time_match else np.nan
    )
    features['is_handicap'] = 1.0 if 'handicap' in str(cd.get('weight type', '')).lower() else 0.0

    # ── Race restrictions (age/sex) ──
    age_restriction = str(cd.get('age restrictions') or '')
    age_match = re.match(r'(\d+)', age_restriction)
    features['race_min_age'] = float(age_match.group(1)) if age_match else np.nan
    features['race_age_open'] = 1.0 if '+' in age_restriction else 0.0
    sex_restriction = str(cd.get('sex restrictions') or '').strip().lower()
    features['race_sex_restricted'] = 1.0 if sex_restriction and sex_restriction not in ('no', 'none', 'nan') else 0.0

    # ── Per-condition records regardless of today's condition ──
    for cond_name in ('good', 'soft', 'heavy'):
        features[f'rec_{cond_name}_win_rate'] = win_rate(cd.get(f'horse record {cond_name}', ''))
    soft_runs, _, _, _ = parse_record(cd.get('horse record soft', ''))
    heavy_runs, _, _, _ = parse_record(cd.get('horse record heavy', ''))
    features['wet_track_runs'] = float(soft_runs + heavy_runs)

    # ── Tab number ──
    try:
        features['tab_number'] = float(cd.get('horse number') or np.nan)
    except Exception:
        features['tab_number'] = np.nan

    # ── Rail position (meetings.rail_position) ──
    try:
        features['rail_position'] = float(rail_position) if rail_position is not None else np.nan
    except Exception:
        features['rail_position'] = np.nan

    # ── Sectional time PRICES (ranks are used above; prices are separate) ──
    for n in (200, 400, 600):
        features[f'last{n}_price'] = _pf_price(cd.get(f'last{n}timeprice', cd.get(f'last{n}TimePrice')))

    # ── PuntingForm ratings_json per-runner features ──
    pf_race_id = None
    tab_no = None
    try:
        pf_race_id = int(cd.get('race id'))
        tab_no = int(cd.get('horse number'))
    except Exception:
        pass
    rating = (pf_ratings_lookup or {}).get((pf_race_id, tab_no)) if pf_race_id else None
    if rating:
        features['pf_time_rank'] = _pf_rank(rating.get('timeRank'))
        features['pf_time_price'] = _pf_price(rating.get('timePrice'))
        features['pf_early_time_rank'] = _pf_rank(rating.get('earlyTimeRank'))
        features['pf_weight_class_rank'] = _pf_rank(rating.get('weightClassRank'))
        features['pf_adj_weight_class_rank'] = _pf_rank(rating.get('timeAdjustedWeightClassRank'))
        features['pf_class_change'] = float(rating.get('classChange') or 0)
        predicted_settle = rating.get('predictedSettlePostion')
        features['pf_predicted_settle'] = float(predicted_settle) if predicted_settle not in (None, 0, 25) else np.nan
        avg_settle = rating.get('averageHistoricalSettlePosition')
        features['pf_avg_hist_settle'] = float(avg_settle) if avg_settle not in (None, 0, 101) else np.nan
        features['pf_run_style'] = PF_RUN_STYLE_MAP.get(str(rating.get('runStyle') or '').strip().lower(), np.nan)
        features['pf_is_reliable'] = 1.0 if rating.get('isReliable') else 0.0
        features['pfai_price'] = _pf_price(rating.get('pfaiPrice'))
        features['pfai_rank'] = _pf_rank(rating.get('pfaiRank'))
    else:
        for key in ('pf_time_rank', 'pf_time_price', 'pf_early_time_rank', 'pf_weight_class_rank',
                    'pf_adj_weight_class_rank', 'pf_class_change', 'pf_predicted_settle',
                    'pf_avg_hist_settle', 'pf_run_style'):
            features[key] = np.nan
        features['pf_is_reliable'] = 0.0
        features['pfai_price'] = np.nan
        features['pfai_rank'] = np.nan

    # ── PuntingForm speed_maps_json per-runner features ──
    speedmap = (pf_speedmaps_lookup or {}).get((pf_race_id, tab_no)) if pf_race_id else None
    if speedmap:
        features['sm_assessed_price'] = _pf_price(speedmap.get('assessedPrice'))
        features['sm_speed'] = float(speedmap.get('speed') or 0)
        settle = speedmap.get('settle')
        features['sm_settle'] = float(settle) if settle not in (None, 25) else np.nan
        features['sm_map_a2e'] = float(speedmap.get('mapA2E') or 0) or np.nan
        jockey_a2e = speedmap.get('jockeyA2E')
        features['sm_jockey_a2e'] = float(jockey_a2e) if jockey_a2e not in (None, 0) else np.nan
        features['sm_rated_run_style'] = float(speedmap.get('ratedRunStyle') or 0)
        features['sm_rated_settle'] = float(speedmap.get('ratedSettle') or 0) or np.nan
    else:
        for key in ('sm_assessed_price', 'sm_speed', 'sm_settle', 'sm_map_a2e',
                    'sm_jockey_a2e', 'sm_rated_run_style', 'sm_rated_settle'):
            features[key] = np.nan
    assessed = features.get('sm_assessed_price')
    features['sm_assessed_prob'] = (1.0 / assessed) if (assessed and np.isfinite(assessed) and assessed > 1) else np.nan

    # ── Jockey/trainer career & L100 actual-vs-expected (strike_rates extras) ──
    jockey_name_norm = normalize_name(str(cd.get('horse jockey', '') or '').strip())
    trainer_name_norm = normalize_name(str(cd.get('horse trainer', '') or '').strip())
    jockey_extra = (jockey_extra_lookup or {}).get(jockey_name_norm)
    trainer_extra = (trainer_extra_lookup or {}).get(trainer_name_norm)
    for prefix, extra in (('jockey', jockey_extra), ('trainer', trainer_extra)):
        features[f'{prefix}_career_a2e'] = float(extra['career_a2e']) if extra and extra.get('career_a2e') is not None else np.nan
        features[f'{prefix}_l100_a2e'] = float(extra['l100_a2e']) if extra and extra.get('l100_a2e') is not None else np.nan
        features[f'{prefix}_career_runs'] = float(extra['career_runs']) if extra and extra.get('career_runs') else np.nan

    # ── Sire/dam historical progeny win rates ──
    # At live-scoring time every recorded result is strictly earlier than the
    # race being scored, so a plain aggregate over recorded results matches the
    # strictly-earlier point-in-time accumulation used in training.
    sire_name = normalize_name(str(cd.get('horse sire') or ''))
    dam_name = normalize_name(str(cd.get('horse dam') or ''))
    sire_rate = (sire_rates or {}).get(sire_name) if sire_name else None
    dam_rate = (dam_rates or {}).get(dam_name) if dam_name else None
    features['sire_win_rate'] = float(sire_rate) if sire_rate is not None else np.nan
    features['dam_win_rate'] = float(dam_rate) if dam_rate is not None else np.nan

    return features

# Race-relative derivatives (rank within race + delta vs race average) for the
# 2026-07 audit features. Only rank/vs_avg are generated for these — exactly
# the derivative set that was holdout-validated — unlike relative_cols below
# which also get vs_best/percentile. Mirrors backtest.py.
NEW_RELATIVE_COLS = [
    'form_speed_mps', 'pf_time_rank', 'pf_weight_class_rank', 'pfai_price',
    'sm_assessed_prob', 'sm_settle', 'pf_early_time_rank',
]
NEW_RELATIVE_LOWER_IS_BETTER = {
    'pf_time_rank', 'pf_weight_class_rank', 'pfai_price', 'sm_settle',
    'pf_early_time_rank',
}


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

    for col in NEW_RELATIVE_COLS:
        if col not in temp.columns:
            continue
        ascending = col in NEW_RELATIVE_LOWER_IS_BETTER
        temp[f'{col}_race_rank'] = temp[col].rank(method='min', ascending=ascending)
        temp[f'{col}_vs_race_avg'] = temp[col] - temp[col].mean()

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
    'jockeys_can_claim',
    # 2026-07 audit features (same insertion order as backtest.extract_features)
    'form_speed_mps', 'race_number', 'start_hour', 'is_handicap',
    'race_min_age', 'race_age_open', 'race_sex_restricted',
    'rec_good_win_rate', 'rec_soft_win_rate', 'rec_heavy_win_rate',
    'wet_track_runs', 'tab_number', 'rail_position',
    'last200_price', 'last400_price', 'last600_price',
    'pf_time_rank', 'pf_time_price', 'pf_early_time_rank', 'pf_weight_class_rank',
    'pf_adj_weight_class_rank', 'pf_class_change', 'pf_predicted_settle',
    'pf_avg_hist_settle', 'pf_run_style', 'pf_is_reliable', 'pfai_price', 'pfai_rank',
    'sm_assessed_price', 'sm_speed', 'sm_settle', 'sm_map_a2e',
    'sm_jockey_a2e', 'sm_rated_run_style', 'sm_rated_settle', 'sm_assessed_prob',
    'jockey_career_a2e', 'jockey_l100_a2e', 'jockey_career_runs',
    'trainer_career_a2e', 'trainer_l100_a2e', 'trainer_career_runs',
    'sire_win_rate', 'dam_win_rate',
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

NEW_RELATIVE_FEATURES = []
for col in NEW_RELATIVE_COLS:
    NEW_RELATIVE_FEATURES.extend([f'{col}_race_rank', f'{col}_vs_race_avg'])

# field_size sits between the raw features and the race-relative derivatives —
# the same column order build_training_set()/add_race_relative_features()
# produce in backtest.py, so this list can stand in for a training contract.
FEATURE_NAMES = FEATURE_NAMES + ['field_size'] + RACE_RELATIVE_FEATURES + NEW_RELATIVE_FEATURES


def _model_feature_names(model):
    """Return feature names persisted on the trained estimator, if available."""
    names = getattr(model, 'feature_names_in_', None)
    if names is None:
        names = getattr(model, '_form_analyst_expected_features', None)
    if names is None:
        return None
    return [str(name) for name in list(names)]


def _fill_missing_features(model, X):
    """Fill missing/null feature values the same way training did.

    Training (backtest.py) imputes NaNs with each feature's training-split
    median and persists that exact median dict on the model as
    _form_analyst_feature_medians. Previously live scoring filled every gap
    with a hardcoded 0 regardless of what training did, which is a real
    train/serve skew: 0 is rarely close to the median for features like
    last_sp, jockey_sr, or days_since_run, so any horse missing one of those
    fields got fed an out-of-distribution value the model never saw zero-filled
    at train time. Falls back to 0 only for models saved before this fix (no
    stored medians) or for a feature the stored medians don't cover.
    """
    feature_medians = getattr(model, '_form_analyst_feature_medians', None) or {}
    if feature_medians:
        X = X.fillna(pd.Series(feature_medians))
    return X.fillna(0)


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
        # Live extraction may generate a SUPERSET of an older artifact's stored
        # contract (e.g. the 204-feature generator scoring a 146-feature
        # champion): extra generated columns are simply dropped by the reindex
        # to the stored contract, so they are reported (extra_features /
        # extra_features_empty) but do NOT fail the contract. What must still
        # hold exactly: every stored feature is generated, and the final matrix
        # equals the stored contract in names and order.
        extra_features = [name for name in raw_features if name not in stored_features]
        names_match = not missing_features and set(final_features) == set(stored_features)
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
    same named columns in the same order and default missing/null values using
    the same stored training medians (see _fill_missing_features), not 0.
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

    has_stored_medians = bool(getattr(model, '_form_analyst_feature_medians', None))
    log.info(
        "ML_FEATURE_AUDIT meeting=%s race=%s model_id=%s status=passed feature_count=%s order_matches=%s "
        "preprocessing=training_fillna_median live_fillna=%s scaling=none",
        meeting_id, getattr(race, 'race_number', None), model_id, final_X.shape[1], contract['order_matches'],
        'stored_training_median_fallback_zero' if has_stored_medians else 'zero_no_stored_medians_on_this_artifact',
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


# ── Live lookups for the 2026-07 audit features ──────────────────────────────

def _load_live_strike_rate_lookups(db_session):
    """Load jockey/trainer strike-rate lookups + A2E extras from strike_rates.

    Mirrors backtest.load_strike_rate_data()'s live-scoring (current snapshot)
    path: at prediction time "today's" snapshot is the correct point-in-time
    value, unlike training where dated history is needed.
    Returns {'jockeys': ..., 'trainers': ..., 'jockeys_extra': ..., 'trainers_extra': ...}
    with empty dicts for anything that fails to load.
    """
    from sqlalchemy import text

    lookups = {'jockeys': {}, 'trainers': {}, 'jockeys_extra': {}, 'trainers_extra': {}}
    for sr_type, lookup_key, extra_key in (('jockey', 'jockeys', 'jockeys_extra'),
                                           ('trainer', 'trainers', 'trainers_extra')):
        try:
            rows = db_session.execute(text("""
                SELECT name, l100_wins, l100_runs,
                       career_actual_to_expected, last100_actual_to_expected, career_runs
                FROM strike_rates
                WHERE type = :sr_type
                ORDER BY updated_at DESC
            """), {'sr_type': sr_type}).fetchall()
        except Exception as e:
            log.warning("Could not load %s strike-rate data for live scoring (%s_sr and "
                        "%s A2E features will use their unmatched/median fallbacks): %s",
                        sr_type, sr_type, sr_type, e)
            continue
        lookups[lookup_key] = build_strike_rate_lookup([(r[0], r[1], r[2]) for r in rows])
        extra = {}
        for name, _wins, _runs, career_a2e, l100_a2e, career_runs in rows:
            norm = normalize_name(str(name or ''))
            if norm and norm not in extra:
                extra[norm] = {'career_a2e': career_a2e, 'l100_a2e': l100_a2e,
                               'career_runs': career_runs}
        lookups[extra_key] = extra
        log.info("Live scoring loaded %s %s strike-rate records (with A2E extras).",
                 len(rows), sr_type)
    return lookups


def _load_pf_race_lookups_for_meeting(races):
    """Parse ratings_json/speed_maps_json off this meeting's race rows into
    per-runner lookups keyed by (PuntingForm raceId, tabNo) — the same ids
    csv_data carries as 'race id' / 'horse number'. Mirrors
    backtest.load_pf_race_lookups() but scoped to one meeting's races.
    """
    ratings_lookup = {}
    speedmaps_lookup = {}
    for race in races:
        try:
            d = getattr(race, 'ratings_json', None)
            while isinstance(d, str):
                d = json.loads(d)
            for item in ((d or {}).get('payLoad') or []):
                ratings_lookup[(item.get('raceId'), item.get('tabNo'))] = item
        except Exception:
            pass
        try:
            d = getattr(race, 'speed_maps_json', None)
            while isinstance(d, str):
                d = json.loads(d)
            for pf_race in ((d or {}).get('payLoad') or []):
                rid = pf_race.get('raceId')
                for item in (pf_race.get('items') or []):
                    speedmaps_lookup[(rid, item.get('tabNo'))] = item
        except Exception:
            pass
    return ratings_lookup, speedmaps_lookup


def _load_breeding_win_rates(db_session):
    """Historical sire/dam progeny win rates from recorded results.

    Aggregates over the same row population training uses (unscratched runners
    with a recorded finish and a real SP > 1.0). Every recorded result is
    strictly earlier than the race being scored, so this matches the
    strictly-earlier accumulation in backtest._fill_point_in_time_breeding_rates.
    Names with fewer than MIN_BREEDING_RUNS_FOR_RATE runs are omitted (feature
    stays NaN -> training-median fill). Returns (sire_rates, dam_rates).
    """
    from sqlalchemy import text

    rates = []
    for key in ('horse sire', 'horse dam'):
        merged = defaultdict(lambda: [0, 0])  # norm name -> [runs, wins]
        try:
            rows = db_session.execute(text(f"""
                SELECT h.csv_data->>'{key}' AS name,
                       COUNT(*) AS runs,
                       SUM(CASE WHEN r.finish_position = 1 THEN 1 ELSE 0 END) AS wins
                FROM horses h
                JOIN results r ON r.horse_id = h.id
                WHERE r.finish_position > 0
                  AND r.sp IS NOT NULL AND r.sp > 1.0
                  AND COALESCE(h.is_scratched, FALSE) = FALSE
                  AND h.csv_data->>'{key}' IS NOT NULL
                GROUP BY 1
            """)).fetchall()
        except Exception as e:
            log.warning("Could not load %s progeny win rates for live scoring "
                        "(feature stays NaN -> training-median fill): %s", key, e)
            rates.append({})
            continue
        for name, runs, wins in rows:
            norm = normalize_name(str(name or ''))
            if norm:
                merged[norm][0] += int(runs or 0)
                merged[norm][1] += int(wins or 0)
        rates.append({
            norm: wins / runs
            for norm, (runs, wins) in merged.items()
            if runs >= MIN_BREEDING_RUNS_FOR_RATE
        })
    return rates[0], rates[1]


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

    strike_rate_data = dict(strike_rate_data or {})
    # Anything the caller didn't supply is loaded from the DB directly, so the
    # jockey_sr/trainer_sr features and the strike_rates A2E extras are always
    # computed from real current-snapshot data instead of silently defaulting.
    if not all(strike_rate_data.get(key) for key in
               ('jockeys', 'trainers', 'jockeys_extra', 'trainers_extra')):
        for key, value in _load_live_strike_rate_lookups(db_session).items():
            if not strike_rate_data.get(key):
                strike_rate_data[key] = value
    jockey_sr  = strike_rate_data.get('jockeys', {})
    trainer_sr = strike_rate_data.get('trainers', {})
    jockey_extra  = strike_rate_data.get('jockeys_extra', {})
    trainer_extra = strike_rate_data.get('trainers_extra', {})

    rail_position = getattr(meeting, 'rail_position', None)
    sire_rates, dam_rates = _load_breeding_win_rates(db_session)

    all_scores   = {}   # horse_id -> ml_score
    by_race      = {}   # race_id  -> {horse_id: ml_score}

    races = db_session.query(Race).filter_by(meeting_id=meeting_id).all()

    pf_ratings_lookup, pf_speedmaps_lookup = _load_pf_race_lookups_for_meeting(races)
    log.info(
        "ML_PREDICTION_LIVE_LOOKUPS meeting=%s rail_position=%s pf_ratings_entries=%s "
        "pf_speedmap_entries=%s jockey_sr_loaded=%s trainer_sr_loaded=%s "
        "jockey_extras=%s trainer_extras=%s sire_rates=%s dam_rates=%s",
        meeting_id, rail_position, len(pf_ratings_lookup), len(pf_speedmaps_lookup),
        bool(jockey_sr), bool(trainer_sr), len(jockey_extra), len(trainer_extra),
        len(sire_rates), len(dam_rates),
    )

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
                feats = extract_features(
                    cd, race.track_condition, jockey_sr, trainer_sr,
                    rail_position=rail_position,
                    pf_ratings_lookup=pf_ratings_lookup,
                    pf_speedmaps_lookup=pf_speedmaps_lookup,
                    jockey_extra_lookup=jockey_extra,
                    trainer_extra_lookup=trainer_extra,
                    sire_rates=sire_rates,
                    dam_rates=dam_rates,
                )

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
        # Leave gaps as NaN here (no fill_value) so the median-based fill below
        # applies uniformly to columns X_raw is missing entirely *and* individual
        # null cells in columns it does have.
        X = X_raw.reindex(columns=model_features)
        _log_live_feature_audit(model, meeting_id, race, feature_rows, X_raw, X)
        X = _fill_missing_features(model, X)

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
