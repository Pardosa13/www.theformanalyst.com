"""Place Bet route: ran 2nd last start AND carries 2-3kg less than the field average.

Found by the wide edge hunt (audit/tasks/hunt2.py, 27 Oct 2026): the only
pattern of ~48,000 place-bet patterns that held up on months it never saw.
Search Feb-Jun: 337 bets, +27% at Betfair place SP. Unseen Jul-Sep: 202 bets,
+52%, passing the luck test. It is a PLACE bet, so it is settled on the place
(Betfair place BSP after commission), never as a win bet.

The rule uses only pre-race data: the CSV form file's last-start position and
the analyzer's weight-vs-field component in the prediction notes.
"""
import json
from collections import defaultdict
from datetime import datetime

from sqlalchemy import text

from notes_parsing import parse_notes_components

ROUTE = 'place_2nd_light'
BADGE = '🥉 Place Bet: 2nd last start, 2-3kg under the field'
REASON = ('Back for a PLACE, not the win: ran 2nd last start and carries 2-3kg less than the '
          'field average. Feb-Sep test: +52% at Betfair place SP on 202 unseen bets.')
COMPONENT = 'Weight vs Field - Below (2-3kg)'
LIVE_START = datetime(2026, 10, 28)
COMMISSION = 0.08
STAKE = 10.0


def _csv(horse_or_value):
    value = getattr(horse_or_value, 'csv_data', horse_or_value)
    while isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def last_start_position(horse_or_csv):
    """Finish position at the horse's most recent start.

    Read from the 'horse last10' form string, whose right-most digit is the
    latest run ('x' marks a spell, '0' is 10th or worse). The saved
    'form position' field held an older run for many horses (fixed 28 Oct
    2026), so it is only a fallback.
    """
    csv_data = _csv(horse_or_csv)
    last10 = str(csv_data.get('horse last10') or getattr(horse_or_csv, 'form', '') or '').strip().lower().rstrip('x')
    if last10:
        ch = last10[-1]
        if ch.isdigit():
            return 10 if ch == '0' else int(ch)
        return None
    try:
        return int(float(csv_data.get('form position')))
    except (TypeError, ValueError):
        return None


def has_component(notes, components=None):
    """True when the analyzer notes carry the 2-3kg-below-field component.

    `components` may be the dict from app.parse_notes_component_matches (keys
    are display names, values carry 'legacy_name'), to avoid parsing twice.
    """
    if components:
        for name, match in components.items():
            if COMPONENT in (name, (match or {}).get('legacy_name')):
                return True
        return False
    if not notes:
        return False
    try:
        return COMPONENT in parse_notes_components(notes)
    except Exception:
        return False


def qualifies(horse, components=None):
    prediction = getattr(horse, 'prediction', None)
    if prediction is None or getattr(horse, 'is_scratched', False):
        return False
    return last_start_position(horse) == 2 and has_component(prediction.notes, components)


def places_paid(starters):
    """Betfair AU place markets: 3 places for 8+ runners, 2 for 5-7, else 1."""
    if starters >= 8:
        return 3
    if starters >= 5:
        return 2
    return 1


def _summary(rows, label):
    bets = len(rows)
    placed = sum(1 for r in rows if r['placed'])
    priced = [r for r in rows if r['place_bsp']]
    profit = sum(((r['place_bsp'] - 1) * (1 - COMMISSION) if r['placed'] else -1.0) * STAKE for r in priced)
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
    SELECT h.id AS horse_id, h.race_id, h.csv_data, p.notes, r.finish_position AS fin,
           r.place_bsp, m.date AS meeting_date, rc.start_time_utc,
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
      AND LOWER(p.notes) LIKE '%below race avg%'
"""


def _rows(session, sql, params):
    """Query rows; before bsp_import has added results.place_bsp, use no price."""
    try:
        return list(session.execute(text(sql), params).mappings())
    except Exception:
        session.rollback()
        return list(session.execute(text(sql.replace('r.place_bsp', 'NULL AS place_bsp')), params).mappings())


def performance(session, date_from=None, date_to=None):
    """History (every past runner the rule picks) and live (recorded pre-race) results."""
    sql = _BASE_SQL
    params = {}
    if date_from:
        sql += ' AND m.date >= :date_from'
        params['date_from'] = date_from
    if date_to:
        sql += ' AND m.date <= :date_to'
        params['date_to'] = date_to
    seen, history, live = set(), [], []
    for row in _rows(session, sql, params):
        if row['horse_id'] in seen:
            continue
        seen.add(row['horse_id'])
        if last_start_position(row['csv_data']) != 2 or not has_component(row['notes']):
            continue
        item = {
            'date': str(row['meeting_date'])[:10] if row['meeting_date'] else '',
            'placed': row['fin'] <= places_paid(int(row['starters'] or 0)),
            'place_bsp': row['place_bsp'] if row['place_bsp'] and row['place_bsp'] > 1 else None,
        }
        history.append(item)
        recorded = ROUTE in (row['best_bet_routes'] or '')
        start = row['start_time_utc']
        if recorded and row['best_bet_qualified_at'] and start and row['best_bet_qualified_at'] < start:
            live.append(item)
    by_month = defaultdict(list)
    for item in history:
        by_month[item['date'][:7]].append(item)
    try:
        bsp_through = session.execute(text(
            "SELECT MAX(m.date) FROM results r JOIN horses h ON h.id = r.horse_id JOIN races rc ON rc.id = h.race_id "
            "JOIN meetings m ON m.id = rc.meeting_id WHERE r.place_bsp IS NOT NULL")).scalar()
    except Exception:
        session.rollback()
        bsp_through = None
    return {
        'rows': [_summary(history, 'Every past runner that fits the rule'),
                 _summary(live, f'Live: shown on Best Bets before the jump (from {LIVE_START:%d %b %Y})')],
        'months': [_summary(v, k) for k, v in sorted(by_month.items(), reverse=True) if k],
        'place_bsp_through': str(bsp_through) if bsp_through else None,
        'stake': STAKE,
        'commission_pct': COMMISSION * 100,
    }
