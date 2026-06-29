import json
import subprocess
import sys
from pathlib import Path


def test_afl_debug_pipeline_command_exits_zero_and_prints_status():
    result = subprocess.run(
        [sys.executable, "afl_backtest.py", "--debug-pipeline"],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "database" in payload
    assert "counts" in payload
    assert "ml" in payload
    assert "local_model" in payload["ml"]
    assert "postgres_active_model" in payload["ml"]


def test_afl_current_selection_endpoint_uses_safe_json_and_no_data_shape():
    source = Path("afl_routes.py").read_text()
    fn_start = source.find("def api_afl_ml_current_selections():")
    assert fn_start != -1
    fn_source = source[fn_start:source.find("@app.route", fn_start + 1)]
    assert "safe_json_response" in fn_source
    assert '"status": "no_data"' in fn_source
    assert '"selections": []' in fn_source
    assert '"summary": {}' in fn_source


def test_afl_scoring_sanitises_non_json_values_and_logs_model_source():
    source = Path("afl_backtest.py").read_text()
    assert "def json_safe" in source
    assert "def df_to_safe_records" in source
    assert "allow_nan=False" in source
    assert "Using AFL ML model artifact source=%s" in source
