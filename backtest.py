"""
backtest.py - Nightly backtesting and ML analysis for The Form Analyst

Runs as a Railway cron job (0 2 * * * = 2am every night)

Two parallel tracks:
  Track A: Random Forest feature importance - which raw horse features predict winners
  Track B: Component ROI analysis - which analyzer.js components help/hurt ROI

Results written to DB, viewable at /backtest in the web app.
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

# Railway uses postgres:// but SQLAlchemy needs postgresql://
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
                current_analyzer_weight VARCHAR(100),
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

        conn.commit()
    log.info("Backtest tables verified.")


# ─────────────────────────────────────────────
# STEP 1: LOAD ALL HISTORICAL DATA
# ─────────────────────────────────────────────
def load_historical_data():
    """
    Pull all races that have results recorded.
    Returns a DataFrame with one row per horse per race,
    including the full csv_data dict and the actual result.
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
    return df


# ─────────────────────────────────────────────
# STEP 2: EXTRACT FEATURES FROM CSV_DATA
# ─────────────────────────────────────────────
def parse_record(record_str):
    """Parse a record string like '10:2-1-1' into (runs, wins, seconds, thirds)."""
    if not record_str or not isinstance(record_str, str):
        return 0, 0, 0, 0
    # Handle formats like "10: 2-1-1" or "10:2-1-1"
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
    # Get only non-X characters (actual runs)
    runs = [c for c in s if c.lower() != 'x' and c.isdigit()]
    if not runs:
        return {'l10_runs': 0, 'l10_wins': 0, 'l10_win_rate': 0,
                'l10_places': 0, 'l10_place_rate': 0, 'l5_win_rate': 0,
                'l5_place_rate': 0, 'is_first_up': 0, 'is_second_up': 0,
                'last_position': 9, 'form_trend': 0}

    # Most recent is rightmost
    runs_list = [int(c) for c in runs]
    last5 = runs_list[-5:] if len(runs_list) >= 5 else runs_list
    last10 = runs_list[-10:] if len(runs_list) >= 10 else runs_list

    l10_wins = sum(1 for x in last10 if x == 1)
    l10_places = sum(1 for x in last10 if x in [1, 2, 3])
    l5_wins = sum(1 for x in last5 if x == 1)
    l5_places = sum(1 for x in last5 if x in [1, 2, 3])

    # Form trend: compare first half vs second half win rates
    if len(runs_list) >= 4:
        mid = len(runs_list) // 2
        early_wr = sum(1 for x in runs_list[:mid] if x == 1) / mid
        recent_wr = sum(1 for x in runs_list[mid:] if x == 1) / (len(runs_list) - mid)
        trend = recent_wr - early_wr  # positive = improving
    else:
        trend = 0.0

    # First up / second up detection
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


def days_since_run(meeting_date_str, form_date_str):
    """Calculate days between race date and last run date."""
    try:
        if not meeting_date_str or not form_date_str:
            return -1
        # Handle various date formats
        for fmt in ['%d/%m/%Y %H:%M:%S', '%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d']:
            try:
                race_date = datetime.strptime(str(meeting_date_str).split(' ')[0], fmt.split(' ')[0])
                break
            except:
                continue
        else:
            return -1
        for fmt in ['%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d']:
            try:
                last_date = datetime.strptime(str(form_date_str).split(' ')[0], fmt)
                break
            except:
                continue
        else:
            return -1
        return (race_date - last_date).days
    except:
        return -1


def extract_features(row):
    """
    Extract ~30 ML features from a horse's csv_data dict.
    Returns a flat dict of features.
    """
    cd = row.get('csv_data') or {}
    if isinstance(cd, str):
        try:
            cd = json.loads(cd)
        except:
            cd = {}

    features = {}

    # ── Basic horse attributes ──
    try:
        features['horse_age'] = float(cd.get('horse age', 0) or 0)
    except:
        features['horse_age'] = 0.0

    sex_map = {'Gelding': 0, 'Mare': 1, 'Horse': 2, 'Colt': 3, 'Filly': 4, 'Rig': 0}
    features['horse_sex'] = sex_map.get(str(cd.get('horse sex', '')).strip(), 0)

    try:
        features['horse_weight'] = float(cd.get('horse weight', 57) or 57)
    except:
        features['horse_weight'] = 57.0

    try:
        features['horse_claim'] = float(cd.get('horse claim', 0) or 0)
    except:
        features['horse_claim'] = 0.0

    # ── Race context ──
    try:
        features['distance'] = float(str(cd.get('distance', 1400) or 1400).replace('m', ''))
    except:
        features['distance'] = 1400.0

    condition_map = {'good': 0, 'soft': 1, 'heavy': 2, 'firm': 3, 'synthetic': 4}
    track_cond = str(row.get('track_condition', '') or '').lower().strip()
    # Handle "Good 4", "Soft 7" etc
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
        career_runs, _, _, _ = parse_record(cd.get('horse record', ''))
        features['career_runs'] = float(career_runs)
    except:
        features['career_runs'] = 0.0

    # ── Specialist records ──
    features['distance_win_rate'] = win_rate(cd.get('horse record distance', ''))
    features['track_win_rate'] = win_rate(cd.get('horse record track', ''))
    features['track_distance_win_rate'] = win_rate(cd.get('horse record track distance', ''))
    features['condition_win_rate'] = win_rate(cd.get(f"horse record {track_cond.split()[0] if track_cond else 'good'}", ''))
    features['first_up_win_rate'] = win_rate(cd.get('horse record first up', ''))
    features['second_up_win_rate'] = win_rate(cd.get('horse record second up', ''))

    # ── Last 10 runs features ──
    l10_features = parse_last10(cd.get('horse last10', ''))
    features.update(l10_features)

    # ── Last start ──
    try:
        features['last_position'] = float(cd.get('form position', 5) or 5)
    except:
        features['last_position'] = 5.0

    try:
        features['last_margin'] = float(cd.get('form margin', 10) or 10)
    except:
        features['last_margin'] = 10.0

    try:
        features['last_sp'] = float(cd.get('form price', 10) or 10)
    except:
        features['last_sp'] = 10.0

    # ── Distance change ──
    try:
        last_dist = float(cd.get('form distance', 0) or 0)
        curr_dist = features['distance']
        features['distance_change'] = curr_dist - last_dist if last_dist > 0 else 0.0
    except:
        features['distance_change'] = 0.0

    # ── Days since last run ──
    features['days_since_run'] = float(days_since_run(
        cd.get('meeting date', ''),
        cd.get('form meeting date', '')
    ))

    # ── Market / PFAI ──
    try:
        features['pfai_score'] = float(cd.get('pfaiScore', 0) or 0)
    except:
        features['pfai_score'] = 0.0

    # ── Sectionals ──
    try:
        features['last200_rank'] = float(cd.get('last200TimeRank', 99) or 99)
    except:
        features['last200_rank'] = 99.0

    try:
        features['last400_rank'] = float(cd.get('last400TimeRank', 99) or 99)
    except:
        features['last400_rank'] = 99.0

    try:
        features['last600_rank'] = float(cd.get('last600TimeRank', 99) or 99)
    except:
        features['last600_rank'] = 99.0

    # ── Country ──
    country = str(cd.get('country', 'AUS') or 'AUS').strip().upper()
    features['is_aus_bred'] = 1.0 if country == 'AUS' else 0.0

    # ── Running position ──
    pos_map = {'LEADER': 3, 'ONPACE': 2, 'MIDFIELD': 1, 'BACKMARKER': 0}
    features['running_position'] = float(pos_map.get(
        str(cd.get('runningPosition', '') or '').upper().strip(), 1
    ))

    return features


# ─────────────────────────────────────────────
# STEP 3: BUILD ML TRAINING SET
# ─────────────────────────────────────────────
def build_training_set(df):
    """
    Build one training row per horse per race.
    Uses the latest form snapshot (highest horse id) per horse per race.
    Target variable: ROI = sp if won, -1 if lost.
    """
    log.info("Building ML training set...")

    # Deduplicate: one row per horse per race (latest snapshot)
    df_unique = df.sort_values('horse_id').drop_duplicates(
        subset=['race_id', 'horse_name'], keep='last'
    )

    feature_rows = []
    targets_roi = []
    targets_won = []
    race_ids = []
    horse_ids = []
    meeting_dates = []

    for _, row in df_unique.iterrows():
        try:
            features = extract_features(row)
            finish = int(row['finish_position'])
            sp = float(row['sp']) if row['sp'] else None

            if sp is None or sp <= 1.0:
                continue

            # ROI target: if won, return is sp - 1 (profit per $1 bet)
            # if lost, return is -1
            roi = (sp - 1.0) if finish == 1 else -1.0
            won = 1 if finish == 1 else 0

            feature_rows.append(features)
            targets_roi.append(roi)
            targets_won.append(won)
            race_ids.append(row['race_id'])
            horse_ids.append(row['horse_id'])
            meeting_dates.append(row['meeting_date'])

        except Exception as e:
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
    Uses time-series cross validation (no future leakage).
    Returns feature importance dict.
    """
    log.info("Running Random Forest analysis (Track A)...")

    if len(X) < 100:
        log.warning("Not enough data for reliable RF analysis (need 100+ horses).")
        return {}

    feature_names = X.columns.tolist()

    # ── ROI Regression (primary - what professional punters care about) ──
    rf_roi = RandomForestRegressor(
        n_estimators=200,
        max_depth=8,
        min_samples_leaf=20,  # Prevent overfitting on small samples
        max_features='sqrt',
        random_state=42,
        n_jobs=-1
    )

    # ── Win Classification (secondary - for filtering obvious losers) ──
    rf_win = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        min_samples_leaf=20,
        max_features='sqrt',
        random_state=42,
        n_jobs=-1,
        class_weight='balanced'  # Handle imbalanced classes (few winners)
    )

    # Time series split — train on older races, validate on newer
    # This prevents the model from "seeing the future"
    dates = pd.Series(meeting_dates)
    cutoff = dates.quantile(0.8)  # Train on oldest 80%, validate on newest 20%

    train_mask = dates <= cutoff
    test_mask = dates > cutoff

    X_train, X_test = X[train_mask], X[test_mask]
    y_roi_train, y_roi_test = y_roi[train_mask], y_roi[test_mask]
    y_won_train, y_won_test = y_won[train_mask], y_won[test_mask]

    log.info(f"Train set: {len(X_train)} horses | Test set: {len(X_test)} horses")

    # Train both models
    rf_roi.fit(X_train, y_roi_train)
    rf_win.fit(X_train, y_won_train)

    # Feature importance — average of both models (normalised)
    roi_importance = rf_roi.feature_importances_
    win_importance = rf_win.feature_importances_

    # Normalise each to sum to 1 then average
    roi_norm = roi_importance / roi_importance.sum()
    win_norm = win_importance / win_importance.sum()
    combined_importance = (roi_norm * 0.6 + win_norm * 0.4)  # Weight ROI higher

    importance_dict = dict(zip(feature_names, combined_importance))
    importance_sorted = dict(sorted(importance_dict.items(), key=lambda x: x[1], reverse=True))

    # Validate on test set
    roi_pred_test = rf_roi.predict(X_test)

    # Simulate betting: back top predicted ROI horse per race
    # (simplified validation — proper validation done in Track B)
    log.info("Top 10 most predictive features:")
    for i, (feat, imp) in enumerate(list(importance_sorted.items())[:10]):
        log.info(f"  {i+1:2d}. {feat:<35} {imp:.4f}")

    return importance_sorted


# ─────────────────────────────────────────────
# STEP 5: TRACK B — COMPONENT ROI ANALYSIS
# ─────────────────────────────────────────────
def parse_components_from_notes(notes_text):
    """
    Parse analyzer.js notes to extract which components fired and their scores.
    Returns list of (component_name, score) tuples.

    Notes format example:
    '+15.0 : Sprint Leader Run Down Bonus — mapped to lead in sprint, narrow loss last start'
    '-20.0 : Old age (7-8yo, 4.5% SR, -40.2% ROI)'
    '+12.0 : LEADER in Sprint (≤1200m)'
    """
    if not notes_text:
        return []

    components = []
    lines = str(notes_text).split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Match pattern: +15.0 : Component Name or -20.0 : Component Name
        match = re.match(r'^([+-]?\d+\.?\d*)\s*:\s*(.+)$', line)
        if match:
            try:
                score = float(match.group(1))
                name = match.group(2).strip()

                # Clean up the name — remove trailing stats like '(+33.4% ROI, 154 races)'
                # Keep the core component name
                name = re.sub(r'\s*[\(\[].*?[\)\]]$', '', name).strip()
                name = re.sub(r'\s*[-—].*$', '', name).strip()  # Remove after dash
                name = name[:100]  # Truncate

                if name and score != 0:
                    components.append((name, score))
            except:
                continue

    return components


def run_component_analysis(df):
    """
    Track B: Parse all prediction notes, calculate per-component ROI.
    Also runs variation testing: what if this component value was different?
    Returns list of component analysis dicts.
    """
    log.info("Running component ROI analysis (Track B)...")

    # Filter to horses that are the TOP PICK in their race
    # (highest analyzer score per race)
    df_with_scores = df.dropna(subset=['analyzer_score']).copy()

    if df_with_scores.empty:
        log.warning("No analyzer scores found. Skipping Track B.")
        return []

    # Find top pick per race
    top_picks = df_with_scores.loc[
        df_with_scores.groupby('race_id')['analyzer_score'].idxmax()
    ].copy()

    log.info(f"Analysing {len(top_picks)} top picks across {len(top_picks)} races")

    # Calculate baseline ROI
    winners = top_picks[top_picks['finish_position'] == 1]
    total_staked = len(top_picks)
    total_returned = winners['sp'].sum() if not winners.empty else 0
    baseline_roi = ((total_returned - total_staked) / total_staked * 100) if total_staked > 0 else 0
    baseline_sr = len(winners) / total_staked * 100 if total_staked > 0 else 0

    log.info(f"Baseline: {total_staked} races, {len(winners)} wins, "
             f"{baseline_sr:.1f}% SR, {baseline_roi:.1f}% ROI")

    # ── Parse components from each top pick's notes ──
    component_data = {}  # component_name -> {'appearances': [], 'wins': [], 'sps': [], 'scores': []}

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
            component_data[comp_name]['roi_contributions'].append(sp if won else -1)

    # ── Calculate stats per component ──
    results = []

    for comp_name, data in component_data.items():
        appearances = data['appearances']
        if appearances < 5:  # Skip tiny samples
            continue

        wins = data['wins']
        strike_rate = wins / appearances * 100
        avg_sp = np.mean(data['sps'])
        roi = (np.sum(data['roi_contributions']) / appearances) * 100
        current_value = np.mean(data['scores']) if data['scores'] else 0

        # ── Variation testing ──
        # What would ROI be if this component was worth 0? (disabled)
        # Simulate by checking if removing this component changes top pick
        # (simplified: just flag as positive/negative/neutral based on ROI)

        if roi > 10:
            verdict = 'BOOST'
            # Suggest increasing by 20-50%
            suggested_value = round(current_value * 1.3, 1)
            roi_delta = roi - baseline_roi
        elif roi < -10:
            verdict = 'REDUCE'
            # Suggest reducing by 30-50%
            suggested_value = round(current_value * 0.5, 1)
            roi_delta = roi - baseline_roi
        elif roi < -5:
            verdict = 'MONITOR'
            suggested_value = round(current_value * 0.75, 1)
            roi_delta = roi - baseline_roi
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

    # Sort by absolute ROI impact (most impactful first)
    results.sort(key=lambda x: abs(x['roi']), reverse=True)

    log.info(f"Analysed {len(results)} components.")
    boost_count = sum(1 for r in results if r['verdict'] == 'BOOST')
    reduce_count = sum(1 for r in results if r['verdict'] == 'REDUCE')
    log.info(f"  {boost_count} components to BOOST, {reduce_count} to REDUCE")

    return results, baseline_roi, baseline_sr, total_staked, len(top_picks[top_picks['finish_position'] == 1])


# ─────────────────────────────────────────────
# CURRENT ANALYZER WEIGHTS (for comparison)
# ─────────────────────────────────────────────
ANALYZER_WEIGHTS = {
    'horse_age': 'Up to ±45 pts (age penalties)',
    'horse_sex': 'Up to ±20 pts (sex bonuses)',
    'horse_weight': 'Up to ±15 pts (weight vs average)',
    'distance': 'Indirect (running position score)',
    'track_condition': 'Up to ±24 pts (condition form)',
    'career_win_rate': '+10 pts if 40%+, -15 if <10%',
    'distance_win_rate': 'Up to ±16 pts',
    'track_win_rate': 'Up to ±12 pts',
    'track_distance_win_rate': 'Up to ±16 pts',
    'condition_win_rate': 'Up to ±24 pts',
    'first_up_win_rate': 'Up to +5 pts',
    'second_up_win_rate': 'Up to +5 pts',
    'l10_win_rate': 'Up to +6 pts per run (recency weighted)',
    'last_position': 'Up to ±25 pts (margin scoring)',
    'last_margin': 'Up to ±25 pts (margin scoring)',
    'last_sp': 'Up to ±50 pts (form price)',
    'days_since_run': 'Up to ±20 pts',
    'pfai_score': 'Blended 30% into final score',
    'last200_rank': 'Up to ±20 pts (API sectionals)',
    'last400_rank': 'Up to ±20 pts (API sectionals)',
    'last600_rank': 'Up to ±20 pts (API sectionals)',
    'running_position': 'Up to ±12 pts (speedmap)',
    'is_aus_bred': '±8-10 pts (country scoring)',
    'distance_change': 'Up to ±8 pts',
    'form_trend': 'Not directly scored',
    'l5_win_rate': 'Indirect via last10 scoring',
    'career_runs': 'Not directly scored',
}


def generate_feature_recommendations(importance_sorted):
    """
    Compare RF feature importance with current analyzer weights.
    Generate recommendations for each feature.
    """
    recommendations = []

    for rank, (feature, importance) in enumerate(importance_sorted.items(), 1):
        current_weight = ANALYZER_WEIGHTS.get(feature, 'Not directly scored')

        # High importance features
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

        # Generate recommendation
        if importance > 0.05 and 'Not directly' in current_weight:
            rec = f"HIGH IMPACT but not directly scored — consider adding explicit scoring"
        elif importance > 0.05:
            rec = f"High predictive power — current scoring appears appropriate"
        elif importance < 0.01 and 'Not directly' not in current_weight:
            rec = f"Low predictive power — consider reducing weight in analyzer"
        else:
            rec = f"Moderate predictive power — current scoring reasonable"

        recommendations.append({
            'feature_name': feature,
            'importance_score': round(float(importance), 6),
            'importance_rank': rank,
            'importance_label': importance_label,
            'current_analyzer_weight': current_weight,
            'recommendation': rec
        })

    return recommendations


# ─────────────────────────────────────────────
# STEP 6: WRITE RESULTS TO DB
# ─────────────────────────────────────────────
def write_results(run_id, feature_recommendations, component_results,
                  baseline_roi, baseline_sr, total_races, total_horses):
    """Write all backtest findings to the database."""
    log.info("Writing results to database...")

    with engine.connect() as conn:
        # Write feature importance
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

        # Write component analysis
        for comp in component_results:
            conn.execute(text("""
                INSERT INTO backtest_component_analysis
                (run_id, component_name, appearances, wins, strike_rate, roi,
                 avg_sp, current_value, suggested_value, roi_delta, verdict)
                VALUES (:run_id, :component_name, :appearances, :wins, :strike_rate,
                        :roi, :avg_sp, :current_value, :suggested_value, :roi_delta, :verdict)
            """), {'run_id': run_id, **comp})

        # Update run record as complete
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

    # Ensure tables exist
    ensure_tables()

    # Create a run record
    with engine.connect() as conn:
        result = conn.execute(text("""
            INSERT INTO backtest_runs (status) VALUES ('running') RETURNING id
        """))
        run_id = result.fetchone()[0]
        conn.commit()
    log.info(f"Backtest run ID: {run_id}")

    try:
        # Load data
        df = load_historical_data()

        if len(df) < 50:
            log.warning("Not enough historical data to run backtest (need 50+ races with results).")
            with engine.connect() as conn:
                conn.execute(text(
                    "UPDATE backtest_runs SET status='failed', notes='Insufficient data' WHERE id=:id"
                ), {'id': run_id})
                conn.commit()
            return

        # Build ML training set
        X, y_roi, y_won, race_ids, horse_ids, meeting_dates = build_training_set(df)

        # Track A: Random Forest
        importance_sorted = run_random_forest(X, y_roi, y_won, meeting_dates)
        feature_recommendations = generate_feature_recommendations(importance_sorted)

        # Track B: Component Analysis
        component_results_tuple = run_component_analysis(df)
        if isinstance(component_results_tuple, tuple):
            component_results, baseline_roi, baseline_sr, total_races, total_wins = component_results_tuple
        else:
            component_results = []
            baseline_roi = 0.0
            baseline_sr = 0.0
            total_races = df['race_id'].nunique()
            total_wins = 0

        total_horses = len(df)

        # Write everything to DB
        write_results(
            run_id,
            feature_recommendations,
            component_results,
            baseline_roi,
            baseline_sr,
            total_races,
            total_horses
        )

        log.info("=" * 60)
        log.info("BACKTEST JOB COMPLETE")
        log.info(f"Run ID: {run_id}")
        log.info(f"Races analysed: {total_races}")
        log.info(f"Baseline ROI: {baseline_roi:.1f}%")
        log.info(f"Baseline SR: {baseline_sr:.1f}%")
        log.info(f"Features analysed: {len(feature_recommendations)}")
        log.info(f"Components analysed: {len(component_results)}")
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
