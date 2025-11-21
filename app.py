import os
import subprocess
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
from datetime import datetime
import psycopg2

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'csv'}

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# PostgreSQL connection
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

# Create table if not exists
cur.execute("""
CREATE TABLE IF NOT EXISTS meetings (
    id SERIAL PRIMARY KEY,
    meeting_name TEXT NOT NULL,
    uploaded_at TIMESTAMP NOT NULL,
    results TEXT NOT NULL
)
""")
conn.commit()

# Fake current_user for template compatibility
@app.context_processor
def inject_user():
    class User:
        is_authenticated = False
        username = ''
        is_admin = False
    return dict(current_user=User())

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def dashboard():
    cur.execute("SELECT id, meeting_name, uploaded_at, results FROM meetings ORDER BY uploaded_at DESC")
    meetings = cur.fetchall()
    meetings = [
        {'id': m[0], 'meeting_name': m[1], 'uploaded_at': m[2], 'results': m[3]}
        for m in meetings
    ]
    return render_template('dashboard.html', recent_meetings=meetings)

@app.route('/history')
def history():
    cur.execute("SELECT id, meeting_name, uploaded_at, results FROM meetings ORDER BY uploaded_at DESC")
    meetings = cur.fetchall()
    meetings = [
        {'id': m[0], 'meeting_name': m[1], 'uploaded_at': m[2], 'results': m[3]}
        for m in meetings
    ]
    return render_template('history.html', meetings=meetings)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        # Simple placeholder authentication
        if username == 'admin' and password == 'changeme123':
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid credentials', 'danger')
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/analyze', methods=['POST'])
def analyze():
    if 'csv_file' not in request.files:
        flash('No file part', 'danger')
        return redirect(url_for('dashboard'))
    file = request.files['csv_file']
    if file.filename == '':
        flash('No selected file', 'danger')
        return redirect(url_for('dashboard'))
    if file and allowed_fil_
