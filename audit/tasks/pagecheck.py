"""Run the new page queries (betfair_check) against the real database."""
import json
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from audit.run_audit import emit


def run(ctx):
    import betfair_check
    url = os.environ["DATABASE_URL"].replace("postgres://", "postgresql://", 1)
    eng = create_engine(url, connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=120000"})
    with Session(eng) as s:
        out = betfair_check.pick_type_results(s)
        emit("page.ml", f"bsp_through={out['bsp_through']}")
        for r in out["rows"]:
            emit("page.ml", json.dumps(r, default=str))
        out = betfair_check.pick_type_results(s, date_from="2026-07-01", date_to="2026-09-30")
        emit("page.ml.filtered", json.dumps(out["rows"][0], default=str))
        rec = betfair_check.best_bets_record(s)
        emit("page.bb", json.dumps({k: rec[k] for k in ("total", "excluded_after_start")}, default=str))
        for r in rec["by_route"]:
            emit("page.bb.route", json.dumps(r, default=str))
        for r in rec["by_month"]:
            emit("page.bb.month", json.dumps(r, default=str))
