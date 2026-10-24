"""Read-only data audit runner.

Runs on Railway next to Postgres and prints every result to the logs, one
line per row, each line starting with "AUD|<run id>|". Nothing is written:
the connection is opened read-only and every query has a time limit.

What to run comes from environment variables:
  AUDIT_TASKS  comma list of task names in audit/tasks (.py or .sql)
  AUDIT_SQL    ad-hoc SQL, same format as a .sql task file
  AUDIT_MAX_ROWS  row cap per query (default 300)
"""
import importlib
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

RUN_ID = datetime.now(timezone.utc).strftime("%m%d%H%M")
MAX_ROWS = int(os.environ.get("AUDIT_MAX_ROWS", "300"))
TASK_DIR = Path(__file__).parent / "tasks"


def emit(section, text):
    """Print one log line. Long text is split so no line is cut by Railway."""
    text = str(text).replace("\n", " ")
    chunks = [text[i:i + 1800] for i in range(0, len(text), 1800)] or [""]
    for chunk in chunks:
        print(f"AUD|{RUN_ID}|{section}|{chunk}", flush=True)
        time.sleep(0.004)  # stay under Railway's log rate limit


def fmt(v):
    if v is None:
        return "∅"
    if isinstance(v, float):
        return f"{v:.4g}"
    s = str(v).replace("|", "/")
    return s if len(s) <= 120 else s[:117] + "..."


class Ctx:
    def __init__(self, conn):
        self.conn = conn

    def rows(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description] if cur.description else []
            return cols, cur.fetchall() if cur.description else []

    def df(self, sql, params=None):
        import pandas as pd
        cols, rows = self.rows(sql, params)
        return pd.DataFrame(rows, columns=cols)

    def table(self, title, cols, rows, limit=None):
        limit = limit or MAX_ROWS
        emit(title, "#cols " + " | ".join(cols) + f"  (rows={len(rows)})")
        for r in rows[:limit]:
            emit(title, " | ".join(fmt(v) for v in r))
        if len(rows) > limit:
            emit(title, f"... {len(rows) - limit} more rows not shown")

    def q(self, title, sql, params=None, limit=None):
        """Run a query and print it. Errors are printed, not raised."""
        t0 = time.time()
        try:
            cols, rows = self.rows(sql, params)
            self.table(title, cols, rows, limit)
            emit(title, f"#took {time.time() - t0:.1f}s")
            return cols, rows
        except Exception as e:  # keep going after a bad query
            self.conn.rollback()
            emit(title, f"#ERROR {type(e).__name__}: {e}")
            return [], []

    def dframe(self, title, frame, limit=None):
        self.table(title, [str(c) for c in frame.columns],
                   [tuple(r) for r in frame.itertuples(index=False)], limit)


def run_sql_text(ctx, text, prefix):
    """Run SQL blocks split on lines starting with '-- @' (the block name)."""
    name, buf = f"{prefix}", []
    blocks = []
    for line in text.splitlines():
        if line.strip().startswith("-- @"):
            if "".join(buf).strip():
                blocks.append((name, "\n".join(buf)))
            name, buf = f"{prefix}.{line.strip()[4:].strip()}", []
        else:
            buf.append(line)
    if "".join(buf).strip():
        blocks.append((name, "\n".join(buf)))
    for name, sql in blocks:
        body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--")).strip()
        if body:
            ctx.q(name, sql)


def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    timeout_ms = int(os.environ.get("AUDIT_TIMEOUT_MS", "300000"))
    conn = psycopg2.connect(
        url,
        options=f"-c default_transaction_read_only=on -c statement_timeout={timeout_ms}",
        application_name="data_audit_readonly",
    )
    conn.set_session(readonly=True, autocommit=True)
    ctx = Ctx(conn)
    emit("START", f"tasks={os.environ.get('AUDIT_TASKS', '')} sql={'yes' if os.environ.get('AUDIT_SQL') else 'no'}")

    for task in [t.strip() for t in os.environ.get("AUDIT_TASKS", "").split(",") if t.strip()]:
        emit("TASK", f"begin {task}")
        t0 = time.time()
        try:
            sql_file = TASK_DIR / f"{task}.sql"
            if sql_file.exists():
                run_sql_text(ctx, sql_file.read_text(), task)
            else:
                importlib.import_module(f"audit.tasks.{task}").run(ctx)
        except Exception:
            emit(task, "#ERROR " + traceback.format_exc())
        emit("TASK", f"end {task} {time.time() - t0:.0f}s")

    if os.environ.get("AUDIT_SQL"):
        run_sql_text(ctx, os.environ["AUDIT_SQL"], "adhoc")

    emit("DONE", "finished")
    conn.close()


if __name__ == "__main__":
    main()
