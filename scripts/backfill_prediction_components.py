#!/usr/bin/env python3
"""
One-off backfill: populate predictions.parsed_components for existing rows.

Background: Component Analysis (and other /data analytics) used to re-run
parse_notes_components() — ~250 regex patterns — against every horse's
notes text on every single request. At 8,000+ races that's 90,000+ horse
rows parsed per page load, which is why the page took ages and then timed
out. A before_insert/before_update listener on Prediction (app.py) now
caches the parsed result in predictions.parsed_components as notes are
written, so live requests just read pre-parsed JSON. This script does the
one-time catch-up for predictions that existed before that cache did.

Safe to re-run: only rows where parsed_components IS NULL are touched, so an
interrupted run just picks up where it left off.

Usage:
    # Dry run (default): report how many rows need backfilling, write nothing.
    python scripts/backfill_prediction_components.py

    # Actually populate parsed_components for existing rows.
    python scripts/backfill_prediction_components.py --apply

Environment:
    DATABASE_URL (or SQLALCHEMY_DATABASE_URI) must be set, same as the app.
"""
import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod
from models import db, Prediction

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout)

BATCH_SIZE = 2000


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true',
                         help='Write the backfilled parsed_components values to the DB. '
                              'Without this flag, the script only reports how many rows need it.')
    args = parser.parse_args()

    with appmod.app.app_context():
        total_missing = db.session.query(Prediction.id).filter(
            Prediction.parsed_components.is_(None)
        ).count()
        log.info("Predictions missing parsed_components: %s", total_missing)

        if not args.apply:
            log.info("Dry run only — pass --apply to actually backfill.")
            return

        done = 0
        start = time.time()
        while True:
            batch = db.session.query(Prediction).filter(
                Prediction.parsed_components.is_(None)
            ).order_by(Prediction.id).limit(BATCH_SIZE).all()
            if not batch:
                break

            for pred in batch:
                pred.parsed_components = appmod.parse_notes_components(pred.notes or '')

            db.session.commit()
            done += len(batch)
            elapsed = time.time() - start
            log.info("Backfilled %s/%s rows (%.1fs elapsed)", done, total_missing, elapsed)

        log.info("Done. Backfilled %s rows in %.1fs.", done, time.time() - start)


if __name__ == '__main__':
    main()
