import os
import json
import csv
import io
import requests
import logging
from strike_rate_matching import normalize_name
from sqlalchemy import create_engine, text
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

log = logging.getLogger(__name__)


def _safe_log_url(url):
    """Return a URL safe for logs while preserving diagnostic query params."""
    parts = urlsplit(url)
    safe_params = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in {'apikey', 'api_key', 'key'}:
            value = '[REDACTED]'
        safe_params.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_params), parts.fragment))


class PuntingFormService:
    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("PUNTINGFORM_API_KEY")
        if not self.api_key:
            raise ValueError("PuntingForm API key not found")
        self.base_url = 'https://www.puntingform.com.au/api/formdataservice'

    def _make_request(self, url):
        """Make authenticated request"""
        try:
            response = requests.get(url, timeout=30)
            if not response.ok:
                raise Exception(f"PuntingForm API error {response.status_code}: {response.text}")
            return response
        except requests.exceptions.RequestException as e:
            raise Exception(f"API request failed: {str(e)}")

    def get_meetings_list(self, date=None):
        """Get meetings list - V1 format"""
        if date is None:
            date_obj = datetime.now()
        else:
            date_obj = datetime.strptime(date, '%Y-%m-%d')

        date_str = date_obj.strftime('%d-%b-%Y')
        url = f"{self.base_url}/GetMeetingListExt/{date_str}?apikey={self.api_key}"

        response = self._make_request(url)
        data = response.json()

        if data.get('IsError'):
            raise Exception(f"API returned error: {data}")

        meetings = []
        for meeting in data.get('Result', []):
            if meeting.get('IsBarrierTrial', False):
                continue
            meetings.append({
                'meeting_id': str(meeting['MeetingId']),
                'track_name': meeting['Track'],
                'track_code': meeting['TrackCode'],
                'state': meeting['State'],
                'race_count': meeting['RaceCount'],
                'date': date_obj.strftime('%Y-%m-%d'),
                'resulted': meeting.get('Resulted', False)
            })

        return {'meetings': meetings}

    def get_fields_csv(self, track, date, race_number=None):
        """Get CSV data - V1 format"""
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        date_str = date_obj.strftime('%d-%b-%Y')

        if race_number:
            url = f"{self.base_url}/GetFormText/{track.strip()}/{race_number}/{date_str}?apikey={self.api_key}"
            response = self._make_request(url)
            return response.text
        else:
            meeting_url = f"{self.base_url}/GetMeetingListExt/{date_str}?apikey={self.api_key}"
            meeting_response = self._make_request(meeting_url)
            meeting_data = meeting_response.json()

            target_meeting = None
            for meeting in meeting_data.get('Result', []):
                if meeting['Track'].lower() == track.lower():
                    target_meeting = meeting
                    break

            if not target_meeting:
                raise Exception(f"No meeting found for {track} on {date_str}")

            all_csv = []
            for race_num in target_meeting['RaceNumbers']:
                race_url = f"{self.base_url}/GetFormText/{track.strip()}/{race_num}/{date_str}?apikey={self.api_key}"
                race_response = self._make_request(race_url)
                all_csv.append(race_response.text)

            return '\n'.join(all_csv)

    def get_results(self, track, date):
        """Get results - V1 format"""
        date_obj = datetime.strptime(date, '%Y-%m-%d')
        date_str = date_obj.strftime('%d-%b-%Y')
        url = f"{self.base_url}/GetResults/{track}/{date_str}?apikey={self.api_key}"
        response = self._make_request(url)
        return response.json()

    def get_scratchings(self):
        """Get scratchings"""
        url = f"https://www.puntingform.com.au/api/ScratchingsService/GetAllScratchings?apikey={self.api_key}"
        response = self._make_request(url)
        return response.json()

    @staticmethod
    def _normalise_entity_name(name):
        return normalize_name(name)

    @staticmethod
    def _to_int(value):
        try:
            if value in (None, ''):
                return 0
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _to_float(value):
        try:
            if value in (None, ''):
                return None
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _fetch_v2_strike_rate_rows(self, entity_type, jurisdiction=2):
        entity_type_id = 1 if entity_type == 'jockey' else 2
        url = 'https://api.puntingform.com.au/v2/form/strikerate/csv'
        params = {
            'apiKey': self.api_key,
            'jurisdiction': jurisdiction,
            'entityType': entity_type_id,
        }
        request = requests.Request(
            'GET',
            url,
            params=params,
            headers={'accept': 'text/plain'},
        ).prepare()
        log.info(
            "PuntingForm strike-rate request: url=%s entityType=%s entityTypeId=%s jurisdiction=%s",
            _safe_log_url(request.url),
            entity_type,
            entity_type_id,
            jurisdiction,
        )
        log.info("Request URL (API key redacted): %s", _safe_log_url(request.url))
        response = requests.Session().send(request, timeout=30)
        content_type = response.headers.get('content-type', '')
        log.info("HTTP status code: %s", response.status_code)
        log.info("Response content type: %s", content_type)
        log.info(
            "PuntingForm strike-rate response: status=%s content_type=%s body_first_500=%r",
            response.status_code,
            content_type,
            response.text[:500],
        )
        if not response.ok:
            raise Exception(f"PuntingForm strike-rate API error {response.status_code}: {response.text}")

        body = response.text.lstrip()
        expected_csv_header = 'StartDate,EntityId,EntityName'
        is_csv = 'csv' in content_type.lower() or body.startswith(expected_csv_header)
        if not is_csv:
            log.warning(
                "Zero strike-rate rows parsed for %ss jurisdiction %s because response body did not start with expected CSV header %r and content type was not CSV: %s",
                entity_type,
                jurisdiction,
                expected_csv_header,
                content_type,
            )
            return [], []

        reader = csv.DictReader(io.StringIO(response.text))
        rows = list(reader)
        headers = reader.fieldnames or []
        log.info("CSV headers returned: %s", headers)
        log.info("First sample CSV row: %s", rows[0] if rows else None)
        log.info("Parsed row count: %s", len(rows))
        log.info(
            "PuntingForm strike-rate CSV parsed: type=%s jurisdiction=%s headers=%s total_rows=%s first_row=%s",
            entity_type,
            jurisdiction,
            headers,
            len(rows),
            rows[0] if rows else None,
        )
        if not rows:
            log.warning(
                "Zero strike-rate rows parsed for %ss jurisdiction %s because CSV contained headers=%s and no data rows.",
                entity_type,
                jurisdiction,
                headers,
            )
        return rows, headers

    def _map_v2_strike_rate_row(self, row, entity_type, jurisdiction=2):
        career_wins = self._to_int(row.get('CareerWins'))
        career_expected_wins = self._to_float(row.get('CareerExpectedWins'))
        l100_wins = self._to_int(row.get('Last100Wins'))
        l100_expected_wins = self._to_float(row.get('Last100ExpectedWins'))
        name = (row.get('EntityName') or '').strip()
        entity_id = (row.get('EntityId') or '').strip() or None

        return {
            'type': entity_type,
            'jurisdiction': jurisdiction,
            'entity_id': entity_id,
            'normalised_name': normalize_name(name),
            'name': name,
            'start_date': (row.get('StartDate') or '').strip() or None,
            'l100_wins': l100_wins,
            'l100_runs': self._to_int(row.get('Last100Starts')),
            'career_wins': career_wins,
            'career_runs': self._to_int(row.get('CareerStarts')),
            'career_expected_wins': career_expected_wins,
            'l100_expected_wins': l100_expected_wins,
            'career_actual_to_expected': (career_wins / career_expected_wins) if career_expected_wins and career_expected_wins > 0 else None,
            'last100_actual_to_expected': (l100_wins / l100_expected_wins) if l100_expected_wins and l100_expected_wins > 0 else None,
            'raw_csv_row': dict(row),
            'raw_data': dict(row),
        }

    def _database_url(self):
        database_url = os.environ.get('DATABASE_URL')
        if not database_url:
            raise RuntimeError('DATABASE_URL not set; cannot upsert strike_rates')
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        return database_url

    def _ensure_strike_rate_snapshot_table(self, conn):
        """Append-only history of daily strike-rate snapshots.

        `strike_rates` is upserted in place (see below), so it only ever holds
        the *current* L100 win rate per jockey/trainer — there is no way to
        recover what a jockey's strike rate actually was on some date in the
        past. This table starts keeping one dated row per entity per day going
        forward, so backtest.py can look up the strike rate as it stood on (or
        just before) each historical race's own date instead of applying
        today's snapshot to races run months or years ago.
        """
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS strike_rate_snapshots (
                id SERIAL PRIMARY KEY,
                snapshot_date DATE NOT NULL,
                type VARCHAR(20) NOT NULL,
                jurisdiction INTEGER NOT NULL DEFAULT 2,
                normalised_name VARCHAR(255) NOT NULL,
                name VARCHAR(255) NOT NULL,
                l100_wins INTEGER DEFAULT 0,
                l100_runs INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT NOW(),
                UNIQUE (snapshot_date, type, jurisdiction, normalised_name)
            )
        """))
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_strike_rate_snapshots_lookup
            ON strike_rate_snapshots (type, jurisdiction, normalised_name, snapshot_date)
        """))

    def _append_strike_rate_snapshot_rows(self, conn, mapped_rows, snapshot_date):
        appended = 0
        for data in mapped_rows:
            if not data['name'] or not data['normalised_name']:
                continue
            conn.execute(text("""
                INSERT INTO strike_rate_snapshots
                    (snapshot_date, type, jurisdiction, normalised_name, name, l100_wins, l100_runs)
                VALUES (:snapshot_date, :type, :jurisdiction, :normalised_name, :name, :l100_wins, :l100_runs)
                ON CONFLICT (snapshot_date, type, jurisdiction, normalised_name)
                DO UPDATE SET name = EXCLUDED.name, l100_wins = EXCLUDED.l100_wins, l100_runs = EXCLUDED.l100_runs
            """), {
                'snapshot_date': snapshot_date,
                'type': data['type'],
                'jurisdiction': data['jurisdiction'],
                'normalised_name': data['normalised_name'],
                'name': data['name'],
                'l100_wins': data['l100_wins'],
                'l100_runs': data['l100_runs'],
            })
            appended += 1
        return appended

    def _upsert_strike_rate_rows(self, mapped_rows):
        if not mapped_rows:
            log.warning("Zero strike-rate rows inserted because no mapped rows were supplied to upsert.")
            return {'inserted': 0, 'updated': 0, 'skipped': 0}

        engine = create_engine(self._database_url(), pool_pre_ping=True)
        inserted = 0
        updated = 0
        skipped = 0
        now = datetime.utcnow()

        with engine.begin() as conn:
            self._ensure_strike_rate_snapshot_table(conn)
            snapshot_appended = self._append_strike_rate_snapshot_rows(conn, mapped_rows, now.date())
            log.info("Appended %s dated strike-rate snapshot rows for %s.", snapshot_appended, now.date())
            for data in mapped_rows:
                if not data['name']:
                    skipped += 1
                    continue

                lookup = {
                    'type': data['type'],
                    'jurisdiction': data['jurisdiction'],
                    'entity_id': data.get('entity_id'),
                    'normalised_name': data['normalised_name'],
                }
                if data.get('entity_id'):
                    existing = conn.execute(text("""
                        SELECT id FROM strike_rates
                        WHERE type = :type AND jurisdiction = :jurisdiction AND entity_id = :entity_id
                        LIMIT 1
                    """), lookup).fetchone()
                else:
                    existing = conn.execute(text("""
                        SELECT id FROM strike_rates
                        WHERE type = :type AND jurisdiction = :jurisdiction AND normalised_name = :normalised_name
                        LIMIT 1
                    """), lookup).fetchone()

                params = dict(data)
                params['updated_at'] = now
                params['created_at'] = now
                if existing is None:
                    conn.execute(text("""
                        INSERT INTO strike_rates (
                            type, jurisdiction, entity_id, normalised_name, name, start_date,
                            l100_wins, l100_runs, career_wins, career_runs,
                            career_expected_wins, l100_expected_wins,
                            career_actual_to_expected, last100_actual_to_expected,
                            raw_csv_row, raw_data, updated_at, created_at
                        ) VALUES (
                            :type, :jurisdiction, :entity_id, :normalised_name, :name, :start_date,
                            :l100_wins, :l100_runs, :career_wins, :career_runs,
                            :career_expected_wins, :l100_expected_wins,
                            :career_actual_to_expected, :last100_actual_to_expected,
                            CAST(:raw_csv_row AS JSON), CAST(:raw_data AS JSON), :updated_at, :created_at
                        )
                    """), {**params, 'raw_csv_row': json.dumps(params['raw_csv_row']), 'raw_data': json.dumps(params['raw_data'])})
                    inserted += 1
                else:
                    params['id'] = existing[0]
                    conn.execute(text("""
                        UPDATE strike_rates SET
                            type=:type, jurisdiction=:jurisdiction, entity_id=:entity_id,
                            normalised_name=:normalised_name, name=:name, start_date=:start_date,
                            l100_wins=:l100_wins, l100_runs=:l100_runs,
                            career_wins=:career_wins, career_runs=:career_runs,
                            career_expected_wins=:career_expected_wins,
                            l100_expected_wins=:l100_expected_wins,
                            career_actual_to_expected=:career_actual_to_expected,
                            last100_actual_to_expected=:last100_actual_to_expected,
                            raw_csv_row=CAST(:raw_csv_row AS JSON), raw_data=CAST(:raw_data AS JSON),
                            updated_at=:updated_at
                        WHERE id=:id
                    """), {**params, 'raw_csv_row': json.dumps(params['raw_csv_row']), 'raw_data': json.dumps(params['raw_data'])})
                    updated += 1

        log.info("Inserted row count: %s", inserted)
        log.info("Updated row count: %s", updated)
        log.info("Skipped row count: %s", skipped)
        log.info(
            "PuntingForm strike-rate INSERT/UPSERT summary: inserted_rows=%s updated_rows=%s skipped_rows=%s",
            inserted,
            updated,
            skipped,
        )
        if inserted == 0:
            reason = 'all parsed rows matched existing strike_rates rows and were updated' if updated else 'no valid mapped rows with a non-empty name were available'
            if skipped:
                reason += f'; skipped_rows={skipped}'
            log.warning("Zero strike-rate rows inserted: %s", reason)
        return {'inserted': inserted, 'updated': updated, 'skipped': skipped}

    def ingest_strike_rates(self, jurisdiction=2):
        """Fetch and upsert confirmed V2 strike-rate data for trainers and jockeys."""
        log.info("Starting PuntingForm strike-rate ingestion")
        totals = {'trainer': {'inserted': 0, 'updated': 0, 'skipped': 0}, 'jockey': {'inserted': 0, 'updated': 0, 'skipped': 0}}
        for entity_type in ('trainer', 'jockey'):
            try:
                rows, _headers = self._fetch_v2_strike_rate_rows(entity_type, jurisdiction=jurisdiction)
                mapped_rows = [self._map_v2_strike_rate_row(row, entity_type, jurisdiction=jurisdiction) for row in rows]
                totals[entity_type] = self._upsert_strike_rate_rows(mapped_rows)
                if totals[entity_type]['inserted'] == 0 and totals[entity_type]['updated'] == 0:
                    log.warning(
                        "Zero strike-rate rows inserted for %ss jurisdiction %s because parsed_rows=%s mapped_rows=%s skipped_rows=%s.",
                        entity_type,
                        jurisdiction,
                        len(rows),
                        len(mapped_rows),
                        totals[entity_type]['skipped'],
                    )
                log.info("PuntingForm %s rows ingested: %s", entity_type, totals[entity_type])
            except Exception as e:
                log.error(
                    "PuntingForm strike-rate failed API call for %ss jurisdiction %s: %s",
                    entity_type,
                    jurisdiction,
                    e,
                    exc_info=True,
                )
        return totals

    def get_strike_rates(self, meeting_date=None, entity_type='jockey'):
        """
        Fetch confirmed V2 L100 win strike-rate data for all jockeys or trainers.

        Returns:
            dict keyed by normalised name -> {'L100Wins': int, 'L100Runs': int}
        """
        if entity_type not in ('jockey', 'trainer'):
            raise ValueError("entity_type must be 'jockey' or 'trainer'")

        try:
            rows, _headers = self._fetch_v2_strike_rate_rows(entity_type, jurisdiction=2)
            mapped_rows = [self._map_v2_strike_rate_row(row, entity_type, jurisdiction=2) for row in rows]
            self._upsert_strike_rate_rows(mapped_rows)
            log.info("PuntingForm %s rows ingested: %s", entity_type, len(mapped_rows))

            return {
                row['normalised_name']: {
                    'L100Wins': row['l100_wins'],
                    'L100Runs': row['l100_runs'],
                }
                for row in mapped_rows
                if row['normalised_name']
            }
        except Exception as e:
            log.error(
                "PuntingForm strike-rate failed API call for %ss jurisdiction 2: %s",
                entity_type,
                e,
                exc_info=True,
            )
            return {}
