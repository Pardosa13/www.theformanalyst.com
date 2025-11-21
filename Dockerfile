# Base Python image
FROM python:3.13-slim

# Install system dependencies + Node.js
RUN apt-get update && \
    apt-get install -y curl build-essential && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Set workdir
WORKDIR /app

# Copy project files
COPY . .

# Install Python dependencies
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Expose the port for Railway
EXPOSE 8080

# Run the app with Gunicorn
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]
