# Updated 2025-11-26 to fix deployment
import os
import json
import re
import subprocess
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, session
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash
from datetime import datetime
import requests
import tweepy
from anthropic import Anthropic
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import uuid

from models import db, User, Meeting, Race, Horse, Prediction, Result, ChatMessage
from puntingform_service import PuntingFormService
from ladbrokes import match_race_uuid, fetch_race_odds

import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ===== CSV HELPER FUNCTIONS FOR API SECTIONAL INJECTION =====
def parseCSV(csv_string):
    """Parse CSV string into list of dicts"""
    lines = csv_string.strip().split('\n')
    if not lines:
        return []
    
    headers = [h.strip() for h in lines[0].split(',')]
    rows = []
    
    for line in lines[1:]:
        values = [v.strip() for v in line.split(',')]
        if len(values) == len(headers):
            row = dict(zip(headers, values))
            rows.append(row)
    
    return rows

def rebuildCSV(rows):
    """Rebuild CSV string from list of dicts"""
    if not rows:
        return ""
    
    headers = list(rows[0].keys())
    csv_lines = [','.join(headers)]
    
    for row in rows:
        csv_lines.append(','.join(str(row.get(h, '')) for h in headers))
    
    return '\n'.join(csv_lines)
# ===== END CSV HELPERS =====

app = Flask(__name__)

# Initialize Claude API client
client = Anthropic(api_key=os.environ.get('ANTHROPIC_API_KEY'))

# Initialize rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

# Configuration
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///formanalyst.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Database connection pooling to reduce memory
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': int(os.environ.get('SQLALCHEMY_POOL_SIZE', 5)),
    'max_overflow': int(os.environ.get('SQLALCHEMY_MAX_OVERFLOW', 2)),
    'pool_recycle': 3600,
    'pool_pre_ping': True
}

# Fix for postgres:// vs postgresql:// (Railway uses postgres://)
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgres://'):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace('postgres://', 'postgresql://', 1)
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '8533463194:AAGHWamzq_Atz9cxjejKsm-hQlzwpYPSBh0')
TELEGRAM_CHANNEL = os.environ.get('TELEGRAM_CHANNEL', '-1003602052698')

# Twitter API Configuration
TWITTER_API_KEY = os.environ.get('TWITTER_API_KEY')
TWITTER_API_SECRET = os.environ.get('TWITTER_API_SECRET')
TWITTER_ACCESS_TOKEN = os.environ.get('TWITTER_ACCESS_TOKEN')
TWITTER_ACCESS_TOKEN_SECRET = os.environ.get('TWITTER_ACCESS_TOKEN_SECRET')

# Initialize Twitter client
twitter_client = None
if all([TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_TOKEN_SECRET]):
    try:
        twitter_client = tweepy.Client(
            consumer_key=TWITTER_API_KEY,
            consumer_secret=TWITTER_API_SECRET,
            access_token=TWITTER_ACCESS_TOKEN,
            access_token_secret=TWITTER_ACCESS_TOKEN_SECRET
        )
        print("✓ Twitter client initialized successfully")
    except Exception as e:
        print(f"✗ Failed to initialize Twitter client: {e}")
        twitter_client = None

# Initialize extensions
db.init_app(app)

# Initialize PuntingForm API service
pf_service = PuntingFormService()

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
    
@app.teardown_appcontext
def shutdown_session(exception=None):
    db.session.remove()
    import gc
    gc.collect()

# Create tables and default admin user
with app.app_context():
    db.create_all()
    
    # Migration: Add PuntingForm integration columns
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        meetings_columns = [col['name'] for col in inspector.get_columns('meetings')]
        
        if 'puntingform_id' not in meetings_columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE meetings ADD COLUMN puntingform_id VARCHAR(255)'))
                conn.commit()
            print("✓ Added puntingform_id column to meetings table")
            
        if 'auto_imported' not in meetings_columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE meetings ADD COLUMN auto_imported BOOLEAN DEFAULT FALSE'))
                conn.commit()
            print("✓ Added auto_imported column to meetings table")
            
    except Exception as e:
        print(f"PuntingForm migration check: {e}")
    
   # Migration: Add V2 API JSON columns to races table
    print("DEBUG: Starting V2 API migration check...")
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        races_columns = [col['name'] for col in inspector.get_columns('races')]
        print(f"DEBUG: Found races columns: {races_columns}")
        
        if 'speed_maps_json' not in races_columns:
            print("DEBUG: speed_maps_json not found, adding columns...")
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE races ADD COLUMN speed_maps_json JSON'))
                conn.execute(text('ALTER TABLE races ADD COLUMN ratings_json JSON'))
                conn.execute(text('ALTER TABLE races ADD COLUMN sectionals_json JSON'))
                conn.commit()
            print("✓ Added V2 API JSON columns to races table")
        else:
            print("DEBUG: V2 columns already exist, skipping")
            
    except Exception as e:
        print(f"V2 API migration ERROR: {e}")
        import traceback
        traceback.print_exc()
    
    # Migration: Add best_bet_flagged_at column if it doesn't exist
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        predictions_columns = [col['name'] for col in inspector.get_columns('predictions')]
        
        if 'best_bet_flagged_at' not in predictions_columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE predictions ADD COLUMN best_bet_flagged_at TIMESTAMP'))
                conn.commit()
            print("Added best_bet_flagged_at column to predictions table")
    except Exception as e:
        print(f"Best Bet migration check: {e}")

    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        horses_columns = [col['name'] for col in inspector.get_columns('horses')]
        
        if 'is_scratched' not in horses_columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE horses ADD COLUMN is_scratched BOOLEAN DEFAULT FALSE'))
                conn.commit()
            print("Added is_scratched column to horses table")
    except Exception as e:
        print(f"Scratched migration check: {e}")

    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        meetings_columns = [col['name'] for col in inspector.get_columns('meetings')]

        if 'rail_position' not in meetings_columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE meetings ADD COLUMN rail_position INTEGER DEFAULT 0'))
                conn.commit()
            print("Added rail_position column to meetings table")

        if 'pace_bias' not in meetings_columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE meetings ADD COLUMN pace_bias INTEGER DEFAULT 0'))
                conn.commit()
            print("Added pace_bias column to meetings table")
    except Exception as e:
        print(f"Track bias migration check: {e}")

    # Migration: Create components table if it doesn't exist
    try:
        from models import Component
        db.create_all()
        
        component_count = Component.query.count()
        if component_count == 0:
            print("Seeding initial components...")
            
            starter_components = [
                {'component_name': 'Age/Sex - 5yo Horse (Entire)',                 'appearances': 21,   'wins': 6,   'strike_rate': 28.6, 'roi_percentage': 224.8, 'is_active': True},
                {'component_name': 'Days Since Run - Fresh Return (150-199 days)', 'appearances': 16,   'wins': 3,   'strike_rate': 18.8, 'roi_percentage': 193.1, 'is_active': True},
                {'component_name': 'Colt - Base Bonus',                            'appearances': 841,  'wins': 120, 'strike_rate': 14.3, 'roi_percentage': 66.1,  'is_active': True},
                {'component_name': 'Country: USA-bred',                            'appearances': 11,   'wins': 1,   'strike_rate': 9.1,  'roi_percentage': 63.6,  'is_active': True},
                {'component_name': 'Market Expectation - Worst in Field',          'appearances': 8,    'wins': 2,   'strike_rate': 25.0, 'roi_percentage': 62.5,  'is_active': True},
                {'component_name': 'Colt - 3yo Colt',                             'appearances': 2043, 'wins': 370, 'strike_rate': 18.1, 'roi_percentage': 49.2,  'is_active': True},
                {'component_name': 'Running Position - Leader Staying',            'appearances': 14,   'wins': 2,   'strike_rate': 14.3, 'roi_percentage': 42.9,  'is_active': True},
                {'component_name': 'Days Since Run - Too Fresh (250+ days)',       'appearances': 198,  'wins': 28,  'strike_rate': 14.1, 'roi_percentage': 31.8,  'is_active': True},
                {'component_name': 'Age/Sex - 3yo',                               'appearances': 286,  'wins': 55,  'strike_rate': 19.2, 'roi_percentage': 27.7,  'is_active': True},
            ]
            
            for comp_data in starter_components:
                component = Component(**comp_data)
                db.session.add(component)
            
            db.session.commit()
            print(f"Added {len(starter_components)} starter components")
        else:
            print(f"Components table already has {component_count} entries")
            
    except Exception as e:
        print(f"Component migration check: {e}")
    # Migration: Upsert profitable components into live DB
    try:
        from models import Component

        profitable_components = [
    # ── Original list ────────────────────────────────────────────────────────
    {'component_name': 'Age/Sex - 5yo Horse (Entire)',                          'appearances': 21,   'wins': 6,   'strike_rate': 28.6, 'roi_percentage': 224.8, 'is_active': True},
    {'component_name': 'Colt - Base Bonus',                                     'appearances': 841,  'wins': 120, 'strike_rate': 14.3, 'roi_percentage': 66.1,  'is_active': True},
    {'component_name': 'Colt - 3yo Colt',                                       'appearances': 2043, 'wins': 370, 'strike_rate': 18.1, 'roi_percentage': 49.2,  'is_active': True},
    {'component_name': 'Running Position - Leader Staying',                     'appearances': 14,   'wins': 2,   'strike_rate': 14.3, 'roi_percentage': 42.9,  'is_active': True},
    {'component_name': 'Days Since Run - Too Fresh (250+ days)',                'appearances': 198,  'wins': 28,  'strike_rate': 14.1, 'roi_percentage': 31.8,  'is_active': True},
    {'component_name': 'Age/Sex - 3yo',                                         'appearances': 286,  'wins': 55,  'strike_rate': 19.2, 'roi_percentage': 27.7,  'is_active': True},
    {'component_name': 'Pace Angle - Sprint Leader Run Down',                   'appearances': 123,  'wins': 32,  'strike_rate': 26.0, 'roi_percentage': 32.1,  'is_active': True},
    {'component_name': 'Undefeated on Condition',                               'appearances': 31,   'wins': 7,   'strike_rate': 22.6, 'roi_percentage': 34.0,  'is_active': True},
    # ── New — Top Pick Notes ROI ─────────────────────────────────────────────
    {'component_name': 'Running Position - Backmarker Staying',                 'appearances': 6,    'wins': 1,   'strike_rate': 16.7, 'roi_percentage': 333.3, 'is_active': True},
    {'component_name': 'Last Start - Photo Win (<0.5L)',                        'appearances': 212,  'wins': 48,  'strike_rate': 22.6, 'roi_percentage': 48.0,  'is_active': True},
    {'component_name': 'Specialist - Undefeated Distance',                      'appearances': 220,  'wins': 54,  'strike_rate': 24.5, 'roi_percentage': 43.3,  'is_active': True},
    {'component_name': 'Distance Change - Drop Back Moderate (200-400m)',       'appearances': 45,   'wins': 11,  'strike_rate': 24.4, 'roi_percentage': 37.9,  'is_active': True},
    {'component_name': 'Running Position - Backmarker Middle',                  'appearances': 37,   'wins': 6,   'strike_rate': 16.2, 'roi_percentage': 31.6,  'is_active': True},
    {'component_name': 'Specialist - Undefeated Track+Distance',                'appearances': 146,  'wins': 43,  'strike_rate': 29.5, 'roi_percentage': 31.4,  'is_active': True},
    {'component_name': 'First Up - Specialist Undefeated',                      'appearances': 62,   'wins': 21,  'strike_rate': 33.9, 'roi_percentage': 27.7,  'is_active': True},
]

        added = 0
        for comp_data in profitable_components:
            existing = Component.query.filter_by(component_name=comp_data['component_name']).first()
            if not existing:
                component = Component(**comp_data)
                db.session.add(component)
                added += 1
            else:
                existing.appearances    = comp_data['appearances']
                existing.wins           = comp_data['wins']
                existing.strike_rate    = comp_data['strike_rate']
                existing.roi_percentage = comp_data['roi_percentage']

        db.session.commit()
        if added > 0:
            print(f"✓ Seeded {added} new profitable components")
        else:
            print("✓ Profitable components already present — stats updated")

    except Exception as e:
        db.session.rollback()
        print(f"Profitable component migration error: {e}")
    # Create default admin if doesn't exist
    admin = User.query.filter_by(username='admin').first()
    if not admin:
        admin = User(
            username='admin',
            email='admin@theformanalyst.com',
            is_admin=True
        )
        admin.set_password(os.environ.get('ADMIN_PASSWORD', 'changeme123'))
        db.session.add(admin)
        db.session.commit()

# ----- Analyzer Integration -----
def run_analyzer(csv_data, track_condition, is_advanced=False, strike_rate_data=None):
    input_data = {
        'csv_data': csv_data,
        'track_condition': track_condition,
        'is_advanced': is_advanced,
        'strike_rate_data': strike_rate_data or {'jockeys': {}, 'trainers': {}}
    }

    analyzer_path = os.path.join(os.path.dirname(__file__), 'analyzer.js')

    try:
        result = subprocess.run(
            ['node', analyzer_path],
            input=json.dumps(input_data),
            capture_output=True,
            text=True,
            timeout=60  # Increased timeout for large files
        )

        if result.returncode != 0:
            raise Exception(f"Analyzer error: {result.stderr}")

        parsed_results = json.loads(result.stdout)
        if parsed_results:
            print("=== FIRST RESULT FROM ANALYZER ===")
            print(json.dumps(parsed_results[0], indent=2))
            print("===================================")

        return parsed_results

    except subprocess.TimeoutExpired:
        raise Exception("Analysis timed out (>60 seconds)")
    except json.JSONDecodeError as e:
        raise Exception(f"Invalid analyzer output: {e}")
    except FileNotFoundError:
        raise Exception("Node.js not found.  Please ensure Node.js is installed.")
    except Exception as e:
        raise Exception(f"Analysis failed: {str(e)}")
        
def post_best_bets_to_telegram(best_bets, meeting_name):
    """
    Post best bets to Telegram channel
    """
    if not best_bets:
        logger.warning("No bets to post to Telegram")
        return False
    
    try:
        logger.info(f"Attempting to post {len(best_bets)} bets for {meeting_name} to Telegram")
        
        # Don't escape underscores - let them display naturally
        safe_meeting_name = meeting_name.replace('_', ' ')  # Convert to space instead
        
        # Build message with bold formatting
        message = "*BET ALERT:*\n\n"
        message += f"🏇 *{safe_meeting_name.upper()}*\n\n"
        
        for bet in best_bets:
            message += f"*R{bet['race_number']}: {bet['horse_name']}*\n\n"
            message += f"💰 *Predicted Price: {bet['predicted_odds']}*\n\n"
        
        message += "⚠️ Think. Is this a bet you really want to place? Gamble Responsibly | 1800 858 858"
        
        # ... rest of your function code to actually send it
        
        # Send via Telegram Bot API directly
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHANNEL,
            'text': message,
            'parse_mode': 'Markdown'
        }
        
        logger.info(f"Sending POST request to Telegram API")
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            logger.info(f"✓ Successfully posted {len(best_bets)} tips for {meeting_name} to Telegram")
            
            # ALSO POST TO TWITTER
            post_best_bets_to_twitter(best_bets, meeting_name)
            
            return True
        else:
            logger.error(f"✗ Telegram API error: {response.status_code} - {response.text}")
            return False
        
    except Exception as e:
        logger.error(f"✗ Telegram posting exception: {str(e)}", exc_info=True)
        return False

def post_best_bets_to_twitter(best_bets, meeting_name):
    """
    Post best bets to Twitter/X
    """
    logger.info("=" * 50)
    logger.info("TWITTER POSTING ATTEMPT STARTING")
    logger.info(f"Number of bets: {len(best_bets) if best_bets else 0}")
    logger.info(f"Meeting: {meeting_name}")
    logger.info(f"Twitter client exists: {twitter_client is not None}")
    
    if not best_bets:
        logger.warning("✗ No bets to post to Twitter")
        return False
        
    if not twitter_client:
        logger.error("✗ Twitter client not initialized - skipping Twitter post")
        logger.error(f"Twitter API Key set: {bool(TWITTER_API_KEY)}")
        logger.error(f"Twitter API Secret set: {bool(TWITTER_API_SECRET)}")
        logger.error(f"Twitter Access Token set: {bool(TWITTER_ACCESS_TOKEN)}")
        logger.error(f"Twitter Access Token Secret set: {bool(TWITTER_ACCESS_TOKEN_SECRET)}")
        return False
    
    try:
        logger.info(f"Building Twitter message for {len(best_bets)} bets...")
        
        # Build message - clean and concise format
        message = "BET ALERT:\n\n"
        message += f"🏇 {meeting_name.upper()}\n\n"
        
        for bet in best_bets:
            message += f"R{bet['race_number']}: {bet['horse_name']}\n"
            message += f"💰 Predicted Price: {bet['predicted_odds']}\n\n"
        
        message += "⚠️ Think. Is this a bet you really want to place? Gamble Responsibly | 1800 858 858"
        
        logger.info(f"Message built. Length: {len(message)} chars")
        logger.info(f"Message preview: {message[:100]}...")
        
        # Check length (Twitter limit is 280 chars)
        if len(message) > 280:
            logger.warning(f"Message too long ({len(message)} chars), truncating...")
            message = message[:277] + "..."
        
        # Post to Twitter
        logger.info("Calling twitter_client.create_tweet()...")
        response = twitter_client.create_tweet(text=message)
        logger.info(f"Twitter API response received: {response}")
        
        if response.data:
            tweet_id = response.data.get('id', 'unknown')
            logger.info(f"✓ Successfully posted to Twitter! Tweet ID: {tweet_id}")
            logger.info(f"✓ Posted {len(best_bets)} tips for {meeting_name} to Twitter")
            logger.info("=" * 50)
            return True
        else:
            logger.error(f"✗ Twitter posting failed - no response data")
            logger.error(f"Full response: {response}")
            logger.info("=" * 50)
            return False
        
    except Exception as e:
        logger.error(f"✗ Twitter posting EXCEPTION occurred!")
        logger.error(f"Exception type: {type(e).__name__}")
        logger.error(f"Exception message: {str(e)}", exc_info=True)
        logger.info("=" * 50)
        return False
    # ----- PuntingForm V2 Data Extraction Helpers -----
def extract_race_speed_maps(speed_maps_data, race_number):
    """Extract speed map data for a specific race"""
    if not speed_maps_data:
        return None
    
    payload = speed_maps_data.get('payLoad', [])
    for race in payload:
        if race.get('raceNo') == int(race_number):
            return race  # Return the whole race speed map
    return None

def extract_race_ratings(ratings_data, race_number):
    """Extract ratings for a specific race"""
    if not ratings_data:
        return None
    
    payload = ratings_data.get('payLoad', [])
    race_ratings = []
    for runner in payload:
        if runner.get('raceNo') == int(race_number):
            race_ratings.append(runner)
    
    return race_ratings if race_ratings else None

def extract_race_sectionals(sectionals_data, race_number):
    """Extract sectionals for a specific race"""
    if not sectionals_data:
        return None
    
    payload = sectionals_data.get('payLoad', [])
    race_sectionals = []
    for runner in payload:
        if runner.get('raceNo') == int(race_number):
            race_sectionals.append(runner)
    
    return race_sectionals if race_sectionals else None

def normalize_runner_name(name: str) -> str:
    """
    Normalize runner names from different sources so lookups match.
    """
    if not name:
        return ''
    s = str(name).lower().strip()
    s = re.sub(r"[^a-z0-9\s]", " ", s)   # remove punctuation
    s = re.sub(r"\s+", " ", s).strip()  # collapse multiple spaces
    return s

def apply_track_bias(speed_map_score, running_position, rail_position, pace_bias):
    """
    Adds rail and pace bias ON TOP of the existing speed map score
    already calculated by the analyzer (e.g. +6.0 LEADER in Mile).
    Rail applied once at import. Pace bias applied/updated live.
    """
    if not running_position:
        return round(speed_map_score, 1)

    # ── Rail modifier ──
    # Wider rail = narrower track = leaders harder to run down
    if rail_position >= 13:
        rail_mod = 3.0
    elif rail_position >= 10:
        rail_mod = 2.0
    elif rail_position >= 7:
        rail_mod = 1.2
    elif rail_position >= 4:
        rail_mod = 0.6
    elif rail_position >= 1:
        rail_mod = 0.3
    else:
        rail_mod = 0.0  # True rail — no adjustment

    # ── Pace bias modifier ──
    # pace_bias is -2..+2. Each step = 1.5 points base adjustment
    pace_mod = float(pace_bias) * 1.5

    # ── Apply by running position ──
    pos = running_position.strip().upper()

    if pos == 'LEADER':
        speed_map_score += rail_mod + pace_mod
    elif pos == 'ONPACE':
        speed_map_score += (rail_mod * 0.6) + (pace_mod * 0.6)
    elif pos == 'MIDFIELD':
        speed_map_score -= (rail_mod * 0.3) + (pace_mod * 0.3)
    elif pos == 'BACKMARKER':
        speed_map_score -= (rail_mod * 0.8) + (pace_mod * 0.8)

    return round(speed_map_score, 1)
    # ── Rail modifier ──
    # Wider rail = narrower track = leaders harder to run down
    if rail_position >= 13:
        rail_mod = 3.0
    elif rail_position >= 10:
        rail_mod = 2.0
    elif rail_position >= 7:
        rail_mod = 1.2
    elif rail_position >= 4:
        rail_mod = 0.6
    elif rail_position >= 1:
        rail_mod = 0.3
    else:
        rail_mod = 0.0   # True rail — no adjustment

    # ── Pace bias modifier ──
    # Each step = 1.5 points of base adjustment
    pace_mod = pace_bias * 1.5

    # ── Apply by running position ──
    pos = running_position.upper()

    if pos == 'LEADER':
        speed_map_score += rail_mod + pace_mod
    elif pos == 'ONPACE':
        speed_map_score += (rail_mod * 0.6) + (pace_mod * 0.6)
    elif pos == 'MIDFIELD':
        speed_map_score -= (rail_mod * 0.3) + (pace_mod * 0.3)
    elif pos == 'BACKMARKER':
        speed_map_score -= (rail_mod * 0.8) + (pace_mod * 0.8)

    return round(speed_map_score, 1)

def process_and_store_results(csv_data, filename, track_condition, user_id, 
                              is_advanced=False, puntingform_id=None,
                              speed_maps_data=None, ratings_data=None, 
                              sectionals_data=None, rail_position=0,
                              scratched_set=None, strike_rate_data=None, **kwargs):
    kwargs['rail_position'] = rail_position

    # ===== INJECT API SECTIONAL DATA INTO CSV =====
    parsed_csv = parseCSV(csv_data)

    if sectionals_data:
        sectionals_payload = sectionals_data.get('payLoad', [])
        logger.info(f"Injecting API data for {len(sectionals_payload)} runners")
        
        for row in parsed_csv:
            horse_name = row.get('horse name', '').strip()
            race_num = row.get('race number', '').strip()
            
            for runner in sectionals_payload:
                runner_name = runner.get('runnerName', '') or runner.get('name', '')
                if (str(runner.get('raceNo')) == str(race_num) and 
                    runner_name.strip().lower() == horse_name.lower()):
                    row['last200TimePrice'] = str(runner.get('last200TimePrice', ''))
                    row['last200TimeRank'] = str(runner.get('last200TimeRank', ''))
                    row['last400TimePrice'] = str(runner.get('last400TimePrice', ''))
                    row['last400TimeRank'] = str(runner.get('last400TimeRank', ''))
                    row['last600TimePrice'] = str(runner.get('last600TimePrice', ''))
                    row['last600TimeRank'] = str(runner.get('last600TimeRank', ''))
                    logger.debug(f"Injected API data for {horse_name} (R{race_num}): PFAI={runner.get('pfaiScore', 'N/A')}")
                    break

    # ===== INJECT PFAI SCORE FROM RATINGS DATA =====
    if ratings_data:
        ratings_payload = ratings_data.get('payLoad', [])
        logged_horses = set()

        for row in parsed_csv:
            row['pfaiScore'] = ''
            horse_name = row.get('horse name', '').strip()
            race_num = row.get('race number', '').strip()
            key = f"{horse_name}-{race_num}"

            for runner in ratings_payload:
                runner_name = runner.get('runnerName', '').strip()
                if (str(runner.get('raceNo')) == str(race_num) and
                    runner_name.lower() == horse_name.lower()):
                    row['pfaiScore'] = str(runner.get('pfaiScore', ''))
                    if key not in logged_horses:
                        logger.info(f"✅ PFAI injected: {horse_name} R{race_num} = {runner.get('pfaiScore')}")
                        logged_horses.add(key)
                    break

    # ===== INJECT RUNNING POSITION FROM SPEED MAP DATA =====
    if speed_maps_data:
        logger.info("✅ SPEEDMAP: starting runningPosition injection")

        for row in parsed_csv:
            row['runningPosition'] = ''

        speedmap_lookup = {}

        payload = speed_maps_data.get('payLoad', [])
        logger.info(f"✅ SPEEDMAP: payLoad races={len(payload)}")

        for race_sm in payload:
            race_no = str(race_sm.get('raceNo', '')).strip()

            for item in race_sm.get('items', []):
                runner_name = normalize_runner_name(item.get('runnerName') or '')
                settle_val = item.get('settle')
                try:
                    settle_num = int(str(settle_val).split('/')[0].strip())
                except Exception:
                    settle_num = None

                if settle_num == 1:
                    pos_category = 'LEADER'
                elif settle_num is not None and 2 <= settle_num <= 3:
                    pos_category = 'ONPACE'
                elif settle_num is not None and 4 <= settle_num <= 7:
                    pos_category = 'MIDFIELD'
                elif settle_num is not None:
                    pos_category = 'BACKMARKER'
                else:
                    pos_category = None

                if race_no and runner_name and pos_category:
                    speedmap_lookup[(race_no, runner_name)] = pos_category

        injected_count = 0
        for row in parsed_csv:
            horse_name = normalize_runner_name(row.get('horse name', ''))
            race_num = str(row.get('race number', '')).strip()
            key = (race_num, horse_name)
            if key in speedmap_lookup:
                row['runningPosition'] = speedmap_lookup[key]
                injected_count += 1

        logger.info(f"✅ SPEEDMAP: Injected running position for {injected_count} horses")

    # ==========================================
    # SPLIT OUT SCRATCHED HORSES BEFORE ANALYSIS
    # ==========================================
    scratched_rows = []
    if scratched_set:
        active_rows = []
        for row in parsed_csv:
            try:
                horse_num = int(str(row.get('horse number', '')).strip())
                race_num = int(str(row.get('race number', '')).strip())
            except (ValueError, TypeError):
                active_rows.append(row)
                continue
            if (race_num, horse_num) in scratched_set:
                scratched_rows.append(row)
                logger.info(f"✂️  Scratched: {row.get('horse name')} R{race_num} #{horse_num}")
            else:
                active_rows.append(row)
        parsed_csv = active_rows
        logger.info(f"✅ {len(scratched_rows)} scratched horses removed before analysis")

    # Rebuild CSV with active horses only
    csv_data = rebuildCSV(parsed_csv)
    logger.info("✅ Rebuilt CSV with API data injection")

    # Run the analyzer
    analysis_results = run_analyzer(csv_data, track_condition, is_advanced,
                                    strike_rate_data=strike_rate_data)
    
    if not analysis_results:
        raise Exception("No results returned from analyzer")
    
    # Create meeting record
    meeting = Meeting(
        user_id=user_id,
        meeting_name=filename.replace('.csv', ''),
        csv_data=csv_data,
        puntingform_id=puntingform_id,
        auto_imported=puntingform_id is not None,
        rail_position=rail_position
    )
    db.session.add(meeting)
    db.session.flush()

    # Group results by race
    races_data = {}
    for result in analysis_results:
        race_num = result['horse'].get('race number', '0')
        if not race_num or not str(race_num).isdigit():
            continue
        if race_num not in races_data:
            races_data[race_num] = []
        races_data[race_num].append(result)

    # Create race and horse records
    for race_num, horses_results in races_data.items():
        first_horse = horses_results[0]['horse'] if horses_results else {}
        
        race = Race(
            meeting_id=meeting.id,
            race_number=int(race_num) if race_num else 0,
            distance=first_horse.get('distance', ''),
            race_class=first_horse.get('class restrictions', ''),
            track_condition=track_condition,
            speed_maps_json=extract_race_speed_maps(speed_maps_data, race_num),
            ratings_json=extract_race_ratings(ratings_data, race_num),
            sectionals_json=extract_race_sectionals(sectionals_data, race_num) if sectionals_data else None
        )
        db.session.add(race)
        db.session.flush()

        for result in horses_results:
            horse_data = result['horse']
            
            horse = Horse(
                race_id=race.id,
                horse_name=horse_data.get('horse name', 'Unknown'),
                barrier=int(horse_data.get('barrier', 0)) if horse_data.get('barrier') else None,
                weight=float(horse_data.get('horse weight', 0)) if horse_data.get('horse weight') else None,
                jockey=horse_data.get('horse jockey', ''),
                trainer=horse_data.get('horse trainer', ''),
                form=horse_data.get('horse last10', ''),
                csv_data=horse_data
            )
            db.session.add(horse)
            db.session.flush()

            base_score = result.get('adjustedScore', result.get('score', 0))
            running_position = horse_data.get('runningposition', '')
            rail_pos = rail_position

            if running_position and rail_pos:
                base_score = apply_track_bias(base_score, running_position, rail_pos, 0)

            prediction = Prediction(
                horse_id=horse.id,
                score=base_score,
                predicted_odds=result.get('trueOdds', ''),
                win_probability=result.get('winProbability', ''),
                performance_component=result.get('performanceComponent', ''),
                base_probability=result.get('baseProbability', ''),
                notes=result.get('notes', '')
            )
            db.session.add(prediction)

    # ==========================================
    # SAVE SCRATCHED HORSES WITH ZERO SCORES
    # ==========================================
    if scratched_rows:
        race_id_lookup = {}
        for race_num_str in races_data.keys():
            race = Race.query.filter_by(
                meeting_id=meeting.id,
                race_number=int(race_num_str)
            ).first()
            if race:
                race_id_lookup[int(race_num_str)] = race.id

        for row in scratched_rows:
            try:
                s_race_num = int(str(row.get('race number', '')).strip())
            except (ValueError, TypeError):
                continue

            race_id = race_id_lookup.get(s_race_num)
            if not race_id:
                race = Race.query.filter_by(
                    meeting_id=meeting.id,
                    race_number=s_race_num
                ).first()
                if not race:
                    continue
                race_id = race.id

            s_horse = Horse(
                race_id=race_id,
                horse_name=row.get('horse name', 'Unknown'),
                barrier=int(row.get('horse barrier', 0)) if row.get('horse barrier') else None,
                weight=float(row.get('horse weight', 0)) if row.get('horse weight') else None,
                jockey=row.get('horse jockey', ''),
                trainer=row.get('horse trainer', ''),
                form=row.get('horse last10', ''),
                csv_data=row,
                is_scratched=True
            )
            db.session.add(s_horse)
            db.session.flush()

            s_prediction = Prediction(
                horse_id=s_horse.id,
                score=0.0,
                predicted_odds='',
                win_probability='',
                performance_component='',
                base_probability='',
                notes='Scratched'
            )
            db.session.add(s_prediction)

        logger.info(f"✅ Saved {len(scratched_rows)} scratched horses with zero scores")

    db.session.commit()

    # CLEANUP
    csv_data = None
    analysis_results = None
    races_data = None
    import gc
    gc.collect()

    return meeting

def get_meeting_results(meeting_id):
    """
    Retrieve meeting results formatted for display
    """
    meeting = Meeting.query.get_or_404(meeting_id)
    races = Race.query.filter_by(meeting_id=meeting_id).order_by(Race.race_number).all()
    
    results = {
        'meeting_name': meeting.meeting_name,
        'uploaded_at': meeting.uploaded_at,
        'races': []
    }
    
    for race in races:
        horses = Horse.query.filter_by(race_id=race.id).distinct(Horse.horse_name).all()
        
        race_data = {
            'race_number': race.race_number,
            'distance': race.distance,
            'race_class': race.race_class,
            'track_condition': race.track_condition,
            'speed_maps_json': race.speed_maps_json,
            'ratings_json': race.ratings_json,
            'horses': []
        }
        
        for horse in horses:
            pred = horse.prediction
            horse_data = {
                'horse_id': horse.id,
                'horse_name': horse.horse_name,
                'barrier': horse.barrier,
                'weight': horse.weight,
                'jockey': horse.jockey,
                'trainer': horse.trainer,
                'form': horse.form,
                'score': pred.score if pred else 0,
                'odds': pred.predicted_odds if pred else '',
                'win_probability': pred.win_probability if pred else '',
                'performance_component': pred.performance_component if pred else '',
                'base_probability': pred.base_probability if pred else '',
                'notes': pred.notes if pred else '',
                'is_scratched': horse.is_scratched,
                'prediction': type('P', (), {
                    'score': pred.score if pred else 0,
                    'predicted_odds': pred.predicted_odds if pred else '',
                    'win_probability': pred.win_probability if pred else '',
                    'notes': pred.notes if pred else '',
                })() if pred else None
            }
            race_data['horses'].append(horse_data)
        
        # Sort horses by score descending
        race_data['horses'].sort(key=lambda x: x['score'], reverse=True)
        results['races'].append(race_data)
    
    return results


# ----- Routes -----
@app.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("history"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("history"))
        
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        remember = bool(request.form.get("remember"))
        
        user = User.query.filter_by(username=username).first()
        
        if not user or not user.check_password(password):
            flash("Invalid username or password", "danger")
            return redirect(url_for("login"))
        
        if not user.is_active:
            flash("Your account has been deactivated", "danger")
            return redirect(url_for("login"))
        
        # Update last login
        user.last_login = datetime.utcnow()
        db.session.commit()
        
        login_user(user, remember=remember)
        flash(f"Welcome back, {username}!", "success")
        return redirect(url_for("history"))
    
    # This handles GET requests - notice it's NOT indented under the if POST block
    return render_template("login.html")
    
def parse_notes_components(notes):
    """
    Parse the notes field to extract individual scoring components.
    Returns a dict of component_name -> score_value
    """
    if not notes:
        return {}

    import re
    components = {}

    patterns = [

        # ====== LAST 10 FORM ======
        (r'([+-]?\s*[\d.]+)\s*:\s*Ran places:', '_ran_places_dynamic'),

        # ====== JOCKEYS ======
        (r'\+\s*25\.0\s*:\s*Elite value jockey', 'Jockey - Elite (50%+ ROI)'),
        (r'\+\s*20\.0\s*:\s*Strong value jockey', 'Jockey - Strong Value (20-50% ROI)'),
        (r'\+\s*10\.0\s*:\s*Profitable jockey', 'Jockey - Profitable (0-20% ROI)'),
        (r'-\s*15\.0\s*:\s*Poor value jockey', 'Jockey - Poor Value'),
        # Live L100 strike rate patterns
        (r'\+\s*12\.0\s*:\s*Jockey hot form', 'Jockey - Hot Form (L100 25%+ SR)'),
        (r'\+\s*6\.0\s*:\s*Jockey solid form', 'Jockey - Solid Form (L100 18-25% SR)'),
        (r'[-−]\s*6\.0\s*:\s*Jockey poor form', 'Jockey - Poor Form (L100 6-11% SR)'),
        (r'[-−]\s*12\.0\s*:\s*Jockey cold', 'Jockey - Cold (L100 <6% SR)'),

        # ====== TRAINERS ======
        (r'\+\s*20\.0\s*:\s*Elite value trainer', 'Trainer - Elite (50%+ ROI)'),
        (r'\+\s*15\.0\s*:\s*Strong value trainer', 'Trainer - Strong Value (20-50% ROI)'),
        (r'\+\s*10\.0\s*:\s*Profitable trainer', 'Trainer - Profitable (0-20% ROI)'),
        (r'-\s*15\.0\s*:\s*Poor value trainer|Poor value trainer.*destroys ROI', 'Trainer - Poor Value'),
        # Live L100 strike rate patterns
        (r'\+\s*10\.0\s*:\s*Trainer hot form', 'Trainer - Hot Form (L100 22%+ SR)'),
        (r'\+\s*5\.0\s*:\s*Trainer solid form', 'Trainer - Solid Form (L100 16-22% SR)'),
        (r'[-−]\s*5\.0\s*:\s*Trainer poor form', 'Trainer - Poor Form (L100 5-10% SR)'),
        (r'[-−]\s*10\.0\s*:\s*Trainer cold', 'Trainer - Cold (L100 <5% SR)'),

        # ====== TRACK RECORD - WIN RATES ======
        (r'\+\s*6\.0\s*:\s*Exceptional win rate.*at this track\b', 'Track Win Rate - Exceptional (51%+)'),
        (r'\+\s*5\.0\s*:\s*Strong win rate.*at this track\b', 'Track Win Rate - Strong (36-50%)'),
        (r'\+\s*4\.0\s*:\s*Good win rate.*at this track\b', 'Track Win Rate - Good (26-35%)'),
        (r'\+\s*2\.0\s*:\s*Moderate win rate.*at this track\b', 'Track Win Rate - Moderate (16-25%)'),
        (r'\+\s*1\.0\s*:\s*Low win rate.*at this track\b', 'Track Win Rate - Low (1-15%)'),
        (r'\+\s*0\.0\s*:\s*No wins at this track\b', 'Track Win Rate - No Wins'),
        (r'\+\s*0\.0\s*:\s*No runs at this track\b', 'Track - No Runs'),

        # ====== TRACK RECORD - PODIUM RATES ======
        (r'\+\s*6\.0\s*:\s*Elite podium rate.*at this track\b', 'Track Podium Rate - Elite (85%+)'),
        (r'\+\s*5\.0\s*:\s*Excellent podium rate.*at this track\b', 'Track Podium Rate - Excellent (70-84%)'),
        (r'\+\s*4\.0\s*:\s*Strong podium rate.*at this track\b', 'Track Podium Rate - Strong (55-69%)'),
        (r'\+\s*3\.0\s*:\s*Good podium rate.*at this track\b', 'Track Podium Rate - Good (40-54%)'),
        (r'\+\s*1\.0\s*:\s*Moderate podium rate.*at this track\b', 'Track Podium Rate - Moderate (25-39%)'),
        # FIX: also catches "Poor podium rate" phrasing
        (r'-\s*5\.0\s*:\s*Poor performance at this track|Poor podium rate.*at this track', 'Track - Poor Performance'),
        (r'=\s*([\d.]+)\s*:\s*Total track score', '_track_score_dynamic'),

        # ====== TRACK+DISTANCE RECORD - WIN RATES ======
        (r'\+\s*8\.0\s*:\s*Exceptional win rate.*at this track\+distance', 'Track+Distance Win Rate - Exceptional'),
        (r'\+\s*7\.0\s*:\s*Strong win rate.*at this track\+distance', 'Track+Distance Win Rate - Strong'),
        (r'\+\s*5\.0\s*:\s*Good win rate.*at this track\+distance', 'Track+Distance Win Rate - Good'),
        (r'\+\s*3\.0\s*:\s*Moderate win rate.*at this track\+distance', 'Track+Distance Win Rate - Moderate'),
        (r'\+\s*1\.0\s*:\s*Low win rate.*at this track\+distance', 'Track+Distance Win Rate - Low'),
        (r'\+\s*0\.0\s*:\s*No wins at this track\+distance', 'Track+Distance Win Rate - No Wins'),
        (r'\+\s*0\.0\s*:\s*No runs at this track\+distance', 'Track+Distance - No Runs'),

        # ====== TRACK+DISTANCE RECORD - PODIUM RATES ======
        (r'\+\s*8\.0\s*:\s*Elite podium rate.*at this track\+distance', 'Track+Distance Podium Rate - Elite'),
        (r'\+\s*7\.0\s*:\s*Excellent podium rate.*at this track\+distance', 'Track+Distance Podium Rate - Excellent'),
        (r'\+\s*6\.0\s*:\s*Strong podium rate.*at this track\+distance', 'Track+Distance Podium Rate - Strong'),
        (r'\+\s*4\.0\s*:\s*Good podium rate.*at this track\+distance', 'Track+Distance Podium Rate - Good'),
        (r'\+\s*2\.0\s*:\s*Moderate podium rate.*at this track\+distance', 'Track+Distance Podium Rate - Moderate'),
        (r'-\s*6\.0\s*:\s*Poor performance at this track\+distance', 'Track+Distance - Poor Performance'),
        (r'=\s*([\d.]+)\s*:\s*Total track\+distance score', '_td_score_dynamic'),

        # ====== DISTANCE RECORD - WIN RATES ======
        (r'\+\s*8\.0\s*:\s*Exceptional win rate.*at this distance\b', 'Distance Win Rate - Exceptional (51%+)'),
        (r'\+\s*7\.0\s*:\s*Strong win rate.*at this distance\b', 'Distance Win Rate - Strong (36-50%)'),
        (r'\+\s*5\.0\s*:\s*Good win rate.*at this distance\b', 'Distance Win Rate - Good (26-35%)'),
        (r'\+\s*3\.0\s*:\s*Moderate win rate.*at this distance\b', 'Distance Win Rate - Moderate (16-25%)'),
        (r'\+\s*1\.0\s*:\s*Low win rate.*at this distance\b', 'Distance Win Rate - Low (1-15%)'),
        (r'\+\s*0\.0\s*:\s*No wins at this distance\b', 'Distance Win Rate - No Wins'),
        (r'\+\s*0\.0\s*:\s*No runs at this distance\b', 'Distance - No Runs'),

        # ====== DISTANCE RECORD - PODIUM RATES ======
        (r'\+\s*8\.0\s*:\s*Elite podium rate.*at this distance\b', 'Distance Podium Rate - Elite (85%+)'),
        (r'\+\s*7\.0\s*:\s*Excellent podium rate.*at this distance\b', 'Distance Podium Rate - Excellent (70-84%)'),
        (r'\+\s*6\.0\s*:\s*Strong podium rate.*at this distance\b', 'Distance Podium Rate - Strong (55-69%)'),
        (r'\+\s*4\.0\s*:\s*Good podium rate.*at this distance\b', 'Distance Podium Rate - Good (40-54%)'),
        (r'\+\s*2\.0\s*:\s*Moderate podium rate.*at this distance\b', 'Distance Podium Rate - Moderate (25-39%)'),
        (r'-\s*6\.0\s*:\s*Poor performance at this distance\b', 'Distance - Poor Performance'),
        (r'=\s*([\d.]+)\s*:\s*Total distance score', '_dist_score_dynamic'),

        # ====== TRACK CONDITION - WIN RATES ======
        (r'\+\s*12\.0\s*:\s*Exceptional win rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - Exceptional (51%+)'),
        (r'\+\s*10\.0\s*:\s*Strong win rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - Strong (36-50%)'),
        (r'\+\s*8\.0\s*:\s*Good win rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - Good (26-35%)'),
        (r'\+\s*5\.0\s*:\s*Moderate win rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - Moderate (16-25%)'),
        (r'\+\s*2\.0\s*:\s*Low win rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - Low (1-15%)'),
        (r'\+\s*0\.0\s*:\s*No wins on (good|soft|heavy|firm|synthetic)', 'Condition Win Rate - No Wins'),
        (r'\+\s*0\.0\s*:\s*No runs on (good|soft|heavy|firm|synthetic)', 'Condition - No Runs'),

        # ====== TRACK CONDITION - PODIUM RATES ======
        (r'\+\s*12\.0\s*:\s*Elite podium rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Podium Rate - Elite (85%+)'),
        (r'\+\s*10\.0\s*:\s*Excellent podium rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Podium Rate - Excellent (70-84%)'),
        (r'\+\s*9\.0\s*:\s*Strong podium rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Podium Rate - Strong (55-69%)'),
        (r'\+\s*6\.0\s*:\s*Good podium rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Podium Rate - Good (40-54%)'),
        (r'\+\s*3\.0\s*:\s*Moderate podium rate.*on (good|soft|heavy|firm|synthetic)', 'Condition Podium Rate - Moderate (25-39%)'),
        (r'-\s*8\.0\s*:\s*Poor performance on (good|soft|heavy|firm|synthetic)', 'Condition - Poor Performance'),
        (r'=\s*([\d.]+)\s*:\s*Total track condition score', '_cond_score_dynamic'),

        # ====== DISTANCE CHANGE ======
        # FIX: old patterns matched "Stepping up Xm in distance" — new format uses bracketed ranges
        # Also handle ~ prefix for near-baseline
        (r'[~+\-]\s*[\d.]+\s*:\s*Step(?:ping)? up.*\(400m\+\)', 'Distance Change - Step Up Large (400m+)'),
        (r'[~+\-]\s*[\d.]+\s*:\s*Step(?:ping)? up.*\(200-400m\)', 'Distance Change - Step Up Moderate (200-400m)'),
        (r'[~+\-]\s*[\d.]+\s*:\s*Drop(?:ping)? back.*\(400m\+\)', 'Distance Change - Drop Back Large (400m+)'),
        (r'\+\s*8\.0\s*:\s*Drop back in distance \(200-400m\)', 'Distance Change - Drop Back Moderate (200-400m)'),

        # ====== CLASS CHANGE ======
        (r'\+\s*([\d.]+):\s*Stepping DOWN', '_class_drop_dynamic'),
        (r'(-[\d.]+):\s*Stepping UP', '_class_rise_dynamic'),

        # ====== LAST START - WINNERS ======
        (r'\+\s*10\.0\s*:\s*Dominant last.?start win', 'Last Start - Dominant Win (5L+)'),
        (r'\+\s*7\.0\s*:\s*Comfortable last.?start win', 'Last Start - Comfortable Win (2-5L)'),
        (r'\+\s*5\.0\s*:\s*Narrow last.?start win', 'Last Start - Narrow Win (0.5-2L)'),
        (r'\+\s*15\.0\s*:\s*Last Start - Photo Win', 'Last Start - Photo Win (<0.5L)'),

        # ====== LAST START - PLACED ======
        (r'\+\s*5\.0\s*:\s*Narrow loss.*very competitive', 'Last Start - Narrow Loss (≤1L)'),
        (r'\+\s*3\.0\s*:\s*Close loss \(.*nd.*\)', 'Last Start - Close Loss 2nd (1-2L)'),
        (r'\+\s*3\.0\s*:\s*Close loss \(.*rd.*\)', 'Last Start - Close Loss 3rd (1-2L)'),

        # ====== LAST START - BEATEN ======
        (r'\+\s*0\.0\s*:\s*Competitive effort', 'Last Start - Competitive Effort (≤3L)'),
        (r'-\s*3\.0\s*:\s*Beaten clearly', 'Last Start - Beaten Clearly (3-6L)'),
        (r'-\s*5\.0\s*:\s*Beaten badly.*nd', 'Last Start - Beaten Badly Placed'),
        (r'\+\s*5\.0\s*:\s*Well beaten.*BUT major class drop', 'Last Start - Well Beaten + Class Drop'),
        (r'\+\s*5\.0\s*:\s*Beaten.*dropping in class significantly', 'Last Start - Beaten + Dropping Class'),
        (r'\+\s*0\.0\s*:\s*Beaten clearly.*BUT dropping in class', 'Last Start - Beaten Clearly + Dropping'),
        (r'-\s*7\.0\s*:\s*Well beaten', 'Last Start - Well Beaten (6-10L)'),
        (r'-\s*25\.0\s*:\s*Demolished', 'Last Start - Demolished (10L+)'),

        # ====== DAYS SINCE RUN ======
        # FIX: new format is "Fresh return - X days since last run (150-199 days, +ROI%)"
        # not "Too fresh (150+ days)" — match on the bracket ranges and also the old format
        (r'\+\s*0\.0\s*:\s*Quick backup', 'Days Since Run - Quick Backup (≤7 days)'),
        (r'[\d.]+\s*days?\s*since last run.*150-199 days|Too fresh.*150', 'Days Since Run - Fresh Return (150-199 days)'),
        (r'[\d.]+\s*days?\s*since last run.*200-249 days|Too fresh.*200', 'Days Since Run - Too Fresh (200+ days)'),
        (r'[\d.]+\s*days?\s*since last run.*250|Too fresh.*250', 'Days Since Run - Too Fresh (250+ days)'),
        (r'[\d.]+\s*days?\s*since last run.*(?:365|year|1\+\s*year)|Too fresh.*over 1 year', 'Days Since Run - Too Fresh (1+ year)'),

        # ====== FORM PRICE ======
        # FIX: old patterns used score magnitude to infer price bracket — unreliable.
        # Match on the price value directly from notes text instead.
        (r'Form price \$(\d+\.\d+)', '_form_price_dynamic'),

        # ====== FIRST UP / SECOND UP ======
        (r'\+\s*0\.0\s*:\s*First-?up winner', 'First Up - Has Won First Up'),
        (r'\+\s*0\.0\s*:\s*Strong first-?up podium', 'First Up - Strong Podium Rate'),
        (r'\+\s*3\.0\s*:\s*Second-?up winner', 'Second Up - Has Won Second Up'),
        (r'\+\s*2\.0\s*:\s*Strong second-?up podium', 'Second Up - Strong Podium Rate'),
        # FIX: old pattern required literal "(UNDEFEATED)" — new format is "(UNDEFEATED: 3:3-0-0)"
        (r'\+\s*15\.0\s*:\s*First-?up specialist.*UNDEFEATED', 'First Up - Specialist Undefeated'),
        (r'\+\s*15\.0\s*:\s*Second-?up specialist.*UNDEFEATED', 'Second Up - Specialist Undefeated'),
        (r'-\s*1\.0\s*:\s*Unclear spell', 'Spell Status - Unclear'),

        # ====== WEIGHT ======
        (r'\+\s*15\.0\s*:\s*Weight.*(?:BELOW|well below) race avg', 'Weight vs Field - Well Below (3kg+)'),
        (r'\+\s*10\.0\s*:\s*Weight.*below race avg', 'Weight vs Field - Below (2-3kg)'),
        (r'\+\s*6\.0\s*:\s*Weight.*below race avg', 'Weight vs Field - Slightly Below (1-2kg)'),
        (r'\+\s*3\.0\s*:\s*Weight.*below race avg', 'Weight vs Field - Marginally Below (0.5-1kg)'),
        (r'0\.0\s*:\s*Weight.*near race avg', 'Weight vs Field - Near Average'),
        (r'-\s*3\.0\s*:\s*Weight.*above race avg', 'Weight vs Field - Marginally Above'),
        (r'-\s*6\.0\s*:\s*Weight.*above race avg', 'Weight vs Field - Above (1-2kg)'),
        (r'-\s*10\.0\s*:\s*Weight.*above race avg', 'Weight vs Field - Well Above (2-3kg)'),
        (r'-\s*15\.0\s*:\s*Weight.*(?:ABOVE|well above) race avg', 'Weight vs Field - Well Above (3kg+)'),
        (r'\+\s*15\.0\s*:\s*Dropped.*from last start', 'Weight Change - Dropped 3kg+'),
        (r'\+\s*10\.0\s*:\s*Dropped.*from last start', 'Weight Change - Dropped 2-3kg'),
        (r'\+\s*5\.0\s*:\s*Dropped.*from last start', 'Weight Change - Dropped 1-2kg'),
        (r'-\s*5\.0\s*:\s*Up.*from last start', 'Weight Change - Up 1-2kg'),
        (r'-\s*10\.0\s*:\s*Up.*from last start', 'Weight Change - Up 2-3kg'),
        (r'-\s*15\.0\s*:\s*Up.*from last start', 'Weight Change - Up 3kg+'),

        # ====== CAREER WIN RATE ======
        (r'\+\s*0\.0\s*:\s*Elite career win rate', 'Career Win Rate - Elite 40%+'),
        (r'\+\s*0\.0\s*:\s*Strong career win rate', 'Career Win Rate - Strong 30-40%'),
        (r'-\s*15\.0\s*:\s*Poor career win rate', 'Career Win Rate - Poor <10%'),

        # ====== AGE/SEX - BONUSES ======
        (r'\+\s*25\.0\s*:\s*5yo horse', 'Age/Sex - 5yo Horse (Entire)'),
        (r'\+\s*20\.0\s*:\s*8yo Mare', 'Age/Sex - 8yo Mare'),
        (r'\+\s*3\.0\s*:\s*Prime age \(3yo\)', 'Age/Sex - 3yo'),
        (r'\+\s*0\.0\s*:\s*\(4yo\)', 'Age/Sex - 4yo'),

        # ====== AGE/SEX - MARE PENALTIES ======
        (r'-\s*15\.0\s*:\s*5yo Mare', 'Age/Sex - 5yo Mare Penalty'),
        (r'-\s*10\.0\s*:\s*6-7yo Mare', 'Age/Sex - 6-7yo Mare Penalty'),

        # ====== AGE/SEX - OLD AGE PENALTIES ======
        (r'-\s*25\.0\s*:\s*Old age \(7-8yo', 'Age/Sex - 7-8yo Penalty'),
        (r'-\s*35\.0\s*:\s*9yo - ZERO WINS', 'Age/Sex - 9yo Penalty'),
        (r'-\s*40\.0\s*:\s*10yo', 'Age/Sex - 10yo Penalty'),
        (r'-\s*45\.0\s*:\s*11yo', 'Age/Sex - 11yo Penalty'),
        (r'-\s*50\.0\s*:\s*12yo', 'Age/Sex - 12yo Penalty'),
        (r'-\s*60\.0\s*:\s*13\+yo', 'Age/Sex - 13+yo Penalty'),

        # ====== COLT BONUSES ======
        # FIX: new format is "3yo COLT (" not "3yo COLT combo"
        (r'\+\s*20\.0\s*:\s*3yo COLT', 'Colt - 3yo Colt'),
        # FIX: "COLT (" pattern — make sure this comes AFTER 3yo COLT so it doesn't double-match
        (r'\+\s*20\.0\s*:\s*COLT\s*\((?!.*3yo)', 'Colt - Base Bonus'),
        (r'\+\s*15\.0\s*:\s*Fast sectional \+ COLT combo', 'Colt - Fast Sectional + Colt'),

        # ====== SIRE SCORING ======
        # NEW: e.g. "+6.0: Sire Night Of Thunder (66.3% ROI, 26 runners)"
        (r'[+-][\d.]+\s*:\s*Sire\s+.+?\(([-\d.]+)%\s*ROI', '_sire_dynamic'),

        # ====== COUNTRY OF ORIGIN ======
        # NEW: e.g. "- 2.0 : Irish-bred (-11.0% ROI, 350 runners)"
        (r':\s*([\w][\w -]*?bred)\s*\(([-+\d.]+)%\s*ROI', '_country_dynamic'),

        # ====== SPECIALIST / PERFECT RECORD ======
        (r'\+\s*15\.0\s*:\s*Specialist - Undefeated Track\+Distance', 'Specialist - Undefeated Track+Distance'),
        (r'\+\s*15\.0\s*:\s*Specialist - Undefeated Distance(?!.*Track)', 'Specialist - Undefeated Distance'),
        (r'\+\s*([\d.]+)\s*:\s*UNDEFEATED.*condition.*specialist', 'Specialist - Undefeated Condition'),
        (r'\+\s*([\d.]+)\s*:\s*100% PODIUM.*track\+distance', 'Specialist - Perfect Podium Track+Distance'),
        (r'\+\s*([\d.]+)\s*:\s*100% PODIUM.*track\b', 'Specialist - Perfect Podium Track'),
        (r'\+\s*([\d.]+)\s*:\s*100% PODIUM.*distance', 'Specialist - Perfect Podium Distance'),
        (r'\+\s*([\d.]+)\s*:\s*100% PODIUM.*condition', 'Specialist - Perfect Podium Condition'),

        # ====== HISTORICAL SECTIONALS (CSV) ======
        # FIX: old pattern required leading + but new format uses +- prefix for negative z-scores
        (r'(\+[\d.]+)\s*:\s*weighted avg \(z=', 'Sectional History - Weighted Avg'),
        (r'(\+[\d.]+)\s*:\s*best of last \d+', 'Sectional History - Best Recent'),
        (r'\+\s*([\d.]+):\s*consistency - excellent', 'Sectional Consistency - Excellent'),
        (r'\+\s*([\d.]+):\s*consistency - good', 'Sectional Consistency - Good'),
        (r'[+\-]?\s*([\d.]+):\s*consistency - fair', 'Sectional Consistency - Fair'),
        (r'[+\-]?\s*([\d.]+):\s*consistency - poor', 'Sectional Consistency - Poor'),

        # ====== API SECTIONALS ======
        (r'[+\-]?\s*[\d.]+:\s*Last 200m \(Rank \d+.*ELITE', 'API Sectional - Last 200m Elite'),
        (r'[+\-]?\s*[\d.]+:\s*Last 200m \(Rank \d+.*VERY GOOD', 'API Sectional - Last 200m Very Good'),
        (r'[+\-]?\s*[\d.]+:\s*Last 200m \(Rank \d+.*\bGOOD\b', 'API Sectional - Last 200m Good'),
        (r'[+\-]?\s*[\d.]+:\s*Last 200m \(Rank \d+(?!.*(?:ELITE|VERY GOOD|GOOD)).*AVERAGE', 'API Sectional - Last 200m Average'),
        (r'[+\-]?\s*[\d.]+:\s*Last 200m \(Rank \d+(?!.*(?:ELITE|VERY GOOD|GOOD|AVERAGE)).*POOR', 'API Sectional - Last 200m Poor'),
        (r'[+\-]?\s*[\d.]+:\s*Last 400m \(Rank \d+.*ELITE', 'API Sectional - Last 400m Elite'),
        (r'[+\-]?\s*[\d.]+:\s*Last 400m \(Rank \d+.*VERY GOOD', 'API Sectional - Last 400m Very Good'),
        (r'[+\-]?\s*[\d.]+:\s*Last 400m \(Rank \d+.*\bGOOD\b', 'API Sectional - Last 400m Good'),
        (r'[+\-]?\s*[\d.]+:\s*Last 400m \(Rank \d+(?!.*(?:ELITE|VERY GOOD|GOOD)).*AVERAGE', 'API Sectional - Last 400m Average'),
        (r'[+\-]?\s*[\d.]+:\s*Last 400m \(Rank \d+(?!.*(?:ELITE|VERY GOOD|GOOD|AVERAGE)).*POOR', 'API Sectional - Last 400m Poor'),
        (r'[+\-]?\s*[\d.]+:\s*Last 600m \(Rank \d+.*ELITE', 'API Sectional - Last 600m Elite'),
        (r'[+\-]?\s*[\d.]+:\s*Last 600m \(Rank \d+.*VERY GOOD', 'API Sectional - Last 600m Very Good'),
        (r'[+\-]?\s*[\d.]+:\s*Last 600m \(Rank \d+.*\bGOOD\b', 'API Sectional - Last 600m Good'),
        (r'\+\s*([\d.]+):\s*IMPROVING TREND', 'API Sectional - Improving Trend'),

        # ====== RUNNING POSITION (SPEEDMAP) ======
        # ====== SPRINT LEADER RUN DOWN BONUS ======
        (r'\+\s*15\.0\s*:\s*Sprint Leader Run Down Bonus', 'Pace Angle - Sprint Leader Run Down'),

        # ====== RUNNING POSITION (SPEEDMAP) ======
        (r'[+\-]?\s*12\.0\s*:\s*LEADER in Sprint', 'Running Position - Leader Sprint'),
        (r'[+\-]?\s*8\.0\s*:\s*ONPACE in Sprint', 'Running Position - OnPace Sprint'),
        (r'[+\-]?\s*0\.0\s*:\s*MIDFIELD in Sprint', 'Running Position - Midfield Sprint'),
        (r'[+\-]?\s*8\.0\s*:\s*BACKMARKER in Sprint', 'Running Position - Backmarker Sprint'),
        (r'[+\-]?\s*6\.0\s*:\s*LEADER in Mile', 'Running Position - Leader Mile'),
        (r'[+\-]?\s*8\.0\s*:\s*ONPACE in Mile', 'Running Position - OnPace Mile'),
        (r'[+\-]?\s*2\.0\s*:\s*MIDFIELD in Mile', 'Running Position - Midfield Mile'),
        (r'[+\-]?\s*5\.0\s*:\s*BACKMARKER in Mile', 'Running Position - Backmarker Mile'),
        (r'[+\-]?\s*5\.0\s*:\s*LEADER in Middle distance', 'Running Position - Leader Middle'),
        (r'[+\-]?\s*5\.0\s*:\s*ONPACE in Middle distance', 'Running Position - OnPace Middle'),
        (r'[+\-]?\s*3\.0\s*:\s*MIDFIELD in Middle distance', 'Running Position - Midfield Middle'),
        (r'[+\-]?\s*0\.0\s*:\s*BACKMARKER in Middle distance', 'Running Position - Backmarker Middle'),
        (r'[+\-]?\s*7\.0\s*:\s*LEADER in Staying', 'Running Position - Leader Staying'),
        (r'[+\-]?\s*3\.0\s*:\s*ONPACE in Staying', 'Running Position - OnPace Staying'),
        (r'[+\-]?\s*5\.0\s*:\s*MIDFIELD in Staying', 'Running Position - Midfield Staying'),
        (r'[+\-]?\s*2\.0\s*:\s*BACKMARKER in Staying', 'Running Position - Backmarker Staying'),
        
        # ====== HIDDEN EDGE COMBINATION BONUSES ======
        (r'\+\s*[\d.]+\s*:\s*Hidden Edge.*Sprint leader.*last start favoured', 'Hidden Edge - Sprint Leader + Last Start Favoured'),
        (r'\+\s*[\d.]+\s*:\s*Hidden Edge.*Strong condition podium.*last start favourite', 'Hidden Edge - Condition Podium + Last Start Favourite'),
        
        # ====== PFAI BLEND ======
        (r'PFAI Score:\s*(9[0-9]|100)[\. ]', 'PFAI Score - 90+'),
        (r'PFAI Score:\s*(8[0-9])[\. ]', 'PFAI Score - 80-89'),
        (r'PFAI Score:\s*(7[0-9])[\. ]', 'PFAI Score - 70-79'),
        (r'PFAI Score:\s*(6[0-9])[\. ]', 'PFAI Score - 60-69'),
        (r'PFAI Score:\s*([0-5][0-9])[\. ]', 'PFAI Score - <60'),

        # ====== MARKET EXPECTATION ======
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(best market performer', 'Market Expectation - Best in Field'),
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(chronic overperformer', 'Market Expectation - Chronic Overperformer'),
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(strong overperformer', 'Market Expectation - Strong Overperformer'),
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(moderate outperformer', 'Market Expectation - Moderate Outperformer'),
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(above field average', 'Market Expectation - Above Average'),
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(worst market performer', 'Market Expectation - Worst in Field'),
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(chronic underperformer', 'Market Expectation - Chronic Underperformer'),
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(significant underperformer', 'Market Expectation - Significant Underperformer'),
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(mild underperformer', 'Market Expectation - Mild Underperformer'),
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(below field average', 'Market Expectation - Below Average'),
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(meeting expectations', 'Market Expectation - Neutral'),
        # FIX: "near field average" maps to Neutral (it's not a named bucket in old patterns)
        (r'[+-][\d.]+\s*:\s*A/E=[\d.]+\s*\(near field average', 'Market Expectation - Neutral'),

    ]

    for pattern, name in patterns:
        match = re.search(pattern, notes, re.IGNORECASE | re.DOTALL)
        if match:
            # ---- Dynamic handlers ----
            if name == '_form_price_dynamic':
                try:
                    price = float(match.group(1))
                    if price <= 2.0:
                        components['Form Price - Very Short ($1-$2)'] = price
                    elif price <= 5.0:
                        components['Form Price - Short ($2-$5)'] = price
                    elif price <= 13.0:
                        components['Form Price - Backed ($5-$13)'] = price
                    elif price <= 14.5:
                        components['Form Price - Slight Value ($12-$14)'] = price
                    else:
                        components['Form Price - Outsider ($15+)'] = price
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_sire_dynamic':
                try:
                    roi = float(match.group(1))
                    if roi >= 50:
                        components['Sire - Elite ROI (50%+)'] = roi
                    elif roi >= 20:
                        components['Sire - Strong ROI (20-50%)'] = roi
                    elif roi >= 0:
                        components['Sire - Positive ROI (0-20%)'] = roi
                    else:
                        components['Sire - Negative ROI'] = roi
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_country_dynamic':
                try:
                    country = match.group(1).strip()
                    roi = float(match.group(2))
                    components[f'Country: {country}'] = roi
                except (ValueError, IndexError):
                    pass
                continue
            if name == '_track_score_dynamic':
                try:
                    val = float(match.group(1))
                    if val >= 8:
                        components['Track Score Total - Strong (8+)'] = val
                    elif val >= 4:
                        components['Track Score Total - Moderate (4-7)'] = val
                    else:
                        components['Track Score Total - Low (0-3)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_td_score_dynamic':
                try:
                    val = float(match.group(1))
                    if val >= 8:
                        components['Track+Distance Score Total - Strong (8+)'] = val
                    elif val >= 4:
                        components['Track+Distance Score Total - Moderate (4-7)'] = val
                    else:
                        components['Track+Distance Score Total - Low (0-3)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_dist_score_dynamic':
                try:
                    val = float(match.group(1))
                    if val >= 8:
                        components['Distance Score Total - Strong (8+)'] = val
                    elif val >= 4:
                        components['Distance Score Total - Moderate (4-7)'] = val
                    else:
                        components['Distance Score Total - Low (0-3)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_cond_score_dynamic':
                try:
                    val = float(match.group(1))
                    if val >= 8:
                        components['Track Condition Score Total - Strong (8+)'] = val
                    elif val >= 4:
                        components['Track Condition Score Total - Moderate (4-7)'] = val
                    else:
                        components['Track Condition Score Total - Low (0-3)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_ran_places_dynamic':
                try:
                    val = float(match.group(1).replace(' ', '').replace('+', ''))
                    if val >= 8:
                        components['Ran Places - Strong (8+)'] = val
                    elif val >= 3:
                        components['Ran Places - Moderate (3-7)'] = val
                    else:
                        components['Ran Places - Low (0-2)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_class_drop_dynamic':
                try:
                    val = float(match.group(1))
                    if val >= 10:
                        components['Class Drop - Large (10+)'] = val
                    else:
                        components['Class Drop - Small (0-9)'] = val
                except (ValueError, IndexError):
                    pass
                continue

            if name == '_class_rise_dynamic':
                try:
                    val = float(match.group(1))
                    if val <= -10:
                        components['Class Rise - Large (10+)'] = val
                    else:
                        components['Class Rise - Small (0-9)'] = val
                except (ValueError, IndexError):
                    pass
                continue
            # ---- Standard score extraction ----
            try:
                score_str = match.group(1).replace(' ', '').replace('+', '')
                score = float(score_str)
            except (IndexError, ValueError):
                score = 1.0
            components[name] = score

    return components


def aggregate_component_stats(all_results_data, stake=10.0):
    """
    Aggregate component statistics across all results.
    Returns dict of component_name -> {appearances, wins, places, total_score, avg_score, strike_rate, place_rate, roi}
    """
    component_stats = {}
    
    for entry in all_results_data:
        prediction = entry['prediction']
        result = entry['result']
        horse = entry['horse']
        
        if not prediction or not result:
            continue
        
        notes = prediction.notes or ''
        components = parse_notes_components(notes)
        won = result.finish_position == 1
        placed = result.finish_position in [1, 2, 3]
        sp = result.sp or 0
        
        profit = (sp * stake - stake) if won else -stake
        
        if horse.csv_data:
            csv = horse.csv_data

            # ====== AGE/SEX DEMOGRAPHICS ======
            horse_age = csv.get('horse age')
            horse_sex = csv.get('horse sex')
            if horse_age and horse_sex:
                components[f"{horse_age}yo {horse_sex}"] = 1.0

            # ====== COUNTRY OF ORIGIN ======
            country = csv.get('country', '').strip()
            if country:
                components[f"Country: {country}"] = 1.0

            # ====== FORM POSITION (LAST START FINISH) ======
            form_pos_raw = csv.get('form position', '')
            try:
                form_pos = int(str(form_pos_raw).strip())
                if form_pos == 1:
                    components['Last Finish: 1st'] = 1.0
                elif form_pos <= 3:
                    components['Last Finish: 2nd-3rd'] = 1.0
                elif form_pos <= 6:
                    components['Last Finish: 4th-6th'] = 1.0
                elif form_pos <= 10:
                    components['Last Finish: 7th-10th'] = 1.0
                else:
                    components['Last Finish: 11th+'] = 1.0
            except (ValueError, TypeError):
                components['Last Finish: No Recent Form'] = 1.0

            # ====== FORM DISTANCE (LAST START vs TODAY) ======
            try:
                today_dist = int(str(csv.get('distance', '') or '').replace('m', '').strip())
                form_dist = int(str(csv.get('form distance', '') or '').replace('m', '').strip())
                dist_delta = today_dist - form_dist  # positive = stepping up

                if abs(dist_delta) <= 200:
                    components['Distance Last Start: Same (±200m)'] = 1.0
                elif dist_delta > 400:
                    components['Distance Last Start: Stepped Up 400m+'] = 1.0
                elif dist_delta > 200:
                    components['Distance Last Start: Stepped Up 200-400m'] = 1.0
                elif dist_delta < -400:
                    components['Distance Last Start: Dropped 400m+'] = 1.0
                else:
                    components['Distance Last Start: Dropped 200-400m'] = 1.0
            except (ValueError, TypeError):
                pass  # Skip if distance data missing

            # ====== WEIGHT CHANGE (LAST START vs TODAY) ======
            try:
                today_weight = float(str(csv.get('horse weight', '') or '').strip())
                form_weight = float(str(csv.get('form weight', '') or '').strip())

                # Validate sensible weight range
                if 49 <= today_weight <= 65 and 49 <= form_weight <= 65:
                    weight_delta = form_weight - today_weight  # positive = dropped weight

                    if weight_delta >= 5:
                        components['Weight Change: Dropped 5kg+'] = 1.0
                    elif weight_delta >= 3:
                        components['Weight Change: Dropped 3-5kg'] = 1.0
                    elif weight_delta >= 1:
                        components['Weight Change: Dropped 1-3kg'] = 1.0
                    elif weight_delta > -1:
                        components['Weight Change: Similar (±1kg)'] = 1.0
                    elif weight_delta > -3:
                        components['Weight Change: Up 1-3kg'] = 1.0
                    elif weight_delta > -5:
                        components['Weight Change: Up 3-5kg'] = 1.0
                    else:
                        components['Weight Change: Up 5kg+'] = 1.0
            except (ValueError, TypeError):
                pass  # Skip if weight data missing

            # ====== APPRENTICE CLAIM ======
            try:
                claim = float(str(csv.get('horse claim', '0') or '0').strip())
                if claim >= 3:
                    components['Apprentice Claim: 3kg+'] = 1.0
                elif claim >= 1:
                    components['Apprentice Claim: 1-2kg'] = 1.0
            except (ValueError, TypeError):
                pass  # No claim, skip
            # ====== WEIGHT TYPE ======
            weight_type = csv.get('weight type', '').strip()
            if weight_type:
                components[f"Weight Type: {weight_type}"] = 1.0

            # ====== SEX RESTRICTIONS ======
            sex_restrictions = csv.get('sex restrictions', '').strip()
            if sex_restrictions:
                components[f"Sex Restrictions: {sex_restrictions}"] = 1.0

            # ====== AGE RESTRICTIONS ======
            age_restrictions = csv.get('age restrictions', '').strip()
            if age_restrictions:
                components[f"Age Restrictions: {age_restrictions}"] = 1.0

            # ====== JOCKEYS CAN CLAIM ======
            can_claim = csv.get('jockeys can claim', '').strip()
            if can_claim:
                components[f"Jockeys Can Claim: {can_claim}"] = 1.0

            # ====== WEIGHT RESTRICTIONS ======
            weight_restrictions = csv.get('weight restrictions', '').strip()
            if weight_restrictions:
                components[f"Weight Restrictions: {weight_restrictions}"] = 1.0

            # ====== FORM MARGIN ======
            try:
                form_pos_check = str(csv.get('form position', '') or '').strip()
                form_margin = float(str(csv.get('form margin', '') or '').strip())
                if form_pos_check == '1':
                    if form_margin <= 0.5:
                        components['Last Start Margin: Won Short Head'] = 1.0
                    elif form_margin <= 2.0:
                        components['Last Start Margin: Won ≤2L'] = 1.0
                    elif form_margin <= 5.0:
                        components['Last Start Margin: Won 2-5L'] = 1.0
                    else:
                        components['Last Start Margin: Won 5L+'] = 1.0
                else:
                    if form_margin <= 0.5:
                        components['Last Start Margin: Beaten ≤0.5L'] = 1.0
                    elif form_margin <= 1.0:
                        components['Last Start Margin: Beaten 0.5-1L'] = 1.0
                    elif form_margin <= 2.0:
                        components['Last Start Margin: Beaten 1-2L'] = 1.0
                    elif form_margin <= 4.0:
                        components['Last Start Margin: Beaten 2-4L'] = 1.0
                    elif form_margin <= 8.0:
                        components['Last Start Margin: Beaten 4-8L'] = 1.0
                    else:
                        components['Last Start Margin: Beaten 8L+'] = 1.0
            except (ValueError, TypeError):
                pass

            # ====== FORM MEETING DATE (DAYS SINCE LAST RUN) ======
            try:
                from datetime import datetime as _dt
                form_date_raw = csv.get('form meeting date', '').strip()
                meeting_date_raw = csv.get('meeting date', '').strip()
                if form_date_raw and meeting_date_raw:
                    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y', '%d %b %Y'):
                        try:
                            form_date = _dt.strptime(form_date_raw, fmt)
                            meeting_date = _dt.strptime(meeting_date_raw, fmt)
                            days_since = (meeting_date - form_date).days
                            if days_since <= 7:
                                components['Days Since Run (Raw): ≤7'] = 1.0
                            elif days_since <= 14:
                                components['Days Since Run (Raw): 8-14'] = 1.0
                            elif days_since <= 21:
                                components['Days Since Run (Raw): 15-21'] = 1.0
                            elif days_since <= 28:
                                components['Days Since Run (Raw): 22-28'] = 1.0
                            elif days_since <= 60:
                                components['Days Since Run (Raw): 29-60'] = 1.0
                            elif days_since <= 120:
                                components['Days Since Run (Raw): 61-120'] = 1.0
                            elif days_since <= 200:
                                components['Days Since Run (Raw): 121-200'] = 1.0
                            else:
                                components['Days Since Run (Raw): 200+'] = 1.0
                            break
                        except ValueError:
                            continue
            except Exception:
                pass

            # ====== FORM OTHER RUNNERS ======
            try:
                form_runners = int(str(csv.get('form other runners', '') or '').strip())
                if form_runners >= 1:
                    if form_runners <= 7:
                        components['Last Start Field Size: Small (≤7)'] = 1.0
                    elif form_runners <= 11:
                        components['Last Start Field Size: Medium (8-11)'] = 1.0
                    elif form_runners <= 15:
                        components['Last Start Field Size: Large (12-15)'] = 1.0
                    else:
                        components['Last Start Field Size: Very Large (16+)'] = 1.0
            except (ValueError, TypeError):
                pass

            # ====== FORM PRICE ======
            try:
                form_price = float(str(csv.get('form price', '') or '').strip())
                if form_price > 0:
                    if form_price <= 2.0:
                        components['Last Start SP: ≤$2 Fav'] = 1.0
                    elif form_price <= 4.0:
                        components['Last Start SP: $2-$4'] = 1.0
                    elif form_price <= 8.0:
                        components['Last Start SP: $4-$8'] = 1.0
                    elif form_price <= 15.0:
                        components['Last Start SP: $8-$15'] = 1.0
                    elif form_price <= 30.0:
                        components['Last Start SP: $15-$30'] = 1.0
                    else:
                        components['Last Start SP: $30+'] = 1.0
            except (ValueError, TypeError):
                pass

            # ====== FORM TIME ======
            form_time = csv.get('form time', '').strip()
            if form_time:
                try:
                    form_time_val = float(form_time)
                    if form_time_val > 0:
                        components['Has Last Start Time'] = 1.0
                except (ValueError, TypeError):
                    pass

            # ====== HORSE RECORD (CAREER) ======
            horse_record = csv.get('horse record', '').strip()
            if horse_record:
                try:
                    # Format: "starts:wins-seconds-thirds" e.g. "20:5-3-2"
                    parts = horse_record.replace('-', ':').split(':')
                    if len(parts) >= 4:
                        starts = int(parts[0])
                        wins = int(parts[1])
                        if starts > 0:
                            career_sr = wins / starts * 100
                            if career_sr >= 40:
                                components['Career Record: Elite (40%+ SR)'] = 1.0
                            elif career_sr >= 25:
                                components['Career Record: Strong (25-40% SR)'] = 1.0
                            elif career_sr >= 15:
                                components['Career Record: Moderate (15-25% SR)'] = 1.0
                            elif career_sr >= 5:
                                components['Career Record: Low (5-15% SR)'] = 1.0
                            else:
                                components['Career Record: Poor (<5% SR)'] = 1.0
                except (ValueError, TypeError, IndexError):
                    pass

            # ====== HORSE RECORD DISTANCE ======
            record_dist = csv.get('horse record distance', '').strip()
            if record_dist:
                try:
                    parts = record_dist.replace('-', ':').split(':')
                    if len(parts) >= 2:
                        starts = int(parts[0])
                        wins = int(parts[1])
                        if starts == 0:
                            components['Distance Record: No Runs'] = 1.0
                        else:
                            sr = wins / starts * 100
                            if sr >= 33:
                                components['Distance Record: Strong (33%+)'] = 1.0
                            elif sr >= 15:
                                components['Distance Record: Moderate (15-33%)'] = 1.0
                            elif sr > 0:
                                components['Distance Record: Low (<15%)'] = 1.0
                            else:
                                components['Distance Record: No Wins'] = 1.0
                except (ValueError, TypeError, IndexError):
                    pass
            # ====== HORSE RECORD TRACK ======
            record_track = csv.get('horse record track', '').strip()
            if record_track:
                try:
                    parts = record_track.replace('-', ':').split(':')
                    if len(parts) >= 2:
                        starts = int(parts[0])
                        wins = int(parts[1])
                        if starts > 0:
                            sr = wins / starts * 100
                            if sr >= 33:
                                components['Track Record: Strong (33%+)'] = 1.0
                            elif sr >= 15:
                                components['Track Record: Moderate (15-33%)'] = 1.0
                            elif sr > 0:
                                components['Track Record: Low (<15%)'] = 1.0
                            else:
                                components['Track Record: No Wins'] = 1.0
                        else:
                            components['Track Record: No Runs'] = 1.0
                except (ValueError, TypeError, IndexError):
                    pass

            # ====== HORSE RECORD TRACK+DISTANCE ======
            record_td = csv.get('horse record track distance', '').strip()
            if record_td:
                try:
                    parts = record_td.replace('-', ':').split(':')
                    if len(parts) >= 2:
                        starts = int(parts[0])
                        wins = int(parts[1])
                        if starts > 0:
                            sr = wins / starts * 100
                            if sr >= 33:
                                components['Track+Dist Record: Strong (33%+)'] = 1.0
                            elif sr >= 15:
                                components['Track+Dist Record: Moderate (15-33%)'] = 1.0
                            elif sr > 0:
                                components['Track+Dist Record: Low (<15%)'] = 1.0
                            else:
                                components['Track+Dist Record: No Wins'] = 1.0
                        else:
                            components['Track+Dist Record: No Runs'] = 1.0
                except (ValueError, TypeError, IndexError):
                    pass

            # ====== GOING RECORDS ======
            for going, label in [
                ('horse record firm',    'Firm'),
                ('horse record good',    'Good'),
                ('horse record soft',    'Soft'),
                ('horse record heavy',   'Heavy'),
                ('horse record synthetic','Synthetic'),
            ]:
                rec = csv.get(going, '').strip()
                if rec:
                    try:
                        parts = rec.replace('-', ':').split(':')
                        if len(parts) >= 2:
                            starts = int(parts[0])
                            wins = int(parts[1])
                            if starts > 0:
                                sr = wins / starts * 100
                                if sr >= 33:
                                    components[f"{label} Record: Strong (33%+)"] = 1.0
                                elif sr >= 15:
                                    components[f"{label} Record: Moderate (15-33%)"] = 1.0
                                elif sr > 0:
                                    components[f"{label} Record: Low (<15%)"] = 1.0
                                else:
                                    components[f"{label} Record: No Wins"] = 1.0
                            else:
                                components[f"{label} Record: No Runs"] = 1.0
                    except (ValueError, TypeError, IndexError):
                        pass

            # ====== HORSE RECORD JUMPS ======
            record_jumps = csv.get('horse record jumps', '').strip()
            if record_jumps:
                try:
                    parts = record_jumps.replace('-', ':').split(':')
                    starts = int(parts[0])
                    if starts > 0:
                        components['Has Jumps Experience'] = 1.0
                except (ValueError, TypeError, IndexError):
                    pass

            # ====== HORSE RECORD FIRST UP ======
            record_fu = csv.get('horse record first up', '').strip()
            if record_fu:
                try:
                    parts = record_fu.replace('-', ':').split(':')
                    if len(parts) >= 2:
                        starts = int(parts[0])
                        wins = int(parts[1])
                        if starts > 0:
                            sr = wins / starts * 100
                            if sr >= 33:
                                components['First Up Record: Strong (33%+)'] = 1.0
                            elif sr >= 15:
                                components['First Up Record: Moderate (15-33%)'] = 1.0
                            elif sr > 0:
                                components['First Up Record: Low (<15%)'] = 1.0
                            else:
                                components['First Up Record: No Wins'] = 1.0
                        else:
                            components['First Up Record: No Runs'] = 1.0
                except (ValueError, TypeError, IndexError):
                    pass

            # ====== HORSE RECORD SECOND UP ======
            record_su = csv.get('horse record second up', '').strip()
            if record_su:
                try:
                    parts = record_su.replace('-', ':').split(':')
                    if len(parts) >= 2:
                        starts = int(parts[0])
                        wins = int(parts[1])
                        if starts > 0:
                            sr = wins / starts * 100
                            if sr >= 33:
                                components['Second Up Record: Strong (33%+)'] = 1.0
                            elif sr >= 15:
                                components['Second Up Record: Moderate (15-33%)'] = 1.0
                            elif sr > 0:
                                components['Second Up Record: Low (<15%)'] = 1.0
                            else:
                                components['Second Up Record: No Wins'] = 1.0
                        else:
                            components['Second Up Record: No Runs'] = 1.0
                except (ValueError, TypeError, IndexError):
                    pass

            # ====== HORSE LAST 10 FORM STRING ======
            last10 = csv.get('horse last10', '').strip()
            if last10:
                wins_in_10 = last10.count('1')
                places_in_10 = last10.count('2') + last10.count('3')
                if wins_in_10 >= 4:
                    components['Last 10: 4+ Wins'] = 1.0
                elif wins_in_10 >= 2:
                    components['Last 10: 2-3 Wins'] = 1.0
                elif wins_in_10 == 1:
                    components['Last 10: 1 Win'] = 1.0
                else:
                    components['Last 10: No Recent Wins'] = 1.0
                if places_in_10 >= 5:
                    components['Last 10: 5+ Places'] = 1.0
                elif places_in_10 >= 3:
                    components['Last 10: 3-4 Places'] = 1.0

            # ====== SECTIONAL ======
            sectional = csv.get('sectional', '').strip()
            if sectional:
                try:
                    sec_val = float(sectional)
                    if sec_val > 0:
                        components['Has Sectional Data'] = 1.0
                except (ValueError, TypeError):
                    pass

            # ====== IDs (existence checks only — no ROI signal expected) ======
            for id_field, label in [
                ('meeting id',    'Has Meeting ID'),
                ('race id',       'Has Race ID'),
                ('horse id',      'Has Horse ID'),
                ('horse trainer id', 'Has Trainer ID'),
                ('horse jockey id',  'Has Jockey ID'),
                ('form trainer id',  'Has Form Trainer ID'),
                ('form jockey id',   'Has Form Jockey ID'),
            ]:
                if csv.get(id_field, ''):
                    components[label] = 1.0

            # ====== START TIME ======
            start_time = csv.get('start time', '').strip()
            if start_time:
                try:
                    hour = int(start_time.split(':')[0])
                    if hour < 12:
                        components['Race Time: Morning'] = 1.0
                    elif hour < 14:
                        components['Race Time: Early Afternoon'] = 1.0
                    elif hour < 16:
                        components['Race Time: Mid Afternoon'] = 1.0
                    else:
                        components['Race Time: Late Afternoon'] = 1.0
                except (ValueError, TypeError, IndexError):
                    pass

            

            # ====== MEETING DATE ======
            meeting_date_raw = csv.get('meeting date', '').strip()
            if meeting_date_raw:
                try:
                    from datetime import datetime as _dt2
                    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
                        try:
                            md = _dt2.strptime(meeting_date_raw, fmt)
                            components[f"Meeting Day: {md.strftime('%A')}"] = 1.0
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass
        for component_name, score_value in components.items():
            if component_name not in component_stats:
                component_stats[component_name] = {
                    'appearances': 0,
                    'wins': 0,
                    'places': 0,
                    'total_score': 0,
                    'total_profit': 0,
                    'scores': []
                }
            
            stats = component_stats[component_name]
            stats['appearances'] += 1
            if won:
                stats['wins'] += 1
            if placed:
                stats['places'] += 1
            stats['total_score'] += score_value
            stats['total_profit'] += profit
            stats['scores'].append(score_value)
    
    # Calculate averages and rates
    for name, stats in component_stats.items():
        stats['avg_score'] = stats['total_score'] / stats['appearances'] if stats['appearances'] > 0 else 0
        stats['strike_rate'] = (stats['wins'] / stats['appearances'] * 100) if stats['appearances'] > 0 else 0
        stats['place_rate'] = (stats['places'] / stats['appearances'] * 100) if stats['appearances'] > 0 else 0
        stats['roi'] = (stats['total_profit'] / (stats['appearances'] * stake) * 100) if stats['appearances'] > 0 else 0
    
    return component_stats

def analyze_external_factors(all_results_data, races_data, stake=10.0):
    """
    Analyze external factors: jockeys, trainers, barriers, distances, tracks
    Returns dict with stats for each factor
    
    FIXED: Barriers, Distances, Track Conditions now count RACES (top picks only)
    just like Tracks do, instead of counting every horse.
    """
    
    jockeys = {}
    trainers = {}
    barriers = {'1-3': {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0},
                '4-6': {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0},
                '7-9': {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0},
                '10+': {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0}}
    distances = {'Sprint (≤1200m)': {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0},
                 'Short (1300-1500m)': {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0},
                 'Mile (1550-1700m)': {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0},
                 'Middle (1800-2200m)': {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0},
                 'Staying (2400m+)': {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0}}
    tracks = {}
    track_conditions = {}
    
    # Process jockeys and trainers (all horses that we picked)
    for entry in all_results_data:
        horse = entry['horse']
        result = entry['result']
        
        if not result:
            continue
        
        won = result.finish_position == 1
        placed = result.finish_position in [1, 2, 3]
        sp = result.sp or 0
        profit = (sp * stake - stake) if won else -stake
        
        # Get CSV data
        csv_data = horse.csv_data or {}
        
        # Jockey
        jockey = csv_data.get('horse jockey', '').strip()
        if jockey:
            if jockey not in jockeys:
                jockeys[jockey] = {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0}
            jockeys[jockey]['runs'] += 1
            if won:
                jockeys[jockey]['wins'] += 1
            if placed:
                jockeys[jockey]['places'] += 1
            jockeys[jockey]['profit'] += profit
        
        # Trainer
        trainer = csv_data.get('horse trainer', '').strip()
        if trainer:
            if trainer not in trainers:
                trainers[trainer] = {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0}
            trainers[trainer]['runs'] += 1
            if won:
                trainers[trainer]['wins'] += 1
            if placed:
                trainers[trainer]['places'] += 1
            trainers[trainer]['profit'] += profit

    # Sires Analysis (30+ appearances)
    sire_stats = {}
    for entry in all_results_data:
        horse = entry['horse']
        result = entry['result']
        
        if not result:
            continue
        
        won = result.finish_position == 1
        placed = result.finish_position in [1, 2, 3]
        sp = result.sp or 0
        profit = (sp * stake - stake) if won else -stake
        
        # Get CSV data
        csv_data = horse.csv_data or {}
        
        # Sire
        sire = csv_data.get('horse sire', '').strip()
        if sire:
            if sire not in sire_stats:
                sire_stats[sire] = {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0}
            sire_stats[sire]['runs'] += 1
            if won:
                sire_stats[sire]['wins'] += 1
            if placed:
                sire_stats[sire]['places'] += 1
            sire_stats[sire]['profit'] += profit

    # Dams Analysis (30+ appearances)
    dam_stats = {}
    for entry in all_results_data:
        horse = entry['horse']
        result = entry['result']
        
        if not result:
            continue
        
        won = result.finish_position == 1
        placed = result.finish_position in [1, 2, 3]
        sp = result.sp or 0
        profit = (sp * stake - stake) if won else -stake
        
        # Get CSV data
        csv_data = horse.csv_data or {}
        
        # Dam
        dam = csv_data.get('horse dam', '').strip()
        if dam:
            if dam not in dam_stats:
                dam_stats[dam] = {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0}
            dam_stats[dam]['runs'] += 1
            if won:
                dam_stats[dam]['wins'] += 1
            if placed:
                dam_stats[dam]['places'] += 1
            dam_stats[dam]['profit'] += profit
    
    # Process barriers, distances, track conditions, and tracks (TOP PICKS ONLY - by race)
    for race_key, horses in races_data.items():
        if not horses:
            continue
        
        horses_sorted = sorted(horses, key=lambda x: x['prediction'].score, reverse=True)
        top_pick = horses_sorted[0]
        
        result = top_pick['result']
        horse = top_pick['horse']
        race = top_pick['race']
        meeting = top_pick['meeting']
        
        won = result.finish_position == 1
        placed = result.finish_position in [1, 2, 3]
        sp = result.sp or 0
        profit = (sp * stake - stake) if won else -stake
        
        csv_data = horse.csv_data or {}
        
        # Barrier (top pick only)
        try:
            barrier = int(csv_data.get('horse barrier', 0))
            if barrier >= 1:
                if barrier <= 3:
                    bucket = '1-3'
                elif barrier <= 6:
                    bucket = '4-6'
                elif barrier <= 9:
                    bucket = '7-9'
                else:
                    bucket = '10+'
                barriers[bucket]['runs'] += 1
                if won:
                    barriers[bucket]['wins'] += 1
                if placed:
                    barriers[bucket]['places'] += 1
                barriers[bucket]['profit'] += profit
        except (ValueError, TypeError):
            pass
        
        # Distance (top pick only)
        try:
            dist = int(csv_data.get('distance', 0))
            if dist > 0:
                if dist <= 1200:
                    bucket = 'Sprint (≤1200m)'
                elif dist <= 1500:
                    bucket = 'Short (1300-1500m)'
                elif dist <= 1700:
                    bucket = 'Mile (1550-1700m)'
                elif dist <= 2200:
                    bucket = 'Middle (1800-2200m)'
                else:
                    bucket = 'Staying (2400m+)'
                distances[bucket]['runs'] += 1
                if won:
                    distances[bucket]['wins'] += 1
                if placed:
                    distances[bucket]['places'] += 1
                distances[bucket]['profit'] += profit
        except (ValueError, TypeError):
            pass
        
        # Track Condition (top pick only)
        condition = race.track_condition or 'Unknown'
        if condition:
            if condition not in track_conditions:
                track_conditions[condition] = {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0}
            track_conditions[condition]['runs'] += 1
            if won:
                track_conditions[condition]['wins'] += 1
            if placed:
                track_conditions[condition]['places'] += 1
            track_conditions[condition]['profit'] += profit
        
        # Track (top pick only)
        meeting_name = meeting.meeting_name or ''
        if '_' in meeting_name:
            track = meeting_name.split('_')[1]
        else:
            track = meeting_name
        
        if track:
            if track not in tracks:
                tracks[track] = {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0}
            tracks[track]['runs'] += 1
            if won:
                tracks[track]['wins'] += 1
            if placed:
                tracks[track]['places'] += 1
            tracks[track]['profit'] += profit
    
    # Calculate rates for all categories
    def calc_rates(data_dict, stake):
        for key, stats in data_dict.items():
            if stats['runs'] > 0:
                stats['strike_rate'] = (stats['wins'] / stats['runs']) * 100
                stats['place_rate'] = (stats['places'] / stats['runs']) * 100
                stats['roi'] = (stats['profit'] / (stats['runs'] * stake)) * 100
            else:
                stats['strike_rate'] = 0
                stats['place_rate'] = 0
                stats['roi'] = 0
        return data_dict
    
    jockeys = calc_rates(jockeys, stake)
    trainers = calc_rates(trainers, stake)
    barriers = calc_rates(barriers, stake)
    distances = calc_rates(distances, stake)
    tracks = calc_rates(tracks, stake)
    track_conditions = calc_rates(track_conditions, stake)
    sire_stats = calc_rates(sire_stats, stake)
    dam_stats = calc_rates(dam_stats, stake)
    
    # Split jockeys into reliable (100+ runs)
    jockeys_reliable = {k: v for k, v in jockeys.items() if v['runs'] >= 100}
    jockeys_limited = {k: v for k, v in jockeys.items() if 3 <= v['runs'] < 100}

    # Sort by ROI
    jockeys_reliable = dict(sorted(jockeys_reliable.items(), key=lambda x: x[1]['roi'], reverse=True))
    jockeys_limited = dict(sorted(jockeys_limited.items(), key=lambda x: x[1]['roi'], reverse=True))

    # Split trainers into reliable (100+ runs)
    trainers_reliable = {k: v for k, v in trainers.items() if v['runs'] >= 100}
    trainers_limited = {k: v for k, v in trainers.items() if 5 <= v['runs'] < 100}

    # Sort by ROI
    trainers_reliable = dict(sorted(trainers_reliable.items(), key=lambda x: x[1]['roi'], reverse=True))
    trainers_limited = dict(sorted(trainers_limited.items(), key=lambda x: x[1]['roi'], reverse=True))

    # Filter and sort sires (100+ runs only)
    sires_reliable = {k: v for k, v in sire_stats.items() if v['runs'] >= 100}
    sires_reliable = dict(sorted(sires_reliable.items(), key=lambda x: x[1]['roi'], reverse=True))

    # Filter and sort dams (100+ runs only)
    dams_reliable = {k: v for k, v in dam_stats.items() if v['runs'] >= 100}
    dams_reliable = dict(sorted(dams_reliable.items(), key=lambda x: x[1]['roi'], reverse=True))
    print(f"DEBUG: Found {len(dams_reliable)} dams with 100+ runs")
    
    # Filter tracks with 2+ races
    tracks = {k: v for k, v in tracks.items() if v['runs'] >= 2}
    tracks = dict(sorted(tracks.items(), key=lambda x: x[1]['strike_rate'], reverse=True))
    
    return {
        'jockeys_reliable': jockeys_reliable,
        'jockeys_limited': jockeys_limited,
        'trainers_reliable': trainers_reliable,
        'trainers_limited': trainers_limited,
        'sires_reliable': sires_reliable,
        'dams_reliable': dams_reliable,
        'barriers': barriers,
        'distances': distances,
        'tracks': tracks,
        'track_conditions': track_conditions
    }
    
def analyze_race_classes(races_data, stake=10.0):
    """
    Analyze performance by race class (top picks only)
    Returns dict with stats for each class
    """
    class_stats = {}
    
    for race_key, horses in races_data.items():
        if not horses:
            continue
        
        # Sort by score to get top pick
        horses_sorted = sorted(horses, key=lambda x: x['prediction'].score, reverse=True)
        top_pick = horses_sorted[0]
        
        result = top_pick['result']
        race = top_pick['race']
        
        # Get race class
        race_class = race.race_class or 'Unknown'
        
        won = result.finish_position == 1
        placed = result.finish_position in [1, 2, 3]
        sp = result.sp or 0
        profit = (sp * stake - stake) if won else -stake
        
        # Initialize class if not seen before
        if race_class not in class_stats:
            class_stats[race_class] = {
                'runs': 0,
                'wins': 0,
                'places': 0,
                'profit': 0
            }
        
        # Update stats
        class_stats[race_class]['runs'] += 1
        if won:
            class_stats[race_class]['wins'] += 1
        if placed:
            class_stats[race_class]['places'] += 1
        class_stats[race_class]['profit'] += profit
    
    # Calculate rates
    for race_class, stats in class_stats.items():
        stats['strike_rate'] = (stats['wins'] / stats['runs'] * 100) if stats['runs'] > 0 else 0
        stats['place_rate'] = (stats['places'] / stats['runs'] * 100) if stats['runs'] > 0 else 0
        stats['roi'] = (stats['profit'] / (stats['runs'] * stake) * 100) if stats['runs'] > 0 else 0
    
    # Sort by ROI descending
    class_stats = dict(sorted(class_stats.items(), key=lambda x: x[1]['roi'], reverse=True))
    
    return class_stats
    
def analyze_class_drops(stake=10.0):
    """
    Analyze performance by class drop magnitude (ALL horses with results)
    Returns dict with stats for each class drop range
    """
    class_drop_stats = {}
    
    # Initialize all buckets from 100+ down to 0, in 10-point increments
    # Also include class rises
    buckets = ['100+', '90-99', '80-89', '70-79', '60-69', '50-59', '40-49', '30-39', 
               '20-29', '10-19', '0-9', 'Same Class', 'Rise 0-9', 'Rise 10-19', 
               'Rise 20-29', 'Rise 30+']
    
    for bucket in buckets:
        class_drop_stats[bucket] = {
            'runs': 0,
            'wins': 0,
            'places': 0,
            'profit': 0
        }
    
    # Query ALL horses with predictions AND results (regardless of if you backed them)
    all_horses = db.session.query(Horse, Prediction, Result).join(
        Prediction, Horse.id == Prediction.horse_id
    ).join(
        Result, Horse.id == Result.horse_id
    ).filter(
        Result.finish_position > 0
    ).all()
    
    for horse, prediction, result in all_horses:
        won = result.finish_position == 1
        placed = result.finish_position in [1, 2, 3]
        sp = result.sp or 0
        profit = (sp * stake - stake) if won else -stake
        
        # Parse class drop from notes
        notes = prediction.notes or ''
        class_points = None
        direction = None
        
        if 'Stepping DOWN' in notes or 'Stepping UP' in notes:
            import re
            match = re.search(r'Stepping (DOWN|UP) ([\d.]+) class points', notes)
            if match:
                direction = match.group(1)
                class_points = float(match.group(2))
        
        # Determine bucket
        if class_points is None:
            bucket = 'Same Class'
        elif direction == 'DOWN':
            if class_points >= 100:
                bucket = '100+'
            elif class_points >= 90:
                bucket = '90-99'
            elif class_points >= 80:
                bucket = '80-89'
            elif class_points >= 70:
                bucket = '70-79'
            elif class_points >= 60:
                bucket = '60-69'
            elif class_points >= 50:
                bucket = '50-59'
            elif class_points >= 40:
                bucket = '40-49'
            elif class_points >= 30:
                bucket = '30-39'
            elif class_points >= 20:
                bucket = '20-29'
            elif class_points >= 10:
                bucket = '10-19'
            else:
                bucket = '0-9'
        else:  # UP
            if class_points >= 30:
                bucket = 'Rise 30+'
            elif class_points >= 20:
                bucket = 'Rise 20-29'
            elif class_points >= 10:
                bucket = 'Rise 10-19'
            else:
                bucket = 'Rise 0-9'
        
        # Update stats
        class_drop_stats[bucket]['runs'] += 1
        if won:
            class_drop_stats[bucket]['wins'] += 1
        if placed:
            class_drop_stats[bucket]['places'] += 1
        class_drop_stats[bucket]['profit'] += profit
    
    # Calculate rates (OUTSIDE the loop - only run once after all horses processed)
    for bucket, stats in class_drop_stats.items():
        if stats['runs'] > 0:
            stats['strike_rate'] = (stats['wins'] / stats['runs']) * 100
            stats['place_rate'] = (stats['places'] / stats['runs']) * 100
            stats['roi'] = (stats['profit'] / (stats['runs'] * stake)) * 100
        else:
            stats['strike_rate'] = 0
            stats['place_rate'] = 0
            stats['roi'] = 0
    
    # Filter out buckets with no data
    class_drop_stats = {k: v for k, v in class_drop_stats.items() if v['runs'] > 0}

    del all_horses
    import gc
    gc.collect()

    return class_drop_stats
    
@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for("history"))
    # Get all recent meetings (shared across all users)
    recent_meetings = Meeting.query\
        .order_by(Meeting.uploaded_at.desc())\
        .limit(5)\
        .all()
    return render_template("dashboard.html", recent_meetings=recent_meetings)
# ----- PuntingForm API Routes -----

@app.route("/api/meetings/today")
@login_required
def api_get_todays_meetings():
    """Get list of today's meetings from PuntingForm API"""
    try:
        meetings_data = pf_service.get_meetings_list()
        
        return jsonify({
            'success': True, 
            'meetings': meetings_data['meetings']
        })
    except Exception as e:
        logger.error(f"Failed to fetch meetings: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route("/api/meetings/date/<date_str>")
@login_required
def api_get_meetings_by_date(date_str):
    """Get list of meetings for a specific date from PuntingForm API"""
    try:
        # Validate date format
        try:
            datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD'}), 400
        
        meetings_data = pf_service.get_meetings_list(date=date_str)
        
        return jsonify({
            'success': True, 
            'meetings': meetings_data['meetings']
        })
    except Exception as e:
        logger.error(f"Failed to fetch meetings for {date_str}: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route("/api/meetings/<meeting_id>/speedmaps/<int:race_number>")
@login_required
def api_get_speedmaps(meeting_id, race_number):
    """Get speed maps for a specific race"""
    try:
        url = f"https://api.puntingform.com.au/v2/User/Speedmaps?meetingId={meeting_id}&raceNo={race_number}&apiKey={pf_service.api_key}"
        
        headers = {
            'accept': 'application/json'
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if not response.ok:
            return jsonify({'success': False, 'error': f'API error {response.status_code}'}), response.status_code
        
        return jsonify(response.json())
        
    except Exception as e:
        logger.error(f"Speed maps fetch failed: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/api/debug/meeting/<int:meeting_id>/positions", methods=["GET"])
@login_required
def debug_positions(meeting_id):
    races = Race.query.filter_by(meeting_id=meeting_id).all()
    result = []
    for race in races:
        for h in race.horses[:3]:  # first 3 horses per race only
            csv_keys = list(h.csv_data.keys())[:10] if h.csv_data else []
            running_pos = h.csv_data.get('runningPosition', 'NOT FOUND') if h.csv_data else 'NO CSV_DATA'
            result.append({
                'horse': h.horse_name,
                'race': race.race_number,
                'runningPosition': running_pos,
                'csv_data_keys': csv_keys
            })
    return jsonify(result)

@app.route("/api/debug/horse/<int:horse_id>", methods=["GET"])
@login_required  
def debug_horse(horse_id):
    try:
        horse = Horse.query.get_or_404(horse_id)
        result = {
            'horse_id': horse.id,
            'horse_name': horse.horse_name,
            'is_scratched': horse.is_scratched,
            'has_race_id': hasattr(horse, 'race_id'),
            'race_id': getattr(horse, 'race_id', 'NO RACE_ID ATTR'),
        }
        if hasattr(horse, 'race_id') and horse.race_id:
            race = Race.query.get(horse.race_id)
            result['race_found'] = race is not None
            if race:
                result['meeting_id'] = race.meeting_id
                result['active_horses_in_race'] = len([h for h in race.horses if not h.is_scratched])
                first_active = next((h for h in race.horses if not h.is_scratched), None)
                if first_active:
                    result['has_prediction'] = first_active.prediction is not None
                    result['prediction_attr'] = type(first_active.prediction).__name__
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e), 'type': type(e).__name__}), 500

@app.route("/api/race/<int:race_id>/pfai-sectionals")
@login_required
def api_get_pfai_sectionals(race_id):
    """Get PFAI sectional rankings for a specific race from stored data"""
    try:
        race = Race.query.get_or_404(race_id)
        
        if not race.sectionals_json:
            return jsonify({'success': False, 'error': 'No PFAI data available'}), 404
        
        # Parse the stored JSON
        sectionals_data = json.loads(race.sectionals_json) if isinstance(race.sectionals_json, str) else race.sectionals_json
        
        if not sectionals_data or 'payLoad' not in sectionals_data:
            return jsonify({'success': False, 'error': 'No PFAI data available'}), 404
        
        # Filter for this race only
        race_data = [
            runner for runner in sectionals_data.get('payLoad', [])
            if runner.get('raceNo') == race.race_number
        ]
        
        return jsonify({'success': True, 'runners': race_data})
        
    except Exception as e:
        logger.error(f"PFAI sectionals fetch failed: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
        
@app.route("/api/meetings/<meeting_id>/ratings")
@login_required
def api_get_ratings(meeting_id):
    """Get ratings for a meeting"""
    try:
        url = f"https://api.puntingform.com.au/v2/Ratings/MeetingRatings?meetingId={meeting_id}&apiKey={pf_service.api_key}"
        
        headers = {
            'accept': 'application/json'
        }
        
        response = requests.get(url, headers=headers, timeout=30)
        
        if not response.ok:
            return jsonify({'success': False, 'error': f'API error {response.status_code}'}), response.status_code
        
        return jsonify(response.json())
        
    except Exception as e:
        logger.error(f"Ratings fetch failed: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/api/meetings/<int:meeting_id>/strikerate")
@login_required
def api_get_strikerate(meeting_id):
    """Get jockey/trainer career and last 100 strike rate data"""
    try:
        entity_type = request.args.get('entityType', '0')  # 0=Jockey, 1=Trainer, 2=Both
        jurisdiction = request.args.get('jurisdiction', '0')
        
        url = f"https://api.puntingform.com.au/v2/form/strikerate/csv?entityType={entity_type}&jurisdiction={jurisdiction}&apiKey={pf_service.api_key}"
        
        response = requests.get(url, headers={'accept': 'text/plain'}, timeout=30)
        
        if not response.ok:
            return jsonify({'success': False, 'error': f'API error {response.status_code}'}), response.status_code
        
        return response.text, 200, {'Content-Type': 'text/plain'}
        
    except Exception as e:
        logger.error(f"Strikerate fetch failed: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
@app.route("/api/meeting/<int:meeting_id>/ladbrokes-map")
@login_required
def get_ladbrokes_race_map(meeting_id):
    """Return a mapping of race_number → Ladbrokes event UUID for all races in a meeting."""
    try:
        meeting = Meeting.query.get_or_404(meeting_id)

        # Resolve track name — prefer puntingform_id, fall back to meeting_name suffix
        track_name = meeting.puntingform_id
        if not track_name and '_' in (meeting.meeting_name or ''):
            track_name = meeting.meeting_name.split('_')[1]
        if not track_name:
            return jsonify({})

        # Resolve date string
        if meeting.date:
            date_str = meeting.date.strftime('%Y-%m-%d')
        elif meeting.meeting_name and len(meeting.meeting_name) >= 6:
            dp = meeting.meeting_name.split('_')[0]
            date_str = f"20{dp[:2]}-{dp[2:4]}-{dp[4:6]}"
        else:
            return jsonify({})

        races = Race.query.filter_by(meeting_id=meeting_id).order_by(Race.race_number).all()
        race_map = {}
        for race in races:
            uuid = match_race_uuid(track_name, date_str, race.race_number)
            if uuid:
                race_map[str(race.race_number)] = uuid

        return jsonify(race_map)

    except Exception as e:
        logger.warning(f"Ladbrokes race map failed for meeting {meeting_id}: {e}")
        return jsonify({})

@app.route("/api/odds/ladbrokes/<race_uuid>")
@login_required
def get_ladbrokes_odds(race_uuid):
    """Proxy Ladbrokes live odds for a specific race UUID."""
    try:
        result = fetch_race_odds(race_uuid)
        return jsonify(result)
    except Exception as e:
        logger.warning(f"Ladbrokes odds route failed for {race_uuid}: {e}")
        return jsonify({"status": "error", "odds": {}})
        
@app.route("/api/meetings/<meeting_id>/scratchings")
@login_required
def api_get_scratchings(meeting_id):
    try:
        url = f"https://api.puntingform.com.au/v2/Updates/Scratchings?apiKey={pf_service.api_key}"
        response = requests.get(url, headers={'accept': 'application/json'}, timeout=30)

        if not response.ok:
            return jsonify({'success': False, 'error': f'API error {response.status_code}'}), response.status_code

        data = response.json()
        items = data.get('payLoad') or [] if isinstance(data, dict) else data if isinstance(data, list) else []

        # Build a tab->horseName lookup from stored speedmap data
        # meeting_id here is the DB meeting ID
        tab_name_lookup = {}  # { (raceNo, tabNo): horseName }
        try:
            db_meeting = Meeting.query.get(int(meeting_id))
            if db_meeting:
                for race in db_meeting.races:
                    if race.speed_maps_json:
                        sm = race.speed_maps_json if isinstance(race.speed_maps_json, dict) else json.loads(race.speed_maps_json)
                        for item in sm.get('payLoad', [{}])[0].get('items', []):
                            tab_name_lookup[(race.race_number, int(item.get('tabNo', 0)))] = item.get('runnerName', '')
        except Exception as e:
            logger.warning(f"Could not build tab lookup: {e}")

        scratchings = []
        for s in items:
            if not isinstance(s, dict):
                continue
            track = s.get('track') or s.get('Track') or s.get('trackName') or s.get('TrackName')
            race_no = s.get('raceNo') or s.get('RaceNo') or s.get('raceNumber') or s.get('RaceNumber')
            tab_no = s.get('tabNo') or s.get('TabNo') or s.get('tabNumber') or s.get('TabNumber')

            if track is None or race_no is None or tab_no is None:
                continue

            race_no_int = int(race_no) if str(race_no).isdigit() else race_no
            tab_no_int = int(tab_no) if str(tab_no).isdigit() else tab_no

            horse_name = tab_name_lookup.get((race_no_int, tab_no_int), '')

            scratchings.append({
                "track": str(track).strip(),
                "raceNo": race_no_int,
                "tabNo": tab_no_int,
                "horseName": horse_name  # Will be '' if not found in speedmap
            })

        return jsonify({"success": True, "scratchings": scratchings})

    except Exception as e:
        logger.error(f"Scratchings fetch failed: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
@app.route("/api/meetings/<meeting_id>/import", methods=["POST"])
@login_required
def api_import_meeting(meeting_id):
    """Import meeting from PuntingForm API with speed maps, ratings, AND sectionals"""
    try:
        # Get date from request
        date_str = request.form.get('date')
        if not date_str:
            date_str = datetime.now().strftime('%Y-%m-%d')
        
        # Get meetings for the specified date
        meetings_response = pf_service.get_meetings_list(date=date_str)
        meetings = meetings_response.get('meetings', [])
        
        # Find the meeting by ID
        meeting_info = next((m for m in meetings if m['meeting_id'] == meeting_id), None)
        
        if not meeting_info:
            return jsonify({'success': False, 'error': 'Meeting not found'}), 404
        
        track_name = meeting_info['track_name']
        
        # Track condition
        track_condition = request.form.get('track_condition', 'good')
        rail_position = int(request.form.get('rail_position', 0))
        
        # Generate meeting name
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        meeting_name = f"{date_obj.strftime('%y%m%d')}_{track_name}"
        
        # ==========================================
        # FETCH V2 API DATA BEFORE PROCESSING
        # ==========================================
        headers = {
            'Authorization': f'Bearer {pf_service.api_key}',
            'Content-Type': 'application/json'
        }
        
        # Fetch sectionals/ratings (meeting-level)
        sectionals_data = None
        try:
            sectionals_url = f"https://api.puntingform.com.au/v2/Ratings/MeetingRatings?meetingId={meeting_id}&apiKey={pf_service.api_key}"
            logger.info(f"📡 Fetching sectionals/ratings for meeting {meeting_id}")
            
            sectionals_response = requests.get(sectionals_url, headers=headers, timeout=30)
            
            if sectionals_response.ok:
                sectionals_data = sectionals_response.json()
                logger.info(f"✅ Fetched sectionals data with {len(sectionals_data.get('payLoad', []))} runners")
            else:
                logger.warning(f"⚠️  Sectionals fetch failed: {sectionals_response.status_code}")
        except Exception as e:
            logger.warning(f"Could not fetch sectionals: {str(e)}")
        
        # ==========================================
        # FETCH CSV DATA
        # ==========================================
        csv_data = pf_service.get_fields_csv(track_name, date_str)
        if not csv_data:
            return jsonify({'success': False, 'error': 'No data available for this meeting'}), 400

        # ==========================================
        # PRE-FETCH STRIKE RATES
        # ==========================================
        strike_rate_data = {'jockeys': {}, 'trainers': {}}
        try:
            strike_rate_data['jockeys']  = pf_service.get_strike_rates(date_str, 'jockey')
            strike_rate_data['trainers'] = pf_service.get_strike_rates(date_str, 'trainer')
            logger.info(f"✅ Strike rates: {len(strike_rate_data['jockeys'])} jockeys, {len(strike_rate_data['trainers'])} trainers")
            logger.info(f"Sample jockey keys: {list(strike_rate_data['jockeys'].keys())[:5]}")
            logger.info(f"Sample trainer keys: {list(strike_rate_data['trainers'].keys())[:5]}")
        except Exception as e:
            logger.warning(f"Strike rate pre-fetch failed (non-fatal): {str(e)}", exc_info=True)
        # ==========================================

        # ==========================================
        # FETCH SCRATCHINGS BEFORE ANALYSIS
        # ==========================================
        scratched_set = set()
        try:
            scratch_data = pf_service.get_scratchings()
            if isinstance(scratch_data, list):
                scratch_items = scratch_data
            else:
                scratch_items = (scratch_data.get('Result') or
                                scratch_data.get('result') or
                                scratch_data.get('payLoad') or
                                scratch_data.get('Scratchings') or [])

            for item in scratch_items:
                if not isinstance(item, dict):
                    continue
                item_track = str(item.get('TrackName') or item.get('trackName') or '').strip()
                if item_track.lower() != str(track_name).strip().lower():
                    continue
                for entry in item.get('Scratchings', []):
                    try:
                        parts = str(entry).split(',', 2)
                        if len(parts) >= 2:
                            scratched_set.add((int(parts[0]), int(parts[1])))
                    except (ValueError, TypeError):
                        continue
                break

            logger.info(f"✅ Found {len(scratched_set)} scratchings for {track_name}")
        except Exception as e:
            logger.warning(f"Could not fetch scratchings: {e}")
            scratched_set = set()
    
        # ==========================================
        # PRE-FETCH SPEED MAPS (needed before analysis for running position injection)
        # ==========================================
        import io as _io
        import csv as _csv

        csv_reader = _csv.DictReader(_io.StringIO(csv_data))
        race_numbers = set()
        for csv_row in csv_reader:
            rn = csv_row.get('race number', '').strip()
            if rn and rn.isdigit():
                race_numbers.add(int(rn))

        combined_speedmap = {'payLoad': []}
        all_speedmap_data = {}  # race_number (int) -> raw speed data for DB storage

        for rn in sorted(race_numbers):
            try:
                speed_url = f"https://api.puntingform.com.au/v2/User/Speedmaps?meetingId={meeting_id}&raceNo={rn}&apiKey={pf_service.api_key}"
                logger.info(f"📡 Pre-fetching speed map for Race {rn}")
                speed_response = requests.get(speed_url, headers=headers, timeout=30)
                if speed_response.ok:
                    speed_data = speed_response.json()
                    if isinstance(speed_data, dict) and speed_data.get('payLoad'):
                        all_speedmap_data[rn] = speed_data
                        for item in speed_data.get('payLoad', []):
                            combined_speedmap['payLoad'].append(item)
                        logger.info(f"   ✅ Pre-fetched speed map for race {rn}")
                    else:
                        logger.warning(f"   ⚠️  Empty speedmap payload for race {rn}")
                else:
                    logger.error(f"   ❌ HTTP {speed_response.status_code} for race {rn}")
            except Exception as e:
                logger.warning(f"   ⚠️  Could not pre-fetch speedmap for race {rn}: {e}")

        # ==========================================
        # PROCESS AND STORE (with all V2 data including speedmaps)
        # ==========================================
        meeting = process_and_store_results(
            csv_data=csv_data,
            filename=meeting_name,
            track_condition=track_condition,
            user_id=current_user.id,
            is_advanced=False,
            puntingform_id=track_name,
            speed_maps_data=combined_speedmap if combined_speedmap['payLoad'] else None,
            ratings_data=sectionals_data,
            sectionals_data=sectionals_data,
            rail_position=rail_position,
            scratched_set=scratched_set,
            strike_rate_data=strike_rate_data
        )

        meeting.date = date_obj.date()
        meeting.rail_position = rail_position
        meeting.pace_bias = 0  # Always starts neutral
        db.session.commit()

        # ==========================================
        # STORE SPEED MAPS ON RACE RECORDS
        # ==========================================
        races = Race.query.filter_by(meeting_id=meeting.id).order_by(Race.race_number).all()

        for race in races:
            if race.race_number in all_speedmap_data:
                race.speed_maps_json = json.dumps(all_speedmap_data[race.race_number])
                logger.info(f"   ✅ Stored speed map for race {race.race_number}")

        # Store sectionals data on ALL races
        if sectionals_data and races:
            for race in races:
                race.sectionals_json = json.dumps(sectionals_data)
                race.ratings_json = json.dumps(sectionals_data)
            logger.info("✅ Stored sectionals/ratings data on all races")

        db.session.commit()
        
        # ==========================================
        # FETCH AND STORE SPEED MAPS PER RACE
        # ==========================================
        races = Race.query.filter_by(meeting_id=meeting.id).order_by(Race.race_number).all()
        
        for race in races:
            try:
                speed_url = f"https://api.puntingform.com.au/v2/User/Speedmaps?meetingId={meeting_id}&raceNo={race.race_number}&apiKey={pf_service.api_key}"
                
                logger.info(f"📡 Fetching speed map for Race {race.race_number}")
                
                speed_response = requests.get(speed_url, headers=headers, timeout=30)
                
                if speed_response.ok:
                    speed_data = speed_response.json()
                    
                    if isinstance(speed_data, dict) and speed_data.get('payLoad'):
                        race.speed_maps_json = json.dumps(speed_data)
                        logger.info(f"   ✅ Stored speed map for race {race.race_number}")
                else:
                    logger.error(f"   ❌ HTTP {speed_response.status_code} for race {race.race_number}")
                    
            except Exception as e:
                logger.error(f"   ❌ Error for race {race.race_number}: {str(e)}")
        
        # Store sectionals data on ALL races
        if sectionals_data and races:
            for race in races:
                race.sectionals_json = json.dumps(sectionals_data)
                race.ratings_json = json.dumps(sectionals_data)
            logger.info("✅ Stored sectionals/ratings data on all races")
        
        db.session.commit()
        
        logger.info(f"✓ Imported {meeting_name} with V2 API data (speed maps, ratings, sectionals)")
        # ==========================================
        # ZERO OUT HORSES ALREADY SCRATCHED AT IMPORT
        # ==========================================
        try:
            scratch_url = f"https://api.puntingform.com.au/v2/Updates/Scratchings?apiKey={pf_service.api_key}"
            scratch_response = requests.get(scratch_url, headers={'accept': 'application/json'}, timeout=30)
            if scratch_response.ok:
                scratch_data = scratch_response.json()
                items = scratch_data.get('payLoad') or []
                tab_name_lookup = {}
                fresh_races = Race.query.filter_by(meeting_id=meeting.id).all()
                for race in fresh_races:
                    if race.speed_maps_json:
                        sm = race.speed_maps_json if isinstance(race.speed_maps_json, dict) else json.loads(race.speed_maps_json)
                        for it in sm.get('payLoad', [{}])[0].get('items', []):
                            try:
                                tab_no = int(it.get('tabNo', 0))
                            except Exception:
                                tab_no = 0
                            tab_name_lookup[(race.race_number, tab_no)] = it.get('runnerName', '') or ''
                for s in items:
                    if not isinstance(s, dict):
                        continue
                    track = s.get('track') or s.get('Track') or s.get('trackName') or s.get('TrackName')
                    race_no = s.get('raceNo') or s.get('RaceNo') or s.get('raceNumber') or s.get('RaceNumber')
                    tab_no = s.get('tabNo') or s.get('TabNo') or s.get('tabNumber') or s.get('TabNumber')
                    if track is None or race_no is None or tab_no is None:
                        continue
                    if str(track).strip().lower() != str(track_name).strip().lower():
                        continue
                    try:
                        rn = int(race_no)
                        tn = int(tab_no)
                    except Exception:
                        continue
                    horse_name = tab_name_lookup.get((rn, tn), '')
                    if not horse_name:
                        continue
                    race = next((r for r in fresh_races if r.race_number == rn), None)
                    if not race:
                        continue
                    horse = Horse.query.filter_by(race_id=race.id)\
                        .filter(db.func.lower(Horse.horse_name) == horse_name.lower()).first()
                    if horse:
                        horse.is_scratched = True
                        pred = Prediction.query.filter_by(horse_id=horse.id).first()
                        if pred:
                            pred.score = 0.0
                            pred.predicted_odds = ''
                            pred.win_probability = ''
                            pred.performance_component = ''
                            pred.base_probability = ''
                            pred.notes = 'Scratched'
                        
                db.session.commit()
                logger.info("✅ Zeroed scratched-at-import horses")
        except Exception as e:
            logger.warning(f"Could not zero import-time scratchings: {e}")
        return jsonify({
            'success': True,
            'meeting_id': meeting.id,
            'redirect_url': url_for('view_meeting', meeting_id=meeting.id)
        })
        
    except Exception as e:
        logger.error(f"Import failed: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route("/import-from-api")
@login_required
def import_from_api():
    """Page to import meetings from PuntingForm API"""
    from datetime import date
    today = date.today().strftime('%Y-%m-%d')
    return render_template("import_from_api.html", today=today)

@app.route("/api/meetings/<int:meeting_id>/update-scratchings", methods=["POST"])
@login_required
def update_scratchings(meeting_id):
    """Re-fetch fresh CSV + V2 data, exclude scratched horses, re-run analyzer, upsert predictions."""
    try:
        meeting = Meeting.query.get_or_404(meeting_id)
        headers = {
            'Authorization': f'Bearer {pf_service.api_key}',
            'Content-Type': 'application/json'
        }

        # ── 1. Parse date and track from meeting name (format: YYMMDD_TrackName) ──
        if not meeting.meeting_name or '_' not in meeting.meeting_name:
            return jsonify({'success': False, 'error': 'Cannot determine meeting date from name'}), 400

        date_part = meeting.meeting_name.split('_')[0]
        date_str = f"20{date_part[:2]}-{date_part[2:4]}-{date_part[4:6]}"
        track_name = meeting.puntingform_id or ''

        # ── 2. Resolve puntingform meeting_id ──
        pf_meeting_id = None
        try:
            meetings_data = pf_service.get_meetings_list(date_str)
            for m in meetings_data.get('meetings', []):
                if m['track_name'].lower() == track_name.lower():
                    pf_meeting_id = m['meeting_id']
                    break
        except Exception as e:
            logger.warning(f"Could not resolve puntingform meeting_id: {e}")

        if not pf_meeting_id:
            return jsonify({'success': False, 'error': f'Could not find meeting ID for {track_name} on {date_str}'}), 400

        # ── 3. Fetch scratchings ──
        scratched_names = set()
        try:
            url = f"https://api.puntingform.com.au/v2/Updates/Scratchings?apiKey={pf_service.api_key}"
            response = requests.get(url, headers={'accept': 'application/json'}, timeout=30)

            if response.ok:
                data = response.json()
                items = data.get('payLoad') if isinstance(data, dict) else data
                items = items or []

                # build tab->horseName lookup from DB speedmaps
                tab_name_lookup = {}
                for race in meeting.races:
                    if race.speed_maps_json:
                        sm = race.speed_maps_json if isinstance(race.speed_maps_json, dict) else json.loads(race.speed_maps_json)
                        for it in sm.get('payLoad', [{}])[0].get('items', []):
                            tab_no = it.get('tabNo', 0)
                            try:
                                tab_no = int(tab_no)
                            except Exception:
                                tab_no = 0
                            tab_name_lookup[(race.race_number, tab_no)] = it.get('runnerName', '') or ''

                for s in items:
                    if not isinstance(s, dict):
                        continue

                    track = s.get('track') or s.get('Track') or s.get('trackName') or s.get('TrackName')
                    race_no = s.get('raceNo') or s.get('RaceNo') or s.get('raceNumber') or s.get('RaceNumber')
                    tab_no = s.get('tabNo') or s.get('TabNo') or s.get('tabNumber') or s.get('TabNumber')

                    if track is None or race_no is None or tab_no is None:
                        continue

                    # filter to this meeting/track
                    if str(track).strip().lower() != str(track_name).strip().lower():
                        continue

                    try:
                        rn = int(race_no)
                        tn = int(tab_no)
                    except Exception:
                        continue

                    horse_name = tab_name_lookup.get((rn, tn), '')
                    if horse_name:
                        scratched_names.add(normalize_runner_name(horse_name))

        except Exception as e:
            logger.warning(f"Could not fetch scratchings: {e}")

        # ── 4. Get ALL scratched horses (existing + new) ──
        all_scratched_names = set(scratched_names)  # Start with new scratchings
        all_races = Race.query.filter_by(meeting_id=meeting_id).all()
        
        # Add existing scratched horses to the set
        for race in all_races:
            for horse in race.horses:
                if horse.is_scratched:
                    all_scratched_names.add(normalize_runner_name(horse.horse_name))

        # ── 5. Mark ALL scratched horses in DB ──
        scratched_count = 0
        for race in all_races:
            for horse in race.horses:
                norm = normalize_runner_name(horse.horse_name)
                was_scratched = horse.is_scratched
                horse.is_scratched = norm in all_scratched_names
                if horse.is_scratched and not was_scratched:
                    scratched_count += 1
        db.session.flush()

        # ── 6. Fetch fresh CSV from PuntingForm ──
        csv_data = pf_service.get_fields_csv(track_name, date_str)
        if not csv_data:
            return jsonify({'success': False, 'error': 'No CSV data returned from PuntingForm'}), 400

        # ── 7. Fetch sectionals/ratings ──
        sectionals_data = None
        try:
            sec_url = f"https://api.puntingform.com.au/v2/Ratings/MeetingRatings?meetingId={pf_meeting_id}&apiKey={pf_service.api_key}"
            sec_response = requests.get(sec_url, headers=headers, timeout=30)
            if sec_response.ok:
                sectionals_data = sec_response.json()
                logger.info(f"✅ Fetched sectionals/ratings for meeting {pf_meeting_id}")
        except Exception as e:
            logger.warning(f"Could not fetch sectionals: {e}")

        # ── 8. Fetch speedmaps per race ──
        import io as _io
        import csv as _csv
        csv_reader = _csv.DictReader(_io.StringIO(csv_data))
        race_numbers = set()
        for csv_row in csv_reader:
            rn = csv_row.get('race number', '').strip()
            if rn and rn.isdigit():
                race_numbers.add(int(rn))

        combined_speedmap = {'payLoad': []}
        all_speedmap_data = {}

        for rn in sorted(race_numbers):
            try:
                speed_url = f"https://api.puntingform.com.au/v2/User/Speedmaps?meetingId={pf_meeting_id}&raceNo={rn}&apiKey={pf_service.api_key}"
                speed_response = requests.get(speed_url, headers=headers, timeout=30)
                if speed_response.ok:
                    speed_data = speed_response.json()
                    if isinstance(speed_data, dict) and speed_data.get('payLoad'):
                        all_speedmap_data[rn] = speed_data
                        for item in speed_data.get('payLoad', []):
                            combined_speedmap['payLoad'].append(item)
                        logger.info(f"   ✅ Fetched speedmap for race {rn}")
            except Exception as e:
                logger.warning(f"Could not fetch speedmap for race {rn}: {e}")

        # ── 9. Parse CSV and remove scratched horses for analysis ──
        parsed_csv = parseCSV(csv_data)
        active_csv = [
            row for row in parsed_csv
            if normalize_runner_name(row.get('horse name', '')) not in all_scratched_names
        ]

        if not active_csv:
            return jsonify({'success': False, 'error': 'No active runners after scratchings'}), 400

        # ── 10. Inject sectionals (only for active horses) ──
        if sectionals_data:
            sectionals_payload = sectionals_data.get('payLoad', [])
            for row in active_csv:
                horse_name = row.get('horse name', '').strip()
                race_num = row.get('race number', '').strip()
                for runner in sectionals_payload:
                    runner_name = runner.get('runnerName', '') or runner.get('name', '')
                    if (str(runner.get('raceNo')) == str(race_num) and
                            runner_name.strip().lower() == horse_name.lower()):
                        row['last200TimePrice'] = str(runner.get('last200TimePrice', ''))
                        row['last200TimeRank'] = str(runner.get('last200TimeRank', ''))
                        row['last400TimePrice'] = str(runner.get('last400TimePrice', ''))
                        row['last400TimeRank'] = str(runner.get('last400TimeRank', ''))
                        row['last600TimePrice'] = str(runner.get('last600TimePrice', ''))
                        row['last600TimeRank'] = str(runner.get('last600TimeRank', ''))
                        break

        # ── 11. Inject PFAI scores (only for active horses) ──
        if sectionals_data:
            ratings_payload = sectionals_data.get('payLoad', [])
            for row in active_csv:
                row['pfaiScore'] = ''
                horse_name = row.get('horse name', '').strip()
                race_num = row.get('race number', '').strip()
                for runner in ratings_payload:
                    runner_name = runner.get('runnerName', '').strip()
                    if (str(runner.get('raceNo')) == str(race_num) and
                            runner_name.lower() == horse_name.lower()):
                        row['pfaiScore'] = str(runner.get('pfaiScore', ''))
                        break

        # ── 12. Inject running positions (only for active horses) ──
        speed_maps_data = combined_speedmap if combined_speedmap['payLoad'] else None
        if speed_maps_data:
            for row in active_csv:
                row['runningPosition'] = ''

            speedmap_lookup = {}
            payload = speed_maps_data.get('payLoad', [])
            for race_sm in payload:
                race_no = str(race_sm.get('raceNo', '')).strip()
                for item in race_sm.get('items', []):
                    runner_name = normalize_runner_name(item.get('runnerName') or '')
                    settle_val = item.get('settle')
                    try:
                        settle_num = int(str(settle_val).split('/')[0].strip())
                    except Exception:
                        settle_num = None
                    if settle_num == 1:
                        pos_category = 'LEADER'
                    elif settle_num is not None and 2 <= settle_num <= 3:
                        pos_category = 'ONPACE'
                    elif settle_num is not None and 4 <= settle_num <= 7:
                        pos_category = 'MIDFIELD'
                    elif settle_num is not None:
                        pos_category = 'BACKMARKER'
                    else:
                        pos_category = None
                    if race_no and runner_name and pos_category:
                        speedmap_lookup[(race_no, runner_name)] = pos_category

            for row in active_csv:
                horse_name = normalize_runner_name(row.get('horse name', ''))
                race_num = str(row.get('race number', '')).strip()
                key = (race_num, horse_name)
                if key in speedmap_lookup:
                    row['runningPosition'] = speedmap_lookup[key]

        # ── 13. Rebuild CSV and run analyzer (only active horses) ──
        fresh_csv = rebuildCSV(active_csv)
        track_condition = all_races[0].track_condition if all_races else 'good'

        analysis_results = run_analyzer(fresh_csv, track_condition, False)
        if not analysis_results:
            return jsonify({'success': False, 'error': 'Analyzer returned no results'}), 500

        # ── 14. Group results by race ──
        races_data = {}
        for result in analysis_results:
            race_num = result['horse'].get('race number', '0')
            if not race_num or not str(race_num).isdigit():
                continue
            if race_num not in races_data:
                races_data[race_num] = []
            races_data[race_num].append(result)

        # ── 15. DELETE ALL existing predictions first ──
        for race in all_races:
            for horse in race.horses:
                if horse.prediction:
                    db.session.delete(horse.prediction)
        db.session.flush()

        # ── 16. Create predictions for ALL horses ──
        rail_pos = meeting.rail_position or 0
        pace_bias = meeting.pace_bias or 0
        races_updated = 0

        for race in all_races:
            race_num_str = str(race.race_number)
            horses_results = races_data.get(race_num_str, [])
            result_lookup = {}
            for r in horses_results:
                name = r.get('horse', {}).get('horse name', '')
                if name:
                    result_lookup[normalize_runner_name(name)] = r

            updated_any = False

            for horse in race.horses:
                horse_norm = normalize_runner_name(horse.horse_name)
                
                if horse.is_scratched:
                    # Create zero prediction for scratched horses
                    pred = Prediction(
                        horse_id=horse.id,
                        score=0.0,
                        predicted_odds='',
                        win_probability='',
                        performance_component='',
                        base_probability='',
                        notes='Scratched'
                    )
                    db.session.add(pred)
                    updated_any = True
                else:
                    # Create normal prediction for active horses
                    r = result_lookup.get(horse_norm)
                    if not r:
                        continue

                    base_score = r.get('adjustedScore', r.get('score', 0))
                    running_position = r['horse'].get('runningposition', '')

                    # Apply rail bias
                    if running_position and rail_pos:
                        base_score = apply_track_bias(base_score, running_position, rail_pos, 0)

                    # Apply current pace bias
                    if running_position and pace_bias:
                        base_score = round(base_score + _bias_adjustment(running_position, rail_pos, pace_bias), 1)

                    pred = Prediction(
                        horse_id=horse.id,
                        score=base_score,
                        predicted_odds=r.get('trueOdds', ''),
                        win_probability=r.get('winProbability', ''),
                        performance_component=r.get('performanceComponent', ''),
                        base_probability=r.get('baseProbability', ''),
                        notes=r.get('notes', '')
                    )
                    db.session.add(pred)
                    updated_any = True

            if updated_any:
                races_updated += 1

        db.session.commit()

        import gc
        gc.collect()

        return jsonify({
            'success': True,
            'scratched_count': scratched_count,
            'races_updated': races_updated,
            'total_scratched': len(all_scratched_names),
            'message': f'Updated {scratched_count} new scratching(s), repriced {races_updated} race(s). Total scratched: {len(all_scratched_names)}'
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"update_scratchings failed: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
    
@app.route("/results/<int:meeting_id>/fetch-auto", methods=["POST"])
@login_required
def fetch_automatic_results(meeting_id):
    """Auto-fetch results from PuntingForm API"""
    meeting = Meeting.query.get_or_404(meeting_id)
    
    if not meeting.puntingform_id:
        flash("⚠️ Meeting not imported from PuntingForm API", "warning")
        return redirect(url_for('results_entry', meeting_id=meeting_id))
    
    try:
        track_name = meeting.puntingform_id
        
        # Get date from meeting name (format: YYMMDD_Track)
        if meeting.meeting_name and '_' in meeting.meeting_name:
            date_part = meeting.meeting_name.split('_')[0]
            # Convert YYMMDD to YYYY-MM-DD
            year = '20' + date_part[:2]
            month = date_part[2:4]
            day = date_part[4:6]
            date_str = f"{year}-{month}-{day}"
        else:
            flash("⚠️ Could not determine meeting date", "warning")
            return redirect(url_for('results_entry', meeting_id=meeting_id))
        
        # Fetch results using V1 method (track_name + date)
        results_response = pf_service.get_results(track_name, date_str)
        
        # Check for errors
        if results_response.get('IsError'):
            flash("⚠️ Results not yet available for this meeting", "warning")
            return redirect(url_for('results_entry', meeting_id=meeting_id))
        
        # V1 API returns format: {"Result": [{"RaceNumber": 1, "Runners": [...]}]}
        races_results = results_response.get('RaceDetails', [])
        
        if not races_results:
            flash("⚠️ No results available yet", "warning")
            return redirect(url_for('results_entry', meeting_id=meeting_id))
        
        horses_updated = 0
        
        # Process each race's results
        for race_result in races_results:
            race_num = race_result.get('RaceNumber')
            runners = race_result.get('Runners', [])
            
            # Find the race in our database
            race = Race.query.filter_by(
                meeting_id=meeting_id,
                race_number=race_num
            ).first()
            
            if not race:
                continue
            
            # Process each runner
            for runner in runners:
                horse_name = runner.get('Name', '').strip()
                finish_pos = runner.get('Position', 0)
                sp = runner.get('Price_SP', 0)
                
                if not horse_name:
                    continue
                
                # Handle unplaced horses (5th or worse)
                if finish_pos > 4:
                    finish_pos = 5
                elif finish_pos == 0:
                    finish_pos = 0
                
                # Find horse by name (case-insensitive)
                horse = Horse.query.filter(
                    Horse.race_id == race.id,
                    db.func.lower(Horse.horse_name) == horse_name.lower()
                ).first()
                
                if not horse:
                    continue
                
                # Create or update result
                if horse.result:
                    horse.result.finish_position = finish_pos
                    horse.result.sp = sp
                    horse.result.recorded_at = datetime.utcnow()
                    horse.result.recorded_by = current_user.id
                else:
                    result = Result(
                        horse_id=horse.id,
                        finish_position=finish_pos,
                        sp=sp,
                        recorded_by=current_user.id
                    )
                    db.session.add(result)
                
                horses_updated += 1
        
        db.session.commit()
        flash(f"✓ Auto-fetched results for {horses_updated} horses from PuntingForm", "success")
        
    except Exception as e:
        logger.error(f"Auto-fetch error: {str(e)}", exc_info=True)
        flash(f"✗ Failed to fetch results: {str(e)}", "danger")
    
    return redirect(url_for('results_entry', meeting_id=meeting_id))

@app.route("/results/<int:meeting_id>/mark-scratched-and-complete", methods=["POST"])
@login_required
def mark_scratched_and_complete(meeting_id):
    """Mark all remaining horses as scratched and complete the meeting"""
    meeting = Meeting.query.get_or_404(meeting_id)
    
    try:
        scratched_count = 0
        
        # Get all races in this meeting
        races = Race.query.filter_by(meeting_id=meeting_id).all()
        
        for race in races:
            # Get all horses in this race
            horses = Horse.query.filter_by(race_id=race.id).all()
            
            for horse in horses:
                # If horse has no result, mark as scratched
                if not horse.result:
                    result = Result(
                        horse_id=horse.id,
                        finish_position=0,  # 0 = scratched
                        sp=None,
                        recorded_by=current_user.id
                    )
                    db.session.add(result)
                    scratched_count += 1
        
        db.session.commit()
        flash(f"✓ Marked {scratched_count} horses as scratched and completed meeting", "success")
        
    except Exception as e:
        logger.error(f"Mark scratched error: {str(e)}", exc_info=True)
        flash(f"✗ Failed to complete meeting: {str(e)}", "danger")
    
    return redirect(url_for('view_meeting', meeting_id=meeting_id))

@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    """Handle CSV upload and run analysis"""
    csv_file = request.files.get("csv_file")
    
    if not csv_file or csv_file.filename == '':
        flash("Please select a CSV file", "danger")
        return redirect(url_for("dashboard"))
    
    if not csv_file.filename.endswith('.csv'):
        flash("Please upload a CSV file", "danger")
        return redirect(url_for("dashboard"))
    
    track_condition = request.form.get("track_condition", "good")
    is_advanced = bool(request.form.get("advanced_mode"))
    
    try:
        # Read CSV data
        csv_data = csv_file.read().decode('utf-8')
        
        # Process and store results
        meeting = process_and_store_results(
            csv_data=csv_data,
            filename=csv_file.filename,
            track_condition=track_condition,
            user_id=current_user.id,
            is_advanced=is_advanced
        )
        
        flash(f"{meeting.meeting_name} analyzed successfully!", "success")
        return redirect(url_for("view_meeting", meeting_id=meeting.id))
        
    except Exception as e:
        flash(f"Analysis failed: {str(e)}", "danger")
        return redirect(url_for("dashboard"))


@app.route('/history')
@login_required
def history():
    meetings = Meeting.query.order_by(Meeting.date.asc(), Meeting.uploaded_at.desc()).all()
    
    # Convert meetings to JSON for calendar view
    meetings_json = [{
        'id': m.id,
        'meeting_name': m.meeting_name,
        'user': m.user.username if m.user else 'unknown',
        'uploaded_at': m.uploaded_at.isoformat() if m.uploaded_at else None,
        'date': m.date.isoformat() if m.date else None
    } for m in meetings]
    
    import json
    return render_template('history.html', meetings=meetings, meetings_json=json.dumps(meetings_json))


@app.route("/meeting/<int:meeting_id>")
@login_required
def view_meeting(meeting_id):
    """View analysis results for a meeting"""
    meeting = Meeting.query.get_or_404(meeting_id)
    
    # All logged-in users can view all meetings
    results = get_meeting_results(meeting_id)
    return render_template("view_meeting.html", meeting=meeting, results=results)
    
@app.route("/api/horse/<int:horse_id>/toggle-scratch", methods=["POST"])
@login_required
def toggle_horse_scratch(horse_id):
    """Toggle scratch status and recalculate remaining runners' odds/probabilities"""
    horse = Horse.query.get_or_404(horse_id)
    horse.is_scratched = not horse.is_scratched
    db.session.commit()

    # Force fresh query — don't rely on cached relationship
    active_horses = Horse.query.filter_by(
        race_id=horse.race_id,
        is_scratched=False
    ).join(Prediction).all()

    if active_horses:
        OVERROUND = 1.10  # 110% market
        total_score = sum(
            h.prediction.score for h in active_horses
            if h.prediction and h.prediction.score > 0
        )

        for h in active_horses:
            if total_score > 0 and h.prediction:
                new_prob = (h.prediction.score / total_score) * 100
                new_odds = round(1 / ((new_prob / 100) * OVERROUND), 2) if new_prob > 0 else 99.0
                h.prediction.win_probability = f"{new_prob:.1f}%"
                h.prediction.predicted_odds = f"${new_odds:.2f}"

        db.session.commit()

    return jsonify({
        'success': True,
        'horse_id': horse_id,
        'is_scratched': horse.is_scratched,
        'horse_name': horse.horse_name
    })
@app.route("/api/meeting/<int:meeting_id>/update-bias", methods=["POST"])
@login_required
def update_meeting_bias(meeting_id):
    meeting = Meeting.query.get_or_404(meeting_id)
    data = request.get_json()
    new_bias = int(data.get('pace_bias', 0))

    if new_bias not in [-2, -1, 0, 1, 2]:
        return jsonify({'success': False, 'error': 'pace_bias must be -2 to +2'}), 400

    old_bias = meeting.pace_bias or 0
    meeting.pace_bias = new_bias
    db.session.commit()

    races = Race.query.filter_by(meeting_id=meeting_id).all()
    updated_count = 0

    for race in races:
        position_map = {}

        if race.speed_maps_json:
            try:
                smap = race.speed_maps_json
                if isinstance(smap, str):
                    import json
                    smap = json.loads(smap)

                items = smap.get('payLoad', [{}])[0].get('items', [])
                for item in items:
                    settle = item.get('settle', 99)
                    name = item.get('runnerName', '').strip().lower()
                    if settle <= 2:
                        pos = 'LEADER'
                    elif settle <= 4:
                        pos = 'ONPACE'
                    elif settle <= 8:
                        pos = 'MIDFIELD'
                    else:
                        pos = 'BACKMARKER'
                    position_map[name] = pos
            except Exception as e:
                logger.warning(f"Could not parse speedmap for race {race.id}: {e}")

        active_horses = [h for h in race.horses if not h.is_scratched and h.prediction]

        for h in active_horses:
            horse_name_norm = h.horse_name.strip().lower()
            running_position = position_map.get(horse_name_norm, '')

            if not running_position:
                continue

            old_adj = _bias_adjustment(running_position, meeting.rail_position or 0, old_bias)
            new_adj = _bias_adjustment(running_position, meeting.rail_position or 0, new_bias)
            diff = new_adj - old_adj

            if diff != 0:
                h.prediction.score = round(h.prediction.score + diff, 1)
                updated_count += 1

        total_score = sum(h.prediction.score for h in active_horses if h.prediction and h.prediction.score > 0)
        for h in active_horses:
            if h.prediction and total_score > 0:
                new_prob = (h.prediction.score / total_score) * 100
                new_odds = round(1 / (new_prob / 100), 2) if new_prob > 0 else 99.0
                h.prediction.win_probability = f"{new_prob:.1f}%"
                h.prediction.predicted_odds = f"${new_odds:.2f}"

    db.session.commit()
    logger.info(f"✅ Updated pace_bias to {new_bias} for meeting {meeting_id}, adjusted {updated_count} horses")

    return jsonify({
        'success': True,
        'meeting_id': meeting_id,
        'pace_bias': new_bias,
        'updated_horses': updated_count
    })


def _bias_adjustment(running_position, rail_position, pace_bias):
    """
    Score adjustment based on rail position and pace bias.
    Rail position = metres the running rail is out from the fence.
    Wider rail = narrower track = leaders harder to run down.
    """
    # ── Rail modifier ──
    # True rail: no adjustment
    # +15m out: massive leader advantage — up to +15 points for leaders
    if rail_position >= 13:
        rail_mod = 15.0
    elif rail_position >= 10:
        rail_mod = 11.0
    elif rail_position >= 7:
        rail_mod = 7.0
    elif rail_position >= 4:
        rail_mod = 4.0
    elif rail_position >= 1:
        rail_mod = 1.5
    else:
        rail_mod = 0.0  # True rail

    # ── Pace bias modifier ──
    # Each step = 4.0 points base adjustment
    # -2 = strong backmarker, +2 = leaders paradise
    pace_mod = pace_bias * 4.0

    pos = running_position.upper()
    if pos == 'LEADER':
        return rail_mod + pace_mod
    elif pos == 'ONPACE':
        return (rail_mod * 0.65) + (pace_mod * 0.65)
    elif pos == 'MIDFIELD':
        return -((rail_mod * 0.35) + (pace_mod * 0.35))
    elif pos == 'BACKMARKER':
        return -((rail_mod * 0.85) + (pace_mod * 0.85))
    return 0.0

@app.route("/meeting/<int:meeting_id>/delete", methods=["POST"])
@login_required
def delete_meeting(meeting_id):
    """Delete a meeting"""
    meeting = Meeting.query.get_or_404(meeting_id)
    
    if meeting.user_id != current_user.id and not current_user.is_admin:
        flash("You don't have permission to delete this meeting", "danger")
        return redirect(url_for("history"))
    
    db.session.delete(meeting)
    db.session.commit()
    
    flash(f"Meeting '{meeting.meeting_name}' deleted", "success")
    return redirect(url_for("history"))


# ----- Results Tracking Routes -----

@app.route("/results")
@login_required
def results():
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for("history"))
    """Show meetings needing results - complete ones load via AJAX"""
    from sqlalchemy import func
    
    # ONLY load meetings that need results - SUPER FAST
    needs_results_query = db.session.query(
        Meeting.id,
        Meeting.meeting_name,
        Meeting.uploaded_at,
        User.username,
        func.count(Race.id.distinct()).label('total_races'),
        func.count(Horse.id).label('total_horses'),
        func.count(Result.id).label('horses_with_results')
    ).join(
        User, Meeting.user_id == User.id
    ).outerjoin(
        Race, Meeting.id == Race.meeting_id
    ).outerjoin(
        Horse, Race.id == Horse.race_id
    ).outerjoin(
        Result, Horse.id == Result.horse_id
    ).group_by(
        Meeting.id, Meeting.meeting_name, Meeting.uploaded_at, User.username
    ).having(
        func.count(Horse.id) > func.count(Result.id)
    ).order_by(
        Meeting.uploaded_at.desc()
    ).limit(20).all()
    
    needs_results = []
    
    for row in needs_results_query:
        meeting = Meeting.query.get(row.id)
        races_complete = sum(1 for race in meeting.races 
                           if len(race.horses) > 0 and 
                           sum(1 for h in race.horses if h.result) == len(race.horses))
        
        needs_results.append({
            'id': row.id,
            'meeting_name': row.meeting_name,
            'uploaded_at': row.uploaded_at,
            'user': row.username,
            'total_races': row.total_races or 0,
            'races_complete': races_complete,
            'total_horses': row.total_horses or 0,
            'horses_with_results': row.horses_with_results or 0
        })
    
    return render_template("results.html", 
                          needs_results=needs_results, 
                          results_complete=[])
@app.route("/api/results/complete")
@login_required
def api_results_complete():
    """API endpoint to load completed results"""
    from flask import jsonify
    from sqlalchemy import func
    
    # Load complete meetings
    complete_query = db.session.query(
        Meeting.id,
        Meeting.meeting_name,
        Meeting.uploaded_at,
        User.username,
        func.count(Race.id.distinct()).label('total_races'),
        func.count(Horse.id).label('total_horses')
    ).join(
        User, Meeting.user_id == User.id
    ).outerjoin(
        Race, Meeting.id == Race.meeting_id
    ).outerjoin(
        Horse, Race.id == Horse.race_id
    ).outerjoin(
        Result, Horse.id == Result.horse_id
    ).group_by(
        Meeting.id, Meeting.meeting_name, Meeting.uploaded_at, User.username
    ).having(
        func.count(Horse.id) == func.count(Result.id)
    ).order_by(
        Meeting.uploaded_at.desc()
    ).all()
    
    results_complete = []
    for row in complete_query:
        results_complete.append({
            'id': row.id,
            'meeting_name': row.meeting_name,
            'uploaded_at': row.uploaded_at.isoformat(),
            'user': row.username,
            'total_races': row.total_races or 0,
            'total_horses': row.total_horses or 0
        })
    
    return jsonify(results_complete)

@app.route("/results/<int:meeting_id>")
@login_required
def results_entry(meeting_id):
    """Form to enter results for a meeting"""
    meeting = Meeting.query.get_or_404(meeting_id)
    results = get_meeting_results(meeting_id)
    
    # Add result data to each horse
    for race in results['races']:
        for horse in race['horses']:
            # Find the horse record to get any existing result
            horse_record = Horse.query.filter_by(
                race_id=Race.query.filter_by(
                    meeting_id=meeting_id, 
                    race_number=race['race_number']
                ).first().id,
                horse_name=horse['horse_name']
            ).first()
            
            if horse_record and horse_record.result:
                horse['result_finish'] = horse_record.result.finish_position
                horse['result_sp'] = horse_record.result.sp
            else:
                horse['result_finish'] = None
                horse['result_sp'] = None
            
            horse['horse_id'] = horse_record.id if horse_record else None
    
    return render_template("results_entry.html", meeting=meeting, results=results)


@app.route("/results/<int:meeting_id>/save", methods=["POST"])
@login_required
def save_results(meeting_id):
    """Save results for a race"""
    meeting = Meeting.query.get_or_404(meeting_id)
    
    race_number = request.form.get('race_number', type=int)
    
    if not race_number:
        flash("No race specified", "danger")
        return redirect(url_for('results_entry', meeting_id=meeting_id))
    
    race = Race.query.filter_by(meeting_id=meeting_id, race_number=race_number).first()
    
    if not race:
        flash(f"Race {race_number} not found", "danger")
        return redirect(url_for('results_entry', meeting_id=meeting_id))
    
    # Collect all horse results from form
    errors = []
    results_to_save = []
    
    for horse in race.horses:
        finish_key = f"finish_{horse.id}"
        sp_key = f"sp_{horse.id}"
        
        finish = request.form.get(finish_key, type=int)
        sp = request.form.get(sp_key, type=float)
        
        if finish is None:
            errors.append(f"Missing finish position for {horse.horse_name}")
        elif finish not in [0, 1, 2, 3, 4, 5]:  # Added 0 for scratched
            errors.append(f"Invalid finish position for {horse.horse_name}")
        
        # Only require SP for horses that actually ran (not scratched)
        if finish in [1, 2, 3, 4]:
            if sp is None:
                errors.append(f"Missing SP for {horse.horse_name}")
            elif sp < 1.01 or sp > 999:
                errors.append(f"Invalid SP for {horse.horse_name} (must be $1.01 - $999)")
        
        if finish is not None:
            results_to_save.append({
                'horse': horse,
                'finish': finish,
                'sp': sp if finish > 0 else None  # NULL SP for scratched horses
            })
    
    if errors:
        for error in errors:
            flash(error, "danger")
        return redirect(url_for('results_entry', meeting_id=meeting_id))
    
    # All validation passed - save results
    for item in results_to_save:
        horse = item['horse']
        
        # Update existing or create new
        if horse.result:
            horse.result.finish_position = item['finish']
            horse.result.sp = item['sp']  # Will be None for scratched horses
            horse.result.recorded_at = datetime.utcnow()
            horse.result.recorded_by = current_user.id
        else:
            result = Result(
                horse_id=horse.id,
                finish_position=item['finish'],
                sp=item['sp'],  # Will be None for scratched horses
                recorded_by=current_user.id
            )
            db.session.add(result)
    
    db.session.commit()
    flash(f"Race {race_number} results saved successfully", "success")
    
    # Check if all races are complete
    all_complete = True
    for r in meeting.races:
        for h in r.horses:
            if h.result is None:
                all_complete = False
                break
        if not all_complete:
            break
    
    if all_complete:
        flash(f"All results for {meeting.meeting_name} are now complete!", "success")
    
    return redirect(url_for('results_entry', meeting_id=meeting_id))


@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin_panel():
    if not current_user.is_admin:
        flash("Access denied", "danger")
        return redirect(url_for("history"))
    
    from models import Component  # ADD THIS IMPORT
    
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "create_user":
            username = request.form.get("username")
            email = request.form.get("email")
            password = request.form.get("password")
            is_admin = bool(request.form.get("is_admin"))
            
            if not username or not email or not password:
                flash("All fields are required", "danger")
            elif User.query.filter_by(username=username).first():
                flash(f"Username '{username}' already exists", "danger")
            elif User.query.filter_by(email=email).first():
                flash(f"Email '{email}' already exists", "danger")
            elif len(password) < 6:
                flash("Password must be at least 6 characters", "danger")
            else:
                new_user = User(username=username, email=email, is_admin=is_admin)
                new_user.set_password(password)
                db.session.add(new_user)
                db.session.commit()
                flash(f"User '{username}' created successfully", "success")
        
        elif action == "delete_user":
            user_id = request.form.get("user_id")
            user = User.query.get(user_id)
            
            if not user:
                flash("User not found", "danger")
            elif user.id == current_user.id:
                flash("You cannot delete your own account", "danger")
            else:
                username = user.username
                db.session.delete(user)
                db.session.commit()
                flash(f"User '{username}' deleted", "success")
        
        elif action == "reset_password":
            user_id = request.form.get("user_id")
            new_password = request.form.get("new_password")
            user = User.query.get(user_id)
            
            if not user:
                flash("User not found", "danger")
            elif len(new_password) < 6:
                flash("Password must be at least 6 characters", "danger")
            else:
                user.set_password(new_password)
                db.session.commit()
                flash(f"Password reset for '{user.username}'", "success")
        
        elif action == "toggle_admin":
            user_id = request.form.get("user_id")
            user = User.query.get(user_id)
            
            if not user:
                flash("User not found", "danger")
            elif user.id == current_user.id:
                flash("You cannot change your own admin status", "danger")
            else:
                user.is_admin = not user.is_admin
                db.session.commit()
                status = "admin" if user.is_admin else "regular user"
                flash(f"'{user.username}' is now a {status}", "success")
        
        elif action == "toggle_active":
            user_id = request.form.get("user_id")
            user = User.query.get(user_id)
            
            if not user:
                flash("User not found", "danger")
            elif user.id == current_user.id:
                flash("You cannot deactivate your own account", "danger")
            else:
                user.is_active = not user.is_active
                db.session.commit()
                status = "activated" if user.is_active else "deactivated"
                flash(f"'{user.username}' has been {status}", "success")
        
        elif action == "change_my_password":
            current_password = request.form.get("current_password")
            new_password = request.form.get("new_password")
            confirm_password = request.form.get("confirm_password")
            
            if not current_user.check_password(current_password):
                flash("Current password is incorrect", "danger")
            elif len(new_password) < 6:
                flash("New password must be at least 6 characters", "danger")
            elif new_password != confirm_password:
                flash("New passwords do not match", "danger")
            else:
                current_user.set_password(new_password)
                db.session.commit()
                flash("Your password has been changed successfully", "success")
        
        # NEW COMPONENT ACTIONS START HERE
        elif action == "create_component":
            comp_name = request.form.get("component_name")
            appearances = request.form.get("appearances", type=int, default=0)
            wins = request.form.get("wins", type=int, default=0)
            roi = request.form.get("roi", type=float, default=0.0)
            is_active = bool(request.form.get("is_active"))
            notes = request.form.get("notes", "")
            
            if not comp_name:
                flash("Component name is required", "danger")
            elif Component.query.filter_by(component_name=comp_name).first():
                flash(f"Component '{comp_name}' already exists", "danger")
            else:
                strike_rate = (wins / appearances * 100) if appearances > 0 else 0.0
                
                new_component = Component(
                    component_name=comp_name,
                    appearances=appearances,
                    wins=wins,
                    strike_rate=strike_rate,
                    roi_percentage=roi,
                    is_active=is_active,
                    notes=notes
                )
                db.session.add(new_component)
                db.session.commit()
                flash(f"Component '{comp_name}' created successfully", "success")
        
        elif action == "edit_component":
            comp_id = request.form.get("component_id")
            component = Component.query.get(comp_id)
            
            if not component:
                flash("Component not found", "danger")
            else:
                component.component_name = request.form.get("component_name", component.component_name)
                component.appearances = request.form.get("appearances", type=int, default=component.appearances)
                component.wins = request.form.get("wins", type=int, default=component.wins)
                component.roi_percentage = request.form.get("roi", type=float, default=component.roi_percentage)
                component.notes = request.form.get("notes", "")
                
                # Recalculate strike rate
                if component.appearances > 0:
                    component.strike_rate = (component.wins / component.appearances) * 100
                else:
                    component.strike_rate = 0.0
                
                component.last_updated = datetime.utcnow()
                db.session.commit()
                flash(f"Component '{component.component_name}' updated successfully", "success")
        
        elif action == "toggle_component":
            comp_id = request.form.get("component_id")
            component = Component.query.get(comp_id)
            
            if not component:
                flash("Component not found", "danger")
            else:
                component.is_active = not component.is_active
                component.last_updated = datetime.utcnow()
                db.session.commit()
                status = "activated" if component.is_active else "deactivated"
                flash(f"Component '{component.component_name}' has been {status}", "success")
        
        elif action == "delete_component":
            comp_id = request.form.get("component_id")
            component = Component.query.get(comp_id)
            
            if not component:
                flash("Component not found", "danger")
            else:
                comp_name = component.component_name
                db.session.delete(component)
                db.session.commit()
                flash(f"Component '{comp_name}' deleted", "success")
        # NEW COMPONENT ACTIONS END HERE
        
        return redirect(url_for("admin_panel"))
    
    # Get stats
    users = User.query.all()
    total_meetings = Meeting.query.count()
    
    users_data = []
    for user in users:
        meeting_count = Meeting.query.filter_by(user_id=user.id).count()
        users_data.append({
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'is_admin': user.is_admin,
            'is_active': user.is_active,
            'meeting_count': meeting_count,
            'last_login': user.last_login,
            'created_at': user.created_at
        })
    
    stats = {
        'total_users': len(users),
        'total_meetings': total_meetings,
        'users': users_data
    }
    
    # NEW: Get component stats
    components = Component.query.order_by(Component.roi_percentage.desc()).all()
    
    components_data = []
    for comp in components:
        components_data.append({
            'id': comp.id,
            'name': comp.component_name,
            'is_active': comp.is_active,
            'appearances': comp.appearances,
            'wins': comp.wins,
            'strike_rate': comp.strike_rate,
            'roi': comp.roi_percentage,
            'notes': comp.notes,
            'last_updated': comp.last_updated
        })
    
    stats['components'] = components_data
    # END NEW
    
    return render_template("admin.html", stats=stats)


@app.route("/data")
@login_required
def data_analytics():
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for("history"))

    track_filter = request.args.get('track', '')
    min_score_filter = request.args.get('min_score', type=float)
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    limit_param = request.args.get('limit', 'all')

    tracks = db.session.query(Meeting.meeting_name).order_by(Meeting.uploaded_at.desc()).limit(200).all()
    track_list = sorted(set([t[0].split('_')[1] if '_' in t[0] else t[0] for t in tracks]))

    # SINGLE lean query — column-level only
    q = db.session.query(
        Meeting.id,
        Race.race_number,
        Prediction.score,
        Result.finish_position,
        Result.sp
    ).join(Race,       Race.meeting_id      == Meeting.id
    ).join(Horse,      Horse.race_id        == Race.id
    ).join(Prediction, Prediction.horse_id  == Horse.id
    ).join(Result,     Result.horse_id      == Horse.id
    ).filter(Result.finish_position > 0)

    if track_filter:
        q = q.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        q = q.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        q = q.filter(Meeting.uploaded_at <= date_to)

    q = q.order_by(Meeting.uploaded_at.desc(), Race.id.desc())
    rows = q.all()

    # Group by race
    from collections import defaultdict
    races = defaultdict(list)
    race_keys_ordered = []
    for meeting_id, race_num, score, finish_pos, sp in rows:
        key = (meeting_id, race_num)
        if key not in races:
            race_keys_ordered.append(key)
        races[key].append({'score': score, 'finish_pos': finish_pos, 'sp': sp or 0})

    # Apply limit by race count
    if limit_param != 'all':
        limit = int(limit_param) if str(limit_param).isdigit() else 200
        race_keys_ordered = race_keys_ordered[:limit]

    stake = 10.0
    total_races = 0
    top_pick_wins = 0
    total_profit = 0.0
    winner_sps = []

    for key in race_keys_ordered:
        horses = races[key]
        top = max(horses, key=lambda x: x['score'])

        if min_score_filter and top['score'] < min_score_filter:
            continue

        total_races += 1
        won = top['finish_pos'] == 1
        sp = top['sp']

        if won:
            top_pick_wins += 1
            total_profit += (sp * stake - stake)
            if sp > 0:
                winner_sps.append(sp)
        else:
            total_profit -= stake

    strike_rate = (top_pick_wins / total_races * 100) if total_races > 0 else 0
    roi = (total_profit / (total_races * stake) * 100) if total_races > 0 else 0
    avg_winner_sp = sum(winner_sps) / len(winner_sps) if winner_sps else 0

    # Best bets stats (already limited to 500, keep as-is)
    best_bets_stats = None
    try:
        best_bet_predictions = db.session.query(Prediction, Result, Horse).join(
            Horse, Prediction.horse_id == Horse.id
        ).join(
            Result, Horse.id == Result.horse_id
        ).filter(
            Prediction.best_bet_flagged_at.isnot(None),
            Result.finish_position > 0
        ).limit(500).all()

        if best_bet_predictions:
            total_bets = len(best_bet_predictions)
            wins = sum(1 for pred, res, horse in best_bet_predictions if res.finish_position == 1)
            places = sum(1 for pred, res, horse in best_bet_predictions if res.finish_position in [1, 2, 3])
            stake_per_bet = 10
            total_staked = total_bets * stake_per_bet
            total_return = sum(
                stake_per_bet * res.sp
                for pred, res, horse in best_bet_predictions
                if res.finish_position == 1 and res.sp
            )
            profit = total_return - total_staked
            bb_roi = (profit / total_staked * 100) if total_staked > 0 else 0

            component_performance = {}
            for pred, res, horse in best_bet_predictions:
                if pred.notes:
                    components = parse_notes_components(pred.notes)
                    for comp_name in components.keys():
                        if comp_name not in component_performance:
                            component_performance[comp_name] = {'bets': 0, 'wins': 0, 'staked': 0, 'return': 0}
                        component_performance[comp_name]['bets'] += 1
                        component_performance[comp_name]['staked'] += stake_per_bet
                        if res.finish_position == 1 and res.sp:
                            component_performance[comp_name]['wins'] += 1
                            component_performance[comp_name]['return'] += stake_per_bet * res.sp

            for comp in component_performance.values():
                comp['profit'] = comp['return'] - comp['staked']
                comp['roi'] = (comp['profit'] / comp['staked'] * 100) if comp['staked'] > 0 else 0
                comp['sr'] = (comp['wins'] / comp['bets'] * 100) if comp['bets'] > 0 else 0

            component_performance = dict(sorted(component_performance.items(), key=lambda x: x[1]['roi'], reverse=True))

            best_bets_stats = {
                'total_bets': total_bets,
                'wins': wins,
                'places': places,
                'strike_rate': (wins / total_bets * 100) if total_bets else 0,
                'place_rate': (places / total_bets * 100) if total_bets else 0,
                'total_staked': total_staked,
                'total_return': total_return,
                'profit': profit,
                'roi': bb_roi,
                'component_performance': component_performance
            }
    except Exception as e:
        print(f"Error calculating Best Bets stats: {e}")

    return render_template("data.html",
        total_races=total_races,
        strike_rate=strike_rate,
        top_pick_wins=top_pick_wins,
        roi=roi,
        total_profit=total_profit,
        avg_winner_sp=avg_winner_sp,
        track_list=track_list,
        best_bets_stats=best_bets_stats,
        filters={
            'track': track_filter,
            'min_score': min_score_filter,
            'date_from': date_from,
            'date_to': date_to,
            'limit': int(limit_param) if limit_param != 'all' else 'all'
        }
    )

@app.route("/api/data/score-analysis")
@login_required
def api_score_analysis():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    track_filter = request.args.get('track', '')
    min_score_filter = request.args.get('min_score', type=float)
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    limit_param = request.args.get('limit', 'all')

    from collections import defaultdict

    q = db.session.query(
        Meeting.id,
        Race.race_number,
        Prediction.score,
        Result.finish_position,
        Result.sp
    ).join(Race,       Race.meeting_id      == Meeting.id
    ).join(Horse,      Horse.race_id        == Race.id
    ).join(Prediction, Prediction.horse_id  == Horse.id
    ).join(Result,     Result.horse_id      == Horse.id
    ).filter(Result.finish_position > 0)

    if track_filter:
        q = q.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        q = q.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        q = q.filter(Meeting.uploaded_at <= date_to)

    q = q.order_by(Meeting.uploaded_at.desc(), Race.id.desc())
    rows = q.all()

    races = defaultdict(list)
    race_keys_ordered = []
    for meeting_id, race_num, score, finish_pos, sp in rows:
        key = (meeting_id, race_num)
        if key not in races:
            race_keys_ordered.append(key)
        races[key].append({'score': score, 'finish_pos': finish_pos, 'sp': sp or 0})

    if limit_param != 'all':
        limit = int(limit_param) if str(limit_param).isdigit() else 200
        race_keys_ordered = race_keys_ordered[:limit]

    stake = 10.0
    score_tiers = {t: {'races': 0, 'wins': 0, 'profit': 0} for t in
                   ['90-100','80-89','70-79','60-69','50-59','40-49','30-39','20-29','0-19']}
    score_gaps  = {g: {'races': 0, 'wins': 0, 'profit': 0} for g in
                   ['50+','40-49','30-39','20-29','10-19','<10']}

    for key in race_keys_ordered:
        horses = sorted(races[key], key=lambda x: x['score'], reverse=True)
        if not horses:
            continue
        top = horses[0]
        if min_score_filter and top['score'] < min_score_filter:
            continue

        s = top['score']
        gap = s - (horses[1]['score'] if len(horses) > 1 else 0)
        won = top['finish_pos'] == 1
        profit = (top['sp'] * stake - stake) if won else -stake

        tier = ('90-100' if s >= 90 else '80-89' if s >= 80 else '70-79' if s >= 70 else
                '60-69' if s >= 60 else '50-59' if s >= 50 else '40-49' if s >= 40 else
                '30-39' if s >= 30 else '20-29' if s >= 20 else '0-19')
        gap_bucket = ('50+' if gap >= 50 else '40-49' if gap >= 40 else '30-39' if gap >= 30 else
                      '20-29' if gap >= 20 else '10-19' if gap >= 10 else '<10')

        score_tiers[tier]['races'] += 1
        score_gaps[gap_bucket]['races'] += 1
        if won:
            score_tiers[tier]['wins'] += 1
            score_gaps[gap_bucket]['wins'] += 1
        score_tiers[tier]['profit'] += profit
        score_gaps[gap_bucket]['profit'] += profit

    for d in list(score_tiers.values()) + list(score_gaps.values()):
        d['strike_rate'] = (d['wins'] / d['races'] * 100) if d['races'] else 0
        d['roi'] = (d['profit'] / (d['races'] * stake) * 100) if d['races'] else 0

    return jsonify({'score_tiers': score_tiers, 'score_gaps': score_gaps})
    
def extract_sectional_history(notes):
    
    import re
    
    result = {}
    
    if not notes:
        return result
    
    # Unescape literal \n if stored that way in DB
    notes = notes.replace('\\n', '\n')
    
    # Extract HISTORY_ADJ array
    adj_match = re.search(r'HISTORY_ADJ:\s*\[([\d.,\s]+)\]', notes)
    if adj_match:
        result['history_adjusted'] = [float(x.strip()) for x in adj_match.group(1).split(',')]
    
    # Extract HISTORY_RAW array
    raw_match = re.search(r'HISTORY_RAW:\s*\[([\d.,\s]+)\]', notes)
    if raw_match:
        result['history_raw'] = [float(x.strip()) for x in raw_match.group(1).split(',')]
    
    # Extract best recent info: "best of last 5 (z=-0.54)" then "33.77s → 33.02s"
    best_match = re.search(r'best of last (\d+) \(z=([-\d.]+)\)\s+└─\s+([\d.]+)s\s*→\s*([\d.]+)s', notes)
    if best_match:
        result['best_recent'] = {
            'from_last': int(best_match.group(1)),
            'zscore': float(best_match.group(2)),
            'raw_time': float(best_match.group(3)),
            'adjusted_time': float(best_match.group(4))
        }
    
    # Extract weighted avg: "weighted avg (z=0.86, 3 runs)"
    wavg_match = re.search(r'weighted avg \(z=([-\d.]+),\s*(\d+)\s*runs?\)', notes)
    if wavg_match:
        result['weighted_avg'] = {
            'zscore': float(wavg_match.group(1)),
            'run_count': int(wavg_match.group(2))
        }
    
    # Extract consistency: "consistency - good (SD=0.44s)"
    cons_match = re.search(r'consistency - (\w+) \(SD=([\d.]+)s\)', notes)
    if cons_match:
        result['consistency'] = {
            'rating': cons_match.group(1),
            'std_dev': float(cons_match.group(2))
        }
    
    return result

@app.route("/api/meeting/<int:meeting_id>/sectionals")
@login_required
def get_meeting_sectionals(meeting_id):
    """
    Returns sectional times for each horse's last runs.
    Now includes PFAI API sectionals when available.
    """
    try:
        meeting = Meeting.query.get_or_404(meeting_id)
        races = Race.query.filter_by(meeting_id=meeting_id).all()
        
        if not races:
            return jsonify({}), 200
        
        races_data = {}
        
        for race in races:
            race_key = f"race_{race.race_number}"
            
            if race_key not in races_data:
                races_data[race_key] = []
            
            horses = Horse.query.filter_by(race_id=race.id).all()
            
            for horse in horses:
                prediction = Prediction.query.filter_by(horse_id=horse.id).first()
                
                if not prediction:
                    continue
                
                # Extract historical sectional times from notes
                sectional_data = extract_sectional_history(prediction.notes)
                
                # ✨ NEW: Add PFAI sectionals from race.sectionals_json if available
                pfai_sectionals = None
                if race.sectionals_json:
                    try:
                        sectionals_payload = json.loads(race.sectionals_json) if isinstance(race.sectionals_json, str) else race.sectionals_json
                        
                        # Find this horse's PFAI data
                        if sectionals_payload and 'payLoad' in sectionals_payload:
                            for runner in sectionals_payload['payLoad']:
                                runner_name = runner.get('runnerName', '') or runner.get('name', '')
                                
                                if runner_name.strip().lower() == horse.horse_name.strip().lower():
                                    pfai_sectionals = {
                                        'last200_price': runner.get('last200TimePrice'),
                                        'last200_rank': runner.get('last200TimeRank'),
                                        'last400_price': runner.get('last400TimePrice'),
                                        'last400_rank': runner.get('last400TimeRank'),
                                        'last600_price': runner.get('last600TimePrice'),
                                        'last600_rank': runner.get('last600TimeRank')
                                    }
                                    break
                    except Exception as e:
                        print(f"Error parsing PFAI sectionals: {e}")
                
                horse_data = {
                    'horse_name': horse.horse_name,
                    'score': prediction.score,
                    'best_recent': sectional_data.get('best_recent'),
                    'weighted_avg': sectional_data.get('weighted_avg'),
                    'consistency': sectional_data.get('consistency'),
                    'history_adjusted': sectional_data.get('history_adjusted', []),
                    'history_raw': sectional_data.get('history_raw', []),
                    'pfai_sectionals': pfai_sectionals,  # ✨ NEW
                    'has_data': len(sectional_data.get('history_adjusted', [])) > 0 or pfai_sectionals is not None
                }
                
                races_data[race_key].append(horse_data)
        
        for race_key in races_data:
            races_data[race_key].sort(key=lambda x: x['score'], reverse=True)
        
        return jsonify(races_data)
    
    except Exception as e:
        print(f"Error in get_meeting_sectionals: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    
_PFAI_ANALYZER_RE = re.compile(
    r'Analyzer Score \(normalized\): ([\d.]+)',
    re.DOTALL
)

def _parse_analyzer_score(notes):
    if not notes:
        return None
    m = _PFAI_ANALYZER_RE.search(notes)
    if m:
        return float(m.group(1))
    return None

SCORING_PREFIXES = (
    'Jockey', 'Trainer', 'Track Win Rate', 'Track Podium',
    'Track+Distance Win', 'Track+Distance Podium', 'Track+Distance Score',
    'Track+Distance -',
    'Distance Win', 'Distance Podium', 'Distance Change', 'Distance -',
    'Distance Score', 'Condition Win', 'Condition Podium', 'Condition -',
    'Class Drop', 'Class Rise', 'Last Start', 'Days Since Run -',
    'Form Price', 'First Up', 'Second Up', 'Weight vs Field',
    'Weight Change', 'Career Win Rate', 'Age/Sex', 'Colt', 'Sire',
    'Specialist', 'Sectional History', 'Sectional Consistency',
    'API Sectional', 'Running Position', 'Hidden Edge', 'PFAI Score',
    'Market Expectation', 'Pace Angle', 'Ran Places', 'Track Score',
    'Track Condition Score',
)

NEGATIVE_COMPONENTS = {
    'Jockey - Poor Value',
    'Trainer - Poor Value',
    'Track - Poor Performance',
    'Distance - Poor Performance',
    'Condition - Poor Performance',
    'Last Start - Beaten Clearly (3-6L)',
    'Last Start - Well Beaten (6-10L)',
    'Last Start - Demolished (10L+)',
    'Last Start - Beaten Badly Placed',
    'Career Win Rate - Poor <10%',
    'Age/Sex - 5yo Mare Penalty',
    'Age/Sex - 6-7yo Mare Penalty',
    'Age/Sex - 7-8yo Penalty',
    'Age/Sex - 9yo Penalty',
    'Age/Sex - 10yo Penalty',
    'Age/Sex - 11yo Penalty',
    'Age/Sex - 12yo Penalty',
    'Age/Sex - 13+yo Penalty',
    'Market Expectation - Worst in Field',
    'Market Expectation - Chronic Underperformer',
    'Market Expectation - Significant Underperformer',
    'Market Expectation - Mild Underperformer',
    'Market Expectation - Below Average',
    'Sire - Negative ROI',
}

def is_scoring_component(name):
    return any(name.startswith(p) for p in SCORING_PREFIXES)

@app.route("/api/data/component-analysis")
@login_required
def api_component_analysis():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from collections import defaultdict

    track_filter     = request.args.get('track', '')
    date_from        = request.args.get('date_from', '')
    date_to          = request.args.get('date_to', '')
    limit_param      = request.args.get('limit', 'all')

    # ── Build race id list ──────────────────────────────────────────────
    race_id_query = db.session.query(
        Race.id, Meeting.uploaded_at
    ).join(
        Meeting, Race.meeting_id == Meeting.id
    ).join(
        Horse, Horse.race_id == Race.id
    ).join(
        Result, Result.horse_id == Horse.id
    ).filter(
        Result.finish_position > 0
    )

    if track_filter:
        race_id_query = race_id_query.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        race_id_query = race_id_query.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        race_id_query = race_id_query.filter(Meeting.uploaded_at <= date_to)

    all_race_ids = race_id_query.distinct().order_by(
        Meeting.uploaded_at.desc(), Race.id.desc()
    ).all()

    if limit_param == 'all':
        recent_race_ids = [r[0] for r in all_race_ids]
    else:
        limit = int(limit_param) if limit_param.isdigit() else 200
        recent_race_ids = [r[0] for r in all_race_ids[:limit]]

    if not recent_race_ids:
        return jsonify({
            'components': [], 'race_relative': [], 'winner_gap': [],
            'winner_gap_meta': {}, 'stacking': {}, 'scoring_audit': []
        })

    # ── Fetch all horses ────────────────────────────────────────────────
    rows = db.session.query(
        Race.id,
        Horse.horse_name,
        Prediction.score,
        Prediction.notes,
        Result.finish_position,
        Result.sp
    ).join(Horse,      Horse.race_id       == Race.id
    ).join(Prediction, Prediction.horse_id == Horse.id
    ).join(Result,     Result.horse_id     == Horse.id
    ).filter(
        Result.finish_position > 0,
        Race.id.in_(recent_race_ids)
    ).all()

    # ── Group into races — use analyzer score for ranking ───────────────
    races = defaultdict(list)
    for race_id, horse_name, score, notes, finish_pos, sp in rows:
        analyzer_score = _parse_analyzer_score(notes)
        races[race_id].append({
            'horse_name': horse_name,
            'score':      analyzer_score if analyzer_score is not None else (score or 0),
            'notes':      notes or '',
            'finish_pos': finish_pos,
            'sp':         sp or 0,
            'components': parse_notes_components(notes or '')
        })

    stake = 10.0

    # ══════════════════════════════════════════════════════════════════
    # A — COMPONENT ROI STATS (all horses, ROI sorted)
    # ══════════════════════════════════════════════════════════════════
    comp_stats = defaultdict(lambda: {
        'appearances': 0, 'wins': 0, 'places': 0, 'profit': 0.0
    })

    for race_id, horses in races.items():
        for h in horses:
            won   = h['finish_pos'] == 1
            placed = h['finish_pos'] in (1, 2, 3)
            profit = (h['sp'] * stake - stake) if won else -stake
            for comp in h['components']:
                if comp.startswith('_'):
                    continue
                comp_stats[comp]['appearances'] += 1
                if won:
                    comp_stats[comp]['wins'] += 1
                    comp_stats[comp]['profit'] += profit
                else:
                    comp_stats[comp]['profit'] -= stake
                if placed:
                    comp_stats[comp]['places'] += 1

    components_list = []
    for name, stats in sorted(comp_stats.items(), key=lambda x: x[1]['profit'] / max(x[1]['appearances'], 1), reverse=True):
        n = stats['appearances']
        if n < 2:
            continue
        w = stats['wins']
        p = stats['places']
        roi = round(stats['profit'] / (n * stake) * 100, 1)
        components_list.append({
            'name':        name,
            'appearances': n,
            'wins':        w,
            'strike_rate': round(w / n * 100, 1),
            'places':      p,
            'place_rate':  round(p / n * 100, 1),
            'roi':         roi,
        })

    components_list.sort(key=lambda x: x['roi'], reverse=True)

    # ══════════════════════════════════════════════════════════════════
    # A (ENHANCED) — RACE-RELATIVE COMPONENT LIFT
    # ══════════════════════════════════════════════════════════════════
    comp_with    = defaultdict(lambda: {'races': 0, 'wins': 0})
    comp_without = defaultdict(lambda: {'races': 0, 'wins': 0})

    for race_id, horses in races.items():
        if len(horses) < 2:
            continue
        all_comps_in_race = set()
        for h in horses:
            all_comps_in_race.update(
                k for k in h['components'] if not k.startswith('_')
            )
        for comp in all_comps_in_race:
            for h in horses:
                has_comp = comp in h['components']
                won      = h['finish_pos'] == 1
                if has_comp:
                    comp_with[comp]['races'] += 1
                    if won:
                        comp_with[comp]['wins'] += 1
                else:
                    comp_without[comp]['races'] += 1
                    if won:
                        comp_without[comp]['wins'] += 1

    race_relative = []
    for comp in comp_with:
        with_r = comp_with[comp]['races']
        with_w = comp_with[comp]['wins']
        wo_r   = comp_without[comp]['races']
        wo_w   = comp_without[comp]['wins']
        if with_r < 10 or wo_r < 10:
            continue
        with_sr = round(with_w / with_r * 100, 1)
        wo_sr   = round(wo_w   / wo_r   * 100, 1)
        lift    = round(with_sr - wo_sr, 1)
        race_relative.append({
            'name':        comp,
            'with_races':  with_r,
            'with_wins':   with_w,
            'with_sr':     with_sr,
            'without_sr':  wo_sr,
            'lift':        lift,
        })

    race_relative.sort(key=lambda x: x['lift'], reverse=True)

    # ══════════════════════════════════════════════════════════════════
    # B — WINNER GAP ANALYSIS
    # ══════════════════════════════════════════════════════════════════
    gap_comp_counts = defaultdict(int)
    gap_total_races = 0
    top_pick_losses = 0

    for race_id, horses in races.items():
        if len(horses) < 2:
            continue
        top_pick = max(horses, key=lambda x: x['score'])
        winner   = next((h for h in horses if h['finish_pos'] == 1), None)
        if not winner:
            continue
        gap_total_races += 1
        if top_pick['horse_name'] == winner['horse_name']:
            continue
        top_comps    = set(k for k in top_pick['components'] if not k.startswith('_'))
        winner_comps = set(k for k in winner['components']   if not k.startswith('_'))
        missing      = winner_comps - top_comps
        top_pick_losses += 1
        for comp in missing:
            gap_comp_counts[comp] += 1

    winner_gap = [
        {
            'component':     comp,
            'count':         count,
            'pct_of_losses': round(count / top_pick_losses * 100, 1) if top_pick_losses else 0,
        }
        for comp, count in sorted(gap_comp_counts.items(), key=lambda x: x[1], reverse=True)
        if count >= 5
    ]

    # ══════════════════════════════════════════════════════════════════
    # C — COMPONENT STACKING
    # ══════════════════════════════════════════════════════════════════
    stacking_buckets = {
        '<0':    {'horses': 0, 'wins': 0, 'profit': 0.0},
        '0-4':   {'horses': 0, 'wins': 0, 'profit': 0.0},
        '5-9':   {'horses': 0, 'wins': 0, 'profit': 0.0},
        '10-14': {'horses': 0, 'wins': 0, 'profit': 0.0},
        '15-19': {'horses': 0, 'wins': 0, 'profit': 0.0},
        '20-24': {'horses': 0, 'wins': 0, 'profit': 0.0},
        '25-29': {'horses': 0, 'wins': 0, 'profit': 0.0},
        '30+':   {'horses': 0, 'wins': 0, 'profit': 0.0},
    }

    def get_stacking_bucket(n):
        if n < 0:     return '<0'
        elif n <= 4:  return '0-4'
        elif n <= 9:  return '5-9'
        elif n <= 14: return '10-14'
        elif n <= 19: return '15-19'
        elif n <= 24: return '20-24'
        elif n <= 29: return '25-29'
        else:         return '30+'

    for race_id, horses in races.items():
        for h in horses:
            pos_count = sum(
                1 for k in h['components']
                if is_scoring_component(k)
                and k not in NEGATIVE_COMPONENTS
            )
            neg_count = sum(
                1 for k in h['components']
                if is_scoring_component(k)
                and k in NEGATIVE_COMPONENTS
            )
            net_count = pos_count - neg_count
            won    = h['finish_pos'] == 1
            profit = (h['sp'] * stake - stake) if won else -stake
            bucket = get_stacking_bucket(net_count)
            stacking_buckets[bucket]['horses'] += 1
            if won:
                stacking_buckets[bucket]['wins'] += 1
            stacking_buckets[bucket]['profit'] += profit

    bucket_order = ['<0','0-4','5-9','10-14','15-19','20-24','25-29','30+']
    stacking_results = {}
    for bucket in bucket_order:
        data = stacking_buckets[bucket]
        n = data['horses']
        w = data['wins']
        stacking_results[bucket] = {
            'horses':      n,
            'wins':        w,
            'strike_rate': round(w / n * 100, 1) if n else 0,
            'roi':         round(data['profit'] / (n * stake) * 100, 1) if n else 0,
            'profit':      round(data['profit'], 2),
        }

    # ══════════════════════════════════════════════════════════════════
    # D — SCORING AUDIT (over/under scored)
    # ══════════════════════════════════════════════════════════════════
    comp_points_roi = defaultdict(lambda: {
        'total_points': 0.0,
        'appearances':  0,
        'wins':         0,
        'profit':       0.0
    })

    for race_id, horses in races.items():
        for h in horses:
            won    = h['finish_pos'] == 1
            profit = (h['sp'] * stake - stake) if won else -stake
            for comp, pts in h['components'].items():
                if comp.startswith('_'):
                    continue
                if not isinstance(pts, (int, float)):
                    continue
                comp_points_roi[comp]['total_points'] += pts
                comp_points_roi[comp]['appearances']  += 1
                if won:
                    comp_points_roi[comp]['wins']   += 1
                    comp_points_roi[comp]['profit'] += profit
                else:
                    comp_points_roi[comp]['profit'] -= stake

    scoring_audit = []
    for comp, data in comp_points_roi.items():
        n = data['appearances']
        if n < 10:
            continue
        avg_pts    = round(data['total_points'] / n, 1)
        roi        = round(data['profit'] / (n * stake) * 100, 1)
        sr         = round(data['wins'] / n * 100, 1)
        divergence = round(avg_pts - (roi / 10), 1)
        scoring_audit.append({
            'name':        comp,
            'appearances': n,
            'avg_pts':     avg_pts,
            'strike_rate': sr,
            'roi':         roi,
            'divergence':  divergence,
        })

    scoring_audit.sort(key=lambda x: x['divergence'], reverse=True)
    # ══════════════════════════════════════════════════════════════════
    # E — RAW FACTOR MINING (csv_data fields not scored by analyzer)
    # ══════════════════════════════════════════════════════════════════
    # Needs csv_data + meeting name, so requires a second lightweight query
    import re as _re
 
    raw_rows = db.session.query(
        Race.id,
        Horse.csv_data,
        Horse.is_scratched,
        Race.track_condition,
        Meeting.meeting_name,
        Result.finish_position,
        Result.sp
    ).join(Horse,   Horse.race_id    == Race.id
    ).join(Meeting, Race.meeting_id  == Meeting.id
    ).join(Result,  Result.horse_id  == Horse.id
    ).filter(
        Result.finish_position > 0,
        Horse.is_scratched == False,
        Race.id.in_(recent_race_ids)
    ).all()
 
    raw_buckets = defaultdict(lambda: {"wins": 0, "total": 0, "profit": 0.0, "category": ""})
 
    def _racc(key, category, won, sp):
        raw_buckets[key]["wins"]    += 1 if won else 0
        raw_buckets[key]["total"]   += 1
        raw_buckets[key]["profit"]  += (sp * stake - stake) if won else -stake
        raw_buckets[key]["category"] = category
 
    total_raw = len(raw_rows)
    raw_wins  = sum(1 for r in raw_rows if r.finish_position == 1)
    raw_avg_wr = raw_wins / total_raw * 100 if total_raw else 0
 
    for race_id, csv, is_scratched, track_cond, meeting_name, finish_pos, sp in raw_rows:
        csv  = csv or {}
        won  = finish_pos == 1
        sp   = sp or 0
 
        # Age + Sex
        age = csv.get("horse age")
        sex = csv.get("horse sex", "").strip()
        if age and sex:
            _racc(f"AgeSex:{age}yo {sex}", "Age / Sex", won, sp)
        if age:
            _racc(f"Age:{age}yo", "Age", won, sp)
 
        # Country
        country = csv.get("country", "").strip().upper()
        if country:
            label = ("AUS" if country in ("AUS","AUSTRALIA") else
                     "NZ"  if country in ("NZ","NEW ZEALAND") else
                     "IRE" if country in ("IRE","IRELAND")    else
                     "GB"  if country in ("GB","GBR","UK")    else
                     "Other")
            _racc(f"Country:{label}", "Country of Origin", won, sp)
 
        # Barrier
        try:
            b = int(str(csv.get("horse barrier","0") or 0).strip())
            if 1 <= b <= 20:
                bg = ("1" if b==1 else "2-3" if b<=3 else "4-6" if b<=6 else "7-9" if b<=9 else "10+")
                _racc(f"BarrierGroup:{bg}", "Barrier Group", won, sp)
        except (ValueError, TypeError):
            pass
 
        # Weight type
        wt = csv.get("weight type", "").strip()
        if wt:
            _racc(f"WeightType:{wt}", "Weight Type", won, sp)
 
        # Sex restrictions
        sr = csv.get("sex restrictions", "").strip()
        if sr:
            _racc(f"SexRestrict:{sr}", "Sex Restrictions", won, sp)
 
        # Apprentice claim
        try:
            claim = float(str(csv.get("horse claim","0") or 0).strip())
            if claim > 0:
                cb = ("1kg" if claim<=1 else "1.5kg" if claim<=1.5 else "2kg" if claim<=2 else "3kg+")
                _racc(f"Claim:{cb}", "Claim Allowance", won, sp)
                _racc("Claim:HasClaim", "Claim: Any", won, sp)
            else:
                _racc("Claim:NoClaim", "Claim: No Claim", won, sp)
        except (ValueError, TypeError):
            pass
 
        # Last start finish position
        fp_raw = csv.get("form position", "")
        try:
            fp = int(str(fp_raw).strip())
            label = ("1st" if fp==1 else "2nd" if fp==2 else "3rd" if fp==3 else
                     "4th-6th" if fp<=6 else "7th-10th" if fp<=10 else "11th+")
            _racc(f"LastPos:{label}", "Last Start Position", won, sp)
        except (ValueError, TypeError):
            _racc("LastPos:NoForm", "Last Start Position", won, sp)
 
        # Last start SP
        try:
            fpp = float(str(csv.get("form price","0") or 0).strip())
            if fpp > 0:
                pb = ("≤$2" if fpp<=2 else "$2-$4" if fpp<=4 else "$4-$8" if fpp<=8 else
                      "$8-$15" if fpp<=15 else "$15-$30" if fpp<=30 else "$30+")
                _racc(f"LastSP:{pb}", "Last Start SP", won, sp)
        except (ValueError, TypeError):
            pass
 
        # Last start margin
        try:
            fm  = float(str(csv.get("form margin","0") or 0).strip())
            won_last = str(fp_raw).strip() == "1"
            if won_last:
                ml = ("Won <0.5L" if fm<0.5 else "Won 0.5-2L" if fm<=2 else
                      "Won 2-5L" if fm<=5 else "Won 5L+")
            else:
                ml = ("Lost <1L" if fm<1 else "Lost 1-2L" if fm<=2 else
                      "Lost 2-4L" if fm<=4 else "Lost 4-8L" if fm<=8 else "Lost 8L+")
            _racc(f"LastMargin:{ml}", "Last Start Margin", won, sp)
        except (ValueError, TypeError):
            pass
 
        # Last start going
        ftc = csv.get("form track condition", "").strip()
        if ftc:
            going_base = ftc.split()[0].capitalize()
            _racc(f"LastGoing:{ftc}",          "Last Start Going (Full)",  won, sp)
            _racc(f"LastGoingBase:{going_base}","Last Start Going (Type)", won, sp)
 
        # Last start class type
        fc = csv.get("form class", "").strip().upper()
        if fc:
            cls_type = ("Maiden"    if "MAIDEN" in fc else
                        "Benchmark" if "BENCH"  in fc or "BM" in fc else
                        "Restricted" if "REST"  in fc else
                        "Class"     if "CLASS"  in fc else
                        "Open"      if "OPEN"   in fc else
                        "Group/Listed" if ("GROUP" in fc or "GR" in fc or "LIST" in fc) else "Other")
            _racc(f"LastClassType:{cls_type}", "Last Start Class Type", won, sp)
 
        # Distance change vs last start
        try:
            today_d = int(str(csv.get("distance","0") or 0).replace("m","").strip())
            form_d  = int(str(csv.get("form distance","0") or 0).replace("m","").strip())
            if today_d > 0 and form_d > 0:
                delta = today_d - form_d
                dl = ("Same ±200m"   if abs(delta)<=200 else
                      "Up 200-400m"  if 200<delta<=400  else
                      "Up 400m+"     if delta>400        else
                      "Down 200-400m" if -400<=delta<-200 else "Down 400m+")
                _racc(f"DistChange:{dl}", "Distance Change", won, sp)
        except (ValueError, TypeError):
            pass
 
        # Weight change vs last start
        try:
            hw  = float(str(csv.get("horse weight","0") or 0).strip())
            fmw = float(str(csv.get("form weight","0")  or 0).strip())
            if 49<=hw<=65 and 49<=fmw<=65:
                diff = fmw - hw
                wl = ("Same ±0.5kg"    if abs(diff)<0.5  else
                      "Lighter ≤1.5kg" if 0.5<=diff<=1.5 else
                      "Lighter >1.5kg" if diff>1.5        else
                      "Heavier ≤1.5kg" if diff>=-1.5     else "Heavier >1.5kg")
                _racc(f"WeightChange:{wl}", "Weight Change vs Last", won, sp)
        except (ValueError, TypeError):
            pass
 
        # Career win-rate bucket
        rec = csv.get("horse record","").strip()
        try:
            parts  = rec.replace("-",":").split(":")
            starts = int(parts[0]); wins_c = int(parts[1])
            if starts >= 3:
                cr = wins_c/starts*100
                cl = ("0 wins ever" if wins_c==0 else "<10% SR" if cr<10 else
                      "10-20% SR" if cr<20 else "20-33% SR" if cr<33 else
                      "33-50% SR" if cr<50 else "50%+ SR")
                _racc(f"CareerSR:{cl}", "Career Strike Rate", won, sp)
        except (ValueError, TypeError, IndexError):
            pass
 
        # Race prizemoney tier
        prize_raw = csv.get("race prizemoney","") or csv.get("prizemoney","")
        pm_match  = _re.search(r"\$([\d,]+)", str(prize_raw))
        try:
            if pm_match:
                pv = int(pm_match.group(1).replace(",",""))
                pl = ("<$30k" if pv<30_000 else "$30k-$60k" if pv<60_000 else
                      "$60k-$100k" if pv<100_000 else "$100k-$200k" if pv<200_000 else "$200k+")
                _racc(f"Prizemoney:{pl}", "Race Prizemoney", won, sp)
        except (ValueError, TypeError):
            pass
 
        # Last start field size
        fsize_raw = csv.get("form other runners","")
        try:
            m2 = _re.match(r"^(\d+)", str(fsize_raw).strip())
            if m2:
                total_runners = int(m2.group(1)) + 1
                fl = ("Small ≤7" if total_runners<=7 else "Mid 8-11" if total_runners<=11 else
                      "Large 12-15" if total_runners<=15 else "Big 16+")
                _racc(f"LastFieldSize:{fl}", "Last Start Field Size", won, sp)
        except (ValueError, TypeError):
            pass
 
        # Last start sectional (600m)
        sect_raw = csv.get("sectional","").strip()
        sect_match = _re.match(r"([\d.]+)sec", sect_raw)
        try:
            if sect_match:
                sv = float(sect_match.group(1))
                if sv > 1:
                    sl = ("<33.5s" if sv<33.5 else "33.5-34.5s" if sv<34.5 else
                          "34.5-35.5s" if sv<35.5 else "35.5-36.5s" if sv<36.5 else ">36.5s")
                    _racc(f"LastSect600m:{sl}", "Last 600m Sectional", won, sp)
        except (ValueError, TypeError):
            pass
 
        # horse last10 form string
        last10 = csv.get("horse last10","").strip()
        if last10:
            digits = [c for c in last10 if c.isdigit()]
            if digits:
                wins_10 = digits.count("1")
                recent5 = digits[-5:]
                w5 = recent5.count("1")
                _racc(f"Last10Wins:{'4+' if wins_10>=4 else '2-3' if wins_10>=2 else '1' if wins_10==1 else '0'}",
                      "Last 10 Win Count", won, sp)
                _racc(f"Last5Wins:{'2+' if w5>=2 else '1' if w5==1 else '0'}",
                      "Last 5 Win Count", won, sp)
 
        # Same track as last start
        form_track    = csv.get("form track","").strip()
        meeting_track = meeting_name.split("_")[1] if "_" in meeting_name else meeting_name
        if form_track and meeting_track:
            same = form_track.lower() == meeting_track.lower()
            _racc(f"SameTrack:{'Yes' if same else 'No'}", "Same Track as Last Start", won, sp)
 
        # Unregistered jockey flag
        jock_id = csv.get("horse jockey id","").strip()
        _racc(f"JockeyReg:{'Unregistered' if not jock_id else 'Registered'}",
              "Jockey Registration", won, sp)
 
        # Age restrictions
        age_restr = csv.get("age restrictions","").strip()
        _racc(f"AgeRestriction:{age_restr if age_restr else 'Open Age'}", "Age Restrictions", won, sp)
 
        # Today's track condition type
        cond_base = (track_cond or "Unknown").split()[0].capitalize()
        _racc(f"ConditionType:{cond_base}", "Condition Type", won, sp)
 
        # Jockeys can claim
        jcc = csv.get("jockeys can claim","").strip()
        if jcc:
            _racc(f"JockeysCanClaim:{jcc}", "Jockeys Can Claim", won, sp)

        # ── PFAI SCORE ──
        pfai_raw = csv.get("pfaiScore", "").strip()
        try:
            pfai_val = float(pfai_raw)
            if pfai_val > 0:
                pfai_bucket = (
                    "85-100" if pfai_val >= 85 else
                    "75-84"  if pfai_val >= 75 else
                    "60-74"  if pfai_val >= 60 else
                    "40-59"  if pfai_val >= 40 else
                    "<40"
                )
                _racc(f"PFAI:{pfai_bucket}", "PFAI Score", won, sp)
        except (ValueError, TypeError):
            pass

        # ── RUNNING POSITION (speed map) ──
        rp = csv.get("runningPosition", "").strip().upper()
        if rp in ("LEADER", "ONPACE", "MIDFIELD", "BACKMARKER"):
            _racc(f"RunPos:{rp}", "Running Position", won, sp)

        # ── LAST 600m SECTIONAL RANK (field-relative) ──
        try:
            rank600 = int(str(csv.get("last600TimeRank", "") or "").strip())
            if rank600 > 0:
                r600_bucket = (
                    "Rank 1"   if rank600 == 1 else
                    "Rank 2-3" if rank600 <= 3 else
                    "Rank 4-6" if rank600 <= 6 else
                    "Rank 7+"
                )
                _racc(f"Rank600m:{r600_bucket}", "Last 600m Rank", won, sp)
        except (ValueError, TypeError):
            pass

        # ── LAST 400m SECTIONAL RANK ──
        try:
            rank400 = int(str(csv.get("last400TimeRank", "") or "").strip())
            if rank400 > 0:
                r400_bucket = (
                    "Rank 1"   if rank400 == 1 else
                    "Rank 2-3" if rank400 <= 3 else
                    "Rank 4-6" if rank400 <= 6 else
                    "Rank 7+"
                )
                _racc(f"Rank400m:{r400_bucket}", "Last 400m Rank", won, sp)
        except (ValueError, TypeError):
            pass

        # ── LAST 200m SECTIONAL RANK ──
        try:
            rank200 = int(str(csv.get("last200TimeRank", "") or "").strip())
            if rank200 > 0:
                r200_bucket = (
                    "Rank 1"   if rank200 == 1 else
                    "Rank 2-3" if rank200 <= 3 else
                    "Rank 4-6" if rank200 <= 6 else
                    "Rank 7+"
                )
                _racc(f"Rank200m:{r200_bucket}", "Last 200m Rank", won, sp)
        except (ValueError, TypeError):
            pass

        # ── LAST 600m RAW TIME ──
        try:
            time600 = float(str(csv.get("last600TimePrice", "") or "").strip())
            if time600 > 1:
                t600_bucket = (
                    "<33.5s"     if time600 < 33.5 else
                    "33.5-34.5s" if time600 < 34.5 else
                    "34.5-35.5s" if time600 < 35.5 else
                    ">35.5s"
                )
                _racc(f"Time600m:{t600_bucket}", "Last 600m Time", won, sp)
        except (ValueError, TypeError):
            pass

        # ── RUNNING POSITION × DISTANCE ──
        try:
            dist_int = int(str(csv.get("distance", "0") or "0").replace("m", "").strip())
            dist_type = (
                "Sprint"  if dist_int <= 1200 else
                "Mile"    if dist_int <= 1700 else
                "Middle"  if dist_int <= 2200 else
                "Staying"
            )
            if rp in ("LEADER", "ONPACE", "MIDFIELD", "BACKMARKER"):
                _racc(f"RunPosDist:{rp}_{dist_type}", "Running Position × Distance", won, sp)
        except (ValueError, TypeError):
            pass

    # Build raw_factors list — min 30 appearances, sorted by ROI
    min_raw_n = 30
    raw_factors = []
    for key, stats in raw_buckets.items():
        n = stats["total"]
        if n < min_raw_n:
            continue
        w   = stats["wins"]
        wr  = w / n * 100
        roi = stats["profit"] / (n * stake) * 100
        lift = wr - raw_avg_wr
        raw_factors.append({
            "key":      key,
            "category": stats["category"],
            "label":    key.split(":",1)[1] if ":" in key else key,
            "total":    n,
            "wins":     w,
            "win_rate": round(wr, 1),
            "roi":      round(roi, 1),
            "lift":     round(lift, 1),
            "profit":   round(stats["profit"], 2),
        })
 
    raw_factors.sort(key=lambda x: x["roi"], reverse=True)

# ══════════════════════════════════════════════════════════════════
    # F — TOP PICK ONLY COMPONENT ANALYSIS
    #     F1: parsed notes components (mirror of A, top pick only)
    #     F2: raw CSV bucketed factors (mirror of E, top pick only)
    # ══════════════════════════════════════════════════════════════════

    # Build a lookup of top pick horse name per race
    top_picks = {}
    for race_id, horses in races.items():
        if horses:
            top_picks[race_id] = max(horses, key=lambda x: x['score'])['horse_name']

    # ── F1: Parsed notes components — top pick only ─────────────────
    tp_comp_stats = defaultdict(lambda: {
        'appearances': 0, 'wins': 0, 'places': 0, 'profit': 0.0
    })

    for race_id, horses in races.items():
        for h in horses:
            if h['horse_name'] != top_picks.get(race_id):
                continue
            won    = h['finish_pos'] == 1
            placed = h['finish_pos'] in (1, 2, 3)
            profit = (h['sp'] * stake - stake) if won else -stake
            for comp in h['components']:
                if comp.startswith('_'):
                    continue
                tp_comp_stats[comp]['appearances'] += 1
                if won:
                    tp_comp_stats[comp]['wins']   += 1
                    tp_comp_stats[comp]['profit'] += profit
                else:
                    tp_comp_stats[comp]['profit'] -= stake
                if placed:
                    tp_comp_stats[comp]['places'] += 1

    tp_notes_list = []
    for name, stats in tp_comp_stats.items():
        n = stats['appearances']
        if n < 2:
            continue
        w   = stats['wins']
        p   = stats['places']
        roi = round(stats['profit'] / (n * stake) * 100, 1)
        tp_notes_list.append({
            'name':        name,
            'appearances': n,
            'wins':        w,
            'strike_rate': round(w / n * 100, 1),
            'places':      p,
            'place_rate':  round(p / n * 100, 1),
            'roi':         roi,
        })

    tp_notes_list.sort(key=lambda x: x['roi'], reverse=True)

    # ── F2: Raw CSV bucketed factors — top pick only ─────────────────
    tp_raw_buckets = defaultdict(lambda: {"wins": 0, "total": 0, "profit": 0.0, "category": ""})

    def _racc_tp(key, category, won, sp):
        tp_raw_buckets[key]["wins"]    += 1 if won else 0
        tp_raw_buckets[key]["total"]   += 1
        tp_raw_buckets[key]["profit"]  += (sp * stake - stake) if won else -stake
        tp_raw_buckets[key]["category"] = category

    # Build a top-pick lookup keyed by (race_id) for the raw_rows loop
    # raw_rows has race_id as first element so we can match on that
    top_pick_names_by_race = top_picks  # same dict, already built above

    for race_id, csv, is_scratched, track_cond, meeting_name, finish_pos, sp in raw_rows:
        csv      = csv or {}
        sp       = sp or 0
        won      = finish_pos == 1

        # We need the horse name to match against top_picks — raw_rows doesn't include it
        # So we skip this row if this race_id has no top pick recorded
        # Instead we'll use a horse_name lookup built from the races dict
        pass  # see note below — we rebuild the loop differently

    # raw_rows doesn't carry horse_name, so build a set of (race_id, horse_name) for top picks
    top_pick_set = set(top_picks.items())  # {(race_id, horse_name), ...}

    # We need horse_name in raw_rows — fetch it with a targeted query
    tp_raw_rows = db.session.query(
        Race.id,
        Horse.horse_name,
        Horse.csv_data,
        Horse.is_scratched,
        Race.track_condition,
        Meeting.meeting_name,
        Result.finish_position,
        Result.sp
    ).join(Horse,   Horse.race_id    == Race.id
    ).join(Meeting, Race.meeting_id  == Meeting.id
    ).join(Result,  Result.horse_id  == Horse.id
    ).filter(
        Result.finish_position > 0,
        Horse.is_scratched == False,
        Race.id.in_(recent_race_ids)
    ).all()

    for race_id, horse_name, csv, is_scratched, track_cond, meeting_name, finish_pos, sp in tp_raw_rows:
        if (race_id, horse_name) not in top_pick_set:
            continue
        csv = csv or {}
        sp  = sp or 0
        won = finish_pos == 1

        age = csv.get("horse age")
        sex = csv.get("horse sex", "").strip()
        if age and sex:
            _racc_tp(f"AgeSex:{age}yo {sex}", "Age / Sex", won, sp)
        if age:
            _racc_tp(f"Age:{age}yo", "Age", won, sp)

        country = csv.get("country", "").strip().upper()
        if country:
            label = ("AUS" if country in ("AUS","AUSTRALIA") else
                     "NZ"  if country in ("NZ","NEW ZEALAND") else
                     "IRE" if country in ("IRE","IRELAND")    else
                     "GB"  if country in ("GB","GBR","UK")    else
                     "Other")
            _racc_tp(f"Country:{label}", "Country of Origin", won, sp)

        try:
            b = int(str(csv.get("horse barrier","0") or 0).strip())
            if 1 <= b <= 20:
                bg = ("1" if b==1 else "2-3" if b<=3 else "4-6" if b<=6 else "7-9" if b<=9 else "10+")
                _racc_tp(f"BarrierGroup:{bg}", "Barrier Group", won, sp)
        except (ValueError, TypeError):
            pass

        wt = csv.get("weight type", "").strip()
        if wt:
            _racc_tp(f"WeightType:{wt}", "Weight Type", won, sp)

        sr_val = csv.get("sex restrictions", "").strip()
        if sr_val:
            _racc_tp(f"SexRestrict:{sr_val}", "Sex Restrictions", won, sp)

        try:
            claim = float(str(csv.get("horse claim","0") or 0).strip())
            if claim > 0:
                cb = ("1kg" if claim<=1 else "1.5kg" if claim<=1.5 else "2kg" if claim<=2 else "3kg+")
                _racc_tp(f"Claim:{cb}", "Claim Allowance", won, sp)
                _racc_tp("Claim:HasClaim", "Claim: Any", won, sp)
            else:
                _racc_tp("Claim:NoClaim", "Claim: No Claim", won, sp)
        except (ValueError, TypeError):
            pass

        fp_raw = csv.get("form position", "")
        try:
            fp = int(str(fp_raw).strip())
            label = ("1st" if fp==1 else "2nd" if fp==2 else "3rd" if fp==3 else
                     "4th-6th" if fp<=6 else "7th-10th" if fp<=10 else "11th+")
            _racc_tp(f"LastPos:{label}", "Last Start Position", won, sp)
        except (ValueError, TypeError):
            _racc_tp("LastPos:NoForm", "Last Start Position", won, sp)

        try:
            fpp = float(str(csv.get("form price","0") or 0).strip())
            if fpp > 0:
                pb = ("≤$2" if fpp<=2 else "$2-$4" if fpp<=4 else "$4-$8" if fpp<=8 else
                      "$8-$15" if fpp<=15 else "$15-$30" if fpp<=30 else "$30+")
                _racc_tp(f"LastSP:{pb}", "Last Start SP", won, sp)
        except (ValueError, TypeError):
            pass

        try:
            fm       = float(str(csv.get("form margin","0") or 0).strip())
            won_last = str(fp_raw).strip() == "1"
            if won_last:
                ml = ("Won <0.5L" if fm<0.5 else "Won 0.5-2L" if fm<=2 else
                      "Won 2-5L" if fm<=5 else "Won 5L+")
            else:
                ml = ("Lost <1L" if fm<1 else "Lost 1-2L" if fm<=2 else
                      "Lost 2-4L" if fm<=4 else "Lost 4-8L" if fm<=8 else "Lost 8L+")
            _racc_tp(f"LastMargin:{ml}", "Last Start Margin", won, sp)
        except (ValueError, TypeError):
            pass

        ftc = csv.get("form track condition", "").strip()
        if ftc:
            going_base = ftc.split()[0].capitalize()
            _racc_tp(f"LastGoing:{ftc}",           "Last Start Going (Full)",  won, sp)
            _racc_tp(f"LastGoingBase:{going_base}", "Last Start Going (Type)", won, sp)

        fc = csv.get("form class", "").strip().upper()
        if fc:
            cls_type = ("Maiden"       if "MAIDEN" in fc else
                        "Benchmark"    if "BENCH"  in fc or "BM" in fc else
                        "Restricted"   if "REST"   in fc else
                        "Class"        if "CLASS"  in fc else
                        "Open"         if "OPEN"   in fc else
                        "Group/Listed" if ("GROUP" in fc or "GR" in fc or "LIST" in fc) else "Other")
            _racc_tp(f"LastClassType:{cls_type}", "Last Start Class Type", won, sp)

        try:
            today_d = int(str(csv.get("distance","0") or 0).replace("m","").strip())
            form_d  = int(str(csv.get("form distance","0") or 0).replace("m","").strip())
            if today_d > 0 and form_d > 0:
                delta = today_d - form_d
                dl = ("Same ±200m"    if abs(delta)<=200 else
                      "Up 200-400m"   if 200<delta<=400  else
                      "Up 400m+"      if delta>400        else
                      "Down 200-400m" if -400<=delta<-200 else "Down 400m+")
                _racc_tp(f"DistChange:{dl}", "Distance Change", won, sp)
        except (ValueError, TypeError):
            pass

        try:
            hw  = float(str(csv.get("horse weight","0") or 0).strip())
            fmw = float(str(csv.get("form weight","0")  or 0).strip())
            if 49<=hw<=65 and 49<=fmw<=65:
                diff = fmw - hw
                wl = ("Same ±0.5kg"    if abs(diff)<0.5  else
                      "Lighter ≤1.5kg" if 0.5<=diff<=1.5 else
                      "Lighter >1.5kg" if diff>1.5        else
                      "Heavier ≤1.5kg" if diff>=-1.5     else "Heavier >1.5kg")
                _racc_tp(f"WeightChange:{wl}", "Weight Change vs Last", won, sp)
        except (ValueError, TypeError):
            pass

        rec = csv.get("horse record","").strip()
        try:
            parts  = rec.replace("-",":").split(":")
            starts = int(parts[0]); wins_c = int(parts[1])
            if starts >= 3:
                cr = wins_c/starts*100
                cl = ("0 wins ever" if wins_c==0 else "<10% SR" if cr<10 else
                      "10-20% SR" if cr<20 else "20-33% SR" if cr<33 else
                      "33-50% SR" if cr<50 else "50%+ SR")
                _racc_tp(f"CareerSR:{cl}", "Career Strike Rate", won, sp)
        except (ValueError, TypeError, IndexError):
            pass

        prize_raw = csv.get("race prizemoney","") or csv.get("prizemoney","")
        pm_match  = _re.search(r"\$([\d,]+)", str(prize_raw))
        try:
            if pm_match:
                pv = int(pm_match.group(1).replace(",",""))
                pl = ("<$30k" if pv<30_000 else "$30k-$60k" if pv<60_000 else
                      "$60k-$100k" if pv<100_000 else "$100k-$200k" if pv<200_000 else "$200k+")
                _racc_tp(f"Prizemoney:{pl}", "Race Prizemoney", won, sp)
        except (ValueError, TypeError):
            pass

        fsize_raw = csv.get("form other runners","")
        try:
            m2 = _re.match(r"^(\d+)", str(fsize_raw).strip())
            if m2:
                total_runners = int(m2.group(1)) + 1
                fl = ("Small ≤7" if total_runners<=7 else "Mid 8-11" if total_runners<=11 else
                      "Large 12-15" if total_runners<=15 else "Big 16+")
                _racc_tp(f"LastFieldSize:{fl}", "Last Start Field Size", won, sp)
        except (ValueError, TypeError):
            pass

        sect_raw   = csv.get("sectional","").strip()
        sect_match = _re.match(r"([\d.]+)sec", sect_raw)
        try:
            if sect_match:
                sv = float(sect_match.group(1))
                if sv > 1:
                    sl = ("<33.5s" if sv<33.5 else "33.5-34.5s" if sv<34.5 else
                          "34.5-35.5s" if sv<35.5 else "35.5-36.5s" if sv<36.5 else ">36.5s")
                    _racc_tp(f"LastSect600m:{sl}", "Last 600m Sectional", won, sp)
        except (ValueError, TypeError):
            pass

        last10 = csv.get("horse last10","").strip()
        if last10:
            digits  = [c for c in last10 if c.isdigit()]
            if digits:
                wins_10 = digits.count("1")
                recent5 = digits[-5:]
                w5 = recent5.count("1")
                _racc_tp(f"Last10Wins:{'4+' if wins_10>=4 else '2-3' if wins_10>=2 else '1' if wins_10==1 else '0'}",
                         "Last 10 Win Count", won, sp)
                _racc_tp(f"Last5Wins:{'2+' if w5>=2 else '1' if w5==1 else '0'}",
                         "Last 5 Win Count", won, sp)

        form_track    = csv.get("form track","").strip()
        meeting_track = meeting_name.split("_")[1] if "_" in meeting_name else meeting_name
        if form_track and meeting_track:
            same = form_track.lower() == meeting_track.lower()
            _racc_tp(f"SameTrack:{'Yes' if same else 'No'}", "Same Track as Last Start", won, sp)

        jock_id = csv.get("horse jockey id","").strip()
        _racc_tp(f"JockeyReg:{'Unregistered' if not jock_id else 'Registered'}",
                 "Jockey Registration", won, sp)

        age_restr = csv.get("age restrictions","").strip()
        _racc_tp(f"AgeRestriction:{age_restr if age_restr else 'Open Age'}", "Age Restrictions", won, sp)

        cond_base = (track_cond or "Unknown").split()[0].capitalize()
        _racc_tp(f"ConditionType:{cond_base}", "Condition Type", won, sp)

        jcc = csv.get("jockeys can claim","").strip()
        if jcc:
            _racc_tp(f"JockeysCanClaim:{jcc}", "Jockeys Can Claim", won, sp)

        pfai_raw = csv.get("pfaiScore", "").strip()
        try:
            pfai_val = float(pfai_raw)
            if pfai_val > 0:
                pfai_bucket = (
                    "85-100" if pfai_val >= 85 else
                    "75-84"  if pfai_val >= 75 else
                    "60-74"  if pfai_val >= 60 else
                    "40-59"  if pfai_val >= 40 else
                    "<40"
                )
                _racc_tp(f"PFAI:{pfai_bucket}", "PFAI Score", won, sp)
        except (ValueError, TypeError):
            pass

        rp = csv.get("runningPosition", "").strip().upper()
        if rp in ("LEADER", "ONPACE", "MIDFIELD", "BACKMARKER"):
            _racc_tp(f"RunPos:{rp}", "Running Position", won, sp)

        try:
            rank600 = int(str(csv.get("last600TimeRank", "") or "").strip())
            if rank600 > 0:
                r600_bucket = (
                    "Rank 1"   if rank600 == 1 else
                    "Rank 2-3" if rank600 <= 3 else
                    "Rank 4-6" if rank600 <= 6 else
                    "Rank 7+"
                )
                _racc_tp(f"Rank600m:{r600_bucket}", "Last 600m Rank", won, sp)
        except (ValueError, TypeError):
            pass

        try:
            rank400 = int(str(csv.get("last400TimeRank", "") or "").strip())
            if rank400 > 0:
                r400_bucket = (
                    "Rank 1"   if rank400 == 1 else
                    "Rank 2-3" if rank400 <= 3 else
                    "Rank 4-6" if rank400 <= 6 else
                    "Rank 7+"
                )
                _racc_tp(f"Rank400m:{r400_bucket}", "Last 400m Rank", won, sp)
        except (ValueError, TypeError):
            pass

        try:
            rank200 = int(str(csv.get("last200TimeRank", "") or "").strip())
            if rank200 > 0:
                r200_bucket = (
                    "Rank 1"   if rank200 == 1 else
                    "Rank 2-3" if rank200 <= 3 else
                    "Rank 4-6" if rank200 <= 6 else
                    "Rank 7+"
                )
                _racc_tp(f"Rank200m:{r200_bucket}", "Last 200m Rank", won, sp)
        except (ValueError, TypeError):
            pass

        try:
            time600 = float(str(csv.get("last600TimePrice", "") or "").strip())
            if time600 > 1:
                t600_bucket = (
                    "<33.5s"     if time600 < 33.5 else
                    "33.5-34.5s" if time600 < 34.5 else
                    "34.5-35.5s" if time600 < 35.5 else
                    ">35.5s"
                )
                _racc_tp(f"Time600m:{t600_bucket}", "Last 600m Time", won, sp)
        except (ValueError, TypeError):
            pass

        try:
            dist_int  = int(str(csv.get("distance", "0") or "0").replace("m", "").strip())
            dist_type = (
                "Sprint"  if dist_int <= 1200 else
                "Mile"    if dist_int <= 1700 else
                "Middle"  if dist_int <= 2200 else
                "Staying"
            )
            if rp in ("LEADER", "ONPACE", "MIDFIELD", "BACKMARKER"):
                _racc_tp(f"RunPosDist:{rp}_{dist_type}", "Running Position × Distance", won, sp)
        except (ValueError, TypeError):
            pass

    # Build F2 list
    tp_raw_list = []
    for key, stats in tp_raw_buckets.items():
        n = stats["total"]
        if n < 2:
            continue
        w   = stats["wins"]
        roi = round(stats["profit"] / (n * stake) * 100, 1)
        tp_raw_list.append({
            "key":        key,
            "category":   stats["category"],
            "label":      key.split(":",1)[1] if ":" in key else key,
            "appearances": n,
            "wins":       w,
            "strike_rate": round(w / n * 100, 1),
            "roi":        roi,
            "profit":     round(stats["profit"], 2),
        })

    tp_raw_list.sort(key=lambda x: x["roi"], reverse=True)
    
    raw_meta = {
        "total_horses":     total_raw,
        "overall_win_rate": round(raw_avg_wr, 1),
        "min_n":            min_raw_n,
    }
    try:
        return jsonify({
            'components':      components_list,
            'race_relative':   race_relative,
            'winner_gap':      winner_gap,
            'winner_gap_meta': {
                'total_races':     gap_total_races,
                'top_pick_losses': top_pick_losses,
                'top_pick_wins':   gap_total_races - top_pick_losses,
                'top_pick_sr':     round((gap_total_races - top_pick_losses) / gap_total_races * 100, 1) if gap_total_races else 0,
            },
            'stacking':        stacking_results,
            'scoring_audit':   scoring_audit,
            'raw_factors':     raw_factors,
            'raw_meta':        raw_meta,
            'tp_notes':        tp_notes_list,    # F1 — top pick parsed notes ROI
            'tp_raw':          tp_raw_list,      # F2 — top pick CSV factors ROI
        })
    finally:
        db.session.expunge_all()
        db.session.remove()

@app.route("/api/data/external-factors")
@login_required
def api_external_factors():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403
    
    from flask import jsonify
    
    track_filter = request.args.get('track', '')
    min_score_filter = request.args.get('min_score', type=float)
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    race_id_query = db.session.query(Race.id).join(
        Meeting, Race.meeting_id == Meeting.id
    ).join(
        Horse, Horse.race_id == Race.id
    ).join(
        Result, Result.horse_id == Horse.id
    ).filter(
        Result.finish_position > 0
    )
    
    if track_filter:
        race_id_query = race_id_query.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        race_id_query = race_id_query.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        race_id_query = race_id_query.filter(Meeting.uploaded_at <= date_to)
    
    limit_param = request.args.get('limit', 'all')
    all_race_ids = race_id_query.add_columns(Meeting.uploaded_at).distinct().order_by(Meeting.uploaded_at.desc(), Race.id.desc()).all()
    
    if limit_param == 'all':
        recent_race_ids = [r[0] for r in all_race_ids]
    else:
        limit = int(limit_param) if limit_param.isdigit() else 200
        recent_race_ids = [r[0] for r in all_race_ids[:limit]]
    
    base_query = db.session.query(
        Horse, Prediction, Result, Race, Meeting
    ).join(
        Prediction, Horse.id == Prediction.horse_id
    ).join(
        Result, Horse.id == Result.horse_id
    ).join(
        Race, Horse.race_id == Race.id
    ).join(
        Meeting, Race.meeting_id == Meeting.id
    ).filter(
        Result.finish_position > 0,
        Race.id.in_(recent_race_ids)
    )
    
    all_results = base_query.all()
    
    all_results_data = []
    races_data = {}
    for horse, pred, result, race, meeting in all_results:
        all_results_data.append({
            'horse': horse,
            'prediction': pred,
            'result': result,
            'race': race,
            'meeting': meeting
        })
        
        race_key = (meeting.id, race.race_number)
        if race_key not in races_data:
            races_data[race_key] = []
        races_data[race_key].append({
            'horse': horse,
            'prediction': pred,
            'result': result,
            'race': race,
            'meeting': meeting
        })
    
    external_factors = analyze_external_factors(all_results_data, races_data, stake=10.0)
    class_performance = analyze_race_classes(races_data, stake=10.0)
    
    class_performance_filtered = {k: v for k, v in class_performance.items() if v['runs'] >= 2}

    class_drops = analyze_class_drops(stake=10.0)
    
    result = jsonify({
        'jockeys_reliable': external_factors['jockeys_reliable'],
        'jockeys_limited': external_factors['jockeys_limited'],
        'trainers_reliable': external_factors['trainers_reliable'],
        'trainers_limited': external_factors['trainers_limited'],
        'sires_reliable': external_factors['sires_reliable'],
        'dams_reliable': external_factors['dams_reliable'],
        'barriers': external_factors['barriers'],
        'distances': external_factors['distances'],
        'tracks': external_factors['tracks'],
        'track_conditions': external_factors['track_conditions'],
        'class_performance': class_performance_filtered,
        'class_drops': class_drops
    })
    
    del all_results
    del all_results_data
    del races_data
    del external_factors
    del class_performance
    del class_drops
    import gc
    gc.collect()
    db.session.expunge_all()
    db.session.remove()

    return result
@app.route("/api/data/probability-calibration")
@login_required
def api_probability_calibration():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    track_filter = request.args.get('track', '')
    date_from    = request.args.get('date_from', '')
    date_to      = request.args.get('date_to', '')
    limit_param  = request.args.get('limit', '200')

    from collections import defaultdict

    q = db.session.query(
        Meeting.id,
        Race.race_number,
        Prediction.win_probability,
        Result.finish_position
    ).join(Race,       Race.meeting_id      == Meeting.id
    ).join(Horse,      Horse.race_id        == Race.id
    ).join(Prediction, Prediction.horse_id  == Horse.id
    ).join(Result,     Result.horse_id      == Horse.id
    ).filter(
        Result.finish_position > 0,
        Prediction.win_probability.isnot(None),
        Prediction.win_probability != ''
    )

    if track_filter:
        q = q.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        q = q.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        q = q.filter(Meeting.uploaded_at <= date_to)

    q = q.order_by(Meeting.uploaded_at.desc(), Race.id.desc())
    rows = q.all()

    # Group by race for limit
    races = defaultdict(list)
    race_keys_ordered = []
    for meeting_id, race_num, win_prob, finish_pos in rows:
        key = (meeting_id, race_num)
        if key not in races:
            race_keys_ordered.append(key)
        races[key].append({'win_prob': win_prob, 'finish_pos': finish_pos})

    if limit_param != 'all':
        limit = int(limit_param) if str(limit_param).isdigit() else 200
        allowed = set(race_keys_ordered[:limit])
    else:
        allowed = set(race_keys_ordered)

    bucket_defs = [
        ('50%+',   50, 100),
        ('40-50%', 40, 50),
        ('30-40%', 30, 40),
        ('20-30%', 20, 30),
        ('15-20%', 15, 20),
        ('10-15%', 10, 15),
        ('5-10%',  5,  10),
        ('<5%',    0,  5),
    ]
    buckets = {label: {'label': label, 'min': mn, 'max': mx, 'horses': 0, 'wins': 0}
               for label, mn, mx in bucket_defs}

    total_horses = 0
    skipped = 0

    for key, horse_list in races.items():
        if key not in allowed:
            continue
        for h in horse_list:
            try:
                wp = float(str(h['win_prob']).replace('%', '').strip())
            except (ValueError, TypeError):
                skipped += 1
                continue
            won = h['finish_pos'] == 1
            total_horses += 1
            for label, mn, mx in bucket_defs:
                if mn <= wp < mx or (label == '50%+' and wp >= 50):
                    buckets[label]['horses'] += 1
                    if won:
                        buckets[label]['wins'] += 1
                    break

    results_list = []
    for label, mn, mx in bucket_defs:
        b = buckets[label]
        if b['horses'] == 0:
            continue
        actual_sr = (b['wins'] / b['horses']) * 100
        midpoint = 55 if label == '50%+' else (mn + min(mx, 60)) / 2
        difference = actual_sr - midpoint
        results_list.append({
            'label': label,
            'horses': b['horses'],
            'wins': b['wins'],
            'predicted_midpoint': round(midpoint, 1),
            'actual_strike_rate': round(actual_sr, 1),
            'difference': round(difference, 1),
            'calibrated': abs(difference) <= 5
        })

    if results_list:
        mae = sum(abs(r['difference']) for r in results_list) / len(results_list)
        calibration_grade = 'Excellent' if mae <= 3 else 'Good' if mae <= 6 else 'Fair' if mae <= 10 else 'Poor'
    else:
        mae = None
        calibration_grade = 'No Data'

    return jsonify({
        'buckets': results_list,
        'total_horses': total_horses,
        'skipped': skipped,
        'mae': round(mae, 2) if mae else None,
        'calibration_grade': calibration_grade
    })
@app.route("/api/data/price-analysis")
@login_required
def api_price_analysis():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    track_filter     = request.args.get('track', '')
    min_score_filter = request.args.get('min_score', type=float)
    date_from        = request.args.get('date_from', '')
    date_to          = request.args.get('date_to', '')
    limit_param      = request.args.get('limit', '200')
    top_n            = request.args.get('top_n', 1, type=int)  # how many top-ranked runners to test

    # ── Race ID subquery (no hardcoded date) ──────────────────────────────────
    race_id_query = db.session.query(Race.id).join(
        Meeting, Race.meeting_id == Meeting.id
    ).join(
        Horse, Horse.race_id == Race.id
    ).join(
        Result, Result.horse_id == Horse.id
    ).filter(
        Result.finish_position > 0   # scratches already excluded here
    )

    if track_filter:
        race_id_query = race_id_query.filter(
            Meeting.meeting_name.ilike(f'%{track_filter}%')
        )
    if date_from:
        race_id_query = race_id_query.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        race_id_query = race_id_query.filter(Meeting.uploaded_at <= date_to)

    race_id_query = race_id_query.add_columns(Meeting.uploaded_at) \
        .distinct() \
        .order_by(Meeting.uploaded_at.desc(), Race.id.desc())

    if limit_param != 'all':
        limit = int(limit_param) if str(limit_param).isdigit() else 200
        race_id_query = race_id_query.limit(limit)

    recent_race_ids = [r[0] for r in race_id_query.all()]

    if not recent_race_ids:
        return jsonify({'error': 'No races found'}), 404

    # ── Main query ────────────────────────────────────────────────────────────
    base_query = db.session.query(
        Horse, Prediction, Result, Race, Meeting
    ).join(
        Prediction, Horse.id == Prediction.horse_id
    ).join(
        Result, Horse.id == Result.horse_id
    ).join(
        Race, Horse.race_id == Race.id
    ).join(
        Meeting, Race.meeting_id == Meeting.id
    ).filter(
        Result.finish_position > 0,
        Race.id.in_(recent_race_ids)
    )

    if min_score_filter is not None:
        base_query = base_query.filter(Prediction.score >= min_score_filter)

    all_results = base_query.all()

    # ── Group by race ─────────────────────────────────────────────────────────
    races_data = {}
    for horse, pred, result, race, meeting in all_results:
        race_key = (meeting.id, race.race_number)
        if race_key not in races_data:
            races_data[race_key] = []
        races_data[race_key].append({
            'horse':      horse,
            'prediction': pred,
            'result':     result
        })

    stake = 10.0

    # ── Threshold levels to test ──────────────────────────────────────────────
    thresholds = [0, 5, 10, 15, 20, 25, 30, 40, 50]

    # Accumulators
    # top_n picks overlay tiers (existing buckets)
    overlay_tiers = {
        'overlay_10_20': {'count': 0, 'wins': 0, 'profit': 0},
        'overlay_20_30': {'count': 0, 'wins': 0, 'profit': 0},
        'overlay_30_50': {'count': 0, 'wins': 0, 'profit': 0},
        'overlay_50_plus': {'count': 0, 'wins': 0, 'profit': 0},
        'total_overlays': {'count': 0, 'wins': 0, 'profit': 0},
    }

    # Threshold tuning: top_n picks
    topn_thresholds  = {t: {'count': 0, 'wins': 0, 'profit': 0} for t in thresholds}
    # Threshold tuning: all runners
    all_thresholds   = {t: {'count': 0, 'wins': 0, 'profit': 0} for t in thresholds}

    # All-horse overlay tiers (mirrors overlay_tiers but for every runner)
    all_horse_tiers = {
        'overlay_10_20':  {'count': 0, 'wins': 0, 'profit': 0},
        'overlay_20_30':  {'count': 0, 'wins': 0, 'profit': 0},
        'overlay_30_50':  {'count': 0, 'wins': 0, 'profit': 0},
        'overlay_50_plus':{'count': 0, 'wins': 0, 'profit': 0},
        'total_overlays': {'count': 0, 'wins': 0, 'profit': 0},
    }

    total_compared  = 0
    skipped         = 0
    overlay_examples = []

    for race_key, horses in races_data.items():

        # Full SP coverage check (scratches already excluded by query)
        valid_sp = [h for h in horses if h['result'].sp and h['result'].sp > 0]
        if len(valid_sp) != len(horses):
            skipped += 1
            continue

        # Sort by model score descending
        horses_sorted = sorted(horses, key=lambda x: x['prediction'].score, reverse=True)

        # ── Top-N picks overlay ───────────────────────────────────────────────
        for rank_idx, runner in enumerate(horses_sorted[:top_n]):
            pred   = runner['prediction']
            result = runner['result']

            try:
                predicted_odds = float((pred.predicted_odds or '').replace('$', '').strip())
            except (ValueError, AttributeError):
                continue

            sp = result.sp
            if not sp or sp <= 0 or predicted_odds <= 0:
                continue

            total_compared += 1
            won    = result.finish_position == 1
            profit = (sp * stake - stake) if won else -stake
            edge   = ((sp - predicted_odds) / predicted_odds) * 100

            # Existing tier buckets (top pick only — rank 0)
            if rank_idx == 0:
                if edge >= 10:
                    if edge >= 50:
                        tier = 'overlay_50_plus'
                    elif edge >= 30:
                        tier = 'overlay_30_50'
                    elif edge >= 20:
                        tier = 'overlay_20_30'
                    else:
                        tier = 'overlay_10_20'

                    overlay_tiers[tier]['count']  += 1
                    overlay_tiers[tier]['wins']   += (1 if won else 0)
                    overlay_tiers[tier]['profit'] += profit

                    overlay_tiers['total_overlays']['count']  += 1
                    overlay_tiers['total_overlays']['wins']   += (1 if won else 0)
                    overlay_tiers['total_overlays']['profit'] += profit

                    overlay_examples.append({
                        'horse':       runner['horse'].horse_name,
                        'score':       pred.score,
                        'your_price':  predicted_odds,
                        'sp':          sp,
                        'overlay_pct': round(edge, 1),
                        'won':         won,
                        'profit':      profit,
                        'race_id':     race_key[0],
                        'race_number': race_key[1]
                    })

            # Threshold tuning for top-n
            for t in thresholds:
                if edge >= t:
                    topn_thresholds[t]['count']  += 1
                    topn_thresholds[t]['wins']   += (1 if won else 0)
                    topn_thresholds[t]['profit'] += profit

        # ── All runners overlay ───────────────────────────────────────────────
        for runner in horses_sorted:
            pred_all   = runner['prediction']
            result_all = runner['result']

            try:
                predicted_odds_all = float((pred_all.predicted_odds or '').replace('$', '').strip())
            except (ValueError, AttributeError):
                continue

            sp_all = result_all.sp
            if not sp_all or sp_all <= 0 or predicted_odds_all <= 0:
                continue

            won_all    = result_all.finish_position == 1
            profit_all = (sp_all * stake - stake) if won_all else -stake
            edge_all   = ((sp_all - predicted_odds_all) / predicted_odds_all) * 100

            # All-horse tier buckets
            if edge_all >= 10:
                if edge_all >= 50:
                    tier_all = 'overlay_50_plus'
                elif edge_all >= 30:
                    tier_all = 'overlay_30_50'
                elif edge_all >= 20:
                    tier_all = 'overlay_20_30'
                else:
                    tier_all = 'overlay_10_20'

                all_horse_tiers[tier_all]['count']  += 1
                all_horse_tiers[tier_all]['wins']   += (1 if won_all else 0)
                all_horse_tiers[tier_all]['profit'] += profit_all

                all_horse_tiers['total_overlays']['count']  += 1
                all_horse_tiers['total_overlays']['wins']   += (1 if won_all else 0)
                all_horse_tiers['total_overlays']['profit'] += profit_all

            # Threshold tuning for all runners
            for t in thresholds:
                if edge_all >= t:
                    all_thresholds[t]['count']  += 1
                    all_thresholds[t]['wins']   += (1 if won_all else 0)
                    all_thresholds[t]['profit'] += profit_all

    # ── Calculate rates ───────────────────────────────────────────────────────
    def _calc(d, stake):
        for v in d.values():
            n = v['count']
            v['strike_rate'] = round(v['wins'] / n * 100, 1) if n else 0
            v['roi']         = round(v['profit'] / (n * stake) * 100, 1) if n else 0

    _calc(overlay_tiers, stake)
    _calc(all_horse_tiers, stake)

    def _calc_threshold(d, stake):
        result_list = []
        for t in thresholds:
            v = d[t]
            n = v['count']
            result_list.append({
                'threshold':   t,
                'count':       n,
                'wins':        v['wins'],
                'strike_rate': round(v['wins'] / n * 100, 1) if n else 0,
                'roi':         round(v['profit'] / (n * stake) * 100, 1) if n else 0,
                'profit':      round(v['profit'], 2)
            })
        return result_list

    # ── Finalise overlay examples ─────────────────────────────────────────────
    overlay_examples.sort(key=lambda x: (x['race_id'], x['race_number']), reverse=True)
    overlay_examples = overlay_examples[:10]
    for ex in overlay_examples:
        ex.pop('race_id', None)
        ex.pop('race_number', None)

    payload = {
        # Existing tier buckets (top pick)
        **overlay_tiers,
        'total_compared':          total_compared,
        'skipped_incomplete_races': skipped,
        'overlay_examples':        overlay_examples,

        # All-horse tier breakdown (NEW — was a single aggregate before)
        'all_horse_tiers':         all_horse_tiers,

        # Threshold tuning tables (NEW)
        'threshold_tuning': {
            'top_n':      top_n,
            'top_n_picks': _calc_threshold(topn_thresholds, stake),
            'all_runners': _calc_threshold(all_thresholds, stake),
        }
    }

    result = jsonify(payload)
    del all_results, races_data, overlay_tiers, all_horse_tiers
    del topn_thresholds, all_thresholds, overlay_examples
    import gc
    gc.collect()
    db.session.expunge_all()
    db.session.remove()

    return result
@app.route("/api/data/pnl-over-time")
@login_required
def api_pnl_over_time():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    track_filter = request.args.get('track', '')
    date_from    = request.args.get('date_from', '')
    date_to      = request.args.get('date_to', '')
    limit_param  = request.args.get('limit', '200')

    # SINGLE QUERY — no IN clause, only fetch what we need
    q = db.session.query(
        Meeting.id,
        Meeting.uploaded_at,
        Meeting.meeting_name,
        Race.race_number,
        Prediction.score,
        Result.finish_position,
        Result.sp
    ).join(Race,      Race.meeting_id      == Meeting.id
    ).join(Horse,     Horse.race_id        == Race.id
    ).join(Prediction,Prediction.horse_id  == Horse.id
    ).join(Result,    Result.horse_id      == Horse.id
    ).filter(Result.finish_position > 0)

    if track_filter:
        q = q.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        q = q.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        q = q.filter(Meeting.uploaded_at <= date_to)

    q = q.order_by(Meeting.uploaded_at.asc(), Race.race_number.asc())

    rows = q.all()

    # Group by race, then apply limit AFTER grouping
    from collections import defaultdict
    races = defaultdict(list)
    race_meta = {}
    for meeting_id, uploaded_at, meeting_name, race_num, score, finish_pos, sp in rows:
        key = (meeting_id, race_num)
        races[key].append({'score': score, 'finish_pos': finish_pos, 'sp': sp or 0})
        if key not in race_meta:
            race_meta[key] = (uploaded_at, meeting_name, race_num)

    # Apply limit by taking last N races chronologically
    all_keys = list(races.keys())
    if limit_param != 'all':
        limit = int(limit_param) if str(limit_param).isdigit() else 200
        all_keys = all_keys[-limit:]

    stake = 10.0
    cumulative = 0.0
    data_points = []
    monthly = {}

    for key in all_keys:
        horses = races[key]
        top = max(horses, key=lambda x: x['score'])
        won   = top['finish_pos'] == 1
        sp    = top['sp']
        profit = (sp * stake - stake) if won else -stake
        cumulative += profit

        uploaded_at, meeting_name, race_num = race_meta[key]
        date_str  = uploaded_at.strftime('%Y-%m-%d') if uploaded_at else ''
        month_key = uploaded_at.strftime('%Y-%m')    if uploaded_at else 'Unknown'

        data_points.append({
            'date': date_str, 'meeting': meeting_name, 'race': race_num,
            'profit': round(profit, 2), 'cumulative': round(cumulative, 2), 'won': won
        })

        if month_key not in monthly:
            monthly[month_key] = {'races': 0, 'wins': 0, 'profit': 0.0}
        monthly[month_key]['races'] += 1
        if won:
            monthly[month_key]['wins'] += 1
        monthly[month_key]['profit'] += profit

    monthly_list = []
    for month, s in sorted(monthly.items()):
        monthly_list.append({
            'month': month,
            'races': s['races'],
            'wins':  s['wins'],
            'roi':   round(s['profit'] / (s['races'] * stake) * 100, 1) if s['races'] else 0,
            'strike_rate': round(s['wins'] / s['races'] * 100, 1) if s['races'] else 0,
            'profit': round(s['profit'], 2)
        })

    return jsonify({
        'data_points': data_points,
        'monthly': monthly_list,
        'total_profit': round(cumulative, 2),
        'total_races': len(data_points)
    })

@app.route("/api/data/sole-leader-analysis")
@login_required
def api_sole_leader_analysis():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    import json as _json
    from collections import defaultdict

    stake = 10.0

    track_filter = request.args.get('track', '')
    date_from    = request.args.get('date_from', '')
    date_to      = request.args.get('date_to', '')
    limit_param  = request.args.get('limit', '200')

    race_id_query = db.session.query(Race.id).join(
        Meeting, Race.meeting_id == Meeting.id
    ).join(
        Horse, Horse.race_id == Race.id
    ).join(
        Result, Result.horse_id == Horse.id
    ).filter(
        Result.finish_position > 0,
        Race.speed_maps_json.isnot(None)
    )

    if track_filter:
        race_id_query = race_id_query.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        race_id_query = race_id_query.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        race_id_query = race_id_query.filter(Meeting.uploaded_at <= date_to)

    all_race_ids = race_id_query.add_columns(Meeting.uploaded_at).distinct().order_by(
        Meeting.uploaded_at.desc(), Race.id.desc()
    ).all()

    if limit_param == 'all':
        recent_race_ids = [r[0] for r in all_race_ids]
    else:
        limit = int(limit_param) if limit_param.isdigit() else 200
        recent_race_ids = [r[0] for r in all_race_ids[:limit]]

    all_results = db.session.query(
        Horse, Prediction, Result, Race, Meeting
    ).join(
        Prediction, Horse.id == Prediction.horse_id
    ).join(
        Result, Horse.id == Result.horse_id
    ).join(
        Race, Horse.race_id == Race.id
    ).join(
        Meeting, Race.meeting_id == Meeting.id
    ).filter(
        Result.finish_position > 0,
        Race.id.in_(recent_race_ids)
    ).all()

    races_data = {}
    for horse, pred, result, race, meeting in all_results:
        race_key = (meeting.id, race.race_number)
        if race_key not in races_data:
            races_data[race_key] = []
        races_data[race_key].append({
            'horse':      horse,
            'prediction': pred,
            'result':     result,
            'race':       race,
            'meeting':    meeting
        })

    results = {
        'sole_leader':      {'races': 0, 'wins': 0, 'profit': 0.0, 'examples': []},
        'contested_leader': {'races': 0, 'wins': 0, 'profit': 0.0},
        'no_leader':        {'races': 0, 'wins': 0, 'profit': 0.0},
    }
    dist_breakdown       = defaultdict(lambda: {'races': 0, 'wins': 0, 'profit': 0.0})
    field_size_breakdown = defaultdict(lambda: {'races': 0, 'wins': 0, 'profit': 0.0})
    sp_breakdown         = defaultdict(lambda: {'races': 0, 'wins': 0, 'profit': 0.0})
    pick_is_leader_breakdown = {
        'yes': {'races': 0, 'wins': 0, 'profit': 0.0},
        'no':  {'races': 0, 'wins': 0, 'profit': 0.0},
    }

    for race_key, horses in races_data.items():
        if not horses:
            continue

        race = horses[0]['race']

        try:
            smap = race.speed_maps_json
            if isinstance(smap, str):
                smap = _json.loads(smap)
            items = smap.get('payLoad', [{}])[0].get('items', [])
        except Exception:
            continue

        leaders = []
        for item in items:
            try:
                settle = int(str(item.get('settle', 99)).split('/')[0].strip())
            except (ValueError, TypeError):
                continue
            if settle == 1:
                leaders.append(normalize_runner_name(item.get('runnerName', '')))

        if len(leaders) == 0:
            bucket = 'no_leader'
        elif len(leaders) == 1:
            bucket = 'sole_leader'
        else:
            bucket = 'contested_leader'

        horses_sorted = sorted(horses, key=lambda x: x['prediction'].score, reverse=True)
        top = horses_sorted[0]

        won    = top['result'].finish_position == 1
        sp     = top['result'].sp or 0
        profit = (sp * stake - stake) if won else -stake

        results[bucket]['races']  += 1
        results[bucket]['wins']   += 1 if won else 0
        results[bucket]['profit'] += profit

        if bucket == 'sole_leader':

            # Distance
            try:
                dist = int(str(race.distance or '0').replace('m', '').strip())
                if dist <= 1200:   dl = 'Sprint (≤1200m)'
                elif dist <= 1700: dl = 'Mile (1300-1700m)'
                elif dist <= 2200: dl = 'Middle (1800-2200m)'
                else:              dl = 'Staying (2400m+)'
            except (ValueError, TypeError):
                dl = 'Unknown'
            dist_breakdown[dl]['races']  += 1
            dist_breakdown[dl]['wins']   += 1 if won else 0
            dist_breakdown[dl]['profit'] += profit

            # Field size
            field_size = len(horses)
            if field_size <= 7:    fs_label = 'Small (≤7)'
            elif field_size <= 11: fs_label = 'Medium (8-11)'
            elif field_size <= 15: fs_label = 'Large (12-15)'
            else:                  fs_label = 'Very Large (16+)'
            field_size_breakdown[fs_label]['races']  += 1
            field_size_breakdown[fs_label]['wins']   += 1 if won else 0
            field_size_breakdown[fs_label]['profit'] += profit

            # SP bracket
            if sp <= 0:     sp_label = 'No SP'
            elif sp < 2.0:  sp_label = 'Odds-on (<$2)'
            elif sp < 3.0:  sp_label = '$2-$2.99'
            elif sp < 5.0:  sp_label = '$3-$4.99'
            elif sp < 8.0:  sp_label = '$5-$7.99'
            elif sp < 15.0: sp_label = '$8-$14.99'
            else:           sp_label = '$15+'
            sp_breakdown[sp_label]['races']  += 1
            sp_breakdown[sp_label]['wins']   += 1 if won else 0
            sp_breakdown[sp_label]['profit'] += profit

            # Pick is leader
            top_norm       = normalize_runner_name(top['horse'].horse_name)
            pick_is_leader = top_norm in leaders
            pk = 'yes' if pick_is_leader else 'no'
            pick_is_leader_breakdown[pk]['races']  += 1
            pick_is_leader_breakdown[pk]['wins']   += 1 if won else 0
            pick_is_leader_breakdown[pk]['profit'] += profit

            # Examples
            if len(results['sole_leader']['examples']) < 10:
                results['sole_leader']['examples'].append({
                    'meeting':            top['meeting'].meeting_name,
                    'race':               race.race_number,
                    'top_pick':           top['horse'].horse_name,
                    'top_pick_is_leader': pick_is_leader,
                    'leader':             leaders[0] if leaders else '',
                    'won':                won,
                    'sp':                 sp,
                })

    for bucket in results.values():
        n = bucket['races']
        bucket['strike_rate'] = round(bucket['wins'] / n * 100, 1) if n else 0
        bucket['roi']         = round(bucket['profit'] / (n * stake) * 100, 1) if n else 0
        bucket['profit']      = round(bucket['profit'], 2)

    def build_list(breakdown, order):
        out = []
        for label in order:
            stats = breakdown.get(label)
            if not stats or stats['races'] == 0:
                continue
            n = stats['races']
            out.append({
                'label':       label,
                'races':       n,
                'wins':        stats['wins'],
                'strike_rate': round(stats['wins'] / n * 100, 1),
                'roi':         round(stats['profit'] / (n * stake) * 100, 1),
                'profit':      round(stats['profit'], 2),
            })
        return out

    dist_list = build_list(dist_breakdown, ['Staying (2400m+)', 'Mile (1300-1700m)', 'Sprint (≤1200m)', 'Middle (1800-2200m)', 'Unknown'])
    dist_list.sort(key=lambda x: x['roi'], reverse=True)

    fs_list = build_list(field_size_breakdown, ['Small (≤7)', 'Medium (8-11)', 'Large (12-15)', 'Very Large (16+)'])

    sp_list = build_list(sp_breakdown, ['Odds-on (<$2)', '$2-$2.99', '$3-$4.99', '$5-$7.99', '$8-$14.99', '$15+', 'No SP'])

    pick_list = []
    for label, key, desc in [
        ('✅ Pick IS Leader',     'yes', 'Your top pick is the sole leader'),
        ('❌ Pick is NOT Leader', 'no',  'Sole leader is a different horse'),
    ]:
        stats = pick_is_leader_breakdown[key]
        n = stats['races']
        if n == 0:
            continue
        pick_list.append({
            'label':       label,
            'desc':        desc,
            'races':       n,
            'wins':        stats['wins'],
            'strike_rate': round(stats['wins'] / n * 100, 1),
            'roi':         round(stats['profit'] / (n * stake) * 100, 1),
            'profit':      round(stats['profit'], 2),
        })

    del all_results, races_data
    import gc
    gc.collect()
    db.session.expunge_all()
    db.session.remove()

    return jsonify({
        'buckets':                    results,
        'distance_breakdown':         dist_list,
        'field_size_breakdown':       fs_list,
        'sp_breakdown':               sp_list,
        'pick_is_leader_breakdown':   pick_list,
        'total_races_with_speedmaps': len(recent_race_ids),
    })
@app.route("/api/data/field-size")
@login_required
def api_field_size():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    track_filter = request.args.get('track', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    limit_param = request.args.get('limit', 'all')

    race_id_query = db.session.query(Race.id).join(
        Meeting, Race.meeting_id == Meeting.id
    ).join(
        Horse, Horse.race_id == Race.id
    ).join(
        Result, Result.horse_id == Horse.id
    ).filter(Result.finish_position > 0)

    if track_filter:
        race_id_query = race_id_query.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        race_id_query = race_id_query.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        race_id_query = race_id_query.filter(Meeting.uploaded_at <= date_to)

    all_race_ids = race_id_query.add_columns(Meeting.uploaded_at).distinct().order_by(
        Meeting.uploaded_at.desc(), Race.id.desc()
    ).all()

    if limit_param == 'all':
        recent_race_ids = [r[0] for r in all_race_ids]
    else:
        limit = int(limit_param) if limit_param.isdigit() else 200
        recent_race_ids = [r[0] for r in all_race_ids[:limit]]

    all_results = db.session.query(
        Horse, Prediction, Result, Race, Meeting
    ).join(
        Prediction, Horse.id == Prediction.horse_id
    ).join(
        Result, Horse.id == Result.horse_id
    ).join(
        Race, Horse.race_id == Race.id
    ).join(
        Meeting, Race.meeting_id == Meeting.id
    ).filter(
        Result.finish_position > 0,
        Race.id.in_(recent_race_ids)
    ).all()

    races_data = {}
    for horse, pred, result, race, meeting in all_results:
        race_key = (meeting.id, race.race_number)
        if race_key not in races_data:
            races_data[race_key] = []
        races_data[race_key].append({'horse': horse, 'prediction': pred, 'result': result})

    stake = 10.0
    buckets = {
        'Small (≤7)':       {'races': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Medium (8-11)':    {'races': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Large (12-15)':    {'races': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Very Large (16+)': {'races': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
    }

    for race_key, horses in races_data.items():
        active = [h for h in horses if h['result'].finish_position > 0]
        field_size = len(active)

        if field_size <= 7:
            bucket = 'Small (≤7)'
        elif field_size <= 11:
            bucket = 'Medium (8-11)'
        elif field_size <= 15:
            bucket = 'Large (12-15)'
        else:
            bucket = 'Very Large (16+)'

        horses_sorted = sorted(active, key=lambda x: x['prediction'].score, reverse=True)
        top = horses_sorted[0]
        won = top['result'].finish_position == 1
        placed = top['result'].finish_position in [1, 2, 3]
        sp = top['result'].sp or 0
        profit = (sp * stake - stake) if won else -stake

        buckets[bucket]['races'] += 1
        if won:
            buckets[bucket]['wins'] += 1
        if placed:
            buckets[bucket]['places'] += 1
        buckets[bucket]['profit'] += profit

    result_list = []
    for label, stats in buckets.items():
        if stats['races'] > 0:
            result_list.append({
                'label': label,
                'races': stats['races'],
                'wins': stats['wins'],
                'places': stats['places'],
                'strike_rate': round(stats['wins'] / stats['races'] * 100, 1),
                'place_rate': round(stats['places'] / stats['races'] * 100, 1),
                'profit': round(stats['profit'], 2),
                'roi': round(stats['profit'] / (stats['races'] * stake) * 100, 1)
            })

    result = jsonify({'field_sizes': result_list})
    del all_results, races_data, result_list
    import gc
    gc.collect()
    db.session.expunge_all()
    db.session.remove()
    return result


@app.route("/api/data/days-since-run")
@login_required
def api_days_since_run():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    track_filter = request.args.get('track', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    limit_param = request.args.get('limit', 'all')

    race_id_query = db.session.query(Race.id).join(
        Meeting, Race.meeting_id == Meeting.id
    ).join(
        Horse, Horse.race_id == Race.id
    ).join(
        Result, Result.horse_id == Horse.id
    ).filter(Result.finish_position > 0)

    if track_filter:
        race_id_query = race_id_query.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        race_id_query = race_id_query.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        race_id_query = race_id_query.filter(Meeting.uploaded_at <= date_to)

    all_race_ids = race_id_query.add_columns(Meeting.uploaded_at).distinct().order_by(
        Meeting.uploaded_at.desc(), Race.id.desc()
    ).all()

    if limit_param == 'all':
        recent_race_ids = [r[0] for r in all_race_ids]
    else:
        limit = int(limit_param) if limit_param.isdigit() else 200
        recent_race_ids = [r[0] for r in all_race_ids[:limit]]

    all_results = db.session.query(
        Horse, Prediction, Result
    ).join(
        Prediction, Horse.id == Prediction.horse_id
    ).join(
        Result, Horse.id == Result.horse_id
    ).join(
        Race, Horse.race_id == Race.id
    ).filter(
        Result.finish_position > 0,
        Race.id.in_(recent_race_ids)
    ).all()

    import re as _re
    stake = 10.0
    buckets = {
        'First Start':          {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Quick Back-up (≤7d)':  {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Short (8-14d)':        {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Normal (15-21d)':      {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Normal (22-28d)':      {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Fresh (29-44d)':       {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Fresh (45-59d)':       {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Resuming (60-89d)':    {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Spell (90-119d)':      {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Spell (120-149d)':     {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Long Spell (150-199d)':{'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Long Spell (200-249d)':{'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Extended (250-364d)':  {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Extended (365d+)':     {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'Unknown':              {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
    }

    for horse, pred, result in all_results:
        won = result.finish_position == 1
        placed = result.finish_position in [1, 2, 3]
        sp = result.sp or 0
        profit = (sp * stake - stake) if won else -stake

        days = None
        csv_data = horse.csv_data or {}
        raw_days = csv_data.get('days since last run', '') or csv_data.get('days_since_run', '')
        if raw_days:
            try:
                days = int(float(str(raw_days).strip()))
            except (ValueError, TypeError):
                pass

        if days is None and pred.notes:
            match = _re.search(r'(\d+)\s*days?\s*since\s*(last\s*)?run', pred.notes, _re.IGNORECASE)
            if match:
                try:
                    days = int(match.group(1))
                except ValueError:
                    pass

        if days is None:
            bucket = 'Unknown'
        elif days == 0:
            bucket = 'First Start'
        elif days <= 7:
            bucket = 'Quick Back-up (≤7d)'
        elif days <= 14:
            bucket = 'Short (8-14d)'
        elif days <= 21:
            bucket = 'Normal (15-21d)'
        elif days <= 28:
            bucket = 'Normal (22-28d)'
        elif days <= 44:
            bucket = 'Fresh (29-44d)'
        elif days <= 59:
            bucket = 'Fresh (45-59d)'
        elif days <= 89:
            bucket = 'Resuming (60-89d)'
        elif days <= 119:
            bucket = 'Spell (90-119d)'
        elif days <= 149:
            bucket = 'Spell (120-149d)'
        elif days <= 199:
            bucket = 'Long Spell (150-199d)'
        elif days <= 249:
            bucket = 'Long Spell (200-249d)'
        elif days <= 364:
            bucket = 'Extended (250-364d)'
        else:
            bucket = 'Extended (365d+)'

        buckets[bucket]['runs'] += 1
        if won:
            buckets[bucket]['wins'] += 1
        if placed:
            buckets[bucket]['places'] += 1
        buckets[bucket]['profit'] += profit

    result_list = []
    for label, stats in buckets.items():
        if stats['runs'] > 0:
            result_list.append({
                'label': label,
                'runs': stats['runs'],
                'wins': stats['wins'],
                'places': stats['places'],
                'strike_rate': round(stats['wins'] / stats['runs'] * 100, 1),
                'place_rate': round(stats['places'] / stats['runs'] * 100, 1),
                'profit': round(stats['profit'], 2),
                'roi': round(stats['profit'] / (stats['runs'] * stake) * 100, 1)
            })

    result = jsonify({'buckets': result_list})
    del all_results, result_list
    import gc
    gc.collect()
    db.session.expunge_all()
    db.session.remove()
    return result


@app.route("/api/data/market-divergence")
@login_required
def api_market_divergence():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    track_filter = request.args.get('track', '')
    date_from    = request.args.get('date_from', '')
    date_to      = request.args.get('date_to', '')
    limit_param  = request.args.get('limit', '200')
    stake        = 10.0

    race_id_q = db.session.query(Race.id).join(
        Meeting, Race.meeting_id == Meeting.id
    ).join(
        Horse, Horse.race_id == Race.id
    ).join(
        Result, Result.horse_id == Horse.id
    ).filter(Result.finish_position > 0)

    if track_filter:
        race_id_q = race_id_q.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        race_id_q = race_id_q.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        race_id_q = race_id_q.filter(Meeting.uploaded_at <= date_to)

    all_ids = race_id_q.add_columns(Meeting.uploaded_at).distinct().order_by(
        Meeting.uploaded_at.desc(), Race.id.desc()
    ).all()

    if limit_param == 'all':
        race_ids = [r[0] for r in all_ids]
    else:
        limit = int(limit_param) if limit_param.isdigit() else 200
        race_ids = [r[0] for r in all_ids[:limit]]

    rows = db.session.query(
        Horse, Prediction, Result, Race, Meeting
    ).join(
        Prediction, Horse.id == Prediction.horse_id
    ).join(
        Result, Horse.id == Result.horse_id
    ).join(
        Race, Horse.race_id == Race.id
    ).join(
        Meeting, Race.meeting_id == Meeting.id
    ).filter(
        Result.finish_position > 0,
        Race.id.in_(race_ids)
    ).all()

    from collections import defaultdict
    races_map = defaultdict(list)
    for horse, pred, result, race, meeting in rows:
        key = (meeting.id, race.race_number)
        races_map[key].append({
            'horse_id': horse.id,
            'score':    pred.score,
            'sp':       result.sp or 999,
            'result':   result,
        })

    stats = {
        'agree':    {'races': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
        'disagree': {'races': 0, 'wins': 0, 'places': 0, 'profit': 0.0},
    }
    skipped = 0

    for race_data in races_map.values():
        valid = [h for h in race_data if h['sp'] < 900]
        if len(valid) < 2:
            skipped += 1
            continue

        top_pick   = max(race_data, key=lambda x: x['score'])
        market_fav = min(valid, key=lambda x: x['sp'])

        won    = top_pick['result'].finish_position == 1
        placed = top_pick['result'].finish_position in [1, 2, 3]
        sp     = top_pick['result'].sp or 0
        profit = (sp * stake - stake) if won else -stake

        bucket = 'agree' if top_pick['horse_id'] == market_fav['horse_id'] else 'disagree'

        stats[bucket]['races']  += 1
        stats[bucket]['wins']   += (1 if won else 0)
        stats[bucket]['places'] += (1 if placed else 0)
        stats[bucket]['profit'] += profit

    for b, s in stats.items():
        n = s['races']
        s['strike_rate'] = round(s['wins']   / n * 100, 1) if n else 0
        s['place_rate']  = round(s['places'] / n * 100, 1) if n else 0
        s['roi']         = round(s['profit'] / (n * stake) * 100, 1) if n else 0

    result = jsonify({'market_divergence': stats, 'skipped_races': skipped})
    del rows, races_map
    import gc
    gc.collect()
    db.session.expunge_all()
    db.session.remove()
    return result


@app.route("/api/data/monthly-performance")
@login_required
def api_monthly_performance():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from collections import defaultdict

    track_filter = request.args.get('track', '')
    date_from    = request.args.get('date_from', '')
    date_to      = request.args.get('date_to', '')
    limit_param  = request.args.get('limit', '500')
    stake        = 10.0

    q = db.session.query(
        Meeting.id,
        Meeting.uploaded_at,
        Meeting.date,
        Race.race_number,
        Prediction.score,
        Result.finish_position,
        Result.sp
    ).join(Race,       Race.meeting_id       == Meeting.id
    ).join(Horse,      Horse.race_id         == Race.id
    ).join(Prediction, Prediction.horse_id   == Horse.id
    ).join(Result,     Result.horse_id       == Horse.id
    ).filter(Result.finish_position > 0)

    if track_filter:
        q = q.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        q = q.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        q = q.filter(Meeting.uploaded_at <= date_to)

    q = q.order_by(Meeting.uploaded_at.asc(), Race.id.asc())

    rows = q.all()

    # Group into races first
    races = defaultdict(list)
    race_keys_ordered = []
    for meeting_id, uploaded_at, meeting_date, race_num, score, finish_pos, sp in rows:
        key = (meeting_id, race_num)
        if key not in races:
            race_keys_ordered.append(key)

        try:
            if meeting_date:
                period = meeting_date.strftime('%Y-%m')
            elif uploaded_at:
                period = uploaded_at.strftime('%Y-%m')
            else:
                continue
        except Exception:
            continue

        races[key].append({
            'period':     period,
            'score':      score,
            'finish_pos': finish_pos,
            'sp':         sp or 0
        })

    # Apply limit by race count
    if limit_param != 'all':
        limit = int(limit_param) if str(limit_param).isdigit() else 500
        race_keys_ordered = race_keys_ordered[:limit]

    monthly = defaultdict(lambda: {'races': 0, 'wins': 0, 'places': 0, 'profit': 0.0})

    for key in race_keys_ordered:
        race_data = races[key]
        top    = max(race_data, key=lambda x: x['score'])
        period = top['period']
        won    = top['finish_pos'] == 1
        placed = top['finish_pos'] in [1, 2, 3]
        sp     = top['sp']
        profit = (sp * stake - stake) if won else -stake

        monthly[period]['races']  += 1
        monthly[period]['wins']   += (1 if won else 0)
        monthly[period]['places'] += (1 if placed else 0)
        monthly[period]['profit'] += profit

    result_list = []
    for period in sorted(monthly.keys()):
        s = monthly[period]
        n = s['races']
        result_list.append({
            'period':      period,
            'races':       n,
            'wins':        s['wins'],
            'places':      s['places'],
            'strike_rate': round(s['wins']   / n * 100, 1) if n else 0,
            'place_rate':  round(s['places'] / n * 100, 1) if n else 0,
            'roi':         round(s['profit'] / (n * stake) * 100, 1) if n else 0,
            'profit':      round(s['profit'], 2),
        })

    return jsonify({'monthly': result_list})

@app.route("/api/data/pfai-analysis")
@login_required
def api_pfai_analysis():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    import re
    from collections import defaultdict

    PFAI_RE = re.compile(
        r'Analyzer Score \(normalized\): ([\d.]+).*?'
        r'PFAI Score: ([\d.]+).*?'
        r'Final Blended Score: ([\d.]+)',
        re.DOTALL
    )

    # Pull only PFAI era records — filter at SQL level first
    rows = db.session.query(
        Meeting.id,
        Race.id,
        Race.race_number,
        Horse.horse_name,
        Prediction.score,
        Prediction.notes,
        Result.finish_position,
        Result.sp
    ).join(Race,       Race.meeting_id     == Meeting.id
    ).join(Horse,      Horse.race_id       == Race.id
    ).join(Prediction, Prediction.horse_id == Horse.id
    ).join(Result,     Result.horse_id     == Horse.id
    ).filter(
        Result.finish_position > 0,
        Prediction.notes.like('%=== PFAI BLEND ===%')
    ).order_by(Meeting.uploaded_at.desc(), Race.id.desc()).all()

    # Group by race, parse blend scores from notes
    races = defaultdict(list)
    race_keys_ordered = []

    for meeting_id, race_id, race_num, horse_name, score, notes, finish_pos, sp in rows:
        key = (meeting_id, race_id)
        if key not in races:
            race_keys_ordered.append(key)

        match = PFAI_RE.search(notes or '')
        if not match:
            # Mark race as incomplete — will be excluded
            races[key].append(None)
            continue

        analyzer_norm = float(match.group(1))
        pfai_score    = float(match.group(2))
        blended_final = float(match.group(3))

        races[key].append({
            'horse_name':    horse_name,
            'analyzer_norm': analyzer_norm,
            'pfai_score':    pfai_score,
            'blended_final': blended_final,
            'finish_pos':    finish_pos,
            'sp':            sp or 0
        })

    # Exclude any race where even one horse is missing the blend block
    clean_race_keys = [
        k for k in race_keys_ordered
        if all(h is not None for h in races[k]) and len(races[k]) >= 2
    ]

    # ── WEIGHT SIMULATIONS ──────────────────────────────────────────────
    weightings = [
    ('100/0  (Pure Analyzer)', 1.0,   0.0  ),
    ('95/5',                   0.95,  0.05 ),
    ('90/10',                  0.9,   0.1  ),
    ('85/15',                  0.85,  0.15 ),
    ('80/20',                  0.8,   0.2  ),
    ('75/25',                  0.75,  0.25 ),
    ('70/30  (Current)',       0.7,   0.3  ),
    ('65/35',                  0.65,  0.35 ),
    ('60/40',                  0.6,   0.4  ),
    ('55/45',                  0.55,  0.45 ),
    ('50/50',                  0.5,   0.5  ),
    ('45/55',                  0.45,  0.55 ),
    ('40/60',                  0.4,   0.6  ),
    ('35/65',                  0.35,  0.65 ),
    ('30/70',                  0.3,   0.7  ),
    ('25/75',                  0.25,  0.75 ),
    ('20/80',                  0.2,   0.8  ),
    ('15/85',                  0.15,  0.85 ),
    ('10/90',                  0.1,   0.9  ),
    ('5/95',                   0.05,  0.95 ),
    ('0/100  (Pure PFAI)',     0.0,   1.0  ),
]

    stake = 10.0
    weight_results = {}

    for label, w_analyzer, w_pfai in weightings:
        races_count = 0
        wins        = 0
        profit      = 0.0
        winner_sps  = []

        for key in clean_race_keys:
            horses = races[key]

            # Simulate score for this weighting
            for h in horses:
                h['sim_score'] = (h['analyzer_norm'] * w_analyzer) + (h['pfai_score'] * w_pfai)

            top = max(horses, key=lambda x: x['sim_score'])
            races_count += 1

            if top['finish_pos'] == 1:
                wins += 1
                profit += (top['sp'] * stake - stake)
                if top['sp'] > 0:
                    winner_sps.append(top['sp'])
            else:
                profit -= stake

        weight_results[label] = {
            'races':        races_count,
            'wins':         wins,
            'strike_rate':  round(wins / races_count * 100, 1) if races_count else 0,
            'roi':          round(profit / (races_count * stake) * 100, 1) if races_count else 0,
            'profit':       round(profit, 2),
            'avg_winner_sp': round(sum(winner_sps) / len(winner_sps), 2) if winner_sps else 0,
        }

    # ── AGREEMENT + DISAGREEMENT ANALYSIS ─────────────────────────────
    analyzer_right = 0
    pfai_right     = 0
    both_wrong     = 0
    disagreements  = 0
    
    agree_races  = 0
    agree_wins   = 0
    agree_profit = 0.0
    agree_sps    = []

    for key in clean_race_keys:
        horses = races[key]

        analyzer_top = max(horses, key=lambda x: x['analyzer_norm'])
        pfai_top     = max(horses, key=lambda x: x['pfai_score'])
        blended_top  = max(horses, key=lambda x: x['blended_final'])

        if analyzer_top['horse_name'] == pfai_top['horse_name']:
            # Signals agree
            agree_races += 1
            if blended_top['finish_pos'] == 1:
                agree_wins += 1
                agree_profit += (blended_top['sp'] * stake - stake)
                if blended_top['sp'] > 0:
                    agree_sps.append(blended_top['sp'])
            else:
                agree_profit -= stake
        else:
            # Signals disagree
            disagreements += 1
            analyzer_won = analyzer_top['finish_pos'] == 1
            pfai_won     = pfai_top['finish_pos'] == 1

            if analyzer_won:
                analyzer_right += 1
            elif pfai_won:
                pfai_right += 1
            else:
                both_wrong += 1

    agree_strike  = round(agree_wins / agree_races * 100, 1) if agree_races else 0
    agree_roi     = round(agree_profit / (agree_races * stake) * 100, 1) if agree_races else 0
    agree_avg_sp  = round(sum(agree_sps) / len(agree_sps), 2) if agree_sps else 0

    disagreement_results = {
        'total_disagreements':  disagreements,
        'analyzer_right':       analyzer_right,
        'pfai_right':           pfai_right,
        'both_wrong':           both_wrong,
        'analyzer_right_pct':   round(analyzer_right / disagreements * 100, 1) if disagreements else 0,
        'pfai_right_pct':       round(pfai_right     / disagreements * 100, 1) if disagreements else 0,
        'both_wrong_pct':       round(both_wrong     / disagreements * 100, 1) if disagreements else 0,
        'agree_races':          agree_races,
        'agree_wins':           agree_wins,
        'agree_strike':         agree_strike,
        'agree_roi':            round(agree_roi, 1),
        'agree_profit':         round(agree_profit, 2),
        'agree_avg_sp':         agree_avg_sp,
    }

    # ── PFAI SCORE BAND ANALYSIS ────────────────────────────────────────
    # Does high PFAI confidence on the top blended pick produce better results?
    pfai_bands = {
        'Low (0-40)':   {'races': 0, 'wins': 0, 'profit': 0.0},
        'Mid (40-70)':  {'races': 0, 'wins': 0, 'profit': 0.0},
        'High (70-100)':{'races': 0, 'wins': 0, 'profit': 0.0},
    }

    for key in clean_race_keys:
        horses = races[key]
        # Use current blended score to identify top pick (as per live system)
        top = max(horses, key=lambda x: x['blended_final'])
        pfai = top['pfai_score']

        band = ('High (70-100)' if pfai >= 70 else 'Mid (40-70)' if pfai >= 40 else 'Low (0-40)')
        pfai_bands[band]['races'] += 1

        if top['finish_pos'] == 1:
            pfai_bands[band]['wins'] += 1
            pfai_bands[band]['profit'] += (top['sp'] * stake - stake)
        else:
            pfai_bands[band]['profit'] -= stake

    for band in pfai_bands.values():
        r = band['races']
        band['strike_rate'] = round(band['wins'] / r * 100, 1) if r else 0
        band['roi']         = round(band['profit'] / (r * stake) * 100, 1) if r else 0
        band['profit']      = round(band['profit'], 2)

    # ── OVERVIEW ────────────────────────────────────────────────────────
    total_horses = sum(len(races[k]) for k in clean_race_keys)

    return jsonify({
        'overview': {
            'total_horses': total_horses,
            'total_races':  len(clean_race_keys),
        },
        'weight_simulation': weight_results,
        'disagreements':     disagreement_results,
        'pfai_bands':        pfai_bands,
    })

@app.route("/api/data/combination-analysis")
@login_required
def api_combination_analysis():
    """
    Finds positive-ROI single factors across ALL horses (not just top picks),
    then identifies combinations of 2-5 factors that produce positive ROI.

    Purpose: discover what your scoring model is MISSING — factors that predict
    winners but aren't being weighted heavily enough in the analyzer.
    """
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    track_filter    = request.args.get('track', '')
    date_from       = request.args.get('date_from', '')
    date_to         = request.args.get('date_to', '')
    limit_param     = request.args.get('limit', 'all')
    min_appearances = int(request.args.get('min_appearances', 10))
    stake           = 10.0

    # ── 1. Pull ALL horse rows across all races ────────────────────────────────
    q = db.session.query(
        Meeting.id,
        Meeting.meeting_name,
        Meeting.uploaded_at,
        Race.id.label('race_id'),
        Race.race_number,
        Race.distance,
        Race.race_class,
        Race.track_condition,
        Horse.id.label('horse_id'),
        Horse.horse_name,
        Horse.jockey,
        Horse.trainer,
        Horse.csv_data,
        Prediction.score,
        Prediction.win_probability,
        Prediction.predicted_odds,
        Prediction.notes,
        Result.finish_position,
        Result.sp
    ).join(Race,       Race.meeting_id      == Meeting.id
    ).join(Horse,      Horse.race_id        == Race.id
    ).join(Prediction, Prediction.horse_id  == Horse.id
    ).join(Result,     Result.horse_id      == Horse.id
    ).filter(Result.finish_position > 0)

    if track_filter:
        q = q.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        q = q.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        q = q.filter(Meeting.uploaded_at <= date_to)

    q = q.order_by(Meeting.uploaded_at.desc(), Race.id.desc())
    rows = q.all()

    from collections import defaultdict
    import itertools
    import re as _re

    # Group by race so we can apply the race limit
    races_map = defaultdict(list)
    race_keys_ordered = []
    for row in rows:
        key = (row.id, row.race_number)
        if key not in races_map:
            race_keys_ordered.append(key)
        races_map[key].append(row)

    if limit_param != 'all':
        limit = int(limit_param) if str(limit_param).isdigit() else 200
        race_keys_ordered = race_keys_ordered[:limit]

    # Flatten back to only the horses in the limited race set
    all_horse_rows = []
    for key in race_keys_ordered:
        all_horse_rows.extend(races_map[key])

    # ── Notes cache — parse once per horse ────────────────────────────────────
    notes_cache = {}
    for row in all_horse_rows:
        notes_cache[row.horse_id] = parse_notes_components(row.notes or '')

    # ── 2. Helper functions ────────────────────────────────────────────────────
    def _accum(bucket, won, sp):
        bucket['races']  += 1
        bucket['wins']   += (1 if won else 0)
        bucket['profit'] += ((sp * stake - stake) if won else -stake)

    def empty_bucket():
        return {'races': 0, 'wins': 0, 'profit': 0.0}

    def _score_tier(s):
        if s >= 90: return '90-100'
        if s >= 80: return '80-89'
        if s >= 70: return '70-79'
        if s >= 60: return '60-69'
        if s >= 50: return '50-59'
        if s >= 40: return '40-49'
        if s >= 30: return '30-39'
        return '<30'

    def _dist_bucket(d):
        try:
            d = int(d)
        except (ValueError, TypeError):
            return None
        if d <= 1200: return 'Sprint (≤1200m)'
        if d <= 1500: return 'Short (1300-1500m)'
        if d <= 1700: return 'Mile (1550-1700m)'
        if d <= 2200: return 'Middle (1800-2200m)'
        return 'Staying (2400m+)'

    def _barrier_bucket(b):
        try:
            b = int(b)
        except (ValueError, TypeError):
            return None
        if b <= 3:  return '1-3'
        if b <= 6:  return '4-6'
        if b <= 9:  return '7-9'
        return '10+'

    def _days_bucket(days):
        if days is None:  return 'Unknown'
        if days == 0:     return 'First Start'
        if days <= 7:     return 'Quick Back-up (≤7d)'
        if days <= 14:    return 'Short (8-14d)'
        if days <= 21:    return 'Normal (15-21d)'
        if days <= 28:    return 'Normal (22-28d)'
        if days <= 44:    return 'Fresh (29-44d)'
        if days <= 59:    return 'Fresh (45-59d)'
        if days <= 89:    return 'Resuming (60-89d)'
        if days <= 119:   return 'Spell (90-119d)'
        if days <= 149:   return 'Spell (120-149d)'
        if days <= 199:   return 'Long Spell (150-199d)'
        if days <= 249:   return 'Long Spell (200-249d)'
        if days <= 364:   return 'Extended (250-364d)'
        return 'Extended (365d+)'

    def _weight_bucket(w):
        try:
            w = float(str(w).strip())
        except (ValueError, TypeError):
            return None
        if w <= 54:  return '54kg or less'
        if w <= 57:  return '55-57kg'
        if w <= 60:  return '58-60kg'
        return '61kg+'

    def _claim_bucket(c):
        try:
            c = float(str(c).strip())
        except (ValueError, TypeError):
            return None
        if c == 0:    return 'No Claim'
        if c <= 1.5:  return 'Claim 1-1.5kg'
        if c <= 3:    return 'Claim 2-3kg'
        return 'Claim 3kg+'

    def _form_price_bucket(p):
        try:
            p = float(str(p).strip())
        except (ValueError, TypeError):
            return None
        if p <= 2.0:   return 'Last Start Fav (≤$2)'
        if p <= 4.0:   return 'Last Start Favoured ($2-$4)'
        if p <= 8.0:   return 'Last Start Mid ($4-$8)'
        if p <= 15.0:  return 'Last Start Roughie ($8-$15)'
        return 'Last Start Long Shot ($15+)'

    def _weight_change_bucket(form_w, horse_w):
        try:
            fw = float(str(form_w).strip())
            hw = float(str(horse_w).strip())
            diff = hw - fw  # positive = carrying more today
        except (ValueError, TypeError):
            return None
        if diff <= -2:   return 'Weight Drop 2kg+'
        if diff < 0:     return 'Weight Drop <2kg'
        if diff == 0:    return 'Same Weight'
        if diff <= 2:    return 'Weight Rise <2kg'
        return 'Weight Rise 2kg+'

    def _track_from_name(meeting_name):
        if '_' in meeting_name:
            return meeting_name.split('_')[1]
        return meeting_name

    def _parse_days(row):
        csv_data = row.csv_data or {}
        raw = csv_data.get('days since last run', '') or csv_data.get('days_since_run', '')
        if raw:
            try:
                return int(float(str(raw).strip()))
            except (ValueError, TypeError):
                pass
        if row.notes:
            m = _re.search(r'(\d+)\s*days?\s*since\s*(last\s*)?run', row.notes, _re.IGNORECASE)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass
        return None

    # ── 3. First pass: accumulate single-factor buckets across ALL horses ──────
    score_buckets         = defaultdict(empty_bucket)
    dist_buckets          = defaultdict(empty_bucket)
    cond_buckets          = defaultdict(empty_bucket)
    barrier_buckets       = defaultdict(empty_bucket)
    track_buckets         = defaultdict(empty_bucket)
    class_buckets         = defaultdict(empty_bucket)
    jockey_buckets        = defaultdict(empty_bucket)
    trainer_buckets       = defaultdict(empty_bucket)
    sire_buckets          = defaultdict(empty_bucket)
    dam_buckets           = defaultdict(empty_bucket)
    days_buckets          = defaultdict(empty_bucket)
    age_sex_buckets       = defaultdict(empty_bucket)
    age_buckets           = defaultdict(empty_bucket)
    sex_buckets           = defaultdict(empty_bucket)
    component_buckets     = defaultdict(empty_bucket)
    weight_buckets        = defaultdict(empty_bucket)
    claim_buckets         = defaultdict(empty_bucket)
    country_buckets       = defaultdict(empty_bucket)
    sex_restrict_buckets  = defaultdict(empty_bucket)
    weight_type_buckets   = defaultdict(empty_bucket)
    form_price_buckets    = defaultdict(empty_bucket)
    form_weight_buckets   = defaultdict(empty_bucket)
    weight_change_buckets = defaultdict(empty_bucket)

    for row in all_horse_rows:
        won      = row.finish_position == 1
        sp       = row.sp or 0
        csv_data = row.csv_data or {}

        # Score tier
        _accum(score_buckets[_score_tier(row.score)], won, sp)

        # Distance
        db_val = _dist_bucket(csv_data.get('distance') or row.distance)
        if db_val:
            _accum(dist_buckets[db_val], won, sp)

        # Track condition
        cond = row.track_condition or 'Unknown'
        _accum(cond_buckets[cond], won, sp)

        # Barrier
        bb = _barrier_bucket(csv_data.get('horse barrier') or csv_data.get('barrier'))
        if bb:
            _accum(barrier_buckets[bb], won, sp)

        # Track
        track = _track_from_name(row.meeting_name)
        _accum(track_buckets[track], won, sp)

        # Race class
        rc = row.race_class or 'Unknown'
        _accum(class_buckets[rc], won, sp)

        # Jockey
        j = (row.jockey or '').strip()
        if j:
            _accum(jockey_buckets[j], won, sp)

        # Trainer
        t = (row.trainer or '').strip()
        if t:
            _accum(trainer_buckets[t], won, sp)

        # Sire
        s = csv_data.get('horse sire', '').strip()
        if s:
            _accum(sire_buckets[s], won, sp)

        # Dam
        dam = csv_data.get('horse dam', '').strip()
        if dam:
            _accum(dam_buckets[dam], won, sp)

        # Days since run
        days = _parse_days(row)
        db_days = _days_bucket(days)
        if db_days:
            _accum(days_buckets[db_days], won, sp)

        # Age / Sex — combined and separate
        age = csv_data.get('horse age')
        sex = csv_data.get('horse sex', '').strip()
        if age and sex:
            _accum(age_sex_buckets[f"{age}yo {sex}"], won, sp)
        if age:
            _accum(age_buckets[f"{age}yo"], won, sp)
        if sex:
            _accum(sex_buckets[sex], won, sp)

        # Horse weight
        hw = csv_data.get('horse weight')
        wb = _weight_bucket(hw)
        if wb:
            _accum(weight_buckets[wb], won, sp)

        # Claiming allowance
        claim = csv_data.get('horse claim')
        cb = _claim_bucket(claim)
        if cb:
            _accum(claim_buckets[cb], won, sp)

        # Country
        country = csv_data.get('country', '').strip()
        if country:
            if country.upper() in ('AUS', 'AUSTRALIA'):
                country_label = 'AUS'
            elif country.upper() in ('NZ', 'NEW ZEALAND'):
                country_label = 'NZ'
            else:
                country_label = 'Other'
            _accum(country_buckets[country_label], won, sp)

        # Sex restrictions
        sex_restrict = csv_data.get('sex restrictions', '').strip()
        if sex_restrict:
            _accum(sex_restrict_buckets[sex_restrict], won, sp)

        # Weight type
        wt = csv_data.get('weight type', '').strip()
        if wt:
            _accum(weight_type_buckets[wt], won, sp)

        # Form price (last start market price)
        fp = csv_data.get('form price')
        fpb = _form_price_bucket(fp)
        if fpb:
            _accum(form_price_buckets[fpb], won, sp)

        # Form weight (last start weight carried)
        fw = csv_data.get('form weight')
        fwb = _weight_bucket(fw)
        if fwb:
            _accum(form_weight_buckets[fwb], won, sp)

        # Weight change (form weight vs today's horse weight)
        wcb = _weight_change_bucket(fw, hw)
        if wcb:
            _accum(weight_change_buckets[wcb], won, sp)

        # Components from notes
        for cname, val in notes_cache[row.horse_id].items():
            if val:
                _accum(component_buckets[cname], won, sp)

    # ── 4. Derive positive-ROI sets (min appearances to trust signal) ──────────
    def _positive_keys(bucket_dict, min_races=10):
        return {
            k for k, v in bucket_dict.items()
            if v['races'] >= min_races
            and (v['profit'] / (v['races'] * stake) * 100) > 0
        }

    pos_scores        = _positive_keys(score_buckets,         min_races=20)
    pos_dists         = _positive_keys(dist_buckets,          min_races=20)
    pos_conds         = _positive_keys(cond_buckets,          min_races=20)
    pos_barriers      = _positive_keys(barrier_buckets,       min_races=20)
    pos_tracks        = _positive_keys(track_buckets,         min_races=10)
    pos_classes       = _positive_keys(class_buckets,         min_races=10)
    pos_jockeys       = _positive_keys(jockey_buckets,        min_races=min_appearances)
    pos_trainers      = _positive_keys(trainer_buckets,       min_races=min_appearances)
    pos_sires         = _positive_keys(sire_buckets,          min_races=min_appearances)
    pos_dams          = _positive_keys(dam_buckets,           min_races=min_appearances)
    pos_days          = _positive_keys(days_buckets,          min_races=20)
    pos_age_sex       = _positive_keys(age_sex_buckets,       min_races=20)
    pos_age           = _positive_keys(age_buckets,           min_races=20)
    pos_sex           = _positive_keys(sex_buckets,           min_races=20)
    pos_components    = _positive_keys(component_buckets,     min_races=20)
    pos_weights       = _positive_keys(weight_buckets,        min_races=20)
    pos_claims        = _positive_keys(claim_buckets,         min_races=20)
    pos_countries     = _positive_keys(country_buckets,       min_races=20)
    pos_sex_restrict  = _positive_keys(sex_restrict_buckets,  min_races=20)
    pos_weight_types  = _positive_keys(weight_type_buckets,   min_races=20)
    pos_form_prices   = _positive_keys(form_price_buckets,    min_races=20)
    pos_form_weights  = _positive_keys(form_weight_buckets,   min_races=20)
    pos_weight_change = _positive_keys(weight_change_buckets, min_races=20)

    # ── 5. Tag every horse with its positive factors ───────────────────────────
    tagged_horses = []

    for row in all_horse_rows:
        won      = row.finish_position == 1
        sp       = row.sp or 0
        csv_data = row.csv_data or {}
        factors  = set()

        # Score tier
        st = _score_tier(row.score)
        if st in pos_scores:
            factors.add(f"Score: {st}")

        # Distance
        db_val = _dist_bucket(csv_data.get('distance') or row.distance)
        if db_val and db_val in pos_dists:
            factors.add(f"Distance: {db_val}")

        # Track condition
        cond = row.track_condition or 'Unknown'
        if cond in pos_conds:
            factors.add(f"Condition: {cond}")

        # Barrier
        bb = _barrier_bucket(csv_data.get('horse barrier') or csv_data.get('barrier'))
        if bb and bb in pos_barriers:
            factors.add(f"Barrier: {bb}")

        # Track
        track = _track_from_name(row.meeting_name)
        if track in pos_tracks:
            factors.add(f"Track: {track}")

        # Race class
        rc = row.race_class or 'Unknown'
        if rc in pos_classes:
            factors.add(f"Class: {rc}")

        # Days since run
        days = _parse_days(row)
        db_days = _days_bucket(days)
        if db_days and db_days in pos_days:
            factors.add(f"Days: {db_days}")

        # Jockey
        j = (row.jockey or '').strip()
        if j and j in pos_jockeys:
            factors.add(f"Jockey: {j}")

        # Trainer
        t = (row.trainer or '').strip()
        if t and t in pos_trainers:
            factors.add(f"Trainer: {t}")

        # Sire
        s = csv_data.get('horse sire', '').strip()
        if s and s in pos_sires:
            factors.add(f"Sire: {s}")

        # Dam
        dam = csv_data.get('horse dam', '').strip()
        if dam and dam in pos_dams:
            factors.add(f"Dam: {dam}")

        # Age / Sex — combined and separate
        age = csv_data.get('horse age')
        sex = csv_data.get('horse sex', '').strip()
        if age and sex:
            agesex = f"{age}yo {sex}"
            if agesex in pos_age_sex:
                factors.add(f"AgeSex: {agesex}")
        if age and f"{age}yo" in pos_age:
            factors.add(f"Age: {age}yo")
        if sex and sex in pos_sex:
            factors.add(f"Sex: {sex}")

        # Horse weight
        hw = csv_data.get('horse weight')
        wb = _weight_bucket(hw)
        if wb and wb in pos_weights:
            factors.add(f"Weight: {wb}")

        # Claiming allowance
        claim = csv_data.get('horse claim')
        cb = _claim_bucket(claim)
        if cb and cb in pos_claims:
            factors.add(f"Claim: {cb}")

        # Country
        country = csv_data.get('country', '').strip()
        if country:
            if country.upper() in ('AUS', 'AUSTRALIA'):
                country_label = 'AUS'
            elif country.upper() in ('NZ', 'NEW ZEALAND'):
                country_label = 'NZ'
            else:
                country_label = 'Other'
            if country_label in pos_countries:
                factors.add(f"Country: {country_label}")

        # Sex restrictions
        sex_restrict = csv_data.get('sex restrictions', '').strip()
        if sex_restrict and sex_restrict in pos_sex_restrict:
            factors.add(f"SexRestrict: {sex_restrict}")

        # Weight type
        wt = csv_data.get('weight type', '').strip()
        if wt and wt in pos_weight_types:
            factors.add(f"WeightType: {wt}")

        # Form price
        fp = csv_data.get('form price')
        fpb = _form_price_bucket(fp)
        if fpb and fpb in pos_form_prices:
            factors.add(f"FormPrice: {fpb}")

        # Form weight
        fw = csv_data.get('form weight')
        fwb = _weight_bucket(fw)
        if fwb and fwb in pos_form_weights:
            factors.add(f"FormWeight: {fwb}")

        # Weight change
        wcb = _weight_change_bucket(fw, hw)
        if wcb and wcb in pos_weight_change:
            factors.add(f"WeightChange: {wcb}")

        # Components
        for cname, val in notes_cache[row.horse_id].items():
            if val and cname in pos_components:
                factors.add(f"Component: {cname}")

        if len(factors) >= 2:
            tagged_horses.append({
                'factors': factors,
                'won':     won,
                'sp':      sp,
                'horse':   row.horse_name,
            })

    # ── 6. Count all 2, 3, 4 and 5 factor combinations ────────────────────────
    combo_stats = defaultdict(lambda: {'races': 0, 'wins': 0, 'profit': 0.0})

    for horse in tagged_horses:
        factors = sorted(horse['factors'])
        # Cap to 12 most specific factors to avoid combinatorial explosion
        if len(factors) > 12:
            priority = [f for f in factors if any(f.startswith(p) for p in (
                'Component:', 'Jockey:', 'Trainer:', 'Sire:', 'Dam:', 'AgeSex:', 'FormPrice:'
            ))]
            generic  = [f for f in factors if f not in priority]
            factors  = (priority + generic)[:12]

        won    = horse['won']
        sp     = horse['sp']
        profit = (sp * stake - stake) if won else -stake

        for pair in itertools.combinations(factors, 2):
            combo_stats[pair]['races']  += 1
            combo_stats[pair]['wins']   += (1 if won else 0)
            combo_stats[pair]['profit'] += profit

        for triple in itertools.combinations(factors, 3):
            combo_stats[triple]['races']  += 1
            combo_stats[triple]['wins']   += (1 if won else 0)
            combo_stats[triple]['profit'] += profit

        for quad in itertools.combinations(factors, 4):
            combo_stats[quad]['races']  += 1
            combo_stats[quad]['wins']   += (1 if won else 0)
            combo_stats[quad]['profit'] += profit

    # ── 7. Filter: positive ROI, tiered minimum appearances, sort by ROI ───────
    results_list = []
    for combo, stats in combo_stats.items():
        n            = stats['races']
        factor_count = len(combo)
        min_n        = {2: 10, 3: 10, 4: 15, 5: 20}.get(factor_count, 10)
        if n < min_n:
            continue
        roi = stats['profit'] / (n * stake) * 100
        if roi <= 0:
            continue
        sr = stats['wins'] / n * 100
        results_list.append({
            'factors':      list(combo),
            'factor_count': factor_count,
            'races':        n,
            'wins':         stats['wins'],
            'strike_rate':  round(sr, 1),
            'roi':          round(roi, 1),
            'profit':       round(stats['profit'], 2),
        })

    # Sort: highest factor count first, then by ROI descending
    results_list.sort(key=lambda x: (-x['factor_count'], -x['roi']))
    results_list = results_list[:300]

    # ── 7b. Hidden edge combinations ──────────────────────────────────────────
    # Tag every horse with ALL factors regardless of individual ROI
    # Then find pairs/triples that flip positive despite both being negative alone
    all_neg_tagged = []

    for row in all_horse_rows:
        won      = row.finish_position == 1
        sp       = row.sp or 0
        csv_data = row.csv_data or {}
        factors  = set()

        # Score tier — tag all
        factors.add(f"Score: {_score_tier(row.score)}")

        # Distance
        db_val = _dist_bucket(csv_data.get('distance') or row.distance)
        if db_val:
            factors.add(f"Distance: {db_val}")

        # Track condition
        factors.add(f"Condition: {row.track_condition or 'Unknown'}")

        # Barrier
        bb = _barrier_bucket(csv_data.get('horse barrier') or csv_data.get('barrier'))
        if bb:
            factors.add(f"Barrier: {bb}")

        # Track
        factors.add(f"Track: {_track_from_name(row.meeting_name)}")

        # Race class
        factors.add(f"Class: {row.race_class or 'Unknown'}")

        # Days since run
        days = _parse_days(row)
        db_days = _days_bucket(days)
        if db_days:
            factors.add(f"Days: {db_days}")

        # Jockey
        j = (row.jockey or '').strip()
        if j:
            factors.add(f"Jockey: {j}")

        # Trainer
        t = (row.trainer or '').strip()
        if t:
            factors.add(f"Trainer: {t}")

        # Sire
        s = csv_data.get('horse sire', '').strip()
        if s:
            factors.add(f"Sire: {s}")

        # Age / Sex
        age = csv_data.get('horse age')
        sex = csv_data.get('horse sex', '').strip()
        if age and sex:
            factors.add(f"AgeSex: {age}yo {sex}")
        if age:
            factors.add(f"Age: {age}yo")
        if sex:
            factors.add(f"Sex: {sex}")

        # Weight
        hw = csv_data.get('horse weight')
        wb = _weight_bucket(hw)
        if wb:
            factors.add(f"Weight: {wb}")

        # Claim
        claim = csv_data.get('horse claim')
        cb = _claim_bucket(claim)
        if cb:
            factors.add(f"Claim: {cb}")

        # Country
        country = csv_data.get('country', '').strip()
        if country:
            if country.upper() in ('AUS', 'AUSTRALIA'):
                factors.add('Country: AUS')
            elif country.upper() in ('NZ', 'NEW ZEALAND'):
                factors.add('Country: NZ')
            else:
                factors.add('Country: Other')

        # Weight type
        wt = csv_data.get('weight type', '').strip()
        if wt:
            factors.add(f"WeightType: {wt}")

        # Form price
        fp = csv_data.get('form price')
        fpb = _form_price_bucket(fp)
        if fpb:
            factors.add(f"FormPrice: {fpb}")

        # Weight change
        fw = csv_data.get('form weight')
        wcb = _weight_change_bucket(fw, hw)
        if wcb:
            factors.add(f"WeightChange: {wcb}")

        # Components — tag all regardless of ROI
        for cname, val in notes_cache[row.horse_id].items():
            if val:
                factors.add(f"Component: {cname}")

        # Cap to 15 factors to avoid explosion
        if len(factors) > 15:
            priority = [f for f in factors if any(f.startswith(p) for p in (
                'Component:', 'Jockey:', 'Trainer:', 'Sire:', 'AgeSex:', 'FormPrice:'
            ))]
            generic  = [f for f in factors if f not in priority]
            factors  = set((priority + generic)[:15])

        all_neg_tagged.append({
            'factors': factors,
            'won':     won,
            'sp':      sp,
        })

    # Build single factor ROI lookup across ALL factors
    all_single_buckets = defaultdict(lambda: {'races': 0, 'wins': 0, 'profit': 0.0})
    for horse in all_neg_tagged:
        won    = horse['won']
        sp     = horse['sp']
        profit = (sp * stake - stake) if won else -stake
        for f in horse['factors']:
            all_single_buckets[f]['races']  += 1
            all_single_buckets[f]['wins']   += 1 if won else 0
            all_single_buckets[f]['profit'] += profit

    # Identify factors that are NOT positive ROI individually (negative or neutral)
    def _is_not_positive(factor):
        b = all_single_buckets[factor]
        if b['races'] < 20:
            return True  # too small to trust — treat as non-positive
        roi = b['profit'] / (b['races'] * stake) * 100
        return roi <= 0

    # Count all pairs and triples across all_neg_tagged
    hidden_combo_stats = defaultdict(lambda: {'races': 0, 'wins': 0, 'profit': 0.0})

    for horse in all_neg_tagged:
        factors = sorted(horse['factors'])
        won     = horse['won']
        sp      = horse['sp']
        profit  = (sp * stake - stake) if won else -stake

        for pair in itertools.combinations(factors, 2):
            # Only count if BOTH factors are individually non-positive
            if _is_not_positive(pair[0]) and _is_not_positive(pair[1]):
                hidden_combo_stats[pair]['races']  += 1
                hidden_combo_stats[pair]['wins']   += 1 if won else 0
                hidden_combo_stats[pair]['profit'] += profit

    # Filter: 50+ races, positive ROI, sort by ROI
    hidden_list = []
    for combo, stats in hidden_combo_stats.items():
        n = stats['races']
        if n < 50:
            continue
        roi = stats['profit'] / (n * stake) * 100
        if roi <= 0:
            continue
        sr = stats['wins'] / n * 100
        hidden_list.append({
            'factors':      list(combo),
            'factor_count': len(combo),
            'races':        n,
            'wins':         stats['wins'],
            'strike_rate':  round(sr, 1),
            'roi':          round(roi, 1),
            'profit':       round(stats['profit'], 2),
        })

    hidden_list.sort(key=lambda x: (-x['factor_count'], -x['roi']))
    hidden_list = hidden_list[:200]

    # ── 8. Pace angle analysis (bypasses positive ROI gate entirely) ──────────
    leader_patterns = {
        'Sprint':  'Running Position - Leader Sprint',
        'Mile':    'Running Position - Leader Mile',
        'Middle':  'Running Position - Leader Middle',
        'Staying': 'Running Position - Leader Staying',
    }
    narrow_loss_patterns = [
        'Last Start - Narrow Loss (≤1L)',
        'Last Start - Close Loss 2nd (1-2L)',
        'Last Start - Close Loss 3rd (1-2L)',
        'Last Start - Competitive Effort (≤3L)',
    ]

    def _empty_pace():
        return {'races': 0, 'wins': 0, 'profit': 0.0}

    pace_buckets = {
        'leader_only':      _empty_pace(),
        'narrow_loss_only': _empty_pace(),
        'both':             _empty_pace(),
        'neither':          _empty_pace(),
        'both_sprint':      _empty_pace(),
        'both_mile':        _empty_pace(),
        'both_middle':      _empty_pace(),
        'both_staying':     _empty_pace(),
    }

    for row in all_horse_rows:
        won    = row.finish_position == 1
        sp     = row.sp or 0
        profit = (sp * stake - stake) if won else -stake
        comps  = notes_cache[row.horse_id]

        is_leader      = any(p in comps for p in leader_patterns.values())
        is_narrow_loss = any(p in comps for p in narrow_loss_patterns)

        if is_leader and is_narrow_loss:
            key = 'both'
        elif is_leader:
            key = 'leader_only'
        elif is_narrow_loss:
            key = 'narrow_loss_only'
        else:
            key = 'neither'

        pace_buckets[key]['races']  += 1
        pace_buckets[key]['wins']   += 1 if won else 0
        pace_buckets[key]['profit'] += profit

        # Distance sub-breakdown — only for 'both'
        if is_leader and is_narrow_loss:
            for dist_label, pattern in leader_patterns.items():
                if pattern in comps:
                    sub_key = f'both_{dist_label.lower()}'
                    pace_buckets[sub_key]['races']  += 1
                    pace_buckets[sub_key]['wins']   += 1 if won else 0
                    pace_buckets[sub_key]['profit'] += profit

    def _fmt_pace(b, min_races=0):
        n = b['races']
        if n < min_races:
            return None
        if n == 0:
            return {'races': 0, 'wins': 0, 'strike_rate': 0, 'roi': 0, 'profit': 0}
        return {
            'races':       n,
            'wins':        b['wins'],
            'strike_rate': round(b['wins'] / n * 100, 1),
            'roi':         round(b['profit'] / (n * stake) * 100, 1),
            'profit':      round(b['profit'], 2),
        }

    pace_angle = {
        # These four always show regardless of sample size
        'leader_only':      _fmt_pace(pace_buckets['leader_only']),
        'narrow_loss_only': _fmt_pace(pace_buckets['narrow_loss_only']),
        'both':             _fmt_pace(pace_buckets['both']),
        'neither':          _fmt_pace(pace_buckets['neither']),
        # Distance sub-buckets only show if 50+ races
        'both_sprint':      _fmt_pace(pace_buckets['both_sprint'],  min_races=50),
        'both_mile':        _fmt_pace(pace_buckets['both_mile'],    min_races=50),
        'both_middle':      _fmt_pace(pace_buckets['both_middle'],  min_races=50),
        'both_staying':     _fmt_pace(pace_buckets['both_staying'], min_races=50),
    }

    import gc
    gc.collect()
    db.session.expunge_all()
    db.session.remove()

    return jsonify({
        'combinations':          results_list,
        'hidden_edges':          hidden_list,
        'total_found':           len(results_list),
        'total_horses_analysed': len(all_horse_rows),
        'pace_angle':            pace_angle,
        'positive_single_factors': {
            'score_tiers':   sorted(pos_scores),
            'distances':     sorted(pos_dists),
            'conditions':    sorted(pos_conds),
            'barriers':      sorted(pos_barriers),
            'tracks':        sorted(pos_tracks),
            'classes':       sorted(pos_classes),
            'days_buckets':  sorted(pos_days),
            'jockeys':       sorted(pos_jockeys),
            'trainers':      sorted(pos_trainers),
            'sires':         sorted(pos_sires),
            'dams':          sorted(pos_dams),
            'age_sex':       sorted(pos_age_sex),
            'ages':          sorted(pos_age),
            'sexes':         sorted(pos_sex),
            'components':    sorted(pos_components),
            'weights':       sorted(pos_weights),
            'claims':        sorted(pos_claims),
            'countries':     sorted(pos_countries),
            'sex_restrict':  sorted(pos_sex_restrict),
            'weight_types':  sorted(pos_weight_types),
            'form_prices':   sorted(pos_form_prices),
            'form_weights':  sorted(pos_form_weights),
            'weight_change': sorted(pos_weight_change),
        }
    })

# ----- ML Data Export Route -----
@app.route("/data/export")
@login_required
def export_ml_data():
    """Export all race data with parsed scoring components AND raw CSV data for ML analysis"""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required to download ML training data.', 'error')
        return redirect(url_for('analytics'))
    import csv
    from io import StringIO
    from flask import make_response

    track_filter     = request.args.get('track', '')
    min_score_filter = request.args.get('min_score', type=float)
    date_from        = request.args.get('date_from', '')
    date_to          = request.args.get('date_to', '')

    base_query = db.session.query(
        Meeting.date,
        Meeting.meeting_name,
        Race.race_number,
        Race.distance,
        Race.race_class,
        Race.track_condition,
        Horse.id,
        Horse.horse_name,
        Horse.barrier,
        Horse.weight,
        Horse.jockey,
        Horse.trainer,
        Horse.form,
        Horse.csv_data,
        Prediction.score,
        Prediction.predicted_odds,
        Prediction.win_probability,
        Prediction.notes,
        Result.finish_position,
        Result.sp
    ).join(Race,       Horse.race_id        == Race.id)\
     .join(Meeting,    Race.meeting_id      == Meeting.id)\
     .join(Prediction, Horse.id             == Prediction.horse_id)\
     .join(Result,     Horse.id             == Result.horse_id)\
     .filter(Result.finish_position > 0)

    if track_filter:
        base_query = base_query.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        base_query = base_query.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        base_query = base_query.filter(Meeting.uploaded_at <= date_to)

    query_results = base_query.order_by(Meeting.date.desc(), Race.race_number.asc()).limit(50000).all()

    # First pass: collect all unique CSV field names
    all_csv_fields = set()
    for row in query_results:
        csv_data = row[13]
        if csv_data and isinstance(csv_data, dict):
            all_csv_fields.update(csv_data.keys())
    csv_field_names = sorted(list(all_csv_fields))

    si = StringIO()
    writer = csv.writer(si)

    component_columns = [
        # Form
        'ran_places',
        # Jockey
        'jockey_elite', 'jockey_strong', 'jockey_profitable', 'jockey_poor',
        # Trainer
        'trainer_elite', 'trainer_strong', 'trainer_profitable', 'trainer_poor',
        # Track record
        'track_win_exceptional', 'track_win_strong', 'track_win_good', 'track_win_moderate', 'track_win_low',
        'track_no_wins', 'track_no_runs',
        'track_podium_elite', 'track_podium_excellent', 'track_podium_strong', 'track_podium_good',
        'track_podium_moderate', 'track_poor',
        # Track+Distance
        'td_win_exceptional', 'td_win_strong', 'td_win_good', 'td_win_moderate', 'td_win_low',
        'td_no_wins', 'td_no_runs',
        'td_podium_elite', 'td_podium_excellent', 'td_podium_strong', 'td_podium_good',
        'td_podium_moderate', 'td_poor',
        # Distance record
        'dist_win_exceptional', 'dist_win_strong', 'dist_win_good', 'dist_win_moderate', 'dist_win_low',
        'dist_no_wins', 'dist_no_runs',
        'dist_podium_elite', 'dist_podium_excellent', 'dist_podium_strong', 'dist_podium_good',
        'dist_podium_moderate', 'dist_poor',
        # Condition
        'cond_win_exceptional', 'cond_win_strong', 'cond_win_good', 'cond_win_moderate', 'cond_win_low',
        'cond_no_wins', 'cond_no_runs',
        'cond_podium_elite', 'cond_podium_excellent', 'cond_podium_strong', 'cond_podium_good',
        'cond_podium_moderate', 'cond_poor',
        # Distance change
        'dist_change_step_up_large', 'dist_change_step_up_moderate',
        'dist_change_drop_large', 'dist_change_drop_moderate',
        # Class change
        'class_drop', 'class_rise',
        # Last start
        'ls_dominant_win', 'ls_comfortable_win', 'ls_narrow_win', 'ls_photo_win',
        'ls_narrow_loss', 'ls_close_loss_2nd', 'ls_close_loss_3rd',
        'ls_competitive_effort', 'ls_beaten_clearly', 'ls_beaten_badly',
        'ls_well_beaten_class_drop', 'ls_beaten_dropping', 'ls_beaten_clearly_dropping',
        'ls_well_beaten', 'ls_demolished',
        # Days since run
        'days_quick_backup', 'days_fresh_return', 'days_too_fresh_200',
        'days_too_fresh_250', 'days_too_fresh_1yr',
        # Form price
        'form_price_very_short', 'form_price_short', 'form_price_backed',
        'form_price_slight_value', 'form_price_outsider',
        # First/Second up
        'first_up_winner', 'first_up_podium', 'second_up_winner', 'second_up_podium',
        'first_up_undefeated', 'second_up_undefeated', 'spell_unclear',
        # Weight vs field
        'weight_well_below', 'weight_below', 'weight_slightly_below', 'weight_marginally_below',
        'weight_near_avg', 'weight_marginally_above', 'weight_above',
        'weight_well_above_2kg', 'weight_well_above_3kg',
        # Weight change
        'weight_dropped_3kg', 'weight_dropped_2kg', 'weight_dropped_1kg',
        'weight_up_1kg', 'weight_up_2kg', 'weight_up_3kg',
        # Career win rate
        'career_win_elite', 'career_win_strong', 'career_win_poor',
        # Age/Sex
        'age_5yo_entire', 'age_8yo_mare', 'age_3yo', 'age_4yo',
        'age_5yo_mare_penalty', 'age_67yo_mare_penalty',
        'age_78yo_penalty', 'age_9yo_penalty', 'age_10yo_penalty',
        'age_11yo_penalty', 'age_12yo_penalty', 'age_13yo_penalty',
        # Colt
        'colt_3yo', 'colt_base', 'colt_fast_sectional',
        # Sire
        'sire_elite_roi', 'sire_strong_roi', 'sire_positive_roi', 'sire_negative_roi',
        # Specialist
        'specialist_undefeated_td', 'specialist_undefeated_track',
        'specialist_undefeated_dist', 'specialist_undefeated_cond',
        'specialist_podium_td', 'specialist_podium_track',
        'specialist_podium_dist', 'specialist_podium_cond',
        # Historical sectionals
        'sectional_weighted_avg', 'sectional_best_recent',
        'sectional_consistency_excellent', 'sectional_consistency_good',
        'sectional_consistency_fair', 'sectional_consistency_poor',
        # API sectionals
        'api_200m_elite', 'api_200m_very_good', 'api_200m_good', 'api_200m_average', 'api_200m_poor',
        'api_400m_elite', 'api_400m_very_good', 'api_400m_good', 'api_400m_average', 'api_400m_poor',
        'api_600m_elite', 'api_600m_very_good', 'api_600m_good', 'api_improving_trend',
        # Running position
        'pos_leader_sprint', 'pos_onpace_sprint', 'pos_midfield_sprint', 'pos_backmarker_sprint',
        'pos_leader_mile', 'pos_onpace_mile', 'pos_midfield_mile', 'pos_backmarker_mile',
        'pos_leader_middle', 'pos_onpace_middle', 'pos_midfield_middle', 'pos_backmarker_middle',
        'pos_leader_staying', 'pos_onpace_staying', 'pos_midfield_staying', 'pos_backmarker_staying',
        # Pace angle
        'pace_sprint_leader_rundown',
        # Hidden edges
        'hidden_short_price_competitive', 'hidden_600m_elite_marginally_below',
        'hidden_400m_elite_competitive', 'hidden_400m_elite_marginally_below',
        'hidden_600m_elite_competitive', 'hidden_condition_win_narrow_win',
        'hidden_short_price_slightly_below', 'hidden_short_price_best_sectional',
        # PFAI
        'pfai_90plus', 'pfai_80_89', 'pfai_70_79', 'pfai_60_69', 'pfai_sub60',
        # Market expectation
        'me_best_in_field', 'me_chronic_over', 'me_strong_over', 'me_moderate_out', 'me_above_avg',
        'me_worst_in_field', 'me_chronic_under', 'me_significant_under', 'me_mild_under',
        'me_below_avg', 'me_neutral',
    ]

    header = [
        'date', 'meeting_name', 'track', 'race_number', 'distance', 'race_class', 'track_condition',
        'horse_name', 'barrier', 'weight', 'jockey', 'trainer', 'form',
        'total_score', 'predicted_odds', 'win_probability',
        'finish_position', 'sp', 'won', 'placed', 'roi',
    ] + component_columns + csv_field_names

    writer.writerow(header)

    for row in query_results:
        date, meeting_name, race_num, distance, race_class, track_cond, \
        horse_id, horse_name, barrier, weight, jockey, trainer, form, csv_data, \
        score, pred_odds, win_prob, notes, finish_pos, sp = row

        track = ''
        if meeting_name:
            if '_' in meeting_name:
                parts = meeting_name.split('_')
                track = parts[1] if len(parts) > 1 else meeting_name
            else:
                track = meeting_name

        date_str = date.strftime('%Y-%m-%d') if date else ''

        try:
            pred_odds_clean = str(pred_odds or '').replace('$', '').strip()
        except:
            pred_odds_clean = ''

        won    = 1 if finish_pos == 1 else 0
        placed = 1 if finish_pos <= 3 else 0

        try:
            roi = ((float(sp) - 1) * 100) if (finish_pos == 1 and sp) else -100
        except (ValueError, TypeError):
            roi = -100

        components = parse_notes_components(notes or '')

        component_values = {
            'ran_places':                         components.get('Ran Places', 0),
            'jockey_elite':                       components.get('Jockey - Elite (50%+ ROI)', 0),
            'jockey_strong':                      components.get('Jockey - Strong Value (20-50% ROI)', 0),
            'jockey_profitable':                  components.get('Jockey - Profitable (0-20% ROI)', 0),
            'jockey_poor':                        components.get('Jockey - Poor Value', 0),
            'trainer_elite':                      components.get('Trainer - Elite (50%+ ROI)', 0),
            'trainer_strong':                     components.get('Trainer - Strong Value (20-50% ROI)', 0),
            'trainer_profitable':                 components.get('Trainer - Profitable (0-20% ROI)', 0),
            'trainer_poor':                       components.get('Trainer - Poor Value', 0),
            'track_win_exceptional':              components.get('Track Win Rate - Exceptional (51%+)', 0),
            'track_win_strong':                   components.get('Track Win Rate - Strong (36-50%)', 0),
            'track_win_good':                     components.get('Track Win Rate - Good (26-35%)', 0),
            'track_win_moderate':                 components.get('Track Win Rate - Moderate (16-25%)', 0),
            'track_win_low':                      components.get('Track Win Rate - Low (1-15%)', 0),
            'track_no_wins':                      components.get('Track Win Rate - No Wins', 0),
            'track_no_runs':                      components.get('Track - No Runs', 0),
            'track_podium_elite':                 components.get('Track Podium Rate - Elite (85%+)', 0),
            'track_podium_excellent':             components.get('Track Podium Rate - Excellent (70-84%)', 0),
            'track_podium_strong':                components.get('Track Podium Rate - Strong (55-69%)', 0),
            'track_podium_good':                  components.get('Track Podium Rate - Good (40-54%)', 0),
            'track_podium_moderate':              components.get('Track Podium Rate - Moderate (25-39%)', 0),
            'track_poor':                         components.get('Track - Poor Performance', 0),
            'td_win_exceptional':                 components.get('Track+Distance Win Rate - Exceptional', 0),
            'td_win_strong':                      components.get('Track+Distance Win Rate - Strong', 0),
            'td_win_good':                        components.get('Track+Distance Win Rate - Good', 0),
            'td_win_moderate':                    components.get('Track+Distance Win Rate - Moderate', 0),
            'td_win_low':                         components.get('Track+Distance Win Rate - Low', 0),
            'td_no_wins':                         components.get('Track+Distance Win Rate - No Wins', 0),
            'td_no_runs':                         components.get('Track+Distance - No Runs', 0),
            'td_podium_elite':                    components.get('Track+Distance Podium Rate - Elite', 0),
            'td_podium_excellent':                components.get('Track+Distance Podium Rate - Excellent', 0),
            'td_podium_strong':                   components.get('Track+Distance Podium Rate - Strong', 0),
            'td_podium_good':                     components.get('Track+Distance Podium Rate - Good', 0),
            'td_podium_moderate':                 components.get('Track+Distance Podium Rate - Moderate', 0),
            'td_poor':                            components.get('Track+Distance - Poor Performance', 0),
            'dist_win_exceptional':               components.get('Distance Win Rate - Exceptional (51%+)', 0),
            'dist_win_strong':                    components.get('Distance Win Rate - Strong (36-50%)', 0),
            'dist_win_good':                      components.get('Distance Win Rate - Good (26-35%)', 0),
            'dist_win_moderate':                  components.get('Distance Win Rate - Moderate (16-25%)', 0),
            'dist_win_low':                       components.get('Distance Win Rate - Low (1-15%)', 0),
            'dist_no_wins':                       components.get('Distance Win Rate - No Wins', 0),
            'dist_no_runs':                       components.get('Distance - No Runs', 0),
            'dist_podium_elite':                  components.get('Distance Podium Rate - Elite (85%+)', 0),
            'dist_podium_excellent':              components.get('Distance Podium Rate - Excellent (70-84%)', 0),
            'dist_podium_strong':                 components.get('Distance Podium Rate - Strong (55-69%)', 0),
            'dist_podium_good':                   components.get('Distance Podium Rate - Good (40-54%)', 0),
            'dist_podium_moderate':               components.get('Distance Podium Rate - Moderate (25-39%)', 0),
            'dist_poor':                          components.get('Distance - Poor Performance', 0),
            'cond_win_exceptional':               components.get('Condition Win Rate - Exceptional (51%+)', 0),
            'cond_win_strong':                    components.get('Condition Win Rate - Strong (36-50%)', 0),
            'cond_win_good':                      components.get('Condition Win Rate - Good (26-35%)', 0),
            'cond_win_moderate':                  components.get('Condition Win Rate - Moderate (16-25%)', 0),
            'cond_win_low':                       components.get('Condition Win Rate - Low (1-15%)', 0),
            'cond_no_wins':                       components.get('Condition Win Rate - No Wins', 0),
            'cond_no_runs':                       components.get('Condition - No Runs', 0),
            'cond_podium_elite':                  components.get('Condition Podium Rate - Elite (85%+)', 0),
            'cond_podium_excellent':              components.get('Condition Podium Rate - Excellent (70-84%)', 0),
            'cond_podium_strong':                 components.get('Condition Podium Rate - Strong (55-69%)', 0),
            'cond_podium_good':                   components.get('Condition Podium Rate - Good (40-54%)', 0),
            'cond_podium_moderate':               components.get('Condition Podium Rate - Moderate (25-39%)', 0),
            'cond_poor':                          components.get('Condition - Poor Performance', 0),
            'dist_change_step_up_large':          components.get('Distance Change - Step Up Large (400m+)', 0),
            'dist_change_step_up_moderate':       components.get('Distance Change - Step Up Moderate (200-400m)', 0),
            'dist_change_drop_large':             components.get('Distance Change - Drop Back Large (400m+)', 0),
            'dist_change_drop_moderate':          components.get('Distance Change - Drop Back Moderate (200-400m)', 0),
            'class_drop':                         components.get('Class Drop', 0),
            'class_rise':                         components.get('Class Rise', 0),
            'ls_dominant_win':                    components.get('Last Start - Dominant Win (5L+)', 0),
            'ls_comfortable_win':                 components.get('Last Start - Comfortable Win (2-5L)', 0),
            'ls_narrow_win':                      components.get('Last Start - Narrow Win (0.5-2L)', 0),
            'ls_photo_win':                       components.get('Last Start - Photo Win (<0.5L)', 0),
            'ls_narrow_loss':                     components.get('Last Start - Narrow Loss (≤1L)', 0),
            'ls_close_loss_2nd':                  components.get('Last Start - Close Loss 2nd (1-2L)', 0),
            'ls_close_loss_3rd':                  components.get('Last Start - Close Loss 3rd (1-2L)', 0),
            'ls_competitive_effort':              components.get('Last Start - Competitive Effort (≤3L)', 0),
            'ls_beaten_clearly':                  components.get('Last Start - Beaten Clearly (3-6L)', 0),
            'ls_beaten_badly':                    components.get('Last Start - Beaten Badly Placed', 0),
            'ls_well_beaten_class_drop':          components.get('Last Start - Well Beaten + Class Drop', 0),
            'ls_beaten_dropping':                 components.get('Last Start - Beaten + Dropping Class', 0),
            'ls_beaten_clearly_dropping':         components.get('Last Start - Beaten Clearly + Dropping', 0),
            'ls_well_beaten':                     components.get('Last Start - Well Beaten (6-10L)', 0),
            'ls_demolished':                      components.get('Last Start - Demolished (10L+)', 0),
            'days_quick_backup':                  components.get('Days Since Run - Quick Backup (≤7 days)', 0),
            'days_fresh_return':                  components.get('Days Since Run - Fresh Return (150-199 days)', 0),
            'days_too_fresh_200':                 components.get('Days Since Run - Too Fresh (200+ days)', 0),
            'days_too_fresh_250':                 components.get('Days Since Run - Too Fresh (250+ days)', 0),
            'days_too_fresh_1yr':                 components.get('Days Since Run - Too Fresh (1+ year)', 0),
            'form_price_very_short':              components.get('Form Price - Very Short ($1-$2)', 0),
            'form_price_short':                   components.get('Form Price - Short ($2-$5)', 0),
            'form_price_backed':                  components.get('Form Price - Backed ($5-$13)', 0),
            'form_price_slight_value':            components.get('Form Price - Slight Value ($12-$14)', 0),
            'form_price_outsider':                components.get('Form Price - Outsider ($15+)', 0),
            'first_up_winner':                    components.get('First Up - Has Won First Up', 0),
            'first_up_podium':                    components.get('First Up - Strong Podium Rate', 0),
            'second_up_winner':                   components.get('Second Up - Has Won Second Up', 0),
            'second_up_podium':                   components.get('Second Up - Strong Podium Rate', 0),
            'first_up_undefeated':                components.get('First Up - Specialist Undefeated', 0),
            'second_up_undefeated':               components.get('Second Up - Specialist Undefeated', 0),
            'spell_unclear':                      components.get('Spell Status - Unclear', 0),
            'weight_well_below':                  components.get('Weight vs Field - Well Below (3kg+)', 0),
            'weight_below':                       components.get('Weight vs Field - Below (2-3kg)', 0),
            'weight_slightly_below':              components.get('Weight vs Field - Slightly Below (1-2kg)', 0),
            'weight_marginally_below':            components.get('Weight vs Field - Marginally Below (0.5-1kg)', 0),
            'weight_near_avg':                    components.get('Weight vs Field - Near Average', 0),
            'weight_marginally_above':            components.get('Weight vs Field - Marginally Above', 0),
            'weight_above':                       components.get('Weight vs Field - Above (1-2kg)', 0),
            'weight_well_above_2kg':              components.get('Weight vs Field - Well Above (2-3kg)', 0),
            'weight_well_above_3kg':              components.get('Weight vs Field - Well Above (3kg+)', 0),
            'weight_dropped_3kg':                 components.get('Weight Change - Dropped 3kg+', 0),
            'weight_dropped_2kg':                 components.get('Weight Change - Dropped 2-3kg', 0),
            'weight_dropped_1kg':                 components.get('Weight Change - Dropped 1-2kg', 0),
            'weight_up_1kg':                      components.get('Weight Change - Up 1-2kg', 0),
            'weight_up_2kg':                      components.get('Weight Change - Up 2-3kg', 0),
            'weight_up_3kg':                      components.get('Weight Change - Up 3kg+', 0),
            'career_win_elite':                   components.get('Career Win Rate - Elite 40%+', 0),
            'career_win_strong':                  components.get('Career Win Rate - Strong 30-40%', 0),
            'career_win_poor':                    components.get('Career Win Rate - Poor <10%', 0),
            'age_5yo_entire':                     components.get('Age/Sex - 5yo Horse (Entire)', 0),
            'age_8yo_mare':                       components.get('Age/Sex - 8yo Mare', 0),
            'age_3yo':                            components.get('Age/Sex - 3yo', 0),
            'age_4yo':                            components.get('Age/Sex - 4yo', 0),
            'age_5yo_mare_penalty':               components.get('Age/Sex - 5yo Mare Penalty', 0),
            'age_67yo_mare_penalty':              components.get('Age/Sex - 6-7yo Mare Penalty', 0),
            'age_78yo_penalty':                   components.get('Age/Sex - 7-8yo Penalty', 0),
            'age_9yo_penalty':                    components.get('Age/Sex - 9yo Penalty', 0),
            'age_10yo_penalty':                   components.get('Age/Sex - 10yo Penalty', 0),
            'age_11yo_penalty':                   components.get('Age/Sex - 11yo Penalty', 0),
            'age_12yo_penalty':                   components.get('Age/Sex - 12yo Penalty', 0),
            'age_13yo_penalty':                   components.get('Age/Sex - 13+yo Penalty', 0),
            'colt_3yo':                           components.get('Colt - 3yo Colt', 0),
            'colt_base':                          components.get('Colt - Base Bonus', 0),
            'colt_fast_sectional':                components.get('Colt - Fast Sectional + Colt', 0),
            'sire_elite_roi':                     components.get('Sire - Elite ROI (50%+)', 0),
            'sire_strong_roi':                    components.get('Sire - Strong ROI (20-50%)', 0),
            'sire_positive_roi':                  components.get('Sire - Positive ROI (0-20%)', 0),
            'sire_negative_roi':                  components.get('Sire - Negative ROI', 0),
            'specialist_undefeated_td':           components.get('Specialist - Undefeated Track+Distance', 0),
            'specialist_undefeated_track':        components.get('Specialist - Undefeated Track', 0),
            'specialist_undefeated_dist':         components.get('Specialist - Undefeated Distance', 0),
            'specialist_undefeated_cond':         components.get('Specialist - Undefeated Condition', 0),
            'specialist_podium_td':               components.get('Specialist - Perfect Podium Track+Distance', 0),
            'specialist_podium_track':            components.get('Specialist - Perfect Podium Track', 0),
            'specialist_podium_dist':             components.get('Specialist - Perfect Podium Distance', 0),
            'specialist_podium_cond':             components.get('Specialist - Perfect Podium Condition', 0),
            'sectional_weighted_avg':             components.get('Sectional History - Weighted Avg', 0),
            'sectional_best_recent':              components.get('Sectional History - Best Recent', 0),
            'sectional_consistency_excellent':    components.get('Sectional Consistency - Excellent', 0),
            'sectional_consistency_good':         components.get('Sectional Consistency - Good', 0),
            'sectional_consistency_fair':         components.get('Sectional Consistency - Fair', 0),
            'sectional_consistency_poor':         components.get('Sectional Consistency - Poor', 0),
            'api_200m_elite':                     components.get('API Sectional - Last 200m Elite', 0),
            'api_200m_very_good':                 components.get('API Sectional - Last 200m Very Good', 0),
            'api_200m_good':                      components.get('API Sectional - Last 200m Good', 0),
            'api_200m_average':                   components.get('API Sectional - Last 200m Average', 0),
            'api_200m_poor':                      components.get('API Sectional - Last 200m Poor', 0),
            'api_400m_elite':                     components.get('API Sectional - Last 400m Elite', 0),
            'api_400m_very_good':                 components.get('API Sectional - Last 400m Very Good', 0),
            'api_400m_good':                      components.get('API Sectional - Last 400m Good', 0),
            'api_400m_average':                   components.get('API Sectional - Last 400m Average', 0),
            'api_400m_poor':                      components.get('API Sectional - Last 400m Poor', 0),
            'api_600m_elite':                     components.get('API Sectional - Last 600m Elite', 0),
            'api_600m_very_good':                 components.get('API Sectional - Last 600m Very Good', 0),
            'api_600m_good':                      components.get('API Sectional - Last 600m Good', 0),
            'api_improving_trend':                components.get('API Sectional - Improving Trend', 0),
            'pos_leader_sprint':                  components.get('Running Position - Leader Sprint', 0),
            'pos_onpace_sprint':                  components.get('Running Position - OnPace Sprint', 0),
            'pos_midfield_sprint':                components.get('Running Position - Midfield Sprint', 0),
            'pos_backmarker_sprint':              components.get('Running Position - Backmarker Sprint', 0),
            'pos_leader_mile':                    components.get('Running Position - Leader Mile', 0),
            'pos_onpace_mile':                    components.get('Running Position - OnPace Mile', 0),
            'pos_midfield_mile':                  components.get('Running Position - Midfield Mile', 0),
            'pos_backmarker_mile':                components.get('Running Position - Backmarker Mile', 0),
            'pos_leader_middle':                  components.get('Running Position - Leader Middle', 0),
            'pos_onpace_middle':                  components.get('Running Position - OnPace Middle', 0),
            'pos_midfield_middle':                components.get('Running Position - Midfield Middle', 0),
            'pos_backmarker_middle':              components.get('Running Position - Backmarker Middle', 0),
            'pos_leader_staying':                 components.get('Running Position - Leader Staying', 0),
            'pos_onpace_staying':                 components.get('Running Position - OnPace Staying', 0),
            'pos_midfield_staying':               components.get('Running Position - Midfield Staying', 0),
            'pos_backmarker_staying':             components.get('Running Position - Backmarker Staying', 0),
            'pace_sprint_leader_rundown':         components.get('Pace Angle - Sprint Leader Run Down', 0),
            'hidden_short_price_competitive':     components.get('Hidden Edge - Short Price + Competitive Effort', 0),
            'hidden_600m_elite_marginally_below': components.get('Hidden Edge - Elite 600m + Marginally Below Weight', 0),
            'hidden_400m_elite_competitive':      components.get('Hidden Edge - Elite 400m + Competitive Effort', 0),
            'hidden_400m_elite_marginally_below': components.get('Hidden Edge - Elite 400m + Marginally Below Weight', 0),
            'hidden_600m_elite_competitive':      components.get('Hidden Edge - Elite 600m + Competitive Effort', 0),
            'hidden_condition_win_narrow_win':    components.get('Hidden Edge - Good Condition WR + Narrow Win', 0),
            'hidden_short_price_slightly_below':  components.get('Hidden Edge - Short Price + Slightly Below Weight', 0),
            'hidden_short_price_best_sectional':  components.get('Hidden Edge - Short Price + Best Recent Sectional', 0),
            'pfai_90plus':                        components.get('PFAI Score - 90+', 0),
            'pfai_80_89':                         components.get('PFAI Score - 80-89', 0),
            'pfai_70_79':                         components.get('PFAI Score - 70-79', 0),
            'pfai_60_69':                         components.get('PFAI Score - 60-69', 0),
            'pfai_sub60':                         components.get('PFAI Score - <60', 0),
            'me_best_in_field':                   components.get('Market Expectation - Best in Field', 0),
            'me_chronic_over':                    components.get('Market Expectation - Chronic Overperformer', 0),
            'me_strong_over':                     components.get('Market Expectation - Strong Overperformer', 0),
            'me_moderate_out':                    components.get('Market Expectation - Moderate Outperformer', 0),
            'me_above_avg':                       components.get('Market Expectation - Above Average', 0),
            'me_worst_in_field':                  components.get('Market Expectation - Worst in Field', 0),
            'me_chronic_under':                   components.get('Market Expectation - Chronic Underperformer', 0),
            'me_significant_under':               components.get('Market Expectation - Significant Underperformer', 0),
            'me_mild_under':                      components.get('Market Expectation - Mild Underperformer', 0),
            'me_below_avg':                       components.get('Market Expectation - Below Average', 0),
            'me_neutral':                         components.get('Market Expectation - Neutral', 0),
        }

        data_row = [
            date_str,
            meeting_name or '',
            track,
            race_num or '',
            distance or '',
            race_class or '',
            track_cond or '',
            horse_name or '',
            barrier or '',
            weight or '',
            jockey or '',
            trainer or '',
            form or '',
            score or 0,
            pred_odds_clean,
            win_prob or '',
            finish_pos or '',
            sp or '',
            won,
            placed,
            roi,
        ]

        for col in component_columns:
            data_row.append(component_values.get(col, 0))

        csv_data_dict = csv_data if isinstance(csv_data, dict) else {}
        for field_name in csv_field_names:
            data_row.append(csv_data_dict.get(field_name, ''))

        writer.writerow(data_row)

    output = make_response(si.getvalue())
    filename = f"ml_complete_data_{datetime.now().strftime('%Y%m%d')}"
    if track_filter:
        filename += f"_{track_filter}"
    if min_score_filter:
        filename += f"_min{int(min_score_filter)}"
    filename += ".csv"

    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    output.headers["Content-type"] = "text/csv"

    del query_results
    del si
    del writer
    import gc
    gc.collect()

    return output

@app.route("/api/data/betting-filters")
@login_required
def api_betting_filters():
    if not current_user.is_admin:
        return jsonify({'error': 'Admin access required'}), 403

    from collections import defaultdict
    import re as _re

    track_filter = request.args.get('track', '')
    date_from    = request.args.get('date_from', '')
    date_to      = request.args.get('date_to', '')
    limit_param  = request.args.get('limit', '200')
    stake        = 10.0

    # ── Race ID subquery ──────────────────────────────────────────────
    race_id_q = db.session.query(Race.id).join(
        Meeting, Race.meeting_id == Meeting.id
    ).join(Horse, Horse.race_id == Race.id
    ).join(Result, Result.horse_id == Horse.id
    ).filter(Result.finish_position > 0)

    if track_filter:
        race_id_q = race_id_q.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        race_id_q = race_id_q.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        race_id_q = race_id_q.filter(Meeting.uploaded_at <= date_to)

    all_ids = race_id_q.add_columns(Meeting.uploaded_at).distinct().order_by(
        Meeting.uploaded_at.desc(), Race.id.desc()
    ).all()

    if limit_param == 'all':
        race_ids = [r[0] for r in all_ids]
    else:
        limit = int(limit_param) if str(limit_param).isdigit() else 200
        race_ids = [r[0] for r in all_ids[:limit]]

    if not race_ids:
        return jsonify({'day_of_week': {}, 'win_prob_filter': [], 'confidence_filter': [], 'confidence_tiers': {}})

    # ── Main data pull ────────────────────────────────────────────────
    rows = db.session.query(
        Meeting.date,
        Meeting.uploaded_at,
        Meeting.id,
        Race.race_number,
        Prediction.score,
        Prediction.win_probability,
        Prediction.predicted_odds,
        Result.finish_position,
        Result.sp
    ).join(Race,       Race.meeting_id      == Meeting.id
    ).join(Horse,      Horse.race_id        == Race.id
    ).join(Prediction, Prediction.horse_id  == Horse.id
    ).join(Result,     Result.horse_id      == Horse.id
    ).filter(
        Result.finish_position > 0,
        Race.id.in_(race_ids)
    ).all()

    # ── Group by race ─────────────────────────────────────────────────
    from collections import defaultdict
    races_map = defaultdict(list)
    for row in rows:
        key = (row[2], row[3])  # meeting_id, race_number
        races_map[key].append(row)

    # ── A: Day of week ────────────────────────────────────────────────
    DAYS = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
    day_stats = {d: {'races': 0, 'wins': 0, 'profit': 0.0} for d in DAYS}

    for key, horses in races_map.items():
        top = max(horses, key=lambda x: x[4])  # x[4] = score
        row = top
        # Determine date
        date_val = row[0]  # Meeting.date
        if not date_val:
            ts = row[1]    # Meeting.uploaded_at
            date_val = ts.date() if ts else None
        if not date_val:
            continue
        day_name = DAYS[date_val.weekday()]
        won    = row[7] == 1
        sp     = row[8] or 0
        profit = (sp * stake - stake) if won else -stake
        day_stats[day_name]['races']  += 1
        day_stats[day_name]['wins']   += 1 if won else 0
        day_stats[day_name]['profit'] += profit

    day_result = {}
    for day, s in day_stats.items():
        n = s['races']
        if n == 0:
            continue
        day_result[day] = {
            'races':       n,
            'wins':        s['wins'],
            'strike_rate': round(s['wins'] / n * 100, 1),
            'roi':         round(s['profit'] / (n * stake) * 100, 1),
            'profit':      round(s['profit'], 2),
        }

    # ── B: Win probability as betting filter ──────────────────────────
    thresholds_wp = [0, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 80, 90]
    wp_accum = {t: {'count': 0, 'wins': 0, 'profit': 0.0} for t in thresholds_wp}

    for key, horses in races_map.items():
        top = max(horses, key=lambda x: x[4])
        wp_raw = top[5]  # win_probability
        sp     = top[8] or 0
        won    = top[7] == 1
        profit = (sp * stake - stake) if won else -stake
        try:
            wp = float(str(wp_raw).replace('%','').strip())
        except (ValueError, TypeError):
            continue
        for t in thresholds_wp:
            if wp >= t:
                wp_accum[t]['count']  += 1
                wp_accum[t]['wins']   += 1 if won else 0
                wp_accum[t]['profit'] += profit

    wp_result = []
    for t in thresholds_wp:
        v = wp_accum[t]
        n = v['count']
        if n == 0:
            continue
        wp_result.append({
            'threshold':   t,
            'count':       n,
            'wins':        v['wins'],
            'strike_rate': round(v['wins'] / n * 100, 1),
            'roi':         round(v['profit'] / (n * stake) * 100, 1),
            'profit':      round(v['profit'], 2),
        })

    # ── C: Race confidence (score gap) as betting filter ─────────────
    gap_thresholds = [0, 5, 10, 15, 20, 25, 30, 40, 50]
    gap_accum = {t: {'count': 0, 'wins': 0, 'profit': 0.0} for t in gap_thresholds}

    # Also tier breakdown
    tier_defs = [
        ('Dominant (50+ gap)',   50, 9999),
        ('Clear (30-49 gap)',    30,   49),
        ('Comfortable (20-29)', 20,   29),
        ('Moderate (15-19)',    15,   19),
        ('Marginal (10-14)',    10,   14),
        ('Slim (5-9)',           5,    9),
        ('Tight (<5)',           0,    4),
    ]
    tier_stats = {label: {'count': 0, 'wins': 0, 'profit': 0.0} for label, _, _ in tier_defs}

    for key, horses in races_map.items():
        sorted_horses = sorted(horses, key=lambda x: x[4], reverse=True)
        top    = sorted_horses[0]
        second = sorted_horses[1] if len(sorted_horses) > 1 else None
        gap    = top[4] - (second[4] if second else 0)
        sp     = top[8] or 0
        won    = top[7] == 1
        profit = (sp * stake - stake) if won else -stake

        for t in gap_thresholds:
            if gap >= t:
                gap_accum[t]['count']  += 1
                gap_accum[t]['wins']   += 1 if won else 0
                gap_accum[t]['profit'] += profit

        for label, lo, hi in tier_defs:
            if lo <= gap <= hi:
                tier_stats[label]['count']  += 1
                tier_stats[label]['wins']   += 1 if won else 0
                tier_stats[label]['profit'] += profit
                break

    gap_result = []
    for t in gap_thresholds:
        v = gap_accum[t]
        n = v['count']
        if n == 0:
            continue
        gap_result.append({
            'threshold':   t,
            'count':       n,
            'wins':        v['wins'],
            'strike_rate': round(v['wins'] / n * 100, 1),
            'roi':         round(v['profit'] / (n * stake) * 100, 1),
            'profit':      round(v['profit'], 2),
        })

    tier_result = {}
    for label, _, _ in tier_defs:
        s = tier_stats[label]
        n = s['count']
        if n == 0:
            continue
        tier_result[label] = {
            'count':       n,
            'wins':        s['wins'],
            'strike_rate': round(s['wins'] / n * 100, 1),
            'roi':         round(s['profit'] / (n * stake) * 100, 1),
            'profit':      round(s['profit'], 2),
        }

    db.session.expunge_all()
    db.session.remove()

    return jsonify({
        'day_of_week':       day_result,
        'win_prob_filter':   wp_result,
        'confidence_filter': gap_result,
        'confidence_tiers':  tier_result,
    })

# ─────────────────────────────────────────────────────────────
# BACKTEST DASHBOARD ROUTES
# Add these routes to app.py alongside your other routes
# ─────────────────────────────────────────────────────────────

@app.route('/backtest')
@login_required
def backtest():
    """Backtest dashboard - shows latest RF + component analysis results."""
    if not current_user.is_admin:
        flash('Admin access required.', 'danger')
        return redirect(url_for('dashboard'))
    
    from sqlalchemy import text
    # Get latest completed run
    latest_run = db.session.execute(text("""
        SELECT * FROM backtest_runs
        ORDER BY id DESC LIMIT 1
    """)).fetchone()

    run_count = db.session.execute(text(
        "SELECT COUNT(*) FROM backtest_runs"
    )).scalar() or 0

    all_runs = db.session.execute(text("""
        SELECT * FROM backtest_runs ORDER BY id DESC LIMIT 20
    """)).fetchall()

    feature_results = []
    component_results = []
    momentum_results = []

    if latest_run and latest_run.status == 'complete':
        feature_results = db.session.execute(text("""
            SELECT * FROM backtest_feature_importance
            WHERE run_id = :run_id
            ORDER BY importance_rank ASC
        """), {'run_id': latest_run.id}).fetchall()

        component_results = db.session.execute(text("""
            SELECT * FROM backtest_component_analysis
            WHERE run_id = :run_id
            ORDER BY ABS(roi) DESC
        """), {'run_id': latest_run.id}).fetchall()

        momentum_results_all = db.session.execute(text("""
            SELECT * FROM backtest_momentum_analysis
            WHERE run_id = :run_id AND scope = 'all_horses'
            ORDER BY roi DESC
        """), {'run_id': latest_run.id}).fetchall()

        momentum_results_tp = db.session.execute(text("""
            SELECT * FROM backtest_momentum_analysis
            WHERE run_id = :run_id AND scope = 'top_pick'
            ORDER BY roi DESC
        """), {'run_id': latest_run.id}).fetchall()

    return render_template(
        'backtest.html',
        latest_run=latest_run,
        run_count=run_count,
        all_runs=all_runs,
        feature_results=feature_results,
        component_results=component_results,
        momentum_results_all=momentum_results_all,
        momentum_results_tp=momentum_results_tp
    )

@app.route('/backtest/run-now')
@login_required
def backtest_run_now():
    """Trigger a manual backtest run (admin only)."""
    import subprocess
    import threading

    if not current_user.is_admin:
        flash('Admin access required.', 'danger')
        return redirect(url_for('backtest'))

    def run_backtest():
        try:
            subprocess.run(['python', 'backtest.py'], timeout=3600, check=True)
        except Exception as e:
            app.logger.error(f"Manual backtest failed: {e}")

    thread = threading.Thread(target=run_backtest, daemon=True)
    thread.start()

    flash('Backtest started in the background. Results will appear here when complete (may take several minutes).', 'info')
    return redirect(url_for('backtest'))

@app.route("/best-bets")
@login_required
def best_bets():
    if not current_user.is_admin:
        flash("Access denied. Admin privileges required.", "danger")
        return redirect(url_for("history"))
    """Show today's best bets based on active positive ROI components"""
    from models import Component, Prediction
    from datetime import datetime, timedelta

    # Get filter parameters
    hours_back = request.args.get('hours', default=80, type=int)
    min_score = request.args.get('min_score', type=float)
    min_gap = request.args.get('min_gap', type=float)
    mode = request.args.get('mode', default='top_pick')

    # Get all active components
    active_components = Component.query.filter_by(is_active=True).all()
    component_names = {c.component_name for c in active_components}

    # Get recent meetings (last X hours)
    cutoff = datetime.utcnow() - timedelta(hours=hours_back)
    recent_meetings = Meeting.query.filter(Meeting.uploaded_at >= cutoff).order_by(Meeting.meeting_name.asc()).all()

    best_bets = []
    total_horses_scanned = 0

    for meeting in recent_meetings:
        for race in meeting.races:
            # Build scored list for ALL horses in race
            horses_in_race = []
            for horse in race.horses:
                total_horses_scanned += 1
                if horse.prediction:
                    horses_in_race.append({
                        'horse': horse,
                        'score': horse.prediction.score
                    })
            horses_in_race.sort(key=lambda x: x['score'], reverse=True)
            if not horses_in_race:
                continue

            top_score = horses_in_race[0]['score']
            second_score = horses_in_race[1]['score'] if len(horses_in_race) > 1 else 0
            score_gap = top_score - second_score  # gap still measured from top horse

            # NEW: scan every horse, not just the top pick
            for rank_idx, horse_data in enumerate(horses_in_race):
                horse = horse_data['horse']
                if not horse.prediction:
                    continue

                is_top_pick = (rank_idx == 0)
                rank_in_race = rank_idx + 1

                # Apply min_gap filter only to top pick (gap is a top-pick metric)
                if min_gap and is_top_pick and score_gap < min_gap:
                    continue

                if min_score and horse.prediction.score < min_score:
                    continue

                # Mode filter
                if mode == 'top_pick' and not is_top_pick:
                    continue
                if mode == 'non_top_pick' and is_top_pick:
                    continue

                components = parse_notes_components(horse.prediction.notes)
                matched_components = []
                for comp_name in components.keys():
                    if comp_name in component_names:
                        comp_obj = next((c for c in active_components if c.component_name == comp_name), None)
                        if comp_obj:
                            matched_components.append({
                                'name': comp_name,
                                'roi': comp_obj.roi_percentage,
                                'sr': comp_obj.strike_rate,
                                'appearances': comp_obj.appearances
                            })

                if matched_components:
                    matched_components.sort(key=lambda x: x['roi'], reverse=True)

                    # Score gap only meaningful for top pick; show 0 for others
                    horse_score_gap = score_gap if is_top_pick else (
                        horses_in_race[rank_idx - 1]['score'] - horse.prediction.score
                        if rank_idx > 0 else 0
                    )

                    best_bets.append({
                        'meeting_id': meeting.id,
                        'meeting_name': meeting.meeting_name,
                        'uploaded_at': meeting.uploaded_at,
                        'race_id': race.id,
                        'race_number': race.race_number,
                        'distance': race.distance,
                        'race_class': race.race_class,
                        'track_condition': race.track_condition,
                        'horse_id': horse.id,
                        'horse_name': horse.horse_name,
                        'score': horse.prediction.score,
                        'score_gap': horse_score_gap,   # gap to horse above it
                        'predicted_odds': horse.prediction.predicted_odds,
                        'win_probability': horse.prediction.win_probability,
                        'components': matched_components,
                        'component_count': len(matched_components),
                        'jockey': horse.jockey,
                        'trainer': horse.trainer,
                        'barrier': horse.barrier,
                        'weight': horse.weight,
                        'form': horse.form,
                        'is_top_pick': is_top_pick,         # NEW
                        'rank_in_race': rank_in_race,       # NEW
                    })

    best_bets.sort(key=lambda x: x['score'], reverse=True)

    # Group by meeting → race
    meetings_with_bets = {}
    for bet in best_bets:
        meeting_key = bet['meeting_name']
        if meeting_key not in meetings_with_bets:
            meetings_with_bets[meeting_key] = {
                'meeting_id': bet['meeting_id'],
                'meeting_name': bet['meeting_name'],
                'uploaded_at': bet['uploaded_at'],
                'races': {}
            }
        race_key = bet['race_number']
        if race_key not in meetings_with_bets[meeting_key]['races']:
            meetings_with_bets[meeting_key]['races'][race_key] = {
                'race_number': bet['race_number'],
                'distance': bet['distance'],
                'race_class': bet['race_class'],
                'track_condition': bet['track_condition'],
                'horses': []
            }
        meetings_with_bets[meeting_key]['races'][race_key]['horses'].append(bet)

    # Sort meetings by name (YYMMDD prefix)
    meetings_with_bets = dict(sorted(meetings_with_bets.items(), key=lambda x: x[0]))

    return render_template("best_bets.html",
        best_bets=best_bets,
        meetings_with_bets=meetings_with_bets,
        total_bets=len(best_bets),
        total_horses_scanned=total_horses_scanned,
        active_components=active_components,
        hours_back=hours_back,
        min_score=min_score,
        min_gap=min_gap,
        mode=mode,   # NEW
    )
@app.route("/best-bets/post", methods=["POST"])
@login_required
def post_best_bets_manual():
    """Manually post selected best bets to Telegram and Twitter"""
    if not current_user.is_admin:
        flash("Admin only", "danger")
        return redirect(url_for("best_bets"))
    
    try:
        # Get the selected horse IDs from the form
        horse_ids = request.form.getlist('bet_ids[]')
        
        if not horse_ids:
            flash("No bets selected", "warning")
            return redirect(url_for("best_bets"))
        
        # Group selected bets by meeting
        bets_by_meeting = {}
        
        for horse_id in horse_ids:
            horse = Horse.query.get(int(horse_id))
            if not horse or not horse.prediction:
                continue
            
            race = Race.query.get(horse.race_id)
            meeting = Meeting.query.get(race.meeting_id)
            
            # Mark as flagged
            if not horse.prediction.best_bet_flagged_at:
                horse.prediction.best_bet_flagged_at = datetime.utcnow()
                db.session.add(horse.prediction)
            
            # Build bet data structure matching what Telegram expects
            meeting_name = meeting.meeting_name
            if meeting_name not in bets_by_meeting:
                bets_by_meeting[meeting_name] = []
            
            bets_by_meeting[meeting_name].append({
                'race_number': race.race_number,
                'horse_name': horse.horse_name,
                'predicted_odds': horse.prediction.predicted_odds
            })
        
        db.session.commit()
        
        # Post to Telegram and Twitter
        posted_count = 0
        for meeting_name, meeting_bets in bets_by_meeting.items():
            success = post_best_bets_to_telegram(meeting_bets, meeting_name)
            if success:
                posted_count += 1
        
        flash(f"✓ Posted {posted_count} meeting(s) to Telegram and Twitter", "success")
        
    except Exception as e:
        logger.error(f"Failed to post best bets: {str(e)}", exc_info=True)
        flash(f"✗ Failed to post: {str(e)}", "danger")
    
    return redirect(url_for("best_bets"))
@app.route("/test-telegram")
@login_required
def test_telegram():
    """Test Telegram connection"""
    if not current_user.is_admin:
        flash("Admin only", "danger")
        return redirect(url_for("dashboard"))
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHANNEL,
            'text': '🧪 Test message from The Form Analyst\n\nIf you see this, the connection works!',
            'parse_mode': 'Markdown'
        }
        
        response = requests.post(url, json=payload, timeout=10)
        
        if response.status_code == 200:
            flash("✓ Test message sent to Telegram successfully!", "success")
        else:
            flash(f"✗ Telegram API error: {response.status_code} - {response.text}", "danger")
    except Exception as e:
        flash(f"✗ Error: {e}", "danger")
    
    return redirect(url_for("admin_panel"))
        
    # ----- CHAT SYSTEM ROUTES -----

RACING_SYSTEM_PROMPT = """You are an expert horse racing analyst with direct access to The Form Analyst database through tools.

You can call these tools to answer user questions:
1. query_database - Find meetings, horses, results, or calculate statistics
2. calculate_quaddie - Generate optimal quaddie combinations based on scores
3. analyze_patterns - Discover patterns in historical performance, components, and horse characteristics

When users ask questions:
- Call the appropriate tools to get data
- Analyze the results intelligently
- Provide specific, actionable recommendations
- Explain your reasoning

For quaddie questions: Use calculate_quaddie to get top selections, explain why they're chosen
For pattern analysis: Use analyze_patterns to find what's working/not working
For specific data: Use query_database with appropriate filters

Available pattern analysis types:
- score_performance: How different score ranges perform
- trainer_stats: Trainer performance analysis
- jockey_stats: Jockey performance analysis  
- track_specialists: Horses that excel at specific tracks
- overlays: Value bets where predictions beat market
- horse_characteristics: Age/sex combination patterns (e.g., 3YO Mares)
- component_performance: Which scoring components are profitable
- class_drop_patterns: Performance by class change magnitude

Be proactive - if you need more data to answer properly, call multiple tools.
Keep responses concise unless detailed analysis is requested.
Always remind users that gambling involves risk."""

def execute_tool(tool_name, tool_input, user_id):
    """Execute tool calls from Claude"""
    
    if tool_name == "query_database":
        query_type = tool_input.get("query_type")
        filters = tool_input.get("filters", {})
        
        if query_type == "meetings":
            query = Meeting.query
            if filters.get("meeting_name"):
                query = query.filter(Meeting.meeting_name.like(f"%{filters['meeting_name']}%"))
            if filters.get("date"):
                query = query.filter(Meeting.meeting_name.like(f"{filters['date']}%"))
            meetings = query.order_by(Meeting.uploaded_at.desc()).limit(50).all()
            return [{"name": m.meeting_name, "race_count": len(m.races)} for m in meetings]
        
        elif query_type == "horses":
            query = db.session.query(Horse, Race, Meeting, Prediction).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).outerjoin(
                Prediction, Horse.id == Prediction.horse_id
            )
            
            if filters.get("meeting_name"):
                query = query.filter(Meeting.meeting_name == filters["meeting_name"])
            if filters.get("min_score"):
                query = query.filter(Prediction.score >= filters["min_score"])
            if filters.get("race_number"):
                query = query.filter(Race.race_number == filters["race_number"])
                
            horses = query.limit(100).all()
            return [{
                "horse": h.horse_name,
                "race": r.race_number,
                "meeting": m.meeting_name,
                "score": p.score if p else None,
                "predicted_odds": p.predicted_odds if p else None,
                "jockey": h.jockey,
                "trainer": h.trainer,
                "barrier": h.barrier
            } for h, r, m, p in horses]
        
        elif query_type == "results":
            results = db.session.query(Result, Horse, Race, Meeting, Prediction).join(
                Horse, Result.horse_id == Horse.id
            ).outerjoin(
                Prediction, Horse.id == Prediction.horse_id
            ).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).filter(Result.finish_position > 0)
            
            if filters.get("won_only"):
                results = results.filter(Result.finish_position == 1)
            if filters.get("min_score"):
                results = results.filter(Prediction.score >= filters["min_score"])
            if filters.get("meeting_name"):
                results = results.filter(Meeting.meeting_name.like(f"%{filters['meeting_name']}%"))
                
            results = results.order_by(Result.recorded_at.desc()).limit(1000).all()
            
            return [{
                "meeting": m.meeting_name,
                "race": r.race_number,
                "horse": h.horse_name,
                "position": res.finish_position,
                "sp": res.sp,
                "score": p.score if p else None,
                "predicted_odds": p.predicted_odds if p else None
            } for res, h, r, m, p in results]
        
        elif query_type == "statistics":
            results = db.session.query(Result, Horse, Race, Meeting, Prediction).join(
                Horse, Result.horse_id == Horse.id
            ).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).outerjoin(
                Prediction, Horse.id == Prediction.horse_id
            ).filter(Result.finish_position > 0).all()
            
            unique_races = set()
            wins = 0
            places = 0
            high_score_wins = 0
            high_score_total = 0
            
            for res, h, race, m, p in results:
                race_key = f"{m.id}_{race.race_number}"
                
                if race_key not in unique_races:
                    unique_races.add(race_key)
                    
                    if res.finish_position == 1:
                        wins += 1
                    if res.finish_position <= 3:
                        places += 1
                    
                    if p and p.score >= 80:
                        high_score_total += 1
                        if res.finish_position == 1:
                            high_score_wins += 1
            
            total = len(unique_races)
            
            return {
                "total_races": total,
                "wins": wins,
                "strike_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "place_rate": round(places / total * 100, 1) if total > 0 else 0,
                "high_score_strike": round(high_score_wins / high_score_total * 100, 1) if high_score_total > 0 else 0
            }
    
    elif tool_name == "calculate_quaddie":
        meeting_name = tool_input.get("meeting_name")
        min_score = tool_input.get("min_score", 70)
        
        meeting = Meeting.query.filter_by(meeting_name=meeting_name).first()
        if not meeting:
            return {"error": "Meeting not found"}
        
        quaddie_races = [r for r in meeting.races if 5 <= r.race_number <= 8]
        
        if len(quaddie_races) < 4:
            return {"error": "Not enough races for quaddie (need races 5-8)"}
        
        combinations = []
        for race in quaddie_races[:4]:
            top_horses = sorted(
                [h for h in race.horses if h.prediction and h.prediction.score >= min_score],
                key=lambda h: h.prediction.score,
                reverse=True
            )[:3]
            
            combinations.append([{
                "horse": h.horse_name,
                "score": h.prediction.score,
                "odds": h.prediction.predicted_odds,
                "barrier": h.barrier
            } for h in top_horses])
        
        return {
            "meeting": meeting_name,
            "races": [r.race_number for r in quaddie_races[:4]],
            "selections": combinations
        }
    
    elif tool_name == "analyze_patterns":
        analysis_type = tool_input.get("analysis_type")
        
        if analysis_type == "score_performance":
            results = db.session.query(Result, Prediction, Horse, Race, Meeting).join(
                Horse, Result.horse_id == Horse.id
            ).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).outerjoin(
                Prediction, Horse.id == Prediction.horse_id
            ).filter(Result.finish_position > 0).all()
            
            score_ranges = {
                "100+": {"wins": 0, "total": 0},
                "80-99": {"wins": 0, "total": 0},
                "60-79": {"wins": 0, "total": 0},
                "40-59": {"wins": 0, "total": 0},
                "<40": {"wins": 0, "total": 0}
            }
            
            races_seen = {key: set() for key in score_ranges.keys()}
            
            for r, p, h, race, m in results:
                if not p:
                    continue
                    
                score = p.score
                if score >= 100:
                    key = "100+"
                elif score >= 80:
                    key = "80-99"
                elif score >= 60:
                    key = "60-79"
                elif score >= 40:
                    key = "40-59"
                else:
                    key = "<40"
                
                race_key = f"{m.id}_{race.race_number}"
                
                if race_key not in races_seen[key]:
                    races_seen[key].add(race_key)
                    score_ranges[key]["total"] += 1
                    if r.finish_position == 1:
                        score_ranges[key]["wins"] += 1
            
            for key in score_ranges:
                total = score_ranges[key]["total"]
                wins = score_ranges[key]["wins"]
                score_ranges[key]["strike_rate"] = round(wins / total * 100, 1) if total > 0 else 0
            
            return score_ranges
        
        elif analysis_type == "overlays":
            results = db.session.query(Result, Prediction, Horse, Meeting, Race).join(
                Horse, Result.horse_id == Horse.id
            ).join(
                Prediction, Horse.id == Prediction.horse_id
            ).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).filter(Result.finish_position > 0).filter(Result.finish_position == 1).all()
            
            races_seen = set()
            overlays = []
            
            for r, p, h, m, race in results:
                race_key = f"{m.id}_{race.race_number}"
                
                if race_key in races_seen:
                    continue
                    
                races_seen.add(race_key)
                
                if p.predicted_odds and r.sp:
                    try:
                        predicted = float(str(p.predicted_odds).replace('$', '').strip())
                        if predicted < r.sp:
                            overlay_percent = ((r.sp - predicted) / predicted) * 100
                            overlays.append({
                                "horse": h.horse_name,
                                "meeting": m.meeting_name,
                                "race": race.race_number,
                                "predicted": predicted,
                                "sp": r.sp,
                                "overlay": round(overlay_percent, 1),
                                "score": p.score
                            })
                    except (ValueError, AttributeError):
                        continue
            
            return sorted(overlays, key=lambda x: x["overlay"], reverse=True)[:20]
        
        elif analysis_type == "horse_characteristics":
            results = db.session.query(Result, Horse, Race, Meeting, Prediction).join(
                Horse, Result.horse_id == Horse.id
            ).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).join(
                Prediction, Horse.id == Prediction.horse_id
            ).filter(Result.finish_position > 0).all()
            
            patterns = {}
            races_seen = {}
            
            for r, h, race, m, p in results:
                csv_data = h.csv_data or {}
                age = csv_data.get('horse age')
                sex = csv_data.get('horse sex')
                
                if not age or not sex:
                    continue
                
                pattern_key = f"{age}yo {sex}"
                race_key = f"{m.id}_{race.race_number}"
                
                if pattern_key not in patterns:
                    patterns[pattern_key] = {
                        "wins": 0,
                        "total": 0,
                        "total_profit": 0,
                        "stake": 0
                    }
                    races_seen[pattern_key] = set()
                
                if race_key not in races_seen[pattern_key]:
                    races_seen[pattern_key].add(race_key)
                    patterns[pattern_key]["total"] += 1
                    patterns[pattern_key]["stake"] += 10
                    
                    if r.finish_position == 1:
                        patterns[pattern_key]["wins"] += 1
                        if r.sp:
                            patterns[pattern_key]["total_profit"] += (r.sp * 10 - 10)
                    else:
                        patterns[pattern_key]["total_profit"] -= 10
            
            pattern_list = []
            for pattern, stats in patterns.items():
                if stats["total"] >= 10:
                    strike_rate = (stats["wins"] / stats["total"]) * 100
                    roi = (stats["total_profit"] / stats["stake"]) * 100
                    
                    pattern_list.append({
                        "pattern": pattern,
                        "wins": stats["wins"],
                        "total": stats["total"],
                        "strike_rate": round(strike_rate, 1),
                        "roi": round(roi, 1),
                        "profit": round(stats["total_profit"], 2)
                    })
            
            return sorted(pattern_list, key=lambda x: x["roi"], reverse=True)[:30]
        
        elif analysis_type == "trainer_stats":
            results = db.session.query(Result, Horse, Race, Meeting, Prediction).join(
                Horse, Result.horse_id == Horse.id
            ).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).outerjoin(
                Prediction, Horse.id == Prediction.horse_id
            ).filter(Result.finish_position > 0).all()
            
            trainer_stats = {}
            races_seen = {}
            
            for r, h, race, m, p in results:
                trainer = h.trainer
                race_key = f"{m.id}_{race.race_number}"
                
                if trainer not in trainer_stats:
                    trainer_stats[trainer] = {"wins": 0, "total": 0, "places": 0}
                    races_seen[trainer] = set()
                
                if race_key not in races_seen[trainer]:
                    races_seen[trainer].add(race_key)
                    trainer_stats[trainer]["total"] += 1
                    if r.finish_position == 1:
                        trainer_stats[trainer]["wins"] += 1
                    if r.finish_position <= 3:
                        trainer_stats[trainer]["places"] += 1
            
            trainer_list = []
            for trainer, stats in trainer_stats.items():
                if stats["total"] >= 10:
                    trainer_list.append({
                        "trainer": trainer,
                        "wins": stats["wins"],
                        "total": stats["total"],
                        "strike_rate": round(stats["wins"] / stats["total"] * 100, 1),
                        "place_rate": round(stats["places"] / stats["total"] * 100, 1)
                    })
            
            return sorted(trainer_list, key=lambda x: x["strike_rate"], reverse=True)[:20]
        
        elif analysis_type == "jockey_stats":
            results = db.session.query(Result, Horse, Race, Meeting, Prediction).join(
                Horse, Result.horse_id == Horse.id
            ).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).outerjoin(
                Prediction, Horse.id == Prediction.horse_id
            ).filter(Result.finish_position > 0).all()
            
            jockey_stats = {}
            races_seen = {}
            
            for r, h, race, m, p in results:
                jockey = h.jockey
                race_key = f"{m.id}_{race.race_number}"
                
                if jockey not in jockey_stats:
                    jockey_stats[jockey] = {"wins": 0, "total": 0, "places": 0}
                    races_seen[jockey] = set()
                
                if race_key not in races_seen[jockey]:
                    races_seen[jockey].add(race_key)
                    jockey_stats[jockey]["total"] += 1
                    if r.finish_position == 1:
                        jockey_stats[jockey]["wins"] += 1
                    if r.finish_position <= 3:
                        jockey_stats[jockey]["places"] += 1
            
            jockey_list = []
            for jockey, stats in jockey_stats.items():
                if stats["total"] >= 10:
                    jockey_list.append({
                        "jockey": jockey,
                        "wins": stats["wins"],
                        "total": stats["total"],
                        "strike_rate": round(stats["wins"] / stats["total"] * 100, 1),
                        "place_rate": round(stats["places"] / stats["total"] * 100, 1)
                    })
            
            return sorted(jockey_list, key=lambda x: x["strike_rate"], reverse=True)[:20]
        
        elif analysis_type == "track_specialists":
            results = db.session.query(Result, Horse, Meeting, Race, Prediction).join(
                Horse, Result.horse_id == Horse.id
            ).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).outerjoin(
                Prediction, Horse.id == Prediction.horse_id
            ).filter(Result.finish_position > 0).all()
            
            horse_track_stats = {}
            races_seen = {}
            
            for r, h, m, race, p in results:
                track = m.meeting_name.split('_')[1] if '_' in m.meeting_name else 'Unknown'
                key = f"{h.horse_name}_{track}"
                race_key = f"{m.id}_{race.race_number}"
                
                if key not in horse_track_stats:
                    horse_track_stats[key] = {
                        "horse": h.horse_name,
                        "track": track,
                        "wins": 0,
                        "total": 0
                    }
                    races_seen[key] = set()
                
                if race_key not in races_seen[key]:
                    races_seen[key].add(race_key)
                    horse_track_stats[key]["total"] += 1
                    if r.finish_position == 1:
                        horse_track_stats[key]["wins"] += 1
            
            specialists = []
            for stats in horse_track_stats.values():
                if stats["total"] >= 3:
                    strike_rate = stats["wins"] / stats["total"] * 100
                    if strike_rate >= 50:
                        specialists.append({
                            "horse": stats["horse"],
                            "track": stats["track"],
                            "wins": stats["wins"],
                            "total": stats["total"],
                            "strike_rate": round(strike_rate, 1)
                        })
            
            return sorted(specialists, key=lambda x: x["strike_rate"], reverse=True)[:20]
        
        elif analysis_type == "component_performance":
            results = db.session.query(Result, Horse, Race, Meeting, Prediction).join(
                Horse, Result.horse_id == Horse.id
            ).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).join(
                Prediction, Horse.id == Prediction.horse_id
            ).filter(Result.finish_position > 0).all()
            
            component_stats = {}
            races_seen = {}
            
            for r, h, race, m, p in results:
                if not p.notes:
                    continue
                
                race_key = f"{m.id}_{race.race_number}"
                
                lines = p.notes.split('\n')
                for line in lines:
                    if ':' in line:
                        parts = line.split(':', 1)
                        if len(parts) == 2:
                            score_part = parts[0].strip()
                            component_name = parts[1].strip()
                            
                            if any(skip in component_name.lower() for skip in ['total', 'specialist bonus', 'sectional weighted', 'condition multiplier', 'sectional weight', '└─', 'adj:', 'ℹ️']):
                                continue
                            
                            if component_name not in component_stats:
                                component_stats[component_name] = {
                                    "appearances": 0,
                                    "wins": 0,
                                    "total_profit": 0,
                                    "stake": 0
                                }
                                races_seen[component_name] = set()
                            
                            if race_key not in races_seen[component_name]:
                                races_seen[component_name].add(race_key)
                                component_stats[component_name]["appearances"] += 1
                                component_stats[component_name]["stake"] += 10
                                
                                if r.finish_position == 1:
                                    component_stats[component_name]["wins"] += 1
                                    if r.sp:
                                        component_stats[component_name]["total_profit"] += (r.sp * 10 - 10)
                                else:
                                    component_stats[component_name]["total_profit"] -= 10
            
            component_list = []
            for comp_name, stats in component_stats.items():
                if stats["appearances"] >= 5:
                    strike_rate = (stats["wins"] / stats["appearances"]) * 100
                    roi = (stats["total_profit"] / stats["stake"]) * 100
                    
                    component_list.append({
                        "component": comp_name,
                        "appearances": stats["appearances"],
                        "wins": stats["wins"],
                        "strike_rate": round(strike_rate, 1),
                        "roi": round(roi, 1),
                        "profit": round(stats["total_profit"], 2)
                    })
            
            return sorted(component_list, key=lambda x: x["roi"], reverse=True)[:50]
        
        elif analysis_type == "class_drop_patterns":
            results = db.session.query(Result, Horse, Race, Meeting, Prediction).join(
                Horse, Result.horse_id == Horse.id
            ).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).join(
                Prediction, Horse.id == Prediction.horse_id
            ).filter(Result.finish_position > 0).all()
            
            class_patterns = {
                "Major Drop (30+)": {"wins": 0, "total": 0, "profit": 0, "races_seen": set()},
                "Significant Drop (20-29)": {"wins": 0, "total": 0, "profit": 0, "races_seen": set()},
                "Moderate Drop (10-19)": {"wins": 0, "total": 0, "profit": 0, "races_seen": set()},
                "Small Drop (1-9)": {"wins": 0, "total": 0, "profit": 0, "races_seen": set()},
                "Same Class": {"wins": 0, "total": 0, "profit": 0, "races_seen": set()},
                "Small Rise (1-9)": {"wins": 0, "total": 0, "profit": 0, "races_seen": set()},
                "Moderate Rise (10-19)": {"wins": 0, "total": 0, "profit": 0, "races_seen": set()},
                "Significant Rise (20+)": {"wins": 0, "total": 0, "profit": 0, "races_seen": set()}
            }
            
            for r, h, race, m, p in results:
                race_key = f"{m.id}_{race.race_number}"
                
                if p.notes and ("Stepping DOWN" in p.notes or "Stepping UP" in p.notes):
                    import re
                    for line in p.notes.split('\n'):
                        if "Stepping DOWN" in line or "Stepping UP" in line:
                            match = re.search(r'(DOWN|UP)\s+([\d.]+)\s+class points', line)
                            if match:
                                direction = match.group(1)
                                points = float(match.group(2))
                                
                                if direction == "DOWN":
                                    if points >= 30:
                                        category = "Major Drop (30+)"
                                    elif points >= 20:
                                        category = "Significant Drop (20-29)"
                                    elif points >= 10:
                                        category = "Moderate Drop (10-19)"
                                    else:
                                        category = "Small Drop (1-9)"
                                else:
                                    if points >= 20:
                                        category = "Significant Rise (20+)"
                                    elif points >= 10:
                                        category = "Moderate Rise (10-19)"
                                    else:
                                        category = "Small Rise (1-9)"
                                
                                if race_key not in class_patterns[category]["races_seen"]:
                                    class_patterns[category]["races_seen"].add(race_key)
                                    class_patterns[category]["total"] += 1
                                    if r.finish_position == 1:
                                        class_patterns[category]["wins"] += 1
                                        if r.sp:
                                            class_patterns[category]["profit"] += (r.sp * 10 - 10)
                                    else:
                                        class_patterns[category]["profit"] -= 10
                                break
                else:
                    category = "Same Class"
                    if race_key not in class_patterns[category]["races_seen"]:
                        class_patterns[category]["races_seen"].add(race_key)
                        class_patterns[category]["total"] += 1
                        if r.finish_position == 1:
                            class_patterns[category]["wins"] += 1
                            if r.sp:
                                class_patterns[category]["profit"] += (r.sp * 10 - 10)
                        else:
                            class_patterns[category]["profit"] -= 10
            
            result_list = []
            for category, stats in class_patterns.items():
                if stats["total"] > 0:
                    strike_rate = (stats["wins"] / stats["total"]) * 100
                    roi = (stats["profit"] / (stats["total"] * 10)) * 100
                    
                    result_list.append({
                        "category": category,
                        "wins": stats["wins"],
                        "total": stats["total"],
                        "strike_rate": round(strike_rate, 1),
                        "roi": round(roi, 1),
                        "profit": round(stats["profit"], 2)
                    })
            
            return result_list
    
    return {"error": "Unknown tool or analysis type"}

@app.route('/api/chat', methods=['POST'])
@limiter.limit("10 per minute")
def chat():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Please log in to use chat'}), 401
    
    try:
        user_message = request.json.get('message', '').strip()
        
        if not user_message:
            return jsonify({'error': 'Message cannot be empty'}), 400
        
        if len(user_message) > 500:
            return jsonify({'error': 'Message too long (max 500 characters)'}), 400
        
        user_id = current_user.id
        
        if 'chat_session_id' not in session:
            session['chat_session_id'] = str(uuid.uuid4())
        
        chat_session_id = session['chat_session_id']
        
        from models import ChatMessage
        user_msg = ChatMessage(
            user_id=user_id,
            role='user',
            content=user_message,
            session_id=chat_session_id
        )
        db.session.add(user_msg)
        db.session.commit()
        
        history = ChatMessage.query.filter_by(
            user_id=user_id,
            session_id=chat_session_id
        ).order_by(ChatMessage.timestamp.desc()).limit(10).all()
        
        history = list(reversed(history))
        
        messages = []
        for msg in history:
            messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        tools = [
            {
                "name": "query_database",
                "description": "Execute SQL query on the racing database to find specific horses, races, results, or statistics",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query_type": {
                            "type": "string",
                            "enum": ["meetings", "horses", "results", "statistics"],
                            "description": "Type of query to run"
                        },
                        "filters": {
                            "type": "object",
                            "description": "Filters like meeting_name, date, track, min_score, race_number, won_only"
                        }
                    },
                    "required": ["query_type"]
                }
            },
            {
                "name": "calculate_quaddie",
                "description": "Calculate best quaddie combinations for a meeting (races 5-8) based on scores and overlays",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "meeting_name": {
                            "type": "string",
                            "description": "Full meeting name like '260131_Caulfield'"
                        },
                        "min_score": {
                            "type": "number",
                            "description": "Minimum score threshold for selections",
                            "default": 70
                        },
                        "max_combinations": {
                            "type": "integer",
                            "description": "Maximum number of combinations to return",
                            "default": 10
                        }
                    },
                    "required": ["meeting_name"]
                }
            },
            {
                "name": "analyze_patterns",
                "description": "Analyze historical results to find patterns in performance, components, and horse characteristics",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "analysis_type": {
                            "type": "string",
                            "enum": [
                                "score_performance",
                                "trainer_stats",
                                "jockey_stats",
                                "track_specialists",
                                "overlays",
                                "horse_characteristics",
                                "component_performance",
                                "class_drop_patterns"
                            ],
                            "description": "Type of pattern analysis: score_performance, trainer_stats, jockey_stats, track_specialists, overlays, horse_characteristics (age/sex combos), component_performance (scoring component ROI), class_drop_patterns"
                        }
                    },
                    "required": ["analysis_type"]
                }
            }
        ]
        
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            system=RACING_SYSTEM_PROMPT,
            tools=tools,
            messages=messages
        )
        
        assistant_response = ""
        conversation_messages = messages.copy()
        
        while response.stop_reason == "tool_use":
            tool_results = []
            
            for content_block in response.content:
                if content_block.type == "tool_use":
                    tool_name = content_block.name
                    tool_input = content_block.input
                    tool_use_id = content_block.id
                    
                    print(f"Tool called: {tool_name} with input: {tool_input}")
                    
                    result = execute_tool(tool_name, tool_input, user_id)
                    
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": str(result)
                    })
            
            conversation_messages.append({"role": "assistant", "content": response.content})
            conversation_messages.append({"role": "user", "content": tool_results})
            
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=RACING_SYSTEM_PROMPT,
                tools=tools,
                messages=conversation_messages
            )
        
        for content_block in response.content:
            if hasattr(content_block, 'text'):
                assistant_response += content_block.text
        
        assistant_msg = ChatMessage(
            user_id=user_id,
            role='assistant',
            content=assistant_response,
            session_id=chat_session_id
        )
        db.session.add(assistant_msg)
        db.session.commit()
        
        return jsonify({
            'response': assistant_response,
            'message_count': len(conversation_messages)
        })
    
    except Exception as e:
        print(f"Chat error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': 'Failed to process message'}), 500

@app.route('/api/chat/history', methods=['GET'])
def get_chat_history():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Please log in'}), 401
    
    user_id = current_user.id
    chat_session_id = session.get('chat_session_id')
    
    if not chat_session_id:
        return jsonify({'messages': []})
    
    from models import ChatMessage
    messages = ChatMessage.query.filter_by(
        user_id=user_id,
        session_id=chat_session_id
    ).order_by(ChatMessage.timestamp.asc()).limit(20).all()
    
    return jsonify({
        'messages': [{
            'role': msg.role,
            'content': msg.content,
            'timestamp': msg.timestamp.isoformat()
        } for msg in messages]
    })

@app.route('/api/chat/new', methods=['POST'])
def new_chat_session():
    if not current_user.is_authenticated:
        return jsonify({'error': 'Please log in'}), 401
    
    session['chat_session_id'] = str(uuid.uuid4())
    return jsonify({'message': 'New conversation started'})

# ===== POSTGRES FULL RAW DATABASE EXPORT =====
@app.route('/export-all-data')
@login_required
def export_all_data():
    import io
    import csv
    import zipfile
    from flask import send_file as flask_send_file
    
    tables = ['chat_messages', 'components', 'horses', 'meetings', 
              'predictions', 'races', 'results', 'users']
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for table in tables:
            result = db.session.execute(db.text(f'SELECT * FROM "{table}"'))
            rows = result.fetchall()
            columns = result.keys()
            
            csv_buffer = io.StringIO()
            writer = csv.writer(csv_buffer)
            writer.writerow(columns)
            writer.writerows(rows)
            
            zip_file.writestr(f'{table}.csv', csv_buffer.getvalue())
    
    zip_buffer.seek(0)
    return flask_send_file(zip_buffer, mimetype='application/zip', 
                     as_attachment=True, download_name='database_export.zip')
