# Updated 2025-11-26 to fix deployment
import os
import json
import re
import subprocess
from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash
from datetime import datetime

from models import db, User, Meeting, Race, Horse, Prediction, Result

app = Flask(__name__)

# Configuration
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///formanalyst.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Fix for postgres:// vs postgresql:// (Railway uses postgres://)
if app.config['SQLALCHEMY_DATABASE_URI'].startswith('postgres://'):
    app.config['SQLALCHEMY_DATABASE_URI'] = app.config['SQLALCHEMY_DATABASE_URI'].replace('postgres://', 'postgresql://', 1)

# Initialize extensions
db.init_app(app)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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
            print("✓ Added market_id column to races table")
        
        # Migration: Make sp column nullable in results table
        with db.engine.connect() as conn:
            conn.execute(text('ALTER TABLE results ALTER COLUMN sp DROP NOT NULL'))
            conn.commit()
        print("✓ Made sp column nullable in results table")
    except Exception as e:
        print(f"Migration check: {e}")
    
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
        
        # Specialist Bonuses
        (r'\+\s*([\d.]+)\s*:\s*UNDEFEATED \(track\)', 'Specialist - Track'),
        (r'\+\s*([\d.]+)\s*:\s*UNDEFEATED \(distance\)', 'Specialist - Distance'),
        (r'\+\s*([\d.]+)\s*:\s*UNDEFEATED \(track\+distance\)', 'Specialist - Track+Distance'),
        (r'\+\s*([\d.]+)\s*:\s*UNDEFEATED \(.*condition\)', 'Specialist - Condition'),
        (r'\+\s*([\d.]+)\s*:\s*100% PODIUM', 'Specialist - Perfect Podium'),
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


def aggregate_component_stats(all_results_data):
    """
    Aggregate component statistics across all results.
    Returns dict of component_name -> {appearances, wins, total_score, avg_score}
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
        
        for component_name, score_value in components.items():
            if component_name not in component_stats:
                component_stats[component_name] = {
                    'appearances': 0,
                    'wins': 0,
                    'places': 0,
                    'total_score': 0,
                    'scores': []
                }
            
            stats = component_stats[component_name]
            stats['appearances'] += 1
            if won:
                stats['wins'] += 1
            if placed:
                stats['places'] += 1
            stats['total_score'] += score_value
            stats['scores'].append(score_value)
    
    # Calculate averages and rates
    for name, stats in component_stats.items():
        stats['avg_score'] = stats['total_score'] / stats['appearances'] if stats['appearances'] > 0 else 0
        stats['strike_rate'] = (stats['wins'] / stats['appearances'] * 100) if stats['appearances'] > 0 else 0
        stats['place_rate'] = (stats['places'] / stats['appearances'] * 100) if stats['appearances'] > 0 else 0
    
    return component_stats
def analyze_external_factors(all_results_data, races_data, stake=10.0):
    """
    Analyze external factors: jockeys, trainers, barriers, distances, tracks
    Returns dict with stats for each factor
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
    
    for entry in all_results_data:
        horse = entry['horse']
        result = entry['result']
        meeting = entry['meeting']
        race = entry['race']
        
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
        
        # Barrier
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
        
        # Distance
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
        
        # Track - skip here, handled separately below using top picks only
        pass
    
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
    # Track analysis - top picks only
    for race_key, horses in races_data.items():
        if not horses:
            continue
        
        horses_sorted = sorted(horses, key=lambda x: x['prediction'].score, reverse=True)
        top_pick = horses_sorted[0]
        
        result = top_pick['result']
        meeting = top_pick['meeting']
        
        won = result.finish_position == 1
        placed = result.finish_position in [1, 2, 3]
        sp = result.sp or 0
        profit = (sp * stake - stake) if won else -stake
        
        meeting_name = meeting.meeting_name or ''
        if '_' in meeting_name:
            track = meeting_name.split('_')[1]
        else:
            track = meeting_name
        
        # Clean track name - remove (1), (2), etc.
        track = re.sub(r'\s*\(\d+\)\s*$', '', track).strip()
        
        if track:
            if track not in tracks:
                tracks[track] = {'runs': 0, 'wins': 0, 'places': 0, 'profit': 0}
            tracks[track]['runs'] += 1
            if won:
                tracks[track]['wins'] += 1
            if placed:
                tracks[track]['places'] += 1
            tracks[track]['profit'] += profit
    tracks = calc_rates(tracks, stake)
    
    # Split jockeys into reliable (5+) and limited (2-4)
    jockeys_reliable = {k: v for k, v in jockeys.items() if v['runs'] >= 5}
    jockeys_limited = {k: v for k, v in jockeys.items() if 2 <= v['runs'] < 5}
    
    # Sort by strike rate
    jockeys_reliable = dict(sorted(jockeys_reliable.items(), key=lambda x: x[1]['strike_rate'], reverse=True))
    jockeys_limited = dict(sorted(jockeys_limited.items(), key=lambda x: x[1]['strike_rate'], reverse=True))
    
    # Split trainers into reliable (3+) and limited (2)
    trainers_reliable = {k: v for k, v in trainers.items() if v['runs'] >= 3}
    trainers_limited = {k: v for k, v in trainers.items() if v['runs'] == 2}
    
    # Sort by strike rate
    trainers_reliable = dict(sorted(trainers_reliable.items(), key=lambda x: x[1]['strike_rate'], reverse=True))
    trainers_limited = dict(sorted(trainers_limited.items(), key=lambda x: x[1]['strike_rate'], reverse=True))
    
    # Filter tracks with 2+ races
    tracks = {k: v for k, v in tracks.items() if v['runs'] >= 2}
    tracks = dict(sorted(tracks.items(), key=lambda x: x[1]['strike_rate'], reverse=True))
    
    return {
        'jockeys_reliable': jockeys_reliable,
        'jockeys_limited': jockeys_limited,
        'trainers_reliable': trainers_reliable,
        'trainers_limited': trainers_limited,
        'barriers': barriers,
        'distances': distances,
        'tracks': tracks
    }
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
    """Show meetings needing results and completed meetings"""
    all_meetings = Meeting.query.order_by(Meeting.uploaded_at.desc()).all()
    
    needs_results = []
    results_complete = []
    
    for meeting in all_meetings:
        # Count total horses and horses with results
        total_horses = 0
        horses_with_results = 0
        total_races = len(meeting.races)
        races_complete = 0
        
        for race in meeting.races:
            race_horses = len(race.horses)
            race_results = sum(1 for h in race.horses if h.result is not None)
            total_horses += race_horses
            horses_with_results += race_results
            
            if race_horses > 0 and race_results == race_horses:
                races_complete += 1
        
        meeting_data = {
            'id': meeting.id,
            'meeting_name': meeting.meeting_name,
            'uploaded_at': meeting.uploaded_at,
            'user': meeting.user.username,
            'total_races': total_races,
            'races_complete': races_complete,
            'total_horses': total_horses,
            'horses_with_results': horses_with_results
        }
        
        if total_horses > 0 and horses_with_results == total_horses:
            results_complete.append(meeting_data)
        else:
            needs_results.append(meeting_data)
    
    return render_template("results.html", 
                          needs_results=needs_results, 
                          results_complete=results_complete)


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
        elif finish not in [0, 1, 2, 3, 4, 5]:  # CHANGED: Added 0 for scratched
            errors.append(f"Invalid finish position for {horse.horse_name}")
        
        # CHANGED: Only require SP for horses that actually ran (not scratched)
        if finish in [1, 2, 3, 4]:
            if sp is None:
                errors.append(f"Missing SP for {horse.horse_name}")
            elif sp < 1.01 or sp > 999:
                errors.append(f"Invalid SP for {horse.horse_name} (must be $1.01 - $999)")
        
        if finish is not None:
            results_to_save.append({
                'horse': horse,
                'finish': finish,
                'sp': sp if finish > 0 else None  # CHANGED: NULL SP for scratched horses
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
    
    return render_template("admin.html", stats=stats)


# ----- Data Analytics Route -----

@app.route("/data")
@login_required
def data_analytics():
    """Analytics dashboard showing model performance"""
    
    track_filter = request.args.get('track', '')
    min_score_filter = request.args.get('min_score', type=float)
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
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
    )
    
    if track_filter:
        base_query = base_query.filter(Meeting.meeting_name.ilike(f'%{track_filter}%'))
    if date_from:
        base_query = base_query.filter(Meeting.uploaded_at >= date_from)
    if date_to:
        base_query = base_query.filter(Meeting.uploaded_at <= date_to)
    
    all_results = base_query.all()
    
    # Build structured data for component analysis
    all_results_data = []
    for horse, pred, result, race, meeting in all_results:
        all_results_data.append({
            'horse': horse,
            'prediction': pred,
            'result': result,
            'race': race,
            'meeting': meeting
        })
    
    # Get component stats
    component_stats = aggregate_component_stats(all_results_data)
    
    # Sort components by appearances (most common first)
    sorted_components = sorted(
        component_stats.items(),
        key=lambda x: x[1]['appearances'],
        reverse=True
    )
    
    # Group races for score analysis
    races_data = {}
    for horse, pred, result, race, meeting in all_results:
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
    
    total_races = len(races_data)
    top_pick_wins = 0
    total_profit = 0
    stake = 10.0
    winner_sps = []
    
    score_tiers = {
        '90+': {'races': 0, 'wins': 0, 'profit': 0},
        '80-89': {'races': 0, 'wins': 0, 'profit': 0},
        '70-79': {'races': 0, 'wins': 0, 'profit': 0},
        '60-69': {'races': 0, 'wins': 0, 'profit': 0},
        '<60': {'races': 0, 'wins': 0, 'profit': 0},
    }
    
    score_gaps = {
        '30+': {'races': 0, 'wins': 0, 'profit': 0},
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
        
        if top_score >= 90:
            tier = '90+'
        elif top_score >= 80:
            tier = '80-89'
        elif top_score >= 70:
            tier = '70-79'
        elif top_score >= 60:
            tier = '60-69'
        else:
            tier = '<60'
        
        if score_gap >= 30:
            gap_bucket = '30+'
        elif score_gap >= 20:
            gap_bucket = '20-29'
        elif score_gap >= 10:
            gap_bucket = '10-19'
        else:
            gap_bucket = '<10'
        
        won = top_pick['result'].finish_position == 1
        sp = top_pick['result'].sp
        profit = (sp * stake - stake) if won else -stake
        
        if won:
            top_pick_wins += 1
            winner_sps.append(sp)
        total_profit += profit
        
        score_tiers[tier]['races'] += 1
        if won:
            score_tiers[tier]['wins'] += 1
        score_tiers[tier]['profit'] += profit
        
        score_gaps[gap_bucket]['races'] += 1
        if won:
            score_gaps[gap_bucket]['wins'] += 1
        score_gaps[gap_bucket]['profit'] += profit
    
    strike_rate = (top_pick_wins / total_races * 100) if total_races > 0 else 0
    roi = (total_profit / (total_races * stake) * 100) if total_races > 0 else 0
    avg_winner_sp = sum(winner_sps) / len(winner_sps) if winner_sps else 0
    
    for tier in score_tiers:
        t = score_tiers[tier]
        t['strike_rate'] = (t['wins'] / t['races'] * 100) if t['races'] > 0 else 0
        t['roi'] = (t['profit'] / (t['races'] * stake) * 100) if t['races'] > 0 else 0
    
    for gap in score_gaps:
        g = score_gaps[gap]
        g['strike_rate'] = (g['wins'] / g['races'] * 100) if g['races'] > 0 else 0
        g['roi'] = (g['profit'] / (g['races'] * stake) * 100) if g['races'] > 0 else 0
    
    tracks = db.session.query(Meeting.meeting_name).distinct().all()
    track_list = sorted(set([t[0].split('_')[1] if '_' in t[0] else t[0] for t in tracks]))
    
    # Price Analysis
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
        
        # Parse predicted odds (remove $ sign)
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
        
        # Calculate difference (positive = overlay/value)
        price_diff = sp - predicted_odds
        price_diff_pct = ((sp - predicted_odds) / predicted_odds) * 100
        price_analysis['price_diffs']. append(price_diff_pct)
        
        horse_name = top_pick['horse'].horse_name
        meeting_name = top_pick['meeting'].meeting_name
        
        # Categorize: overlay if SP is 10%+ higher than your price
        if price_diff_pct >= 10:
            # Overlay - market offering better odds than you assessed
            price_analysis['overlays']['count'] += 1
            if won:
                price_analysis['overlays']['wins'] += 1
            price_analysis['overlays']['profit'] += profit
            
            if len(price_analysis['overlay_examples']) < 5:
                price_analysis['overlay_examples'].append({
                    'horse': horse_name,
                    'meeting': meeting_name,
                    'your_price': predicted_odds,
                    'sp': sp,
                    'diff_pct': price_diff_pct,
                    'won': won
                })
        
        elif price_diff_pct <= -10:
            # Underlay - market odds shorter than your assessment
            price_analysis['underlays']['count'] += 1
            if won:
                price_analysis['underlays']['wins'] += 1
            price_analysis['underlays']['profit'] += profit
            
            if len(price_analysis['underlay_examples']) < 5:
                price_analysis['underlay_examples']. append({
                    'horse': horse_name,
                    'meeting': meeting_name,
                    'your_price': predicted_odds,
                    'sp': sp,
                    'diff_pct': price_diff_pct,
                    'won': won
                })
        
        else:
            # Accurate - within 10% either way
            price_analysis['accurate']['count'] += 1
            if won:
                price_analysis['accurate']['wins'] += 1
            price_analysis['accurate']['profit'] += profit
    
    # Calculate rates
    for category in ['overlays', 'underlays', 'accurate']:
        cat = price_analysis[category]
        cat['strike_rate'] = (cat['wins'] / cat['count'] * 100) if cat['count'] > 0 else 0
        cat['roi'] = (cat['profit'] / (cat['count'] * stake) * 100) if cat['count'] > 0 else 0
    
    # Average price difference
    price_analysis['avg_diff'] = sum(price_analysis['price_diffs']) / len(price_analysis['price_diffs']) if price_analysis['price_diffs'] else 0
    
    # External Factors Analysis
    external_factors = analyze_external_factors(all_results_data, races_data, stake)
    
    return render_template("data.html",
        total_races=total_races,
        top_pick_wins=top_pick_wins,
        strike_rate=strike_rate,
        roi=roi,
        total_profit=total_profit,
        avg_winner_sp=avg_winner_sp,
        score_tiers=score_tiers,
        score_gaps=score_gaps,
        track_list=track_list,
        component_stats=sorted_components,
        price_analysis=price_analysis,
        external_factors=external_factors,
        filters={
            'track': track_filter,
            'min_score': min_score_filter,
            'date_from': date_from,
            'date_to': date_to
        }
    )

# Error handlers
@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('500.html'), 500


# ----- Run -----
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)


# ----- Run -----
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
