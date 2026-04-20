FROM python:3.12-slim
# Install system dependencies for psycopg2 + Node.js + R
RUN apt-get update && \
    apt-get install -y curl build-essential libpq-dev gcc r-base r-base-dev && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
# Upgrade pip, setuptools, wheel
RUN pip install --upgrade pip setuptools wheel
# Install Python dependencies
RUN pip install -r requirements.txt
EXPOSE 8080
CMD ["gunicorn", "-b", "0.0.0.0:8080", "app:app"]
