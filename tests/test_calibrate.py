"""Phase 4 calibration: the fit recovers a known distortion and the report is honest."""
import json
import math
import os
import random
from datetime import date, timedelta

import pytest

import calibrate as cal


def _synthetic_races(n_races, true_b, seed=7, start=date(2026, 3, 1)):
    """Races whose real win chances are raw ** true_b (normalised): raw is overconfident when b < 1."""
    rng = random.Random(seed)
    races = []
    for i in range(n_races):
        scores = [rng.gauss(50, 15) for _ in range(rng.randint(6, 12))]
        raw = cal.softmax(scores)
        true = cal.power_apply(raw, true_b)
        winner = rng.choices(range(len(true)), weights=true)[0]
        races.append({'race_id': i, 'day': start + timedelta(days=i // 8), 'winner': winner,
                      'raw': raw, 'market': true, 'scores': scores})
    return races


def test_softmax_sums_to_one_without_margin():
    probs = cal.softmax([70, 55, 40])
    assert abs(sum(probs) - 1) < 1e-12 and probs[0] > probs[1] > probs[2]


def test_power_fit_recovers_the_true_exponent():
    races = _synthetic_races(4000, true_b=0.6)
    assert abs(cal.fit_power(races) - 0.6) < 0.08


def test_isotonic_is_monotone_and_normalised():
    races = _synthetic_races(2000, true_b=0.6)
    params = cal.fit_isotonic(races)
    probs = cal.isotonic_apply([0.6, 0.3, 0.1], params)
    assert abs(sum(probs) - 1) < 1e-9 and probs[0] >= probs[1] >= probs[2]


def test_calibration_fixes_an_overconfident_model_on_held_out_days():
    races = _synthetic_races(6000, true_b=0.6)
    train, test = cal.split_by_day(races, cal.TRAIN_SHARE)
    assert max(r['day'] for r in train) < min(r['day'] for r in test)
    method, params, _ = cal.choose_and_fit(train)
    raw_table = cal.band_table(test, lambda raw: raw)
    fixed_table = cal.band_table(test, cal.make_predictor(method, params))
    assert not cal.passes_within_noise(raw_table)  # the distortion is real
    assert cal.passes_within_noise(fixed_table)
    assert cal.log_loss(test, cal.make_predictor(method, params)) < cal.log_loss(test, lambda raw: raw)


def test_band_table_counts_every_runner_once():
    races = _synthetic_races(300, true_b=1.0)
    table = cal.band_table(races, lambda raw: raw)
    assert sum(row['runners'] for row in table) == sum(len(r['raw']) for r in races)


PG_URL = os.environ.get('TEST_POSTGRES_URL')


@pytest.mark.skipif(not PG_URL, reason='set TEST_POSTGRES_URL to run against Postgres')
def test_run_end_to_end_on_postgres(capsys):
    import psycopg2
    if not hasattr(psycopg2, 'paramstyle'):
        pytest.skip('psycopg2 is stubbed out by another test module in this run')
    from sqlalchemy import create_engine, text
    from models import db

    engine = create_engine(PG_URL)
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE IF EXISTS calibration_runs'))
    db.metadata.drop_all(engine)
    db.metadata.create_all(engine)
    races = _synthetic_races(900, true_b=0.6)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (id, username, email, password_hash) VALUES (1,'u','u@x','x')"))
        hid = 0
        for race in races:
            mid = race['race_id'] + 1
            conn.execute(text("INSERT INTO meetings (id, user_id, meeting_name, date, auto_imported) "
                              "VALUES (:m, 1, 'x', :d, true)"), {'m': mid, 'd': race['day']})
            conn.execute(text("INSERT INTO races (id, meeting_id, race_number) VALUES (:r, :m, 1)"),
                         {'r': mid, 'm': mid})
            for i, (score, p) in enumerate(zip(race['scores'], race['market'])):
                hid += 1
                conn.execute(text("INSERT INTO horses (id, race_id, horse_name) VALUES (:h, :r, 'x')"),
                             {'h': hid, 'r': mid})
                conn.execute(text("INSERT INTO predictions (horse_id, score, ladbrokes_signal_mask) "
                                  "VALUES (:h, :s, 0)"), {'h': hid, 's': score})
                conn.execute(text("INSERT INTO results (horse_id, finish_position, sp) VALUES (:h, :f, :sp)"),
                             {'h': hid, 'f': 1 if i == race['winner'] else 5, 'sp': round(1 / (p * 1.15), 2)})
    cal.run(engine)
    out = capsys.readouterr().out
    assert 'ANALYZER CALIBRATION' in out and 'VERDICT' in out
    with engine.connect() as conn:
        run = conn.execute(text('SELECT * FROM calibration_runs')).mappings().one()
        assert run['model'] == 'analyzer' and run['test_races'] > 0 and run['train_to'] < run['test_from']
        assert run['calibrated_test_log_loss'] < run['raw_test_log_loss']
        total = conn.execute(text(
            'SELECT round(sum(p.calibrated_win_prob)::numeric, 4) FROM predictions p '
            'JOIN horses h ON h.id = p.horse_id WHERE h.race_id = 1')).scalar()
        assert float(total) == pytest.approx(1.0, abs=1e-3)
        assert cal.latest_params(conn)[0] == run['method']
        conn.execute(text('DROP TABLE calibration_runs'))
        conn.commit()
    db.metadata.drop_all(engine)
