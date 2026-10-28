# Read-only data audit job. Runs once, prints results to the logs, exits.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
# libgomp1: LightGBM's threading library.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir psycopg2-binary==2.9.10 pandas==2.2.2 numpy==1.26.4 scipy==1.13.1 "SQLAlchemy>=2.0.16,<2.1" \
    lightgbm==4.5.0 requests==2.32.3
WORKDIR /app
COPY audit /app/audit
COPY betfair_check.py /app/betfair_check.py
COPY notes_parsing.py /app/notes_parsing.py
# The site's own place rules and form-file reader, so routecheck tests the live code.
COPY place_bet.py /app/place_bet.py
COPY past_runs.py /app/past_runs.py
COPY strike_rate_matching.py /app/strike_rate_matching.py
CMD ["python", "-m", "audit.run_audit"]
