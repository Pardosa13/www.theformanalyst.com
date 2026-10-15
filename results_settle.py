"""Record race results automatically from PuntingForm.

Replaces the results-page routine (Auto-fetch, Mark scratched and complete):

  1. For each PuntingForm-imported meeting from the last few days that still
     has runners without a result, fetch the official results.
  2. Save finish position and starting price for every runner it can match.
     Runners are matched on TAB number (name must agree), else on cleaned name.
     A missing SP is stored as NULL, never 0.
  3. Once a race has results, any of our active runners not in them are
     recorded as scratched (finish 0). Each one is logged by name.
  4. Refresh the incomplete-results flag for the meeting.

Results come from PuntingForm's V2 /form/results feed (TAB numbers, official
SP). The older V1 GetResults feed is the fallback. Scores and ML are never
touched. Safe to re-run: results are upserted per runner.

Run:  python results_settle.py              # last 3 days (Melbourne)
      python results_settle.py --days 7
      python results_settle.py --meeting-id 1234
"""
import argparse
import logging
import os
import sys
from datetime import date, timedelta

import requests
from sqlalchemy import create_engine, text

from bsp_import import clean_name, meeting_day, same_horse, track_of, to_int
from scoring_integrity import melbourne_today, refresh_results_incomplete_flags

log = logging.getLogger('results_settle')

V2_RESULTS_URL = 'https://api.puntingform.com.au/v2/form/results'


# ─────────────────────────────────────────────
# Fetching
# ─────────────────────────────────────────────
def _price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price > 1 else None


def _position(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def fetch_v2(pf, day, track):
    """[{race_no, runners: [{name, tab, position, price}]}] from V2, or None."""
    meetings = pf.get_meetings_list(day.isoformat()).get('meetings', [])
    pf_meeting = next((m for m in meetings if m['track_name'].lower() == str(track).lower()), None)
    if not pf_meeting:
        return None
    response = requests.get(V2_RESULTS_URL, params={
        'meetingId': pf_meeting['meeting_id'], 'raceNumber': 0, 'apiKey': pf.api_key,
    }, headers={'accept': 'application/json'}, timeout=30)
    if not response.ok:
        log.warning('V2 results HTTP %s for %s %s', response.status_code, track, day)
        return None
    payload = (response.json() or {}).get('payLoad') or []
    races = []
    for meeting in payload:
        for race in meeting.get('raceResults') or []:
            races.append({
                'race_no': to_int(race.get('raceNumber')),
                'runners': [{
                    'name': r.get('runner') or '',
                    'tab': to_int(r.get('tabNo')),
                    'position': _position(r.get('position')),
                    'price': _price(r.get('price')),
                } for r in race.get('runners') or []],
            })
    return races


def fetch_v1(pf, day, track):
    """Same shape as fetch_v2, from the older GetResults feed, or None."""
    response = pf.get_results(track, day.isoformat())
    if not response or response.get('IsError'):
        return None
    races = []
    for race in response.get('RaceDetails') or response.get('Result') or []:
        races.append({
            'race_no': to_int(race.get('RaceNumber') or race.get('RaceNo') or race.get('Race')),
            'runners': [{
                'name': r.get('Name') or r.get('Horse') or r.get('RunnerName') or '',
                'tab': to_int(r.get('TabNo') or r.get('TabNumber') or r.get('Number')),
                'position': _position(r.get('Position') or r.get('FinishPosition') or r.get('Place')),
                'price': _price(r.get('Price_SP') or r.get('SP') or r.get('StartingPrice')),
            } for r in race.get('Runners') or []],
        })
    return races


def fetch_results(pf, day, track):
    """(races, source). Tries V2 then V1."""
    try:
        races = fetch_v2(pf, day, track)
        if races:
            return races, 'v2'
    except Exception as exc:
        log.warning('V2 results failed for %s %s: %s', track, day, exc)
    try:
        races = fetch_v1(pf, day, track)
        if races:
            return races, 'v1'
    except Exception as exc:
        log.warning('V1 results failed for %s %s: %s', track, day, exc)
    return [], None


# ─────────────────────────────────────────────
# Matching and saving
# ─────────────────────────────────────────────
def match(pf_runner, our_runners):
    """Our runner for one PuntingForm result row: TAB (name must agree), else name."""
    name = clean_name(pf_runner['name'])
    if pf_runner['tab'] is not None:
        by_tab = next((r for r in our_runners if r['tab'] == pf_runner['tab']), None)
        if by_tab and (not name or same_horse(name, by_tab['clean'])):
            return by_tab, 'tab'
    by_name = next((r for r in our_runners if r['clean'] == name), None)
    if by_name:
        return by_name, 'name'
    return None, None


def save_result(conn, runner, finish, sp, recorded_by):
    """Insert or update one runner's result. Returns 'created' or 'updated'.

    Never creates a second result row for a horse: the database is checked
    first, and the new id is remembered on `runner`. A known SP is never
    replaced by NULL.
    """
    result_id = runner['result_id'] or conn.execute(
        text('SELECT id FROM results WHERE horse_id = :horse_id ORDER BY id LIMIT 1'),
        {'horse_id': runner['horse_id']},
    ).scalar()
    if result_id:
        conn.execute(text("""
            UPDATE results SET finish_position = :finish, sp = COALESCE(:sp, sp),
                   recorded_at = NOW(), recorded_by = COALESCE(:by, recorded_by)
            WHERE id = :id
        """), {'finish': finish, 'sp': sp, 'by': recorded_by, 'id': result_id})
        runner['result_id'] = result_id
        return 'updated'
    runner['result_id'] = conn.execute(text("""
        INSERT INTO results (horse_id, finish_position, sp, recorded_at, recorded_by)
        VALUES (:horse_id, :finish, :sp, NOW(), :by)
        RETURNING id
    """), {'horse_id': runner['horse_id'], 'finish': finish, 'sp': sp, 'by': recorded_by}).scalar()
    return 'created'


def load_runners(conn, meeting_id):
    rows = conn.execute(text("""
        SELECT r.id AS race_id, r.race_number, h.id AS horse_id, h.horse_name,
               COALESCE(h.is_scratched, FALSE) AS is_scratched, h.csv_data->>'horse number' AS tab,
               res.id AS result_id, res.finish_position, res.sp
        FROM races r JOIN horses h ON h.race_id = r.id
        LEFT JOIN results res ON res.horse_id = h.id
        WHERE r.meeting_id = :meeting_id
    """), {'meeting_id': meeting_id}).mappings().all()
    races = {}
    seen_horses = set()
    for row in rows:
        if row['horse_id'] in seen_horses:
            continue  # a horse with several result rows: keep one
        seen_horses.add(row['horse_id'])
        races.setdefault(row['race_number'], {'race_id': row['race_id'], 'race_ids': set(), 'runners': []})
        race = races[row['race_number']]
        race['race_ids'].add(row['race_id'])
        race['runners'].append({
            'horse_id': row['horse_id'], 'name': row['horse_name'], 'clean': clean_name(row['horse_name']),
            'tab': to_int(row['tab']), 'is_scratched': row['is_scratched'], 'result_id': row['result_id'],
            'finish_position': row['finish_position'], 'sp': row['sp'],
        })
    return races


def settle_meeting(conn, meeting, pf, recorded_by=None):
    """Fetch and save results for one meeting. Returns a summary dict.

    `meeting` needs id, meeting_name, date and puntingform_id. `conn` is a
    connection inside the caller's transaction; the caller commits.
    """
    summary = {'meeting_id': meeting['id'], 'meeting_name': meeting['meeting_name'], 'source': None,
               'races_resulted': 0, 'saved': 0, 'created': 0, 'updated': 0, 'no_sp': 0,
               'duplicate_races': [], 'marked_scratched': [], 'unmatched': [],
               'match_methods': {}}
    day = meeting_day(meeting.get('date'), meeting['meeting_name'])
    track = meeting.get('puntingform_id') or track_of(meeting['meeting_name'], None)
    if not day or not track:
        summary['status'] = 'skipped: no date or track'
        return summary

    pf_races, summary['source'] = fetch_results(pf, day, track)
    if not pf_races:
        summary['status'] = 'pending: no results yet'
        return summary

    ours = load_runners(conn, meeting['id'])
    touched_race_ids = []
    done_races = set()
    for pf_race in pf_races:
        race = ours.get(pf_race['race_no'])
        if not race or not any((r['position'] or 0) > 0 for r in pf_race['runners']):
            continue  # not our race, or not run yet
        if pf_race['race_no'] in done_races:
            log.warning('%s: race %s came back twice from the feed; using the first copy',
                        meeting['meeting_name'], pf_race['race_no'])
            continue
        done_races.add(pf_race['race_no'])
        if len(race['race_ids']) > 1:
            log.warning('%s: race %s has %s race rows in our database (%s)', meeting['meeting_name'],
                        pf_race['race_no'], len(race['race_ids']), sorted(race['race_ids']))
            summary['duplicate_races'].append(pf_race['race_no'])
        summary['races_resulted'] += 1
        touched_race_ids.extend(race['race_ids'])
        seen = set()
        for pf_runner in pf_race['runners']:
            runner, method = match(pf_runner, race['runners'])
            if runner is None or runner['horse_id'] in seen:
                summary['unmatched'].append(
                    f"R{pf_race['race_no']} #{pf_runner['tab']} '{pf_runner['name']}'")
                continue
            seen.add(runner['horse_id'])
            summary['match_methods'][method] = summary['match_methods'].get(method, 0) + 1
            position = pf_runner['position']
            finish = 0 if not position else min(position, 5)
            sp = pf_runner['price'] if finish else None
            if finish and sp is None:
                summary['no_sp'] += 1
            summary[save_result(conn, runner, finish, sp, recorded_by)] += 1
            summary['saved'] += 1
        # Race is official: our active runners missing from it did not run.
        for runner in race['runners']:
            if runner['horse_id'] in seen or runner['result_id']:
                continue
            seen.add(runner['horse_id'])
            summary[save_result(conn, runner, 0, None, recorded_by)] += 1
            summary['marked_scratched'].append(
                f"R{pf_race['race_no']} #{runner['tab']} {runner['name']}"
                + ('' if runner['is_scratched'] else ' (was not marked scratched)'))

    if touched_race_ids and conn.dialect.name == 'postgresql':
        refresh_results_incomplete_flags(conn, touched_race_ids)
    summary['status'] = 'settled' if summary['races_resulted'] else 'pending: no races run yet'
    return summary


# ─────────────────────────────────────────────
# Cron entry point
# ─────────────────────────────────────────────
def meetings_to_settle(conn, days, meeting_id=None):
    today = melbourne_today()
    first = today - timedelta(days=days)
    rows = conn.execute(text("""
        SELECT DISTINCT m.id, m.meeting_name, m.date, m.puntingform_id
        FROM meetings m
        JOIN races r ON r.meeting_id = m.id
        JOIN horses h ON h.race_id = r.id
        LEFT JOIN results res ON res.horse_id = h.id
        WHERE m.puntingform_id IS NOT NULL
          AND res.id IS NULL
          AND (CAST(:meeting_id AS INTEGER) IS NULL OR m.id = :meeting_id)
    """), {'meeting_id': meeting_id}).mappings().all()
    picked = []
    for row in rows:
        day = meeting_day(row['date'], row['meeting_name'])
        if meeting_id or (day and first <= day <= today):
            picked.append(dict(row))
    return picked


def log_summary(summary):
    log.info('%s [%s] %s: %s races, %s results saved (%s new, %s updated, %s without SP), '
             '%s marked scratched, match %s',
             summary['meeting_name'], summary['source'], summary['status'], summary['races_resulted'],
             summary['saved'], summary['created'], summary['updated'], summary['no_sp'],
             len(summary['marked_scratched']), summary['match_methods'])
    for item in summary['marked_scratched']:
        log.info('   marked scratched (not in official results): %s', item)
    for item in summary['unmatched']:
        log.warning('   UNMATCHED result row: %s', item)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout)
    parser = argparse.ArgumentParser(description='Record race results from PuntingForm.')
    parser.add_argument('--days', type=int, default=3, help='Look back this many days (Melbourne).')
    parser.add_argument('--meeting-id', type=int, default=None)
    args = parser.parse_args(argv)

    from bsp_import import get_engine
    from puntingform_service import PuntingFormService

    engine = get_engine()
    pf = PuntingFormService()
    with engine.connect() as conn:
        meetings = meetings_to_settle(conn, args.days, args.meeting_id)
    log.info('%s meeting(s) with runners still waiting on a result', len(meetings))
    for meeting in meetings:
        try:
            with engine.begin() as conn:
                log_summary(settle_meeting(conn, meeting, pf))
        except Exception:
            log.exception('Failed to settle %s', meeting['meeting_name'])


if __name__ == '__main__':
    main()
