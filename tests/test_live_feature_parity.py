"""Train/serve feature parity between backtest.py and ml_predict.py.

The 2026-07 audit features were merged into backtest.py's training pipeline;
these tests pin down that live scoring (ml_predict.py) generates the SAME
features, with the SAME values, in the SAME column layout — and that the
end-to-end predict_meeting() path reports zero missing/defaulted features
via its ML_PREDICTION_FEATURE_DIAGNOSTICS logging once every source is
available.
"""

import json
import logging
import os
import sys
import types
from datetime import date, datetime

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("sqlalchemy")
pytest.importorskip("sklearn")

import backtest
import ml_predict


import past_runs
import pf_form
import track_bias

# 146 legacy + 58 audit + 3 opponent-quality-form, then the 2026-10 blocks:
# past runs, unused ratings prices, track bias, saved PF form feed, and the
# race-relative rank/vs-average pair for each of their relative columns.
EXPECTED_LIVE_FEATURE_COUNT = (
    207 + len(past_runs.PAST_RUN_FEATURES) + 3 + len(track_bias.TRACK_BIAS_FEATURES)
    + len(pf_form.PF_FORM_FEATURES)
    + 2 * (len(past_runs.PAST_RUN_RELATIVE_COLS) + 2 + len(pf_form.PF_FORM_RELATIVE_COLS))
)


def full_csv_data(tab_no=1, jockey="John Smith", trainer="Jane Doe",
                  sire="Great Sire", dam="Great Dam", barrier=4):
    return {
        'horse age': '5', 'horse sex': 'Gelding', 'horse weight': '58.5',
        'horse claim': '0', 'form weight': '57.0', 'distance': '1400m',
        'horse record': '20:5-3-2', 'horse record distance': '10:2-1-1',
        'horse record track': '5:1-0-1', 'horse record track distance': '3:1-0-0',
        'horse record good': '8:2-1-1', 'horse record soft': '6:1-1-0',
        'horse record heavy': '2:0-1-0', 'horse record first up': '4:1-0-1',
        'horse record second up': '4:0-2-0', 'horse last10': '3214532141',
        'form position': '2', 'form margin': '1.5', 'form price': '4.60',
        'form distance': '1400', 'class restrictions': 'BM78',
        'race prizemoney': '1st $22,000', 'form class': 'BM70',
        'prizemoney': '1st $18,000',
        'meeting date': '12/07/2026', 'form meeting date': '21/06/2026',
        'pfaiscore': '78.5', 'last200timerank': '3', 'last400timerank': '4',
        'last600timerank': '5', 'country': 'AUS', 'runningposition': 'ONPACE',
        'horse jockey': jockey, 'horse trainer': trainer,
        'horse barrier': str(barrier), 'form barrier': '6',
        'horse prize money': '$120,000', 'prizemoney won': '$95,000',
        'form track condition': 'Soft 5', 'form other runners': '9',
        'form jockey': jockey, 'track': 'Flemington', 'form track': 'Flemington',
        'jockeys can claim': 'Apprentices Cannot Claim',
        # 2026-07 audit feature sources
        'form time': '01:24.50', 'race number': '5', 'start time': '14:35',
        'weight type': 'Handicap', 'age restrictions': '3+',
        'sex restrictions': 'No', 'horse number': str(tab_no),
        'last200timeprice': '4.5', 'last400timeprice': '5.5',
        # 'race id' in the form file is the horse's LAST START race, never
        # today's; a different number here proves no lookup keys on it.
        'last600timeprice': '6.5', 'race id': '555555',
        'horse sire': sire, 'horse dam': dam,
    }


def pf_rating(tab_no=1):
    return {
        'raceId': 901234, 'tabNo': tab_no, 'timeRank': 2 + tab_no,
        'timePrice': 4.0 + tab_no, 'earlyTimeRank': 1 + tab_no,
        'weightClassRank': 3 + tab_no, 'timeAdjustedWeightClassRank': 2 + tab_no,
        'classChange': 1.5, 'predictedSettlePostion': 3 + tab_no,
        'averageHistoricalSettlePosition': 4.2, 'runStyle': 'MID',
        'isReliable': True, 'pfaiPrice': 5.0 + tab_no, 'pfaiRank': 1 + tab_no,
        'earlyTimePrice': 7.0 + tab_no, 'weightClassPrice': 8.0 + tab_no,
        'timeAdjustedWeightClassPrice': 9.0 + tab_no,
    }


def pf_speedmap_item(tab_no=1):
    return {
        'tabNo': tab_no, 'assessedPrice': 6.0 + tab_no, 'speed': 3.5,
        'settle': 4 + tab_no, 'mapA2E': 1.1, 'jockeyA2E': 0.95,
        'ratedRunStyle': 2.0, 'ratedSettle': 3.5,
    }


JOCKEY_EXTRAS = {'john smith': {'career_a2e': 1.05, 'l100_a2e': 1.11, 'career_runs': 2450}}
TRAINER_EXTRAS = {'jane doe': {'career_a2e': 0.97, 'l100_a2e': 1.02, 'career_runs': 5321}}
PF_RATINGS = {(901234, 1): pf_rating(1), (901234, 2): pf_rating(2)}
PF_SPEEDMAPS = {(901234, 1): pf_speedmap_item(1), (901234, 2): pf_speedmap_item(2)}


PF_RACE_IDS = {(1667, 5): 901234}   # (meeting_id, race_number) -> today's PuntingForm raceId


def backtest_row(cd, rail_position=3):
    return {'csv_data': cd, 'track_condition': 'Good 4', 'rail_position': rail_position,
            'meeting_id': 1667}


def extract_both(cd, rail_position=3, pf_ratings=None, pf_speedmaps=None,
                 jockey_extras=None, trainer_extras=None):
    training, _, _ = backtest.extract_features(
        backtest_row(cd, rail_position), {}, {},
        pf_ratings_lookup=pf_ratings, pf_speedmaps_lookup=pf_speedmaps,
        jockey_extra_lookup=jockey_extras, trainer_extra_lookup=trainer_extras,
        pf_race_ids=PF_RACE_IDS,
    )
    live = ml_predict.extract_features(
        cd, 'Good 4', {}, {},
        rail_position=rail_position,
        pf_ratings_lookup=pf_ratings, pf_speedmaps_lookup=pf_speedmaps,
        jockey_extra_lookup=jockey_extras, trainer_extra_lookup=trainer_extras,
        sire_rates={}, dam_rates={}, pf_race_id=901234,
    )
    return training, live


def assert_feature_dicts_equal(training, live):
    assert set(training.keys()) == set(live.keys())
    for key in training:
        t, l = training[key], live[key]
        t_nan = isinstance(t, float) and np.isnan(t)
        l_nan = isinstance(l, float) and np.isnan(l)
        assert t_nan == l_nan, f"{key}: training={t!r} live={l!r}"
        if not t_nan:
            assert t == pytest.approx(l), f"{key}: training={t!r} live={l!r}"


def test_raw_feature_parity_with_all_sources_present():
    training, live = extract_both(
        full_csv_data(), rail_position=3,
        pf_ratings=PF_RATINGS, pf_speedmaps=PF_SPEEDMAPS,
        jockey_extras=JOCKEY_EXTRAS, trainer_extras=TRAINER_EXTRAS,
    )
    assert_feature_dicts_equal(training, live)
    # Spot-check a few audit features carry real values, not defaults
    assert training['form_speed_mps'] == pytest.approx(1400 / 84.5)
    assert training['rail_position'] == 3.0
    assert training['pf_time_rank'] == 3.0
    assert training['sm_assessed_prob'] == pytest.approx(1.0 / 7.0)
    assert training['jockey_career_a2e'] == pytest.approx(1.05)


def test_raw_feature_parity_with_all_sources_missing():
    training, live = extract_both({}, rail_position=None)
    assert_feature_dicts_equal(training, live)
    # Missing sources must produce NaN (median-filled downstream), not zeros
    for key in ('form_speed_mps', 'rail_position', 'pf_time_rank',
                'sm_assessed_price', 'jockey_career_a2e', 'sire_win_rate'):
        assert np.isnan(training[key])


def test_live_feature_contract_matches_training_columns_exactly():
    """set AND order of ml_predict.FEATURE_NAMES must equal the training
    matrix columns build_training_set() produces."""
    rows = []
    for tab_no in (1, 2):
        cd = full_csv_data(tab_no=tab_no)
        features, _, _ = backtest.extract_features(
            backtest_row(cd), {}, {},
            pf_ratings_lookup=PF_RATINGS, pf_speedmaps_lookup=PF_SPEEDMAPS,
            jockey_extra_lookup=JOCKEY_EXTRAS, trainer_extra_lookup=TRAINER_EXTRAS,
            pf_race_ids=PF_RACE_IDS,
        )
        rows.append(features)
    training_rows = backtest.add_race_relative_features(rows, [1, 1])
    training_columns = list(pd.DataFrame(training_rows).columns)

    assert len(training_columns) == EXPECTED_LIVE_FEATURE_COUNT
    assert len(ml_predict.FEATURE_NAMES) == EXPECTED_LIVE_FEATURE_COUNT
    assert len(set(ml_predict.FEATURE_NAMES)) == EXPECTED_LIVE_FEATURE_COUNT
    assert training_columns == ml_predict.FEATURE_NAMES


def test_race_relative_parity_between_training_and_live():
    cds = [full_csv_data(tab_no=1, barrier=2), full_csv_data(tab_no=2, barrier=9)]
    # Vary a couple of inputs so the ranks are not all ties
    cds[1]['pfaiscore'] = '65.0'
    cds[1]['form price'] = '9.00'
    training_rows, live_rows = [], []
    for cd in cds:
        t, l = extract_both(cd, pf_ratings=PF_RATINGS, pf_speedmaps=PF_SPEEDMAPS,
                            jockey_extras=JOCKEY_EXTRAS, trainer_extras=TRAINER_EXTRAS)
        training_rows.append(t)
        live_rows.append(l)
    training_rel = backtest.add_race_relative_features(training_rows, [7, 7])
    live_rel = ml_predict.add_race_relative_features(live_rows)
    for t_row, l_row in zip(training_rel, live_rel):
        assert_feature_dicts_equal(t_row, l_row)


def test_artifact_feature_contract_requires_live_generatable_features():
    class Artifact:
        pass

    live_ok = Artifact()
    live_ok.feature_names_in_ = list(ml_predict.FEATURE_NAMES)
    assert backtest._artifact_feature_contract_ok(live_ok, list(ml_predict.FEATURE_NAMES))

    not_live = Artifact()
    not_live.feature_names_in_ = list(ml_predict.FEATURE_NAMES) + ['made_up_feature']
    assert not backtest._artifact_feature_contract_ok(
        not_live, list(ml_predict.FEATURE_NAMES) + ['made_up_feature'])

    mismatch = Artifact()
    mismatch.feature_names_in_ = list(reversed(ml_predict.FEATURE_NAMES))
    assert not backtest._artifact_feature_contract_ok(mismatch, list(ml_predict.FEATURE_NAMES))


# ── End-to-end predict_meeting diagnostics ───────────────────────────────────

PAST_RUNS_CSV = (
    "meeting date,track,race number,distance,class restrictions,horse name,horse jockey,"
    "horse weight,form meeting date,form track,form distance,form class,form jockey,"
    "form position,form margin,form price,form time,sectional,form track condition,"
    "form weight, race id\n"
    + "".join(
        f"12/07/2026,Flemington,5,1400m,BM78,{name},John Smith,58.5,{date},Flemington,1400,"
        f"BM70,John Smith,{pos},{margin},{price},01:24.50,35.10sec 600m,{cond},57,{rid}\n"
        for name in ('Test Runner', 'Other Runner')
        for date, pos, margin, price, cond, rid in (
            ('21/06/2026', 1, 1.5, '4.6', 'Good 4', 1),
            ('01/06/2026', 4, 2.5, '7.0', 'Soft 6', 2),
            ('10/05/2026', 2, 0.5, '5.0', 'Heavy 8', 3),
        )
    )
)


def _pf_form_runner(tab_no, name):
    form = {
        'meetingDate': '2026-06-21T00:00:00', 'position': 1, 'starters': 10, 'margin': 1.5,
        'distance': 1400, 'inRun': 'finish,1;settling_down,3;m1200,3;m800,3;m400,2;',
        'flucs': 'opening,5.00;starting,4.60;', 'priceSP': 4.6, 'priceBF': 5.2, 'priceTAB': 4.8,
        'stewardsReport': 'Held up in the straight.', 'isBarrierTrial': False,
        'top4Finishers': [{'runnerName': 'Other Runner' if tab_no == 1 else 'Test Runner', 'position': 2}],
    }
    return pf_form.trim_runner({
        'name': name, 'tabNo': tab_no, 'barrier': 4, 'originalBarrier': 5, 'handicap': 70,
        'prepRuns': 2, 'gearChanges': 'Blinkers first time.',
        'trainerJockeyA2E_Career': {'a2E': 1.1, 'strikeRate': 18.0, 'runners': 120, 'wins': 22},
        'trainerJockeyA2E_Last100': {'a2E': 1.05, 'strikeRate': 17.0, 'runners': 100, 'wins': 17},
        'group1Record': {'starts': 1, 'firsts': 0, 'seconds': 1, 'thirds': 0},
        'forms': [form],
    })


PF_FORM_SNAPSHOT = (
    json.dumps({'5': {'1': _pf_form_runner(1, 'Test Runner'), '2': _pf_form_runner(2, 'Other Runner')}}),
    json.dumps({'penetrometer': '5.1', 'rainfall': 'nil last 24 hours', 'irrigation': '6mm last 24 hours',
                'weather': 'Fine'}),
    datetime(2026, 7, 12, 0, 0),
)


class FakeMeeting:
    def __init__(self):
        self.id = 1667
        self.rail_position = 3
        self.csv_data = PAST_RUNS_CSV
        self.date = date(2026, 7, 12)


class FakeRace:
    def __init__(self, horses):
        self.id = 55
        self.race_number = 5
        self.start_time_utc = datetime(2026, 7, 12, 4, 35)
        self.track_condition = 'Good 4'
        self.horses = horses
        self.ratings_json = json.dumps({'payLoad': [pf_rating(1), pf_rating(2)]})
        self.speed_maps_json = json.dumps({'payLoad': [
            {'raceId': 901234, 'items': [pf_speedmap_item(1), pf_speedmap_item(2)]},
        ]})


class FakeHorse:
    def __init__(self, horse_id, cd, horse_name='Test Runner'):
        self.id = horse_id
        self.csv_data = cd
        self.horse_name = horse_name
        self.is_scratched = False


class FakeSession:
    def __init__(self, meeting, races):
        self.meeting = meeting
        self.races = races

    def query(self, cls):
        session = self

        class Query:
            def get(self, _id):
                return session.meeting

            def filter_by(self, **kwargs):
                return self

            def all(self):
                return session.races

        return Query()

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}

        class Rows:
            def __init__(self, rows):
                self.rows = rows

            def fetchall(self):
                return self.rows

            def first(self):
                return self.rows[0] if self.rows else None

        if 'FROM strike_rates' in sql:
            if params.get('sr_type') == 'jockey':
                return Rows([('John Smith', 18, 100, 1.05, 1.11, 2450)])
            return Rows([('Jane Doe', 15, 100, 0.97, 1.02, 5321)])
        if "'horse sire'" in sql:
            return Rows([('Great Sire', 40, 6)])
        if "'horse dam'" in sql:
            return Rows([('Great Dam', 12, 3)])
        if 'FROM pf_form_snapshots' in sql:
            return Rows([PF_FORM_SNAPSHOT])
        if "'runningposition'" in sql:
            return Rows([('Flemington', '1400m', '2', 'ONPACE', 40, 6, 15),
                         ('Flemington', '1400m', '9', 'ONPACE', 30, 2, 8),
                         ('Caulfield', '1400m', '9', 'LEADER', 50, 7, 18)])
        if 'FROM horse_form_scores' in sql:
            return Rows([('test runner', 1.75), ('other runner', -0.5)])
        raise AssertionError(f"Unhandled SQL in fake session: {sql}")


class FullContractModel:
    """Dummy champion whose contract is the full live feature set."""

    def __init__(self):
        self._form_analyst_expected_features = list(ml_predict.FEATURE_NAMES)
        self._form_analyst_pf_lookup_version = ml_predict.PF_LOOKUP_VERSION
        self._form_analyst_expected_feature_count = len(ml_predict.FEATURE_NAMES)
        self.feature_names_in_ = np.asarray(ml_predict.FEATURE_NAMES)
        self.n_features_in_ = len(ml_predict.FEATURE_NAMES)
        self._form_analyst_model_id = 999
        self._form_analyst_feature_medians = {}

    def predict_proba(self, X):
        assert list(X.columns) == list(ml_predict.FEATURE_NAMES)
        return np.column_stack([np.linspace(0.9, 0.1, len(X)), np.linspace(0.1, 0.9, len(X))])


def test_predict_meeting_reports_zero_missing_and_zero_defaulted_features(monkeypatch, caplog):
    models_stub = types.ModuleType('models')
    models_stub.Meeting = FakeMeeting
    models_stub.Race = FakeRace
    models_stub.Horse = FakeHorse
    monkeypatch.setitem(sys.modules, 'models', models_stub)

    horses = [FakeHorse(11, full_csv_data(tab_no=1, barrier=2), 'Test Runner'),
              FakeHorse(12, full_csv_data(tab_no=2, barrier=9), 'Other Runner')]
    horses[1].csv_data['pfaiscore'] = '65.0'
    horses[0].csv_data['horse name'] = 'Test Runner'
    horses[1].csv_data['horse name'] = 'Other Runner'
    meeting = FakeMeeting()
    session = FakeSession(meeting, [FakeRace(horses)])

    monkeypatch.setattr(ml_predict, 'load_model', lambda: FullContractModel())

    with caplog.at_level(logging.INFO, logger='ml_predict'):
        all_scores, by_race = ml_predict.predict_meeting(1667, session)

    assert set(all_scores.keys()) == {11, 12}
    assert by_race == {55: all_scores}

    diagnostics = [r.getMessage() for r in caplog.records
                   if 'ML_PREDICTION_FEATURE_DIAGNOSTICS' in r.getMessage()]
    assert len(diagnostics) == 1
    assert 'missing_feature_names=[]' in diagnostics[0]
    assert 'features_defaulted_to_zero={}' in diagnostics[0]
    assert f'expected_feature_count={EXPECTED_LIVE_FEATURE_COUNT}' in diagnostics[0]
    assert f'generated_feature_count={EXPECTED_LIVE_FEATURE_COUNT}' in diagnostics[0]
    assert 'feature_counts_match=True' in diagnostics[0]

    audits = [r.getMessage() for r in caplog.records
              if 'ML_FEATURE_AUDIT' in r.getMessage() and 'status=passed' in r.getMessage()]
    assert len(audits) == 1
    # No ML_FEATURE_DEFAULTS warning should fire when every source is present
    assert not [r for r in caplog.records if 'ML_FEATURE_DEFAULTS' in r.getMessage()]
    assert not [r for r in caplog.records if 'ML_FEATURE_MISSING_SOURCE' in r.getMessage()]


def test_extract_features_reports_dated_vs_fallback_snapshot_usage():
    """backtest.extract_features's jockey_used_dated_snapshot/
    trainer_used_dated_snapshot return values (feeding build_training_set's
    per-run "Snapshot coverage" log line) must be True only when
    get_sr_win_pct_asof actually finds an eligible snapshot, and False
    otherwise — one entity with history, one without, to exercise both
    branches in a single call."""
    import datetime as dt
    from strike_rate_matching import build_strike_rate_history_lookup

    jockey_history = build_strike_rate_history_lookup([
        ('John Smith', 20, 100, dt.date(2026, 6, 1)),
    ])
    trainer_history = build_strike_rate_history_lookup([])  # no trainer snapshots yet

    cd = full_csv_data(jockey="John Smith", trainer="Jane Doe")
    _, jockey_used_dated_snapshot, trainer_used_dated_snapshot = backtest.extract_features(
        backtest_row(cd), {}, {},
        jockey_sr_history=jockey_history, trainer_sr_history=trainer_history,
        as_of_date=dt.date(2026, 7, 12),
    )
    assert jockey_used_dated_snapshot is True
    assert trainer_used_dated_snapshot is False
