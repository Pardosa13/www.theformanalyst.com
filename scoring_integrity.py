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
    for pred, weight in zip(preds, weights):
        probability = weight / total
        pred.win_probability = f"{probability * ANALYZER_OVERROUND * 100:.1f}%"
        pred.predicted_odds = f"${1 / (probability * ANALYZER_OVERROUND):.2f}"


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
RESULTS_INCOMPLETE_SQL = """
WITH per_race AS (
    SELECT h.race_id,
           COUNT(*) FILTER (
               WHERE NOT COALESCE(h.is_scratched, FALSE)
                 AND COALESCE(r.finish_position, -1) <> 0
           ) AS starters,
           COUNT(*) FILTER (WHERE r.finish_position >= 1) AS resulted,
           COALESCE(SUM(1.0 / r.sp) FILTER (WHERE r.finish_position >= 1 AND r.sp > 1), 0) * 100 AS market_pct
    FROM horses h
    LEFT JOIN results r ON r.horse_id = h.id
    {where}
    GROUP BY h.race_id
)
UPDATE races
SET results_incomplete = (
        per_race.resulted > 0
        AND (per_race.resulted < per_race.starters * :min_share
             OR per_race.market_pct < :min_market_pct)
    )
FROM per_race
WHERE races.id = per_race.race_id
  AND races.results_incomplete IS DISTINCT FROM (
        per_race.resulted > 0
        AND (per_race.resulted < per_race.starters * :min_share
             OR per_race.market_pct < :min_market_pct)
  )
"""


def refresh_results_incomplete_flags(conn, race_ids=None):
    """Recompute races.results_incomplete. All races, or just `race_ids`. Returns rows changed.

    A race is flagged when it has some results but fewer than 80% of its
    starters have a finish, or the recorded SPs sum to under 95%. Races with
    no results at all are left unflagged: they are simply not resulted yet.
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
    result = conn.execute(text(RESULTS_INCOMPLETE_SQL.format(where=where)), params)
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
