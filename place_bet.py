"""Place Bet routes: the only Best Bets. Each is a PLACE bet, settled on the place
(Betfair place BSP after commission), never as a win bet.

All six rules came from the 28 Oct 2026 audit (audit/tasks/similar.py): every
single condition and pair tested Feb-Sep 2026 at Betfair place BSP, against
three made-up worlds with no edge. 24 back-place patterns passed (luck alone
gave 1-5). These six can be worked out before the race from the form file
and the analyzer notes; the rest needed the price at the jump or Betfair
place results that only arrive monthly.

Each rule reads exactly what the audit read:
- last start position: right-most digit of 'horse last10' (the saved 'form
  position' held an older run until 28 Oct 2026, so it is only a fallback)
- last start margin: the saved 'form margin' (lengths behind the winner, or
  for a winner lengths in front of second)
- analyzer checks: the component names parse_notes_components() finds in the
  prediction notes, the same parser the audit used
"""
import hashlib
import json
import logging
import os
import pickle
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text

from notes_parsing import parse_notes_components

logger = logging.getLogger(__name__)

LIVE_START = datetime(2026, 10, 28)
COMMISSION = 0.08
STAKE = 10.0


@dataclass(frozen=True)
class Rule:
    key: str                 # stored in Prediction.best_bet_routes; never a substring of another key
    name: str                # plain words, shown on the pages
    short: str               # chart button / badge
    components: tuple        # analyzer component names that must all be in the notes
    last_pos: int = None     # last start finishing position must equal this
    max_margin: float = None  # last start margin must be below this (lengths)
    note_like: tuple = field(default_factory=tuple)  # SQL LIKE fragments that narrow the history query
    audit: str = ''          # what the audit found
    retired: str = ''        # why and when it stopped making picks ('' = still live)


# October 2026 was the first month the rules had never seen (they were fitted on
# Feb-Sep). Together they made -4.4% on 568 place bets there (audit/tasks/octcheck.py);
# only place_32_tdnowin held up (+17.3% on 162). The rest stopped making picks on
# 10 Nov 2026. Their record still shows, so a comeback would be visible.
_RETIRED_OCT = 'Retired 10 Nov 2026: {oct} in October, the first month it had not been fitted on'

# A live rule that is still losing at Betfair place SP after this many live bets
# stops making picks (see active_rules).
RETIRE_AFTER_BETS = 500

RULES = (
    Rule('place_2nd_light', 'Ran 2nd last start + carries 2-3kg less than the field average',
         '2nd last start, 2-3kg light', ('Weight vs Field - Below (2-3kg)',), last_pos=2,
         note_like=('%below race avg%',),
         audit='533 bets, +35%, up in 7 of 8 months',
         retired=_RETIRED_OCT.format(oct='+1.8% on 71 bets')),
    Rule('place_close_trackpod', 'Finished within 1 length last start + placed in 55-69% of runs at this track',
         'Within 1L last start, track placer', ('Track Podium Rate - Strong (55-69%)',), max_margin=1.0,
         note_like=('%Strong podium rate%at this track%',),
         audit='804 bets, +21%, up in 6 of 8 months',
         retired=_RETIRED_OCT.format(oct='-5.1% on 100 bets')),
    Rule('place_32_tdnowin', 'Last-5 placings start 3rd then 2nd + has run at this track and distance without winning',
         '3rd-2nd, no track+dist win', ('Ran places: 3rd 2nd', 'Track+Distance Win Rate - No Wins'),
         note_like=('%Ran places: 3rd 2nd%',),
         audit='1,111 bets, +19%, up in 6 of 8 months'),
    Rule('place_32_poortrack', 'Last-5 placings start 3rd then 2nd + poor record at this track',
         '3rd-2nd, poor at track', ('Ran places: 3rd 2nd', 'Track - Poor Performance'),
         note_like=('%Ran places: 3rd 2nd%',),
         audit='731 bets, +26%, up in 6 of 8 months',
         retired=_RETIRED_OCT.format(oct='-1.5% on 97 bets')),
    Rule('place_goingpod_backsprint', 'Placed in 55-69% of runs on today\'s going + backmarker in a sprint (1200m or less)',
         'Going placer, backmarker sprint', ('Condition Podium Rate - Strong (55-69%)', 'Running Position - Backmarker Sprint'),
         note_like=('%BACKMARKER in Sprint%',),
         audit='690 bets, +26%, up in 6 of 8 months',
         retired=_RETIRED_OCT.format(oct='-18.4% on 110 bets')),
    Rule('place_won_onpace_mile', 'Won last start + on pace in a mile race (1300-1700m)',
         'Won last start, on pace mile', ('Running Position - OnPace Mile',), last_pos=1,
         note_like=('%ONPACE in Mile%',),
         audit='655 bets, +24%, up in 6 of 8 months (smaller lately)',
         retired=_RETIRED_OCT.format(oct='-2.2% on 130 bets')),
)
RULES_BY_KEY = {rule.key: rule for rule in RULES}
ROUTE_KEYS = tuple(rule.key for rule in RULES)
LIVE_RULES = tuple(rule for rule in RULES if not rule.retired)


def failing_live(rule, record):
    """True when a rule's live record has RETIRE_AFTER_BETS+ priced bets and is losing."""
    for r in (record or {}).get('rules', []):
        if r.get('key') == rule.key:
            live = r.get('live') or {}
            return (live.get('priced_bets') or 0) >= RETIRE_AFTER_BETS and (live.get('roi') or 0) < 0
    return False


def active_rules(record=None):
    """Rules that make picks: not retired, and not losing after RETIRE_AFTER_BETS live bets."""
    return tuple(rule for rule in LIVE_RULES if not failing_live(rule, record))

# The first rule keeps its old names so existing callers and saved routes still work.
ROUTE = RULES[0].key
COMPONENT = RULES[0].components[0]
BADGE = '🥉 Place Bet: 2nd last start, 2-3kg under the field'
REASON = 'Back for a PLACE, not the win: ' + RULES[0].name + '.'


def badge(rule):
    return f'🥉 Place: {rule.short}'


def reason(rule):
    return f'Back for a PLACE, not the win: {rule.name}. Feb-Sep test: {rule.audit}.'


def _csv(horse_or_value):
    value = getattr(horse_or_value, 'csv_data', horse_or_value)
    while isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def last_start_position(horse_or_csv):
    """Finish position at the horse's most recent start, or None.

    Read from the 'horse last10' form string, whose right-most digit is the
    latest run ('x' marks a spell, '0' is 10th or worse) - exactly as the
    audit frame reads it. The saved 'form position' field held an older run
    for many horses until 28 Oct 2026, so it is never used.
    """
    last10 = str(_csv(horse_or_csv).get('horse last10') or '').strip().lower().rstrip('x')
    if not last10 or not last10[-1].isdigit():
        return None
    return 10 if last10[-1] == '0' else int(last10[-1])


def last_start_margin(horse_or_csv):
    """Saved last-start margin in lengths, or None. Same reading as the audit frame."""
    value = _csv(horse_or_csv).get('form margin')
    try:
        if value is None or value == '':
            return None
        number = float(str(value).replace('$', '').replace(',', '').strip().rstrip('Ll'))
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def note_components(notes):
    """Set of analyzer component names in the notes (same parser as the audit)."""
    if not notes:
        return set()
    try:
        return set(parse_notes_components(notes))
    except Exception:
        return set()


def rule_matches(rule, csv_data, components):
    if not all(name in components for name in rule.components):
        return False
    if rule.last_pos is not None and last_start_position(csv_data) != rule.last_pos:
        return False
    if rule.max_margin is not None:
        margin = last_start_margin(csv_data)
        if margin is None or not margin < rule.max_margin:
            return False
    return True


def _form_ok(rule, csv_data):
    """The rule's last-start checks only (cheap: no notes parsing)."""
    if rule.last_pos is not None and last_start_position(csv_data) != rule.last_pos:
        return False
    if rule.max_margin is not None:
        margin = last_start_margin(csv_data)
        if margin is None or not margin < rule.max_margin:
            return False
    return True


def rules_for(csv_data, notes):
    """Rules a runner fits, in RULES order, given its form data and analyzer notes.

    Same answer as rule_matches() for every rule. The last-start checks run
    first, so the slow notes parser only runs when a rule can still fit.
    """
    if not notes:
        return []
    csv_data = _csv(csv_data)
    candidates = [rule for rule in RULES if _form_ok(rule, csv_data)]
    if not candidates:
        return []
    components = note_components(notes)
    if not components:
        return []
    return [rule for rule in candidates if all(name in components for name in rule.components)]


def matching_rules(horse, rules=None):
    """Active rules this runner fits, in RULES order. Scratched runners fit none.

    rules: the rules allowed to pick (default: active_rules()). The record
    uses rules_for(), which checks every rule, retired ones included.
    """
    prediction = getattr(horse, 'prediction', None)
    if prediction is None or getattr(horse, 'is_scratched', False):
        return []
    allowed = {rule.key for rule in (active_rules() if rules is None else rules)}
    return [rule for rule in rules_for(horse, prediction.notes) if rule.key in allowed]


def has_component(notes, components=None):
    """True when the analyzer notes carry the 2-3kg-below-field component (first rule)."""
    return COMPONENT in note_components(notes)


def qualifies(horse, components=None):
    """First rule only (kept for older callers)."""
    return any(rule.key == ROUTE for rule in matching_rules(horse))


def places_paid(starters):
    """Betfair AU place markets: 3 places for 8+ runners, 2 for 5-7, else 1."""
    if starters >= 8:
        return 3
    if starters >= 5:
        return 2
    return 1


def _profit(item):
    return ((item['place_bsp'] - 1) * (1 - COMMISSION) if item['placed'] else -1.0) * STAKE


def _summary(rows, label):
    bets = len(rows)
    placed = sum(1 for r in rows if r['placed'])
    priced = [r for r in rows if r['place_bsp']]
    profit = sum(_profit(r) for r in priced)
    return {
        'label': label,
        'bets': bets,
        'placed': placed,
        'place_rate': 100.0 * placed / bets if bets else 0.0,
        'priced_bets': len(priced),
        'profit': profit,
        'roi': 100.0 * profit / (len(priced) * STAKE) if priced else None,
    }


_BASE_SQL = """
    SELECT h.id AS horse_id, h.race_id, h.horse_name,
           h.csv_data ->> 'horse last10' AS last10, h.csv_data ->> 'form margin' AS form_margin,
           CASE WHEN h.csv_data ->> 'horse last10' IS NULL AND h.csv_data ->> 'form margin' IS NULL
                THEN h.csv_data END AS csv_fallback,
           p.notes, r.finish_position AS fin,
           r.place_bsp, m.date AS meeting_date, m.meeting_name, rc.race_number, rc.start_time_utc,
           p.best_bet_routes, p.best_bet_qualified_at, s.starters
    FROM horses h
    JOIN (SELECT h2.race_id, COUNT(*) AS starters FROM horses h2
          JOIN results r2 ON r2.horse_id = h2.id WHERE r2.finish_position > 0
          GROUP BY h2.race_id) s ON s.race_id = h.race_id
    JOIN predictions p ON p.horse_id = h.id
    JOIN races rc ON rc.id = h.race_id
    JOIN meetings m ON m.id = rc.meeting_id
    JOIN results r ON r.horse_id = h.id
    WHERE r.finish_position > 0 AND rc.results_incomplete IS NOT TRUE
"""


def _rows(session, sql, params):
    """Query rows; before bsp_import has added results.place_bsp, use no price."""
    try:
        return list(session.execute(text(sql), params).mappings())
    except Exception:
        session.rollback()
        return list(session.execute(text(sql.replace('r.place_bsp', 'NULL AS place_bsp')), params).mappings())


def settled_picks(session, date_from=None, date_to=None):
    """Every settled runner any rule picks: [(rule, item)], oldest first.

    `item` has date, meeting, race, horse, placed, place_bsp and live (True
    when the route was recorded on Best Bets before the jump).
    """
    likes = sorted({frag for rule in RULES for frag in rule.note_like})
    sql = _BASE_SQL + ' AND (' + ' OR '.join(f'LOWER(p.notes) LIKE :like{i}' for i in range(len(likes))) + ')'
    params = {f'like{i}': frag.lower() for i, frag in enumerate(likes)}
    if date_from:
        sql += ' AND m.date >= :date_from'
        params['date_from'] = date_from
    if date_to:
        sql += ' AND m.date <= :date_to'
        params['date_to'] = date_to
    sql += ' ORDER BY m.date, rc.race_number, h.id'
    seen, out = set(), []
    for row in _rows(session, sql, params):
        if row['horse_id'] in seen:
            continue
        seen.add(row['horse_id'])
        # Only the two form fields the rules read are fetched (the whole form
        # row is large). A row stored as a JSON string, not an object, comes
        # back whole in csv_fallback and is read the old way.
        if row['csv_fallback'] is not None:
            csv_data = row['csv_fallback']
        else:
            csv_data = {'horse last10': row['last10'], 'form margin': row['form_margin']}
        rules = rules_for(csv_data, row['notes'])
        if not rules:
            continue
        recorded = set((row['best_bet_routes'] or '').split(','))
        start = row['start_time_utc']
        pre_race = bool(row['best_bet_qualified_at'] and start and row['best_bet_qualified_at'] < start)
        for rule in rules:
            out.append((rule, {
                'date': str(row['meeting_date'])[:10] if row['meeting_date'] else '',
                'meeting': (row['meeting_name'] or '').replace('_', ' '),
                'race': row['race_number'],
                'horse': row['horse_name'],
                'placed': row['fin'] <= places_paid(int(row['starters'] or 0)),
                'place_bsp': row['place_bsp'] if row['place_bsp'] and row['place_bsp'] > 1 else None,
                'live': rule.key in recorded and pre_race,
                'horse_id': row['horse_id'],
                'race_id': row['race_id'],
                'start': start,
            }))
    started = time.time()
    _add_ladbrokes_place_prices(session, [item for _rule, item in out])
    logger.info("PLACE_RULES_TIMING ladbrokes_place_prices seconds=%.2f", time.time() - started)
    return out


def ladbrokes_place_prices(session, runners, column='place_odds'):
    """{horse_id: last Ladbrokes fixed place price saved before the jump}.

    `runners` are dicts with race_id, horse_id and start (start_time_utc or
    None). Runners with no saved place price are left out. column='odds'
    gives the fixed win price instead.
    """
    if column not in ('place_odds', 'odds'):
        raise ValueError(column)
    runners = list(runners)
    if not runners:
        return {}
    # Look up by race first: the snapshot index starts with race_id, so a
    # horse_id-only filter would read the whole odds history.
    pairs = sorted({(r['race_id'], r['horse_id']) for r in runners})
    snaps = {}
    try:
        for first in range(0, len(pairs), 500):
            chunk = pairs[first:first + 500]
            params = {}
            for i, (race_id, horse_id) in enumerate(chunk):
                params[f'r{i}'] = race_id
                params[f'h{i}'] = horse_id
            races = ', '.join(f':r{i}' for i in range(len(chunk)))
            horses = ', '.join(f':h{i}' for i in range(len(chunk)))
            rows = session.execute(text(
                f'SELECT horse_id, {column} AS price, captured_at FROM live_odds_snapshots '
                f'WHERE race_id IN ({races}) AND horse_id IN ({horses}) AND {column} > 1'),
                params).mappings()
            for row in rows:
                snaps.setdefault(row['horse_id'], []).append((row['captured_at'], row['price']))
    except Exception:
        # No odds table (e.g. a fresh database): no stand-in prices.
        session.rollback()
        return {}
    # Sorted here, not in SQL: ORDER BY captured_at let Postgres walk the
    # captured_at index across the whole odds history (minutes per load).
    for found in snaps.values():
        found.sort(key=lambda s: (s[0] is None, s[0]))
    prices = {}
    for r in runners:
        found = snaps.get(r['horse_id'], [])
        if r.get('start'):
            found = [s for s in found if s[0] and s[0] <= r['start']]
        if found:
            prices[r['horse_id']] = float(found[-1][1])
    return prices


def _add_ladbrokes_place_prices(session, items):
    """Set item['ladbrokes_place'] on placed bets that have no Betfair place price yet.

    The chart uses it as a stand-in until the monthly Betfair file arrives.
    """
    for item in items:
        item['ladbrokes_place'] = None
    wanted = [item for item in items if item['placed'] and not item['place_bsp']]
    prices = ladbrokes_place_prices(session, wanted)
    for item in wanted:
        item['ladbrokes_place'] = prices.get(item['horse_id'])


def _chart_profit(item):
    """(profit, estimated) for one chart bet, or None while its place price is unknown.

    A losing bet costs the stake whatever the price, so it counts straight away.
    A placed bet uses the Betfair place price; until that arrives, the Ladbrokes
    fixed place price stands in (no commission) and is marked as an estimate.
    """
    if item['place_bsp']:
        return _profit(item), False
    if not item['placed']:
        return -STAKE, False
    if item.get('ladbrokes_place'):
        return (item['ladbrokes_place'] - 1) * STAKE, True
    return None


def _bsp_through(session):
    try:
        return session.execute(text(
            "SELECT MAX(m.date) FROM results r JOIN horses h ON h.id = r.horse_id JOIN races rc ON rc.id = h.race_id "
            "JOIN meetings m ON m.id = rc.meeting_id WHERE r.place_bsp IS NOT NULL")).scalar()
    except Exception:
        session.rollback()
        return None


_CACHE = {}
_CACHE_SECONDS = 600
# Shared by every web worker on the machine (and kept when a worker restarts),
# so one slow rebuild serves both pages and both workers for 10 minutes.
_CACHE_DIR = os.path.join('/dev/shm' if os.path.isdir('/dev/shm') else tempfile.gettempdir(),
                          'formanalyst_place_rules')


def _cache_file(cache_key):
    name = hashlib.sha1(repr(cache_key).encode()).hexdigest()
    return os.path.join(_CACHE_DIR, name + '.pickle')


def _cache_get(cache_key):
    hit = _CACHE.get(cache_key)
    if hit and time.time() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    try:
        with open(_cache_file(cache_key), 'rb') as f:
            saved_at, result = pickle.load(f)
    except Exception:
        return None
    if time.time() - saved_at >= _CACHE_SECONDS:
        return None
    _CACHE[cache_key] = (saved_at, result)
    return result


def _cache_put(cache_key, result):
    now = time.time()
    _CACHE[cache_key] = (now, result)
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        path = _cache_file(cache_key)
        tmp = f'{path}.{os.getpid()}.tmp'
        with open(tmp, 'wb') as f:
            pickle.dump((now, result), f)
        os.replace(tmp, path)
    except Exception as e:
        logger.info("Place rules cache file not written: %s", e)


def performance_all(session, date_from=None, date_to=None):
    """Per-rule record (history + live), per-month totals and chart series.

    Cached for 10 minutes: it reads every settled runner the rules pick.
    """
    cache_key = (str(date_from or ''), str(date_to or ''))
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit
    started = time.time()
    picks = settled_picks(session, date_from, date_to)
    logger.info("PLACE_RULES_TIMING settled_picks seconds=%.2f picks=%d", time.time() - started, len(picks))
    by_rule = defaultdict(list)
    for rule, item in picks:
        by_rule[rule.key].append(item)
    rules_out = []
    for rule in RULES:
        items = by_rule.get(rule.key, [])
        history = _summary(items, 'Every past runner that fits')
        live = _summary([i for i in items if i['live']], f'Live, on Best Bets before the jump (from {LIVE_START:%d %b %Y})')
        # The chart shows only live bets: picks on Best Bets before the jump,
        # from LIVE_START, the day real money went on.
        cumulative, series, waiting = 0.0, [], 0
        for item in items:
            if not item['live'] or item['date'] < LIVE_START.strftime('%Y-%m-%d'):
                continue
            settled = _chart_profit(item)
            if settled is None:
                waiting += 1
                continue
            profit, estimated = settled
            cumulative += profit
            series.append({'date': item['date'], 'race': item['race'], 'meeting': item['meeting'],
                           'horse': item['horse'], 'won': item['placed'], 'profit': round(profit, 2),
                           'cumulative': round(cumulative, 2), 'estimate': estimated})
        # Live profit and ROI match the chart: Betfair place price where loaded,
        # else the Ladbrokes stand-in. Otherwise live ROI stays blank for a month.
        live['priced_bets'] = len(series)
        live['profit'] = cumulative
        live['roi'] = 100.0 * cumulative / (len(series) * STAKE) if series else None
        live['estimate'] = any(p['estimate'] for p in series)
        months = defaultdict(list)
        for item in items:
            months[item['date'][:7]].append(item)
        rules_out.append({
            'key': rule.key, 'name': rule.name, 'short': rule.short, 'audit': rule.audit,
            'retired': rule.retired,
            'history': history, 'live': live,
            'months': [_summary(v, k) for k, v in sorted(months.items(), reverse=True) if k],
            'chart': {'label': rule.short, 'history': series, 'waiting': waiting},
        })
    bsp_through = _bsp_through(session)
    result = {
        'rules': rules_out,
        'place_bsp_through': str(bsp_through) if bsp_through else None,
        'stake': STAKE,
        'commission_pct': COMMISSION * 100,
    }
    _cache_put(cache_key, result)
    return result


def clear_cache():
    _CACHE.clear()
    try:
        for name in os.listdir(_CACHE_DIR):
            os.remove(os.path.join(_CACHE_DIR, name))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Saved record. Working the record out reads every settled runner and takes
# minutes, so no page does it. The results settler (every 30 minutes on race
# days) saves a fresh copy; the pages show the saved copy at once. If the copy
# is old, the page starts a refresh in a separate process and still shows the
# old copy, so nobody waits.
# ---------------------------------------------------------------------------
_STORE_TABLE = 'place_rules_record'
STALE_SECONDS = 45 * 60
_REFRESH_LOCK = 742001  # Postgres advisory lock: one refresh at a time
_last_spawn = [0.0]


def _json_default(value):
    from decimal import Decimal
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _ensure_store(session):
    session.execute(text(
        f'CREATE TABLE IF NOT EXISTS {_STORE_TABLE} '
        f'(id INTEGER PRIMARY KEY, computed_at FLOAT NOT NULL, payload TEXT NOT NULL)'))


def save_record(session, result):
    _ensure_store(session)
    session.execute(text(f'DELETE FROM {_STORE_TABLE} WHERE id = 1'))
    session.execute(text(f'INSERT INTO {_STORE_TABLE} (id, computed_at, payload) VALUES (1, :at, :payload)'),
                    {'at': time.time(), 'payload': json.dumps(result, default=_json_default)})
    session.commit()


def stored_record(session):
    """(saved_at_epoch_seconds, record) or None when nothing is saved yet."""
    try:
        row = session.execute(text(f'SELECT computed_at, payload FROM {_STORE_TABLE} WHERE id = 1')).first()
    except Exception:
        session.rollback()  # no table yet
        return None
    if not row:
        return None
    return float(row[0]), json.loads(row[1])


def refresh_stored(session):
    """Work the full record out and save it. Skips if another refresh is running (Postgres)."""
    is_pg = session.get_bind().dialect.name == 'postgresql'
    if is_pg and not session.execute(text('SELECT pg_try_advisory_lock(:k)'), {'k': _REFRESH_LOCK}).scalar():
        logger.info("Place rules refresh already running elsewhere; skipped")
        return None
    try:
        started = time.time()
        clear_cache()
        result = performance_all(session)
        save_record(session, result)
        logger.info("PLACE_RULES_TIMING saved_refresh seconds=%.2f", time.time() - started)
        return result
    finally:
        if is_pg:
            session.rollback()
            session.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': _REFRESH_LOCK})
            session.commit()


def _start_background_refresh():
    """Run `python place_bet.py --refresh` in its own process (never inside a page request)."""
    if time.time() - _last_spawn[0] < 10 * 60:
        return
    _last_spawn[0] = time.time()
    import subprocess
    import sys
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), '--refresh'],
                         cwd=os.path.dirname(os.path.abspath(__file__)),
                         start_new_session=True, stdin=subprocess.DEVNULL)
        logger.info("Place rules refresh started in the background")
    except Exception as e:
        logger.warning("Could not start place rules refresh: %s", e)


def record_for_page(session, date_from=None, date_to=None):
    """Record for a page: (record or None, saved_at or None). Never slow for the default view.

    A date-filtered view is worked out on the spot (it is rare and admin-chosen).
    """
    if date_from or date_to:
        return performance_all(session, date_from=date_from, date_to=date_to), None
    saved = stored_record(session)
    if saved is None or time.time() - saved[0] > STALE_SECONDS:
        _start_background_refresh()
    if saved is None:
        return None, None
    return saved[1], saved[0]


def age_minutes(saved_at):
    return None if saved_at is None else max(0, int((time.time() - saved_at) // 60))


if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    parser = argparse.ArgumentParser(description='Save the place rules record.')
    parser.add_argument('--refresh', action='store_true', required=True)
    parser.parse_args()
    from sqlalchemy.orm import Session
    from bsp_import import get_engine
    with Session(get_engine()) as s:
        refresh_stored(s)


# ---------------------------------------------------------------------------
# "Hot combo" tag: shown on Best Bets as information only, never a reason to
# pick. Jockey + trainer pairs winning 33%+ of 10+ runs in the last 90 days
# (results before today only). Tested Feb-Oct 2026 (audit/tasks/trending.py):
# as a WIN bet no better than luck; as a PLACE bet +8.4% on 732 bets but up in
# only 3 of 8 months. Not proven, so it never adds or removes a pick.
# ---------------------------------------------------------------------------
HOT_COMBO_DAYS = 90
HOT_COMBO_MIN_RUNS = 10
HOT_COMBO_MIN_STRIKE = 0.33


def _combo_key(jockey, trainer):
    return (str(jockey or '').strip().lower(), str(trainer or '').strip().lower())


def hot_combos(session, pairs, today):
    """{(jockey, trainer): (wins, runs)} for the given pairs that are hot."""
    from datetime import timedelta
    from sqlalchemy import bindparam
    jockeys = sorted({j for j, t in pairs if j and t})
    if not jockeys:
        return {}
    try:
        rows = session.execute(text("""
            SELECT LOWER(TRIM(h.jockey)) AS j, LOWER(TRIM(h.trainer)) AS t, COUNT(*) AS n,
                   SUM(CASE WHEN r.finish_position = 1 THEN 1 ELSE 0 END) AS w
            FROM horses h
            JOIN results r ON r.horse_id = h.id
            JOIN races rc ON rc.id = h.race_id
            JOIN meetings m ON m.id = rc.meeting_id
            WHERE r.finish_position > 0 AND m.date >= :start AND m.date < :today
              AND LOWER(TRIM(h.jockey)) IN :jockeys
            GROUP BY 1, 2
        """).bindparams(bindparam('jockeys', expanding=True)),
            {'start': today - timedelta(days=HOT_COMBO_DAYS), 'today': today, 'jockeys': jockeys}).fetchall()
    except Exception as e:
        session.rollback()
        logger.warning("Hot combo lookup failed: %s", e)
        return {}
    wanted = set(pairs)
    out = {}
    for j, t, n, w in rows:
        n, w = int(n or 0), int(w or 0)
        if (j, t) in wanted and n >= HOT_COMBO_MIN_RUNS and w / n >= HOT_COMBO_MIN_STRIKE:
            out[(j, t)] = (w, n)
    return out
