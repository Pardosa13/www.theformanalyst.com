"""Phase 6: run each racing day without anyone pressing a button.

Runs every 30 minutes (Railway cron). Each pass brings today's meetings up to
date and is safe to repeat:

  1. Import any of today's PuntingForm meetings we do not have yet, with the
     track rating and rail from PuntingForm's Conditions feed. If Conditions
     has nothing for a meeting, use the meeting's expected rating and rail
     (/v2/form/meeting), else Good / true rail, mark the meeting provisional
     and send an alert.
  2. Score each new meeting with the ML model.
  3. For meetings already imported: when the scratchings, rating or rail have
     changed, rescore the races that have NOT started (the same code as the
     Update Scratchings button) and rescore ML. Started races are never
     touched. A meeting whose rating was set by hand (condition_source =
     'manual') keeps it.
  4. Score ML for any race still to run that has no ML scores yet.
  5. Otherwise re-price ML value edge and Kelly stake against the newest live
     odds, so the edge stored at the jump is at most one pass old.
  6. Record Best Bets routes (Maiden Agreement, Proven Edge) for every race
     still to run, so the route chart counts every race.

Track rating and rail used for every race are stored on the race
(races.track_condition, scored_rail_position, scored_at).

Run:  python daily_run.py            # only acts between 07:00 and 23:30 Melbourne
      python daily_run.py --force    # any time
      python daily_run.py --date 2026-10-16
"""
import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

import requests

log = logging.getLogger('daily_run')

AUSTRALIAN_STATES = set(
    os.environ.get('DAILY_RUN_STATES', 'NSW,VIC,QLD,SA,WA,TAS,ACT,NT').split(',')
)
FIRST_HOUR = int(os.environ.get('DAILY_RUN_FIRST_HOUR', '7'))     # Melbourne
LAST_HOUR = int(os.environ.get('DAILY_RUN_LAST_HOUR', '23'))      # runs until HH:59
CONDITIONS_URL = 'https://api.puntingform.com.au/v2/Updates/Conditions'
FORM_MEETING_URL = 'https://api.puntingform.com.au/v2/form/meeting'
V2_SCRATCHINGS_URL = 'https://api.puntingform.com.au/v2/Updates/Scratchings'


# ─────────────────────────────────────────────
# Track rating and rail
# ─────────────────────────────────────────────
def rating_word(text_value=None, number=None, surface=None):
    """PuntingForm rating -> one of firm / good / soft / heavy / synthetic (what the analyzer takes)."""
    blob = ' '.join(str(v) for v in (text_value, surface) if v).lower()
    if any(word in blob for word in ('synth', 'poly', 'tapeta', 'all weather')):
        return 'synthetic'
    try:
        n = int(number) if number not in (None, '') else None
    except (TypeError, ValueError):
        n = None
    if n is None:
        match = re.search(r'(\d+)', blob)
        n = int(match.group(1)) if match else None
    if n is not None and 1 <= n <= 10:
        return 'firm' if n <= 2 else 'good' if n <= 4 else 'soft' if n <= 7 else 'heavy'
    for word in ('heavy', 'soft', 'good', 'firm'):
        if word in blob:
            return word
    return None


def rail_metres(value):
    """'True', '+3m', 'Out 3m entire circuit', 3 -> metres out (int), or None if unreadable."""
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text_value = str(value).lower()
    if 'true' in text_value:
        return 0
    match = re.search(r'(\d+(?:\.\d+)?)', text_value)
    return int(float(match.group(1))) if match else None


def fetch_conditions(api_key):
    """{meeting_id(str): item, 'track:<name>': item} from the Conditions feed. {} on failure."""
    try:
        response = requests.get(CONDITIONS_URL, params={'apiKey': api_key}, timeout=30,
                                headers={'accept': 'application/json'})
        if not response.ok:
            log.warning('Conditions feed HTTP %s', response.status_code)
            return {}
        payload = response.json()
        items = payload.get('payLoad') if isinstance(payload, dict) else payload
    except Exception as exc:
        log.warning('Conditions feed failed: %s', exc)
        return {}
    by_key = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if item.get('meetingId') not in (None, ''):
            by_key[str(item['meetingId'])] = item
        if item.get('track'):
            by_key[f"track:{str(item['track']).strip().lower()}"] = item
    return by_key


def conditions_for(conditions, pf_meeting_id, track):
    return conditions.get(str(pf_meeting_id)) or conditions.get(f'track:{str(track).strip().lower()}')


def is_abandoned(item):
    value = (item or {}).get('abandonded', (item or {}).get('abandoned'))
    return value is True or str(value).lower() in ('true', 'yes', '1')


def expected_from_form(api_key, pf_meeting_id):
    """(rating word, rail metres) from /v2/form/meeting, either may be None."""
    try:
        response = requests.get(FORM_MEETING_URL, params={'meetingId': pf_meeting_id, 'apiKey': api_key},
                                timeout=30, headers={'accept': 'application/json'})
        if not response.ok:
            return None, None
        payload = (response.json() or {}).get('payLoad') or {}
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        log.info('Form meeting %s: expectedCondition=%r railPosition=%r', pf_meeting_id,
                 payload.get('expectedCondition'), payload.get('railPosition'))
        return rating_word(payload.get('expectedCondition')), rail_metres(payload.get('railPosition'))
    except Exception as exc:
        log.warning('Form meeting %s failed: %s', pf_meeting_id, exc)
        return None, None


def resolve_conditions(item, api_key, pf_meeting_id):
    """(rating, rail, source). source: conditions / expected / default."""
    rating = rail = None
    if item:
        rating = rating_word(item.get('trackCondition'), item.get('trackConditionNumber'), item.get('surface'))
        rail = rail_metres(item.get('rail'))
    if rating is not None and rail is not None:
        return rating, rail, 'conditions'
    exp_rating, exp_rail = expected_from_form(api_key, pf_meeting_id)
    rating = rating or exp_rating
    rail = rail if rail is not None else exp_rail
    if rating is not None and rail is not None:
        return rating, rail, 'expected'
    return rating or 'good', rail if rail is not None else 0, 'default'


# ─────────────────────────────────────────────
# Alerts
# ─────────────────────────────────────────────
def alert(message, level='warning'):
    """Private Telegram message to the admin (TELEGRAM_ADMIN_CHAT_ID). Logged either way.

    Telegram's reply is logged, so a failure says why (no token, bad token,
    chat not found). The token itself is never logged.
    """
    getattr(log, level)('ALERT: %s', message)
    token, chat_id = os.environ.get('TELEGRAM_BOT_TOKEN'), os.environ.get('TELEGRAM_ADMIN_CHAT_ID')
    if not token or token.startswith('${{'):
        log.warning('Telegram: no usable TELEGRAM_BOT_TOKEN on this service, alert not sent')
        return False
    if not chat_id:
        log.warning('Telegram: TELEGRAM_ADMIN_CHAT_ID not set, alert not sent')
        return False
    try:
        response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage',
                                 json={'chat_id': chat_id, 'text': f'Form Analyst: {message}'}, timeout=15)
    except Exception as exc:
        # The exception text can contain the URL, and the URL contains the token.
        log.warning('Telegram alert failed: %s', type(exc).__name__)
        return False
    if response.ok:
        log.info('Telegram: alert sent')
        return True
    try:
        reason = response.json().get('description')
    except ValueError:
        reason = response.text[:200]
    log.warning('Telegram: alert NOT sent, HTTP %s: %s', response.status_code, reason)
    return False


def morning_summary(day, summary, db):
    """One Telegram message after the pass that imports the day's meetings."""
    from models import Meeting
    names = summary['imported']
    provisional = [m.meeting_name for m in Meeting.query.filter(Meeting.meeting_name.in_(names)).all()
                   if m.provisional] if names else []
    lines = [f'{day:%a %d %b}: {len(names)} meeting(s) imported and scored automatically.',
             f'ML scores written: {summary["ml_scored"]}.']
    if summary.get('ml_note'):
        lines.append(summary['ml_note'])
    if provisional:
        lines.append('PROVISIONAL (check rating/rail): ' + ', '.join(provisional))
    if summary['errors']:
        lines.append(f'Errors: {summary["errors"]} (see Railway logs).')
    return alert(' '.join(lines), level='info')


# ─────────────────────────────────────────────
# The pass
# ─────────────────────────────────────────────
def scratch_signature(v1_data, v2_items, track, extract_v1, extract_v2):
    """A short fingerprint of today's official scratchings for one track."""
    try:
        v1_set, _, v1_ok = extract_v1(v1_data, track)
    except Exception:
        v1_set, v1_ok = set(), False
    try:
        v2_set, _ = extract_v2(v2_items, track)
    except Exception:
        v2_set = set()
    blob = json.dumps([v1_ok, sorted(map(str, v1_set)), sorted(map(str, v2_set))])
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def ensure_state_table(db):
    from sqlalchemy import text
    with db.engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS daily_run_state (
                meeting_id INTEGER PRIMARY KEY,
                scratch_signature VARCHAR(32),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """))


def get_signature(db, meeting_id):
    from sqlalchemy import text
    return db.session.execute(text('SELECT scratch_signature FROM daily_run_state WHERE meeting_id = :m'),
                              {'m': meeting_id}).scalar()


def set_signature(db, meeting_id, signature):
    from sqlalchemy import text
    db.session.execute(text("""
        INSERT INTO daily_run_state (meeting_id, scratch_signature, updated_at) VALUES (:m, :s, NOW())
        ON CONFLICT (meeting_id) DO UPDATE SET scratch_signature = EXCLUDED.scratch_signature, updated_at = NOW()
    """), {'m': meeting_id, 's': signature})
    db.session.commit()


def open_races(meeting, race_has_started):
    return [r for r in meeting.races if not race_has_started(r, meeting)[0]]


def needs_ml(db, races):
    """True when a race still to run has active runners without an ML score."""
    from models import Horse, Prediction
    race_ids = [r.id for r in races]
    if not race_ids:
        return False
    return db.session.query(Prediction.id).join(Horse, Horse.id == Prediction.horse_id).filter(
        Horse.race_id.in_(race_ids), Horse.is_scratched.isnot(True), Prediction.ml_score.is_(None),
    ).first() is not None


def score_ml(db, meeting, summary):
    from ml_shadow_routes import _score_meeting_ml
    try:
        result = _score_meeting_ml(db, meeting.id)
    except Exception as exc:
        db.session.rollback()
        alert(f'ML scoring failed for {meeting.meeting_name}: {exc}')
        summary['errors'] += 1
        return
    if result.get('success'):
        summary['ml_scored'] += result.get('scored', 0)
    elif result.get('no_active_champion'):
        summary['ml_note'] = 'no active ML champion model: nothing scored'
    else:
        log.warning('ML not scored for %s: %s', meeting.meeting_name, result.get('reason'))


def reprice_ml(db, meeting):
    """Refresh value edge and Kelly stake from the newest live prices.

    Scoring prices a meeting once, often hours before the jump, against
    whatever odds existed then. Each pass re-prices it so the stored edge is at
    most one pass old when a race starts. Never fatal: a meeting that cannot be
    priced keeps its last edge.
    """
    from ml_shadow_routes import _reprice_meeting_market
    updated, error = _reprice_meeting_market(db, meeting.id)
    if error:
        log.warning('ML re-price failed for %s: %s', meeting.meeting_name, error)
    else:
        log.info('ML re-priced %s: %s runner(s)', meeting.meeting_name, updated)


def record_routes(db, races):
    """Save Best Bets routes (Maiden Agreement, Proven Edge) for races still to
    run, so the ML Data route chart counts every race, not only the ones
    someone opened the Best Bets page for. Never fatal."""
    from app import record_best_bet_routes
    try:
        recorded = record_best_bet_routes(races)
        db.session.commit()
        if recorded:
            log.info('Best Bets routes recorded: %s runner(s)', recorded)
    except Exception:
        db.session.rollback()
        log.exception('Recording Best Bets routes failed')


def run_pass(app, day, summary):
    from app import (_extract_v1_scratchings_for_track, _extract_v2_scratched_set,
                     _update_scratchings_impl, db, import_meeting_from_pf, pf_service)
    from models import Meeting, User
    from scoring_integrity import race_has_started

    ensure_state_table(db)
    day_str = day.isoformat()
    pf_meetings = [m for m in pf_service.get_meetings_list(day_str).get('meetings', [])
                   if m.get('state') in AUSTRALIAN_STATES]
    log.info('%s: %s Australian meeting(s) on PuntingForm', day_str, len(pf_meetings))
    if not pf_meetings:
        return

    conditions = fetch_conditions(pf_service.api_key)
    log.info('Conditions feed: %s entries', len({id(v) for v in conditions.values()}))
    try:
        v1_scratch = pf_service.get_scratchings()
    except Exception as exc:
        log.warning('V1 scratchings failed: %s', exc)
        v1_scratch = []
    try:
        response = requests.get(V2_SCRATCHINGS_URL, params={'apiKey': pf_service.api_key}, timeout=30,
                                headers={'accept': 'application/json'})
        v2_payload = response.json() if response.ok else []
        v2_items = v2_payload.get('payLoad') if isinstance(v2_payload, dict) else v2_payload
    except Exception as exc:
        log.warning('V2 scratchings failed: %s', exc)
        v2_items = []

    admin = User.query.filter_by(is_admin=True).order_by(User.id).first()
    prefix = day.strftime('%y%m%d')

    for pf in pf_meetings:
        track, pf_id = pf['track_name'], pf['meeting_id']
        name = f'{prefix}_{track}'
        item = conditions_for(conditions, pf_id, track)
        try:
            meeting = Meeting.query.filter_by(meeting_name=name, puntingform_id=track).first()
            if meeting is None:
                if is_abandoned(item):
                    log.info('%s abandoned: not imported', name)
                    continue
                rating, rail, source = resolve_conditions(item, pf_service.api_key, pf_id)
                log.info('Importing %s: rating %s, rail %sm (from %s). Conditions item: %s',
                         name, rating, rail, source, json.dumps(item, default=str)[:400] if item else None)
                meeting = import_meeting_from_pf(pf_id, day_str, rating, rail, user_id=admin.id if admin else None)
                meeting.provisional = source != 'conditions'
                meeting.condition_source = source
                db.session.commit()
                summary['imported'].append(name)
                set_signature(db, meeting.id, scratch_signature(
                    v1_scratch, v2_items, track, _extract_v1_scratchings_for_track, _extract_v2_scratched_set))
                score_ml(db, meeting, summary)
                record_routes(db, open_races(meeting, race_has_started))
                if meeting.provisional:
                    alert(f'{name} imported as PROVISIONAL: rating {rating}, rail {rail}m from {source}. '
                          'Check it and fix on the dashboard if needed.')
                continue

            races_to_run = open_races(meeting, race_has_started)
            db.session.commit()  # keeps start times looked up just now
            if not races_to_run:
                continue

            new_rating = None
            reasons = []
            if meeting.condition_source != 'manual' and item and not is_abandoned(item):
                rating = rating_word(item.get('trackCondition'), item.get('trackConditionNumber'), item.get('surface'))
                rail = rail_metres(item.get('rail'))
                current_rating = next((r.track_condition for r in races_to_run if r.track_condition), None)
                if rating and rating != current_rating:
                    new_rating = rating
                    reasons.append(f'rating {current_rating} -> {rating}')
                if rail is not None and rail != (meeting.rail_position or 0):
                    reasons.append(f'rail {meeting.rail_position or 0}m -> {rail}m')
                    meeting.rail_position = rail
                    new_rating = new_rating or current_rating or rating
                if meeting.provisional and rating and rail is not None:
                    meeting.provisional = False
                    meeting.condition_source = 'conditions'
                    reasons.append('confirmed by Conditions feed')
                db.session.commit()

            signature = scratch_signature(v1_scratch, v2_items, track,
                                          _extract_v1_scratchings_for_track, _extract_v2_scratched_set)
            if signature != get_signature(db, meeting.id):
                reasons.append('scratchings changed')
            if new_rating or 'scratchings changed' in reasons:
                with app.test_request_context():
                    response = _update_scratchings_impl(meeting.id, track_condition=new_rating)
                body = response[0].get_json() if isinstance(response, tuple) else response.get_json()
                if not body.get('success'):
                    alert(f'Rescore failed for {name}: {body.get("error")}')
                    summary['errors'] += 1
                    continue
                set_signature(db, meeting.id, signature)
                summary['rescored'].append(f'{name} ({", ".join(reasons)}; {body.get("races_updated")} races)')
                score_ml(db, meeting, summary)
            elif needs_ml(db, races_to_run):
                score_ml(db, meeting, summary)
            else:
                reprice_ml(db, meeting)
            record_routes(db, races_to_run)
        except Exception as exc:
            db.session.rollback()
            log.exception('Daily run failed for %s', name)
            alert(f'Daily run failed for {name}: {exc}')
            summary['errors'] += 1


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', stream=sys.stdout)
    parser = argparse.ArgumentParser(description='Bring today\'s meetings up to date.')
    parser.add_argument('--force', action='store_true', help='Run outside racing hours.')
    parser.add_argument('--date', help='Meeting date YYYY-MM-DD (default: today, Melbourne).')
    args = parser.parse_args(argv)

    from scoring_integrity import MELBOURNE_TZ
    now = datetime.now(timezone.utc).astimezone(MELBOURNE_TZ)
    if not args.force and not (FIRST_HOUR <= now.hour <= LAST_HOUR):
        log.info('Outside racing hours (%s Melbourne): nothing to do', now.strftime('%H:%M'))
        return
    day = datetime.strptime(args.date, '%Y-%m-%d').date() if args.date else now.date()

    from app import app
    summary = {'imported': [], 'rescored': [], 'ml_scored': 0, 'errors': 0, 'ml_note': None}
    with app.app_context():
        run_pass(app, day, summary)
        if summary['imported']:
            from app import db
            morning_summary(day, summary, db)
    log.info('SUMMARY %s: imported %s %s | rescored %s %s | ML scores written %s | errors %s%s',
             day, len(summary['imported']), summary['imported'], len(summary['rescored']), summary['rescored'],
             summary['ml_scored'], summary['errors'], f" | {summary['ml_note']}" if summary['ml_note'] else '')
    log.info('Telegram alerts: %s', 'on' if os.environ.get('TELEGRAM_ADMIN_CHAT_ID') else
             'off (set TELEGRAM_ADMIN_CHAT_ID to get them)')


if __name__ == '__main__':
    main()
