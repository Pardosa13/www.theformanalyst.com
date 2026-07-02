"""
ml_predict.py - Shadow ML scoring using the trained Random Forest pkl.

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
import numpy as np
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

def normalize_name(name):
    if not name:
        return ''
    return re.sub(r'\s+', ' ', str(name).lower().strip())

def get_sr_win_pct(name, sr_lookup):
    if not name or not sr_lookup:
        return -1.0
    key = normalize_name(name)
    data = sr_lookup.get(key)
    if not data:
        return -1.0
    runs = data.get('L100Runs', 0)
    wins = data.get('L100Wins', 0)
    if runs < 10:
        return -1.0
    return (wins / runs) * 100.0

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

# ── In-memory model cache ─────────────────────────────────────────────────────
# Stores the last loaded model together with the fingerprint that was current
# when it was loaded.  ``_cache_fingerprint`` is either:
#   • an (id, run_date, updated_at) tuple when the model came from Postgres, or
#   • the file mtime (float) when it came from the local filesystem.
_cached_model = None
_cache_fingerprint = None


def _db_fingerprint(db_url: str):
    """
    Return (id, run_date, updated_at) for the most-recent row in
    backtest_best_model, or None if the table is empty / unreachable.
    """
    try:
        from sqlalchemy import create_engine, text
        if db_url.startswith('postgres://'):
            db_url = db_url.replace('postgres://', 'postgresql://', 1)
        eng = create_engine(db_url, pool_pre_ping=True)
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT id, run_date, updated_at FROM backtest_best_model "
                "ORDER BY run_date DESC, updated_at DESC, id DESC LIMIT 1"
            )).fetchone()
            if row:
                return (row[0], row[1], row[2])
    except Exception as e:
        log.debug(f"Could not query model fingerprint from DB: {e}")
    return None


def load_model():
    """
    Return the trained Random Forest model, using a module-level cache.

    The cache is invalidated automatically whenever the active artifact in
    Postgres changes (detected via the ``run_date`` / ``updated_at`` columns)
    or, for the filesystem path, whenever the ``.pkl`` file is modified.
    A fresh load is performed only on the first call and after a change is
    detected; all other calls return the cached object immediately.
    """
    global _cached_model, _cache_fingerprint

    model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models', 'form_analyst_best.pkl')

    # ── Filesystem path ───────────────────────────────────────────────────────
    if os.path.exists(model_path):
        import joblib
        mtime = os.path.getmtime(model_path)
        if _cached_model is not None and _cache_fingerprint == mtime:
            log.debug("Returning cached model (filesystem, mtime unchanged).")
            return _cached_model
        log.info(f"Loading model from filesystem (mtime changed or first load): {model_path}")
        _cached_model = joblib.load(model_path)
        _cache_fingerprint = mtime
        return _cached_model

    # ── Postgres path ─────────────────────────────────────────────────────────
    db_url = os.environ.get('DATABASE_URL', '')
    if db_url.startswith('postgres://'):
        db_url = db_url.replace('postgres://', 'postgresql://', 1)

    # Lightweight fingerprint check — avoids a full pkl download when unchanged.
    fp = _db_fingerprint(db_url)
    if fp is not None and _cached_model is not None and _cache_fingerprint == fp:
        log.debug(f"Returning cached model (DB artifact unchanged, fingerprint={fp}).")
        return _cached_model

    log.info(f"Loading model from DB (artifact changed or first load, fingerprint={fp}).")
    try:
        from sqlalchemy import create_engine, text
        import io, joblib
        eng = create_engine(db_url, pool_pre_ping=True)
        with eng.connect() as conn:
            row = conn.execute(text(
                "SELECT pkl_data FROM backtest_best_model ORDER BY run_date DESC, updated_at DESC, id DESC LIMIT 1"
            )).fetchone()
            if row and row[0]:
                _cached_model = joblib.load(io.BytesIO(bytes(row[0])))
                _cache_fingerprint = fp
                return _cached_model
    except Exception as e:
        log.warning(f"Could not load model from DB: {e}")

    raise FileNotFoundError("No trained model found. Run backtest.py first.")

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

        X = pd.DataFrame(feature_rows, columns=FEATURE_NAMES)
        X = X.reindex(columns=FEATURE_NAMES)
        X = X.fillna(X.median())

        try:
            raw_preds = model.predict(X)
        except Exception as ex:
            log.error(f"Model prediction failed for race {race.race_number}: {ex}")
            continue

        min_p = raw_preds.min()
        max_p = raw_preds.max()
        if max_p > min_p:
            normalised = ((raw_preds - min_p) / (max_p - min_p)) * 100
        else:
            normalised = np.full_like(raw_preds, 50.0)

        race_scores = {}
        for horse_id, score in zip(horse_ids, normalised):
            ml_score = round(float(score), 2)
            all_scores[horse_id]  = ml_score
            race_scores[horse_id] = ml_score

        by_race[race.id] = race_scores

    log.info(f"ML scores generated for {len(all_scores)} horses across {len(by_race)} races in meeting {meeting_id}")
    return all_scores, by_race
