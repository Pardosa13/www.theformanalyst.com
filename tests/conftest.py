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
