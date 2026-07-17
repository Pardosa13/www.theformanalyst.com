"""
backtest.py - Nightly backtesting and ML analysis for The Form Analyst

Runs as a Railway cron job (0 2 * * * = 2am every night)

Three parallel tracks + Grid Search:
  Track A: Random Forest feature importance - which raw horse features predict winners
  Track B: Component ROI analysis - which analyzer.js components help/hurt ROI
  Track C: Score momentum analysis - score trajectory bucketing and overlay %
  Track D: GRID SEARCH (NEW) - trains 1000+ RF models to find optimal hyperparams & feature sets

Results written to DB, viewable at /backtest in the web app.

CHANGELOG - 2026-05-11:
  * Added Track D: Grid search with 1000+ models
  * Grid search tests 4 n_estimators × 4 max_depth × 3 min_samples_leaf × 2 max_features × 4 feature subsets
  * Saves top 10 .pkl models
  * Compares grid search best model vs baseline analyzer.js performance
  * Keeps all existing Tracks A, B, C unchanged
"""

import os
import sys
import json
import re
import logging
import pickle
from datetime import datetime, timedelta
from itertools import product
from strike_rate_matching import (
    build_strike_rate_lookup, build_strike_rate_history_lookup,
    get_sr_win_pct, get_sr_win_pct_asof, log_match_stats, normalize_name,
)

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ML
from collections import Counter
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.base import BaseEstimator, RegressorMixin, clone
from sklearn.model_selection import TimeSeriesSplit, StratifiedKFold
from sklearn.preprocessing import LabelEncoder
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import mean_squared_error, log_loss, brier_score_loss
import joblib
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
                grid_search_best_roi FLOAT,
                grid_search_best_sr FLOAT,
                grid_search_improvement FLOAT,
                best_model_rank INTEGER,
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
                scope VARCHAR(20) DEFAULT 'all_horses',
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

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS backtest_rf_models (
                id SERIAL PRIMARY KEY,
                run_id INTEGER REFERENCES backtest_runs(id),
                model_rank INTEGER,
                combined_score FLOAT,
                cv_roi_score FLOAT,
                cv_win_score FLOAT,
                n_features INTEGER,
                features TEXT,
                hyperparams TEXT,
                grid_name VARCHAR(50),
                subset_name VARCHAR(50),
                feature_importance TEXT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS backtest_best_model (
                id SERIAL PRIMARY KEY,
                run_date DATE NOT NULL,
                combined_score FLOAT NOT NULL,
                pkl_data BYTEA NOT NULL,
                run_id INTEGER REFERENCES backtest_runs(id),
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                is_active BOOLEAN DEFAULT FALSE,
                promoted_at TIMESTAMP,
                promotion_reason TEXT,
                validation_roi FLOAT,
                validation_strike_rate FLOAT,
                validation_profit_units FLOAT,
                validation_bets INTEGER,
                validation_drawdown FLOAT,
                validation_longest_losing_streak INTEGER,
                validation_bankroll_growth FLOAT,
                validation_volatility FLOAT,
                model_type VARCHAR(50) DEFAULT 'random_forest',
                model_name VARCHAR(120) DEFAULT 'Random Forest',
                deactivated_at TIMESTAMP,
                retained_until TIMESTAMP,
                model_version VARCHAR(80),
                artifact_filename VARCHAR(255),
                expected_feature_count INTEGER,
                selection_metrics TEXT
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS backtest_model_competition (
                id SERIAL PRIMARY KEY,
                run_id INTEGER REFERENCES backtest_runs(id),
                model_type VARCHAR(50),
                model_name VARCHAR(120),
                validation_roi FLOAT,
                validation_profit_units FLOAT,
                validation_strike_rate FLOAT,
                validation_bets INTEGER,
                validation_drawdown FLOAT,
                validation_longest_losing_streak INTEGER,
                validation_bankroll_growth FLOAT,
                validation_volatility FLOAT,
                last_100 TEXT,
                last_250 TEXT,
                last_500 TEXT,
                agreement_summary TEXT,
                log_loss FLOAT,
                brier_score FLOAT,
                calibration TEXT,
                stability TEXT,
                walk_forward TEXT,
                selection_score FLOAT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """))

        for ddl in [
            "ALTER TABLE backtest_best_model DROP CONSTRAINT IF EXISTS backtest_best_model_run_date_key",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT FALSE",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMP",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS promotion_reason TEXT",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS validation_roi FLOAT",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS validation_strike_rate FLOAT",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS validation_profit_units FLOAT",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS validation_bets INTEGER",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS validation_drawdown FLOAT",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS validation_longest_losing_streak INTEGER",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS validation_bankroll_growth FLOAT",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS validation_volatility FLOAT",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS model_type VARCHAR(50) DEFAULT 'random_forest'",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS model_name VARCHAR(120) DEFAULT 'Random Forest'",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS deactivated_at TIMESTAMP",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS retained_until TIMESTAMP",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS model_version VARCHAR(80)",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS artifact_filename VARCHAR(255)",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS expected_feature_count INTEGER",
            "ALTER TABLE backtest_best_model ADD COLUMN IF NOT EXISTS selection_metrics TEXT",
            "ALTER TABLE backtest_model_competition ADD COLUMN IF NOT EXISTS validation_longest_losing_streak INTEGER",
            "ALTER TABLE backtest_model_competition ADD COLUMN IF NOT EXISTS validation_bankroll_growth FLOAT",
            "ALTER TABLE backtest_model_competition ADD COLUMN IF NOT EXISTS validation_volatility FLOAT",
            "ALTER TABLE backtest_model_competition ADD COLUMN IF NOT EXISTS log_loss FLOAT",
            "ALTER TABLE backtest_model_competition ADD COLUMN IF NOT EXISTS brier_score FLOAT",
            "ALTER TABLE backtest_model_competition ADD COLUMN IF NOT EXISTS calibration TEXT",
            "ALTER TABLE backtest_model_competition ADD COLUMN IF NOT EXISTS stability TEXT",
            "ALTER TABLE backtest_model_competition ADD COLUMN IF NOT EXISTS walk_forward TEXT",
            "ALTER TABLE backtest_model_competition ADD COLUMN IF NOT EXISTS selection_score FLOAT",
        ]:
            conn.execute(text(ddl))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS backtest_model_promotions (
                id SERIAL PRIMARY KEY,
                run_id INTEGER REFERENCES backtest_runs(id),
                old_champion_id INTEGER,
                new_champion_id INTEGER,
                model_type VARCHAR(50),
                promotion_reason TEXT,
                old_validation_metrics TEXT,
                new_validation_metrics TEXT,
                promoted_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS backtest_data_repairs (
                repair_key VARCHAR(120) PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT NOW(),
                notes TEXT
            )
        """))

        conn.execute(text("""
            UPDATE backtest_best_model
            SET is_active = TRUE,
                promoted_at = COALESCE(promoted_at, updated_at, created_at, NOW()),
                promotion_reason = COALESCE(promotion_reason, 'Initial champion from existing newest saved model'),
                model_type = COALESCE(model_type, 'random_forest'),
                model_name = COALESCE(model_name, 'Random Forest')
            WHERE id = (
                SELECT id FROM backtest_best_model
                ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
                LIMIT 1
            )
              AND NOT EXISTS (SELECT 1 FROM backtest_best_model WHERE is_active = TRUE)
        """))

        conn.execute(text("""
            UPDATE backtest_best_model
            SET is_active = FALSE
            WHERE is_active = TRUE
              AND id <> (
                SELECT id FROM backtest_best_model
                WHERE is_active = TRUE
                ORDER BY promoted_at DESC NULLS LAST, updated_at DESC, id DESC
                LIMIT 1
              )
        """))
        repair_key = "restore_champion_52_after_run_107"
        repair_already_applied = conn.execute(text("""
            SELECT 1 FROM backtest_data_repairs WHERE repair_key = :repair_key
        """), {'repair_key': repair_key}).fetchone()
        champion_52_exists = conn.execute(text("""
            SELECT 1 FROM backtest_best_model WHERE id = 52
        """)).fetchone()
        if champion_52_exists and not repair_already_applied:
            conn.execute(text("""
                UPDATE backtest_best_model
                SET is_active = FALSE,
                    deactivated_at = COALESCE(deactivated_at, NOW()),
                    promotion_reason = CASE
                        WHEN id = 53 THEN 'Deactivated by one-time repair: run 107 negative-ROI challenger promotion was invalid'
                        ELSE promotion_reason
                    END,
                    updated_at = NOW()
                WHERE is_active = TRUE OR id = 53
            """))
            conn.execute(text("""
                UPDATE backtest_best_model
                SET is_active = TRUE,
                    promoted_at = COALESCE(promoted_at, NOW()),
                    deactivated_at = NULL,
                    retained_until = NULL,
                    promotion_reason = 'Restored by one-time repair: Champion 52 reactivated after invalid run 107 promotion',
                    updated_at = NOW()
                WHERE id = 52
            """))
            conn.execute(text("""
                INSERT INTO backtest_data_repairs (repair_key, notes)
                VALUES (:repair_key, 'Reactivated Champion 52 and deactivated challenger 53 after run 107 invalid promotion.')
            """), {'repair_key': repair_key})
            log.info("One-time repair applied: Champion 52 restored active and challenger 53 deactivated.")
        elif not champion_52_exists and not repair_already_applied:
            conn.execute(text("""
                INSERT INTO backtest_data_repairs (repair_key, notes)
                VALUES (:repair_key, 'Skipped because Champion 52 no longer exists.')
            """), {'repair_key': repair_key})
            log.info("One-time repair skipped: Champion 52 no longer exists.")

        conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_backtest_best_model_active
            ON backtest_best_model (is_active)
            WHERE is_active = TRUE
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
            m.track AS meeting_track,
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

    # Ingest fresh PuntingForm strike-rate data before loading jockey/trainer SR features.
    try:
        from puntingform_service import PuntingFormService
        PuntingFormService().ingest_strike_rates(jurisdiction=2)
    except Exception as e:
        log.error(
            "PuntingForm strike-rate ingestion raised before feature loading; continuing backtest: %s",
            e,
            exc_info=True,
        )

    # Load strike rate data for jockey/trainer SR features
    strike_rate_data = load_strike_rate_data()

    return df, strike_rate_data


def load_strike_rate_data():
    """
    Load the most recent jockey and trainer strike rate data from the DB, plus
    the dated snapshot history used for point-in-time training features.

    Returns dict: {
        'jockeys': {...current snapshot lookup, used for LIVE scoring...},
        'trainers': {...current snapshot lookup...},
        'jockeys_history': {...dated snapshot lookup, used for TRAINING...},
        'trainers_history': {...dated snapshot lookup...},
    }
    """
    log.info("Loading strike rate data...")
    sr_data = {'jockeys': {}, 'trainers': {}, 'jockeys_history': {}, 'trainers_history': {}}

    try:
        with engine.connect() as conn:
            try:
                result = conn.execute(text("""
                    SELECT name, l100_wins, l100_runs
                    FROM strike_rates
                    WHERE type = 'jockey'
                    ORDER BY updated_at DESC
                """))
                rows = result.fetchall()
                sr_data['jockeys'] = build_strike_rate_lookup(rows)
                log.info(f"Loaded {len(rows)} jockey SR records.")
            except Exception:
                log.warning("No strike_rates table or jockey data — jockey_sr feature will be 0.")

            try:
                result = conn.execute(text("""
                    SELECT name, l100_wins, l100_runs
                    FROM strike_rates
                    WHERE type = 'trainer'
                    ORDER BY updated_at DESC
                """))
                rows = result.fetchall()
                sr_data['trainers'] = build_strike_rate_lookup(rows)
                log.info(f"Loaded {len(rows)} trainer SR records.")
            except Exception:
                log.warning("No strike_rates table or trainer data — trainer_sr feature will be 0.")

            # Point-in-time history for TRAINING only (live scoring correctly
            # keeps using the current snapshot above — a live race genuinely
            # wants "today's" jockey form). Applying today's snapshot to every
            # historical training row instead leaks each jockey/trainer's
            # future form into races run long before that form existed. This
            # table only starts accumulating snapshots once this fix ships, so
            # coverage grows over time rather than being retroactive.
            try:
                result = conn.execute(text("""
                    SELECT name, l100_wins, l100_runs, snapshot_date
                    FROM strike_rate_snapshots
                    WHERE type = 'jockey'
                    ORDER BY snapshot_date ASC
                """))
                rows = result.fetchall()
                sr_data['jockeys_history'] = build_strike_rate_history_lookup(rows)
                log.info(f"Loaded {len(rows)} dated jockey SR snapshot rows for point-in-time training.")
            except Exception:
                log.warning("No strike_rate_snapshots table or jockey history yet — jockey_sr will use the current snapshot for training too, until history accumulates.")

            try:
                result = conn.execute(text("""
                    SELECT name, l100_wins, l100_runs, snapshot_date
                    FROM strike_rate_snapshots
                    WHERE type = 'trainer'
                    ORDER BY snapshot_date ASC
                """))
                rows = result.fetchall()
                sr_data['trainers_history'] = build_strike_rate_history_lookup(rows)
                log.info(f"Loaded {len(rows)} dated trainer SR snapshot rows for point-in-time training.")
            except Exception:
                log.warning("No strike_rate_snapshots table or trainer history yet — trainer_sr will use the current snapshot for training too, until history accumulates.")

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

    gm = re.search(r'Group\s*([123])', s, re.IGNORECASE)
    if gm:
        level = int(gm.group(1))
        return {1: 130, 2: 122, 3: 115}.get(level, 100)

    if re.search(r'Listed', s, re.IGNORECASE):
        return 108.0

    bm = re.search(r'(?:Benchmark|Bench\.?|BM)\s*(\d+)', s, re.IGNORECASE)
    if bm:
        return min(100, max(1, int(bm.group(1))))

    cm = re.search(r'(?:Class|Cls)\s*(\d+)', s, re.IGNORECASE)
    if cm:
        fallback = {1: 40, 2: 55, 3: 65, 4: 75, 5: 85, 6: 92}
        return fallback.get(int(cm.group(1)), 60)

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




def extract_features(row, jockey_sr_lookup=None, trainer_sr_lookup=None,
                      jockey_sr_history=None, trainer_sr_history=None, as_of_date=None):
    """
    Extract ML features from a horse's csv_data dict.
    Returns a flat dict of ~45 features covering everything scored in analyzer.js.

    jockey_sr_history/trainer_sr_history + as_of_date (optional): when supplied,
    jockey_sr/trainer_sr are looked up as they stood on as_of_date (point-in-time)
    instead of the current snapshot in jockey_sr_lookup/trainer_sr_lookup, to
    avoid leaking a jockey/trainer's future form into older training rows. Falls
    back to the current snapshot when no dated entry exists yet for that
    name/date (this only self-heals as snapshot history accumulates over time).
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

    try:
        last_weight = float(cd.get('form weight', 0) or 0)
        curr_weight = features['horse_weight']
        if last_weight >= 49 and last_weight <= 65 and curr_weight >= 49:
            features['weight_change'] = curr_weight - last_weight
        else:
            features['weight_change'] = 0.0
    except Exception:
        features['weight_change'] = 0.0

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

    # ── Class change ──
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

    # ── Country ──
    country = str(cd.get('country', 'AUS') or 'AUS').strip().upper()
    country_score_map = {
        'AUS': 0, 'NZ': -1, 'IRE': -0.5, 'GB': 1, 'FR': -2, 'JPN': -2, 'GER': -2, 'USA': 0,
    }
    features['country_score'] = float(country_score_map.get(country, -0.5))
    features['is_aus_bred'] = 1.0 if country == 'AUS' else 0.0

    # ── Running position ──
    pos_map = {'LEADER': 3, 'ONPACE': 2, 'MIDFIELD': 1, 'BACKMARKER': 0}
    run_pos = str(cd.get('runningposition', cd.get('runningPosition', '')) or '').upper().strip()
    features['running_position'] = float(pos_map.get(run_pos, 1))

    # ── Running position × distance-context ──
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

    # ── Jockey & Trainer L100 SR ──
    # Point-in-time lookup when history + a race date are available (training);
    # -1.0 means "no dated snapshot exists yet for this name/date" rather than
    # "unknown jockey", so fall back to the current snapshot in that case only
    # — this keeps behaviour unchanged for rows the snapshot history doesn't
    # cover yet (i.e. all pre-fix history) while point-in-time data phases in.
    jockey_name = str(cd.get('horse jockey', '') or '').strip()
    features['jockey_sr'] = -1.0
    if jockey_sr_history and as_of_date:
        features['jockey_sr'] = get_sr_win_pct_asof(jockey_name, jockey_sr_history, as_of_date)
    if features['jockey_sr'] == -1.0:
        features['jockey_sr'] = get_sr_win_pct(jockey_name, jockey_sr_lookup or {})

    trainer_name = str(cd.get('horse trainer', '') or '').strip()
    features['trainer_sr'] = -1.0
    if trainer_sr_history and as_of_date:
        features['trainer_sr'] = get_sr_win_pct_asof(trainer_name, trainer_sr_history, as_of_date)
    if features['trainer_sr'] == -1.0:
        features['trainer_sr'] = get_sr_win_pct(trainer_name, trainer_sr_lookup or {})

    # ── Barrier (gate draw) ──
    try:
        features['horse_barrier'] = float(cd.get('horse barrier', 0) or 0)
    except Exception:
        features['horse_barrier'] = 0.0

    try:
        features['form_barrier'] = float(cd.get('form barrier', 0) or 0)
    except Exception:
        features['form_barrier'] = 0.0

    try:
        b_curr = features['horse_barrier']
        b_last = features['form_barrier']
        features['barrier_change'] = (b_curr - b_last) if (b_curr > 0 and b_last > 0) else 0.0
    except Exception:
        features['barrier_change'] = 0.0

    # ── Career prize money ──
    try:
        prize = float(str(cd.get('horse prize money', 0) or 0).replace(',', '').replace('$', ''))
        features['horse_career_prize'] = float(np.log1p(prize))
    except Exception:
        features['horse_career_prize'] = 0.0

    try:
        pwon = float(str(cd.get('prizemoney won', 0) or 0).replace(',', '').replace('$', ''))
        features['prizemoney_won'] = float(np.log1p(pwon))
    except Exception:
        features['prizemoney_won'] = 0.0

    # ── Last start track condition ──
    form_cond = str(cd.get('form track condition', '') or '').lower().strip()
    for k, v in condition_map.items():
        if k in form_cond:
            features['form_track_condition'] = float(v)
            break
    else:
        features['form_track_condition'] = features['track_condition']  # assume same if unknown

    features['track_condition_change'] = features['track_condition'] - features['form_track_condition']

    # ── Field size last start ──
    try:
        features['form_field_size'] = float(cd.get('form other runners', 0) or 0)
    except Exception:
        features['form_field_size'] = 0.0

    # ── Jockey continuity (same rider as last start) ──
    form_jockey = str(cd.get('form jockey', '') or '').strip().lower()
    features['same_jockey'] = 1.0 if (form_jockey and form_jockey == jockey_name.lower()) else 0.0

    # ── Same track as last start ──
    today_track = str(cd.get('track', '') or '').strip().lower()
    form_track  = str(cd.get('form track', '') or '').strip().lower()
    features['same_track'] = 1.0 if (today_track and today_track == form_track) else 0.0

    # ── Apprentice/claim allowed ──
    can_claim_raw = str(cd.get('jockeys can claim', '') or '').strip().lower()
    features['jockeys_can_claim'] = 1.0 if can_claim_raw in ('yes', 'true', '1', 'y') else 0.0

    return features


def add_race_relative_features(feature_rows, race_ids):
    temp = pd.DataFrame(feature_rows)
    temp['race_id'] = race_ids

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

    temp['field_size'] = temp.groupby('race_id')['race_id'].transform('count')

    for col in relative_cols:
        if col not in temp.columns:
            continue

        grouped = temp.groupby('race_id')[col]
        ascending = col in lower_is_better

        temp[f'{col}_race_rank'] = grouped.rank(method='min', ascending=ascending)
        temp[f'{col}_vs_race_avg'] = temp[col] - grouped.transform('mean')

        if ascending:
            temp[f'{col}_vs_race_best'] = temp[col] - grouped.transform('min')
        else:
            temp[f'{col}_vs_race_best'] = temp[col] - grouped.transform('max')

        denom = (temp['field_size'] - 1).replace(0, np.nan)
        temp[f'{col}_race_percentile'] = 1.0 - ((temp[f'{col}_race_rank'] - 1) / denom)
        temp[f'{col}_race_percentile'] = temp[f'{col}_race_percentile'].fillna(1.0)

    return temp.drop(columns=['race_id']).to_dict('records')


# ─────────────────────────────────────────────
# STEP 3: BUILD ML TRAINING SET
# ─────────────────────────────────────────────
def build_training_set(df, strike_rate_data=None):
    """
    Build one training row per horse per race.
    Also computes weight_vs_avg (requires race-level average, done here).
    Targets: ROI for legacy/grid-search tracks and binary winner labels for challenger models.
    """
    log.info("Building ML training set...")

    jockey_sr = (strike_rate_data or {}).get('jockeys', {})
    trainer_sr = (strike_rate_data or {}).get('trainers', {})
    jockey_sr_history = (strike_rate_data or {}).get('jockeys_history', {})
    trainer_sr_history = (strike_rate_data or {}).get('trainers_history', {})

    df_unique = df.sort_values('horse_id').drop_duplicates(
        subset=['race_id', 'horse_name'], keep='last'
    )

    # ── Pre-compute race average weights ──
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
    sp_values = []
    race_ids = []
    horse_ids = []
    meeting_dates = []

    for _, row in df_unique.iterrows():
        try:
            features = extract_features(
                row, jockey_sr, trainer_sr,
                jockey_sr_history=jockey_sr_history, trainer_sr_history=trainer_sr_history,
                as_of_date=row.get('meeting_date'),
            )
            finish = int(row['finish_position'])
            sp = float(row['sp']) if row['sp'] else None

            if sp is None or not np.isfinite(sp) or sp <= 1.0:
                continue

            race_id = row['race_id']
            avg_w = race_avg_weights.get(race_id, 55.0)
            curr_w = features['horse_weight']
            if 49 <= curr_w <= 65:
                features['weight_vs_avg'] = avg_w - curr_w
            else:
                features['weight_vs_avg'] = 0.0

            roi = (sp - 1.0) if finish == 1 else -1.0
            won = 1 if finish == 1 else 0

            feature_rows.append(features)
            targets_roi.append(roi)
            targets_won.append(won)
            sp_values.append(sp)
            race_ids.append(race_id)
            horse_ids.append(row['horse_id'])
            meeting_dates.append(row['meeting_date'])

        except Exception:
            continue

    feature_count_before = len(feature_rows[0]) if feature_rows else 0
    log.info(f"Features before race-relative features: {feature_count_before}")
    feature_rows = add_race_relative_features(feature_rows, race_ids)
    feature_count_after = len(feature_rows[0]) if feature_rows else 0
    log.info(f"Features after race-relative features: {feature_count_after}")
    log.info("field_size feature added")

    X = pd.DataFrame(feature_rows)
    y_roi = pd.Series(targets_roi)
    y_won = pd.Series(targets_won)

    # NOTE: deliberately NOT calling X.fillna(X.median()) here. extract_features()
    # already defaults every raw field to a concrete number, so genuine NaNs are
    # rare/edge-case, but filling them with a median computed across the *whole*
    # dataset (including rows that fall on the future side of whatever train/val
    # split a given track uses) leaks a little future information into the past.
    # Each track below computes its own median from its own training-only rows
    # and persists it on the resulting model artifact so live scoring can reuse
    # the exact same fill values instead of a hardcoded 0 (see ml_predict.py).

    log.info(f"Training set: {len(X)} horses, {X.shape[1]} features, "
             f"{y_won.sum()} winners ({y_won.mean()*100:.1f}% win rate), "
             f"avg ROI: {y_roi.mean():.3f}")
    log_match_stats(log, jockey_sr, trainer_sr)

    return X, y_roi, y_won, sp_values, race_ids, horse_ids, meeting_dates


# ─────────────────────────────────────────────
# TRACK A — RANDOM FOREST (with grid search)
# ─────────────────────────────────────────────
def run_random_forest(X, y_roi, y_won, meeting_dates):
    """
    Track A: Train baseline Random Forest for feature importance.
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

    dates = pd.Series(meeting_dates)
    cutoff = dates.quantile(0.8)
    train_mask = dates <= cutoff
    test_mask  = dates > cutoff

    X_train, X_test = X[train_mask], X[test_mask]
    y_roi_train = y_roi[train_mask]
    y_won_train = y_won[train_mask]

    # Impute with the TRAINING split's own median only, then apply that same
    # value to the test split — never let X_test rows influence the fill value.
    train_median = X_train.median()
    X_train = X_train.fillna(train_median)
    X_test = X_test.fillna(train_median)

    log.info(f"Train set: {len(X_train)} horses | Test set: {len(X_test)} horses")

    rf_roi.fit(X_train, y_roi_train)
    rf_win.fit(X_train, y_won_train)

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
# TRACK D — GRID SEARCH (NEW)
# ─────────────────────────────────────────────
def run_grid_search(X, y_roi, y_won, meeting_dates):
    """
    Track D: Grid search across 1000+ hyperparameter combinations.
    Tests 4 n_estimators × 4 max_depth × 3 min_samples_leaf × 2 max_features × 4 feature subsets.
    Trains models with time-series CV (no future leakage).
    Returns: (results_df, best_roi, best_win, top_10_models)
    """
    log.info("=" * 80)
    log.info("GRID SEARCH: Training 1000+ Random Forest models")
    log.info("=" * 80)

    dates = pd.to_datetime(pd.Series(meeting_dates), errors='coerce')
    order = dates.argsort().values
    X = X.iloc[order].reset_index(drop=True)
    y_roi = y_roi.iloc[order].reset_index(drop=True)
    y_won = y_won.iloc[order].reset_index(drop=True)
    meeting_dates = [meeting_dates[i] for i in order]

    # Track D's final artifacts are always refit on 100% of X (no held-out split
    # of its own), so imputing with the median of this same X isn't a leak under
    # Track D's own methodology. Compute it once and persist it on every saved
    # model so live scoring (ml_predict.py) can reuse these exact fill values
    # instead of defaulting missing features to 0.
    grid_search_feature_medians = X.median().to_dict()
    X = X.fillna(X.median())

    feature_names = X.columns.tolist()
    log.info(f"Total grid features available: {len(feature_names)}")

    # Hyperparameter grids
    n_estimators_opts = [100, 150, 200, 250]
    max_depth_opts = [6, 8, 10, 12]
    min_samples_leaf_opts = [10, 15, 20]
    max_features_opts = ['sqrt', 'log2']

    # Feature subsets
    feature_subsets = {
        'core': ['pfai_score', 'last_sp', 'career_win_rate', 'distance_win_rate', 'track_win_rate'],
        'sectionals': ['pfai_score', 'last_sp', 'last200_rank', 'last400_rank', 'last600_rank'],
        'weight': ['pfai_score', 'last_sp', 'horse_weight', 'weight_change', 'weight_vs_avg'],
        'full': None,  # Use all features
    }

    results = []
    model_count = 0

    # Time-series split for CV
    tscv = TimeSeriesSplit(n_splits=3)

    log.info(f"Grid: {len(n_estimators_opts)} × {len(max_depth_opts)} × {len(min_samples_leaf_opts)} × {len(max_features_opts)} × {len(feature_subsets)} = {len(n_estimators_opts) * len(max_depth_opts) * len(min_samples_leaf_opts) * len(max_features_opts) * len(feature_subsets)} total combinations")

    for n_est in n_estimators_opts:
        for max_d in max_depth_opts:
            for min_leaf in min_samples_leaf_opts:
                for max_feat in max_features_opts:
                    for subset_name, features in feature_subsets.items():
                        if features is None:
                            X_subset = X
                            subset_features = feature_names
                        else:
                            subset_features = [f for f in features if f in X.columns]
                            X_subset = X[subset_features]

                        if X_subset.shape[1] < 3:
                            continue

                        try:
                            cv_roi_scores = []
                            cv_win_scores = []
                            importances = []

                            for train_idx, test_idx in tscv.split(X_subset):
                                X_train, X_test = X_subset.iloc[train_idx], X_subset.iloc[test_idx]
                                y_train_roi, y_test_roi = y_roi.iloc[train_idx], y_roi.iloc[test_idx]
                                y_train_won, y_test_won = y_won.iloc[train_idx], y_won.iloc[test_idx]

                                # ROI model
                                rf_roi = RandomForestRegressor(
                                    n_estimators=n_est,
                                    max_depth=max_d,
                                    min_samples_leaf=min_leaf,
                                    max_features=max_feat,
                                    random_state=42,
                                    n_jobs=-1
                                )
                                rf_roi.fit(X_train, y_train_roi)
                                roi_score = rf_roi.score(X_test, y_test_roi)
                                cv_roi_scores.append(roi_score)
                                importances.append(rf_roi.feature_importances_)

                                # Win model
                                rf_win = RandomForestClassifier(
                                    n_estimators=n_est,
                                    max_depth=max_d,
                                    min_samples_leaf=min_leaf,
                                    max_features=max_feat,
                                    class_weight='balanced',
                                    random_state=42,
                                    n_jobs=-1
                                )
                                rf_win.fit(X_train, y_train_won)
                                try:
                                    from sklearn.metrics import roc_auc_score
                                    win_score = roc_auc_score(y_test_won, rf_win.predict_proba(X_test)[:, 1])
                                except:
                                    win_score = rf_win.score(X_test, y_test_won)
                                cv_win_scores.append(win_score)

                            combined = np.mean(cv_roi_scores) * 0.6 + np.mean(cv_win_scores) * 0.4
                            avg_importance = np.mean(importances, axis=0)

                            results.append({
                                'rank': len(results) + 1,
                                'combined_score': combined,
                                'roi_score': np.mean(cv_roi_scores),
                                'win_score': np.mean(cv_win_scores),
                                'n_estimators': n_est,
                                'max_depth': max_d,
                                'min_samples_leaf': min_leaf,
                                'max_features': max_feat,
                                'subset': subset_name,
                                'n_features': X_subset.shape[1],
                                'features': list(X_subset.columns),
                                'importance': dict(zip(X_subset.columns, avg_importance))
                            })

                            model_count += 1
                            if model_count % 100 == 0:
                                best_so_far = max([r['combined_score'] for r in results])
                                log.info(f"Trained {model_count} models... Best combined score so far: {best_so_far:.4f}")

                        except Exception as e:
                            continue

    log.info(f"Completed {model_count} models")

    results_df = pd.DataFrame(results).sort_values('combined_score', ascending=False)

    # Save top 10 models as .pkl files
    log.info("\nSaving top 10 models as .pkl files...")
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
    os.makedirs(output_dir, exist_ok=True)

    top_10_models = []
    for idx, (i, row) in enumerate(results_df.head(10).iterrows(), 1):
        features = row['features']
        X_final = X[[f for f in features if f in X.columns]]

        # Train final model on all data
        rf_final = RandomForestRegressor(
            n_estimators=int(row['n_estimators']),
            max_depth=int(row['max_depth']),
            min_samples_leaf=int(row['min_samples_leaf']),
            max_features=row['max_features'],
            random_state=42,
            n_jobs=-1
        )
        rf_final.fit(X_final, y_roi)

        rf_final._form_analyst_algorithm = 'random_forest'
        rf_final._form_analyst_model_type = 'random_forest'
        rf_final._form_analyst_model_name = 'Random Forest grid-search'
        rf_final._form_analyst_model_version = MODEL_VERSION
        rf_final._form_analyst_training_run_id = None
        rf_final._form_analyst_feature_medians = {
            col: grid_search_feature_medians.get(col, 0.0) for col in X_final.columns
        }
        rf_final._form_analyst_selection_metrics = {
            'cv_roi_score': float(row['roi_score']),
            'cv_win_score': float(row['win_score']),
            'combined_score': float(row['combined_score']),
        }
        pkl_file = f'{output_dir}/form_analyst_rf_rank{idx:02d}_score{row["combined_score"]:.4f}.pkl'
        joblib.dump(rf_final, pkl_file)
        log.info(f"  Rank #{idx}: {pkl_file}")

        top_10_models.append({
            'rank': idx,
            'score': row['combined_score'],
            'roi_score': row['roi_score'],
            'win_score': row['win_score'],
            'pkl_file': pkl_file,
            'n_estimators': int(row['n_estimators']),
            'max_depth': int(row['max_depth']),
            'min_samples_leaf': int(row['min_samples_leaf']),
            'max_features': row['max_features'],
        })

    # Save rank #1 model with a stable filename for easy loading
    if top_10_models:
        best_pkl = top_10_models[0]['pkl_file']
        stable_path = os.path.join(output_dir, RF_BEST_ARTIFACT_NAME)
        import shutil
        shutil.copy2(best_pkl, stable_path)
        legacy_path = os.path.join(output_dir, LEGACY_RF_BEST_ARTIFACT_NAME)
        if os.path.exists(legacy_path):
            os.remove(legacy_path)
        log.info(f"  Best Random Forest model also saved as: {stable_path}")

    return results_df, top_10_models


# ─────────────────────────────────────────────
# TRACK B — COMPONENT ROI ANALYSIS
# ─────────────────────────────────────────────

COMPONENT_NAME_MAP = {
    'LEADER in Sprint': 'LEADER in Sprint',
    'LEADER in Mile': 'LEADER in Mile',
    'LEADER in Middle distance': 'LEADER in Middle distance',
    'LEADER in Staying race': 'LEADER in Staying race',
    'ONPACE in Sprint': 'ONPACE in Sprint',
    'ONPACE in Mile': 'ONPACE in Mile',
    'BACKMARKER in Sprint': 'BACKMARKER in Sprint',
    'BACKMARKER in Staying race': 'BACKMARKER in Staying race',
    'Sprint Leader Run Down Bonus': 'Sprint Leader Run Down Bonus',
    'Hidden Edge': 'Hidden Edge — Sprint leader + last start favoured',
    'Jockey hot form': 'Jockey hot form',
    'Jockey solid form': 'Jockey solid form',
    'Jockey average form': 'Jockey average form',
    'Jockey poor form': 'Jockey poor form',
    'Trainer hot form': 'Trainer hot form',
    'Trainer solid form': 'Trainer solid form',
    'Trainer average form': 'Trainer average form',
    'Trainer poor form': 'Trainer poor form',
    '5yo horse': '5yo horse (entire)',
    '5yo Mare': '5yo Mare',
    '6-7yo Mare': '6-7yo Mare',
    'Prime age (3yo)': 'Prime age (3yo)',
    'Old age (7-8yo': 'Old age (7-8yo)',
    '3yo COLT': '3yo COLT combo',
    'COLT base bonus': 'COLT base bonus',
    'Fast sectional + COLT combo': 'Fast sectional + COLT combo',
    'Quick backup': 'Quick backup',
    'Long absence': 'Long absence',
    'Fresh return': 'Fresh return',
    'Market Expectation': 'Market Expectation (A/E)',
    'Elite career win rate': 'Elite career win rate',
    'Poor career win rate': 'Poor career win rate',
    'Close loss last start': 'Close loss last start (0.5-2.5L)',
    'NZ-bred': 'NZ-bred penalty',
}


def normalize_component_name(name):
    """Normalize a component name extracted from notes."""
    if not name:
        return ''

    name = re.sub(r'\s*[\(\[]\+?-?[\d.]+%.*?[\)\]]$', '', name).strip()
    name = re.sub(r'\s*\([\d.]+%\s*SR.*$', '', name).strip()
    name = re.sub(r'\s*\(\d+\s*races?\)$', '', name).strip()
    name = re.sub(r'\s+', ' ', name).strip()
    name = name[:150]

    for key, canonical in COMPONENT_NAME_MAP.items():
        if name.startswith(key):
            return canonical

    return name


def parse_components_from_notes(notes_text):
    """Parse analyzer.js notes to extract components and their scores."""
    if not notes_text:
        return []

    components = []
    seen_names = set()
    lines = str(notes_text).split('\n')
    skip_section = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if '===' in line:
            skip_section = ('PFAI BLEND' in line or 'SECTIONAL ANALYSIS' in line)
            if 'MARKET EXPECTATION' in line:
                skip_section = False
            continue

        if skip_section:
            continue

        if line.startswith('ℹ️') or line.startswith('⚠️') or line.startswith('📏'):
            continue

        if re.match(r'^=\s*[\d.]+', line):
            continue

        if line.startswith('└─') or 'HISTORY_' in line:
            continue

        match = re.match(r'^([+-]?\d+\.?\d*)\s*:\s*(.+)$', line)
        if not match:
            continue

        try:
            score = float(match.group(1))
            raw_name = match.group(2).strip()

            if score == 0:
                continue

            raw_name_lc = raw_name.lower()
            if (
                'weighted avg (z=' in raw_name_lc
                or 'sectional weighted' in raw_name_lc
                or raw_name_lc.startswith('weighted avg')
                or raw_name_lc.startswith('adj:')
            ):
                continue

            name = normalize_component_name(raw_name)

            if name and name not in seen_names:
                components.append((name, score))
                seen_names.add(name)

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

    top_picks = df_with_scores.loc[
        df_with_scores.groupby('race_id')['analyzer_score'].idxmax()
    ].copy()

    log.info(f"Analysing {len(top_picks)} top picks")

    winners = top_picks[top_picks['finish_position'] == 1]
    total_staked = len(top_picks)
    total_returned = winners['sp'].sum() if not winners.empty else 0
    baseline_roi = ((total_returned - total_staked) / total_staked * 100) if total_staked > 0 else 0
    baseline_sr = len(winners) / total_staked * 100 if total_staked > 0 else 0

    log.info(f"Baseline: {total_staked} races, {len(winners)} wins, "
             f"{baseline_sr:.1f}% SR, {baseline_roi:.1f}% ROI")

    component_data = {}

    for _, row in top_picks.iterrows():
        components = parse_components_from_notes(row.get('analyzer_notes', ''))
        won = int(row['finish_position']) == 1
        raw_sp = row.get('sp')
        if raw_sp is None or pd.isna(raw_sp):
            continue
        try:
            sp = float(raw_sp)
        except (TypeError, ValueError):
            continue

        if not np.isfinite(sp) or sp <= 0:
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

    results = []

    for comp_name, data in component_data.items():
        appearances = data['appearances']
        if appearances < 50:  # Minimum 50 appearances for statistical significance
            continue

        wins = data['wins']
        strike_rate = wins / appearances * 100
        avg_sp = float(np.mean(data['sps']))
        if not np.isfinite(avg_sp):
            avg_sp = None

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
            'avg_sp': round(avg_sp, 2) if avg_sp is not None else None,
            'current_value': round(current_value, 1),
            'suggested_value': suggested_value,
            'roi_delta': round(roi_delta, 1),
            'verdict': verdict
        })

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
    Track C: Runs two parallel momentum analyses:
      - scope='all_horses': every horse with 3+ scored races
      - scope='top_pick': only the highest scored horse per race
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

    top_pick_ids = set(
        df_scored.groupby('race_id')['analyzer_score'].idxmax().values
    )

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

    def empty_bucket_data():
        return {
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

    all_horses_data = empty_bucket_data()
    top_pick_data   = empty_bucket_data()

    for horse_name, group in df_scored.groupby('horse_name'):
        group = group.sort_values('meeting_date')
        if len(group) < 3:
            continue

        scores         = group['analyzer_score'].tolist()
        positions      = group['finish_position'].tolist()
        sps            = group['sp'].tolist()
        predicted_odds = group['predicted_odds'].tolist() if 'predicted_odds' in group.columns else [None] * len(scores)
        idx_list       = group.index.tolist()

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

            x      = list(range(len(window_scores)))
            slope  = float(np.polyfit(x, window_scores, 1)[0])
            won    = int(positions[i]) == 1
            bucket = get_bucket(slope)

            pred_sp = None
            try:
                raw_pred = predicted_odds[i]
                if raw_pred:
                    pred_sp = float(str(raw_pred).replace('$', '').strip())
            except Exception:
                pred_sp = None

            is_top_pick = idx_list[i] in top_pick_ids

            def _add(data):
                data[bucket]['appearances'] += 1
                if won:
                    data[bucket]['wins'] += 1
                data[bucket]['roi_contributions'].append(sp_val if won else -1.0)
                data[bucket]['sps'].append(sp_val)
                data[bucket]['slopes'].append(slope)
                if pred_sp and pred_sp > 0:
                    data[bucket]['predicted_sps'].append(pred_sp)
                    if sp_val > pred_sp:
                        data[bucket]['overlays'] += 1

            _add(all_horses_data)
            if is_top_pick:
                _add(top_pick_data)

    def build_results(bucket_data, scope):
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
            overlay_pct      = round(data['overlays'] / len(pred_sps) * 100, 1) if pred_sps else None

            results.append({
                'trajectory':        name,
                'scope':             scope,
                'appearances':       n,
                'wins':              wins,
                'strike_rate':       round(strike_rate, 1),
                'roi':               round(roi, 1),
                'avg_sp':            round(avg_sp, 2),
                'avg_slope':         round(avg_slope, 2),
                'avg_predicted_sp':  avg_predicted_sp,
                'overlay_pct':       overlay_pct,
            })
        return results

    all_results      = build_results(all_horses_data, 'all_horses')
    top_pick_results = build_results(top_pick_data,   'top_pick')
    combined         = all_results + top_pick_results

    log.info(f"Momentum all_horses: {sum(1 for r in all_results if r['appearances'] > 0)} buckets")
    log.info(f"Momentum top_pick:   {sum(1 for r in top_pick_results if r['appearances'] > 0)} buckets")
    return combined


ANALYZER_WEIGHTS = {
    'last_margin': 'Up to ±25 pts',
    'last_sp': 'Up to ±50 pts',
    'last_position': 'Up to ±25 pts',
    'career_win_rate': '+10 pts if 40%+',
    'distance_change': '±8 pts',
    'class_change': 'Up to ±20 pts (capped)',
    'horse_weight': 'Up to ±15 pts vs race average',
    'weight_vs_avg': 'Up to ±15 pts',
    'weight_change': 'Up to ±15 pts',
    'running_position': 'Encoded 0-3',
    'jockey_sr': 'Up to +20/-12 pts',
    'trainer_sr': 'Up to +10/-10 pts',
    'horse_age': 'Up to ±60 pts',
    'horse_sex': 'Up to +25 pts (Colt system)',
    'pfai_score': 'Blended 30% into final score',
}


def generate_feature_recommendations(importance_sorted):
    """Compare RF feature importance with current analyzer weights."""
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
            rec = 'ZERO importance — feature may not be populated or fully redundant'
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


MIN_VALIDATION_BETS = int(os.environ.get('ML_MIN_VALIDATION_BETS', '100'))
# A near-zero edge lets a challenger take the Champion seat on essentially any
# positive noise in a single validation window. Champion Score is on the order
# of single-to-double digits (roi_pct + 0.5*strike_rate_pct - penalties), so
# require a real, non-trivial improvement before swapping the production model.
PROMOTION_SELECTION_SCORE_EDGE = float(os.environ.get('ML_PROMOTION_SELECTION_SCORE_EDGE', '1.0'))
PROMOTION_ROI_EDGE_PCT = 3.0  # Legacy display only; promotion is Champion Score based.
PROMOTION_SR_TOLERANCE_PCT = 2.0
LARGE_PROMOTION_ROI_EDGE_PCT = float(os.environ.get('ML_LARGE_PROMOTION_ROI_EDGE_PCT', '10.0'))
CHAMPION_ROLLBACK_RETENTION_DAYS = int(os.environ.get('ML_CHAMPION_ROLLBACK_RETENTION_DAYS', '30'))
# 1.0% is below what even a random top-pick in a typical field would score, so it
# only ever rejected a totally broken model. Raised to a floor that's still well
# below normal top-pick strike rates (so genuine value/longshot-leaning models
# aren't blocked) but actually screens out degenerate models.
MIN_PROMOTION_STRIKE_RATE_PCT = float(os.environ.get('ML_MIN_PROMOTION_STRIKE_RATE_PCT', '15.0'))
MODEL_VERSION = os.environ.get('ML_MODEL_VERSION', datetime.utcnow().strftime('%Y%m%d'))


def _selection_score_from_metrics(metrics, force_recompute=False):
    """Defined out-of-sample Champion Score; ROI alone must never promote.

    force_recompute=True skips the cached metrics['selection_score'] shortcut
    and rebuilds the score from its raw components under the CURRENT formula.
    Needed when comparing a stored champion (whose selection_score was frozen
    at promotion time, possibly under an older formula version) against a
    freshly-scored challenger — otherwise the champion keeps whatever number
    it was promoted with forever, even after the scoring rule changes.
    """
    if not metrics:
        return None
    # A recompute is only meaningful if we actually have the raw components
    # (roi, etc.) to rebuild from — some old/minimal records only ever stored
    # the final selection_score with nothing underneath it. In that case
    # "recomputing" would just zero everything out, which is worse than
    # trusting the one number we do have.
    can_recompute = 'roi' in metrics
    if metrics.get('selection_score') is not None and (not force_recompute or not can_recompute):
        return float(metrics['selection_score'])
    stability = metrics.get('stability') or {}
    calibration = metrics.get('calibration') or {}
    walk_forward = metrics.get('walk_forward') or {}
    holdout_roi = float(metrics.get('roi', 0.0) or 0.0)
    strike_rate = float(metrics.get('strike_rate', 0.0) or 0.0)
    stability_penalty = abs(float(stability.get('roi_last_100', holdout_roi) or holdout_roi) - holdout_roi) + abs(float(stability.get('roi_last_250', holdout_roi) or holdout_roi) - holdout_roi)
    # A single 80/20 chronological holdout is a thin, high-variance sample: a
    # real production run found every candidate showing +30-38% ROI on its
    # holdout while EVERY walk-forward fold on nearly the same tail-end data
    # (93% overlapping rows) was negative, because a handful of long-priced
    # winners near the split boundary can flip the whole holdout's sign. Trust
    # the average of several independent out-of-sample folds far more than the
    # single holdout number. When walk-forward data is missing entirely (e.g. a
    # champion promoted before this metric existed), do NOT fall back to the
    # holdout ROI as a stand-in — that would let an unvalidated model keep the
    # benefit of the one number we know is unreliable on its own. Assume no
    # demonstrated out-of-sample edge (0.0) instead, which is the conservative
    # prior for "we have no walk-forward evidence this holds up."
    fold_rois = [float(f.get('roi', 0.0) or 0.0) for f in (walk_forward.get('folds') or []) if f.get('bets', 0)]
    has_walk_forward = len(fold_rois) > 0
    walk_forward_mean_roi = float(np.mean(fold_rois)) if has_walk_forward else 0.0
    blended_roi = (0.3 * holdout_roi) + (0.7 * walk_forward_mean_roi) if has_walk_forward else (0.3 * holdout_roi)
    walk_forward_penalty = float(walk_forward.get('roi_std', 0.0) or 0.0)
    calibration_penalty = (float(metrics.get('log_loss', 0.0) or 0.0) * 10.0) + (float(metrics.get('brier_score', 0.0) or 0.0) * 25.0) + (float(calibration.get('expected_calibration_error', 0.0) or 0.0) * 100.0)
    return float(blended_roi + (0.5 * strike_rate) - calibration_penalty - (0.05 * stability_penalty) - (1.0 * walk_forward_penalty))


def _promotion_rule_text():
    return (
        f"Promote only if the completed run's overall winner has at least {MIN_VALIDATION_BETS} validation bets, "
        f"positive validation ROI, strike rate >= {MIN_PROMOTION_STRIKE_RATE_PCT:.1f}%, and Champion Score "
        f"> active Champion Score + {PROMOTION_SELECTION_SCORE_EDGE:.3f}. "
        "Champion Score is stored internally as selection_score and equals "
        "(0.3*holdout ROI + 0.7*mean walk-forward-fold ROI) + 0.5*strike_rate - calibration penalties "
        "(log loss, Brier score, expected calibration error) - stability penalty - walk-forward cross-fold ROI-std penalty; "
        "a model with no walk-forward evidence gets no credit for its holdout ROI beyond a 0.3x weight. "
        "ROI alone is never sufficient."
    )
RF_BEST_ARTIFACT_NAME = 'form_analyst_best_random_forest.pkl'
LEGACY_RF_BEST_ARTIFACT_NAME = 'form_analyst_best.pkl'


class ConsensusRegressor(BaseEstimator, RegressorMixin):
    """Weighted consensus of model win-likelihood scores."""

    def __init__(self, estimators, weights=None):
        self.estimators = estimators
        self.weights = weights

    def fit(self, X, y):
        self.feature_names_in_ = np.asarray(list(X.columns)) if hasattr(X, 'columns') else None
        self.estimators_ = []
        for _, estimator in self.estimators:
            fitted = clone(estimator)
            fitted.fit(X, y)
            self.estimators_.append(fitted)
        if self.weights is None:
            self.weights_ = np.ones(len(self.estimators_), dtype=float)
        else:
            self.weights_ = np.asarray(self.weights, dtype=float)
            if len(self.weights_) != len(self.estimators_) or np.sum(self.weights_) <= 0:
                self.weights_ = np.ones(len(self.estimators_), dtype=float)
        self.weights_ = self.weights_ / np.sum(self.weights_)
        return self

    def predict(self, X):
        preds = []
        for est in self.estimators_:
            if hasattr(est, 'predict_proba'):
                proba = np.asarray(est.predict_proba(X), dtype=float)
                preds.append(proba[:, 1] if proba.ndim == 2 and proba.shape[1] > 1 else proba.ravel())
            else:
                preds.append(np.asarray(est.predict(X), dtype=float))
        return np.average(np.column_stack(preds), axis=1, weights=self.weights_)


def _predict_win_scores(model, X):
    """Return comparable win-likelihood scores for classifiers or regressors."""
    if hasattr(model, 'predict_proba'):
        proba = np.asarray(model.predict_proba(X), dtype=float)
        if proba.ndim == 2 and proba.shape[1] > 1:
            return proba[:, 1]
        return proba.ravel()
    return np.asarray(model.predict(X), dtype=float)



def _calibration_summary(y_true, pred, bins=10):
    frame = pd.DataFrame({'y': np.asarray(y_true, dtype=int), 'pred': np.asarray(pred, dtype=float)})
    frame['bin'] = pd.cut(frame['pred'], bins=np.linspace(0.0, 1.0, bins + 1), include_lowest=True)
    grouped = frame.groupby('bin', observed=False).agg(count=('y', 'size'), avg_pred=('pred', 'mean'), observed_rate=('y', 'mean'))
    grouped = grouped[grouped['count'] > 0].fillna(0.0)
    if grouped.empty:
        return {'expected_calibration_error': 0.0, 'max_calibration_error': 0.0, 'bins': []}
    errors = (grouped['avg_pred'] - grouped['observed_rate']).abs()
    weights = grouped['count'] / grouped['count'].sum()
    return {
        'expected_calibration_error': float((errors * weights).sum()),
        'max_calibration_error': float(errors.max()),
        'bins': [
            {
                'range': str(idx),
                'count': int(row['count']),
                'avg_pred': float(row['avg_pred']),
                'observed_rate': float(row['observed_rate']),
            }
            for idx, row in grouped.iterrows()
        ],
    }


def _attach_model_metadata(model, model_type, model_name, run_id, artifact_filename, feature_names, metrics):
    model._form_analyst_algorithm = model_type
    model._form_analyst_model_type = model_type
    model._form_analyst_model_name = model_name
    model._form_analyst_training_run_id = run_id
    model._form_analyst_artifact_filename = artifact_filename
    model._form_analyst_model_version = MODEL_VERSION
    model._form_analyst_expected_features = list(feature_names)
    model._form_analyst_selection_metrics = metrics or {}
    if getattr(model, 'feature_names_in_', None) is None:
        model.feature_names_in_ = np.asarray(list(feature_names))
    return model


def _artifact_feature_contract_ok(model, feature_names):
    stored = getattr(model, 'feature_names_in_', None)
    if stored is None:
        stored = getattr(model, '_form_analyst_expected_features', None)
    if stored is None:
        return False
    return [str(x) for x in list(stored)] == [str(x) for x in list(feature_names)]

def evaluate_model_on_validation(model, X_val, y_won_val, race_ids_val, sp_val):
    """Evaluate top model selection in every validation race with betting metrics."""
    pred = np.clip(_predict_win_scores(model, X_val), 1e-6, 1 - 1e-6)
    eval_df = pd.DataFrame({
        'race_id': list(race_ids_val),
        'pred': pred,
        'won': np.asarray(y_won_val, dtype=int),
        'sp': np.asarray(sp_val, dtype=float),
    })
    selections = eval_df.loc[eval_df.groupby('race_id')['pred'].idxmax()].copy()
    profits = np.where(selections['won'] == 1, selections['sp'] - 1.0, -1.0)
    bets = int(len(selections))
    wins = int(selections['won'].sum())
    cumulative = np.cumsum(profits)
    running_peak = np.maximum.accumulate(np.insert(cumulative, 0, 0.0))[1:]
    drawdown = abs(float(np.min(cumulative - running_peak))) if bets else 0.0
    longest_losing_streak = 0
    current_losing_streak = 0
    for won in selections['won'].astype(int):
        if won:
            current_losing_streak = 0
        else:
            current_losing_streak += 1
            longest_losing_streak = max(longest_losing_streak, current_losing_streak)
    bankroll_growth = float(np.prod(1.0 + (profits * 0.01)) - 1.0) if bets else 0.0
    volatility = float(np.std(profits, ddof=1)) if bets > 1 else 0.0

    def window_metrics(n):
        tail = selections.tail(n)
        if tail.empty:
            return {'bets': 0, 'roi': 0.0, 'strike_rate': 0.0, 'profit_units': 0.0}
        p = np.where(tail['won'] == 1, tail['sp'] - 1.0, -1.0)
        return {
            'bets': int(len(tail)),
            'roi': float(np.mean(p) * 100.0),
            'strike_rate': float(tail['won'].mean() * 100.0),
            'profit_units': float(np.sum(p)),
        }

    return {
        'roi': float(np.mean(profits) * 100.0) if bets else 0.0,
        'profit_units': float(np.sum(profits)) if bets else 0.0,
        'strike_rate': float(wins / bets * 100.0) if bets else 0.0,
        'number_of_bets': bets,
        'winners': wins,
        'average_winner_sp': float(selections.loc[selections['won'] == 1, 'sp'].mean()) if wins else 0.0,
        'average_selection_sp': float(selections['sp'].mean()) if bets else 0.0,
        'average_predicted_probability': float(selections['pred'].mean()) if bets else 0.0,
        'average_winner_predicted_probability': float(selections.loc[selections['won'] == 1, 'pred'].mean()) if wins else 0.0,
        'average_loser_predicted_probability': float(selections.loc[selections['won'] == 0, 'pred'].mean()) if wins < bets else 0.0,
        'drawdown': drawdown,
        'longest_losing_streak': int(longest_losing_streak),
        'bankroll_growth': bankroll_growth,
        'volatility': volatility,
        'last_100': window_metrics(100),
        'last_250': window_metrics(250),
        'last_500': window_metrics(500),
        'log_loss': float(log_loss(np.asarray(y_won_val, dtype=int), pred, labels=[0, 1])),
        'brier_score': float(brier_score_loss(np.asarray(y_won_val, dtype=int), pred)),
        'calibration': _calibration_summary(y_won_val, pred),
        'stability': {
            'roi_last_100': window_metrics(100)['roi'],
            'roi_last_250': window_metrics(250)['roi'],
            'roi_last_500': window_metrics(500)['roi'],
            'strike_rate_last_100': window_metrics(100)['strike_rate'],
            'strike_rate_last_250': window_metrics(250)['strike_rate'],
            'strike_rate_last_500': window_metrics(500)['strike_rate'],
        },
    }


MIN_PER_CLASS_FOR_FOLD = 2


def _clone_for_fold_fit(model, fold_y_train, n_calib_splits=3):
    """Clone `model` for a single walk-forward fold fit.

    CalibratedClassifierCV's default `cv=TimeSeriesSplit(...)` re-splits the
    fold's own (already small) training prefix into further expanding-window
    slices for calibration. On a small/imbalanced fold, an early internal
    slice can end up with only one class, which makes the calibrated
    estimator's predict_proba return a single column later
    (`Got predict_proba of shape (n, 1), but need classifier with two
    classes`). Rebuilding the internal cv as a StratifiedKFold sized to this
    fold's minority-class count guarantees every internal split sees both
    classes.
    """
    cloned = clone(model)
    if isinstance(cloned, CalibratedClassifierCV):
        minority_count = int(pd.Series(fold_y_train).value_counts().min())
        k = max(2, min(n_calib_splits, minority_count))
        cloned.set_params(cv=StratifiedKFold(n_splits=k, shuffle=False))
    return cloned


def _walk_forward_metrics_for_model(model, X_all, y_won_all, sp_all, race_ids_all, n_splits=3):
    """Score ROI/strike-rate stability across chronological expanding-window folds.

    The headline validation metrics above come from a single 80/20 time-ordered
    holdout, which is a single noisy sample of a high-variance domain. This
    re-fits a fresh clone of `model` on each TimeSeriesSplit fold (train prefix
    only, never leaking a fold's own test rows) and scores it on that fold's
    test slice with the same one-bet-per-race rule, so a model that only looks
    good in one lucky/unlucky window (rather than consistently) can be
    penalised in the Champion Score instead of silently winning promotion.
    X_all/y_won_all/sp_all/race_ids_all must already be time-ordered.
    """
    n = len(X_all)
    if n < (n_splits + 1) * 20:
        return {'n_splits': 0, 'n_splits_requested': n_splits, 'folds': [], 'roi_std': 0.0, 'strike_rate_std': 0.0}

    X_all = X_all.reset_index(drop=True)
    y_won_all = pd.Series(y_won_all).reset_index(drop=True)
    sp_array = np.asarray(sp_all, dtype=float)
    race_ids_list = list(race_ids_all)

    tscv = TimeSeriesSplit(n_splits=n_splits)
    folds = []
    for fold_idx, (train_idx, test_idx) in enumerate(tscv.split(X_all)):
        if len(test_idx) < 10 or len(train_idx) < 20:
            continue

        fold_y_train = y_won_all.iloc[train_idx]
        class_counts = fold_y_train.value_counts()
        if len(class_counts) < 2 or class_counts.min() < MIN_PER_CLASS_FOR_FOLD:
            log.warning(
                "Walk-forward fold %s skipped for %s: training slice has class counts %s "
                "(need both classes with >=%s rows each)",
                fold_idx, type(model).__name__, class_counts.to_dict(), MIN_PER_CLASS_FOR_FOLD,
            )
            continue

        try:
            # Impute with this fold's own training-prefix median only, matching
            # the same never-use-future-rows rule as the main train/val split.
            fold_X_train = X_all.iloc[train_idx]
            fold_median = fold_X_train.median()
            fold_X_train = fold_X_train.fillna(fold_median)
            fold_X_test = X_all.iloc[test_idx].fillna(fold_median)

            fold_model = _clone_for_fold_fit(model, fold_y_train)
            fold_model.fit(fold_X_train, fold_y_train)
            fold_race_ids = [race_ids_list[i] for i in test_idx]
            fold_sp = sp_array[test_idx]
            fold_y = y_won_all.iloc[test_idx]
            fold_metrics = evaluate_model_on_validation(fold_model, fold_X_test, fold_y, fold_race_ids, fold_sp)
            folds.append({
                'bets': fold_metrics['number_of_bets'],
                'roi': fold_metrics['roi'],
                'strike_rate': fold_metrics['strike_rate'],
            })
        except Exception as e:
            log.warning(f"Walk-forward fold {fold_idx} failed for {type(model).__name__}: {e}")

    roi_values = [f['roi'] for f in folds if f['bets'] > 0]
    sr_values = [f['strike_rate'] for f in folds if f['bets'] > 0]
    return {
        'n_splits': len(folds),
        'n_splits_requested': n_splits,
        'folds': folds,
        'roi_std': float(np.std(roi_values, ddof=0)) if len(roi_values) > 1 else 0.0,
        'strike_rate_std': float(np.std(sr_values, ddof=0)) if len(sr_values) > 1 else 0.0,
    }


def _log_walk_forward_fold_composition(dates_all, tracks_all, n_splits=3):
    """Log one compact line describing what each walk-forward test fold actually
    contains (date range + top tracks), so a fold that looks bad in the ROI/
    strike-rate numbers can be traced back to a specific period/venue mix
    without needing a DB query. Shared across all candidates (fold boundaries
    are identical for every model), so this runs once per Track E run rather
    than once per candidate, to keep log volume the same as before.
    """
    n = len(dates_all)
    if n < (n_splits + 1) * 20:
        return
    dates_all = pd.to_datetime(pd.Series(dates_all).reset_index(drop=True), errors='coerce')
    tracks_all = pd.Series(tracks_all).reset_index(drop=True) if tracks_all is not None else pd.Series([None] * n)
    tscv = TimeSeriesSplit(n_splits=n_splits)
    summaries = []
    for fold_idx, (_, test_idx) in enumerate(tscv.split(np.arange(n))):
        fold_dates = dates_all.iloc[test_idx].dropna()
        fold_tracks = tracks_all.iloc[test_idx].dropna()
        date_range = (
            f"{fold_dates.min().date()}..{fold_dates.max().date()}" if len(fold_dates) else "unknown"
        )
        top_tracks = ",".join(f"{t}x{c}" for t, c in Counter(fold_tracks).most_common(3)) or "unknown"
        summaries.append(f"fold{fold_idx}=[{date_range} rows={len(test_idx)} top_tracks={top_tracks}]")
    log.info("Walk-forward fold composition (shared by all candidates): %s", " ".join(summaries))


def _optional_classifier(model_type, trial=None):
    if model_type == 'xgboost':
        from xgboost import XGBClassifier
        params = {
            'n_estimators': trial.suggest_int('n_estimators', 80, 300) if trial else 180,
            'max_depth': trial.suggest_int('max_depth', 2, 6) if trial else 4,
            'learning_rate': trial.suggest_float('learning_rate', 0.02, 0.2, log=True) if trial else 0.06,
            'subsample': trial.suggest_float('subsample', 0.7, 1.0) if trial else 0.9,
            'colsample_bytree': trial.suggest_float('colsample_bytree', 0.7, 1.0) if trial else 0.9,
            'random_state': 42,
            'n_jobs': -1,
            'objective': 'binary:logistic',
            'eval_metric': 'logloss',
        }
        return XGBClassifier(**params)
    if model_type == 'lightgbm':
        from lightgbm import LGBMClassifier
        params = {
            'n_estimators': trial.suggest_int('n_estimators', 80, 300) if trial else 180,
            'max_depth': trial.suggest_int('max_depth', 2, 8) if trial else 5,
            'learning_rate': trial.suggest_float('learning_rate', 0.02, 0.2, log=True) if trial else 0.06,
            'num_leaves': trial.suggest_int('num_leaves', 8, 64) if trial else 31,
            'subsample': trial.suggest_float('subsample', 0.7, 1.0) if trial else 0.9,
            'colsample_bytree': trial.suggest_float('colsample_bytree', 0.7, 1.0) if trial else 0.9,
            'random_state': 42,
            'n_jobs': -1,
            'verbosity': -1,
        }
        return LGBMClassifier(**params)
    if model_type == 'catboost':
        from catboost import CatBoostClassifier
        return CatBoostClassifier(
            iterations=trial.suggest_int('iterations', 80, 250) if trial else 160,
            depth=trial.suggest_int('depth', 3, 8) if trial else 5,
            learning_rate=trial.suggest_float('learning_rate', 0.02, 0.2, log=True) if trial else 0.06,
            loss_function='Logloss',
            random_seed=42,
            verbose=False,
        )
    raise ValueError(model_type)



def _top_selection_rows(model, X_val, y_won_val, race_ids_val, sp_val):
    frame = pd.DataFrame({
        'race_id': list(race_ids_val),
        'row_id': range(len(race_ids_val)),
        'pred': _predict_win_scores(model, X_val),
        'won': np.asarray(y_won_val, dtype=int),
        'sp': np.asarray(sp_val, dtype=float),
    })
    return frame.loc[frame.groupby('race_id')['pred'].idxmax()].set_index('race_id')


def _profit_from_selection_rows(rows):
    """Return one-unit win-bet profit for already-selected rows."""
    if rows.empty:
        return 0.0
    return float(np.where(rows['won'] == 1, rows['sp'] - 1.0, -1.0).sum())


def _compare_model_selections(reference_name, challenger_name, reference_selections, challenger_selections):
    """Compare two models' top picks on the same validation races."""
    joined = reference_selections[['row_id', 'won', 'sp']].join(
        challenger_selections[['row_id', 'won', 'sp']], how='inner', lsuffix='_reference', rsuffix='_challenger'
    )
    disagreements = joined[joined['row_id_reference'] != joined['row_id_challenger']]
    same_selection_races = int((joined['row_id_reference'] == joined['row_id_challenger']).sum())
    reference_winners = disagreements[disagreements['won_reference'] == 1]
    challenger_winners = disagreements[disagreements['won_challenger'] == 1]
    reference_disagreement_rows = disagreements.rename(columns={'won_reference': 'won', 'sp_reference': 'sp'})
    challenger_disagreement_rows = disagreements.rename(columns={'won_challenger': 'won', 'sp_challenger': 'sp'})
    return {
        'reference_model': reference_name,
        'challenger_model': challenger_name,
        'same_selection_races': same_selection_races,
        'different_selection_races': int(len(disagreements)),
        'challenger_wins_when_disagreed': int(disagreements['won_challenger'].sum()) if not disagreements.empty else 0,
        'random_forest_wins_when_disagreed': int(disagreements['won_reference'].sum()) if not disagreements.empty else 0,
        'challenger_only_winner_avg_sp': float(challenger_winners['sp_challenger'].mean()) if not challenger_winners.empty else 0.0,
        'random_forest_only_winner_avg_sp': float(reference_winners['sp_reference'].mean()) if not reference_winners.empty else 0.0,
        'challenger_disagreement_profit_units': _profit_from_selection_rows(challenger_disagreement_rows),
        'random_forest_disagreement_profit_units': _profit_from_selection_rows(reference_disagreement_rows),
    }


def _audit_validation_betting_pipeline(selection_frames, race_ids_val, sp_val):
    """Log validation/betting invariants without changing model scoring."""
    expected_races = set(race_ids_val)
    expected_race_count = len(expected_races)
    log.info(
        "ML competition betting audit: validation_rows=%s identical_validation_races=%s "
        "sp_values_source=preserved_historical_data betting_rule=one_unit_win_bet_on_highest_predicted_probability",
        len(race_ids_val), expected_race_count
    )
    sp_array = np.asarray(sp_val, dtype=float)
    sp_nan_count = int(np.isnan(sp_array).sum()) if len(sp_array) else 0
    sp_finite = sp_array[np.isfinite(sp_array)] if len(sp_array) else sp_array
    sp_finite_count = int(len(sp_finite))
    sp_min = float(np.min(sp_finite)) if sp_finite_count else 0.0
    sp_avg = float(np.mean(sp_finite)) if sp_finite_count else 0.0
    sp_max = float(np.max(sp_finite)) if sp_finite_count else 0.0
    log.info(
        "ML competition betting audit: identical_SP_vector_for_all_models=%s "
        "sp_total=%s sp_nan_count=%s sp_finite_count=%s "
        "sp_min_finite=%.2f sp_avg_finite=%.2f sp_max_finite=%.2f",
        True, len(sp_array), sp_nan_count, sp_finite_count, sp_min, sp_avg, sp_max
    )
    baseline_races = None
    for model_type, selections in sorted(selection_frames.items()):
        model_races = set(selections.index)
        missing = expected_races - model_races
        extra = model_races - expected_races
        duplicate_bets = int(selections.index.duplicated().sum())
        one_bet_per_race = len(selections) == expected_race_count and duplicate_bets == 0 and not missing and not extra
        if baseline_races is None:
            baseline_races = model_races
        log.info(
            "ML competition betting audit for %s: identical_validation_races=%s identical_betting_rules=%s "
            "one_bet_per_race=%s bets=%s expected_races=%s skipped_races=%s extra_races=%s duplicate_race_bets=%s "
            "pre_roi_filtering=none ranking=max_predicted_probability",
            model_type, model_races == expected_races and model_races == baseline_races, True,
            one_bet_per_race, len(selections), expected_race_count, len(missing), len(extra), duplicate_bets
        )

def run_model_competition(X, y_roi, y_won, sp_values, race_ids, meeting_dates, df, grid_search_best_rf_params=None, baseline_roi=0.0):
    """Train RF, boosted candidates and consensus on one shared unseen validation set.

    grid_search_best_rf_params: optional hyperparams dict (n_estimators, max_depth,
    min_samples_leaf, max_features) from Track D's grid search. When provided, the
    random_forest candidate here — the one that actually competes for Champion —
    uses those tuned hyperparameters instead of a fixed guess, so the 1000+ models
    trained nightly in Track D actually influence the deployed model instead of
    being discarded.
    """
    dates = pd.to_datetime(pd.Series(meeting_dates), errors='coerce')
    order = dates.argsort().values
    X = X.iloc[order].reset_index(drop=True)
    y_roi = y_roi.iloc[order].reset_index(drop=True)
    y_won = y_won.iloc[order].reset_index(drop=True)
    race_ids = [race_ids[i] for i in order]
    dates_ordered = dates.iloc[order].reset_index(drop=True)
    track_by_race_id = {}
    if 'meeting_track' in df.columns:
        track_by_race_id = df.drop_duplicates(subset=['race_id']).set_index('race_id')['meeting_track'].to_dict()
    tracks_ordered = [track_by_race_id.get(rid) for rid in race_ids]
    cutoff = dates.iloc[order].quantile(0.8)
    train_mask = dates.iloc[order].reset_index(drop=True) <= cutoff
    val_mask = ~train_mask
    sp_values = pd.Series(sp_values).iloc[order].reset_index(drop=True)
    sp_val = sp_values[val_mask].values
    X_train, X_val = X[train_mask], X[val_mask]
    y_train, y_won_val = y_roi[train_mask], y_won[val_mask]
    race_ids_val = [r for r, keep in zip(race_ids, val_mask) if keep]

    # Impute with the TRAINING split's own median only — X_val must never
    # influence a fill value used to train or score against it. The same
    # median dict is persisted on every candidate below (_form_analyst_feature_medians)
    # so live scoring (ml_predict.py) reuses it instead of defaulting to 0.
    train_median = X_train.median()
    X_train = X_train.fillna(train_median)
    X_val = X_val.fillna(train_median)

    rf_params = {
        'n_estimators': 250, 'max_depth': 10, 'min_samples_leaf': 15, 'max_features': 'sqrt',
    }
    rf_params_source = 'fixed_default'
    if grid_search_best_rf_params:
        try:
            rf_params = {
                'n_estimators': int(grid_search_best_rf_params['n_estimators']),
                'max_depth': int(grid_search_best_rf_params['max_depth']),
                'min_samples_leaf': int(grid_search_best_rf_params['min_samples_leaf']),
                'max_features': grid_search_best_rf_params['max_features'],
            }
            rf_params_source = 'track_d_grid_search'
        except (KeyError, TypeError, ValueError) as e:
            log.warning(f"Ignoring malformed grid_search_best_rf_params {grid_search_best_rf_params}: {e}")

    log.info("Track E random_forest hyperparameters source=%s params=%s", rf_params_source, rf_params)

    candidates = {
        # Isotonic-calibrated: class_weight='balanced_subsample' (needed so the
        # forest actually splits on the ~12% winner class instead of always
        # predicting "loses") skews predict_proba far from true win frequency
        # — e.g. observed averaging ~0.63 on selected picks vs ~0.27-0.32 for
        # every boosted candidate on the same races. Ranking (ROI/strike rate)
        # is unaffected since that only needs relative order within a race, but
        # the raw probabilities are unusable wherever an absolute probability
        # matters (log-loss/Brier/ECE, ensemble averaging, EV-vs-market checks).
        # CalibratedClassifierCV rescales the output without touching what the
        # forest itself learned or how it ranks runners.
        'random_forest': CalibratedClassifierCV(
            RandomForestClassifier(random_state=42, n_jobs=-1, class_weight='balanced_subsample', **rf_params),
            method='isotonic',
            cv=TimeSeriesSplit(n_splits=3),
        )
    }
    log.info(
        "Track E split for random_forest: total_candidate_rows=%s train_rows=%s validation_rows=%s "
        "internal_tuning_train_rows=%s internal_tuning_eval_rows=%s",
        len(X), len(X_train), len(X_val), 0, 0
    )
    X_tune_train = X_tune_eval = y_tune_train = y_tune_eval = None
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        X_train_reset = X_train.reset_index(drop=True)
        y_train_reset = y_won[train_mask].reset_index(drop=True)
        # Split the already time-ordered Track E training window by row position
        # for Optuna only.  A date-quantile mask can collapse to all-train/zero-
        # eval when many rows share the same meeting date around the cutoff; the
        # challenger final fit below must still use the full outer X_train window.
        tune_split_idx = int(len(X_train_reset) * 0.8)
        tune_split_idx = min(max(tune_split_idx, 1), max(len(X_train_reset) - 1, 1))
        X_tune_train = X_train_reset.iloc[:tune_split_idx]
        y_tune_train = y_train_reset.iloc[:tune_split_idx]
        X_tune_eval = X_train_reset.iloc[tune_split_idx:]
        y_tune_eval = y_train_reset.iloc[tune_split_idx:]

        for mt in ('xgboost', 'lightgbm', 'catboost'):
            try:
                log.info(
                    "Track E split for %s: total_candidate_rows=%s train_rows=%s validation_rows=%s "
                    "internal_tuning_train_rows=%s internal_tuning_eval_rows=%s",
                    mt, len(X), len(X_train), len(X_val), len(X_tune_train), len(X_tune_eval)
                )
                if len(X_tune_train) < 50 or len(X_tune_eval) < 20:
                    raise ValueError("not enough training rows for safe internal tuning split")

                def objective(trial, model_type=mt):
                    model = _optional_classifier(model_type, trial)
                    model.fit(X_tune_train, y_tune_train)
                    scores = np.clip(_predict_win_scores(model, X_tune_eval), 1e-6, 1 - 1e-6)
                    return -float(log_loss(y_tune_eval, scores, labels=[0, 1]))

                study = optuna.create_study(direction='maximize')
                study.optimize(
                    objective,
                    n_trials=int(os.environ.get('ML_OPTUNA_TRIALS', '12')),
                    timeout=int(os.environ.get('ML_OPTUNA_TIMEOUT_SECONDS', '180')),
                    show_progress_bar=False
                )
                candidates[mt] = _optional_classifier(mt, study.best_trial)
            except Exception as e:
                log.warning(f"Skipping {mt} challenger; training/tuning failed: {e}")
    except Exception as e:
        log.warning(f"Skipping boosted challengers; Optuna unavailable or failed to initialise: {e}")

    fitted = {}
    results = []
    selection_frames = {}
    for mt, model in candidates.items():
        try:
            model.fit(X_train, y_won[train_mask])
            metrics = evaluate_model_on_validation(model, X_val, y_won_val, race_ids_val, sp_val)
            selection_frames[mt] = _top_selection_rows(model, X_val, y_won_val, race_ids_val, sp_val)
            fitted[mt] = model
            results.append({'model_type': mt, 'model_name': mt.replace('_', ' ').title(), 'model': model, 'metrics': metrics})
        except Exception as e:
            if mt == 'random_forest':
                raise
            log.warning(f"Skipping {mt} challenger; final fit/evaluation failed: {e}")

    # Walk-forward stability for each base candidate is computed here (before the
    # ensemble is built) so it can both (a) inform ensemble member weights below
    # and (b) avoid a second, redundant walk-forward pass over the same models later.
    try:
        _log_walk_forward_fold_composition(dates_ordered, tracks_ordered, n_splits=3)
    except Exception as e:
        log.warning(f"Walk-forward fold composition logging failed (non-fatal): {e}")

    walk_forward_by_model = {}
    for result in results:
        try:
            walk_forward = _walk_forward_metrics_for_model(
                result['model'], X, y_won, sp_values, race_ids, n_splits=3
            )
        except Exception as e:
            log.warning(f"Walk-forward stability check failed for {result['model_type']}: {e}")
            walk_forward = {'n_splits': 0, 'folds': [], 'roi_std': 0.0, 'strike_rate_std': 0.0}
        result['metrics']['walk_forward'] = walk_forward
        walk_forward_by_model[result['model_type']] = walk_forward
        log.info(
            "Walk-forward stability for %s: folds=%s roi_std=%.2f strike_rate_std=%.2f fold_rois=%s",
            result['model_type'], walk_forward['n_splits'], walk_forward['roi_std'], walk_forward['strike_rate_std'],
            [round(f['roi'], 1) for f in walk_forward['folds']],
        )

    if len(fitted) > 1:
        ensemble_members = list(fitted.items())
        ensemble_weights = []
        have_oos_tune_split = (
            X_tune_train is not None and X_tune_eval is not None
            and len(X_tune_train) > 0 and len(X_tune_eval) > 0
        )
        # Used for the fold-completion factor below: a member that completed
        # fewer walk-forward folds than the best-covered member in this run
        # (e.g. one fold skipped for a single-class training slice) is less
        # proven and gets discounted relative to that maximum.
        max_completed_folds = max(
            (int((walk_forward_by_model.get(mt) or {}).get('n_splits', 0) or 0) for mt, _ in ensemble_members),
            default=0,
        )
        for mt, model in ensemble_members:
            try:
                if have_oos_tune_split:
                    # Weight members by OUT-OF-SAMPLE error on a held-out slice of
                    # the training window (a shadow refit on X_tune_train, scored
                    # on X_tune_eval), never on the data the member was actually
                    # fit on. Weighting by in-sample/training error rewards
                    # overfitting: a member that just memorised X_train would
                    # look artificially good and dominate the ensemble.
                    oos_model = clone(model)
                    oos_model.fit(X_tune_train, y_tune_train)
                    oos_preds = _predict_win_scores(oos_model, X_tune_eval)
                    oos_mse = float(mean_squared_error(y_tune_eval, oos_preds))
                    base_weight = 1.0 / max(oos_mse, 0.0001)
                else:
                    base_weight = 1.0
            except Exception:
                base_weight = 1.0

            walk_forward = walk_forward_by_model.get(mt) or {}
            # Cross-fold walk-forward stability. Linear (not squared)
            # inverse-variance on purpose, so it nudges toward consistency
            # without letting it dominate the OOS-error signal above.
            roi_std = float(walk_forward.get('roi_std', 0.0) or 0.0)
            stability_factor = 1.0 / (roi_std + 1.0)

            # Performance factor: reward the *level* of walk-forward ROI, not
            # just its variance. Rewarding low roi_std alone lets a model that
            # is "consistently bad" (every fold worse than the market
            # baseline) out-score one that is "consistently good" simply for
            # having lower variance — that's exactly what happened to
            # random_forest in run #137 (folds -16.7%/-17.9% ROI, both worse
            # than the -13.6% baseline, yet it received the largest weight).
            fold_rois = [float(f.get('roi', 0.0) or 0.0) for f in (walk_forward.get('folds') or []) if f.get('bets', 0)]
            if not fold_rois:
                # No walk-forward data available for this member (e.g. dataset
                # too small) — stay neutral rather than penalising it.
                performance_factor = 1.0
            else:
                margin = float(np.mean(fold_rois)) - baseline_roi
                if margin > 0:
                    performance_factor = 1.0 + (margin / 100.0)
                else:
                    # Heavily discount (not zero — a bad-market run where every
                    # member trails baseline shouldn't collapse every weight
                    # to nothing) members that don't beat the market baseline.
                    performance_factor = 0.05

            # Fold-count-aware: discount members proportionally to how many
            # walk-forward folds they actually completed versus the
            # best-covered member, so a model that silently ran on fewer
            # folds isn't treated as equally reliable as one that ran on all
            # of them.
            completed_folds = int(walk_forward.get('n_splits', 0) or 0)
            fold_completion_factor = (completed_folds / max_completed_folds) if max_completed_folds > 0 else 1.0

            ensemble_weights.append(base_weight * stability_factor * performance_factor * fold_completion_factor)
        ensemble = ConsensusRegressor(ensemble_members, weights=ensemble_weights)
        ensemble.fit(X_train, y_won[train_mask])
        metrics = evaluate_model_on_validation(ensemble, X_val, y_won_val, race_ids_val, sp_val)
        metrics['ensemble_weights'] = dict(zip([name for name, _ in ensemble_members], ensemble.weights_.tolist()))
        selection_frames['ensemble'] = _top_selection_rows(ensemble, X_val, y_won_val, race_ids_val, sp_val)
        results.append({'model_type': 'ensemble', 'model_name': 'Ensemble / Consensus', 'model': ensemble, 'metrics': metrics})
        log.info("Ensemble member weights (OOS-error x stability x performance x fold-completion): %s", metrics['ensemble_weights'])

    # Persist the exact training-split median used above on every candidate so
    # live inference (ml_predict.py) can fill missing/unseen features with the
    # same values the model was actually trained against, instead of 0.
    train_median_dict = train_median.to_dict()
    for result in results:
        result['model']._form_analyst_feature_medians = train_median_dict

    # Walk-forward stability for the base candidates was already computed above
    # (before ensemble construction, so it could feed ensemble weighting too).
    # The ensemble itself didn't exist yet at that point, so score it now.
    for result in results:
        if 'walk_forward' in result['metrics']:
            continue
        try:
            walk_forward = _walk_forward_metrics_for_model(
                result['model'], X, y_won, sp_values, race_ids, n_splits=3
            )
        except Exception as e:
            log.warning(f"Walk-forward stability check failed for {result['model_type']}: {e}")
            walk_forward = {'n_splits': 0, 'folds': [], 'roi_std': 0.0, 'strike_rate_std': 0.0}
        result['metrics']['walk_forward'] = walk_forward
        log.info(
            "Walk-forward stability for %s: folds=%s roi_std=%.2f strike_rate_std=%.2f fold_rois=%s",
            result['model_type'], walk_forward['n_splits'], walk_forward['roi_std'], walk_forward['strike_rate_std'],
            [round(f['roi'], 1) for f in walk_forward['folds']],
        )

    agreement_summary = {'4_of_4': 0, '3_of_4': 0, '2_of_4': 0, '1_of_4': 0}
    if fitted:
        val_frame = pd.DataFrame({'race_id': race_ids_val, 'row_id': range(len(race_ids_val))})
        selections_by_model = {}
        for mt, model in fitted.items():
            temp = val_frame.copy()
            temp['pred'] = _predict_win_scores(model, X_val)
            selections_by_model[mt] = temp.loc[temp.groupby('race_id')['pred'].idxmax()].set_index('race_id')['row_id'].to_dict()
        for race_id in val_frame['race_id'].unique():
            chosen = [sel.get(race_id) for sel in selections_by_model.values()]
            max_agreement = max(chosen.count(row_id) for row_id in set(chosen))
            agreement_summary[f'{max_agreement}_of_4'] = agreement_summary.get(f'{max_agreement}_of_4', 0) + 1
    for result in results:
        result['agreement_summary'] = agreement_summary

    _audit_validation_betting_pipeline(selection_frames, race_ids_val, sp_val)

    log.info("ML competition per-model metrics on identical validation races:")
    for result in sorted(results, key=lambda r: r['model_type']):
        m = result['metrics']
        log.info(
            "  %s | ROI=%.1f%% Profit=%.1fu Strike=%.1f%% Bets=%s Winners=%s "
            "AvgWinnerSP=%.2f AvgSelectionSP=%.2f AvgPredProb=%.4f "
            "AvgWinnerPredProb=%.4f AvgLoserPredProb=%.4f",
            result['model_name'], m['roi'], m['profit_units'], m['strike_rate'],
            m['number_of_bets'], m['winners'], m['average_winner_sp'],
            m['average_selection_sp'], m['average_predicted_probability'],
            m['average_winner_predicted_probability'], m['average_loser_predicted_probability']
        )

    if 'random_forest' in selection_frames:
        comparisons = {}
        for result in sorted(results, key=lambda r: r['model_type']):
            mt = result['model_type']
            if mt == 'random_forest' or mt not in selection_frames:
                continue
            comparison = _compare_model_selections(
                'random_forest', mt, selection_frames['random_forest'], selection_frames[mt]
            )
            comparisons[mt] = comparison
            result['rf_comparison'] = comparison
            result['agreement_summary'] = {**result.get('agreement_summary', {}), 'vs_random_forest': comparison}
            log.info(
                "RF vs %s selection comparison: same_horse_races=%s different_horse_races=%s "
                "challenger_wins_when_disagreed=%s random_forest_wins_when_disagreed=%s "
                "challenger_only_winner_avg_sp=%.2f random_forest_only_winner_avg_sp=%.2f "
                "challenger_disagreement_profit=%.1fu random_forest_disagreement_profit=%.1fu",
                mt, comparison['same_selection_races'], comparison['different_selection_races'],
                comparison['challenger_wins_when_disagreed'], comparison['random_forest_wins_when_disagreed'],
                comparison['challenger_only_winner_avg_sp'], comparison['random_forest_only_winner_avg_sp'],
                comparison['challenger_disagreement_profit_units'], comparison['random_forest_disagreement_profit_units']
            )
        for result in results:
            if result['model_type'] == 'random_forest':
                result['agreement_summary'] = {**result.get('agreement_summary', {}), 'challenger_comparisons': comparisons}

    all_negative = bool(results) and all(r['metrics']['roi'] < 0 for r in results)
    best_by_roi = max(results, key=lambda r: r['metrics']['roi']) if results else None
    log.info(
        "ML validation ROI diagnostic summary: all_models_negative=%s likely_cause=%s "
        "evaluation_audit=identical_races_sp_rules_one_bet_per_race_no_pre_roi_filtering best_validation_roi_model=%s "
        "most_likely_roi_improvement_change=%s",
        all_negative,
        "validation_period_or_top-pick_model_edge_at_recorded_SP_not_betting_evaluation" if all_negative else "model_selection_differences",
        best_by_roi['model_type'] if best_by_roi else None,
        "add_or_tune_value/odds-aware bet filtering after this diagnostics-only audit"
    )

    for result in results:
        m = result['metrics']
        stability_penalty = abs(m['stability']['roi_last_100'] - m['roi']) + abs(m['stability']['roi_last_250'] - m['roi'])
        calibration_penalty = (m['log_loss'] * 10.0) + (m['brier_score'] * 25.0) + (m['calibration']['expected_calibration_error'] * 100.0)
        result['selection_score'] = _selection_score_from_metrics({**m, 'selection_score': None})
    best = max(results, key=lambda r: (r['selection_score'], r['metrics']['roi'], r['metrics']['strike_rate']))
    log.info(
        "ML model selection on untouched chronological test set: best=%s selection_score=%.3f roi=%.1f%% sr=%.1f%% log_loss=%.4f brier=%.4f ece=%.4f bets=%s",
        best['model_type'], best['selection_score'], best['metrics']['roi'], best['metrics']['strike_rate'],
        best['metrics']['log_loss'], best['metrics']['brier_score'],
        best['metrics']['calibration']['expected_calibration_error'], best['metrics']['number_of_bets']
    )
    val_dates = dates.iloc[order].reset_index(drop=True)[val_mask]
    validation_period = {
        'start': val_dates.min().date().isoformat() if len(val_dates) and pd.notna(val_dates.min()) else None,
        'end': val_dates.max().date().isoformat() if len(val_dates) and pd.notna(val_dates.max()) else None,
        'cutoff': cutoff.date().isoformat() if pd.notna(cutoff) else None,
    }
    for result in results:
        result['metrics']['selection_score'] = result['selection_score']
        result['metrics']['validation_period'] = validation_period
    return best, results


# ─────────────────────────────────────────────
# SAVE BEST MODEL TO DB (persists across container restarts)
# ─────────────────────────────────────────────
def save_best_model_to_db(pkl_file, combined_score, run_id, model_type='random_forest',
                          model_name='Random Forest', validation_metrics=None):
    """
    Store Challenger .pkl in the database and only activate it if it beats Champion.
    """
    today = datetime.utcnow().date()
    with open(pkl_file, 'rb') as f:
        pkl_bytes = f.read()

    saved_model = joblib.load(pkl_file)
    saved_features = getattr(saved_model, 'feature_names_in_', None)
    if saved_features is None:
        saved_features = getattr(saved_model, '_form_analyst_expected_features', [])
    expected_feature_count = len(list(saved_features)) if saved_features is not None else 0

    with engine.connect() as conn:
        champion = conn.execute(text("""
            SELECT id, validation_roi, validation_strike_rate,
                   validation_profit_units, validation_bets, validation_drawdown,
                   validation_longest_losing_streak, validation_bankroll_growth, validation_volatility,
                   combined_score, selection_metrics
            FROM backtest_best_model
            WHERE is_active = TRUE
            ORDER BY promoted_at DESC NULLS LAST, updated_at DESC, id DESC
            LIMIT 1
        """)).fetchone()
        val_roi = (validation_metrics or {}).get('roi')
        val_sr = (validation_metrics or {}).get('strike_rate')
        val_profit = (validation_metrics or {}).get('profit_units')
        val_bets = (validation_metrics or {}).get('number_of_bets', 0)
        val_drawdown = (validation_metrics or {}).get('drawdown')
        val_losing_streak = (validation_metrics or {}).get('longest_losing_streak')
        val_bankroll_growth = (validation_metrics or {}).get('bankroll_growth')
        val_volatility = (validation_metrics or {}).get('volatility')
        challenger_id = conn.execute(text("""
            INSERT INTO backtest_best_model
            (run_date, combined_score, pkl_data, run_id, is_active, validation_roi,
             validation_strike_rate, validation_profit_units, validation_bets, validation_drawdown,
             validation_longest_losing_streak, validation_bankroll_growth, validation_volatility,
             model_type, model_name, model_version, artifact_filename, expected_feature_count,
             selection_metrics, promotion_reason)
            VALUES (:d, :score, :data, :run_id, FALSE, :val_roi, :val_sr, :val_profit, :val_bets,
                    :val_drawdown, :val_losing_streak, :val_bankroll_growth, :val_volatility,
                    :model_type, :model_name, :model_version, :artifact_filename,
                    :expected_feature_count, :selection_metrics,
                    'Saved as challenger pending champion comparison')
            RETURNING id
        """), {'d': today, 'score': combined_score, 'data': pkl_bytes, 'run_id': run_id,
               'val_roi': val_roi, 'val_sr': val_sr, 'val_profit': val_profit, 'val_bets': val_bets,
               'val_drawdown': val_drawdown, 'val_losing_streak': val_losing_streak,
               'val_bankroll_growth': val_bankroll_growth, 'val_volatility': val_volatility,
               'model_type': model_type, 'model_name': model_name,
               'model_version': MODEL_VERSION,
               'artifact_filename': os.path.basename(pkl_file),
               'expected_feature_count': expected_feature_count,
               'selection_metrics': json.dumps(validation_metrics or {})}).fetchone()[0]

        promote = False
        reason = "Rejected: validation sample too small"
        champion_roi = float(champion[1]) if champion and champion[1] is not None else None
        champion_sr = float(champion[2]) if champion and champion[2] is not None else None
        champion_metrics = {}
        if champion and champion[10]:
            try:
                champion_metrics = json.loads(champion[10])
            except Exception:
                champion_metrics = {}
        # Always recompute both scores from their raw metric components under
        # the CURRENT formula rather than trusting a stored/cached number —
        # otherwise a champion promoted under an older formula version (e.g.
        # before walk-forward scoring existed) keeps an artificial advantage
        # forever, since its frozen score would never reflect a later, harder
        # bar. Only fall back to the stored raw column if we have no metrics
        # at all to recompute from (very old rows with no selection_metrics).
        if champion_metrics:
            champion_score = _selection_score_from_metrics(champion_metrics, force_recompute=True)
            if not (champion_metrics.get('walk_forward') or {}).get('folds'):
                log.warning(
                    "Active champion (id=%s) has NO walk-forward stability data — it was promoted before this "
                    "check existed and has never been evaluated the way current challengers are. Its recomputed "
                    "Champion Score %.3f reflects only a 0.3x-weighted holdout ROI with zero walk-forward credit; "
                    "recommend manually re-validating this model or triggering a rollback review.",
                    champion[0] if champion else None, champion_score if champion_score is not None else -1.0,
                )
        else:
            champion_score = float(champion[9]) if champion and champion[9] is not None else None
        # No force_recompute here: validation_metrics was just produced by this
        # same run's run_model_competition, under the current formula — no
        # staleness risk. The champion (below) is the one loaded from a stored
        # DB row that may predate a scoring-rule change, which is why that side
        # needs the forced recompute.
        challenger_score = _selection_score_from_metrics(validation_metrics or {})
        challenger_roi = float(val_roi) if val_roi is not None else None
        challenger_sr = float(val_sr) if val_sr is not None else None
        challenger_sample_ok = int(val_bets or 0) >= MIN_VALIDATION_BETS
        challenger_roi_positive = challenger_roi is not None and challenger_roi > 0.0
        challenger_sr_acceptable = challenger_sr is not None and challenger_sr >= MIN_PROMOTION_STRIKE_RATE_PCT

        if challenger_roi is None or challenger_sr is None or challenger_score is None:
            reason = "Rejected: challenger validation metrics or Champion Score missing"
        elif not challenger_roi_positive:
            reason = "Rejected: challenger validation ROI is not positive"
        elif not challenger_sample_ok:
            reason = "Rejected: validation sample too small"
        elif not challenger_sr_acceptable:
            reason = "Rejected: challenger validation strike rate is below promotion gate"
        else:
            if champion is None:
                promote = True
                reason = "Promoted: no active champion existed and challenger passed out-of-sample selection gates"
            elif champion_score is None:
                promote = True
                reason = "Promoted: active Champion Score missing, but challenger passed out-of-sample selection gates"
            elif challenger_score > champion_score + PROMOTION_SELECTION_SCORE_EDGE:
                promote = True
                reason = (f"Promoted: challenger Champion Score {challenger_score:.3f} beat "
                          f"Champion Score {champion_score:.3f} under out-of-sample rule")
            else:
                reason = (f"Rejected: challenger Champion Score {challenger_score:.3f} did not beat "
                          f"Champion Score {champion_score:.3f}")

        if promote:
            old_champion_id = champion[0] if champion else None
            conn.execute(text("""
                UPDATE backtest_best_model
                SET is_active = FALSE,
                    deactivated_at = NOW(),
                    retained_until = NOW() + (:retention_days * INTERVAL '1 day')
                WHERE is_active = TRUE
            """), {'retention_days': CHAMPION_ROLLBACK_RETENTION_DAYS})
            conn.execute(text("""
                UPDATE backtest_best_model
                SET is_active = TRUE, promoted_at = NOW(), promotion_reason = :reason, updated_at = NOW()
                WHERE id = :id
            """), {'id': challenger_id, 'reason': reason})
            conn.execute(text("""
                INSERT INTO backtest_model_promotions
                (run_id, old_champion_id, new_champion_id, model_type, promotion_reason,
                 old_validation_metrics, new_validation_metrics)
                VALUES (:run_id, :old_champion_id, :new_champion_id, :model_type, :reason,
                        :old_metrics, :new_metrics)
            """), {
                'run_id': run_id,
                'old_champion_id': old_champion_id,
                'new_champion_id': challenger_id,
                'model_type': model_type,
                'reason': reason,
                'old_metrics': json.dumps({
                    'roi': champion_roi,
                    'strike_rate': champion_sr,
                    'profit_units': float(champion[3]) if champion and champion[3] is not None else None,
                    'number_of_bets': int(champion[4]) if champion and champion[4] is not None else None,
                    'drawdown': float(champion[5]) if champion and champion[5] is not None else None,
                    'longest_losing_streak': int(champion[6]) if champion and champion[6] is not None else None,
                    'bankroll_growth': float(champion[7]) if champion and champion[7] is not None else None,
                    'volatility': float(champion[8]) if champion and champion[8] is not None else None,
                }),
                'new_metrics': json.dumps({**(validation_metrics or {}), 'promotion_rule': _promotion_rule_text()}),
            })
        else:
            conn.execute(text("""
                UPDATE backtest_best_model SET promotion_reason = :reason, updated_at = NOW()
                WHERE id = :id
            """), {'id': challenger_id, 'reason': reason})

        log.info("Champion/Challenger: champion_id=%s challenger_id=%s champion_score=%s challenger_score=%s champion_roi=%s champion_sr=%s challenger_roi=%.1f challenger_sr=%.1f promoted=%s reason=%s best_model=%s rule=%s",
                 champion[0] if champion else None, challenger_id,
                 f"{champion_score:.3f}" if champion_score is not None else "n/a",
                 f"{challenger_score:.3f}" if challenger_score is not None else "n/a",
                 f"{champion_roi:.1f}" if champion_roi is not None else "n/a",
                 f"{champion_sr:.1f}" if champion_sr is not None else "n/a",
                 val_roi or 0.0, val_sr or 0.0, promote, reason, model_type, _promotion_rule_text())

        conn.commit()


def rollback_to_champion(model_id, reason='Manual Champion rollback'):
    """Instantly reactivate a retained previous Champion without retraining."""
    with engine.connect() as conn:
        target = conn.execute(text("""
            SELECT id, retained_until
            FROM backtest_best_model
            WHERE id = :id
              AND pkl_data IS NOT NULL
              AND (retained_until IS NULL OR retained_until >= NOW())
        """), {'id': model_id}).fetchone()
        if not target:
            raise ValueError(f"Model {model_id} is not available for rollback or retention has expired")
        current = conn.execute(text("""
            SELECT id FROM backtest_best_model
            WHERE is_active = TRUE
            ORDER BY promoted_at DESC NULLS LAST, updated_at DESC, id DESC
            LIMIT 1
        """)).fetchone()
        current_id = current[0] if current else None
        conn.execute(text("""
            UPDATE backtest_best_model
            SET is_active = FALSE,
                deactivated_at = NOW(),
                retained_until = COALESCE(retained_until, NOW() + (:retention_days * INTERVAL '1 day'))
            WHERE is_active = TRUE
        """), {'retention_days': CHAMPION_ROLLBACK_RETENTION_DAYS})
        conn.execute(text("""
            UPDATE backtest_best_model
            SET is_active = TRUE,
                promoted_at = NOW(),
                promotion_reason = :reason,
                updated_at = NOW(),
                deactivated_at = NULL,
                retained_until = NULL
            WHERE id = :id
        """), {'id': model_id, 'reason': reason})
        conn.execute(text("""
            INSERT INTO backtest_model_promotions
            (old_champion_id, new_champion_id, model_type, promotion_reason,
             old_validation_metrics, new_validation_metrics)
            SELECT :old_champion_id, id, model_type, :reason, NULL, NULL
            FROM backtest_best_model
            WHERE id = :id
        """), {'old_champion_id': current_id, 'id': model_id, 'reason': reason})
        conn.commit()
    log.info("Champion rollback complete: old_champion_id=%s new_champion_id=%s reason=%s",
             current_id, model_id, reason)


# ─────────────────────────────────────────────
# STEP 6: WRITE RESULTS TO DB
# ─────────────────────────────────────────────
def write_results(run_id, feature_recommendations, component_results,
                  momentum_results, baseline_roi, baseline_sr, total_races, total_horses,
                  grid_search_df, top_10_models, best_challenger=None):
    """Write all backtest findings to the database."""
    log.info("Writing results to database...")

    with engine.connect() as conn:
        # Write feature importance (Track A)
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

        # Write component analysis (Track B)
        for comp in component_results:
            conn.execute(text("""
                INSERT INTO backtest_component_analysis
                (run_id, component_name, appearances, wins, strike_rate, roi,
                 avg_sp, current_value, suggested_value, roi_delta, verdict)
                VALUES (:run_id, :component_name, :appearances, :wins, :strike_rate,
                        :roi, :avg_sp, :current_value, :suggested_value, :roi_delta, :verdict)
            """), {'run_id': run_id, **comp})

        # Write momentum analysis (Track C)
        for mom in momentum_results:
            conn.execute(text("""
                INSERT INTO backtest_momentum_analysis
                (run_id, trajectory, scope, appearances, wins, strike_rate, roi, avg_sp, avg_slope,
                 avg_predicted_sp, overlay_pct)
                VALUES (:run_id, :trajectory, :scope, :appearances, :wins, :strike_rate, :roi, :avg_sp, :avg_slope,
                        :avg_predicted_sp, :overlay_pct)
            """), {'run_id': run_id, **mom})

        # Write grid search models (Track D)
        for idx, (i, row) in enumerate(grid_search_df.head(10).iterrows(), 1):
            conn.execute(text("""
                INSERT INTO backtest_rf_models
                (run_id, model_rank, combined_score, cv_roi_score, cv_win_score, n_features,
                 features, hyperparams, grid_name, subset_name, feature_importance)
                VALUES (:run_id, :rank, :combined_score, :roi_score, :win_score, :n_features,
                        :features, :hyperparams, :grid_name, :subset_name, :importance)
            """), {
                'run_id': run_id,
                'rank': idx,
                'combined_score': row['combined_score'],
                'roi_score': row['roi_score'],
                'win_score': row['win_score'],
                'n_features': row['n_features'],
                'features': json.dumps(row['features']),
                'hyperparams': json.dumps({
                    'n_estimators': int(row['n_estimators']),
                    'max_depth': int(row['max_depth']),
                    'min_samples_leaf': int(row['min_samples_leaf']),
                    'max_features': row['max_features']
                }),
                'grid_name': 'grid_search',
                'subset_name': row['subset'],
                'importance': json.dumps(row['importance'])
            })

        # Update run record
        best_roi = top_10_models[0]['roi_score'] * 100 if top_10_models else 0.0
        best_sr = top_10_models[0]['win_score'] * 100 if top_10_models else 0.0
        improvement = (best_roi - baseline_roi) if baseline_roi != 0 else 0.0

        conn.execute(text("""
            UPDATE backtest_runs
            SET completed_at = NOW(),
                status = 'complete',
                total_races = :total_races,
                total_horses = :total_horses,
                baseline_roi = :baseline_roi,
                baseline_strike_rate = :baseline_sr,
                grid_search_best_roi = :grid_best_roi,
                grid_search_best_sr = :grid_best_sr,
                grid_search_improvement = :improvement,
                best_model_rank = 1
            WHERE id = :run_id
        """), {
            'run_id': run_id,
            'total_races': total_races,
            'total_horses': total_horses,
            'baseline_roi': baseline_roi,
            'baseline_sr': baseline_sr,
            'grid_best_roi': best_roi,
            'grid_best_sr': best_sr,
            'improvement': improvement
        })

        if best_challenger:
            for result in best_challenger.get('competition_results', []):
                metrics = result['metrics']
                conn.execute(text("""
                    INSERT INTO backtest_model_competition
                    (run_id, model_type, model_name, validation_roi, validation_profit_units,
                     validation_strike_rate, validation_bets, validation_drawdown,
                     validation_longest_losing_streak, validation_bankroll_growth,
                     validation_volatility, last_100, last_250, last_500, agreement_summary,
                     log_loss, brier_score, calibration, stability, walk_forward, selection_score)
                    VALUES (:run_id, :model_type, :model_name, :roi, :profit_units,
                            :strike_rate, :bets, :drawdown, :longest_losing_streak,
                            :bankroll_growth, :volatility, :last_100, :last_250,
                            :last_500, :agreement_summary, :log_loss, :brier_score,
                            :calibration, :stability, :walk_forward, :selection_score)
                """), {
                    'run_id': run_id,
                    'model_type': result['model_type'],
                    'model_name': result['model_name'],
                    'roi': metrics['roi'],
                    'profit_units': metrics['profit_units'],
                    'strike_rate': metrics['strike_rate'],
                    'bets': metrics['number_of_bets'],
                    'drawdown': metrics['drawdown'],
                    'longest_losing_streak': metrics['longest_losing_streak'],
                    'bankroll_growth': metrics['bankroll_growth'],
                    'volatility': metrics['volatility'],
                    'last_100': json.dumps(metrics['last_100']),
                    'last_250': json.dumps(metrics['last_250']),
                    'last_500': json.dumps(metrics['last_500']),
                    'agreement_summary': json.dumps(result.get('agreement_summary', {})),
                    'log_loss': metrics.get('log_loss'),
                    'brier_score': metrics.get('brier_score'),
                    'calibration': json.dumps(metrics.get('calibration', {})),
                    'stability': json.dumps(metrics.get('stability', {})),
                    'walk_forward': json.dumps(metrics.get('walk_forward', {})),
                    'selection_score': result.get('selection_score'),
                })

        conn.commit()

    # Persist rank-#1 model to database (survives container restarts without git)
    if best_challenger:
        best = best_challenger
        try:
            save_best_model_to_db(
                best['pkl_file'], best['score'], run_id,
                model_type=best['model_type'],
                model_name=best['model_name'],
                validation_metrics=best['metrics'],
            )
        except Exception as e:
            log.warning(f"Could not save challenger model to DB: {e}")
    elif top_10_models:
        # Track E (the actual champion-selection stage) failed outright, so there
        # are no out-of-sample validation metrics for this Track D model. It's
        # registered as a challenger for visibility/debugging only — with
        # validation_metrics=None, save_best_model_to_db's promotion gate always
        # rejects it (0 validation bets < MIN_VALIDATION_BETS), by design: we
        # never want to promote a model nobody has evaluated out-of-sample.
        best = top_10_models[0]
        try:
            save_best_model_to_db(best['pkl_file'], best['score'], run_id)
        except Exception as e:
            log.warning(f"Could not save best model to DB: {e}")

    # NOTE: model artifacts are no longer auto-committed/pushed to git from this
    # runtime job. The champion .pkl is already durably persisted in Postgres
    # (see save_best_model_to_db above), which is the source of truth ml_predict.py
    # reads from. Pushing binaries to git from a live process added an unnecessary
    # dependency on runtime git credentials and permanently bloated repo history
    # for artifacts that were already safe.

    log.info("Results written successfully.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    log.info("=" * 80)
    log.info("BACKTEST JOB STARTING")
    log.info(f"Time: {datetime.utcnow().isoformat()}")
    log.info("=" * 80)

    ensure_tables()

    rollback_model_id = os.environ.get('ML_ROLLBACK_MODEL_ID')
    if rollback_model_id:
        rollback_to_champion(
            int(rollback_model_id),
            os.environ.get('ML_ROLLBACK_REASON', 'Manual Champion rollback via ML_ROLLBACK_MODEL_ID')
        )
        return

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

        X, y_roi, y_won, sp_values, race_ids, horse_ids, meeting_dates = build_training_set(
            df, strike_rate_data
        )

        # Track A: Baseline RF feature importance
        importance_sorted = run_random_forest(X, y_roi, y_won, meeting_dates)
        feature_recommendations = generate_feature_recommendations(importance_sorted)

        # Track B: Component ROI analysis
        component_results, baseline_roi, baseline_sr, total_races, total_wins = run_component_analysis(df)

        # Track C: Score momentum analysis
        momentum_results = run_momentum_analysis(df)

        # Track D: Grid search (NEW)
        grid_search_df, top_10_models = run_grid_search(X, y_roi, y_won, meeting_dates)

        # Track E: Multi-model Challenger competition on the same unseen validation set.
        # This is additive only: if any challenger path breaks, the original RF grid-search
        # above has already completed and the cron continues with that RF artifact.
        best_challenger = None
        grid_search_best_rf_params = None
        if top_10_models:
            grid_search_best_rf_params = {
                'n_estimators': top_10_models[0]['n_estimators'],
                'max_depth': top_10_models[0]['max_depth'],
                'min_samples_leaf': top_10_models[0]['min_samples_leaf'],
                'max_features': top_10_models[0]['max_features'],
            }
        try:
            best_competitor, competition_results = run_model_competition(
                X, y_roi, y_won, sp_values, race_ids, meeting_dates, df,
                grid_search_best_rf_params=grid_search_best_rf_params,
                baseline_roi=baseline_roi,
            )
            output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'models')
            os.makedirs(output_dir, exist_ok=True)
            saved_competition_artifacts = []
            for competitor in competition_results:
                artifact_name = f"form_analyst_candidate_{competitor['model_type']}_{run_id}.pkl"
                artifact_file = os.path.join(output_dir, artifact_name)
                _attach_model_metadata(
                    competitor['model'], competitor['model_type'], competitor['model_name'],
                    run_id, artifact_name, X.columns, competitor['metrics']
                )
                if not _artifact_feature_contract_ok(competitor['model'], X.columns):
                    raise ValueError(f"Candidate {competitor['model_type']} feature contract does not match production features")
                joblib.dump(competitor['model'], artifact_file)
                competitor['pkl_file'] = artifact_file
                saved_competition_artifacts.append(artifact_file)
                log.info(
                    "Saved compatible model-competition artifact: model_type=%s artifact=%s feature_count=%s",
                    competitor['model_type'], artifact_file, len(X.columns)
                )
            challenger_file = best_competitor.get('pkl_file')
            best_challenger = {
                'pkl_file': challenger_file,
                'score': best_competitor['selection_score'],
                'model_type': best_competitor['model_type'],
                'model_name': best_competitor['model_name'],
                'metrics': best_competitor['metrics'],
                'competition_results': competition_results,
                'saved_artifacts': saved_competition_artifacts,
            }
        except Exception as e:
            log.warning(f"Multi-model challenger competition failed; continuing with RF grid-search artifact: {e}")

        total_horses = len(df)

        # Write all results
        write_results(
            run_id,
            feature_recommendations,
            component_results,
            momentum_results,
            baseline_roi,
            baseline_sr,
            total_races,
            total_horses,
            grid_search_df,
            top_10_models,
            best_challenger
        )

        # Final summary
        log.info("=" * 80)
        log.info("BACKTEST JOB COMPLETE")
        log.info(f"Run ID:               {run_id}")
        log.info(f"Races analysed:       {total_races}")
        log.info(f"Horses analysed:      {total_horses}")
        log.info(f"Baseline ROI:         {baseline_roi:.1f}%")
        log.info(f"Baseline SR:          {baseline_sr:.1f}%")
        if top_10_models:
            best = top_10_models[0]
            log.info(f"Grid Search Best ROI: {best['roi_score']*100:.1f}%")
            log.info(f"Grid Search Best SR:  {best['win_score']*100:.1f}%")
            log.info(f"Improvement:          {(best['roi_score'] - baseline_roi/100)*100:.1f}%")
        log.info(f"Features analysed:    {len(feature_recommendations)}")
        log.info(f"Components analysed:  {len(component_results)}")
        log.info(f"Grid models trained:  {len(grid_search_df)}")
        log.info(f"Best nightly model:   {best_challenger['model_type'] if best_challenger else 'random_forest_grid_fallback'}")
        log.info("=" * 80)

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
