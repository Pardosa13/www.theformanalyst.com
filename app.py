import os
import subprocess
from flask import Flask, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
from datetime import datetime

UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'csv'}

app = Flask(__name__)
app.secret_key = 'supersecretkey'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Fake DB
meetings = []

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
    return render_template('dashboard.html', recent_meetings=reversed(meetings))

@app.route('/history')
def history():
    return render_template('history.html', meetings=reversed(meetings))

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
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        track_condition = request.form.get('track_condition', 'good')
        advanced_mode = 'advanced_mode' in request.form

        # Run Node.js analysis
        try:
            cmd = ['node', 'analyze.js', filepath, track_condition]
            if advanced_mode:
                cmd.append('--advanced')
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            analysis_result = result.stdout
        except subprocess.CalledProcessError as e:
            flash(f'Analysis failed: {e.stderr}', 'danger')
            return redirect(url_for('dashboard'))

        # Save meeting
        meeting = {
            'id': len(meetings) + 1,
            'meeting_name': filename,
            'uploaded_at': datetime.now(),
            'results': analysis_result
        }
        meetings.append(meeting)

        return redirect(url_for('view_meeting', meeting_id=meeting['id']))
    else:
        flash('Invalid file type', 'danger')
        return redirect(url_for('dashboard'))

@app.route('/meeting/<int:meeting_id>')
def view_meeting(meeting_id):
    meeting = next((m for m in meetings if m['id'] == meeting_id), None)
    if not meeting:
        flash('Meeting not found', 'danger')
        return redirect(url_for('dashboard'))
    return render_template('meeting.html', meeting=meeting, results={'races': []})

# Optional logout route for future use
@app.route('/logout')
def logout():
    flash('Logged out (placeholder)', 'info')
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
