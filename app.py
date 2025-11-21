from flask import Flask, request, render_template_string
from werkzeug.utils import secure_filename
import os

app = Flask(__name__)
UPLOAD_FOLDER = "/app/uploads"  # make sure this exists
ALLOWED_EXTENSIONS = {"txt", "pdf", "png", "jpg", "jpeg", "gif"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/upload", methods=["GET", "POST"])
def upload_file():
    if request.method == "POST":
        # Check if a file is part of the request
        if "file" not in request.files:
            return "No file part in the request", 400
        
        file = request.files["file"]

        # Check if the user submitted a file
        if file.filename == "":
            return "No file selected", 400
        
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(save_path)
            return f"File uploaded successfully: {filename}"
        else:
            return "File type not allowed", 400

    # If GET, show a simple HTML form for testing
    return render_template_string('''
        <!doctype html>
        <title>Upload a File</title>
        <h1>Upload a file</h1>
        <form method="POST" enctype="multipart/form-data">
            <input type="file" name="file">
            <input type="submit" value="Upload">
        </form>
    ''')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
