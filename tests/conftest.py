import os
import sys
from pathlib import Path

# Importing app.py builds the PuntingForm client, which refuses to start
# without a key. Tests never call the real API, so a placeholder is enough.
os.environ.setdefault("PUNTINGFORM_API_KEY", "test-key")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def session_id_for(user_id):
    """The value Flask-Login keeps in the session for this user.

    It is the account id plus a password fingerprint (User.get_id), so tests
    that fake a login cookie must use this rather than the bare id.
    """
    import app as appmod
    from models import db, User
    with appmod.app.app_context():
        return db.session.get(User, int(user_id)).get_id()
