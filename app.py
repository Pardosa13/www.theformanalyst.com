from flask import Flask, render_template, redirect, url_for, request, flash
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

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

# ----- Dummy Meetings -----
recent_meetings = []

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
    return render_template("dashboard.html", recent_meetings=recent_meetings)

@app.route("/analyze", methods=["POST"])
@login_required
def analyze():
    # Handle CSV upload and analysis
    csv_file = request.files.get("csv_file")
    meeting_name = csv_file.filename if csv_file else "Meeting " + str(len(recent_meetings) + 1)
    track_condition = request.form.get("track_condition", "Good")
    advanced_mode = bool(request.form.get("advanced_mode"))
    
    recent_meetings.insert(0, {
        "id": len(recent_meetings) + 1,
        "meeting_name": meeting_name,
        "uploaded_at": datetime.now(),
        "user": current_user.username
    })
    
    flash(f"{meeting_name} analyzed successfully!", "success")
    return redirect(url_for("dashboard"))

@app.route("/history")
@login_required
def history():
    return render_template("history.html", meetings=recent_meetings)

@app.route("/view_meeting/<int:meeting_id>")
@login_required
def view_meeting(meeting_id):
    meeting = next((m for m in recent_meetings if m["id"] == meeting_id), None)
    if not meeting:
        flash("Meeting not found", "danger")
        return redirect(url_for("dashboard"))
    return f"<h1>Viewing {meeting['meeting_name']}</h1>"

@app.route("/admin")
@login_required
def admin_panel():
    if not current_user.is_admin:
        flash("Access denied", "danger")
        return redirect(url_for("dashboard"))
    return "<h1>Admin Panel</h1><p>Only admins can see this.</p>"

# ----- Run -----
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
