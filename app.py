from flask import Flask, request, render_template_string, redirect, url_for, session
from werkzeug.utils import secure_filename
import os

app = Flask(__name__)
app.secret_key = "supersecretkey"  # Use a strong secret key in production
UPLOAD_FOLDER = "/app/uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Simple demo users (replace with DB or env vars in production)
USERS = {"admin": "password123"}

# HTML Templates
LOGIN_PAGE = """
<!doctype html>
<title>Login</title>
<h2>Login</h2>
<form method="POST" action="/login">
  Username: <input type="text" name="username"><br>
  Password: <input type="password" name="password"><br>
  <input type="submit" value="Login">
</form>
{% if error %}<p style="color:red">{{ error }}</p>{% endif %}
"""

DASHBOARD_PAGE = """
<!doctype html>
<title>Dashboard</title>
<h2>Welcome, {{ user }}!</h2>
<form method="POST" action="/upload" enctype="multipart/form-data">
  Upload a file: <input type="file" name="file"><br>
  <input type="submit" value="Upload">
</form>
<a href="/logout">Logout</a>
{% if message %}<p>{{ message }}</p>{% endif %}
"""

# Routes
@app.route("/", methods=["GET"])
def index():
    if "username" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if USERS.get(username) == password:
            session["username"] = username
            return redirect(url_for("dashboard"))
        else:
            error = "Invalid credentials"
    return render_template_string(LOGIN_PAGE, error=error)

@app.route("/dashboard", methods=["GET"])
def dashboard():
    if "username" not in session:
        return redirect(url_for("login"))
    return render_template_string(DASHBOARD_PAGE, user=session["username"], message=None)

@app.route("/upload", methods=["POST"])
def upload():
    if "username" not in session:
        return redirect(url_for("login"))
    file = request.files.get("file")
    message = ""
    if file and file.filename:
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)
        message = f"File '{filename}' uploaded successfully!"
    else:
        message = "No file selected"
    return render_template_string(DASHBOARD_PAGE, user=session["username"], message=message)

@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("login"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
