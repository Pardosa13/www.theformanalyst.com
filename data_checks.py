"""Daily data checks: catch bad saved horse data the day it happens.

Why: from 16 Oct to 28 Oct 2026 the scratchings refresh saved an older run as
each horse's "last start" for most horses (11-65% right a day). The ML scored on
those wrong inputs and Best Bets picked the wrong Place Bets, and nobody
noticed for weeks. These checks run on every daily_run pass, save the
result, show a warning on ML Data and Best Bets, and send one Telegram alert
a day while a check is failing.

Checks, on every runner in the day's meetings (not scratched):
  last_start_position  saved 'form position' = right-most digit of the
                       last10 form string (the latest run; 'x' is a spell,
                       '0' is 10th or worse)
  last_start_date      saved 'form meeting date' = the newest past start for
                       that horse in the meeting's own form file
"""
import csv
import io
import json
import logging
from datetime import datetime

from sqlalchemy import text

log = logging.getLogger(__name__)

PASS_PCT = 95.0       # before the bug both sat at 97-100%
MIN_CHECKED = 20      # too few runners to judge below this
EXAMPLES = 8


def _csv(value):
    while isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _norm(name):
    return ' '.join(str(name or '').lower().replace("'", '').split())


def form_last_position(last10):
    """Latest finish from the last10 string, or None. 10 means '0' (10th+)."""
    s = str(last10 or '').strip().lower().rstrip('x')
    if not s or not s[-1].isdigit():
        return None
    return 10 if s[-1] == '0' else int(s[-1])


def _position(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _latest_dates(meeting_csv):
    """{(race number, horse name): newest 'form meeting date'} from a meeting's form file."""
    from past_runs import parse_date
    out = {}
    if not meeting_csv:
        return out
    try:
        rows = csv.DictReader(io.StringIO(meeting_csv))
        for row in rows:
            key = (str(row.get('race number', '')).strip(), _norm(row.get('horse name')))
            d = parse_date(row.get('form meeting date'))
            if d and (key not in out or d > out[key]):
                out[key] = d
    except Exception as e:  # a bad file must not stop the run
        log.warning('data_checks: could not read meeting form file: %s', e)
    return out


def check_day(session, day):
    """Run the checks on one day's meetings. Returns {check: result dict}."""
    from past_runs import parse_date
    meetings = session.execute(text(
        "SELECT id, meeting_name, csv_data FROM meetings WHERE date = :d"), {'d': day}).fetchall()
    results = {
        'last_start_position': {'checked': 0, 'failed': 0, 'examples': []},
        'last_start_date': {'checked': 0, 'failed': 0, 'examples': []},
    }
    for meeting_id, meeting_name, meeting_csv in meetings:
        latest = _latest_dates(meeting_csv)
        horses = session.execute(text("""
            SELECT h.horse_name, h.form, h.csv_data, rc.race_number
            FROM horses h JOIN races rc ON rc.id = h.race_id
            WHERE rc.meeting_id = :m AND COALESCE(h.is_scratched, FALSE) = FALSE
        """), {'m': meeting_id}).fetchall()
        for name, form, csv_data, race_number in horses:
            cd = _csv(csv_data)
            label = f'{meeting_name} R{race_number} {name}'
            want = form_last_position(cd.get('horse last10') or form)
            have = _position(cd.get('form position'))
            if want is not None and have is not None:
                r = results['last_start_position']
                r['checked'] += 1
                if not (have == want or (want == 10 and have >= 10)):
                    r['failed'] += 1
                    if len(r['examples']) < EXAMPLES:
                        r['examples'].append(f'{label}: saved {have}, form says {want} ({form})')
            newest = latest.get((str(race_number), _norm(name)))
            saved = parse_date(cd.get('form meeting date'))
            if newest and saved:
                r = results['last_start_date']
                r['checked'] += 1
                if saved != newest:
                    r['failed'] += 1
                    if len(r['examples']) < EXAMPLES:
                        r['examples'].append(f'{label}: saved {saved}, newest run {newest}')
    for r in results.values():
        r['pct_ok'] = round(100.0 * (r['checked'] - r['failed']) / r['checked'], 1) if r['checked'] else None
        r['ok'] = r['checked'] < MIN_CHECKED or r['pct_ok'] >= PASS_PCT
    return results


def ensure_table(session):
    session.execute(text("""
        CREATE TABLE IF NOT EXISTS data_check_runs (
            id SERIAL PRIMARY KEY,
            check_day DATE NOT NULL,
            check_name VARCHAR(50) NOT NULL,
            checked INTEGER NOT NULL,
            failed INTEGER NOT NULL,
            pct_ok FLOAT,
            ok BOOLEAN NOT NULL,
            examples_json TEXT,
            alerted BOOLEAN NOT NULL DEFAULT FALSE,
            updated_at TIMESTAMP NOT NULL,
            UNIQUE (check_day, check_name)
        )
    """))


def run_and_save(session, day, alert=None):
    """Check `day`, save one row per check (latest pass wins), alert once a day on failure."""
    ensure_table(session)
    results = check_day(session, day)
    for name, r in results.items():
        prev = session.execute(text(
            "SELECT alerted FROM data_check_runs WHERE check_day = :d AND check_name = :n"),
            {'d': day, 'n': name}).scalar()
        send = (not r['ok']) and not prev and alert is not None
        if send:
            alert(f"DATA CHECK FAILED ({name}) on {day}: only {r['pct_ok']}% of {r['checked']} runners "
                  f"correct. e.g. {r['examples'][0] if r['examples'] else ''}")
        params = {'d': day, 'n': name, 'c': r['checked'], 'f': r['failed'], 'p': r['pct_ok'], 'ok': r['ok'],
                  'e': json.dumps(r['examples']), 'a': bool(prev) or send, 't': datetime.utcnow()}
        updated = session.execute(text("""
            UPDATE data_check_runs SET checked = :c, failed = :f, pct_ok = :p, ok = :ok,
                   examples_json = :e, alerted = :a, updated_at = :t
            WHERE check_day = :d AND check_name = :n
        """), params).rowcount
        if not updated:
            session.execute(text("""
                INSERT INTO data_check_runs (check_day, check_name, checked, failed, pct_ok, ok, examples_json, alerted, updated_at)
                VALUES (:d, :n, :c, :f, :p, :ok, :e, :a, :t)
            """), params)
        log.info('DATA_CHECK %s %s: %s/%s ok (%s%%) %s', day, name, r['checked'] - r['failed'], r['checked'],
                 r['pct_ok'], 'PASS' if r['ok'] else 'FAIL')
    session.commit()
    return results


def latest_failures(session):
    """Failing checks from the most recent day checked, for page warnings. [] if none or no table."""
    try:
        day = session.execute(text("SELECT MAX(check_day) FROM data_check_runs")).scalar()
        if not day:
            return []
        rows = session.execute(text("""
            SELECT check_name, checked, failed, pct_ok, examples_json, check_day
            FROM data_check_runs WHERE check_day = :d AND ok = FALSE ORDER BY check_name
        """), {'d': day}).fetchall()
    except Exception:
        session.rollback()
        return []
    return [{'name': r[0], 'checked': r[1], 'failed': r[2], 'pct_ok': r[3],
             'examples': json.loads(r[4] or '[]'), 'day': str(r[5])} for r in rows]


# ─────────────────────────────────────────────
# Repair: rebuild last-start fields from the newest past run
# ─────────────────────────────────────────────
BUG_START = '2026-10-01'   # bad saves seen from 16 Oct 2026; start early to be safe


def _rows_by_horse(meeting_csv):
    out = {}
    if not meeting_csv:
        return out
    try:
        for row in csv.DictReader(io.StringIO(meeting_csv)):
            key = (str(row.get('race number', '')).strip(), _norm(row.get('horse name')))
            out.setdefault(key, []).append(row)
    except Exception as e:
        log.warning('data_checks: could not read meeting form file: %s', e)
    return out


def repair_meeting(session, meeting):
    """Copy the newest past run's fields into each horse's saved data.

    Only fields that differ between a horse's past-run rows are copied (the
    'form ...' columns and the like), so today's fields (jockey, weight,
    barrier, injected speed map) are never touched. Returns horses changed.
    """
    from past_runs import parse_date
    from models import Horse, Race
    rows = _rows_by_horse(meeting.csv_data)
    if not rows:
        return 0
    changed = 0
    horses = session.query(Horse).join(Race, Race.id == Horse.race_id).filter(Race.meeting_id == meeting.id).all()
    for horse in horses:
        race_number = str(horse.race.race_number) if horse.race else ''
        runs = rows.get((race_number, _norm(horse.horse_name)))
        if not runs or len(runs) < 2:
            continue
        dated = [(parse_date(r.get('form meeting date')), r) for r in runs]
        dated = [(d, r) for d, r in dated if d]
        if not dated:
            continue
        newest = max(dated, key=lambda t: t[0])[1]
        varying = {k for k in newest if len({str(r.get(k)) for r in runs}) > 1}
        cd = dict(_csv(horse.csv_data))
        if not cd:
            continue
        if all(str(cd.get(k)) == str(newest.get(k)) for k in varying):
            continue
        for k in varying:
            cd[k] = newest.get(k)
        horse.csv_data = cd
        changed += 1
    return changed


def backfill_once(session):
    """Repair every meeting since the bug started, once. Returns horses changed."""
    ensure_table(session)
    done = session.execute(text(
        "SELECT 1 FROM data_check_runs WHERE check_name = 'repair_backfill_oct2026'")).scalar()
    if done:
        return 0
    from models import Meeting
    total = 0
    meetings = session.query(Meeting).filter(Meeting.date >= BUG_START).all()
    for meeting in meetings:
        total += repair_meeting(session, meeting)
    session.execute(text("""
        INSERT INTO data_check_runs (check_day, check_name, checked, failed, pct_ok, ok, examples_json, alerted, updated_at)
        VALUES (:d, 'repair_backfill_oct2026', :c, :f, NULL, TRUE, '[]', TRUE, :t)
    """), {'d': datetime.utcnow().date(), 'c': len(meetings), 'f': total, 't': datetime.utcnow()})
    session.commit()
    log.info('DATA_REPAIR backfill: %s horses fixed across %s meetings since %s', total, len(meetings), BUG_START)
    return total
