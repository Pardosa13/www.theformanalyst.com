"""Offline tests for afl_weather.py (no network, no database)."""

import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import afl_weather as w  # noqa: E402


def test_classify_threshold():
    assert w.classify_weather(1.0, threshold=1.0) == "RAIN"
    assert w.classify_weather(0.9, threshold=1.0) == "DRY"
    assert w.classify_weather(None) == "DRY"


def test_indoor_always_dry():
    assert w.classify_weather(25.0, indoor=True) == "DRY"
    assert w.get_venue("Docklands")["indoor"] is True
    assert w.get_venue("Marvel Stadium")["indoor"] is True


def test_all_recent_squiggle_venues_mapped():
    for name in [
        "Adelaide Oval", "Barossa Park", "Bellerive Oval", "Carrara", "Docklands",
        "Gabba", "Hands Oval", "Kardinia Park", "M.C.G.", "Manuka Oval",
        "Marrara Oval", "Norwood Oval", "Perth Stadium", "S.C.G.",
        "Sydney Showground", "Traeger Park", "York Park", "Eureka Stadium",
        "Cazaly's Stadium", "Adelaide Hills", "Stadium Australia",
    ]:
        assert w.get_venue(name) is not None, name


def test_bounce_parsing():
    assert w._parse_bounce(datetime(2026, 3, 5, 19, 30), None) == datetime(2026, 3, 5, 19, 30)
    assert w._parse_bounce(datetime(2026, 3, 5), "2026-03-05 13:45:00") == datetime(2026, 3, 5, 13, 45)
    assert w._parse_bounce(datetime(2026, 3, 5), "") is None


def test_windows():
    start, end = w.game_window(date(2026, 3, 5), datetime(2026, 3, 5, 19, 30))
    assert (start, end) == (datetime(2026, 3, 5, 19, 30), datetime(2026, 3, 5, 22, 30))
    start, end = w.game_window(date(2026, 3, 5), None)
    assert (start, end) == (datetime(2026, 3, 5, 12), datetime(2026, 3, 5, 22))


def test_summarise_window_uses_overlapping_hours():
    times = [f"2026-03-05T{h:02d}:00" for h in range(24)]
    rain = [0.0] * 24
    rain[19] = 5.0   # 18:00-19:00, before bounce -> excluded
    rain[20] = 0.4   # 19:00-20:00, overlaps 19:30 bounce
    rain[23] = 0.7   # 22:00-23:00, overlaps 22:30 end
    temps = [15.0] * 24
    hourly = {"time": times, "precipitation": rain, "temperature_2m": temps}
    rain_mm, temp_c = w.summarise_window(
        hourly, datetime(2026, 3, 5, 19, 30), datetime(2026, 3, 5, 22, 30))
    assert rain_mm == 1.1
    assert temp_c == 15.0


def test_summarise_window_no_data():
    hourly = {"time": ["2026-03-05T20:00"], "precipitation": [None], "temperature_2m": [None]}
    assert w.summarise_window(
        hourly, datetime(2026, 3, 5, 19, 30), datetime(2026, 3, 5, 22, 30)) == (None, None)
