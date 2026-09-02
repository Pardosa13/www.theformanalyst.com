"""Runs the race engine's JavaScript tests as part of the Python suite.

The movement maths lives in static/js/race-animation.js and is tested in
tests/race-animation-engine.test.js with Node's own test runner. This shells out
to it so `pytest tests/` covers the engine too — otherwise the JavaScript half
of the page is only ever checked by somebody who remembers to run it.

Skips cleanly where Node is not installed rather than failing the suite.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUITE = Path(__file__).resolve().parent / 'race-animation-engine.test.js'


def test_the_race_engine_javascript_tests_pass():
    node = shutil.which('node')
    if not node:
        pytest.skip('node is not available to run the engine tests')

    completed = subprocess.run(
        [node, '--test', str(SUITE)],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    if completed.returncode != 0:
        pytest.fail(
            'the race engine JavaScript tests failed:\n\n'
            + completed.stdout[-6000:] + '\n' + completed.stderr[-2000:]
        )
    # Guard against the suite silently running nothing at all.
    assert '# fail 0' in completed.stdout
    assert '# pass 0' not in completed.stdout
