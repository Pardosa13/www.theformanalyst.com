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
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text

from notes_parsing import parse_notes_components

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


RULES = (
    Rule('place_2nd_light', 'Ran 2nd last start + carries 2-3kg less than the field average',
         '2nd last start, 2-3kg light', ('Weight vs Field - Below (2-3kg)',), last_pos=2,
         note_like=('%below race avg%',),
         audit='533 bets, +35%, up in 7 of 8 months'),
    Rule('place_close_trackpod', 'Finished within 1 length last start + placed in 55-69% of runs at this track',
         'Within 1L last start, track placer', ('Track Podium Rate - Strong (55-69%)',), max_margin=1.0,
         note_like=('%Strong podium rate%at this track%',),
         audit='804 bets, +21%, up in 6 of 8 months'),
    Rule('place_32_tdnowin', 'Last-5 placings start 3rd then 2nd + has run at this track and distance without winning',
         '3rd-2nd, no track+dist win', ('Ran places: 3rd 2nd', 'Track+Distance Win Rate - No Wins'),
         note_like=('%Ran places: 3rd 2nd%',),
         audit='1,111 bets, +19%, up in 6 of 8 months'),
    Rule('place_32_poortrack', 'Last-5 placings start 3rd then 2nd + poor record at this track',
         '3rd-2nd, poor at track', ('Ran places: 3rd 2nd', 'Track - Poor Performance'),
         note_like=('%Ran places: 3rd 2nd%',),
         audit='731 bets, +26%, up in 6 of 8 months'),
    Rule('place_goingpod_backsprint', 'Placed in 55-69% of runs on today\'s going + backmarker in a sprint (1200m or less)',
         'Going placer, backmarker sprint', ('Condition Podium Rate - Strong (55-69%)', 'Running Position - Backmarker Sprint'),
         note_like=('%BACKMARKER in Sprint%',),
         audit='690 bets, +26%, up in 6 of 8 months'),
    Rule('place_won_onpace_mile', 'Won last start + on pace in a mile race (1300-1700m)',
         'Won last start, on pace mile', ('Running Position - OnPace Mile',), last_pos=1,
         note_like=('%ONPACE in Mile%',),
         audit='655 bets, +24%, up in 6 of 8 months (smaller lately)'),
)
RULES_BY_KEY = {rule.key: rule for rule in RULES}
ROUTE_KEYS = tuple(rule.key for rule in RULES)

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


def matching_rules(horse):
    """Rules this runner fits, in RULES order. Scratched runners fit none."""
    prediction = getattr(horse, 'prediction', None)
    if prediction is None or getattr(horse, 'is_scratched', False):
        return []
    components = note_components(prediction.notes)
    if not components:
        return []
    return [rule for rule in RULES if rule_matches(rule, horse, components)]


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
    SELECT h.id AS horse_id, h.race_id, h.horse_name, h.csv_data, p.notes, r.finish_position AS fin,
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
        components = note_components(row['notes'])
        rules = [rule for rule in RULES if rule_matches(rule, row['csv_data'], components)]
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
                'start': start,
            }))
    _add_ladbrokes_place_prices(session, [item for _rule, item in out])
    return out


def _add_ladbrokes_place_prices(session, items):
    """Set item['ladbrokes_place'] on placed bets that have no Betfair place price yet.

    It is the last Ladbrokes fixed place price saved before the jump. The chart
    uses it as a stand-in until the monthly Betfair file arrives.
    """
    wanted = {item['horse_id'] for item in items if item['placed'] and not item['place_bsp']}
    for item in items:
        item['ladbrokes_place'] = None
    if not wanted:
        return
    ids = sorted(wanted)
    prices = {}
    try:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            names = ', '.join(f':h{i}' for i in range(len(chunk)))
            rows = session.execute(text(
                f'SELECT horse_id, place_odds, captured_at FROM live_odds_snapshots '
                f'WHERE place_odds > 1 AND horse_id IN ({names}) ORDER BY captured_at'),
                {f'h{i}': h for i, h in enumerate(chunk)}).mappings()
            for row in rows:
                prices.setdefault(row['horse_id'], []).append((row['captured_at'], row['place_odds']))
    except Exception:
        # No odds table (e.g. a fresh database): no stand-in prices.
        session.rollback()
        return
    for item in items:
        if item['horse_id'] not in wanted:
            continue
        snaps = prices.get(item['horse_id'], [])
        if item['start']:
            snaps = [s for s in snaps if s[0] and s[0] <= item['start']]
        if snaps:
            item['ladbrokes_place'] = float(snaps[-1][1])


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


def performance_all(session, date_from=None, date_to=None):
    """Per-rule record (history + live), per-month totals and chart series.

    Cached for 10 minutes: it reads every settled runner the rules pick.
    """
    cache_key = (str(date_from or ''), str(date_to or ''))
    hit = _CACHE.get(cache_key)
    if hit and time.time() - hit[0] < _CACHE_SECONDS:
        return hit[1]
    picks = settled_picks(session, date_from, date_to)
    by_rule = defaultdict(list)
    for rule, item in picks:
        by_rule[rule.key].append(item)
    rules_out = []
    for rule in RULES:
        items = by_rule.get(rule.key, [])
        history = _summary(items, 'Every past runner that fits')
        live = _summary([i for i in items if i['live']], f'Live, on Best Bets before the jump (from {LIVE_START:%d %b %Y})')
        cumulative, series, waiting = 0.0, [], 0
        for item in items:
            settled = _chart_profit(item)
            if settled is None:
                waiting += 1
                continue
            profit, estimated = settled
            cumulative += profit
            series.append({'date': item['date'], 'race': item['race'], 'meeting': item['meeting'],
                           'horse': item['horse'], 'won': item['placed'], 'profit': round(profit, 2),
                           'cumulative': round(cumulative, 2), 'estimate': estimated})
        months = defaultdict(list)
        for item in items:
            months[item['date'][:7]].append(item)
        rules_out.append({
            'key': rule.key, 'name': rule.name, 'short': rule.short, 'audit': rule.audit,
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
    _CACHE[cache_key] = (time.time(), result)
    return result


def clear_cache():
    _CACHE.clear()
