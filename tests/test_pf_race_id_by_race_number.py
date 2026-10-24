"""PuntingForm ratings blobs hold the whole meeting. Each race must pick its
own raceId by raceNo, not race 1's (the first item in the blob)."""
import json

import ml_predict

MEETING_BLOB = json.dumps({'payLoad': [
    {'raceId': 101, 'raceNo': 1, 'tabNo': 1, 'pfaiPrice': 2.0},
    {'raceId': 101, 'raceNo': 1, 'tabNo': 2, 'pfaiPrice': 5.0},
    {'raceId': 102, 'raceNo': 2, 'tabNo': 1, 'pfaiPrice': 9.0},
    {'raceId': 102, 'raceNo': 2, 'tabNo': 2, 'pfaiPrice': 3.0},
]})
RACE2_SPEEDMAP = json.dumps({'payLoad': [{'raceId': 102, 'raceNo': 2, 'items': [{'tabNo': 1, 'settle': 4}]}]})


def _parsers():
    out = [ml_predict._parse_pf_race_blobs]
    try:
        import backtest
        out.append(backtest.parse_pf_race_blobs)
    except SystemExit:  # backtest needs DATABASE_URL at import
        pass
    return out


def test_race_two_gets_its_own_race_id():
    for parse in _parsers():
        ratings, _, race_id = parse(MEETING_BLOB, None, race_number=2)
        assert race_id == 102
        assert ratings[(race_id, 1)]['pfaiPrice'] == 9.0


def test_race_one_unchanged():
    for parse in _parsers():
        _, _, race_id = parse(MEETING_BLOB, None, race_number=1)
        assert race_id == 101


def test_speedmap_race_id_used_without_race_number():
    for parse in _parsers():
        _, speedmaps, race_id = parse(MEETING_BLOB, RACE2_SPEEDMAP)
        assert race_id == 102
        assert speedmaps[(102, 1)]['settle'] == 4


def test_versions_bumped_together():
    assert ml_predict.PF_LOOKUP_VERSION == 3
