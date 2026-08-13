#!/usr/bin/env python3
"""
CLI fallback for the Pardo history import.

The primary way to run this is the "Import Pardo History" button on the
Bet Tracker dashboard (POST /bet-tracker/import-pardo-history), which
calls bet_tracker.import_pardo_history() for the logged-in admin. This
script calls the same function directly against the database, for cases
where running it from the command line is more convenient (e.g. a Railway
shell). It shares the monthly totals and bet-building logic with the
route, so there is only one place the import behaviour is defined.

Usage:
    DATABASE_URL=postgres://... python scripts/import_pardo_history.py

Optional:
    PARDO_IMPORT_USER_EMAIL=you@example.com   # required if more than one
                                               # admin user exists
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    from app import app, db
    from models import User
    from bet_tracker import import_pardo_history

    with app.app_context():
        target_email = os.environ.get('PARDO_IMPORT_USER_EMAIL')
        if target_email:
            user = User.query.filter_by(email=target_email, is_admin=True).first()
            if not user:
                print(f"ERROR: no admin user found with email {target_email!r}.")
                sys.exit(1)
        else:
            admins = User.query.filter_by(is_admin=True).all()
            if len(admins) == 0:
                print("ERROR: no admin users found.")
                sys.exit(1)
            if len(admins) > 1:
                print("ERROR: multiple admin users found, set PARDO_IMPORT_USER_EMAIL to pick one:")
                for a in admins:
                    print(f"  - id={a.id} email={a.email}")
                sys.exit(1)
            user = admins[0]

        print(f"Importing into user id={user.id} email={user.email}")
        created, skipped = import_pardo_history(db, user)
        print(f"Done. {created} month(s) added, {skipped} already present.")


if __name__ == '__main__':
    main()
