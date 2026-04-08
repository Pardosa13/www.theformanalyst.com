"""
backtest.py - Nightly backtesting and ML analysis for The Form Analyst

Runs as a Railway cron job (0 2 * * * = 2am every night)

Two parallel tracks:
  Track A: Random Forest feature importance - which raw horse features predict winners
  Track B: Component ROI analysis - which analyzer.js components help/hurt ROI

Results written to DB, viewable at /backtest in the web app.

CHANGELOG:
  2026-04-07 - Major rewrite:
    * Added weight_change feature (lastWeight - currentWeight delta)
    * Added class_change feature (today class score vs last class score)
    * Added running position distance-context features (leader_sprint, leader_staying etc.)
    * Added jockey_sr and trainer_sr as numeric features from strike rate data
    * Added weight_vs_avg feature (horse weight vs race average)
    * Added country encoding (NZ/FR/GB/JPN etc. not just is_aus_bred binary)
    * Fixed parse_components_from_notes dash regex — was mangling component names
    * Updated ANALYZER_WEIGHTS dict to match current analyzer.js scoring
    * Added career_podium_rate and career_runs to ANALYZER_WEIGHTS descriptions
    * Improved component name normalisation to reduce fragmentation across runs
    * Added avg_sp to component output for value analysis
"""

import os
import sys
import json
import re
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ML
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import mean_squared_error
import warnings
warnings.filterwarnings('ignore')

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# DB CONNECTION
# ─────────────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    log.error("DATABASE_URL not set. Exiting.")
    sys.exit(1)

if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine)


# ─────────────────────────────────────────────
# ENSURE BACKTEST TABLES EXIST
# ─────────────────────────────────────────────
def ensure_tables():
    """Create backtest tables if they don't exist yet."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id SERIAL PRIMARY KEY,
                started_at TIMESTAMP DEFAULT NOW(),
                completed_at TIMESTAMP,
                status VARCHAR(20) DEFAULT 'running',
                total_races INTEGER,
                total_horses INTEGER,
                baseline_roi FLOAT,
                baseline_strike_rate FLOAT,
                notes TEXT
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS backtest_feature_importance (
                id SERIAL PRIMARY KEY,
                run_id INTEGER REFERENCES backtest_runs(id),
                feature_name VARCHAR(100),
                importance_score FLOAT,
                importance_rank INTEGER,
                current_analyzer_weight VARCHAR(200),
                recommendation TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS backtest_component_analysis (
                id SERIAL PRIMARY KEY,
                run_id INTEGER REFERENCES backtest_runs(id),
                component_name VARCHAR(200),
                appearances INTEGER,
                wins INTEGER,
                strike_rate FLOAT,
                roi FLOAT,
                avg_sp FLOAT,
                current_value FLOAT,
                suggested_value FLOAT,
                roi_delta FLOAT,
                verdict VARCHAR(20),
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS backtest_momentum_analysis (
                id SERIAL PRIMARY KEY,
                run_id INTEGER REFERENCES backtest_runs(id),
                trajectory VARCHAR(20),
                appearances INTEGER,
                wins INTEGER,
                strike_rate FLOAT,
                roi FLOAT,
                avg_sp FLOAT,
                avg_slope FLOAT,
                avg_predicted_sp FLOAT,
                overlay_pct FLOAT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        conn.commit()
    log.info("Backtest tables verified.")


# ─────────────────────────────────────────────
# STEP 1: LOAD ALL HISTORICAL DATA
# ─────────────────────────────────────────────
def load_historical_data():
    """
    Pull all races that have results recorded.
    Also loads strike rate data for jockey/trainer SR features.
    Returns a DataFrame with one row per horse per race.
    """
    log.info("Loading historical data from DB...")

    query = text("""
        SELECT
            h.id AS horse_id,
            h.race_id,
            h.horse_name,
            h.csv_data,
            h.is_scratched,
            r.finish_position,
            r.sp,
            rc.track_condition,
            rc.distance AS race_distance,
            rc.race_class,
            rc.meeting_id,
            m.date AS meeting_date,
            p.score AS analyzer_score,
            p.notes AS analyzer_notes,
            p.predicted_odds
        FROM horses h
        JOIN races rc ON h.race_id = rc.id
        JOIN meetings m ON rc.meeting_id = m.id
        LEFT JOIN results r ON r.horse_id = h.id
        LEFT JOIN predictions p ON p.horse_id = h.id
        WHERE r.id IS NOT NULL
          AND r.finish_position > 0
          AND h.is_scratched = FALSE
        ORDER BY m.date ASC, rc.id ASC, h.id ASC
    """)

    with engine.connect() as conn:
        result = conn.execute(query)
        rows = result.fetchall()
        columns = result.keys()

    df = pd.DataFrame(rows, columns=columns)
    log.info(f"Loaded {len(df)} horse-race records across {df['race_id'].nunique()} races.")

    # Load strike rate data for jockey/trainer SR features
    strike_rate_data = load_strike_rate_data()

    return df, strike_rate_data


def load_strike_rate_data():
    """
    Load the most recent jockey and trainer strike rate data from the DB.
    Returns dict: {'jockeys': {name: {L100Wins, L100Runs}}, 'trainers': {...}}
    """
    log.info("Loading strike rate data...")
    sr_data = {'jockeys': {}, 'trainers': {}}

    try:
        # Try loading from strike_rates table if it exists
        with engine.connect() as conn:
            # Jockeys
            try:
                result = conn.execute(text("""
                    SELECT name, l100_wins, l100_runs
                    FROM strike_rates
                    WHERE type = 'jockey'
                    ORDER BY updated_at DESC
                """))
                for row in result:
                    name = str(row[0]).strip().lower()
                    sr_data['jockeys'][name] = {
                        'L100Wins': int(row[1] or 0),
                        'L100Runs': int(row[2] or 0)
                    }
                log.info(f"Loaded {len(sr_data['jockeys'])} jockey SR records.")
            except Exception:
                log.warning("No strike_rates table or jockey data — jockey_sr feature will be 0.")

            # Trainers
            try:
                result = conn.execute(text("""
                    SELECT name, l100_wins, l100_runs
                    FROM strike_rates
                    WHERE type = 'trainer'
                    ORDER BY updated_at DESC
                """))
                for row in result:
                    name = str(row[0]).strip().lower()
                    sr_data['trainers'][name] = {
                        'L100Wins': int(row[1] or 0),
                        'L100Runs': int(row[2] or 0)
                    }
                log.info(f"Loaded {len(sr_data['trainers'])} trainer SR records.")
            except Exception:
                log.warning("No strike_rates table or trainer data — trainer_sr feature will be 0.")

    except Exception as e:
        log.warning(f"Could not load strike rate data: {e}")

    return sr_data


# ─────────────────────────────────────────────
# STEP 2: EXTRACT FEATURES FROM CSV_DATA
# ─────────────────────────────────────────────
def parse_record(record_str):
    """Parse a record string like '10:2-1-1' into (runs, wins, seconds, thirds)."""
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
    """Extract features from last10 string like '1213x4521'."""
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
    last5 = runs_list[-5:] if len(runs_list) >= 5 else runs_list
    last10 = runs_list[-10:] if len(runs_list) >= 10 else runs_list

    l10_wins = sum(1 for x in last10 if x == 1)
    l10_places = sum(1 for x in last10 if x in [1, 2, 3])
    l5_wins = sum(1 for x in last5 if x == 1)
    l5_places = sum(1 for x in last5 if x in [1, 2, 3])

    # Form trend: recent half vs early half win rate delta
    if len(runs_list) >= 4:
        mid = len(runs_list) // 2
        early_wr = sum(1 for x in runs_list[:mid] if x == 1) / mid
        recent_wr = sum(1 for x in runs_list[mid:] if x == 1) / (len(runs_list) - mid)
        trend = recent_wr - early_wr
    else:
        trend = 0.0

    is_first_up = 1 if s.lower().endswith('x') else 0
    is_second_up = 0
    if len(s) >= 2 and s[-2].lower() == 'x' and s[-1].isdigit():
        is_second_up = 1

    last_pos = runs_list[-1] if runs_list else 9

    return {
        'l10_runs': len(last10),
        'l10_wins': l10_wins,
        'l10_win_rate': l10_wins / len(last10) if last10 else 0,
        'l10_places': l10_places,
        'l10_place_rate': l10_places / len(last10) if last10 else 0,
        'l5_win_rate': l5_wins / len(last5) if last5 else 0,
        'l5_place_rate': l5_places / len(last5) if last5 else 0,
        'is_first_up': is_first_up,
        'is_second_up': is_second_up,
        'last_position': last_pos,
        'form_trend': trend,
    }


def parse_date_str(date_str):
    """Parse a date string in various formats. Returns datetime or None."""
    if not date_str:
        return None
    for fmt in ['%d/%m/%Y %H:%M:%S', '%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d']:
        try:
            return datetime.strptime(str(date_str).split(' ')[0], fmt.split(' ')[0])
        except Exception:
            continue
    return None


def days_since_run(meeting_date_str, form_date_str):
    """Calculate days between race date and last run date."""
    try:
        race_date = parse_date_str(meeting_date_str)
        last_date = parse_date_str(form_date_str)
        if race_date and last_date:
            return (race_date - last_date).days
    except Exception:
        pass
    return -1


def calculate_class_score(class_string, prize_string):
    """
    Mirror of analyzer.js calculateClassScore — maps class/prize to 0-130 scale.
    Used to compute class_change feature.
    """
    if not class_string:
        return 50.0

    s = str(class_string).strip()

    # Group races
    gm = re.search(r'Group\s*([123])', s, re.IGNORECASE)
    if gm:
        level = int(gm.group(1))
        return {1: 130, 2: 122, 3: 115}.get(level, 100)

    if re.search(r'Listed', s, re.IGNORECASE):
        return 108.0

    # Benchmark
    bm = re.search(r'(?:Benchmark|Bench\.?|BM)\s*(\d+)', s, re.IGNORECASE)
    if bm:
        return min(100, max(1, int(bm.group(1))))

    # Class
    cm = re.search(r'(?:Class|Cls)\s*(\d+)', s, re.IGNORECASE)
    if cm:
        fallback = {1: 40, 2: 55, 3: 65, 4: 75, 5: 85, 6: 92}
        return fallback.get(int(cm.group(1)), 60)

    # Maiden
    if re.search(r'Maiden|Mdn', s, re.IGNORECASE):
        return 50.0

    # Prize money fallback
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
    """Normalize a jockey/trainer name for lookup."""
    if not name:
        return ''
    return re.sub(r'\s+', ' ', str(name).lower().strip())


def get_sr_win_pct(name, sr_lookup):
    """Look up L100 win % for a jockey or trainer. Returns float 0-100."""
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


def extract_features(row, jockey_sr_lookup=None, trainer_sr_lookup=None):
    """
    Extract ML features from a horse's csv_data dict.
    Returns a flat dict of ~45 features covering everything scored in analyzer.js.
    """
    cd = row.get('csv_data') or {}
    if isinstance(cd, str):
        try:
            cd = json.loads(cd)
        except Exception:
            cd = {}

    features = {}

    # ── Basic horse attributes ──
    try:
        features['horse_age'] = float(cd.get('horse age', 0) or 0)
    except Exception:
        features['horse_age'] = 0.0

    sex_map = {'Gelding': 0, 'Mare': 1, 'Horse': 2, 'Colt': 3, 'Filly': 4, 'Rig': 0}
    features['horse_sex'] = sex_map.get(str(cd.get('horse sex', '')).strip(), 0)

    try:
        features['horse_weight'] = float(cd.get('horse weight', 57) or 57)
    except Exception:
        features['horse_weight'] = 57.0

    try:
        features['horse_claim'] = float(cd.get('horse claim', 0) or 0)
    except Exception:
        features['horse_claim'] = 0.0

    # ── NEW: Weight change from last start ──
    # This is the key missing feature — analyzer.js penalises weight gains
    # but backtest data shows gains are POSITIVE. RF needs to see this.
    try:
        last_weight = float(cd.get('form weight', 0) or 0)
        curr_weight = features['horse_weight']
        if last_weight >= 49 and last_weight <= 65 and curr_weight >= 49:
            # Positive = weight increase, negative = weight drop
            features['weight_change'] = curr_weight - last_weight
        else:
            features['weight_change'] = 0.0
    except Exception:
        features['weight_change'] = 0.0

    # ── NEW: Weight vs race average ──
    # Stored as raw weight here; race average computed per-race in build_training_set
    # We store the raw weight and compute diff_from_avg later
    features['weight_vs_avg'] = 0.0  # placeholder, filled in build_training_set

    # ── Race context ──
    try:
        features['distance'] = float(str(cd.get('distance', 1400) or 1400).replace('m', ''))
    except Exception:
        features['distance'] = 1400.0

    condition_map = {'good': 0, 'soft': 1, 'heavy': 2, 'firm': 3, 'synthetic': 4}
    track_cond = str(row.get('track_condition', '') or '').lower().strip()
    for k in condition_map:
        if k in track_cond:
            features['track_condition'] = condition_map[k]
            break
    else:
        features['track_condition'] = 0

    # ── Career record ──
    features['career_win_rate'] = win_rate(cd.get('horse record', ''))
    features['career_podium_rate'] = podium_rate(cd.get('horse record', ''))
    try:
        career_runs_val, _, _, _ = parse_record(cd.get('horse record', ''))
        features['career_runs'] = float(career_runs_val)
    except Exception:
        features['career_runs'] = 0.0

    # ── Specialist records ──
    features['distance_win_rate'] = win_rate(cd.get('horse record distance', ''))
    features['track_win_rate'] = win_rate(cd.get('horse record track', ''))
    features['track_distance_win_rate'] = win_rate(cd.get('horse record track distance', ''))
    cond_key = track_cond.split()[0] if track_cond else 'good'
    features['condition_win_rate'] = win_rate(cd.get(f'horse record {cond_key}', ''))
    features['first_up_win_rate'] = win_rate(cd.get('horse record first up', ''))
    features['second_up_win_rate'] = win_rate(cd.get('horse record second up', ''))

    # ── Last 10 runs features ──
    l10_features = parse_last10(cd.get('horse last10', ''))
    features.update(l10_features)

    # ── Last start ──
    try:
        features['last_position'] = float(cd.get('form position', 5) or 5)
    except Exception:
        features['last_position'] = 5.0

    try:
        features['last_margin'] = float(cd.get('form margin', 10) or 10)
    except Exception:
        features['last_margin'] = 10.0

    try:
        features['last_sp'] = float(cd.get('form price', 10) or 10)
    except Exception:
        features['last_sp'] = 10.0

    # ── Distance change ──
    try:
        last_dist = float(cd.get('form distance', 0) or 0)
        curr_dist = features['distance']
        features['distance_change'] = curr_dist - last_dist if last_dist > 0 else 0.0
    except Exception:
        features['distance_change'] = 0.0

    # ── NEW: Class change (today class score vs last class score) ──
    # Mirrors compareClasses() in analyzer.js — positive = stepping up, negative = dropping
    try:
        today_class = calculate_class_score(
            cd.get('class restrictions', ''),
            cd.get('race prizemoney', '')
        )
        last_class = calculate_class_score(
            cd.get('form class', ''),
            cd.get('prizemoney', '')
        )
        features['class_change'] = today_class - last_class
    except Exception:
        features['class_change'] = 0.0

    # ── Days since last run ──
    features['days_since_run'] = float(days_since_run(
        cd.get('meeting date', ''),
        cd.get('form meeting date', '')
    ))

    # ── Market / PFAI ──
    try:
        features['pfai_score'] = float(cd.get('pfaiscore', 0) or cd.get('pfaiScore', 0) or 0)
    except Exception:
        features['pfai_score'] = 0.0

    # ── Sectionals ──
    try:
        features['last200_rank'] = float(cd.get('last200timerank', cd.get('last200TimeRank', 99)) or 99)
    except Exception:
        features['last200_rank'] = 99.0

    try:
        features['last400_rank'] = float(cd.get('last400timerank', cd.get('last400TimeRank', 99)) or 99)
    except Exception:
        features['last400_rank'] = 99.0

    try:
        features['last600_rank'] = float(cd.get('last600timerank', cd.get('last600TimeRank', 99)) or 99)
    except Exception:
        features['last600_rank'] = 99.0

    # ── NEW: Country (full encoding, not just AUS binary) ──
    # Replaces is_aus_bred with a multi-value encoding that captures NZ/FR/GB etc.
    country = str(cd.get('country', 'AUS') or 'AUS').strip().upper()
    country_score_map = {
        'AUS': 0,
        'NZ':  -1,   # -31.1% ROI confirmed
        'IRE': -0.5, # -11% ROI, moderate
        'GB':  1,    # +59.2% ROI (small sample)
        'FR':  -2,   # -67.7% ROI
        'JPN': -2,   # -100% ROI
        'GER': -2,   # -100% ROI
        'USA': 0,    # too small to score
    }
    features['country_score'] = float(country_score_map.get(country, -0.5))
    # Keep binary for backward compatibility
    features['is_aus_bred'] = 1.0 if country == 'AUS' else 0.0

    # ── Running position (single value for RF) ──
    pos_map = {'LEADER': 3, 'ONPACE': 2, 'MIDFIELD': 1, 'BACKMARKER': 0}
    run_pos = str(cd.get('runningposition', cd.get('runningPosition', '')) or '').upper().strip()
    features['running_position'] = float(pos_map.get(run_pos, 1))

    # ── NEW: Running position × distance-context features ──
    # The single running_position=3 number loses all context — LEADER in sprint
    # is completely different to LEADER in staying. These binary flags let the
    # RF learn the distance-specific value of each position.
    dist = features['distance']
    is_sprint  = dist <= 1200
    is_mile    = 1300 <= dist <= 1700
    is_middle  = 1800 <= dist <= 2200
    is_staying = dist > 2200

    features['leader_sprint']      = 1.0 if (run_pos == 'LEADER'     and is_sprint)  else 0.0
    features['leader_mile']        = 1.0 if (run_pos == 'LEADER'     and is_mile)    else 0.0
    features['leader_middle']      = 1.0 if (run_pos == 'LEADER'     and is_middle)  else 0.0
    features['leader_staying']     = 1.0 if (run_pos == 'LEADER'     and is_staying) else 0.0
    features['onpace_sprint']      = 1.0 if (run_pos == 'ONPACE'     and is_sprint)  else 0.0
    features['onpace_mile']        = 1.0 if (run_pos == 'ONPACE'     and is_mile)    else 0.0
    features['backmarker_sprint']  = 1.0 if (run_pos == 'BACKMARKER' and is_sprint)  else 0.0
    features['backmarker_staying'] = 1.0 if (run_pos == 'BACKMARKER' and is_staying) else 0.0

    # ── NEW: Jockey L100 strike rate ──
    # The live SR bands (checkJockeys) are active in analyzer.js but were never
    # represented as a numeric feature for the RF to evaluate.
    jockey_name = str(cd.get('horse jockey', '') or '').strip()
    features['jockey_sr'] = get_sr_win_pct(jockey_name, jockey_sr_lookup or {})

    # ── NEW: Trainer L100 strike rate ──
    trainer_name = str(cd.get('horse trainer', '') or '').strip()
    features['trainer_sr'] = get_sr_win_pct(trainer_name, trainer_sr_lookup or {})

    return features


# ─────────────────────────────────────────────
# STEP 3: BUILD ML TRAINING SET
# ─────────────────────────────────────────────
def build_training_set(df, strike_rate_data=None):
    """
    Build one training row per horse per race.
    Also computes weight_vs_avg (requires race-level average, done here).
    Target variable: ROI = sp if won, -1 if lost.
    """
    log.info("Building ML training set...")

    jockey_sr = (strike_rate_data or {}).get('jockeys', {})
    trainer_sr = (strike_rate_data or {}).get('trainers', {})

    # Deduplicate: one row per horse per race (latest snapshot)
    df_unique = df.sort_values('horse_id').drop_duplicates(
        subset=['race_id', 'horse_name'], keep='last'
    )

    # ── Pre-compute race average weights ──
    # Needed for weight_vs_avg feature (mirrors calculateWeightScores in analyzer.js)
    race_avg_weights = {}
    for race_id, group in df_unique.groupby('race_id'):
        weights = []
        for _, row in group.iterrows():
            cd = row.get('csv_data') or {}
            if isinstance(cd, str):
                try:
                    cd = json.loads(cd)
                except Exception:
                    cd = {}
            try:
                w = float(cd.get('horse weight', 0) or 0)
                if 49 <= w <= 65:
                    weights.append(w)
            except Exception:
                pass
        race_avg_weights[race_id] = sum(weights) / len(weights) if weights else 55.0

    feature_rows = []
    targets_roi = []
    targets_won = []
    race_ids = []
    horse_ids = []
    meeting_dates = []

    for _, row in df_unique.iterrows():
        try:
            features = extract_features(row, jockey_sr, trainer_sr)
            finish = int(row['finish_position'])
            sp = float(row['sp']) if row['sp'] else None

            if sp is None or sp <= 1.0:
                continue

            # Fill weight_vs_avg now that we have the race average
            race_id = row['race_id']
            avg_w = race_avg_weights.get(race_id, 55.0)
            curr_w = features['horse_weight']
            if 49 <= curr_w <= 65:
                features['weight_vs_avg'] = avg_w - curr_w  # positive = lighter than avg
            else:
                features['weight_vs_avg'] = 0.0

            roi = (sp - 1.0) if finish == 1 else -1.0
            won = 1 if finish == 1 else 0

            feature_rows.append(features)
            targets_roi.append(roi)
            targets_won.append(won)
            race_ids.append(race_id)
            horse_ids.append(row['horse_id'])
            meeting_dates.append(row['meeting_date'])

        except Exception:
            continue

    X = pd.DataFrame(feature_rows)
    y_roi = pd.Series(targets_roi)
    y_won = pd.Series(targets_won)

    # Fill any NaN features with median
    X = X.fillna(X.median())

    log.info(f"Training set: {len(X)} horses, {X.shape[1]} features, "
             f"{y_won.sum()} winners ({y_won.mean()*100:.1f}% win rate)")

    return X, y_roi, y_won, race_ids, horse_ids, meeting_dates


# ─────────────────────────────────────────────
# STEP 4: TRACK A — RANDOM FOREST
# ─────────────────────────────────────────────
def run_random_forest(X, y_roi, y_won, meeting_dates):
    """
    Train Random Forest models and extract feature importance.
    Uses time-series split (no future leakage).
    Returns feature importance dict sorted by importance descending.
    """
    log.info("Running Random Forest analysis (Track A)...")

    if len(X) < 100:
        log.warning("Not enough data for reliable RF analysis (need 100+ horses).")
        return {}

    feature_names = X.columns.tolist()

    rf_roi = RandomForestRegressor(
        n_estimators=200,
        max_depth=8,
        min_samples_leaf=20,
        max_features='sqrt',
        random_state=42,
        n_jobs=-1
    )

    rf_win = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        min_samples_leaf=20,
        max_features='sqrt',
        random_state=42,
        n_jobs=-1,
        class_weight='balanced'
    )

    # Time series split — train on oldest 80%, validate on newest 20%
    dates = pd.Series(meeting_dates)
    cutoff = dates.quantile(0.8)
    train_mask = dates <= cutoff
    test_mask  = dates > cutoff

    X_train, X_test = X[train_mask], X[test_mask]
    y_roi_train = y_roi[train_mask]
    y_won_train = y_won[train_mask]

    log.info(f"Train set: {len(X_train)} horses | Test set: {len(X_test)} horses")

    rf_roi.fit(X_train, y_roi_train)
    rf_win.fit(X_train, y_won_train)

    # Combine importance — weight ROI model higher (60/40)
    roi_norm = rf_roi.feature_importances_ / rf_roi.feature_importances_.sum()
    win_norm = rf_win.feature_importances_ / rf_win.feature_importances_.sum()
    combined = roi_norm * 0.6 + win_norm * 0.4

    importance_dict = dict(zip(feature_names, combined))
    importance_sorted = dict(sorted(importance_dict.items(), key=lambda x: x[1], reverse=True))

    log.info("Top 15 most predictive features:")
    for i, (feat, imp) in enumerate(list(importance_sorted.items())[:15]):
        log.info(f"  {i+1:2d}. {feat:<40} {imp*100:.2f}%")

    return importance_sorted


# ─────────────────────────────────────────────
# STEP 5: TRACK B — COMPONENT ROI ANALYSIS
# ─────────────────────────────────────────────

# Component name normalisation map.
# Maps messy/variant note text → canonical component name.
# Add entries here when a component appears under multiple names across runs.
COMPONENT_NAME_MAP = {
    # Running position
    'LEADER in Sprint':                        'LEADER in Sprint',
    'LEADER in Mile':                          'LEADER in Mile',
    'LEADER in Middle distance':               'LEADER in Middle distance',
    'LEADER in Staying race':                  'LEADER in Staying race',
    'ONPACE in Sprint':                        'ONPACE in Sprint',
    'ONPACE in Mile':                          'ONPACE in Mile',
    'BACKMARKER in Sprint':                    'BACKMARKER in Sprint',
    'BACKMARKER in Staying race':              'BACKMARKER in Staying race',
    # Sprint leader combos
    'Sprint Leader Run Down Bonus':            'Sprint Leader Run Down Bonus',
    'Hidden Edge':                             'Hidden Edge — Sprint leader + last start favoured',
    # Jockey / trainer bands
    'Jockey hot form':                         'Jockey hot form',
    'Jockey solid form':                       'Jockey solid form',
    'Jockey average form':                     'Jockey average form',
    'Jockey poor form':                        'Jockey poor form',
    'Jockey cold':                             'Jockey cold',
    'Trainer hot form':                        'Trainer hot form',
    'Trainer solid form':                      'Trainer solid form',
    'Trainer average form':                    'Trainer average form',
    'Trainer poor form':                       'Trainer poor form',
    'Trainer cold':                            'Trainer cold',
    # Age/sex
    '5yo horse':                               '5yo horse (entire)',
    '5yo Mare':                                '5yo Mare',
    '6-7yo Mare':                              '6-7yo Mare',
    'Prime age (3yo)':                         'Prime age (3yo)',
    'Old age (7-8yo':                          'Old age (7-8yo)',
    '9yo':                                     '9yo penalty',
    # Colt system
    '3yo COLT':                                '3yo COLT combo',
    'COLT base bonus':                         'COLT base bonus',
    'Fast sectional + COLT combo':             'Fast sectional + COLT combo',
    'Colt in Set Weight race':                 'Colt in Set Weight race',
    # Weight
    'Dropped':                                 None,   # will be handled by full name match
    'Up':                                      None,   # will be handled by full name match
    # Days since run
    'Quick backup':                            'Quick backup',
    'Long absence':                            'Long absence',
    'Fresh return':                            'Fresh return',
    'Very long absence':                       'Very long absence',
    # Market expectation
    'Market Expectation':                      'Market Expectation (A/E)',
    # Career
    'Elite career win rate':                   'Elite career win rate',
    'Poor career win rate':                    'Poor career win rate',
    # Close loss
    'Close loss last start':                   'Close loss last start (0.5-2.5L)',
    # Country
    'NZ-bred':                                 'NZ-bred penalty',
    'French-bred':                             'French-bred penalty',
    'GB-bred':                                 'GB-bred bonus',
}


def normalize_component_name(name):
    """
    Normalise a component name extracted from notes.
    1. Strips trailing parenthetical stats — (ROI%, N races)
    2. Does NOT strip after dash — preserves names like 'LEADER in Sprint'
       and 'Hidden Edge — Sprint leader...'
    3. Applies canonical name map for known variants.
    4. Truncates to 150 chars.
    """
    if not name:
        return ''

    # Strip trailing parenthetical stats: (+33.4% ROI, 154 races) or [Low confidence...]
    name = re.sub(r'\s*[\(\[]\+?-?[\d.]+%.*?[\)\]]$', '', name).strip()

    # Strip trailing ROI/SR stat fragments that didn't get caught above
    name = re.sub(r'\s*\([\d.]+%\s*SR.*$', '', name).strip()
    name = re.sub(r'\s*\(\d+\s*races?\)$', '', name).strip()

    # Normalise whitespace
    name = re.sub(r'\s+', ' ', name).strip()

    # Truncate
    name = name[:150]

    # Apply canonical map — check if name STARTS WITH any key
    for key, canonical in COMPONENT_NAME_MAP.items():
        if name.startswith(key) and canonical is not None:
            return canonical

    return name


def parse_components_from_notes(notes_text):
    """
    Parse analyzer.js notes to extract which components fired and their scores.
    Returns list of (component_name, score) tuples.

    FIX vs old version: We no longer strip everything after a dash.
    That was mangling component names like:
      'LEADER in Sprint' (correct — no dash)
      'Hidden Edge — Sprint leader + last start favoured' (would have been truncated to 'Hidden Edge')
      'Demolished (4th) by 12.5L' (correctly left alone)

    We now use normalize_component_name() for clean, consistent naming.

    Also skips:
      - Info/context lines (ℹ️, ⚠️, =====)
      - Total/subtotal lines (= 12.3 : Total track score)
      - Lines with score = 0 (neutral components add noise)
      - PFAI BLEND section lines
    """
    if not notes_text:
        return []

    components = []
    lines = str(notes_text).split('\n')

    # Track whether we're inside a noise section to skip
    skip_section = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Skip section headers/footers
        if '===' in line:
            # Start skipping PFAI BLEND, SECTIONAL ANALYSIS, MARKET EXPECTATION sections
            # (these produce sub-lines we don't want as individual components)
            skip_section = ('PFAI BLEND' in line or 'SECTIONAL ANALYSIS' in line)
            # Market expectation is a single scored line — keep it
            if 'MARKET EXPECTATION' in line:
                skip_section = False
            continue

        if skip_section:
            continue

        # Skip info/warning lines
        if line.startswith('ℹ️') or line.startswith('⚠️') or line.startswith('📏'):
            continue

        # Skip total/subtotal lines (= 12.3 : Total ...)
        if re.match(r'^=\s*[\d.]+', line):
            continue

        # Skip history lines from sectional notes
        if line.startswith('└─') or 'HISTORY_' in line:
            continue

        # Match: +15.0 : Component Name  OR  -20.0 : Component Name
        match = re.match(r'^([+-]?\d+\.?\d*)\s*:\s*(.+)$', line)
        if not match:
            continue

        try:
            score = float(match.group(1))
            raw_name = match.group(2).strip()

            # Skip zero-score lines — neutral components pollute the analysis
            if score == 0:
                continue

            name = normalize_component_name(raw_name)

            if name:
                components.append((name, score))

        except Exception:
            continue

    return components


def run_component_analysis(df):
    """
    Track B: Parse all prediction notes, calculate per-component ROI.
    Only analyses TOP PICK per race (the horse the model actually bets on).
    Returns (results_list, baseline_roi, baseline_sr, total_races, total_wins).
    """
    log.info("Running component ROI analysis (Track B)...")

    df_with_scores = df.dropna(subset=['analyzer_score']).copy()

    if df_with_scores.empty:
        log.warning("No analyzer scores found. Skipping Track B.")
        return [], 0.0, 0.0, 0, 0

    # Top pick = highest analyzer score per race
    top_picks = df_with_scores.loc[
        df_with_scores.groupby('race_id')['analyzer_score'].idxmax()
    ].copy()

    log.info(f"Analysing {len(top_picks)} top picks")

    # Baseline
    winners = top_picks[top_picks['finish_position'] == 1]
    total_staked = len(top_picks)
    total_returned = winners['sp'].sum() if not winners.empty else 0
    baseline_roi = ((total_returned - total_staked) / total_staked * 100) if total_staked > 0 else 0
    baseline_sr = len(winners) / total_staked * 100 if total_staked > 0 else 0

    log.info(f"Baseline: {total_staked} races, {len(winners)} wins, "
             f"{baseline_sr:.1f}% SR, {baseline_roi:.1f}% ROI")

    # Parse components
    component_data = {}

    for _, row in top_picks.iterrows():
        components = parse_components_from_notes(row.get('analyzer_notes', ''))
        won = int(row['finish_position']) == 1
        sp = float(row['sp']) if row['sp'] else None

        if sp is None:
            continue

        for comp_name, comp_score in components:
            if comp_name not in component_data:
                component_data[comp_name] = {
                    'appearances': 0,
                    'wins': 0,
                    'sps': [],
                    'scores': [],
                    'roi_contributions': []
                }
            component_data[comp_name]['appearances'] += 1
            component_data[comp_name]['wins'] += 1 if won else 0
            component_data[comp_name]['sps'].append(sp)
            component_data[comp_name]['scores'].append(comp_score)
            component_data[comp_name]['roi_contributions'].append(sp if won else -1.0)

    # Calculate stats
    results = []

    for comp_name, data in component_data.items():
        appearances = data['appearances']
        if appearances < 5:
            continue

        wins = data['wins']
        strike_rate = wins / appearances * 100
        avg_sp = float(np.mean(data['sps']))
        roi = float(np.sum(data['roi_contributions']) / appearances * 100)
        current_value = float(np.mean(data['scores'])) if data['scores'] else 0.0

        roi_delta = roi - baseline_roi

        if roi > 10:
            verdict = 'BOOST'
            suggested_value = round(current_value * 1.3, 1)
        elif roi < -10:
            verdict = 'REDUCE'
            suggested_value = round(current_value * 0.5, 1)
        elif roi < -5:
            verdict = 'MONITOR'
            suggested_value = round(current_value * 0.75, 1)
        else:
            verdict = 'OK'
            suggested_value = current_value
            roi_delta = 0.0

        results.append({
            'component_name': comp_name,
            'appearances': appearances,
            'wins': wins,
            'strike_rate': round(strike_rate, 1),
            'roi': round(roi, 1),
            'avg_sp': round(avg_sp, 2),
            'current_value': round(current_value, 1),
            'suggested_value': suggested_value,
            'roi_delta': round(roi_delta, 1),
            'verdict': verdict
        })

    # Sort: within each verdict group, sort by abs ROI desc
    results.sort(key=lambda x: abs(x['roi']), reverse=True)

    log.info(f"Analysed {len(results)} components.")
    log.info(f"  BOOST: {sum(1 for r in results if r['verdict'] == 'BOOST')} | "
             f"REDUCE: {sum(1 for r in results if r['verdict'] == 'REDUCE')} | "
             f"MONITOR: {sum(1 for r in results if r['verdict'] == 'MONITOR')} | "
             f"OK: {sum(1 for r in results if r['verdict'] == 'OK')}")

    return results, baseline_roi, baseline_sr, total_staked, len(winners)

# ─────────────────────────────────────────────
# TRACK C — SCORE MOMENTUM ANALYSIS
# ─────────────────────────────────────────────
def run_momentum_analysis(df):
    """
    Track C: For every horse with 3+ scored races, compute the linear slope
    of analyzer_score across their last 5 runs (oldest→newest).
    Buckets: Strong Fall / Moderate Fall / Mild Fall / Flat /
             Mild Rise / Moderate Rise / Strong Rise
    Also tracks avg_predicted_sp and overlay_pct (SP > predicted odds)
    to identify where market mis-prices trajectory.
    Uses ALL horses to maximise sample size.
    """
    log.info("Running score momentum analysis (Track C)...")

    df_scored = df.dropna(subset=['analyzer_score']).copy()
    df_scored = df_scored[df_scored['analyzer_score'] > 0]

    if df_scored.empty:
        log.warning("No scored horses found. Skipping Track C.")
        return []

    df_scored['meeting_date'] = pd.to_datetime(df_scored['meeting_date'], errors='coerce')
    df_scored = df_scored.dropna(subset=['meeting_date'])
    df_scored = df_scored.sort_values(['horse_name', 'meeting_date'])

    BUCKETS = [
        ('Strong Fall',    None,  -5.0),
        ('Moderate Fall',  -5.0,  -2.5),
        ('Mild Fall',      -2.5,  -1.5),
        ('Flat',           -1.5,   1.5),
        ('Mild Rise',       1.5,   2.5),
        ('Moderate Rise',   2.5,   5.0),
        ('Strong Rise',     5.0,  None),
    ]

    def get_bucket(slope):
        for name, low, high in BUCKETS:
            if low is None and slope < high:
                return name
            if high is None and slope >= low:
                return name
            if low is not None and high is not None and low <= slope < high:
                return name
        return 'Flat'

    bucket_data = {
        name: {
            'appearances': 0,
            'wins': 0,
            'roi_contributions': [],
            'sps': [],
            'slopes': [],
            'predicted_sps': [],
            'overlays': 0,
        }
        for name, _, _ in BUCKETS
    }

    for horse_name, group in df_scored.groupby('horse_name'):
        group = group.sort_values('meeting_date')
        if len(group) < 3:
            continue

        scores        = group['analyzer_score'].tolist()
        positions     = group['finish_position'].tolist()
        sps           = group['sp'].tolist()
        predicted_odds = group['predicted_odds'].tolist() if 'predicted_odds' in group.columns else [None] * len(scores)

        for i in range(2, len(scores)):
            window_start  = max(0, i - 4)
            window_scores = scores[window_start:i + 1]

            if len(window_scores) < 3:
                continue

            try:
                sp_val = float(sps[i]) if sps[i] else None
            except Exception:
                sp_val = None

            if sp_val is None or sp_val <= 1.0:
                continue

            x     = list(range(len(window_scores)))
            slope = float(np.polyfit(x, window_scores, 1)[0])
            won   = int(positions[i]) == 1
            bucket = get_bucket(slope)

            # Parse predicted odds for overlay calculation
            pred_sp = None
            try:
                raw_pred = predicted_odds[i]
                if raw_pred:
                    pred_sp = float(str(raw_pred).replace('$', '').strip())
            except Exception:
                pred_sp = None

            bucket_data[bucket]['appearances'] += 1
            if won:
                bucket_data[bucket]['wins'] += 1
            bucket_data[bucket]['roi_contributions'].append(sp_val if won else -1.0)
            bucket_data[bucket]['sps'].append(sp_val)
            bucket_data[bucket]['slopes'].append(slope)

            if pred_sp and pred_sp > 0:
                bucket_data[bucket]['predicted_sps'].append(pred_sp)
                if sp_val > pred_sp:
                    bucket_data[bucket]['overlays'] += 1

    results = []
    for name, _, _ in BUCKETS:
        data = bucket_data[name]
        n = data['appearances']
        if n < 10:
            continue

        wins        = data['wins']
        strike_rate = wins / n * 100
        roi         = sum(data['roi_contributions']) / n * 100
        avg_sp      = float(np.mean(data['sps']))
        avg_slope   = float(np.mean(data['slopes']))

        pred_sps = data['predicted_sps']
        avg_predicted_sp = round(float(np.mean(pred_sps)), 2) if pred_sps else None
        overlay_pct = round(data['overlays'] / len(pred_sps) * 100, 1) if pred_sps else None

        results.append({
            'trajectory':      name,
            'appearances':     n,
            'wins':            wins,
            'strike_rate':     round(strike_rate, 1),
            'roi':             round(roi, 1),
            'avg_sp':          round(avg_sp, 2),
            'avg_slope':       round(avg_slope, 2),
            'avg_predicted_sp': avg_predicted_sp,
            'overlay_pct':     overlay_pct,
        })

    log.info(f"Momentum analysis: {[r['trajectory'] + ' n=' + str(r['appearances']) for r in results]}")
    return results


# ─────────────────────────────────────────────
# ANALYZER WEIGHTS — updated to match current analyzer.js
ANALYZER_WEIGHTS = {
    # Core form
    'last_margin':              'Up to ±25 pts (dominant win → +10, demolished → -25)',
    'last_sp':                  'Up to ±50 pts (form price lookup table $1.01-$500)',
    'last_position':            'Up to ±25 pts (position 1 → +5 to +15, 4+ → -3 to -25)',
    'career_win_rate':          '+10 pts if 40%+, 0 pts 30-40%, -15 pts if <10% (min 5 starts)',
    'career_podium_rate':       'Not directly scored — covered partially by Ran Places recency scoring',
    'career_runs':              'Not directly scored — experience proxy, captured by maiden/class penalties',

    # Distance & class
    'distance_change':          '+8 pts drop 200-400m, -5 pts big drop 400m+, 0 for step ups',
    'class_change':             'Up to ±20 pts (capped) — stepping up penalised, dropping rewarded',
    'distance':                 'Indirect — informs running position score and sectional weighting',
    'distance_win_rate':        'Up to ±16 pts (win rate + podium rate × confidence multiplier)',
    'track_win_rate':           'Up to ±12 pts (win rate + podium rate × confidence multiplier)',
    'track_distance_win_rate':  'Up to ±16 pts (specialist combo)',
    'condition_win_rate':       'Up to ±24 pts (condition form × condition multiplier soft/heavy ×2)',

    # Time-based
    'days_since_run':           '±20 pts: 150-199d +8, 250-364d +5, 200-249d -5, 365d+ -20',

    # Weight
    'horse_weight':             'Up to ±15 pts vs race average (calculated per race)',
    'weight_vs_avg':            'Up to ±15 pts — lighter than avg = bonus, heavier = penalty',
    'weight_change':            'Up to ±15 pts — DROP from last start = +15, GAIN = -15 (may be miscalibrated)',
    'horse_claim':              'Indirect — claim reduces carried weight, affects weight_vs_avg',

    # Running position
    'running_position':         'Encoded 0-3, but distance-context features below are more precise',
    'leader_sprint':            '+15 pts LEADER in Sprint (≤1200m) + combos up to +35',
    'leader_mile':              '+6 pts LEADER in Mile (1300-1700m)',
    'leader_middle':            '-5 pts LEADER in Middle distance (1800-2200m)',
    'leader_staying':           '+7 pts LEADER in Staying (2400m+) — Sole Leader bonus',
    'onpace_sprint':            '+8 pts ONPACE in Sprint',
    'onpace_mile':              '+8 pts ONPACE in Mile',
    'backmarker_sprint':        '-8 pts BACKMARKER in Sprint',
    'backmarker_staying':       '+20 pts BACKMARKER in Staying race',

    # Jockey / trainer (live L100 SR)
    'jockey_sr':                'Up to +20/-12 pts based on L100 win% bands (25%+ → +20, <6% → -12)',
    'trainer_sr':               'Up to +10/-10 pts based on L100 win% bands (22%+ → +10, <5% → -10)',

    # Horse attributes
    'horse_age':                'Up to ±60 pts (3yo +3, 5yo Horse +5, 5yo Mare -15, 9yo -35, 13+ -60)',
    'horse_sex':                'Up to +25 pts (Colt system: 3yo Colt +25, base Colt +15, Mare penalties)',

    # Specialist records
    'first_up_win_rate':        '0 pts (zeroed out in current code — undefeated first up +15)',
    'second_up_win_rate':       'Up to +5 pts second-up record',

    # Last 10 / form
    'l10_win_rate':             'Up to +6 pts per run — recency weighted (1.0 / 0.8 / 0.6 / 0.4 / 0.2)',
    'l10_place_rate':           'Indirect — part of recency scoring via Ran Places components',
    'l10_runs':                 'Not directly scored',
    'l10_wins':                 'Not directly scored',
    'l10_places':               'Not directly scored',
    'l5_win_rate':              'Indirect via last10 recency scoring',
    'l5_place_rate':            'Not directly scored',
    'form_trend':               'Not directly scored — captured by recency weighting',
    'is_first_up':              'Indirect — checkFirstUpSecondUp, undefeated first-up +15',
    'is_second_up':             'Up to +5 pts second-up record',
    'last_position':            'Already listed above',

    # Sectionals (RF shows 0% importance — may be data quality issue)
    'last200_rank':             'Up to ±20 pts (API sectional price/rank scoring)',
    'last400_rank':             'Up to ±20 pts (API sectional price/rank scoring)',
    'last600_rank':             'Up to ±20 pts (API sectional price/rank scoring)',

    # Market
    'pfai_score':               'Blended 30% into final score (RF shows 0% importance — investigate)',

    # Country
    'country_score':            '0 to -2 encoded score (AUS=0, NZ=-1, FR/JPN/GER=-2, GB=+1)',
    'is_aus_bred':              'Binary — superseded by country_score but kept for compatibility',

    # Track condition
    'track_condition':          'Indirect — multiplier for condition form score (soft/heavy ×2 for proven runners)',
}


def generate_feature_recommendations(importance_sorted):
    """
    Compare RF feature importance with current analyzer weights.
    Generates a recommendation for each feature for the dashboard.
    """
    recommendations = []

    for rank, (feature, importance) in enumerate(importance_sorted.items(), 1):
        current_weight = ANALYZER_WEIGHTS.get(feature, 'Not directly scored — new feature')

        if importance > 0.08:
            importance_label = 'VERY HIGH'
        elif importance > 0.05:
            importance_label = 'HIGH'
        elif importance > 0.03:
            importance_label = 'MEDIUM'
        elif importance > 0.01:
            importance_label = 'LOW'
        else:
            importance_label = 'VERY LOW'

        not_scored = 'Not directly scored' in current_weight

        if importance > 0.05 and not_scored:
            rec = 'HIGH IMPACT but not directly scored — consider adding explicit scoring'
        elif importance > 0.05:
            rec = 'High predictive power — current scoring appears appropriate'
        elif importance < 0.005 and not not_scored:
            rec = 'Low predictive power — consider reducing weight in analyzer'
        elif importance == 0.0 and not not_scored:
            rec = 'ZERO importance — feature may not be populated in historical data, or fully redundant'
        else:
            rec = 'Moderate predictive power — current scoring reasonable'

        recommendations.append({
            'feature_name': feature,
            'importance_score': round(float(importance), 6),
            'importance_rank': rank,
            'importance_label': importance_label,
            'current_analyzer_weight': current_weight[:200],
            'recommendation': rec
        })

    return recommendations


# ─────────────────────────────────────────────
# STEP 6: WRITE RESULTS TO DB
# ─────────────────────────────────────────────
def write_results(run_id, feature_recommendations, component_results,
                  momentum_results, baseline_roi, baseline_sr, total_races, total_horses):
    """Write all backtest findings to the database."""
    log.info("Writing results to database...")

    with engine.connect() as conn:
        for rec in feature_recommendations:
            conn.execute(text("""
                INSERT INTO backtest_feature_importance
                (run_id, feature_name, importance_score, importance_rank,
                 current_analyzer_weight, recommendation)
                VALUES (:run_id, :feature_name, :importance_score, :importance_rank,
                        :current_analyzer_weight, :recommendation)
            """), {
                'run_id': run_id,
                'feature_name': rec['feature_name'],
                'importance_score': rec['importance_score'],
                'importance_rank': rec['importance_rank'],
                'current_analyzer_weight': rec['current_analyzer_weight'],
                'recommendation': f"[{rec['importance_label']}] {rec['recommendation']}"
            })

        for comp in component_results:
            conn.execute(text("""
                INSERT INTO backtest_component_analysis
                (run_id, component_name, appearances, wins, strike_rate, roi,
                 avg_sp, current_value, suggested_value, roi_delta, verdict)
                VALUES (:run_id, :component_name, :appearances, :wins, :strike_rate,
                        :roi, :avg_sp, :current_value, :suggested_value, :roi_delta, :verdict)
            """), {'run_id': run_id, **comp})

        for mom in momentum_results:
            conn.execute(text("""
                INSERT INTO backtest_momentum_analysis
                (run_id, trajectory, appearances, wins, strike_rate, roi, avg_sp, avg_slope,
                 avg_predicted_sp, overlay_pct)
                VALUES (:run_id, :trajectory, :appearances, :wins, :strike_rate, :roi, :avg_sp, :avg_slope,
                        :avg_predicted_sp, :overlay_pct)
            """), {'run_id': run_id, **mom})

        conn.execute(text("""
            UPDATE backtest_runs
            SET completed_at = NOW(),
                status = 'complete',
                total_races = :total_races,
                total_horses = :total_horses,
                baseline_roi = :baseline_roi,
                baseline_strike_rate = :baseline_sr
            WHERE id = :run_id
        """), {
            'run_id': run_id,
            'total_races': total_races,
            'total_horses': total_horses,
            'baseline_roi': baseline_roi,
            'baseline_sr': baseline_sr
        })

        conn.commit()

    log.info("Results written successfully.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("BACKTEST JOB STARTING")
    log.info(f"Time: {datetime.utcnow().isoformat()}")
    log.info("=" * 60)

    ensure_tables()

    with engine.connect() as conn:
        result = conn.execute(text(
            "INSERT INTO backtest_runs (status) VALUES ('running') RETURNING id"
        ))
        run_id = result.fetchone()[0]
        conn.commit()
    log.info(f"Backtest run ID: {run_id}")

    try:
        df, strike_rate_data = load_historical_data()

        if len(df) < 50:
            log.warning("Not enough historical data (need 50+ races with results).")
            with engine.connect() as conn:
                conn.execute(text(
                    "UPDATE backtest_runs SET status='failed', notes='Insufficient data' WHERE id=:id"
                ), {'id': run_id})
                conn.commit()
            return

        X, y_roi, y_won, race_ids, horse_ids, meeting_dates = build_training_set(
            df, strike_rate_data
        )

        importance_sorted = run_random_forest(X, y_roi, y_won, meeting_dates)
        feature_recommendations = generate_feature_recommendations(importance_sorted)

        component_results_tuple = run_component_analysis(df)
        if isinstance(component_results_tuple, tuple):
            component_results, baseline_roi, baseline_sr, total_races, total_wins = component_results_tuple
        else:
            component_results = []
            baseline_roi = 0.0
            baseline_sr  = 0.0
            total_races  = df['race_id'].nunique()
            total_wins   = 0

        momentum_results = run_momentum_analysis(df)

        total_horses = len(df)

        write_results(
            run_id,
            feature_recommendations,
            component_results,
            momentum_results,
            baseline_roi,
            baseline_sr,
            total_races,
            total_horses
        )
        log.info("=" * 60)
        log.info("BACKTEST JOB COMPLETE")
        log.info(f"Run ID:             {run_id}")
        log.info(f"Races analysed:     {total_races}")
        log.info(f"Horses analysed:    {total_horses}")
        log.info(f"Baseline ROI:       {baseline_roi:.1f}%")
        log.info(f"Baseline SR:        {baseline_sr:.1f}%")
        log.info(f"Features analysed:  {len(feature_recommendations)}")
        log.info(f"Components analysed:{len(component_results)}")
        log.info("=" * 60)

    except Exception as e:
        log.error(f"Backtest failed: {e}", exc_info=True)
        with engine.connect() as conn:
            conn.execute(text("""
                UPDATE backtest_runs
                SET status='failed', notes=:notes, completed_at=NOW()
                WHERE id=:id
            """), {'id': run_id, 'notes': str(e)[:500]})
            conn.commit()
        sys.exit(1)


if __name__ == '__main__':
    main()
