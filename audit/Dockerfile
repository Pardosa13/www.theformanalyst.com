# Read-only data audit job. Runs once, prints results to the logs, exits.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir psycopg2-binary==2.9.10 pandas==2.2.2 numpy==1.26.4 scipy==1.13.1 "SQLAlchemy>=2.0.16,<2.1"
WORKDIR /app
COPY audit /app/audit
COPY betfair_check.py /app/betfair_check.py
CMD ["python", "-m", "audit.run_audit"]
