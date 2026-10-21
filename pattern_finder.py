"""Pattern finder: searches past races for betting patterns that make a profit.

A pattern is one to three conditions on a runner, e.g. "maiden race" +
"Analyzer top pick" + "price $3-$5". Every pattern is a flat 1-unit win bet
on every runner that matches, settled at the PuntingForm SP.

How a pattern earns "proven":
  1. Races are split by date. The oldest 70% are the search races, the
     newest 30% are held back (unseen).
  2. Search: every single condition, every pair, and the best pairs extended
     to three conditions, on the search races only. A pattern goes forward
     if it has enough bets, a big enough profit, and a profit unlikely to be
     luck (z score vs a zero-edge market).
  3. Unseen test: survivors are checked on the held-back races. They must
     make a profit there too, and pass a luck test that allows for how many
     patterns were sent to the test (Benjamini-Hochberg, at most 1 in 10
     proven patterns expected to be luck).
  4. Luck benchmark: the whole search is re-run on made-up results where
     every runner wins exactly as often as its price says (no edge at all).
     Whatever "passes" there is pure luck, so it shows how much to trust
     the real count.

Proven patterns are saved (pattern_rules) and tracked on races run after
they were proven. A pattern is retired when that live record shows it has
stopped working. Retired patterns are never revived automatically.

"Price" and "market rank" conditions use the price at the jump. Today's
matches show them as "check at the jump" because they cannot be known early.

Analyzer components come from the analyzer notes. ML rank only counts when
the ML score was saved before the race started (forward data).

Run on its own:   python pattern_finder.py
Nightly:          backtest.main() calls run_and_save() at the end unless
                  PATTERN_FINDER=0.
"""
import json
import logging
import math
import os
import re
from datetime import date, datetime, timedelta

import numpy as np

log = logging.getLogger('pattern_finder')


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


SEARCH_SHARE = _env_float('PATTERN_SEARCH_SHARE', 0.70)       # oldest share of races used to search
MIN_SEARCH_BETS = int(_env_float('PATTERN_MIN_SEARCH_BETS', 150))
MIN_TEST_BETS = int(_env_float('PATTERN_MIN_TEST_BETS', 75))
MIN_SEARCH_ROI = _env_float('PATTERN_MIN_SEARCH_ROI', 0.05)    # +5%
SEARCH_Z = _env_float('PATTERN_SEARCH_Z', 2.0)
TEST_FDR = _env_float('PATTERN_TEST_FDR', 0.10)
MAX_TEST_CANDIDATES = int(_env_float('PATTERN_MAX_TEST_CANDIDATES', 200))
BEAM_WIDTH = int(_env_float('PATTERN_BEAM_WIDTH', 60))
MIN_CONDITION_BETS = int(_env_float('PATTERN_MIN_CONDITION_BETS', 150))
LUCK_RUNS = int(_env_float('PATTERN_LUCK_RUNS', 3))
MONTE_CARLO_SIMS = int(_env_float('PATTERN_MONTE_CARLO_SIMS', 5000))
OVERLAP_LIMIT = 0.7          # two patterns sharing 70%+ of their bets count as one
# Retiring a proven pattern, judged on races after it was proven.
RETIRE_MIN_BETS = int(_env_float('PATTERN_RETIRE_MIN_BETS', 50))
RETIRE_Z = _env_float('PATTERN_RETIRE_Z', -2.0)               # clearly losing
RETIRE_WINDOW = int(_env_float('PATTERN_RETIRE_WINDOW', 300))  # last N bets must not lose
BETFAIR_COMMISSION = 0.08


# ─────────────────────────────────────────────
# Loading runners
# ─────────────────────────────────────────────
_RUNNER_SQL = """
    SELECT h.id AS horse_id, h.race_id, h.horse_name, h.barrier, h.csv_data,
           rc.race_number, rc.race_class, rc.distance, rc.track_condition, rc.start_time_utc,
           rc.results_incomplete,
           m.id AS meeting_id, m.date AS meeting_date, m.meeting_name, m.track,
           p.score, p.notes, p.pfai_score, p.ml_score, p.ml_scored_at,
           r.id AS result_id, r.finish_position, r.sp, r.bsp
    FROM horses h
    JOIN races rc ON rc.id = h.race_id
    JOIN meetings m ON m.id = rc.meeting_id
    JOIN predictions p ON p.horse_id = h.id
    LEFT JOIN results r ON r.horse_id = h.id
    WHERE (h.is_scratched IS NULL OR h.is_scratched = :no)
"""
_PFAI_NOTES_RE = re.compile(r'PFAI Score:\s*([\d.]+)', re.IGNORECASE)


def _to_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _to_datetime(value):
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def meeting_date(value, meeting_name):
    """The meeting date column, else the YYMMDD prefix of the meeting name."""
    found = _to_date(value)
    if found:
        return found
    prefix = (meeting_name or '').split('_', 1)[0]
    if len(prefix) == 6 and prefix.isdigit():
        try:
            return datetime.strptime(prefix, '%y%m%d').date()
        except ValueError:
            return None
    return None


def _float(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _csv(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _runner_from_row(row):
    row = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
    csv_data = _csv(row.get('csv_data'))
    pfai = _float(row.get('pfai_score'))
    if pfai is None:
        for key in ('pfaiScore', 'PFAI Score', 'pfai_score'):
            pfai = _float(csv_data.get(key))
            if pfai is not None:
                break
    if pfai is None and row.get('notes'):
        match = _PFAI_NOTES_RE.search(row['notes'])
        pfai = _float(match.group(1)) if match else None
    barrier = row.get('barrier')
    if barrier in (None, ''):
        barrier = csv_data.get('horse barrier') or csv_data.get('barrier')
    return {
        'horse_id': row['horse_id'],
        'race_id': row['race_id'],
        'meeting_id': row.get('meeting_id'),
        'horse_name': row.get('horse_name') or '',
        'meeting_name': row.get('meeting_name') or '',
        'track': row.get('track') or '',
        'race_number': row.get('race_number'),
        'date': meeting_date(row.get('meeting_date'), row.get('meeting_name')),
        'start_time_utc': _to_datetime(row.get('start_time_utc')),
        'race_class': row.get('race_class') or '',
        'distance': row.get('distance'),
        'track_condition': row.get('track_condition') or '',
        'results_incomplete': bool(row.get('results_incomplete')),
        'barrier': _float(barrier),
        'age': _float(csv_data.get('horse age')),
        'sex': str(csv_data.get('horse sex') or '').strip().lower(),
        'notes': row.get('notes') or '',
        'score': _float(row.get('score')),
        'pfai': pfai,
        'ml_score': _float(row.get('ml_score')),
        'ml_scored_at': _to_datetime(row.get('ml_scored_at')),
        'has_result': row.get('result_id') is not None,
        'finish_position': row.get('finish_position'),
        'sp': _float(row.get('sp')),
        'bsp': _float(row.get('bsp')),
    }


def load_settled_runners(conn):
    """Every runner of every clean, resulted race, with race-level ranks filled.

    Races are dropped when results are flagged incomplete, when no runner
    has a result, or when there is no winner with a price.
    """
    from sqlalchemy import text
    rows = conn.execute(text(_RUNNER_SQL + " AND r.id IS NOT NULL AND r.finish_position > 0"),
                        {'no': False}).fetchall()
    runners = [_runner_from_row(row) for row in rows]
    runners = [r for r in runners if r['date'] and not r['results_incomplete']]
    races = _group(runners)
    kept = []
    for race in races.values():
        if not any(r['finish_position'] == 1 and r['sp'] for r in race):
            continue
        _add_race_ranks(race, forward_only=True)
        kept.extend(race)
    return kept


def load_upcoming_runners(conn, today=None, now_utc=None):
    """Runners in races from `today` on that have no results and have not started."""
    from sqlalchemy import text
    today = today or _melbourne_today()
    now_utc = now_utc or datetime.utcnow()
    rows = conn.execute(text(_RUNNER_SQL + " AND r.id IS NULL"), {'no': False}).fetchall()
    runners = [_runner_from_row(row) for row in rows]
    runners = [r for r in runners if r['date'] and r['date'] >= today
               and not (r['start_time_utc'] and r['start_time_utc'] <= now_utc)]
    out = []
    for race in _group(runners).values():
        _add_race_ranks(race, forward_only=False)
        out.extend(race)
    return out


def _melbourne_today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo('Australia/Melbourne')).date()
    except Exception:
        return (datetime.utcnow() + timedelta(hours=10)).date()


def _group(runners):
    races = {}
    for runner in runners:
        races.setdefault(runner['race_id'], []).append(runner)
    return races


def _rank(race, key, reverse):
    """Rank 1 = best. Ties share the better rank. None where the value is missing."""
    values = sorted({r[key] for r in race if r[key] is not None}, reverse=reverse)
    position = {}
    count = 0
    for value in values:
        position[value] = count + 1
        count += sum(1 for r in race if r[key] == value)
    return {id(r): position.get(r[key]) if r[key] is not None else None for r in race}


def _add_race_ranks(race, forward_only):
    field = len(race)
    for runner in race:
        runner['field'] = field
        # ML counts only when it was scored before the race started.
        if forward_only:
            start, scored = runner['start_time_utc'], runner['ml_scored_at']
            runner['ml_forward'] = bool(runner['ml_score'] is not None and start and scored and scored < start)
        else:
            runner['ml_forward'] = runner['ml_score'] is not None
        runner['_ml'] = runner['ml_score'] if runner['ml_forward'] else None
    for key, out, reverse in (('score', 'an_rank', True), ('pfai', 'pf_rank', True),
                              ('_ml', 'ml_rank', True), ('sp', 'mkt_rank', False)):
        ranks = _rank(race, key, reverse)
        for runner in race:
            runner[out] = ranks[id(runner)]


# ─────────────────────────────────────────────
# Conditions
# ─────────────────────────────────────────────
# Each condition: key -> (label, groups, at_jump). Two conditions sharing a
# group are never combined (they overlap or contradict). at_jump = needs the
# final market, so it cannot be checked ahead of the race.
PRICE_BANDS = ((1.0, 2.0), (2.0, 3.0), (3.0, 5.0), (5.0, 8.0), (8.0, 13.0), (13.0, 21.0), (21.0, None))


def _price_key(low, high):
    return f'price:{low:g}-{high:g}' if high else f'price:{low:g}+'


def _price_label(low, high):
    return f'Price ${low:g}–${high:g} at the jump' if high else f'Price ${low:g}+ at the jump'


BASE_CONDITIONS = {
    **{_price_key(lo, hi): (_price_label(lo, hi), ('price', 'mkt'), True) for lo, hi in PRICE_BANDS},
    'mkt:fav': ('Favourite at the jump', ('price', 'mkt'), True),
    'mkt:2-3': ('2nd or 3rd favourite at the jump', ('price', 'mkt'), True),
    'mkt:4+': ('4th favourite or longer at the jump', ('price', 'mkt'), True),
    'an:1': ('Analyzer top pick', ('an',), False),
    'an:2': ('Analyzer 2nd pick', ('an',), False),
    'an:3': ('Analyzer 3rd pick', ('an',), False),
    'an:4+': ('Analyzer 4th pick or lower', ('an',), False),
    'pf:1': ('PFAI top pick', ('pf',), False),
    'pf:2': ('PFAI 2nd pick', ('pf',), False),
    'pf:3': ('PFAI 3rd pick', ('pf',), False),
    'pf:4+': ('PFAI 4th pick or lower', ('pf',), False),
    'ml:1': ('ML top pick', ('ml',), False),
    'ml:2-3': ('ML 2nd or 3rd pick', ('ml',), False),
    'ml:4+': ('ML 4th pick or lower', ('ml',), False),
    'agree:an+pf': ('Analyzer and PFAI both top pick', ('agree', 'an', 'pf'), False),
    'agree:all3': ('Analyzer, PFAI and ML all top pick', ('agree', 'an', 'pf', 'ml'), False),
    'field:<=8': ('Field of 8 or fewer', ('field',), False),
    'field:9-12': ('Field of 9 to 12', ('field',), False),
    'field:13+': ('Field of 13 or more', ('field',), False),
    'class:maiden': ('Maiden race', ('class',), False),
    'class:group': ('Group or Listed race', ('class',), False),
    'class:bm': ('Benchmark race', ('class',), False),
    'class:class': ('Class or Restricted race', ('class',), False),
    'class:other': ('Other race class', ('class',), False),
    'dist:<1200': ('Under 1200m', ('dist',), False),
    'dist:1200-1599': ('1200m to 1599m', ('dist',), False),
    'dist:1600-1999': ('1600m to 1999m', ('dist',), False),
    'dist:2000+': ('2000m or more', ('dist',), False),
    'going:good': ('Good or firm track', ('going',), False),
    'going:soft': ('Soft track', ('going',), False),
    'going:heavy': ('Heavy track', ('going',), False),
    'going:synthetic': ('Synthetic track', ('going',), False),
    'barrier:1-3': ('Barrier 1 to 3', ('barrier',), False),
    'barrier:4-8': ('Barrier 4 to 8', ('barrier',), False),
    'barrier:9+': ('Barrier 9 or wider', ('barrier',), False),
    'age:2': ('2yo', ('age',), False),
    'age:3': ('3yo', ('age',), False),
    'age:4': ('4yo', ('age',), False),
    'age:5-6': ('5 or 6yo', ('age',), False),
    'age:7+': ('7yo or older', ('age',), False),
    'sex:male': ('Colt or entire horse', ('sex',), False),
    'sex:gelding': ('Gelding', ('sex',), False),
    'sex:female': ('Filly or mare', ('sex',), False),
}

_CLASS_RULES = (
    ('class:maiden', re.compile(r'maiden|mdn', re.I)),
    ('class:group', re.compile(r'group|listed|\bG\s?[123]\b|\bGr\.?\s?[123]\b|\bLR\b', re.I)),
    ('class:bm', re.compile(r'benchmark|\bBM\s?\d*\b|\bBm\d+', re.I)),
    ('class:class', re.compile(r'\bclass\s?\d|\bCL\s?\d|\bC\d\b|restricted|\brst\b|rating', re.I)),
)


def _class_key(race_class):
    for key, pattern in _CLASS_RULES:
        if pattern.search(race_class or ''):
            return key
    return 'class:other'


def _distance_m(value):
    match = re.search(r'(\d{3,4})', str(value or ''))
    return int(match.group(1)) if match else None


def _going_key(track_condition):
    text_value = str(track_condition or '').lower()
    if not text_value:
        return None
    if any(word in text_value for word in ('synth', 'poly', 'tapeta', 'all weather')):
        return 'going:synthetic'
    for word, key in (('heavy', 'going:heavy'), ('soft', 'going:soft'), ('good', 'going:good'), ('firm', 'going:good')):
        if word in text_value:
            return key
    match = re.search(r'(\d+)', text_value)
    if match:
        n = int(match.group(1))
        if 1 <= n <= 4:
            return 'going:good'
        if 5 <= n <= 7:
            return 'going:soft'
        if 8 <= n <= 10:
            return 'going:heavy'
    return None


def _sex_key(sex):
    if sex in ('gelding', 'g'):
        return 'sex:gelding'
    if sex in ('colt', 'horse', 'c', 'h', 'entire'):
        return 'sex:male'
    if sex in ('filly', 'mare', 'f', 'm'):
        return 'sex:female'
    return None


def component_group(name):
    """Components from the same analyzer section are never combined."""
    if name.startswith('Country:'):
        return 'c:Country'
    return 'c:' + name.split(' - ', 1)[0].strip()


def runner_conditions(runner, components=None):
    """The set of condition keys this runner matches."""
    keys = set()
    sp = runner.get('sp')
    if sp:
        for lo, hi in PRICE_BANDS:
            if sp >= lo and (hi is None or sp < hi):
                keys.add(_price_key(lo, hi))
    mkt = runner.get('mkt_rank')
    if mkt:
        keys.add('mkt:fav' if mkt == 1 else 'mkt:2-3' if mkt <= 3 else 'mkt:4+')
    for prefix, rank in (('an', runner.get('an_rank')), ('pf', runner.get('pf_rank'))):
        if rank:
            keys.add(f'{prefix}:{rank}' if rank <= 3 else f'{prefix}:4+')
    ml = runner.get('ml_rank')
    if ml:
        keys.add('ml:1' if ml == 1 else 'ml:2-3' if ml <= 3 else 'ml:4+')
    if runner.get('an_rank') == 1 and runner.get('pf_rank') == 1:
        keys.add('agree:an+pf')
        if ml == 1:
            keys.add('agree:all3')
    field = runner.get('field') or 0
    if field:
        keys.add('field:<=8' if field <= 8 else 'field:9-12' if field <= 12 else 'field:13+')
    keys.add(_class_key(runner.get('race_class')))
    dist = _distance_m(runner.get('distance'))
    if dist:
        keys.add('dist:<1200' if dist < 1200 else 'dist:1200-1599' if dist < 1600
                 else 'dist:1600-1999' if dist < 2000 else 'dist:2000+')
    going = _going_key(runner.get('track_condition'))
    if going:
        keys.add(going)
    barrier = runner.get('barrier')
    if barrier and barrier > 0:
        keys.add('barrier:1-3' if barrier <= 3 else 'barrier:4-8' if barrier <= 8 else 'barrier:9+')
    age = runner.get('age')
    if age:
        age = int(age)
        keys.add('age:2' if age <= 2 else 'age:3' if age == 3 else 'age:4' if age == 4
                 else 'age:5-6' if age <= 6 else 'age:7+')
    sex = _sex_key(runner.get('sex') or '')
    if sex:
        keys.add(sex)
    if components is None:
        from notes_parsing import parse_notes_components
        components = parse_notes_components(runner.get('notes'))
    for name in components:
        if not name.startswith('_'):
            keys.add('c:' + name)
    return keys


def condition_info(key):
    """(label, groups, at_jump) for any condition key, components included."""
    if key in BASE_CONDITIONS:
        return BASE_CONDITIONS[key]
    if key.startswith('c:'):
        name = key[2:]
        return (name, (component_group(name),), False)
    return (key, (key,), False)


def rule_key(conditions):
    return ' & '.join(sorted(conditions))


def rule_label(conditions):
    return ' + '.join(condition_info(k)[0] for k in _display_order(conditions))


def _display_order(conditions):
    # Form and model conditions first, jump-price conditions last.
    return sorted(conditions, key=lambda k: (condition_info(k)[2], k))


# ─────────────────────────────────────────────
# Statistics
# ─────────────────────────────────────────────
def bet_stats(won, sp, bsp=None):
    """Flat 1-unit win bets. z is profit against a zero-edge market (win
    chance = 1/price), whose variance per bet is price - 1."""
    won = np.asarray(won, dtype=bool)
    sp = np.asarray(sp, dtype=float)
    n = int(len(sp))
    if n == 0:
        return {'bets': 0, 'wins': 0, 'strike_rate': 0.0, 'profit': 0.0, 'roi': 0.0, 'z': 0.0}
    profit = float(np.sum(np.where(won, sp - 1.0, -1.0)))
    var0 = float(np.sum(sp - 1.0))
    out = {
        'bets': n,
        'wins': int(won.sum()),
        'strike_rate': round(100.0 * float(won.mean()), 1),
        'profit': round(profit, 2),
        'roi': round(100.0 * profit / n, 1),
        'z': round(profit / math.sqrt(var0), 2) if var0 > 0 else 0.0,
        'avg_price': round(float(np.mean(sp)), 2),
    }
    if bsp is not None:
        bsp = np.asarray(bsp, dtype=float)
        has = np.isfinite(bsp) & (bsp > 1.0)
        if has.sum() >= max(20, 0.5 * n):
            b_profit = np.where(won[has], (bsp[has] - 1.0) * (1.0 - BETFAIR_COMMISSION), -1.0)
            out['bsp_bets'] = int(has.sum())
            out['bsp_roi'] = round(100.0 * float(b_profit.mean()), 1)
    return out


def monte_carlo_p(sp, profit, sims=None, seed=0):
    """Chance a zero-edge bettor makes at least `profit` on these prices.
    Each bet wins with probability 1/price."""
    sims = MONTE_CARLO_SIMS if sims is None else sims
    sp = np.asarray(sp, dtype=float)
    if len(sp) == 0:
        return 1.0
    rng = np.random.default_rng(seed)
    p = np.clip(1.0 / sp, 0.0, 1.0)
    hits = 0
    done = 0
    chunk = max(1, min(sims, int(4_000_000 // max(1, len(sp)))))
    while done < sims:
        size = min(chunk, sims - done)
        wins = rng.random((size, len(sp))) < p
        sim_profit = (wins * sp).sum(axis=1) - len(sp)
        hits += int(np.sum(sim_profit >= profit - 1e-9))
        done += size
    return (hits + 1) / (sims + 1)


def benjamini_hochberg(p_values, q):
    """Indexes that pass Benjamini-Hochberg at false-discovery rate q."""
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    k = len(p_values)
    cutoff = -1
    for rank, i in enumerate(order, start=1):
        if p_values[i] <= q * rank / k:
            cutoff = rank
    return set(order[:cutoff]) if cutoff > 0 else set()


# ─────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────
class Dataset:
    """Runners as arrays plus a runner x condition matrix."""

    def __init__(self, runners, keys_per_runner, search_share=None, min_condition_bets=None):
        search_share = SEARCH_SHARE if search_share is None else search_share
        min_condition_bets = MIN_CONDITION_BETS if min_condition_bets is None else min_condition_bets
        order = sorted(range(len(runners)), key=lambda i: (runners[i]['date'], runners[i]['race_id']))
        runners = [runners[i] for i in order]
        keys_per_runner = [keys_per_runner[i] for i in order]
        # Bets need a price; runners without one still counted for ranks.
        has_price = [bool(r['sp'] and r['sp'] > 1.0) for r in runners]
        self.runners = [r for r, ok in zip(runners, has_price) if ok]
        keys_per_runner = [k for k, ok in zip(keys_per_runner, has_price) if ok]
        n = len(self.runners)
        self.n = n
        self.won = np.array([r['finish_position'] == 1 for r in self.runners], dtype=bool)
        self.sp = np.array([r['sp'] for r in self.runners], dtype=float)
        self.bsp = np.array([r['bsp'] if r['bsp'] else np.nan for r in self.runners], dtype=float)
        self.dates = [r['date'] for r in self.runners]
        self.race_ids = [r['race_id'] for r in self.runners]

        race_dates = sorted({(r['date'], r['race_id']) for r in self.runners})
        self.races = len(race_dates)
        if race_dates:
            cut_index = min(len(race_dates) - 1, int(len(race_dates) * search_share))
            self.cutoff = race_dates[cut_index][0]   # first unseen date
        else:
            self.cutoff = None
        self.is_search = np.array([d < self.cutoff for d in self.dates], dtype=bool) if n else np.zeros(0, bool)
        self.first_date = self.dates[0] if n else None
        self.last_date = self.dates[-1] if n else None

        counts = {}
        for keys in keys_per_runner:
            for key in keys:
                counts[key] = counts.get(key, 0) + 1
        search_counts = {}
        for keys, s in zip(keys_per_runner, self.is_search):
            if s:
                for key in keys:
                    search_counts[key] = search_counts.get(key, 0) + 1
        # A condition nearly every runner has tells us nothing.
        search_n = int(self.is_search.sum())
        self.keys = sorted(k for k, c in search_counts.items()
                           if c >= min_condition_bets and c <= 0.95 * search_n)
        self.index = {k: i for i, k in enumerate(self.keys)}
        self.matrix = np.zeros((n, len(self.keys)), dtype=bool)
        for row, keys in enumerate(keys_per_runner):
            for key in keys:
                col = self.index.get(key)
                if col is not None:
                    self.matrix[row, col] = True
        groups = [set(condition_info(k)[1]) for k in self.keys]
        c = len(self.keys)
        self.conflict = np.zeros((c, c), dtype=bool)
        for i in range(c):
            for j in range(i, c):
                if i == j or groups[i] & groups[j]:
                    self.conflict[i, j] = self.conflict[j, i] = True

    def mask(self, conditions):
        out = np.ones(self.n, dtype=bool)
        for key in conditions:
            col = self.index.get(key)
            if col is None:
                return np.zeros(self.n, dtype=bool)
            out &= self.matrix[:, col]
        return out


def _search_candidates(ds, won):
    """Patterns that pass the search-races screen, best first, overlaps removed.
    Returns (candidates, patterns_tested)."""
    s = ds.is_search
    if not s.any() or not ds.keys:
        return [], 0
    M = ds.matrix[s].astype(np.float32)
    sp = ds.sp[s].astype(np.float32)
    r = np.where(won[s], sp - 1.0, -1.0).astype(np.float32)
    v = (sp - 1.0).astype(np.float32)

    def screen(n, profit, var):
        with np.errstate(divide='ignore', invalid='ignore'):
            roi = np.where(n > 0, profit / n, 0.0)
            z = np.where(var > 0, profit / np.sqrt(var), 0.0)
        return roi, z

    found = []
    # Singles
    n1 = M.sum(axis=0)
    roi1, z1 = screen(n1, M.T @ r, M.T @ v)
    tested = len(ds.keys)
    for i in range(len(ds.keys)):
        if n1[i] >= MIN_SEARCH_BETS and roi1[i] >= MIN_SEARCH_ROI and z1[i] >= SEARCH_Z:
            found.append(((ds.keys[i],), float(z1[i])))
    # Pairs
    N2 = M.T @ M
    roi2, z2 = screen(N2, (M * r[:, None]).T @ M, (M * v[:, None]).T @ M)
    upper = np.triu(np.ones_like(ds.conflict), k=1) & ~ds.conflict & (N2 > 0)
    tested += int(upper.sum())
    ok = upper & (N2 >= MIN_SEARCH_BETS) & (roi2 >= MIN_SEARCH_ROI) & (z2 >= SEARCH_Z)
    for i, j in zip(*np.nonzero(ok)):
        found.append(((ds.keys[i], ds.keys[j]), float(z2[i, j])))
    # Triples: extend the strongest pairs with enough bets.
    eligible = upper & (N2 >= MIN_SEARCH_BETS)
    pair_idx = list(zip(*np.nonzero(eligible)))
    pair_idx.sort(key=lambda ij: -z2[ij[0], ij[1]])
    seen = set()
    for i, j in pair_idx[:BEAM_WIDTH]:
        pm = (M[:, i] * M[:, j])
        n3 = M.T @ pm
        roi3, z3 = screen(n3, M.T @ (pm * r), M.T @ (pm * v))
        for k in range(len(ds.keys)):
            if k in (i, j) or ds.conflict[i, k] or ds.conflict[j, k] or n3[k] == 0:
                continue
            combo = tuple(sorted((ds.keys[i], ds.keys[j], ds.keys[k])))
            if combo in seen:
                continue
            seen.add(combo)
            tested += 1
            if n3[k] >= MIN_SEARCH_BETS and roi3[k] >= MIN_SEARCH_ROI and z3[k] >= SEARCH_Z:
                found.append((combo, float(z3[k])))

    found.sort(key=lambda item: -item[1])
    kept, kept_masks = [], []
    for conditions, z in found:
        m = ds.mask(conditions) & s
        twin = next((i for i, other in enumerate(kept_masks) if _overlap(m, other) >= OVERLAP_LIMIT), None)
        if twin is not None:
            # Same bets near enough: keep the simpler pattern.
            if set(conditions) < set(kept[twin]):
                kept[twin], kept_masks[twin] = conditions, m
            continue
        if len(kept) < MAX_TEST_CANDIDATES:
            kept.append(conditions)
            kept_masks.append(m)
    return kept, tested


def _overlap(a, b):
    union = np.count_nonzero(a | b)
    return np.count_nonzero(a & b) / union if union else 0.0


def drop_slices(rows):
    """Drop a pattern when a simpler proven pattern holds all its conditions.
    Slicing a real edge (by price, field size...) makes many lookalikes."""
    sets = [set(row['conditions']) for row in rows]
    return [row for row, mine in zip(rows, sets) if not any(other < mine for other in sets)]


def find_patterns(ds, won=None, seed=0):
    """Search then unseen test. Returns (proven, near_misses, patterns_tested, candidates)."""
    won = ds.won if won is None else won
    candidates, tested = _search_candidates(ds, won)
    unseen = ~ds.is_search
    rows = []
    for idx, conditions in enumerate(candidates):
        m = ds.mask(conditions)
        sm, tm = m & ds.is_search, m & unseen
        search = bet_stats(won[sm], ds.sp[sm], ds.bsp[sm])
        test = bet_stats(won[tm], ds.sp[tm], ds.bsp[tm])
        if test['bets'] >= MIN_TEST_BETS and test['profit'] > 0 and test['z'] > 1.0:
            p = monte_carlo_p(ds.sp[tm], test['profit'], seed=seed + idx)
        else:
            p = 1.0
        test['p_value'] = round(p, 4)
        rows.append({'conditions': list(conditions), 'search': search, 'test': test})
    passed = benjamini_hochberg([row['test']['p_value'] for row in rows], TEST_FDR) if rows else set()
    proven, near = [], []
    for i, row in enumerate(rows):
        ok = (i in passed and row['test']['bets'] >= MIN_TEST_BETS and row['test']['profit'] > 0)
        (proven if ok else near).append(row)
    proven = drop_slices(proven)
    proven.sort(key=lambda row: row['test']['p_value'])
    near.sort(key=lambda row: (-(row['test']['roi'] if row['test']['bets'] else -999)))
    return proven, near, tested, len(candidates)


def luck_benchmark(ds, runs=None, seed=12345):
    """Re-run the search on made-up results with no edge: every runner wins
    with probability 1/price. Returns how many patterns passed each stage."""
    runs = LUCK_RUNS if runs is None else runs
    rng = np.random.default_rng(seed)
    out = []
    p = np.clip(1.0 / ds.sp, 0.0, 1.0)
    for run in range(runs):
        fake_won = rng.random(ds.n) < p
        proven, _near, _tested, candidates = find_patterns(ds, won=fake_won, seed=seed + 1000 * (run + 1))
        out.append({'passed_search': candidates, 'proven': len(proven)})
    return out


# ─────────────────────────────────────────────
# Live record and retiring
# ─────────────────────────────────────────────
def live_record(ds, conditions, after_date):
    """Results on races after `after_date` (the races the rule never saw)."""
    if ds.n == 0:
        return bet_stats([], [])
    m = ds.mask(conditions) & np.array([d > after_date for d in ds.dates], dtype=bool) \
        if after_date else ds.mask(conditions)
    stats = bet_stats(ds.won[m], ds.sp[m], ds.bsp[m])
    if stats['bets'] >= RETIRE_WINDOW:
        idx = np.nonzero(m)[0][-RETIRE_WINDOW:]
        recent = bet_stats(ds.won[idx], ds.sp[idx])
        stats['recent_bets'] = recent['bets']
        stats['recent_roi'] = recent['roi']
    return stats


def retire_reason(live):
    """Why a proven rule should be retired, or None to keep it."""
    if live['bets'] >= RETIRE_MIN_BETS and live['z'] <= RETIRE_Z:
        return (f"Clearly losing since proven: {live['bets']} bets, ROI {live['roi']:+.1f}% "
                f"(z {live['z']:.1f})")
    if live.get('recent_bets', 0) >= RETIRE_WINDOW and live.get('recent_roi', 0.0) < 0:
        return f"Lost money over its last {RETIRE_WINDOW} bets (ROI {live['recent_roi']:+.1f}%)"
    return None


def build_dataset(runners, search_share=None):
    from notes_parsing import parse_notes_components
    cache = {}
    keys = []
    for runner in runners:
        notes = runner.get('notes') or ''
        if notes not in cache:
            cache[notes] = parse_notes_components(notes)
        keys.append(runner_conditions(runner, cache[notes]))
    return Dataset(runners, keys, search_share=search_share)


# ─────────────────────────────────────────────
# Full run with saving
# ─────────────────────────────────────────────
def _ensure_tables(engine):
    from models import PatternFinderRun, PatternRule
    PatternRule.__table__.create(engine, checkfirst=True)
    PatternFinderRun.__table__.create(engine, checkfirst=True)


def _fmt_date(value):
    return value.isoformat() if value else None


def run_and_save(engine, luck_runs=None):
    """Load, search, test, update saved rules, save the run report. Returns the report."""
    from sqlalchemy import select, insert, update
    from models import PatternFinderRun, PatternRule

    luck_runs = LUCK_RUNS if luck_runs is None else luck_runs
    started = datetime.utcnow()
    _ensure_tables(engine)
    with engine.connect() as conn:
        runners = load_settled_runners(conn)
    log.info("PATTERN_FINDER loaded %s runners", len(runners))
    ds = build_dataset(runners)
    log.info("PATTERN_FINDER %s bets, %s races, %s conditions, unseen from %s",
             ds.n, ds.races, len(ds.keys), ds.cutoff)

    proven, near, tested, candidates = find_patterns(ds)
    luck = luck_benchmark(ds, runs=luck_runs) if luck_runs else []
    log.info("PATTERN_FINDER tested=%s passed_search=%s proven=%s luck=%s",
             tested, candidates, len(proven), luck)

    rules_t = PatternRule.__table__
    now = datetime.utcnow()
    added, retired = [], []
    with engine.begin() as conn:
        existing = {row.rule_key: row for row in conn.execute(select(rules_t)).fetchall()}
        # Masks of rules already proven, so a near copy of one is not added again.
        taken_sets = [json.loads(r.conditions_json) for r in existing.values() if r.status == 'proven']
        taken = [ds.mask(c) for c in taken_sets]
        for row in proven:
            key = rule_key(row['conditions'])
            if key in existing:
                continue
            m = ds.mask(row['conditions'])
            if any(_overlap(m, other) >= OVERLAP_LIMIT for other in taken) or \
                    any(set(c) < set(row['conditions']) for c in taken_sets):
                continue
            taken.append(m)
            taken_sets.append(row['conditions'])
            conn.execute(insert(rules_t).values(
                rule_key=key,
                conditions_json=json.dumps(row['conditions']),
                label=rule_label(row['conditions'])[:500],
                status='proven',
                proven_at=now,
                proven_through=ds.last_date,
                history_json=json.dumps({'search': row['search'], 'test': row['test'],
                                         'search_to': _fmt_date(ds.cutoff),
                                         'test_to': _fmt_date(ds.last_date)}),
            ))
            added.append(key)
        for row in conn.execute(select(rules_t)).fetchall():
            conditions = json.loads(row.conditions_json)
            live = live_record(ds, conditions, _to_date(row.proven_through))
            values = {'live_json': json.dumps(live), 'last_checked_at': now}
            if row.status == 'proven':
                reason = retire_reason(live)
                if reason:
                    values.update(status='retired', retired_at=now, retire_reason=reason[:300])
                    retired.append(row.rule_key)
            conn.execute(update(rules_t).where(rules_t.c.id == row.id).values(**values))

    def short(rows, limit):
        return [{'conditions': r['conditions'], 'label': rule_label(r['conditions']),
                 'search': r['search'], 'test': r['test']} for r in rows[:limit]]

    report = {
        'started_at': started.isoformat(),
        'seconds': round((datetime.utcnow() - started).total_seconds()),
        'bets': ds.n, 'races': ds.races, 'conditions': len(ds.keys),
        'first_date': _fmt_date(ds.first_date), 'unseen_from': _fmt_date(ds.cutoff),
        'last_date': _fmt_date(ds.last_date),
        'patterns_tested': tested, 'passed_search': candidates, 'proven_this_run': len(proven),
        'new_rules': added, 'retired_rules': retired,
        'luck': luck,
        'proven': short(proven, 50), 'near_misses': short(near, 25),
        'settings': {
            'search_share': SEARCH_SHARE, 'min_search_bets': MIN_SEARCH_BETS,
            'min_test_bets': MIN_TEST_BETS, 'min_search_roi_pct': 100 * MIN_SEARCH_ROI,
            'search_z': SEARCH_Z, 'test_fdr': TEST_FDR, 'beam_width': BEAM_WIDTH,
            'retire_min_bets': RETIRE_MIN_BETS, 'retire_z': RETIRE_Z, 'retire_window': RETIRE_WINDOW,
        },
    }
    with engine.begin() as conn:
        conn.execute(insert(PatternFinderRun.__table__).values(
            created_at=now, status='complete', report_json=json.dumps(report, default=str)))
    log.info("PATTERN_FINDER done in %ss: %s new, %s retired", report['seconds'], len(added), len(retired))
    return report


def todays_matches(conn, rules, today=None, now_utc=None):
    """Upcoming runners that match a proven rule's form conditions.

    Jump conditions (price, favourite) can't be known yet; they are returned
    as `check_at_jump` for the punter to confirm before betting.
    """
    from notes_parsing import parse_notes_components
    runners = load_upcoming_runners(conn, today=today, now_utc=now_utc)
    parsed = []
    for runner in runners:
        parsed.append((runner, runner_conditions(runner, parse_notes_components(runner['notes']))))
    out = []
    for rule in rules:
        conditions = rule['conditions']
        form = [k for k in conditions if not condition_info(k)[2]]
        jump = [condition_info(k)[0] for k in conditions if condition_info(k)[2]]
        if not form:
            continue   # nothing to check before the jump
        for runner, keys in parsed:
            if all(k in keys for k in form):
                out.append({
                    'rule_id': rule['id'], 'rule_label': rule['label'],
                    'horse_name': runner['horse_name'], 'meeting_name': runner['meeting_name'],
                    'meeting_id': runner['meeting_id'],
                    'race_number': runner['race_number'], 'start_time_utc': runner['start_time_utc'],
                    'check_at_jump': jump,
                })
    out.sort(key=lambda m: (m['start_time_utc'] or datetime.max, m['meeting_name'], m['race_number'] or 0))
    return out


def enabled():
    return os.environ.get('PATTERN_FINDER', '1').strip().lower() not in ('0', 'false', 'no', 'off')


if __name__ == '__main__':
    import sys
    from sqlalchemy import create_engine

    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    url = os.environ.get('DATABASE_URL', '')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    if not url:
        sys.exit('DATABASE_URL is not set')
    report = run_and_save(create_engine(url, pool_pre_ping=True))
    print(json.dumps({k: report[k] for k in ('bets', 'races', 'patterns_tested', 'passed_search',
                                             'proven_this_run', 'luck')}, default=str))
