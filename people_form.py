"""Jockeys, trainers and jockey + trainer pairs: who is in form right now.

Read from this site's own saved results over the last N days: runs, wins,
strike rate, top-3 rate and the return on a $10 win bet at the starting
price. PuntingForm's own last-100 record is shown next to each jockey and
trainer (PuntingForm has no return figure and nothing on pairs).

Also says who has runners in the meetings still to be run, so the page works
as a starting point for a meeting. A guide to form, not a betting system:
the audit found jockeys and trainers that looked good in one half of the
year did worse than average in the next (see review_list.py).
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import text

logger = logging.getLogger(__name__)

STAKE = 10.0
WINDOWS = (14, 30, 60, 90, 365)
# Fewest runs in the window before a name is listed.
MIN_RUNS = {'jockeys': 10, 'trainers': 8, 'combos': 4}
MAX_ROWS = 400

# One row per runner that ran: duplicate horse rows (same race, same name)
# are collapsed to the newest, as the audit does.
_RESULTS_SQL = """
    SELECT LOWER(TRIM(h.jockey)) AS j, LOWER(TRIM(h.trainer)) AS t,
           MAX(TRIM(h.jockey)) AS jockey, MAX(TRIM(h.trainer)) AS trainer,
           COUNT(*) AS runs,
           SUM(CASE WHEN r.finish_position = 1 THEN 1 ELSE 0 END) AS wins,
           SUM(CASE WHEN r.finish_position BETWEEN 1 AND 3 THEN 1 ELSE 0 END) AS places,
           SUM(CASE WHEN r.finish_position = 1 THEN COALESCE(r.sp, 0) ELSE 0 END) AS returned,
           MAX(m.date) AS last_run
    FROM horses h
    JOIN (SELECT MAX(h2.id) AS id FROM horses h2
          JOIN results r2 ON r2.horse_id = h2.id
          WHERE r2.finish_position > 0
          GROUP BY h2.race_id, LOWER(TRIM(h2.horse_name))) one ON one.id = h.id
    JOIN results r ON r.horse_id = h.id
    JOIN races rc ON rc.id = h.race_id
    JOIN meetings m ON m.id = rc.meeting_id
    WHERE r.finish_position > 0 AND rc.results_incomplete IS NOT TRUE
      AND m.date >= :start AND m.date <= :today
    GROUP BY 1, 2
"""

_UPCOMING_SQL = """
    SELECT LOWER(TRIM(h.jockey)) AS j, LOWER(TRIM(h.trainer)) AS t, m.meeting_name AS meeting, COUNT(*) AS runners
    FROM horses h
    JOIN races rc ON rc.id = h.race_id
    JOIN meetings m ON m.id = rc.meeting_id
    LEFT JOIN results r ON r.horse_id = h.id
    WHERE m.date >= :today AND r.id IS NULL AND h.is_scratched IS NOT TRUE
    GROUP BY 1, 2, 3
"""


def local_today():
    """Today's date on the Australian east coast, where the meetings are dated."""
    return (datetime.utcnow() + timedelta(hours=10)).date()


def _new():
    return {'runs': 0, 'wins': 0, 'places': 0, 'returned': 0.0, 'last_run': None, 'upcoming': 0, 'tracks': set()}


def _add(stat, row):
    stat['runs'] += int(row['runs'] or 0)
    stat['wins'] += int(row['wins'] or 0)
    stat['places'] += int(row['places'] or 0)
    stat['returned'] += float(row['returned'] or 0)
    last = str(row['last_run'])[:10] if row['last_run'] else None
    if last and (stat['last_run'] is None or last > stat['last_run']):
        stat['last_run'] = last


def _finish(name, stat, extra=None):
    runs = stat['runs']
    profit = (stat['returned'] - runs) * STAKE
    out = {
        'name': name,
        'runs': runs,
        'wins': stat['wins'],
        'strike_rate': round(100.0 * stat['wins'] / runs, 1) if runs else 0.0,
        'places': stat['places'],
        'place_rate': round(100.0 * stat['places'] / runs, 1) if runs else 0.0,
        'profit': round(profit, 2),
        'roi': round(100.0 * profit / (runs * STAKE), 1) if runs else 0.0,
        'last_run': stat['last_run'],
        'upcoming': stat['upcoming'],
        'tracks': ', '.join(sorted(stat['tracks'])),
    }
    out.update(extra or {})
    return out


def _puntingform(session):
    """{(type, normalised name): last-100 record} from the saved PuntingForm strike rates."""
    try:
        rows = session.execute(text(
            "SELECT type, normalised_name, l100_wins, l100_runs, last100_actual_to_expected AS ae "
            "FROM strike_rates WHERE l100_runs > 0")).mappings()
        best = {}
        for row in rows:
            key = (row['type'], row['normalised_name'])
            if key not in best or (row['l100_runs'] or 0) > (best[key]['l100_runs'] or 0):
                best[key] = dict(row)
        return best
    except Exception as e:
        session.rollback()
        logger.warning("PuntingForm strike rates unavailable: %s", e)
        return {}


def _pf_fields(pf, kind, key):
    row = pf.get((kind, key))
    if not row:
        return {'pf_l100': None, 'pf_sr': None, 'pf_ae': None}
    wins, runs = int(row['l100_wins'] or 0), int(row['l100_runs'] or 0)
    return {
        'pf_l100': f'{wins}/{runs}',
        'pf_sr': round(100.0 * wins / runs, 1) if runs else None,
        'pf_ae': round(float(row['ae']), 2) if row['ae'] is not None else None,
    }


def form(session, days=30, today=None):
    """{'jockeys': [...], 'trainers': [...], 'combos': [...]} for the last `days` days."""
    days = days if days in WINDOWS else 30
    today = today or local_today()
    params = {'start': today - timedelta(days=days), 'today': today}

    from strike_rate_matching import normalize_name
    jockeys, trainers, combos, names = {}, {}, {}, {}

    def key_for(kind, raw):
        """Same person whatever the spelling: 'J Smith (a2)' and 'J Smith' are one."""
        raw = (raw or '').strip()
        key = normalize_name(raw)
        if key:
            shown = names.get((kind, key))
            if shown is None or len(raw) < len(shown):
                names[(kind, key)] = raw
        return key

    for row in session.execute(text(_RESULTS_SQL), params).mappings():
        j, t = key_for('j', row['jockey']), key_for('t', row['trainer'])
        if j:
            _add(jockeys.setdefault(j, _new()), row)
        if t:
            _add(trainers.setdefault(t, _new()), row)
        if j and t:
            _add(combos.setdefault((j, t), _new()), row)

    for row in session.execute(text(_UPCOMING_SQL), {'today': today}).mappings():
        j, t = normalize_name(row['j']), normalize_name(row['t'])
        track = (row['meeting'] or '').replace('_', ' ')
        for stat in (jockeys.get(j), trainers.get(t), combos.get((j, t))):
            if stat is not None:
                stat['upcoming'] += int(row['runners'] or 0)
                if track:
                    stat['tracks'].add(track)

    pf = _puntingform(session)

    def table(kind, rows):
        rows = [r for r in rows if r['runs'] >= MIN_RUNS[kind]]
        rows.sort(key=lambda r: (-r['strike_rate'], -r['runs'], r['name']))
        return rows[:MAX_ROWS]

    return {
        'days': days,
        'windows': list(WINDOWS),
        'from': str(params['start']),
        'to': str(today),
        'min_runs': MIN_RUNS,
        'jockeys': table('jockeys', [
            _finish(names[('j', j)], s, _pf_fields(pf, 'jockey', j)) for j, s in jockeys.items()]),
        'trainers': table('trainers', [
            _finish(names[('t', t)], s, _pf_fields(pf, 'trainer', t)) for t, s in trainers.items()]),
        'combos': table('combos', [
            _finish(f"{names[('j', j)]} + {names[('t', t)]}", s) for (j, t), s in combos.items()]),
    }
