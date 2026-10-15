"""Phase 4: make the analyzer's win probabilities honest.

The analyzer's raw probability is softmax(score / 15) over a race's runners,
the same as analyzer.js but WITHOUT the 10% margin, so each race sums to 100%.
It is overconfident at both ends. This job learns a correction and checks it.

  Data      Clean races only: auto-imported, not flagged incomplete, exactly
            one winner, every starter scored. Starters are runners with a
            finish (scratched runners are left out).
  Split     Oldest 70% of race days train, newest 30% test. The test set is
            never used to choose or tune anything.
  Methods   power  : p_i ∝ raw_i ** b  (one number, fitted by maximum likelihood)
            isotonic: a monotone map raw -> p, then renormalised per race
            The method is chosen on a validation slice taken from the END of
            the training period (last 20% of training days), then refitted on
            all training days.
  Check     Test races are grouped into bands by calibrated probability.
            PASS when every band with at least MIN_BAND_RUNNERS runners has an
            actual win rate within PASS_TOLERANCE_PP of its average prediction.
            The bookmaker market (SP, margin removed) is reported beside it as
            a benchmark.
  Output    predictions.calibrated_win_prob / calibrated_price for every
            scored runner (fair price, no margin), and one calibration_runs row
            with the method, parameters, split dates, band table and verdict.

Run:  python calibrate.py
"""
import bisect
import json
import logging
import math
import sys
from collections import defaultdict
from datetime import date

from sqlalchemy import text

log = logging.getLogger('calibrate')

ANALYZER_K = 15.0
TRAIN_SHARE = 0.7
VALIDATION_SHARE = 0.2      # of the training days, taken from its end
PASS_TOLERANCE_PP = 2.0
MIN_BAND_RUNNERS = 200
BANDS = [(0, 2), (2, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 40), (40, 100)]
PROB_FLOOR = 1e-4


# ─────────────────────────────────────────────
# Maths
# ─────────────────────────────────────────────
def softmax(scores, k=ANALYZER_K):
    top = max(scores)
    weights = [math.exp((s - top) / k) for s in scores]
    total = sum(weights)
    return [w / total for w in weights]


def normalise(values):
    values = [max(v, PROB_FLOOR) for v in values]
    total = sum(values)
    return [v / total for v in values]


def power_apply(raw, b):
    return normalise([p ** b for p in raw])


def log_loss(races, predict):
    """Mean negative log-likelihood of the winner, per race."""
    total = 0.0
    for race in races:
        probs = predict(race['raw'])
        total -= math.log(max(probs[race['winner']], PROB_FLOOR))
    return total / len(races)


def fit_power(races, lo=0.05, hi=3.0, steps=60):
    """Golden-section search for the exponent with the best race log-loss."""
    phi = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - phi * (b - a), a + phi * (b - a)
    fc = log_loss(races, lambda r: power_apply(r, c))
    fd = log_loss(races, lambda r: power_apply(r, d))
    for _ in range(steps):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - phi * (b - a)
            fc = log_loss(races, lambda r: power_apply(r, c))
        else:
            a, c, fc = c, d, fd
            d = a + phi * (b - a)
            fd = log_loss(races, lambda r: power_apply(r, d))
    return round((a + b) / 2, 4)


def fit_isotonic(races):
    """Monotone map raw -> win rate (pool-adjacent-violators). Returns (xs, ys)."""
    from sklearn.isotonic import IsotonicRegression
    xs, ys = [], []
    for race in races:
        for i, p in enumerate(race['raw']):
            xs.append(p)
            ys.append(1.0 if i == race['winner'] else 0.0)
    model = IsotonicRegression(y_min=PROB_FLOOR, y_max=1.0, out_of_bounds='clip')
    model.fit(xs, ys)
    return [float(x) for x in model.X_thresholds_], [float(y) for y in model.y_thresholds_]


def isotonic_apply(raw, params):
    xs, ys = params
    out = []
    for p in raw:
        if p <= xs[0]:
            out.append(ys[0])
        elif p >= xs[-1]:
            out.append(ys[-1])
        else:
            j = bisect.bisect_right(xs, p)
            x0, x1, y0, y1 = xs[j - 1], xs[j], ys[j - 1], ys[j]
            out.append(y0 + (y1 - y0) * (p - x0) / (x1 - x0) if x1 > x0 else y0)
    return normalise(out)


def make_predictor(method, params):
    if method == 'power':
        return lambda raw: power_apply(raw, params['b'])
    if method == 'isotonic':
        return lambda raw: isotonic_apply(raw, (params['xs'], params['ys']))
    return lambda raw: list(raw)


def band_table(races, predict, key='raw'):
    """[{band, runners, predicted_pct, actual_pct, diff_pp}] over all runners of `races`."""
    sums = defaultdict(lambda: [0, 0.0, 0])
    for race in races:
        probs = predict(race[key])
        for i, p in enumerate(probs):
            pct = 100 * p
            for lo, hi in BANDS:
                if lo <= pct < hi or (hi == 100 and pct >= lo):
                    cell = sums[(lo, hi)]
                    cell[0] += 1
                    cell[1] += p
                    cell[2] += 1 if i == race['winner'] else 0
                    break
    table = []
    for lo, hi in BANDS:
        n, psum, wins = sums[(lo, hi)]
        if not n:
            continue
        predicted, actual = 100 * psum / n, 100 * wins / n
        # Standard error of the actual win rate if the prediction were exactly right.
        q = predicted / 100
        se = 100 * math.sqrt(q * (1 - q) / n)
        table.append({'band': f'{lo}-{hi}%', 'runners': n, 'predicted_pct': round(predicted, 2),
                      'actual_pct': round(actual, 2), 'diff_pp': round(actual - predicted, 2),
                      'se_pp': round(se, 2)})
    return table


def passes(table):
    """Strict rule: every band with enough runners within PASS_TOLERANCE_PP."""
    return all(abs(row['diff_pp']) <= PASS_TOLERANCE_PP
               for row in table if row['runners'] >= MIN_BAND_RUNNERS)


def passes_within_noise(table):
    """Like passes(), but a miss inside two standard errors also counts.

    With a few hundred runners in a band, a perfectly calibrated model still
    misses by more than 2pp a good share of the time; this tells real misses
    from bad luck.
    """
    return all(abs(row['diff_pp']) <= max(PASS_TOLERANCE_PP, 2 * row['se_pp'])
               for row in table if row['runners'] >= MIN_BAND_RUNNERS)


def split_by_day(races, share):
    days = sorted({r['day'] for r in races})
    cut = days[min(len(days) - 1, max(1, int(len(days) * share)))]
    return [r for r in races if r['day'] < cut], [r for r in races if r['day'] >= cut]


def choose_and_fit(train):
    """Pick power vs isotonic on the end of the training period, then refit on all of it."""
    fit_part, validate_part = split_by_day(train, 1 - VALIDATION_SHARE)
    candidates = {
        'power': {'b': fit_power(fit_part)},
        'isotonic': dict(zip(('xs', 'ys'), fit_isotonic(fit_part))),
    }
    scores = {m: log_loss(validate_part, make_predictor(m, p)) for m, p in candidates.items()}
    method = min(scores, key=scores.get)
    params = {'b': fit_power(train)} if method == 'power' else dict(zip(('xs', 'ys'), fit_isotonic(train)))
    return method, params, scores


# ─────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────
def ensure_tables(conn):
    conn.execute(text('ALTER TABLE predictions ADD COLUMN IF NOT EXISTS calibrated_win_prob FLOAT'))
    conn.execute(text('ALTER TABLE predictions ADD COLUMN IF NOT EXISTS calibrated_price FLOAT'))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS calibration_runs (
            id SERIAL PRIMARY KEY,
            model VARCHAR(20) NOT NULL,
            method VARCHAR(20) NOT NULL,
            params TEXT NOT NULL,
            train_from DATE, train_to DATE, test_from DATE, test_to DATE,
            train_races INTEGER, test_races INTEGER,
            raw_test_log_loss FLOAT, calibrated_test_log_loss FLOAT,
            bands TEXT, raw_bands TEXT, market_bands TEXT,
            passed BOOLEAN NOT NULL,
            passed_within_noise BOOLEAN,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))


CLEAN_RACES_SQL = """
    SELECT r.id AS race_id, m.date AS meeting_date, m.meeting_name,
           h.id AS horse_id, p.score, res.finish_position, res.sp
    FROM races r
    JOIN meetings m ON m.id = r.meeting_id
    JOIN horses h ON h.race_id = r.id
    JOIN results res ON res.horse_id = h.id
    JOIN predictions p ON p.horse_id = h.id
    WHERE m.auto_imported
      AND r.results_incomplete IS NOT TRUE
      AND res.finish_position > 0
    ORDER BY r.id, h.id
"""


def load_clean_races(conn):
    from bsp_import import meeting_day
    grouped = defaultdict(list)
    meta = {}
    for row in conn.execute(text(CLEAN_RACES_SQL)).mappings():
        grouped[row['race_id']].append(row)
        meta[row['race_id']] = meeting_day(row['meeting_date'], row['meeting_name'])
    races = []
    for race_id, rows in grouped.items():
        winners = [i for i, r in enumerate(rows) if r['finish_position'] == 1]
        if len(winners) != 1 or len(rows) < 2 or meta[race_id] is None:
            continue
        if any(r['score'] is None for r in rows):
            continue
        sp = [r['sp'] for r in rows]
        races.append({
            'race_id': race_id,
            'day': meta[race_id],
            'winner': winners[0],
            'raw': softmax([r['score'] for r in rows]),
            # Bookmaker market with the margin removed; None if any SP is missing.
            'market': normalise([1 / s for s in sp]) if all(s and s > 1 for s in sp) else None,
        })
    return races


def latest_params(conn, model='analyzer'):
    """(method, params) of the newest calibration run, or None."""
    row = conn.execute(text(
        "SELECT method, params FROM calibration_runs WHERE model = :model ORDER BY id DESC LIMIT 1"
    ), {'model': model}).first()
    return (row.method, json.loads(row.params)) if row else None


def write_calibrated(conn, method, params):
    """Calibrated probability and fair price for every scored, active runner in every race."""
    predict = make_predictor(method, params)
    rows = conn.execute(text("""
        SELECT h.race_id, p.id AS prediction_id, p.score
        FROM predictions p JOIN horses h ON h.id = p.horse_id
        WHERE p.score IS NOT NULL AND NOT COALESCE(h.is_scratched, FALSE)
        ORDER BY h.race_id
    """)).mappings().all()
    by_race = defaultdict(list)
    for row in rows:
        by_race[row['race_id']].append(row)
    updates = []
    for race_rows in by_race.values():
        probs = predict(softmax([r['score'] for r in race_rows]))
        for r, p in zip(race_rows, probs):
            updates.append({'id': r['prediction_id'], 'p': round(p, 6), 'price': round(1 / p, 2)})
    if updates:
        conn.execute(text('UPDATE predictions SET calibrated_win_prob = :p, calibrated_price = :price '
                          'WHERE id = :id'), updates)
    return len(updates)


def run(engine):
    with engine.begin() as conn:
        ensure_tables(conn)
        races = load_clean_races(conn)
    if len(races) < 500:
        raise SystemExit(f'Only {len(races)} clean races: not enough to calibrate')
    train, test = split_by_day(races, TRAIN_SHARE)
    method, params, validation = choose_and_fit(train)
    predict = make_predictor(method, params)

    table = band_table(test, predict)
    raw_table = band_table(test, lambda raw: raw)
    market_races = [r for r in test if r['market']]
    market_table = band_table(market_races, lambda m: m, key='market')
    raw_loss = log_loss(test, lambda raw: raw)
    cal_loss = log_loss(test, predict)
    verdict = passes(table)
    noise_verdict = passes_within_noise(table)

    with engine.begin() as conn:
        written = write_calibrated(conn, method, params)
        conn.execute(text("""
            INSERT INTO calibration_runs (model, method, params, train_from, train_to, test_from, test_to,
                train_races, test_races, raw_test_log_loss, calibrated_test_log_loss,
                bands, raw_bands, market_bands, passed, passed_within_noise)
            VALUES ('analyzer', :method, :params, :tf, :tt, :sf, :st, :ntr, :nte, :rl, :cl,
                    :bands, :raw, :market, :passed, :noise)
        """), {
            'method': method, 'params': json.dumps(params),
            'tf': min(r['day'] for r in train), 'tt': max(r['day'] for r in train),
            'sf': min(r['day'] for r in test), 'st': max(r['day'] for r in test),
            'ntr': len(train), 'nte': len(test), 'rl': raw_loss, 'cl': cal_loss,
            'bands': json.dumps(table), 'raw': json.dumps(raw_table),
            'market': json.dumps(market_table), 'passed': verdict, 'noise': noise_verdict,
        })

    def show(title, rows):
        print(title, flush=True)
        for row in rows:
            if row['runners'] < MIN_BAND_RUNNERS:
                flag = ' (too few runners to judge)'
            elif abs(row['diff_pp']) <= PASS_TOLERANCE_PP:
                flag = ' OK'
            elif abs(row['diff_pp']) <= 2 * row['se_pp']:
                flag = ' MISS (within noise)'
            else:
                flag = ' MISS'
            print(f"  {row['band']:>8}  runners {row['runners']:>6}  predicted {row['predicted_pct']:6.2f}%"
                  f"  actual {row['actual_pct']:6.2f}%  diff {row['diff_pp']:+6.2f}pp"
                  f"  (+/-{2 * row['se_pp']:.1f}pp noise){flag}", flush=True)

    print('===== ANALYZER CALIBRATION =====', flush=True)
    print(f'Clean races: {len(races)}  train {len(train)} ({min(r["day"] for r in train)}..{max(r["day"] for r in train)})'
          f'  test {len(test)} ({min(r["day"] for r in test)}..{max(r["day"] for r in test)})', flush=True)
    print(f'Validation log-loss (end of train): {validation}  -> chose {method}', flush=True)
    if method == 'power':
        print(f'  exponent b = {params["b"]} (below 1 = squashes overconfident scores)', flush=True)
    print(f'Test log-loss: raw {raw_loss:.4f}  calibrated {cal_loss:.4f}', flush=True)
    show('Raw analyzer (margin removed, normalised) on TEST:', raw_table)
    show('Calibrated analyzer on TEST:', table)
    show(f'Bookmaker market (SP, margin removed) on TEST, {len(market_races)} races:', market_table)
    print(f'VERDICT (strict): {"PASS" if verdict else "FAIL"} (every band with {MIN_BAND_RUNNERS}+ runners '
          f'within {PASS_TOLERANCE_PP}pp)', flush=True)
    print(f'VERDICT (within noise): {"PASS" if noise_verdict else "FAIL"} '
          f'(misses inside 2 standard errors allowed)', flush=True)
    print(f'Wrote calibrated probability and price on {written} predictions', flush=True)
    return verdict


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout)
    from bsp_import import get_engine
    run(get_engine())


if __name__ == '__main__':
    main()
