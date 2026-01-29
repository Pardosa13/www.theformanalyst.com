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

import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

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
    
    # Migration: Add market_id column if it doesn't exist
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)
        races_columns = [col['name'] for col in inspector.get_columns('races')]
        
        if 'market_id' not in races_columns:
            with db.engine.connect() as conn:
                conn.execute(text('ALTER TABLE races ADD COLUMN market_id VARCHAR(255)'))
                conn.commit()
            print("Added market_id column to races table")
    except Exception as e:
        print(f"Migration check: {e}")
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
    
    # Migration: Create components table if it doesn't exist
    try:
        from models import Component
        # This will create the components table if it doesn't exist
        db.create_all()
        
        # Check if we need to seed initial components
        component_count = Component.query.count()
        if component_count == 0:
            print("Seeding initial components...")
            
            # Add some starter components based on your analyzer patterns
            starter_components = [
                {'component_name': '3yo Colt Combo', 'appearances': 38, 'wins': 8, 'strike_rate': 21.1, 'roi_percentage': 109.6, 'is_active': True},
                {'component_name': 'Major Class Drop + Slow Sectional', 'appearances': 5, 'wins': 2, 'strike_rate': 40.0, 'roi_percentage': 720.0, 'is_active': False, 'notes': 'Sample size too small'},
                {'component_name': 'Fast Sectional + Colt', 'appearances': 9, 'wins': 4, 'strike_rate': 44.4, 'roi_percentage': 10.6, 'is_active': True},
                {'component_name': 'Race-Day - Mile + Fastest', 'appearances': 7, 'wins': 3, 'strike_rate': 42.9, 'roi_percentage': 81.4, 'is_active': False, 'notes': 'Borderline sample size'},
                {'component_name': 'Colt Bonus', 'appearances': 45, 'wins': 9, 'strike_rate': 20.0, 'roi_percentage': 82.7, 'is_active': True},
                {'component_name': 'Undefeated on Condition', 'appearances': 26, 'wins': 6, 'strike_rate': 23.1, 'roi_percentage': 46.5, 'is_active': True},
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
def run_analyzer(csv_data, track_condition, is_advanced=False):
    """
    Run the JavaScript analyzer with the CSV data
    Returns list of analysis results
    """
    input_data = {
        'csv_data': csv_data,
        'track_condition': track_condition,
        'is_advanced': is_advanced
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
        
        return json.loads(result.stdout)
        
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
        
        # Build message
        message = f"🏇 *{meeting_name.upper()}*\n\n"
        
        for bet in best_bets:
            message += f"*R{bet['race_number']}*: {bet['horse_name']}\n"
            message += f"📊 Score: {bet['score']:.1f}\n"
            message += f"💎 Predicted Price: {bet['predicted_odds']}\n"
            
            # Add top 2 components
            if bet['components']:
                top_comps = bet['components'][:2]
                comp_str = ", ".join([f"{c['name']} ({c['roi']:.0f}% ROI)" for c in top_comps])
                message += f"🎯 {comp_str}\n"
            
            message += "\n"
        
        message += "⚠️ Think. Is this a bet you really want to place? Gamble Responsibly | 1800 858 858"
        
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
def process_and_store_results(csv_data, filename, track_condition, user_id, is_advanced=False):
    """
    Process CSV through analyzer and store results in database
    """
    # Run the analyzer
    analysis_results = run_analyzer(csv_data, track_condition, is_advanced)
    
    if not analysis_results:
        raise Exception("No results returned from analyzer")
    
    # Create meeting record
    meeting = Meeting(
        user_id=user_id,
        meeting_name=filename.replace('.csv', ''),
        csv_data=csv_data
    )
    db.session.add(meeting)
    db.session.flush()  # Get meeting ID
    
    # Group results by race
    races_data = {}
    for result in analysis_results:
        race_num = result['horse'].get('race number', '0')
        
        # Skip invalid rows (header rows that slipped through)
        if not race_num or not str(race_num).isdigit():
            continue
            
        if race_num not in races_data:
            races_data[race_num] = []
        races_data[race_num].append(result)
    
    # Create race and horse records
    for race_num, horses_results in races_data.items():
        # Get race info from first horse
        first_horse = horses_results[0]['horse'] if horses_results else {}
        
        race = Race(
            meeting_id=meeting.id,
            race_number=int(race_num) if race_num else 0,
            distance=first_horse.get('distance', ''),
            race_class=first_horse.get('class restrictions', ''),
            track_condition=track_condition
        )
        db.session.add(race)
        db.session.flush()
        
        # Create horse and prediction records
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
            
            prediction = Prediction(
                horse_id=horse.id,
                score=result.get('score', 0),
                predicted_odds=result.get('trueOdds', ''),
                win_probability=result.get('winProbability', ''),
                performance_component=result.get('performanceComponent', ''),
                base_probability=result.get('baseProbability', ''),
                notes=result.get('notes', '')
            )
            db.session.add(prediction)
    
    db.session.commit()
    
    # CLEANUP - Free memory after processing
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
        horses = Horse.query.filter_by(race_id=race.id).all()
        
        race_data = {
            'race_number': race.race_number,
            'distance': race.distance,
            'race_class': race.race_class,
            'track_condition': race.track_condition,
            'horses': []
        }
        
        for horse in horses:
            pred = horse.prediction
            horse_data = {
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
                'notes': pred.notes if pred else ''
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
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
        
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
        return redirect(url_for("dashboard"))
    
    # This handles GET requests - notice it's NOT indented under the if POST block
    return render_template("login.html")
    
def parse_notes_components(notes):
    """
    Parse the notes field to extract individual scoring components.
    Returns a dict of component_name -> score_value
    """
    if not notes:
        return {}
    
    components = {}
    
    # Define patterns to extract - (pattern, component_name, score_group)
    patterns = [
        # Last 10 Form
        (r'([+-]?\s*[\d.]+)\s*:\s*Ran places:', 'Ran Places'),
        (r'(-15\.0)\s*:\s*No wins in last 10', 'No Wins Last 10'),
        
        # Jockeys
        (r'\+\s*10\.0\s*:\s*Love the Jockey', 'Elite Jockey'),
        (r'\+\s*5\.0\s*:\s*Like the Jockey', 'Good Jockey'),
        (r'-\s*5\.0\s*:\s*Kerrin', 'Negative Jockey'),
        
        # Trainers
        (r'\+\s*5\.0\s*:\s*Like the Trainer', 'Good Trainer'),
        
        # Track Record
        (r'([+-]?\s*[\d.]+)\s*:\s*Exceptional win rate.*at this track\n', 'Track Win Rate - Exceptional'),
        (r'([+-]?\s*[\d.]+)\s*:\s*Strong win rate.*at this track\n', 'Track Win Rate - Strong'),
        (r'([+-]?\s*[\d.]+)\s*:\s*Good win rate.*at this track\n', 'Track Win Rate - Good'),
        (r'([+-]?\s*[\d.]+)\s*:\s*Moderate win rate.*at this track\n', 'Track Win Rate - Moderate'),
        (r'([+-]?\s*[\d.]+)\s*:\s*UNDEFEATED.*at this track!', 'Undefeated at Track'),
        
        # Track+Distance
        (r'([+-]?\s*[\d.]+)\s*:\s*UNDEFEATED.*at this track\+distance', 'Undefeated at Track+Distance'),
        
        # Distance
        (r'([+-]?\s*[\d.]+)\s*:\s*UNDEFEATED.*at this distance', 'Undefeated at Distance'),
        (r'=\s*([\d.]+)\s*:\s*Total distance score', 'Distance Score Total'),
        
        # Track Condition
        (r'([+-]?\s*[\d.]+)\s*:\s*UNDEFEATED.*runs on (good|soft|heavy|firm|synthetic)', 'Undefeated on Condition'),
        (r'=\s*([\d.]+)\s*:\s*Total track condition score', 'Track Condition Score Total'),
        
        # Distance Change
        (r'\+\s*1\.0\s*:\s*Longer dist than previous', 'Longer Distance'),
        (r'-\s*1\.0\s*:\s*Shorter dist than previous', 'Shorter Distance'),
        
        # Class Change
        (r'\+\s*([\d.]+):\s*Stepping DOWN', 'Class Drop'),
        (r'(-[\d.]+):\s*Stepping UP', 'Class Rise'),
        
        # Last Start Margin - Winners
        (r'\+\s*10\.0\s*:\s*Dominant last start win', 'Last Start - Dominant Win'),
        (r'\+\s*7\.0\s*:\s*Comfortable last start win', 'Last Start - Comfortable Win'),
        (r'\+\s*5\.0\s*:\s*Narrow last start win', 'Last Start - Narrow Win'),
        (r'\+\s*3\.0\s*:\s*Photo finish last start win', 'Last Start - Photo Win'),
        
        # Last Start Margin - Placed
        (r'\+\s*5\.0\s*:\s*Narrow loss.*very competitive', 'Last Start - Competitive Loss'),
        (r'\+\s*3\.0\s*:\s*Close loss', 'Last Start - Close Loss'),
        
        # Last Start Margin - Beaten
        (r'-\s*3\.0\s*:\s*Beaten clearly', 'Last Start - Beaten Clearly'),
        (r'-\s*7\.0\s*:\s*Well beaten', 'Last Start - Well Beaten'),
        (r'-\s*15\.0\s*:\s*Demolished', 'Last Start - Demolished'),
        
        # Days Since Run
        (r'\+\s*15\.0\s*:\s*Quick backup', 'Quick Backup'),
        (r'(-[\d.]+)\s*:\s*Too fresh', 'Too Fresh'),
        
        # Form Price
        (r'\+\s*([\d.]+)\.0\s*:\s*Form price.*well-backed', 'Form Price - Well Backed'),
        (r'\+\s*0\.0\s*:\s*Form price.*neutral', 'Form Price - Neutral'),
        (r'(-[\d.]+)\.0\s*:\s*Form price', 'Form Price - Negative'),
        
        # First/Second Up
        (r'\+\s*4\.0\s*:\s*First-up winner', 'First Up Winner'),
        (r'\+\s*3\.0\s*:\s*Strong first-up podium', 'First Up Strong Podium'),
        (r'\+\s*3\.0\s*:\s*Second-up winner', 'Second Up Winner'),
        (r'\+\s*2\.0\s*:\s*Strong second-up podium', 'Second Up Strong Podium'),
        (r'\+\s*15\.0\s*:\s*First-up specialist \(UNDEFEATED\)', 'First Up Specialist'),
        (r'\+\s*15\.0\s*:\s*Second-up specialist \(UNDEFEATED\)', 'Second Up Specialist'),
        
        # Sectionals
        (r'\+\s*([\d.]+):\s*weighted avg \(z=([\d.]+)', 'Sectional Weighted Avg'),
        (r'\+\s*([\d.]+):\s*best of last \d+ \(z=([\d.]+)', 'Sectional Best Recent'),
        (r'\+\s*([\d.]+):\s*consistency - excellent', 'Sectional Consistency - Excellent'),
        (r'\+\s*([\d.]+):\s*consistency - good', 'Sectional Consistency - Good'),
        (r'\+\s*([\d.]+):\s*consistency - fair', 'Sectional Consistency - Fair'),
        (r'\+\s*([\d.]+):\s*consistency - poor', 'Sectional Consistency - Poor'),
        
        # Weight
        (r'\+\s*([\d.]+)\s*:\s*Weight.*BELOW race avg', 'Weight - Well Below Avg'),
        (r'\+\s*([\d.]+)\s*:\s*Weight.*below race avg', 'Weight - Below Avg'),
        (r'(-[\d.]+)\s*:\s*Weight.*above race avg', 'Weight - Above Avg'),
        (r'(-[\d.]+)\s*:\s*Weight.*ABOVE race avg', 'Weight - Well Above Avg'),
        (r'\+\s*([\d.]+)\s*:\s*Dropped.*from last start', 'Weight Drop'),
        (r'(-[\d.]+)\s*:\s*Up.*from last start', 'Weight Rise'),
        
        # Combo Bonus
        (r'\+\s*15\.0\s*:\s*COMBO BONUS', 'Combo Bonus'),
        # NEW PATTERNS - Add after the existing patterns, before the closing bracket ]

        # Colt Bonus
        (r'\+\s*15\.0\s*:\s*COLT', 'Colt Bonus'),

        # Age Bonuses
        (r'\+\s*5\.0\s*:\s*Prime age \(3yo', 'Age - 3yo Prime'),
        (r'\+\s*3\.0\s*:\s*Good age \(4yo', 'Age - 4yo Good'),
        (r'-\s*10\.0\s*:\s*Old age \(7\+', 'Age - 7+ Old'),

        # Sire Bonuses (ROI-Based - Updated 2025-01-19)
        (r'([+-]?\s*[\d.]+)\s*:\s*Sire\s+([A-Za-z\'\s]+?)\s+\(', 'Sire - \\2'),

        # Career Win Rate
        (r'\+\s*15\.0\s*:\s*Elite career win rate', 'Career Win Rate - Elite 40%+'),
        (r'\+\s*8\.0\s*:\s*Strong career win rate', 'Career Win Rate - Strong 30-40%'),
        (r'-\s*10\.0\s*:\s*Poor career win rate', 'Career Win Rate - Poor <10%'),

        # Barrier Bonus
        (r'\+\s*5\.0\s*:\s*Sweet spot barrier \(7-9', 'Barrier 7-9'),

        # Close Loss Bonus
        (r'\+\s*5\.0\s*:\s*Close loss last start \(0\.5-2L', 'Close Loss Bonus'),

        # 3yo Colt Combo
        (r'\+\s*20\.0\s*:\s*3yo COLT combo', '3yo Colt Combo'),
        
        # 4yo Mare Combo
        (r'\+\s*15\.0\s*:\s*4yo MARE combo', '4yo Mare Combo'),

        # Demolished + Class Drop Combo
        (r'\+\s*15\.0\s*:\s*Demolished in elite company', 'Demolished + Major Class Drop'),

        # Fast Sectional + Colt Combo
        (r'\+\s*25\.0\s*:\s*Fast sectional \+ COLT combo', 'Fast Sectional + Colt'),

        # Slow Sectional + Class Drop Combo
        (r'\+\s*30\.0\s*:\s*Major class drop \+ slow sectional combo', 'Major Class Drop + Slow Sectional'),

        # Class Drop Context Bonuses
        (r'\+\s*10\.0\s*:\s*Demolished.*BUT MAJOR class drop', 'Context - Demolished with Major Drop'),
        (r'\+\s*5\.0\s*:\s*Well beaten.*BUT major class drop', 'Context - Well Beaten with Drop'),
        (r'\+\s*0\.0\s*:\s*Beaten clearly.*BUT dropping in class', 'Context - Beaten Clearly with Drop'),
        
        # Specialist Bonuses
        (r'\+\s*([\d.]+)\s*:\s*UNDEFEATED \(track\)', 'Specialist - Track'),
        (r'\+\s*([\d.]+)\s*:\s*UNDEFEATED \(distance\)', 'Specialist - Distance'),
        (r'\+\s*([\d.]+)\s*:\s*UNDEFEATED \(track\+distance\)', 'Specialist - Track+Distance'),
        (r'\+\s*([\d.]+)\s*:\s*UNDEFEATED \(.*condition\)', 'Specialist - Condition'),
        (r'\+\s*([\d.]+)\s*:\s*100% PODIUM', 'Specialist - Perfect Podium'),
        
       # ====== RACE-DAY SECTIONAL BONUSES ======
        # Base Race-Day Bonuses
        (r'\+\s*12\.0\s*:\s*Fastest sectional in race.*', 'Race-Day - Fastest in Race'),
        (r'\+\s*(?:18|35)(?:\.\d+)?\s*:\s*Mile\s*\+\s*Fastest(?:\s+sectional)?', 'Race-Day - Mile + Fastest'),
        (r'\+\s*18\.0\s*:\s*Mile\s*\+\s*Fastest sectional.*', 'Race-Day - Mile + Fastest'),
        (r'-\s*8\.0\s*:\s*Long distance.*negates sectional.*', 'Race-Day - Long Distance Penalty'),
        # Weight Advantage + Fast Sectional
        (r'\+\s*50\.0\s*:\s*Big weight advantage \(3kg\+\)\s*\+\s*Fastest.*', 'Race-Day - Big Weight Adv + Fastest'),
        # MEGA COMBOS
        (r'\+\s*30\.0\s*:\s*🔥🔥🔥 MEGA COMBO: 4yo\s*\+\s*Soft\s*\+\s*Fastest\s*\+\s*Weight adv.*', 'MEGA - 4yo+Soft+Fast+Weight'),
        (r'\+\s*24\.0\s*:\s*🔥🔥 Sprint\s*\+\s*Weight adv\s*\+\s*Fastest.*', 'MEGA - Sprint+Weight+Fastest'),
        (r'\+\s*16\.0\s*:\s*🔥 Mile\s*\+\s*Weight adv\s*\+\s*Fastest.*', 'MEGA - Mile+Weight+Fastest'),
        (r'\+\s*12\.0\s*:\s*4yo Mare\s*\+\s*Top 20% sectional.*', 'MEGA - 4yo Mare + Top 20%'),
        ]
    for pattern, name in patterns:
        match = re.search(pattern, notes, re.IGNORECASE)
        if match:
            # Try to extract the score value
            try:
                score_str = match.group(1).replace(' ', '').replace('+', '')
                score = float(score_str)
            except (IndexError, ValueError):
                # Pattern matched but no numeric group - use 1 as indicator
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
        
        if not prediction or not result:
            continue
        
        notes = prediction.notes or ''
        components = parse_notes_components(notes)
        won = result.finish_position == 1
        placed = result.finish_position in [1, 2, 3]
        sp = result.sp or 0
        
        # Calculate profit for this horse
        profit = (sp * stake - stake) if won else -stake
        
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
"""
FIXED analyze_external_factors function for app.py

This replaces the existing function starting around line 630.

KEY FIX: Barriers, Distances, and Track Conditions now count RACES (top picks only)
instead of counting every horse, matching how Tracks work.

To apply this fix:
1. Find the analyze_external_factors function in your app.py (around line 630)
2. Replace the entire function with this code below
"""

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
    
    # Split jockeys into reliable (10+ runs) and limited (3-9 runs)
    jockeys_reliable = {k: v for k, v in jockeys.items() if v['runs'] >= 10}
    jockeys_limited = {k: v for k, v in jockeys.items() if 3 <= v['runs'] < 10}

    # Sort by ROI
    jockeys_reliable = dict(sorted(jockeys_reliable.items(), key=lambda x: x[1]['roi'], reverse=True))
    jockeys_limited = dict(sorted(jockeys_limited.items(), key=lambda x: x[1]['roi'], reverse=True))

    # Split trainers into reliable (10+ runs) and limited (3-9 runs)
    trainers_reliable = {k: v for k, v in trainers.items() if v['runs'] >= 10}
    trainers_limited = {k: v for k, v in trainers.items() if 5 <= v['runs'] < 10}

    # Sort by ROI
    trainers_reliable = dict(sorted(trainers_reliable.items(), key=lambda x: x[1]['roi'], reverse=True))
    trainers_limited = dict(sorted(trainers_limited.items(), key=lambda x: x[1]['roi'], reverse=True))

    # Filter and sort sires (10+ runs only)
    sires_reliable = {k: v for k, v in sire_stats.items() if v['runs'] >= 10}
    sires_reliable = dict(sorted(sires_reliable.items(), key=lambda x: x[1]['roi'], reverse=True))

    # Filter and sort dams (3+ runs only)
    dams_reliable = {k: v for k, v in dam_stats.items() if v['runs'] >= 3}
    dams_reliable = dict(sorted(dams_reliable.items(), key=lambda x: x[1]['roi'], reverse=True))
    print(f"DEBUG: Found {len(dams_reliable)} dams with 3+ runs")
    
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
    
@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out", "info")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    # Get all recent meetings (shared across all users)
    recent_meetings = Meeting.query\
        .order_by(Meeting.uploaded_at.desc())\
        .limit(5)\
        .all()
    return render_template("dashboard.html", recent_meetings=recent_meetings)


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
    meetings = Meeting.query.order_by(Meeting.uploaded_at.desc()).all()
    
    # Convert meetings to JSON for calendar view
    meetings_json = [{
        'id': m.id,
        'meeting_name': m.meeting_name,
        'user': m.user.username,
        'uploaded_at': m.uploaded_at.isoformat()
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
        return redirect(url_for("dashboard"))
    
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
        flash("Access denied. Analytics page is admin-only.", "danger")
        return redirect(url_for("dashboard"))
    
    track_filter = request.args.get('track', '')
    min_score_filter = request.args.get('min_score', type=float)
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    tracks = db.session.query(Meeting.meeting_name).order_by(Meeting.uploaded_at.desc()).limit(200).all()
    track_list = sorted(set([t[0].split('_')[1] if '_' in t[0] else t[0] for t in tracks]))
    
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
    
    limit_param = request.args.get('limit', '200')
    
    # Get distinct race IDs ordered by most recent
    all_race_ids = race_id_query.add_columns(Meeting.uploaded_at).distinct().order_by(Meeting.uploaded_at.desc(), Race.id.desc()).all()
    
    # Apply limit
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
    
    races_data = {}
    for horse, pred, result, race, meeting in all_results:
        race_key = (meeting.id, race.race_number)
        if race_key not in races_data:
            races_data[race_key] = []
        races_data[race_key].append({
            'prediction': pred,
            'result': result
        })
    
    total_races = len(races_data)
    top_pick_wins = 0
    total_profit = 0
    winner_sps = []
    stake = 10.0
    
    for race_key, horses in races_data.items():
        if not horses:
            continue
        horses_sorted = sorted(horses, key=lambda x: x['prediction'].score, reverse=True)
        top_pick = horses_sorted[0]
        
        if min_score_filter and top_pick['prediction'].score < min_score_filter:
            total_races -= 1
            continue
        
        result = top_pick['result']
        won = result.finish_position == 1
        sp = result.sp or 0
        
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
            total_return = 0
            
            for pred, res, horse in best_bet_predictions:
                if res.finish_position == 1 and res.sp:
                    total_return += stake_per_bet * res.sp
            
            profit = total_return - total_staked
            bb_roi = (profit / total_staked * 100) if total_staked > 0 else 0
            bb_strike = (wins / total_bets * 100) if total_bets > 0 else 0
            bb_place = (places / total_bets * 100) if total_bets > 0 else 0
            
            component_performance = {}
            for pred, res, horse in best_bet_predictions:
                if pred.notes:
                    components = parse_notes_components(pred.notes)
                    for comp_name in components.keys():
                        if comp_name not in component_performance:
                            component_performance[comp_name] = {
                                'bets': 0,
                                'wins': 0,
                                'staked': 0,
                                'return': 0
                            }
                        
                        component_performance[comp_name]['bets'] += 1
                        component_performance[comp_name]['staked'] += stake_per_bet
                        
                        if res.finish_position == 1:
                            component_performance[comp_name]['wins'] += 1
                            if res.sp:
                                component_performance[comp_name]['return'] += stake_per_bet * res.sp
            
            for comp_name in component_performance:
                comp = component_performance[comp_name]
                comp['profit'] = comp['return'] - comp['staked']
                comp['roi'] = (comp['profit'] / comp['staked'] * 100) if comp['staked'] > 0 else 0
                comp['sr'] = (comp['wins'] / comp['bets'] * 100) if comp['bets'] > 0 else 0
            
            component_performance = dict(sorted(component_performance.items(), key=lambda x: x[1]['roi'], reverse=True))
            
            best_bets_stats = {
                'total_bets': total_bets,
                'wins': wins,
                'places': places,
                'strike_rate': bb_strike,
                'place_rate': bb_place,
                'total_staked': total_staked,
                'total_return': total_return,
                'profit': profit,
                'roi': bb_roi,
                'component_performance': component_performance
            }
            
            del best_bet_predictions
            import gc
            gc.collect()
            
    except Exception as e:
        print(f"Error calculating Best Bets stats: {e}")
    
    del all_results
    del races_data
    import gc
    gc.collect()
    
    db.session.remove()
    
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
    
    limit_param = request.args.get('limit', '200')
    # Get distinct race IDs ordered by most recent
    all_race_ids = race_id_query.add_columns(Meeting.uploaded_at).distinct().order_by(Meeting.uploaded_at.desc(), Race.id.desc()).all()
    # Apply limit
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
    
    races_data = {}
    for horse, pred, result, race, meeting in all_results:
        race_key = (meeting.id, race.race_number)
        if race_key not in races_data:
            races_data[race_key] = []
        races_data[race_key].append({
            'prediction': pred,
            'result': result
        })
    
    stake = 10.0
    score_tiers = {
        '150+': {'races': 0, 'wins': 0, 'profit': 0},
        '140-149': {'races': 0, 'wins': 0, 'profit': 0},
        '130-139': {'races': 0, 'wins': 0, 'profit': 0},
        '120-129': {'races': 0, 'wins': 0, 'profit': 0},
        '110-119': {'races': 0, 'wins': 0, 'profit': 0},
        '100-109': {'races': 0, 'wins': 0, 'profit': 0},
        '90-99': {'races': 0, 'wins': 0, 'profit': 0},
    }
    
    score_gaps = {
        '50+': {'races': 0, 'wins': 0, 'profit': 0},
        '40-49': {'races': 0, 'wins': 0, 'profit': 0},
        '30-39': {'races': 0, 'wins': 0, 'profit': 0},
        '20-29': {'races': 0, 'wins': 0, 'profit': 0},
        '10-19': {'races': 0, 'wins': 0, 'profit': 0},
        '<10': {'races': 0, 'wins': 0, 'profit': 0},
    }
    
    for race_key, horses in races_data.items():
        horses.sort(key=lambda x: x['prediction'].score, reverse=True)
        if not horses:
            continue
        
        top_pick = horses[0]
        top_score = top_pick['prediction'].score
        
        if min_score_filter and top_score < min_score_filter:
            continue
        
        second_score = horses[1]['prediction'].score if len(horses) > 1 else 0
        score_gap = top_score - second_score
        
        if top_score >= 150:
            tier = '150+'
        elif top_score >= 140:
            tier = '140-149'
        elif top_score >= 130:
            tier = '130-139'
        elif top_score >= 120:
            tier = '120-129'
        elif top_score >= 110:
            tier = '110-119'
        elif top_score >= 100:
            tier = '100-109'
        elif top_score >= 90:
            tier = '90-99'
        else:
            tier = None
        
        if score_gap >= 50:
            gap_bucket = '50+'
        elif score_gap >= 40:
            gap_bucket = '40-49'
        elif score_gap >= 30:
            gap_bucket = '30-39'
        elif score_gap >= 20:
            gap_bucket = '20-29'
        elif score_gap >= 10:
            gap_bucket = '10-19'
        else:
            gap_bucket = '<10'
        
        won = top_pick['result'].finish_position == 1
        sp = top_pick['result'].sp
        profit = (sp * stake - stake) if won else -stake
        
        if tier:
            score_tiers[tier]['races'] += 1
            if won:
                score_tiers[tier]['wins'] += 1
            score_tiers[tier]['profit'] += profit
        
        score_gaps[gap_bucket]['races'] += 1
        if won:
            score_gaps[gap_bucket]['wins'] += 1
        score_gaps[gap_bucket]['profit'] += profit
    
    for tier in score_tiers:
        t = score_tiers[tier]
        t['strike_rate'] = (t['wins'] / t['races'] * 100) if t['races'] > 0 else 0
        t['roi'] = (t['profit'] / (t['races'] * stake) * 100) if t['races'] > 0 else 0
    
    for gap in score_gaps:
        g = score_gaps[gap]
        g['strike_rate'] = (g['wins'] / g['races'] * 100) if g['races'] > 0 else 0
        g['roi'] = (g['profit'] / (g['races'] * stake) * 100) if g['races'] > 0 else 0
    
    result = jsonify({
        'score_tiers': score_tiers,
        'score_gaps': score_gaps
    })
    
    del all_results
    del races_data
    del score_tiers
    del score_gaps
    import gc
    gc.collect()
    
    db.session.remove()
    
    return result


@app.route("/api/data/component-analysis")
@login_required
def api_component_analysis():
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
    
    limit_param = request.args.get('limit', '200')
    # Get distinct race IDs ordered by most recent
    all_race_ids = race_id_query.add_columns(Meeting.uploaded_at).distinct().order_by(Meeting.uploaded_at.desc(), Race.id.desc()).all()
    # Apply limit
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
    for horse, pred, result, race, meeting in all_results:
        all_results_data.append({
            'horse': horse,
            'prediction': pred,
            'result': result,
            'race': race,
            'meeting': meeting
        })
    
    component_stats = aggregate_component_stats(all_results_data, stake=10.0)
    
    sorted_components = sorted(
        component_stats.items(),
        key=lambda x: x[1]['roi'],
        reverse=True
    )
    
    components_list = []
    for name, stats in sorted_components:
        if stats['appearances'] >= 2:
            components_list.append({
                'name': name,
                'appearances': stats['appearances'],
                'wins': stats['wins'],
                'strike_rate': stats['strike_rate'],
                'places': stats['places'],
                'place_rate': stats['place_rate'],
                'roi': stats['roi']
            })
    
    result = jsonify({'components': components_list})
    
    del all_results
    del all_results_data
    del component_stats
    del components_list
    import gc
    gc.collect()
    
    db.session.remove()
    
    return result


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
    
    limit_param = request.args.get('limit', '200')
    # Get distinct race IDs ordered by most recent
    all_race_ids = race_id_query.add_columns(Meeting.uploaded_at).distinct().order_by(Meeting.uploaded_at.desc(), Race.id.desc()).all()
    # Apply limit
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
        'class_performance': class_performance_filtered
    })
    
    del all_results
    del all_results_data
    del races_data
    del external_factors
    del class_performance
    import gc
    gc.collect()
    
    db.session.remove()
    
    return result


@app.route("/api/data/price-analysis")
@login_required
def api_price_analysis():
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
    
    limit_param = request.args.get('limit', '200')
    # Get distinct race IDs ordered by most recent
    all_race_ids = race_id_query.add_columns(Meeting.uploaded_at).distinct().order_by(Meeting.uploaded_at.desc(), Race.id.desc()).all()
    # Apply limit
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
    
    races_data = {}
    for horse, pred, result, race, meeting in all_results:
        race_key = (meeting.id, race.race_number)
        if race_key not in races_data:
            races_data[race_key] = []
        races_data[race_key].append({
            'horse': horse,
            'prediction': pred,
            'result': result
        })
    
    stake = 10.0
    price_analysis = {
        'overlays': {'count': 0, 'wins': 0, 'profit': 0},
        'underlays': {'count': 0, 'wins': 0, 'profit': 0},
        'accurate': {'count': 0, 'wins': 0, 'profit': 0},
        'total_compared': 0,
        'price_diffs': [],
        'overlay_examples': [],
        'underlay_examples': []
    }
    
    for race_key, horses in races_data.items():
        horses.sort(key=lambda x: x['prediction'].score, reverse=True)
        
        if not horses:
            continue
        
        top_pick = horses[0]
        pred = top_pick['prediction']
        result = top_pick['result']
        
        predicted_odds_str = pred.predicted_odds or ''
        try:
            predicted_odds = float(predicted_odds_str.replace('$', '').strip())
        except (ValueError, AttributeError):
            continue
        
        sp = result.sp
        
        if not sp or sp <= 0 or not predicted_odds or predicted_odds <= 0:
            continue
        
        price_analysis['total_compared'] += 1
        
        won = result.finish_position == 1
        profit = (sp * stake - stake) if won else -stake
        
        price_diff_pct = ((sp - predicted_odds) / predicted_odds) * 100
        price_analysis['price_diffs'].append(price_diff_pct)
        
        horse_name = top_pick['horse'].horse_name
        
        if price_diff_pct >= 10:
            price_analysis['overlays']['count'] += 1
            if won:
                price_analysis['overlays']['wins'] += 1
            price_analysis['overlays']['profit'] += profit
            
            if len(price_analysis['overlay_examples']) < 5:
                price_analysis['overlay_examples'].append({
                    'horse': horse_name,
                    'your_price': predicted_odds,
                    'sp': sp,
                    'won': won
                })
        
        elif price_diff_pct <= -10:
            price_analysis['underlays']['count'] += 1
            if won:
                price_analysis['underlays']['wins'] += 1
            price_analysis['underlays']['profit'] += profit
            
            if len(price_analysis['underlay_examples']) < 5:
                price_analysis['underlay_examples'].append({
                    'horse': horse_name,
                    'your_price': predicted_odds,
                    'sp': sp,
                    'won': won
                })
        
        else:
            price_analysis['accurate']['count'] += 1
            if won:
                price_analysis['accurate']['wins'] += 1
            price_analysis['accurate']['profit'] += profit
    
    for category in ['overlays', 'underlays', 'accurate']:
        cat = price_analysis[category]
        cat['strike_rate'] = (cat['wins'] / cat['count'] * 100) if cat['count'] > 0 else 0
        cat['roi'] = (cat['profit'] / (cat['count'] * stake) * 100) if cat['count'] > 0 else 0
    
    price_analysis['avg_diff'] = sum(price_analysis['price_diffs']) / len(price_analysis['price_diffs']) if price_analysis['price_diffs'] else 0
    
    result = jsonify(price_analysis)
    
    del all_results
    del races_data
    del price_analysis
    import gc
    gc.collect()
    
    db.session.remove()
    
    return result
    
# ----- ML Data Export Route -----
@app.route("/data/export")
@login_required
def export_ml_data():
    """Export all race data with parsed scoring components AND raw CSV data for ML analysis"""
    # Add admin check at the very top
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required to download ML training data.', 'error')
        return redirect(url_for('analytics'))
    import csv
    from io import StringIO
    from flask import make_response
    
    # Apply same filters as data page
    track_filter = request.args.get('track', '')
    min_score_filter = request.args.get('min_score', type=float)
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    # Query all horses with predictions and results
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
    ).join(Race, Horse.race_id == Race.id)\
     .join(Meeting, Race.meeting_id == Meeting.id)\
     .join(Prediction, Horse.id == Prediction.horse_id)\
     .join(Result, Horse.id == Result.horse_id)\
     .filter(Result.finish_position > 0)
    
    # Apply filters
    if track_filter:
        base_query = base_query.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        base_query = base_query.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        base_query = base_query.filter(Meeting.uploaded_at <= date_to)
    
    query_results = base_query.order_by(Meeting.date.desc(), Race.race_number.asc()).limit(2000).all()
    
    # First pass: collect all unique CSV field names
    all_csv_fields = set()
    for row in query_results:
        csv_data = row[13]  # Horse.csv_data is at index 13
        if csv_data and isinstance(csv_data, dict):
            all_csv_fields.update(csv_data.keys())
    
    # Sort CSV fields alphabetically for consistent column ordering
    csv_field_names = sorted(list(all_csv_fields))
    
    # Create CSV in memory
    si = StringIO()
    writer = csv.writer(si)
    
    # Define parsed component columns
    component_columns = [
        'ran_places', 'no_wins_last_10', 'elite_jockey', 'good_jockey', 'negative_jockey',
        'good_trainer', 'track_win_rate', 'track_distance_form', 'distance_form',
        'track_condition_score', 'distance_change', 'class_change',
        'last_start_margin', 'days_since_run', 'form_price',
        'first_second_up', 'sectional_weighted_avg', 'sectional_best_recent',
        'sectional_consistency', 'weight_vs_avg', 'weight_change',
        'combo_bonus', 'specialist_bonus','4yo_mare_combo',
        # NEW: Race-day sectional bonuses
        'raceday_fastest_in_race', 'raceday_sprint_fastest', 'raceday_mile_fastest',
        'raceday_long_penalty', 'raceday_big_weight_fastest', 'raceday_weight_fastest',
        'raceday_4yo_top20', 'raceday_mare_top20', 'raceday_soft_fastest',
        'mega_4yo_soft_fast_weight', 'mega_sprint_weight_fastest', 
        'mega_mile_weight_fastest', 'mega_4yo_mare_top20',
        'raceday_bonus_total'
    ]
    
    # Write header: basic info + predictions + results + parsed components + ALL raw CSV fields
    header = [
        'date', 'meeting_name', 'track', 'race_number', 'distance', 'race_class', 'track_condition',
        'horse_name', 'barrier', 'weight', 'jockey', 'trainer', 'form',
        'total_score', 'predicted_odds', 'win_probability',
        'finish_position', 'sp', 'won', 'placed', 'roi'
    ] + component_columns + csv_field_names
    
    writer.writerow(header)
    
    # Write data rows
    for row in query_results:
        date, meeting_name, race_num, distance, race_class, track_cond, \
        horse_id, horse_name, barrier, weight, jockey, trainer, form, csv_data, \
        score, pred_odds, win_prob, notes, finish_pos, sp = row
        
        # SAFE: Extract track from meeting name
        track = ''
        if meeting_name:
            if '_' in meeting_name:
                parts = meeting_name.split('_')
                track = parts[1] if len(parts) > 1 else meeting_name
            else:
                track = meeting_name
        
        # SAFE: Format date
        date_str = date.strftime('%Y-%m-%d') if date else ''
        
        # SAFE: Clean predicted odds
        try:
            pred_odds_str = str(pred_odds or '').replace('$', '').strip()
            pred_odds_clean = pred_odds_str if pred_odds_str else ''
        except:
            pred_odds_clean = ''
        
        # SAFE: Calculate derived fields
        won = 1 if finish_pos == 1 else 0
        placed = 1 if finish_pos <= 3 else 0
        
        # SAFE: Calculate ROI
        try:
            roi = ((float(sp) - 1) * 100) if (finish_pos == 1 and sp) else -100
        except (ValueError, TypeError):
            roi = -100
        
        # Parse components from notes
        components = parse_notes_components(notes or '')
        
        # Map parsed components to simplified column names
        component_values = {
            'ran_places': components.get('Ran Places', 0),
            'no_wins_last_10': components.get('No Wins Last 10', 0),
            'elite_jockey': components.get('Elite Jockey', 0),
            '4yo_mare_combo': components.get('4yo Mare Combo', 0),
            'good_jockey': components.get('Good Jockey', 0),
            'negative_jockey': components.get('Negative Jockey', 0),
            'good_trainer': components.get('Good Trainer', 0),
            'track_win_rate': sum([
                components.get('Track Win Rate - Exceptional', 0),
                components.get('Track Win Rate - Strong', 0),
                components.get('Track Win Rate - Good', 0),
                components.get('Track Win Rate - Moderate', 0),
                components.get('Undefeated at Track', 0)
            ]),
            'track_distance_form': components.get('Undefeated at Track+Distance', 0) or components.get('Distance Score Total', 0),
            'distance_form': components.get('Undefeated at Distance', 0) or components.get('Distance Score Total', 0),
            'track_condition_score': components.get('Track Condition Score Total', 0) or components.get('Undefeated on Condition', 0),
            'distance_change': components.get('Longer Distance', 0) + components.get('Shorter Distance', 0),
            'class_change': components.get('Class Drop', 0) + components.get('Class Rise', 0),
            'last_start_margin': sum([
                components.get('Last Start - Dominant Win', 0),
                components.get('Last Start - Comfortable Win', 0),
                components.get('Last Start - Narrow Win', 0),
                components.get('Last Start - Photo Win', 0),
                components.get('Last Start - Competitive Loss', 0),
                components.get('Last Start - Close Loss', 0),
                components.get('Last Start - Beaten Clearly', 0),
                components.get('Last Start - Well Beaten', 0),
                components.get('Last Start - Demolished', 0)
            ]),
            'days_since_run': components.get('Quick Backup', 0) + components.get('Too Fresh', 0),
            'form_price': components.get('Form Price - Well Backed', 0) + components.get('Form Price - Neutral', 0) + components.get('Form Price - Negative', 0),
            'first_second_up': sum([
                components.get('First Up Winner', 0),
                components.get('First Up Strong Podium', 0),
                components.get('Second Up Winner', 0),
                components.get('Second Up Strong Podium', 0),
                components.get('First Up Specialist', 0),
                components.get('Second Up Specialist', 0)
            ]),
            'sectional_weighted_avg': components.get('Sectional Weighted Avg', 0),
            'sectional_best_recent': components.get('Sectional Best Recent', 0),
            'sectional_consistency': sum([
                components.get('Sectional Consistency - Excellent', 0),
                components.get('Sectional Consistency - Good', 0),
                components.get('Sectional Consistency - Fair', 0),
                components.get('Sectional Consistency - Poor', 0)
            ]),
            'weight_vs_avg': sum([
                components.get('Weight - Well Below Avg', 0),
                components.get('Weight - Below Avg', 0),
                components.get('Weight - Above Avg', 0),
                components.get('Weight - Well Above Avg', 0)
            ]),
            'weight_change': components.get('Weight Drop', 0) + components.get('Weight Rise', 0),
            'combo_bonus': components.get('Combo Bonus', 0),
            'specialist_bonus': sum([
                components.get('Specialist - Track', 0),
                components.get('Specialist - Distance', 0),
                components.get('Specialist - Track+Distance', 0),
                components.get('Specialist - Condition', 0),
                components.get('Specialist - Perfect Podium', 0)
            ]),
            # NEW: Race-day sectional bonuses
            'raceday_fastest_in_race': components.get('Race-Day - Fastest in Race', 0),
            'raceday_sprint_fastest': components.get('Race-Day - Sprint + Fastest', 0),
            'raceday_mile_fastest': components.get('Race-Day - Mile + Fastest', 0),
            'raceday_long_penalty': components.get('Race-Day - Long Distance Penalty', 0),
            'raceday_big_weight_fastest': components.get('Race-Day - Big Weight Adv + Fastest', 0),
            'raceday_weight_fastest': components.get('Race-Day - Weight Adv + Fastest', 0),
            'raceday_4yo_top20': components.get('Race-Day - 4yo + Top 20%', 0),
            'raceday_mare_top20': components.get('Race-Day - Mare + Top 20%', 0),
            'raceday_soft_fastest': components.get('Race-Day - Soft + Fastest', 0),
            'mega_4yo_soft_fast_weight': components.get('MEGA - 4yo+Soft+Fast+Weight', 0),
            'mega_sprint_weight_fastest': components.get('MEGA - Sprint+Weight+Fastest', 0),
            'mega_mile_weight_fastest': components.get('MEGA - Mile+Weight+Fastest', 0),
            'mega_4yo_mare_top20': components.get('MEGA - 4yo Mare + Top 20%', 0),
            'raceday_bonus_total': sum([
                components.get('Race-Day - Fastest in Race', 0),
                components.get('Race-Day - Sprint + Fastest', 0),
                components.get('Race-Day - Mile + Fastest', 0),
                components.get('Race-Day - Long Distance Penalty', 0),
                components.get('Race-Day - Big Weight Adv + Fastest', 0),
                components.get('Race-Day - Weight Adv + Fastest', 0),
                components.get('Race-Day - 4yo + Top 20%', 0),
                components.get('Race-Day - Mare + Top 20%', 0),
                components.get('Race-Day - Soft + Fastest', 0),
                components.get('MEGA - 4yo+Soft+Fast+Weight', 0),
                components.get('MEGA - Sprint+Weight+Fastest', 0),
                components.get('MEGA - Mile+Weight+Fastest', 0),
                components.get('MEGA - 4yo Mare + Top 20%', 0)
            ])
        }
        
        # Build row with safe defaults
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
            roi
        ]
        
        # Add parsed component values
        for col in component_columns:
            data_row.append(component_values.get(col, 0))
        
        # Add ALL raw CSV field values (in same order as header)
        csv_data_dict = csv_data if isinstance(csv_data, dict) else {}
        for field_name in csv_field_names:
            data_row.append(csv_data_dict.get(field_name, ''))
        
        writer.writerow(data_row)
    
    # Create response
    output = make_response(si.getvalue())
    filename = f"ml_complete_data_{datetime.now().strftime('%Y%m%d')}"
    if track_filter:
        filename += f"_{track_filter}"
    if min_score_filter:
        filename += f"_min{int(min_score_filter)}"
    filename += ".csv"
    
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    output.headers["Content-type"] = "text/csv"
    
    # CLEANUP before return
    del query_results
    del si
    del writer
    import gc
    gc.collect()

    return output

@app.route("/best-bets")
@login_required
def best_bets():
    """Show today's best bets based on active positive ROI components"""
    from models import Component, Prediction
    from datetime import datetime, timedelta
    
    # Get filter parameters
    hours_back = request.args.get('hours', default=80, type=int)
    min_score = request.args.get('min_score', type=float)
    min_gap = request.args.get('min_gap', type=float)  # NEW: Score gap filter

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
            score_gap = top_score - second_score
            if min_gap and score_gap < min_gap:
                continue
            top_horse = horses_in_race[0]['horse']
            if not top_horse.prediction:
                continue
            if min_score and top_horse.prediction.score < min_score:
                continue
            components = parse_notes_components(top_horse.prediction.notes)
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
                best_bets.append({
                    'meeting_id': meeting.id,
                    'meeting_name': meeting.meeting_name,
                    'uploaded_at': meeting.uploaded_at,
                    'race_id': race.id,
                    'race_number': race.race_number,
                    'distance': race.distance,
                    'race_class': race.race_class,
                    'track_condition': race.track_condition,
                    'horse_id': top_horse.id,
                    'horse_name': top_horse.horse_name,
                    'score': top_horse.prediction.score,
                    'score_gap': score_gap,
                    'predicted_odds': top_horse.prediction.predicted_odds,
                    'win_probability': top_horse.prediction.win_probability,
                    'components': matched_components,
                    'component_count': len(matched_components),
                    'jockey': top_horse.jockey,
                    'trainer': top_horse.trainer,
                    'barrier': top_horse.barrier,
                    'weight': top_horse.weight,
                    'form': top_horse.form
                })

    best_bets.sort(key=lambda x: x['score'], reverse=True)

    # Group by meeting for better display
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

    # Sort meetings by meeting name (which includes date in YYMMDD format)
    meetings_with_bets = dict(sorted(meetings_with_bets.items(), key=lambda x: x[0]))

    # Flag new best bets and post to Telegram
    logger.info(f"Number of best bets found: {len(best_bets)}")
    updated = 0
    newly_flagged_by_meeting = {}
    
    for bet in best_bets:
        prediction = Prediction.query.filter_by(horse_id=bet['horse_id']).first()
        if prediction and not prediction.best_bet_flagged_at:
            prediction.best_bet_flagged_at = datetime.utcnow()
            db.session.add(prediction)
            updated += 1
            
            # Group newly flagged bets by meeting for Telegram posting
            meeting_name = bet['meeting_name']
            if meeting_name not in newly_flagged_by_meeting:
                newly_flagged_by_meeting[meeting_name] = []
            newly_flagged_by_meeting[meeting_name].append(bet)
    
    try:
        db.session.commit()
        logger.info(f"Flagged {updated} best bets and committed successfully.")
        logger.info(f"About to post {len(newly_flagged_by_meeting)} meetings to Telegram")
        
        # Post newly flagged bets to Telegram (grouped by meeting)
        for meeting_name, meeting_bets in newly_flagged_by_meeting.items():
            post_best_bets_to_telegram(meeting_bets, meeting_name)
            
    except Exception as e:
        logger.error(f"Commit failed: {str(e)}", exc_info=True)

    return render_template("best_bets.html",
        best_bets=best_bets,
        meetings_with_bets=meetings_with_bets,
        total_bets=len(best_bets),
        total_horses_scanned=total_horses_scanned,
        active_components=active_components,
        hours_back=hours_back,
        min_score=min_score,
        min_gap=min_gap
    )
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
    """Execute tool calls from Claude - COMPLETE REWRITE"""
    
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
            results = db.session.query(Result, Prediction).join(
                Horse, Result.horse_id == Horse.id
            ).outerjoin(
                Prediction, Horse.id == Prediction.horse_id
            ).filter(Result.finish_position > 0).all()
            
            total = len(results)
            wins = sum(1 for r, p in results if r.finish_position == 1)
            places = sum(1 for r, p in results if r.finish_position <= 3)
            
            high_score_wins = sum(1 for r, p in results if p and p.score >= 80 and r.finish_position == 1)
            high_score_total = sum(1 for r, p in results if p and p.score >= 80)
            
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
            results = db.session.query(Result, Prediction).join(
                Horse, Result.horse_id == Horse.id
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
            
            for r, p in results:
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
            
            overlays = []
            for r, p, h, m, race in results:
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
        
        elif analysis_type == "track_specialists":
            results = db.session.query(Result, Horse, Meeting, Prediction).join(
                Horse, Result.horse_id == Horse.id
            ).join(
                Race, Horse.race_id == Race.id
            ).join(
                Meeting, Race.meeting_id == Meeting.id
            ).outerjoin(
                Prediction, Horse.id == Prediction.horse_id
            ).filter(Result.finish_position > 0).all()
            
            horse_track_stats = {}
            for r, h, m, p in results:
                track = m.meeting_name.split('_')[1] if '_' in m.meeting_name else 'Unknown'
                key = f"{h.horse_name}_{track}"
                
                if key not in horse_track_stats:
                    horse_track_stats[key] = {
                        "horse": h.horse_name,
                        "track": track,
                        "wins": 0,
                        "total": 0
                    }
                
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
        
        elif analysis_type == "component_performance":
            results = db.session.query(Result, Horse, Prediction).join(
                Horse, Result.horse_id == Horse.id
            ).join(
                Prediction, Horse.id == Prediction.horse_id
            ).filter(Result.finish_position > 0).all()
            
            component_stats = {}
            
            for r, h, p in results:
                if not p.notes:
                    continue
                
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
                "Major Drop (30+)": {"wins": 0, "total": 0, "profit": 0},
                "Significant Drop (20-29)": {"wins": 0, "total": 0, "profit": 0},
                "Moderate Drop (10-19)": {"wins": 0, "total": 0, "profit": 0},
                "Small Drop (1-9)": {"wins": 0, "total": 0, "profit": 0},
                "Same Class": {"wins": 0, "total": 0, "profit": 0},
                "Small Rise (1-9)": {"wins": 0, "total": 0, "profit": 0},
                "Moderate Rise (10-19)": {"wins": 0, "total": 0, "profit": 0},
                "Significant Rise (20+)": {"wins": 0, "total": 0, "profit": 0}
            }
            
            for r, h, race, m, p in results:
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
                                
                                class_patterns[category]["total"] += 1
                                if r.finish_position == 1:
                                    class_patterns[category]["wins"] += 1
                                    if r.sp:
                                        class_patterns[category]["profit"] += (r.sp * 10 - 10)
                                else:
                                    class_patterns[category]["profit"] -= 10
                                break
                else:
                    class_patterns["Same Class"]["total"] += 1
                    if r.finish_position == 1:
                        class_patterns["Same Class"]["wins"] += 1
                        if r.sp:
                            class_patterns["Same Class"]["profit"] += (r.sp * 10 - 10)
                    else:
                        class_patterns["Same Class"]["profit"] -= 10
            
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
