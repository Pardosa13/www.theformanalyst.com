# Updated 2025-11-26 to fix deployment
# This version includes a safe import for python-dateutil with a simple fallback parser
# so the app can boot even when python-dateutil is not installed. Install
# python-dateutil in production (add to requirements.txt) for best parsing coverage.
import os
import json
import subprocess
import csv
import io
import re
from flask import Flask, render_template, redirect, url_for, request, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash
from datetime import datetime, date as _date
# Try to import dateutil; if unavailable, we'll fall back to a simpler parser.
try:
    from dateutil import parser as dateparser   # pip install python-dateutil
    _HAS_DATEUTIL = True
except Exception:
    dateparser = None
    _HAS_DATEUTIL = False

from models import db, User, Meeting, Race, Horse, Prediction

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


# ----- Helpers -----
def parse_date_string_fallback(s):
    """Very small fallback parser: recognizes common numeric formats and ISO-like substrings."""
    if not s:
        return None
    s = str(s).strip()
    if s == '':
        return None

    # ISO-like YYYY-MM-DD anywhere in the string
    m = re.search(r'(\d{4}-\d{2}-\d{2})', s)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except Exception:
            pass

    # YYYY/MM/DD or YYYY.MM.DD
    m = re.search(r'(\d{4})[\/\.](\d{1,2})[\/\.](\d{1,2})', s)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}", "%Y-%m-%d").date()
        except Exception:
            pass

    # DD/MM/YYYY or DD-MM-YYYY or MM/DD/YYYY (try both common orders)
    fmts = ["%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d", "%Y.%m.%d", "%d %b %Y", "%d %B %Y"]
    for f in fmts:
        try:
            return datetime.strptime(s, f).date()
        except Exception:
            continue

    # contiguous YYYYMMDD
    m2 = re.search(r'(\d{4})(\d{2})(\d{2})', s)
    if m2:
        try:
            return datetime.strptime("".join(m2.groups()), "%Y%m%d").date()
        except Exception:
            pass

    return None


def parse_date_using_available_parser(s):
    """
    Use dateutil if available, otherwise fallback to parse_date_string_fallback.
    Returns a datetime.date or None.
    """
    if not s:
        return None
    if _HAS_DATEUTIL:
        try:
            dt = dateparser.parse(str(s), dayfirst=False, yearfirst=True)
            if dt:
                return dt.date()
        except Exception:
            pass
    # fallback
    return parse_date_string_fallback(s)


def parse_date_from_csv(csv_text, filename=None):
    """
    Try to determine a meeting date from:
      1. Filename (YYYY-MM-DD)
      2. Explicit CSV columns named like 'date', 'meeting_date', etc.
      3. Scanning the first rows for any parseable date-like cell
    Returns a datetime.date or None.
    """
    # 1) filename
    if filename:
        m = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except Exception:
                # fall through to content parsing
                pass

    # 2+3) inspect csv contents
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
    except Exception:
        return None

    # Prefer explicit column names
    date_keys = {'date', 'meeting_date', 'meeting date', 'race_date', 'race date', 'start_date', 'meetingDate', 'meeting'}
    for i, row in enumerate(reader):
        # check header-like keys first
        for k, v in row.items():
            if not v or str(v).strip() == '':
                continue
            if k and k.lower() in date_keys:
                dt = parse_date_using_available_parser(v)
                if dt:
                    return dt
        # fallback: try parsing any cell in first few rows
        for k, v in row.items():
            if not v or str(v).strip() == '':
                continue
            dt = parse_date_using_available_parser(v)
            if dt and dt.year >= 1900:
                return dt
        # limit scanning to first N rows to avoid slowness
        if i >= 20:
            break
    return None


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
        raise Exception("Node.js not found. Please ensure Node.js is installed.")
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
    
    # determine meeting-level date (if available)
    parsed_date = parse_date_from_csv(csv_data, filename)

    # Create meeting record
    meeting = Meeting(
        user_id=user_id,
        meeting_name=filename.replace('.csv', ''),
        csv_data=csv_data,
        date=parsed_date
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
    
    return render_template("login.html")


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


@app.route('/api/events')
@login_required
def api_events():
    """
    FullCalendar will call /api/events?start=YYYY-MM-DD&end=YYYY-MM-DD
    We return meetings for the current user. Each event uses Meeting.date
    if present, otherwise falls back to uploaded_at.date().
    """
    start = request.args.get('start')
    end = request.args.get('end')
    if not start or not end:
        return jsonify([])

    # Parse incoming start/end to dates (tolerant)
    start_d = parse_date_using_available_parser(start)
    end_d = parse_date_using_available_parser(end)
    if not start_d or not end_d:
        return jsonify([])

    # Query meetings for current user; filter in Python to handle null dates fallback
    meetings = Meeting.query.filter_by(user_id=current_user.id).order_by(Meeting.uploaded_at.desc()).all()

    out = []
    for m in meetings:
        # Choose the date to show on calendar
        if m.date:
            event_date = m.date
        else:
            event_date = m.uploaded_at.date()

        # Only include events within start..end
        if event_date < start_d or event_date > end_d:
            continue

        out.append({
            'id': m.id,
            'title': m.meeting_name,
            'start': event_date.isoformat(),   # YYYY-MM-DD (allDay)
            'allDay': True,
            'url': url_for('view_meeting', meeting_id=m.id),
            'extendedProps': {
                'uploaded_at': m.uploaded_at.isoformat(),
                'user': m.user.username if m.user else None
            }
        })
    return jsonify(out)


@app.route('/history')
@login_required
def history():
    meetings = Meeting.query.filter_by(user_id=current_user.id).order_by(Meeting.uploaded_at.desc()).all()
    
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
