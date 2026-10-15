"""Load Betfair's free monthly ANZ thoroughbred files and link them to our runners.

Source: https://betfair-datascientists.github.io/data/dataListing/
One CSV per month, one row per runner, with the Betfair Starting Price (BSP).

What this writes:
  betfair_prices        one row per runner per Betfair market (all AU states;
                        NZ only when the market matched one of our races)
  results.bsp           WIN_BSP for every matched runner that has a result
                        (results.sp, the bookmaker SP, is never touched)
  races.market_id       Betfair WIN_MARKET_ID
  races.start_time_utc  ACTUAL_OFF_TIME, else SCHEDULED_RACE_TIME
  horses.betfair_selection_id

Matching: a Betfair market is matched to our race on date + race number, then
by how many runner names agree (plus the track name as a tie-break). Runners
are matched on cleaned name; if the name differs, on TAB number when the two
names are clearly the same horse (one a prefix of the other, or very similar).

Times in the files are Australian eastern time for every state (WA races sit
2.5 hours later than NSW ones), so they are read as Australia/Sydney.

Safe to re-run: rows upsert on (market_id, selection_id), and a file that was
already loaded with the same row count is skipped unless --rematch is given.

Run:  python bsp_import.py            # every month from Feb 2026 to now
      python bsp_import.py --rematch  # re-link everything (e.g. after new meetings)
"""
import argparse
import csv
import difflib
import io
import logging
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from sqlalchemy import create_engine, text

log = logging.getLogger('bsp_import')

BASE_URL = 'https://betfair-datascientists.github.io/data/assets/ANZ_Thoroughbreds_{year}_{month:02d}.csv'
FIRST_MONTH = (2026, 2)
FILE_TZ = ZoneInfo('Australia/Sydney')
MIN_NAME_OVERLAP = 0.5   # share of a market's runners whose names must match one race
FUZZY_NAME_RATIO = 0.8   # name similarity accepted when the TAB number also agrees
SAMPLE = 15              # examples printed per problem type


# ─────────────────────────────────────────────
# Names and times
# ─────────────────────────────────────────────
def clean_name(name):
    """'Milly's Clever Too (NZ)' -> 'millys clever too'. Betfair drops apostrophes."""
    s = str(name or '').lower().strip()
    s = re.sub(r"\((nz|aus|ire|gb|usa|fr|ger|jpn|saf|arg|chi|brz)\)\s*$", '', s)
    s = re.sub(r"[’'`]", '', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return s.strip()


def clean_track(name):
    return re.sub(r'[^a-z0-9]+', ' ', str(name or '').lower()).strip()


def tracks_match(a, b):
    a, b = clean_track(a), clean_track(b)
    return bool(a and b) and (a == b or a.startswith(b) or b.startswith(a) or a in b or b in a)


def same_horse(a, b):
    """Two cleaned names that are clearly the same horse (used only when TAB numbers agree)."""
    if not a or not b:
        return False
    if a.startswith(b) or b.startswith(a):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= FUZZY_NAME_RATIO


def to_utc(day, clock):
    """'2026-09-01' + '13:03:06.000' (eastern time) -> naive UTC datetime, or None."""
    if not day or not clock:
        return None
    try:
        local = datetime.strptime(f'{day} {clock.split(".")[0]}', '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None
    return local.replace(tzinfo=FILE_TZ).astimezone(timezone.utc).replace(tzinfo=None)


def to_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def meeting_day(meeting_date, meeting_name):
    if meeting_date:
        return meeting_date
    name = str(meeting_name or '')
    if len(name) >= 7 and name[:6].isdigit() and name[6] == '_':
        try:
            return date(2000 + int(name[:2]), int(name[2:4]), int(name[4:6]))
        except ValueError:
            return None
    return None


def track_of(meeting_name, puntingform_id):
    name = str(meeting_name or '')
    if len(name) > 7 and name[:6].isdigit() and name[6] == '_':
        return name[7:]
    return puntingform_id or name


# ─────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────
def get_engine():
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise SystemExit('DATABASE_URL not set')
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    return create_engine(url, pool_pre_ping=True)


def ensure_tables(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS betfair_prices (
            id BIGSERIAL PRIMARY KEY,
            market_id VARCHAR(32) NOT NULL,
            selection_id BIGINT NOT NULL,
            meeting_date DATE NOT NULL,
            track VARCHAR(100),
            state VARCHAR(8),
            race_no INTEGER,
            tab_number INTEGER,
            selection_name VARCHAR(100),
            scheduled_off_utc TIMESTAMP,
            actual_off_utc TIMESTAMP,
            won BOOLEAN,
            win_bsp FLOAT,
            win_bsp_volume FLOAT,
            preplay_last_price FLOAT,
            preplay_wap FLOAT,
            preplay_volume FLOAT,
            best_back_at_scheduled_off FLOAT,
            best_lay_at_scheduled_off FLOAT,
            bsp_rank INTEGER,
            race_id INTEGER,
            horse_id INTEGER,
            match_method VARCHAR(20),
            source_file VARCHAR(80),
            imported_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (market_id, selection_id)
        )
    """))
    conn.execute(text('CREATE INDEX IF NOT EXISTS ix_betfair_prices_horse_id ON betfair_prices (horse_id)'))
    conn.execute(text('CREATE INDEX IF NOT EXISTS ix_betfair_prices_race_id ON betfair_prices (race_id)'))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS betfair_import_files (
            file_name VARCHAR(80) PRIMARY KEY,
            row_count INTEGER NOT NULL,
            markets INTEGER,
            markets_matched INTEGER,
            runners_matched INTEGER,
            imported_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text('ALTER TABLE results ADD COLUMN IF NOT EXISTS bsp FLOAT'))
    conn.execute(text('ALTER TABLE races ADD COLUMN IF NOT EXISTS start_time_utc TIMESTAMP'))
    conn.execute(text('ALTER TABLE races ADD COLUMN IF NOT EXISTS market_id VARCHAR(255)'))
    conn.execute(text('ALTER TABLE horses ADD COLUMN IF NOT EXISTS betfair_selection_id INTEGER'))


def load_our_races(conn, first_day, last_day):
    """{(date, race_number): [race, ...]}, each race with its runners by cleaned name and TAB."""
    rows = conn.execute(text("""
        SELECT r.id AS race_id, r.race_number, m.date AS meeting_date, m.meeting_name,
               m.puntingform_id, COALESCE(m.auto_imported, FALSE) AS auto_imported,
               h.id AS horse_id, h.horse_name, COALESCE(h.is_scratched, FALSE) AS is_scratched,
               h.csv_data->>'horse number' AS tab,
               res.id AS result_id, res.finish_position, res.sp
        FROM races r
        JOIN meetings m ON m.id = r.meeting_id
        JOIN horses h ON h.race_id = r.id
        LEFT JOIN results res ON res.horse_id = h.id
        WHERE m.date BETWEEN :first_day AND :last_day
           OR (m.date IS NULL AND m.meeting_name ~ '^[0-9]{6}_')
    """), {'first_day': first_day, 'last_day': last_day}).mappings().all()

    races = {}
    for row in rows:
        day = meeting_day(row['meeting_date'], row['meeting_name'])
        if day is None or not (first_day <= day <= last_day):
            continue
        race = races.setdefault(row['race_id'], {
            'race_id': row['race_id'],
            'key': (day, row['race_number']),
            'meeting_name': row['meeting_name'],
            'track': track_of(row['meeting_name'], row['puntingform_id']),
            'auto_imported': row['auto_imported'],
            'by_name': {},
            'by_tab': {},
            'runners': [],
        })
        runner = {
            'horse_id': row['horse_id'],
            'name': row['horse_name'],
            'clean': clean_name(row['horse_name']),
            'tab': to_int(row['tab']),
            'is_scratched': row['is_scratched'],
            'result_id': row['result_id'],
            'finish_position': row['finish_position'],
            'sp': row['sp'],
        }
        race['runners'].append(runner)
        race['by_name'].setdefault(runner['clean'], runner)
        if runner['tab'] is not None:
            race['by_tab'].setdefault(runner['tab'], runner)

    index = defaultdict(list)
    for race in races.values():
        index[race['key']].append(race)
    return index


# ─────────────────────────────────────────────
# Files
# ─────────────────────────────────────────────
def months_to_load(today=None):
    today = today or date.today()
    year, month = FIRST_MONTH
    while (year, month) <= (today.year, today.month):
        yield year, month
        month += 1
        if month > 12:
            year, month = year + 1, 1


def download(year, month):
    """CSV text, or None when the month is not published yet."""
    url = BASE_URL.format(year=year, month=month)
    response = requests.get(url, timeout=120)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content.decode('utf-8-sig')


def parse(csv_text):
    return list(csv.DictReader(io.StringIO(csv_text)))


# ─────────────────────────────────────────────
# Matching
# ─────────────────────────────────────────────
def match_market(runners, candidates):
    """Pick our race for one Betfair market. Returns (race, reason)."""
    if not candidates:
        return None, 'no race of ours on that date and race number'
    names = {clean_name(r['SELECTION_NAME']) for r in runners}
    track = runners[0]['TRACK']
    scored = []
    for race in candidates:
        overlap = len(names & set(race['by_name']))
        scored.append((overlap, tracks_match(track, race['track']), race['auto_imported'], race))
    scored.sort(key=lambda s: (s[0], s[1], s[2]), reverse=True)
    best = scored[0]
    if best[0] < max(2, MIN_NAME_OVERLAP * len(names)):
        return None, f'best name overlap {best[0]}/{len(names)} ({best[3]["meeting_name"]})'
    return best[3], None


def match_runner(row, race):
    """(our runner, method) or (None, None)."""
    name = clean_name(row['SELECTION_NAME'])
    runner = race['by_name'].get(name)
    if runner:
        return runner, 'name'
    tab_runner = race['by_tab'].get(to_int(row['TAB_NUMBER']))
    if tab_runner and same_horse(name, tab_runner['clean']):
        return tab_runner, 'tab+similar'
    return None, None


def price_row(row, file_name, race, runner, method):
    day = row['LOCAL_MEETING_DATE']
    return {
        'market_id': row['WIN_MARKET_ID'],
        'selection_id': to_int(row['SELECTION_ID']),
        'meeting_date': day,
        'track': row['TRACK'][:100],
        'state': row['STATE_CODE'][:8],
        'race_no': to_int(row['RACE_NO']),
        'tab_number': to_int(row['TAB_NUMBER']),
        'selection_name': row['SELECTION_NAME'][:100],
        'scheduled_off_utc': to_utc(day, row['SCHEDULED_RACE_TIME']),
        'actual_off_utc': to_utc(day, row['ACTUAL_OFF_TIME']),
        'won': row['WIN_RESULT'] == 'WINNER',
        'win_bsp': to_float(row['WIN_BSP']),
        'win_bsp_volume': to_float(row['WIN_BSP_VOLUME']),
        'preplay_last_price': to_float(row['WIN_PREPLAY_LAST_PRICE_TAKEN']),
        'preplay_wap': to_float(row['WIN_PREPLAY_WEIGHTED_AVERAGE_PRICE_TAKEN']),
        'preplay_volume': to_float(row['WIN_PREPLAY_VOLUME']),
        'best_back_at_scheduled_off': to_float(row['BEST_AVAIL_BACK_AT_SCHEDULED_OFF']),
        'best_lay_at_scheduled_off': to_float(row['BEST_AVAIL_LAY_AT_SCHEDULED_OFF']),
        'race_id': race['race_id'] if race else None,
        'horse_id': runner['horse_id'] if runner else None,
        'match_method': method,
        'source_file': file_name,
    }


UPSERT_SQL = text("""
    INSERT INTO betfair_prices (
        market_id, selection_id, meeting_date, track, state, race_no, tab_number, selection_name,
        scheduled_off_utc, actual_off_utc, won, win_bsp, win_bsp_volume, preplay_last_price,
        preplay_wap, preplay_volume, best_back_at_scheduled_off, best_lay_at_scheduled_off,
        race_id, horse_id, match_method, source_file, imported_at
    ) VALUES (
        :market_id, :selection_id, :meeting_date, :track, :state, :race_no, :tab_number, :selection_name,
        :scheduled_off_utc, :actual_off_utc, :won, :win_bsp, :win_bsp_volume, :preplay_last_price,
        :preplay_wap, :preplay_volume, :best_back_at_scheduled_off, :best_lay_at_scheduled_off,
        :race_id, :horse_id, :match_method, :source_file, NOW()
    )
    ON CONFLICT (market_id, selection_id) DO UPDATE SET
        win_bsp = EXCLUDED.win_bsp,
        won = EXCLUDED.won,
        actual_off_utc = EXCLUDED.actual_off_utc,
        preplay_last_price = EXCLUDED.preplay_last_price,
        race_id = EXCLUDED.race_id,
        horse_id = EXCLUDED.horse_id,
        match_method = EXCLUDED.match_method,
        imported_at = NOW()
""")


def import_file(conn, file_name, rows, index, report):
    """Match and upsert one month. Returns (markets, markets_matched, runners_matched)."""
    markets = defaultdict(list)
    for row in rows:
        markets[row['WIN_MARKET_ID']].append(row)

    out = []
    race_updates = []
    selection_updates = []
    matched_markets = matched_runners = 0
    matched_race_ids = set()
    for market_id, runners in markets.items():
        first = runners[0]
        is_nz = first['STATE_CODE'] == 'NZ'
        try:
            day = date.fromisoformat(first['LOCAL_MEETING_DATE'])
        except ValueError:
            continue
        race, reason = match_market(runners, index.get((day, to_int(first['RACE_NO'])), []))
        if race is None:
            if not is_nz:
                # Most of these are simply meetings we never imported.
                report['au_markets_unmatched'] += 1
                out.extend(price_row(r, file_name, None, None, None) for r in runners)
            continue

        matched_markets += 1
        matched_race_ids.add(race['race_id'])
        report['nz_markets_matched' if is_nz else 'au_markets_matched'] += 1
        actual = to_utc(first['LOCAL_MEETING_DATE'], first['ACTUAL_OFF_TIME'])
        scheduled = to_utc(first['LOCAL_MEETING_DATE'], first['SCHEDULED_RACE_TIME'])
        race_updates.append({'id': race['race_id'], 'market_id': market_id, 'start': actual or scheduled})

        seen = set()
        for row in runners:
            runner, method = match_runner(row, race)
            if runner is None or runner['horse_id'] in seen:
                report['runners_unmatched'] += 1
                report['unmatched_runners'].append(
                    f"{race['meeting_name']} R{row['RACE_NO']} #{row['TAB_NUMBER']} "
                    f"Betfair '{row['SELECTION_NAME']}' vs ours "
                    f"'{(race['by_tab'].get(to_int(row['TAB_NUMBER'])) or {}).get('name', '-')}'")
                out.append(price_row(row, file_name, race, None, None))
                continue
            seen.add(runner['horse_id'])
            matched_runners += 1
            report['methods'][method] += 1
            selection_updates.append({'id': runner['horse_id'], 'sel': to_int(row['SELECTION_ID'])})
            out.append(price_row(row, file_name, race, runner, method))

            # Cross-checks against our own results
            label = f"{race['meeting_name']} R{row['RACE_NO']} {runner['name']} (BSP {row['WIN_BSP']})"
            if runner['result_id'] is None:
                report['ran_no_result'] += 1
            elif runner['finish_position'] == 0:
                report['ran_but_ours_scratched'] += 1
                report['ran_but_ours_scratched_eg'].append(label)
            elif not runner['sp'] or runner['sp'] <= 1:
                report['ran_but_ours_no_sp'] += 1
                report['ran_but_ours_no_sp_eg'].append(label)
            if row['WIN_RESULT'] == 'WINNER' and runner['finish_position'] not in (None, 1):
                report['winner_mismatch'].append(f"{label}: ours finish={runner['finish_position']}")
        # Our runners with no Betfair row at all (not scratched by us)
        for runner in race['runners']:
            if runner['horse_id'] not in seen and not runner['is_scratched'] and runner['finish_position'] not in (0,):
                report['ours_missing_on_betfair'].append(f"{race['meeting_name']} R{race['key'][1]} {runner['name']}")

    # The match rate that matters: of OUR races in this month, how many found a market.
    for races in index.values():
        for race in races:
            if not any(r['result_id'] for r in race['runners']):
                continue  # never resulted: not a race we would use
            key = 'our_races_matched' if race['race_id'] in matched_race_ids else 'our_races_unmatched'
            report[key] += 1
            if race['auto_imported']:
                report['our_auto_races_matched' if key == 'our_races_matched' else 'our_auto_races_unmatched'] += 1
            if key == 'our_races_unmatched':
                report['our_unmatched_eg'].append(f"{race['meeting_name']} R{race['key'][1]}")

    if out:
        conn.execute(UPSERT_SQL, out)
    if race_updates:
        conn.execute(text('UPDATE races SET market_id = :market_id, start_time_utc = COALESCE(:start, start_time_utc) '
                          'WHERE id = :id'), race_updates)
    if selection_updates:
        conn.execute(text('UPDATE horses SET betfair_selection_id = :sel WHERE id = :id'), selection_updates)
    return len(markets), matched_markets, matched_runners


def sync_derived(conn):
    """results.bsp and bsp_rank from betfair_prices. Cheap; run every time."""
    conn.execute(text("""
        UPDATE betfair_prices bp SET bsp_rank = ranked.rnk
        FROM (SELECT id, RANK() OVER (PARTITION BY market_id ORDER BY win_bsp ASC NULLS LAST) AS rnk
              FROM betfair_prices) ranked
        WHERE ranked.id = bp.id AND bp.bsp_rank IS DISTINCT FROM ranked.rnk
    """))
    updated = conn.execute(text("""
        UPDATE results r SET bsp = bp.win_bsp
        FROM betfair_prices bp
        WHERE bp.horse_id = r.horse_id AND r.bsp IS DISTINCT FROM bp.win_bsp
    """)).rowcount
    return updated


def new_report():
    return {
        'au_markets_matched': 0, 'au_markets_unmatched': 0, 'nz_markets_matched': 0,
        'our_races_matched': 0, 'our_races_unmatched': 0, 'our_unmatched_eg': [],
        'our_auto_races_matched': 0, 'our_auto_races_unmatched': 0,
        'runners_unmatched': 0, 'methods': Counter(),
        'ran_no_result': 0, 'ran_but_ours_scratched': 0, 'ran_but_ours_no_sp': 0,
        'unmatched_runners': [], 'ran_but_ours_scratched_eg': [],
        'ran_but_ours_no_sp_eg': [], 'winner_mismatch': [], 'ours_missing_on_betfair': [],
    }


def print_report(report):
    matched = sum(report['methods'].values())
    ours = report['our_races_matched'] + report['our_races_unmatched']
    ours_auto = report['our_auto_races_matched'] + report['our_auto_races_unmatched']

    def pct(a, b):
        return f' ({100 * a / b:.1f}%)' if b else ''

    lines = [
        '===== BSP IMPORT REPORT =====',
        f"Our resulted races matched to a Betfair market: {report['our_races_matched']}/{ours}"
        + pct(report['our_races_matched'], ours),
        f"  auto-imported only: {report['our_auto_races_matched']}/{ours_auto}"
        + pct(report['our_auto_races_matched'], ours_auto),
        f"Betfair AU markets with no race of ours (meetings we never imported): {report['au_markets_unmatched']}",
        f"NZ markets matched: {report['nz_markets_matched']}",
        f"Runners matched in matched markets: {matched}/{matched + report['runners_unmatched']}"
        + (f" ({100 * matched / (matched + report['runners_unmatched']):.1f}%)" if matched else ''),
        f"  by method: {dict(report['methods'])}",
        f"Matched runners with no result row yet: {report['ran_no_result']}",
        f"Ran on Betfair but OUR result says scratched (finish 0): {report['ran_but_ours_scratched']}",
        f"Ran on Betfair but OUR result has no SP: {report['ran_but_ours_no_sp']}",
        f"Betfair winner but our finish is not 1: {len(report['winner_mismatch'])}",
        f"Our active runners with no Betfair row in a matched market: {len(report['ours_missing_on_betfair'])}",
    ]
    for title, key in (
        ('Our races with no Betfair market', 'our_unmatched_eg'),
        ('Unmatched runners in matched markets', 'unmatched_runners'),
        ('Ran but ours scratched', 'ran_but_ours_scratched_eg'),
        ('Ran but ours no SP', 'ran_but_ours_no_sp_eg'),
        ('Winner mismatches', 'winner_mismatch'),
        ('Ours missing on Betfair', 'ours_missing_on_betfair'),
    ):
        examples = report[key]
        if examples:
            lines.append(f'-- {title} (first {min(SAMPLE, len(examples))} of {len(examples)}):')
            lines.extend(f'   {e}' for e in examples[:SAMPLE])
    for line in lines:
        print(line, flush=True)


def run(engine, rematch=False, today=None, fetch=download):
    report = new_report()
    with engine.begin() as conn:
        ensure_tables(conn)
    for year, month in months_to_load(today):
        file_name = f'ANZ_Thoroughbreds_{year}_{month:02d}.csv'
        csv_text = fetch(year, month)
        if csv_text is None:
            log.info('%s not published yet', file_name)
            continue
        rows = parse(csv_text)
        with engine.begin() as conn:
            done = conn.execute(text('SELECT row_count FROM betfair_import_files WHERE file_name = :f'),
                                {'f': file_name}).scalar()
            if done == len(rows) and not rematch:
                log.info('%s already loaded (%s rows), skipping', file_name, done)
                continue
            days = [date.fromisoformat(r['LOCAL_MEETING_DATE']) for r in rows if r.get('LOCAL_MEETING_DATE')]
            index = load_our_races(conn, min(days), max(days)) if days else {}
            markets, matched_markets, matched_runners = import_file(conn, file_name, rows, index, report)
            conn.execute(text("""
                INSERT INTO betfair_import_files (file_name, row_count, markets, markets_matched, runners_matched)
                VALUES (:f, :n, :m, :mm, :rm)
                ON CONFLICT (file_name) DO UPDATE SET row_count = EXCLUDED.row_count, markets = EXCLUDED.markets,
                    markets_matched = EXCLUDED.markets_matched, runners_matched = EXCLUDED.runners_matched,
                    imported_at = NOW()
            """), {'f': file_name, 'n': len(rows), 'm': markets, 'mm': matched_markets, 'rm': matched_runners})
            log.info('%s: %s rows, %s markets, %s matched markets, %s matched runners',
                     file_name, len(rows), markets, matched_markets, matched_runners)
    with engine.begin() as conn:
        updated = sync_derived(conn)
        # Betfair is the check on our results: flag races where they disagree.
        from scoring_integrity import refresh_results_incomplete_flags
        flagged = refresh_results_incomplete_flags(conn)
    log.info('results.bsp updated on %s rows; incomplete-results flag changed on %s races', updated, flagged)
    print_report(report)
    return report


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--rematch', action='store_true', help='Re-link files that were already loaded.')
    args = parser.parse_args(argv)
    run(get_engine(), rematch=args.rematch)


if __name__ == '__main__':
    main()
