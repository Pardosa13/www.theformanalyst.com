from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import random

app = Flask(__name__)
app.secret_key = "supersecretkey"

# ----- Flask-Login Setup -----
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

# ----- Dummy Users -----
# Replace with your database in production
users = {
    "admin": {"password": generate_password_hash("adminpass"), "is_admin": True},
    "user": {"password": generate_password_hash("userpass"), "is_admin": False}
}

# ----- Store meetings per user -----
# In production, this should be in a database
all_meetings = {}  # Format: {username: [meetings]}

# ----- User Class -----
class User(UserMixin):
    def __init__(self, username, is_admin=False):
        self.id = username
        self.username = username
        self.is_admin = is_admin

@login_manager.user_loader
def load_user(user_id):
    if user_id in users:
        user_info = users[user_id]
        return User(user_id, user_info["is_admin"])
    return None

# ----- Helper Functions -----
def get_user_meetings(username):
    """Get meetings for a specific user"""
    if username not in all_meetings:
        all_meetings[username] = []
    return all_meetings[username]

def generate_dummy_race_data():
    """Generate dummy race analysis data"""
    horses = []
    for i in range(1, random.randint(8, 12)):
        horses.append({
            "number": i,
            "name": f"Horse {i}",
            "barrier": random.randint(1, 12),
            "weight": round(random.uniform(54, 59), 1),
            "jockey": f"Jockey {random.randint(1, 20)}",
            "trainer": f"Trainer {random.randint(1, 15)}",
            "last_5_starts": f"{random.randint(1,10)}-{random.randint(1,10)}-{random.randint(1,10)}-{random.randint(1,10)}-{random.randint(1,10)}",
            "win_probability": round(random.uniform(5, 25), 1),
            "place_probability": round(random.uniform(15, 60), 1),
            "recommended_bet": random.choice(["Win", "Place", "Each Way", "Avoid"]),
            "speed_rating": random.randint(85, 105),
            "class_rating": random.randint(70, 95)
        })
    return sorted(horses, key=lambda x: x['win_probability'], reverse=True)

# ----- Routes -----
@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        remember = bool(request.form.get("remember"))
        
        user_info = users.get(username)
        if not user_info or not check_password_hash(user_info["password"], password):
            flash("Invalid username or password", "danger")
            return redirect(url_for("login"))
        
        user = User(username, user_info["is_admin"])
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
    # Get only the current user's meetings
    user_meetings = get_user_meetings(current_user.username)
    # Get the 5 most recent meetings for display
    recent_meetings = user_meetings[:5] if user_meetings else []
    return render_template("dashboard.html", recent_meetings=recent_meetings)

@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    # Handle CSV upload and analysis
    csv_file = request.files.get("csv_file")
    meeting_name = csv_file.filename if csv_file else f"Meeting {datetime.now().strftime('%Y%m%d_%H%M%S')}"
    track_condition = request.form.get("track_condition", "Good")
    advanced_mode = bool(request.form.get("advanced_mode"))
    
    # Get user's meetings list
    user_meetings = get_user_meetings(current_user.username)
    
    # Create new meeting analysis
    new_meeting = {
        "id": len(user_meetings) + 1,
        "meeting_name": meeting_name,
        "uploaded_at": datetime.now(),
        "user": current_user.username,
        "track_condition": track_condition,
        "advanced_mode": advanced_mode,
        "total_races": random.randint(6, 10),
        "analyzed_horses": random.randint(50, 120),
        "confidence_level": random.choice(["High", "Medium", "Low"]),
        "races": []  # This would contain actual race data
    }
    
    # Generate dummy races for this meeting
    num_races = new_meeting["total_races"]
    for race_num in range(1, num_races + 1):
        race = {
            "race_number": race_num,
            "race_name": f"Race {race_num} - {random.choice(['Maiden', 'Class 3', 'Open Handicap', 'Group 3'])}",
            "distance": f"{random.choice([1000, 1200, 1400, 1600, 2000])}m",
            "prize_money": f"${random.randint(20, 100) * 1000:,}",
            "horses": generate_dummy_race_data()
        }
        new_meeting["races"].append(race)
    
    # Insert at beginning of user's meetings list
    user_meetings.insert(0, new_meeting)
    all_meetings[current_user.username] = user_meetings
    
    flash(f"{meeting_name} analyzed successfully!", "success")
    return redirect(url_for("view_meeting", meeting_id=new_meeting['id']))

@app.route("/history")
@login_required
def history():
    # Get only the current user's meetings
    user_meetings = get_user_meetings(current_user.username)
    return render_template("history.html", meetings=user_meetings)

@app.route("/view_meeting/<int:meeting_id>")
@login_required
def view_meeting(meeting_id):
    user_meetings = get_user_meetings(current_user.username)
    meeting = next((m for m in user_meetings if m["id"] == meeting_id), None)
    
    if not meeting:
        flash("Meeting not found or you don't have access to it", "danger")
        return redirect(url_for("dashboard"))
    
    return render_template("view_meeting.html", meeting=meeting)

@app.route("/admin", methods=["GET", "POST"])
@login_required
def admin_panel():
    if not current_user.is_admin:
        flash("Access denied", "danger")
        return redirect(url_for("dashboard"))
    
    # Handle user creation form
    if request.method == "POST":
        action = request.form.get("action")
        
        if action == "create_user":
            new_username = request.form.get("username")
            new_password = request.form.get("password")
            is_admin = bool(request.form.get("is_admin"))
            
            # Validation
            if not new_username or not new_password:
                flash("Username and password are required", "danger")
            elif new_username in users:
                flash(f"User '{new_username}' already exists", "danger")
            elif len(new_password) < 6:
                flash("Password must be at least 6 characters long", "danger")
            else:
                # Create new user
                users[new_username] = {
                    "password": generate_password_hash(new_password),
                    "is_admin": is_admin
                }
                flash(f"User '{new_username}' created successfully", "success")
        
        elif action == "delete_user":
            username_to_delete = request.form.get("username")
            
            if username_to_delete == current_user.username:
                flash("You cannot delete your own account", "danger")
            elif username_to_delete not in users:
                flash(f"User '{username_to_delete}' not found", "danger")
            else:
                # Delete user and their meetings
                del users[username_to_delete]
                if username_to_delete in all_meetings:
                    del all_meetings[username_to_delete]
                flash(f"User '{username_to_delete}' deleted successfully", "success")
        
        elif action == "reset_password":
            username_to_reset = request.form.get("username")
            new_password = request.form.get("new_password")
            
            if username_to_reset not in users:
                flash(f"User '{username_to_reset}' not found", "danger")
            elif not new_password or len(new_password) < 6:
                flash("Password must be at least 6 characters long", "danger")
            else:
                users[username_to_reset]["password"] = generate_password_hash(new_password)
                flash(f"Password reset successfully for '{username_to_reset}'", "success")
        
        return redirect(url_for("admin_panel"))
    
    # Prepare admin statistics
    admin_stats = {
        "total_users": len(users),
        "total_meetings": sum(len(meetings) for meetings in all_meetings.values()),
        "users_data": [],
        "all_users": []  # For user management
    }
    
    # Get user activity data
    for username, user_meetings in all_meetings.items():
        admin_stats["users_data"].append({
            "username": username,
            "meeting_count": len(user_meetings),
            "last_activity": user_meetings[0]["uploaded_at"] if user_meetings else None
        })
    
    # Get all users for management
    for username, user_info in users.items():
        user_meetings = all_meetings.get(username, [])
        admin_stats["all_users"].append({
            "username": username,
            "is_admin": user_info["is_admin"],
            "meeting_count": len(user_meetings),
            "last_activity": user_meetings[0]["uploaded_at"] if user_meetings else None
        })
    
    return render_template("admin.html", stats=admin_stats)

# ----- Run -----
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
