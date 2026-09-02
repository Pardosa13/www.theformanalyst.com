"""Runs the race page's JavaScript tests as part of the Python suite.

The movement maths lives in static/js/race-animation.js and the runner artwork
in static/js/race-horse-art.js; both are tested with Node's own test runner in
the sibling .test.js files. This shells out to them so `pytest tests/` covers
the JavaScript half of the page too — otherwise it is only ever checked by
somebody who remembers to run it.

Skips cleanly where Node is not installed rather than failing the suite.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
SUITES = [
    HERE / 'race-animation-engine.test.js',
    HERE / 'race-horse-art.test.js',
]


def test_the_race_engine_javascript_tests_pass():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not available to run the engine tests')

    completed = subprocess.run(
        [node, '--test'] + [str(suite) for suite in SUITES],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if completed.returncode != 0:
        pytest.fail(
            'the race page JavaScript tests failed:\n\n'
            + completed.stdout[-6000:] + '\n' + completed.stderr[-2000:]
        )
    # Guard against the suite silently running nothing at all.
    assert '# fail 0' in completed.stdout
    assert '# pass 0' not in completed.stdout
