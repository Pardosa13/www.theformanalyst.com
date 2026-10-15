"""Rules that keep stored scores honest.

Three jobs, shared by every code path that writes a score:

1. race_has_started(): no score may change once a race has jumped. A score
   written after the start can see the result, and then every backtest that
   reads it is quietly cheating.
2. reprice_race(): turns a race's final scores (after rail / pace bias) into
   win probabilities and rated prices with the same formula analyzer.js uses,
   so score order and price order always agree.
3. log_score_change(): one row in score_history for every score or price
   change, with the reason, so a stored number can always be traced back.

Plus refresh_results_incomplete_flags(), which marks races whose results are
too thin to trust (manual uploads that only recorded the placegetters).
"""
import logging
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import text

log = logging.getLogger(__name__)

MELBOURNE_TZ = ZoneInfo('Australia/Melbourne')

# Must match calculateTrueOdds() in analyzer.js. The 10% margin is kept for
# now so rated prices do not jump on the site; Phase 4 calibration replaces it.
ANALYZER_STEEPNESS_K = 15.0
ANALYZER_OVERROUND = 1.10

# Treat a race as started this long before its scheduled time, so a rescore
# that takes a minute to finish can never land after the jump.
START_SAFETY_MARGIN = timedelta(minutes=2)

SCORE_REASONS = ('upload', 'scratching', 'pace_bias', 'rail', 'ml')

# A race's results are "incomplete" when fewer than this share of its starters
# have a recorded finish, or when the recorded SPs add up to under this market %.
RESULTS_MIN_SHARE_OF_STARTERS = 0.8
RESULTS_MIN_MARKET_PCT = 95.0


# ─────────────────────────────────────────────
# Has the race started?
# ─────────────────────────────────────────────
def melbourne_today(now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    return now_utc.astimezone(MELBOURNE_TZ).date()


def meeting_date(meeting):
    """The meeting's date, from meetings.date or the 'YYMMDD_Track' name."""
    if getattr(meeting, 'date', None):
        return meeting.date
    name = str(getattr(meeting, 'meeting_name', '') or '')
    if len(name) >= 6 and name[:6].isdigit():
        try:
            return date(2000 + int(name[:2]), int(name[2:4]), int(name[4:6]))
        except ValueError:
            return None
    return None


_START_TIME_RE = re.compile(r'(\d{1,2}):(\d{2})\s*([AaPp][Mm])?')


def parse_csv_start_time(value):
    """'14:35', '2:35 PM' -> time(14, 35). None if unreadable."""
    match = _START_TIME_RE.search(str(value or ''))
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    meridiem = (match.group(3) or '').lower()
    if meridiem == 'pm' and hour < 12:
        hour += 12
    elif meridiem == 'am' and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def csv_start_time_utc(race, meeting):
    """Scheduled start from the PuntingForm CSV 'start time' column.

    The column carries no time zone. It is read as Melbourne time. Every
    Australian track is on Melbourne time or behind it, so this can only make a
    race look like it started earlier than it did. That errs towards blocking
    a rescore, never towards allowing a late one.
    """
    day = meeting_date(meeting)
    if day is None:
        return None
    for horse in getattr(race, 'horses', None) or []:
        csv_data = horse.csv_data if isinstance(horse.csv_data, dict) else {}
        start = parse_csv_start_time(csv_data.get('start time'))
        if start:
            local = datetime.combine(day, start, tzinfo=MELBOURNE_TZ)
            return local.astimezone(timezone.utc).replace(tzinfo=None)
    return None


def _ladbrokes_start_time_utc(race, meeting):
    """Scheduled start in UTC from Ladbrokes, or None. Never raises."""
    try:
        from ladbrokes import match_race_info, _parse_ladbrokes_utc
        day = meeting_date(meeting)
        name = str(getattr(meeting, 'meeting_name', '') or '')
        track = name[7:].strip() if len(name) > 7 and name[6] == '_' else (meeting.track or '')
        if not day or not track:
            return None
        info = match_race_info(track, day.strftime('%Y-%m-%d'), race.race_number)
        parsed = _parse_ladbrokes_utc((info or {}).get('start_time') or '')
        if parsed is None:
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except Exception as exc:  # network or parsing: fall back to the CSV
        log.warning("Ladbrokes start time lookup failed for race %s: %s", getattr(race, 'id', None), exc)
        return None


def race_start_utc(race, meeting, allow_network=True):
    """Best known scheduled start (naive UTC). Stored on the race once found."""
    stored = getattr(race, 'start_time_utc', None)
    if stored:
        return stored
    start = _ladbrokes_start_time_utc(race, meeting) if allow_network else None
    if start is None:
        start = csv_start_time_utc(race, meeting)
    if start is not None and hasattr(race, 'start_time_utc'):
        race.start_time_utc = start
    return start


def race_has_started(race, meeting=None, now_utc=None, allow_network=True):
    """(started, reason). Unknown counts as started: we never guess in favour of a late rescore."""
    meeting = meeting or race.meeting
    now_utc = now_utc or datetime.now(timezone.utc)
    day = meeting_date(meeting)
    if day is None:
        return True, 'meeting date unknown'
    today = melbourne_today(now_utc)
    if day < today:
        return True, 'meeting date has passed'
    if day > today:
        return False, 'meeting is in the future'
    start = race_start_utc(race, meeting, allow_network=allow_network)
    if start is None:
        return True, 'start time unknown'
    now_naive = now_utc.astimezone(timezone.utc).replace(tzinfo=None)
    if now_naive >= start - START_SAFETY_MARGIN:
        return True, f'started (scheduled {start:%H:%M} UTC)'
    return False, f'starts {start:%H:%M} UTC'


# ─────────────────────────────────────────────
# Scores -> prices
# ─────────────────────────────────────────────
def parse_price(value):
    """'$3.60' -> 3.6. None if blank or unreadable."""
    if value is None:
        return None
    try:
        price = float(str(value).replace('$', '').strip())
    except ValueError:
        return None
    return price if price > 0 else None


def reprice_race(predictions):
    """Rewrite win_probability and predicted_odds from the current scores.

    `predictions` is every active (not scratched) runner's Prediction in one
    race. Same maths as analyzer.js calculateTrueOdds, run on the final score
    after rail and pace bias, so the price always follows the score.
    """
    preds = [p for p in predictions if p is not None and p.score is not None]
    if not preds:
        return
    top = max(p.score for p in preds)
    # Subtracting the top score keeps exp() from overflowing; the result is identical.
    weights = [math.exp((p.score - top) / ANALYZER_STEEPNESS_K) for p in preds]
    total = sum(weights)
    raw = [weight / total for weight in weights]
    for pred, probability in zip(preds, raw):
        pred.win_probability = f"{probability * ANALYZER_OVERROUND * 100:.1f}%"
        pred.predicted_odds = f"${1 / (probability * ANALYZER_OVERROUND):.2f}"
    _apply_calibration(preds, raw)


_CALIBRATION_CACHE = {'at': 0.0, 'value': None}
_CALIBRATION_TTL_SECONDS = 600


def _latest_calibration():
    """(method, params) from the newest calibration run, cached for 10 minutes. None if unavailable."""
    import time
    now = time.time()
    if now - _CALIBRATION_CACHE['at'] < _CALIBRATION_TTL_SECONDS:
        return _CALIBRATION_CACHE['value']
    value = None
    try:
        from models import db
        from calibrate import latest_params
        with db.engine.connect() as conn:
            value = latest_params(conn)
    except Exception as exc:  # no table yet, no app context, etc.
        log.debug('No calibration available: %s', exc)
    _CALIBRATION_CACHE.update(at=now, value=value)
    return value


def _apply_calibration(preds, raw):
    """Set calibrated_win_prob / calibrated_price from the latest calibration run. Never raises."""
    if not hasattr(preds[0], 'calibrated_win_prob'):
        return
    latest = _latest_calibration()
    if latest is None:
        return
    try:
        from calibrate import make_predictor
        probs = make_predictor(*latest)(raw)
    except Exception as exc:
        log.warning('Calibration failed for a race: %s', exc)
        return
    for pred, p in zip(preds, probs):
        pred.calibrated_win_prob = round(p, 6)
        pred.calibrated_price = round(1 / p, 2)


# ─────────────────────────────────────────────
# score_history
# ─────────────────────────────────────────────
def snapshot(pred):
    """The values score_history tracks, read before a change."""
    if pred is None:
        return {'score': None, 'price': None, 'ml_score': None}
    return {
        'score': pred.score,
        'price': parse_price(pred.predicted_odds),
        'ml_score': pred.ml_score,
    }


def _changed(a, b):
    if a is None or b is None:
        return a is not b
    return abs(float(a) - float(b)) > 1e-9


def log_score_change(session, pred, race_id, reason, before, detail=None):
    """Add a score_history row if score, price or ML score moved. Returns True if logged."""
    from models import ScoreHistory

    if reason not in SCORE_REASONS:
        raise ValueError(f'unknown score change reason: {reason}')
    after = snapshot(pred)
    if not any(_changed(before[k], after[k]) for k in after):
        return False
    session.add(ScoreHistory(
        horse_id=pred.horse_id,
        race_id=race_id,
        changed_at=datetime.utcnow(),
        reason=reason,
        old_score=before['score'],
        new_score=after['score'],
        old_price=before['price'],
        new_price=after['price'],
        old_ml_score=before['ml_score'],
        new_ml_score=after['ml_score'],
        detail=detail,
    ))
    return True


# ─────────────────────────────────────────────
# Incomplete results
# ─────────────────────────────────────────────
# One row per race. With Betfair data loaded, a race is also flagged when
# Betfair disagrees with our results: Betfair's winner is not our finish 1, a
# runner that started on Betfair is finish 0 (scratched) in ours, or a runner
# with a finish has no SP.
_PER_RACE_SQL = """
    SELECT h.race_id,
           COUNT(*) FILTER (
               WHERE NOT COALESCE(h.is_scratched, FALSE)
                 AND COALESCE(r.finish_position, -1) <> 0
           ) AS starters,
           COUNT(*) FILTER (WHERE r.finish_position >= 1) AS resulted,
           COALESCE(SUM(1.0 / r.sp) FILTER (WHERE r.finish_position >= 1 AND r.sp > 1), 0) * 100 AS market_pct,
           COUNT(*) FILTER (WHERE r.finish_position >= 1 AND (r.sp IS NULL OR r.sp <= 1)) AS blank_sp,
           {betfair_columns}
    FROM horses h
    LEFT JOIN results r ON r.horse_id = h.id
    {betfair_join}
    {where}
    GROUP BY h.race_id
"""
_BETFAIR_COLUMNS = """
           COUNT(*) FILTER (WHERE bp.won AND COALESCE(r.finish_position, -1) <> 1) AS bf_wrong_winner,
           COUNT(*) FILTER (WHERE bp.id IS NOT NULL AND r.finish_position = 0) AS bf_ran_but_scratched,
           -- Runners Betfair says started in this race that we have no horse for
           -- (mostly rows the old CSV reader dropped): our field is incomplete.
           MAX((SELECT COUNT(*) FROM betfair_prices bx
                WHERE bx.race_id = h.race_id AND bx.horse_id IS NULL)) AS bf_missing_runners
"""
_NO_BETFAIR_COLUMNS = """
           0 AS bf_wrong_winner,
           0 AS bf_ran_but_scratched,
           0 AS bf_missing_runners
"""
_INCOMPLETE_EXPR = """(
        (per_race.resulted > 0
         AND (per_race.resulted < per_race.starters * :min_share
              OR per_race.market_pct < :min_market_pct
              OR per_race.blank_sp > 0))
        OR per_race.bf_wrong_winner > 0
        OR per_race.bf_ran_but_scratched > 0
        OR per_race.bf_missing_runners > 0
    )"""
RESULTS_INCOMPLETE_SQL = """
WITH per_race AS ({per_race})
UPDATE races
SET results_incomplete = """ + _INCOMPLETE_EXPR + """
FROM per_race
WHERE races.id = per_race.race_id
  AND races.results_incomplete IS DISTINCT FROM """ + _INCOMPLETE_EXPR + """
"""


def _has_betfair_prices(conn):
    return conn.execute(text("SELECT to_regclass('betfair_prices') IS NOT NULL")).scalar()


def refresh_results_incomplete_flags(conn, race_ids=None):
    """Recompute races.results_incomplete. All races, or just `race_ids`. Returns rows changed.

    A race is flagged when it has some results but fewer than 80% of its
    starters have a finish, the recorded SPs sum to under 95%, or a runner
    with a finish has no SP. Once Betfair data is loaded (bsp_import.py) it is
    also flagged when Betfair disagrees with us: a different winner, or a
    runner that started on Betfair recorded as scratched. Races with no
    results and no Betfair disagreement are left unflagged.
    """
    params = {
        'min_share': RESULTS_MIN_SHARE_OF_STARTERS,
        'min_market_pct': RESULTS_MIN_MARKET_PCT,
    }
    where = ''
    if race_ids is not None:
        race_ids = [int(r) for r in race_ids]
        if not race_ids:
            return 0
        where = 'WHERE h.race_id = ANY(:race_ids)'
        params['race_ids'] = race_ids
    if _has_betfair_prices(conn):
        per_race = _PER_RACE_SQL.format(betfair_columns=_BETFAIR_COLUMNS,
                                        betfair_join='LEFT JOIN betfair_prices bp ON bp.horse_id = h.id',
                                        where=where)
    else:
        per_race = _PER_RACE_SQL.format(betfair_columns=_NO_BETFAIR_COLUMNS, betfair_join='', where=where)
    result = conn.execute(text(RESULTS_INCOMPLETE_SQL.format(per_race=per_race)), params)
    return result.rowcount or 0


def refresh_meeting_results_flags(session, meeting_id):
    """Recompute results_incomplete for one meeting's races, inside the caller's transaction.

    Call after results are saved, before commit. Postgres only (the SQL uses
    FILTER and ANY); on other databases it does nothing and returns 0.
    """
    conn = session.connection()
    if conn.dialect.name != 'postgresql':
        return 0
    race_ids = [row[0] for row in conn.execute(
        text('SELECT id FROM races WHERE meeting_id = :meeting_id'), {'meeting_id': meeting_id}
    )]
    session.flush()
    return refresh_results_incomplete_flags(conn, race_ids)


# ─────────────────────────────────────────────
# ML results only count when scored before the jump
# ─────────────────────────────────────────────
def ml_pre_race_sql(pred_alias='p', race_alias='r'):
    """SQL condition: this ML score was written before the race's scheduled start.

    Rows with no ml_scored_at (everything scored before the timestamp existed)
    or no known start time do not count: there is no proof they are pre-race.
    """
    p, r = pred_alias, race_alias
    return (f"({p}.ml_scored_at IS NOT NULL AND {r}.start_time_utc IS NOT NULL "
            f"AND {p}.ml_scored_at < {r}.start_time_utc)")


def ml_pre_race_clause(prediction_model, race_model):
    """The same condition as ml_pre_race_sql, for ORM queries that join Race."""
    from sqlalchemy import and_
    return and_(
        prediction_model.ml_scored_at.isnot(None),
        race_model.start_time_utc.isnot(None),
        prediction_model.ml_scored_at < race_model.start_time_utc,
    )


def backfill_race_start_times(conn):
    """Fill races.start_time_utc from the CSV 'start time' where it is missing. Returns rows filled.

    Uses the same Melbourne-time reading as csv_start_time_utc, which can only
    place a race earlier than it really ran, never later.
    """
    rows = conn.execute(text("""
        SELECT DISTINCT ON (r.id) r.id, m.date, m.meeting_name, h.csv_data->>'start time' AS start_time
        FROM races r
        JOIN meetings m ON m.id = r.meeting_id
        JOIN horses h ON h.race_id = r.id
        WHERE r.start_time_utc IS NULL
          AND COALESCE(h.csv_data->>'start time', '') <> ''
        ORDER BY r.id, h.id
    """)).fetchall()
    updates = []
    for race_id, day, meeting_name, start_text in rows:
        meeting = type('M', (), {'date': day, 'meeting_name': meeting_name})()
        race = type('R', (), {'horses': [type('H', (), {'csv_data': {'start time': start_text}})()]})()
        start = csv_start_time_utc(race, meeting)
        if start is not None:
            updates.append({'id': race_id, 'start': start})
    if updates:
        conn.execute(text('UPDATE races SET start_time_utc = :start WHERE id = :id AND start_time_utc IS NULL'), updates)
    return len(updates)


# ─────────────────────────────────────────────
# One result row per horse
# ─────────────────────────────────────────────
DEDUPE_LOCK_KEY = 731_204_455  # any constant; serialises the two web workers


def dedupe_results(conn):
    """Keep one results row per horse and enforce it with a unique constraint.

    Postgres only. Runs under an advisory lock so both web workers can call it
    at startup. For each horse with several rows, keeps the row that agrees
    with Betfair's result (finish 1 for Betfair's winner, finish 2-5 for a
    loser) when there is one, otherwise the most recently recorded. Every
    removed row is copied to results_dedupe_backup first, with the id of the
    row that was kept, so the clean-up can be undone. Returns rows removed.
    """
    conn.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': DEDUPE_LOCK_KEY})
    has_constraint = conn.execute(text("""
        SELECT 1 FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = 'results'::regclass AND i.indisunique AND i.indnatts = 1
          AND a.attname = 'horse_id'
    """)).scalar()
    if has_constraint:
        return 0

    if _has_betfair_prices(conn):
        betfair_select = ', bf.won'
        betfair_join = """LEFT JOIN LATERAL (
                SELECT won FROM betfair_prices WHERE horse_id = r.horse_id ORDER BY id LIMIT 1
            ) bf ON TRUE"""
        # Rows that agree with Betfair sort first.
        betfair_order = """CASE
                WHEN d.won IS NULL THEN 1
                WHEN d.won AND d.finish_position = 1 THEN 0
                WHEN NOT d.won AND d.finish_position BETWEEN 2 AND 5 THEN 0
                ELSE 1
            END,"""
    else:
        betfair_select = betfair_join = betfair_order = ''

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS results_dedupe_backup (
            id INTEGER, horse_id INTEGER, finish_position INTEGER, sp FLOAT, bsp FLOAT,
            recorded_at TIMESTAMP, recorded_by INTEGER,
            kept_result_id INTEGER, removed_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(text(
        'CREATE TEMP TABLE results_dedupe_plan (id INTEGER, kept_id INTEGER, rn BIGINT) ON COMMIT DROP'
    ))
    conn.execute(text(f"""
        INSERT INTO results_dedupe_plan (id, kept_id, rn)
        SELECT d.id, FIRST_VALUE(d.id) OVER w, ROW_NUMBER() OVER w
        FROM (
            SELECT r.id, r.horse_id, r.finish_position, r.recorded_at {betfair_select}
            FROM results r
            {betfair_join}
            WHERE r.horse_id IN (SELECT horse_id FROM results GROUP BY horse_id HAVING COUNT(*) > 1)
        ) d
        WINDOW w AS (PARTITION BY d.horse_id ORDER BY {betfair_order}
                     d.recorded_at DESC NULLS LAST, d.id DESC)
    """))
    conn.execute(text("""
        INSERT INTO results_dedupe_backup
            (id, horse_id, finish_position, sp, bsp, recorded_at, recorded_by, kept_result_id)
        SELECT r.id, r.horse_id, r.finish_position, r.sp, r.bsp, r.recorded_at, r.recorded_by, p.kept_id
        FROM results r JOIN results_dedupe_plan p ON p.id = r.id
        WHERE p.rn > 1
    """))
    removed = conn.execute(text(
        'DELETE FROM results WHERE id IN (SELECT id FROM results_dedupe_plan WHERE rn > 1)'
    )).rowcount
    conn.execute(text('ALTER TABLE results ADD CONSTRAINT uq_results_horse_id UNIQUE (horse_id)'))
    return removed or 0
