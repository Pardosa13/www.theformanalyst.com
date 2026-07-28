"""Regression test for the ConsensusRegressor cross-process pickling bug.

Production symptom (2026-07-28 gunicorn logs):

    Could not load model from DB: Can't get attribute 'ConsensusRegressor'
    on <module '__main__' from '/usr/local/bin/gunicorn'>

Root cause: ConsensusRegressor used to be defined inline in backtest.py.
joblib/pickle records a class's location as `<module>.<name>` at pickle
time. When the nightly job runs `python backtest.py` directly, Python
treats backtest.py as the `__main__` module, so the class gets pickled as
`__main__.ConsensusRegressor`. The live web app is a completely separate
process (gunicorn), with its own distinct `__main__` — unpickling there
looks for the class on gunicorn's `__main__` and fails, because that class
was never defined there.

These tests reproduce the failure in isolation using real subprocesses (a
single test process only ever has one real `__main__`, so the bug can't be
reproduced within one process) and confirm the fix — ConsensusRegressor
moved into model_classes.py, imported normally by both backtest.py and
ml_predict.py — resolves it for both new artifacts and artifacts already
pickled the old way (like the current production champion, id=112).
"""
import os
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("sklearn")
pytest.importorskip("numpy")
pytest.importorskip("pandas")
pytest.importorskip("joblib")

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Reproduces the PRE-FIX backtest.py: ConsensusRegressor defined inline in
# this script, so when run directly its __module__ is '__main__' — exactly
# how every ensemble artifact already in production (including the current
# active champion) was pickled before this fix.
LEGACY_TRAIN_SCRIPT = textwrap.dedent("""
    import sys
    import joblib
    import numpy as np
    import pandas as pd
    from sklearn.base import BaseEstimator, RegressorMixin, clone
    from sklearn.linear_model import LogisticRegression

    class ConsensusRegressor(BaseEstimator, RegressorMixin):
        def __init__(self, estimators, weights=None):
            self.estimators = estimators
            self.weights = weights

        def fit(self, X, y):
            self.feature_names_in_ = np.asarray(list(X.columns))
            self.estimators_ = [clone(est).fit(X, y) for _, est in self.estimators]
            self.weights_ = np.ones(len(self.estimators_)) / len(self.estimators_)
            return self

        def predict(self, X):
            preds = [np.asarray(e.predict_proba(X))[:, 1] for e in self.estimators_]
            return np.average(np.column_stack(preds), axis=1, weights=self.weights_)

    assert ConsensusRegressor.__module__ == "__main__"

    X = pd.DataFrame({"f1": [0.0, 1.0, 0.0, 1.0], "f2": [1.0, 0.0, 1.0, 0.0]})
    y = [0, 1, 0, 1]
    model = ConsensusRegressor([("lr", LogisticRegression())])
    model.fit(X, y)
    joblib.dump(model, sys.argv[1])
""")

# Trains the same kind of model the way backtest.py does today: importing
# ConsensusRegressor from model_classes rather than defining it inline.
FIXED_TRAIN_SCRIPT = textwrap.dedent("""
    import sys
    import joblib
    import pandas as pd
    from sklearn.linear_model import LogisticRegression
    from model_classes import ConsensusRegressor

    assert ConsensusRegressor.__module__ == "model_classes"

    X = pd.DataFrame({"f1": [0.0, 1.0, 0.0, 1.0], "f2": [1.0, 0.0, 1.0, 0.0]})
    y = [0, 1, 0, 1]
    model = ConsensusRegressor([("lr", LogisticRegression())])
    model.fit(X, y)
    joblib.dump(model, sys.argv[1])
""")

# Simulates loading a model with a process that has never heard of
# ConsensusRegressor at all — the gunicorn web-app process before this fix.
LOAD_WITHOUT_MODEL_CLASSES = textwrap.dedent("""
    import sys
    import joblib
    model = joblib.load(sys.argv[1])
    print("LOADED")
""")

# Simulates the fixed ml_predict.py / scripts/audit_champion_feature_lists.py:
# importing model_classes before ever calling joblib.load().
LOAD_WITH_MODEL_CLASSES_IMPORT = textwrap.dedent("""
    import sys
    import joblib
    import pandas as pd
    import model_classes  # noqa: F401 (registers legacy __main__ compat shim)
    model = joblib.load(sys.argv[1])
    preds = model.predict(pd.DataFrame({"f1": [0.5], "f2": [0.5]}))
    print("LOADED", list(preds))
""")


def _run_script(tmp_path, source, filename, *args):
    # Running `python /tmp/.../script.py` puts the SCRIPT's own directory on
    # sys.path[0], not the cwd — so repo-root imports (model_classes,
    # backtest) need PYTHONPATH set explicitly, exactly like a real deploy
    # would need the app directory on the path regardless of cwd.
    script_path = tmp_path / filename
    script_path.write_text(source)
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    return subprocess.run(
        [sys.executable, str(script_path), *args],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60, env=env,
    )


def _train(tmp_path, script_source, script_name):
    artifact = tmp_path / "model.pkl"
    result = _run_script(tmp_path, script_source, script_name, str(artifact))
    assert result.returncode == 0, f"training subprocess failed: {result.stderr}"
    return artifact


def test_legacy_artifact_fails_without_the_fix_reproducing_production_bug(tmp_path):
    """Confirms the reported bug is real and reproducible, not hypothetical."""
    artifact = _train(tmp_path, LEGACY_TRAIN_SCRIPT, "legacy_train.py")

    result = _run_script(tmp_path, LOAD_WITHOUT_MODEL_CLASSES, "load_naive.py", str(artifact))

    assert result.returncode != 0
    assert "ConsensusRegressor" in result.stderr
    assert "__main__" in result.stderr


def test_legacy_artifact_loads_after_importing_model_classes(tmp_path):
    """The fix repairs artifacts ALREADY pickled the old way (e.g. the
    current production champion id=112) without any database migration or
    retraining — importing model_classes registers the class under
    __main__ in whichever process is unpickling."""
    artifact = _train(tmp_path, LEGACY_TRAIN_SCRIPT, "legacy_train.py")

    result = _run_script(tmp_path, LOAD_WITH_MODEL_CLASSES_IMPORT, "load_fixed.py", str(artifact))

    assert result.returncode == 0, result.stderr
    assert "LOADED" in result.stdout


def test_new_artifact_loads_in_a_completely_separate_process(tmp_path):
    """The primary fix: an artifact trained the way backtest.py trains it
    today (importing from model_classes) has a stable module path, so a
    fresh process can load it even WITHOUT explicitly importing
    ConsensusRegressor itself — Python's unpickler auto-imports the
    'model_classes' module by name."""
    artifact = _train(tmp_path, FIXED_TRAIN_SCRIPT, "fixed_train.py")

    result = _run_script(tmp_path, LOAD_WITHOUT_MODEL_CLASSES, "load_naive.py", str(artifact))

    assert result.returncode == 0, result.stderr
    assert "LOADED" in result.stdout


def test_new_artifact_loads_and_scores_via_the_ml_predict_style_import(tmp_path):
    """End-to-end shape of the real fix: train like backtest.py, load and
    predict like ml_predict.py's load_model()/predict_meeting() path."""
    artifact = _train(tmp_path, FIXED_TRAIN_SCRIPT, "fixed_train.py")

    result = _run_script(tmp_path, LOAD_WITH_MODEL_CLASSES_IMPORT, "load_fixed.py", str(artifact))

    assert result.returncode == 0, result.stderr
    assert "LOADED" in result.stdout
