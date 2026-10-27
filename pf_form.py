"""PuntingForm v2 Form feed: save it before the jump, turn it into ML features.

The v2 Form feed carries far more than the CSV form file: in-run positions for
every past run, stewards reports, gear changes, handicap rating, trainer +
jockey combination record, Betfair/TAB prices and price moves, group record,
original barrier and the top-4 finishers of each past run. PuntingForm only
serves the last 30 days, so it has to be saved as it happens:
daily_run.py calls capture_meeting() once per meeting, and this module's
features are built from the saved copy in training and in live scoring.

Leakage rules:
- Only past runs dated strictly before the meeting are used.
- Fields that change once the race is run (today's position, margin, price,
  career record) are not saved at all.
- Training ignores a race whose saved copy was captured after its start time.
"""
import json
import logging
import math
import re
from datetime import datetime

import numpy as np
import requests

from strike_rate_matching import normalize_name

log = logging.getLogger(__name__)

FORM_URL = 'https://api.puntingform.com.au/v2/form/form'

PF_FORM_FEATURES = [
    'pf_handicap_rating',
    'pf_prep_runs',              # PuntingForm's runs-this-preparation count
    'pf_tj_a2e_career',          # trainer + jockey together: actual vs expected wins
    'pf_tj_strike_rate_career',
    'pf_tj_runners_career',
    'pf_tj_a2e_l100',
    'pf_group_starts',           # Group 1/2/3 starts, wins and placings
    'pf_group_wins',
    'pf_group_places',
    'pf_gear_change',            # any gear change today
    'pf_blinkers_on',            # blinkers (or winkers/visors) on today
    'pf_gear_off',               # a gear item taken off today
    'pf_barrier_moved',          # today's barrier minus original barrier (scratchings)
    'pf_trials_since_last',      # barrier trials since its last race start
    'ir_last_settle_pct',        # in-run position as a fraction of the field (0 = leading)
    'ir_avg_settle_pct_l5',
    'ir_last_m400_pct',
    'ir_avg_m400_pct_l5',
    'ir_last_late_gain',         # places gained from the 400m to the finish, as a fraction of the field
    'ir_avg_late_gain_l5',
    'st_excuse_last',            # stewards reported a hard-luck reason at last start
    'st_excuses_l3',
    'mk_last_bf_vs_sp',          # Betfair price / starting price at last start
    'mk_last_drift',             # starting price / opening price at last start (<1 = backed)
    'mk_avg_drift_l3',
    'ft_net_vs_rivals',          # form ties: times finished ahead of today's rivals minus behind
    'cond_penetrometer',
    'cond_rain_mm',
    'cond_irrigation_24h_mm',
    'cond_weather',              # 0 fine, 1 overcast, 2 showers, 3 rain
    # 2026-10 race-shape inputs (appended so earlier columns keep their order)
    'ir_speed_points_l3',        # Quirin speed points, last 3: settled 1st 4, 2nd 2, 3rd/top quarter 1
    'ir_settle_vs_best',         # last settle % minus its most forward settle % in runs 2-5 (negative = most forward yet)
]

PF_FORM_RELATIVE_COLS = ['ir_avg_settle_pct_l5', 'ir_avg_late_gain_l5', 'pf_handicap_rating']
PF_FORM_RELATIVE_LOWER_IS_BETTER = {'ir_avg_settle_pct_l5'}

_EXCUSE_RE = re.compile(
    r'held up|checked|blocked|crowded|hampered|slowly away|slow away|slow to begin|'
    r'missed the start|dwelt|bumped|steadied|inconvenienced|lost (?:a|its|his|her|both) (?:front |hind )?(?:shoe|plate)|'
    r'bled|lame|wide without cover|(?:three|four|five) wide|over-?raced|raced keenly|pulled hard|'
    r'clipped heels|tightened|squeezed|stumbled|blundered|laid out|lugged|hung (?:in|out)|'
    r'no clear running|unable to obtain clear running|denied a clear run|awkwardly away|'
    r'restrained|carried wide|shifted (?:in|out)|blocked for a run|disappointed for a run',
    re.I,
)


def empty_pf_form_features():
    return {name: np.nan for name in PF_FORM_FEATURES}


# ── Saving ────────────────────────────────────────────────────────────────────

def ensure_table(conn):
    from sqlalchemy import text
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS pf_form_snapshots (
            meeting_id INTEGER PRIMARY KEY,
            pf_meeting_id INTEGER,
            captured_at TIMESTAMP NOT NULL,
            form_json TEXT,
            conditions_json TEXT
        )
    """))


def _trim_form(form):
    # Past starts are already run, so every field is safe to keep. Gear, class,
    # going, weight, barrier, track and jockey per past start give years of
    # history per horse (e.g. "3rd up, blinkers off") from each saved meeting.
    keep = ('meetingDate', 'position', 'starters', 'margin', 'distance', 'inRun', 'flucs',
            'priceSP', 'priceBF', 'priceTAB', 'stewardsReport', 'stewardsReportExt',
            'isBarrierTrial', 'prepRuns', 'gearChanges', 'raceClass', 'trackCondition',
            'trackConditionNumber', 'weight', 'barrier', 'jockeyClaim', 'prizeMoney',
            'officialRaceTime', 'pfRaceTime', 'rail', 'hasSectionalData')
    out = {k: form.get(k) for k in keep if form.get(k) not in (None, '')}
    track = form.get('track') or {}
    if isinstance(track, dict) and track.get('name'):
        out['track'] = track.get('name')
        out['trackState'] = track.get('state')
    jockey = form.get('jockey') or {}
    if isinstance(jockey, dict) and jockey.get('fullName'):
        out['jockey'] = jockey.get('fullName')
    out['top4'] = [[t.get('runnerName'), t.get('position')] for t in (form.get('top4Finishers') or [])]
    return out


def _a2e(record):
    record = record or {}
    return {k: record.get(k) for k in ('a2E', 'strikeRate', 'runners', 'wins')}


def trim_runner(runner):
    """Only the fields features use, and none that change once today's race is run."""
    group = {}
    for g in ('group1Record', 'group2Record', 'group3Record'):
        r = runner.get(g) or {}
        group[g] = [r.get('starts') or 0, r.get('firsts') or 0,
                    (r.get('seconds') or 0) + (r.get('thirds') or 0)]
    return {
        'name': runner.get('name'),
        'tabNo': runner.get('tabNo'),
        'barrier': runner.get('barrier'),
        'originalBarrier': runner.get('originalBarrier'),
        'handicap': runner.get('handicap'),
        'handicapRating': runner.get('handicapRating'),
        'prepRuns': runner.get('prepRuns'),
        'gearChanges': runner.get('gearChanges'),
        'tjCareer': _a2e(runner.get('trainerJockeyA2E_Career')),
        'tjL100': _a2e(runner.get('trainerJockeyA2E_Last100')),
        'group': group,
        'forms': [_trim_form(f) for f in (runner.get('forms') or [])],
    }


def fetch_meeting_form(api_key, pf_meeting_id, race_numbers, timeout=60):
    """{race_no(str): {tabNo(str): trimmed runner}} or None if nothing came back."""
    races = {}
    for race_no in race_numbers:
        try:
            response = requests.get(FORM_URL, params={
                'meetingId': pf_meeting_id, 'raceNumber': race_no, 'runs': 10, 'apiKey': api_key,
            }, headers={'accept': 'application/json'}, timeout=timeout)
            if not response.ok:
                log.warning('PF form feed HTTP %s for meeting %s race %s',
                            response.status_code, pf_meeting_id, race_no)
                continue
            payload = (response.json() or {}).get('payLoad') or []
        except Exception as exc:
            log.warning('PF form feed failed for meeting %s race %s: %s', pf_meeting_id, race_no, exc)
            continue
        runners = {str(r.get('tabNo')): trim_runner(r) for r in payload if r.get('tabNo') is not None}
        if runners:
            races[str(race_no)] = runners
    return races or None


def capture_meeting(db, meeting, pf_meeting_id, api_key, conditions_item=None):
    """Save the form feed for one of our meetings once. Returns True if saved now.

    Safe to call every pass: a meeting already saved is skipped.
    """
    from sqlalchemy import text
    with db.engine.begin() as conn:
        ensure_table(conn)
        exists = conn.execute(text('SELECT 1 FROM pf_form_snapshots WHERE meeting_id = :m'),
                              {'m': meeting.id}).first()
    if exists:
        return False
    race_numbers = sorted({r.race_number for r in meeting.races if r.race_number})
    captured_at = datetime.utcnow()
    races = fetch_meeting_form(api_key, pf_meeting_id, race_numbers)
    if not races:
        return False
    with db.engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO pf_form_snapshots (meeting_id, pf_meeting_id, captured_at, form_json, conditions_json)
            VALUES (:m, :p, :c, :f, :k)
            ON CONFLICT (meeting_id) DO NOTHING
        """), {'m': meeting.id, 'p': int(pf_meeting_id), 'c': captured_at,
               'f': json.dumps(races, separators=(',', ':')),
               'k': json.dumps(conditions_item, default=str) if conditions_item else None})
    log.info('PF form feed saved for %s: %s race(s), %s runner(s)', meeting.meeting_name,
             len(races), sum(len(r) for r in races.values()))
    return True


def load_snapshot(conn_or_session, meeting_id):
    """(races dict, conditions dict, captured_at) for one meeting, or (None, None, None)."""
    from sqlalchemy import text
    # Errors propagate: the caller owns the transaction and must roll back.
    row = conn_or_session.execute(text(
        'SELECT form_json, conditions_json, captured_at FROM pf_form_snapshots WHERE meeting_id = :m'
    ), {'m': meeting_id}).first()
    if not row:
        return None, None, None
    return _loads(row[0]) or {}, _loads(row[1]) or {}, row[2]


def _loads(value):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except Exception:
        return None


# ── Features ──────────────────────────────────────────────────────────────────

def _num(value):
    try:
        number = float(str(value).replace('$', '').replace(',', '').strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _date(value):
    text_value = str(value or '')[:10]
    try:
        return datetime.strptime(text_value, '%Y-%m-%d').date()
    except ValueError:
        return None


def parse_in_run(text_value):
    """'finish,5;settling_down,7;m1200,7;m800,7;m400,7;' -> {'finish': 5, ...}"""
    out = {}
    for part in str(text_value or '').split(';'):
        if ',' not in part:
            continue
        key, value = part.split(',', 1)
        number = _num(value)
        if number and number > 0:
            out[key.strip().lower()] = number
    return out


def parse_flucs(text_value):
    out = {}
    for part in str(text_value or '').split(';'):
        if ',' in part:
            key, value = part.split(',', 1)
            number = _num(value)
            if number and number > 1:
                out[key.strip().lower()] = number
    return out


def _mm_24h(text_value):
    """Millimetres in the last 24 hours from strings like 'nil last 24 hours,
    4mm last 7 days', '8mm last 24 hours', '5mm' or 'nil'. None if unreadable."""
    text_value = str(text_value or '').lower().strip()
    if not text_value:
        return None
    last24 = r'(?:in\s*)?(?:the\s*)?(?:last|past)\s*24'
    if re.search(r'nil\s*' + last24, text_value):
        return 0.0
    match = re.search(r'(\d+(?:\.\d+)?)\s*mm\s*' + last24, text_value)
    if match:
        return float(match.group(1))
    if 'last' in text_value or 'past' in text_value:
        return None  # only other periods given (e.g. 7 days)
    if text_value in ('nil', 'none', '0', 'no'):
        return 0.0
    match = re.search(r'(\d+(?:\.\d+)?)\s*mm', text_value)
    return float(match.group(1)) if match else None


def conditions_features(item):
    out = {k: np.nan for k in ('cond_penetrometer', 'cond_rain_mm', 'cond_irrigation_24h_mm', 'cond_weather')}
    if not item:
        return out
    pen = _num(item.get('penetrometer'))
    if pen is not None and 0 < pen < 20:
        out['cond_penetrometer'] = pen
    rain = _mm_24h(item.get('rainfall'))
    if rain is not None:
        out['cond_rain_mm'] = rain
    irrigation = _mm_24h(item.get('irrigation'))
    if irrigation is not None:
        out['cond_irrigation_24h_mm'] = irrigation
    weather = str(item.get('weather') or '').lower()
    if weather:
        if 'rain' in weather and 'shower' not in weather:
            out['cond_weather'] = 3.0
        elif 'shower' in weather:
            out['cond_weather'] = 2.0
        elif 'overcast' in weather or 'cloud' in weather:
            out['cond_weather'] = 1.0
        elif 'fine' in weather or 'sun' in weather or 'clear' in weather:
            out['cond_weather'] = 0.0
    return out


def quirin_points(settle_position, starters):
    """Professor Quirin's early-speed points for one run, from the settling position.

    Quirin: 4 for leading early, 2 for disputing the lead, 1 for prominent.
    The feed has positions, not lengths, so: settled 1st = 4, 2nd = 2, 3rd or
    in the front quarter of the field = 1, otherwise 0. None if unreadable.
    """
    if not settle_position or settle_position <= 0:
        return None
    if settle_position == 1:
        return 4
    if settle_position == 2:
        return 2
    if settle_position == 3 or (starters and settle_position <= starters / 4):
        return 1
    return 0


def runner_features(runner, meeting_date, today_barrier=None, rival_names=(), conditions=None):
    """One saved runner -> PF_FORM_FEATURES dict."""
    out = empty_pf_form_features()
    out.update(conditions_features(conditions))
    if not runner:
        return out

    out['pf_handicap_rating'] = _num(runner.get('handicapRating')) or _num(runner.get('handicap'))
    if out['pf_handicap_rating'] is None or out['pf_handicap_rating'] <= 0:
        out['pf_handicap_rating'] = np.nan
    prep = _num(runner.get('prepRuns'))
    out['pf_prep_runs'] = prep if prep is not None else np.nan
    for prefix, key in (('career', 'tjCareer'), ('l100', 'tjL100')):
        rec = runner.get(key) or {}
        runners = _num(rec.get('runners'))
        a2e = _num(rec.get('a2E'))
        if runners and runners > 0 and a2e is not None:
            out[f'pf_tj_a2e_{prefix}'] = a2e
            if prefix == 'career':
                out['pf_tj_strike_rate_career'] = _num(rec.get('strikeRate'))
                out['pf_tj_runners_career'] = runners
    group = runner.get('group') or {}
    if group:
        out['pf_group_starts'] = float(sum(v[0] for v in group.values()))
        out['pf_group_wins'] = float(sum(v[1] for v in group.values()))
        out['pf_group_places'] = float(sum(v[2] for v in group.values()))
    gear = str(runner.get('gearChanges') or '').lower()
    out['pf_gear_change'] = 1.0 if gear.strip() else 0.0
    items = [part.strip() for part in gear.split('.') if part.strip()]
    out['pf_blinkers_on'] = 1.0 if any(re.search(r'blinkers|winkers|visor', i) and not re.search(r'\boff\b', i)
                                       for i in items) else 0.0
    out['pf_gear_off'] = 1.0 if any(re.search(r'\boff\b', i) for i in items) else 0.0
    barrier = _num(today_barrier) if today_barrier not in (None, '') else _num(runner.get('barrier'))
    original = _num(runner.get('originalBarrier'))
    if barrier and original:
        out['pf_barrier_moved'] = barrier - original

    forms = []
    for f in runner.get('forms') or []:
        when = _date(f.get('meetingDate'))
        if when is None or (meeting_date is not None and when >= meeting_date):
            continue
        forms.append((when, f))
    forms.sort(key=lambda x: x[0], reverse=True)
    races = [f for _, f in forms if not f.get('isBarrierTrial')]
    trials_since = 0
    for _, f in forms:
        if not f.get('isBarrierTrial'):
            break
        trials_since += 1
    out['pf_trials_since_last'] = float(trials_since) if forms else np.nan

    def pct(position, starters):
        if position is None or not starters or starters <= 1:
            return None
        return (min(position, starters) - 1) / (starters - 1)

    settle, m400, gain, speed_points = [], [], [], []
    for f in races[:5]:
        starters = _num(f.get('starters'))
        in_run = parse_in_run(f.get('inRun'))
        speed_points.append(quirin_points(in_run.get('settling_down'), starters))
        s_pct = pct(in_run.get('settling_down'), starters)
        m_pct = pct(in_run.get('m400'), starters)
        finish = in_run.get('finish') or _num(f.get('position'))
        settle.append(s_pct)
        m400.append(m_pct)
        if in_run.get('m400') and finish and starters and starters > 1:
            gain.append((in_run['m400'] - finish) / (starters - 1))
        else:
            gain.append(None)
    if races:
        out['ir_last_settle_pct'] = settle[0] if settle[0] is not None else np.nan
        out['ir_last_m400_pct'] = m400[0] if m400[0] is not None else np.nan
        out['ir_last_late_gain'] = gain[0] if gain[0] is not None else np.nan
    for name, values in (('ir_avg_settle_pct_l5', settle), ('ir_avg_m400_pct_l5', m400),
                         ('ir_avg_late_gain_l5', gain)):
        vals = [v for v in values if v is not None]
        if vals:
            out[name] = float(np.mean(vals))
    # Speed points need all of the last 3 runs read: a missing position
    # would quietly count as "not prominent" and understate a leader.
    last3 = speed_points[:3]
    if last3 and all(v is not None for v in last3):
        out['ir_speed_points_l3'] = float(sum(last3))
    earlier = [v for v in settle[1:5] if v is not None]
    if settle and settle[0] is not None and earlier:
        out['ir_settle_vs_best'] = settle[0] - min(earlier)

    def excuse(f):
        text_value = ' '.join(str(f.get(k) or '') for k in ('stewardsReport', 'stewardsReportExt'))
        return 1.0 if _EXCUSE_RE.search(text_value) else 0.0

    if races:
        out['st_excuse_last'] = excuse(races[0])
        out['st_excuses_l3'] = float(sum(excuse(f) for f in races[:3]))
        last = races[0]
        sp, bf = _num(last.get('priceSP')), _num(last.get('priceBF'))
        if sp and sp > 1 and bf and bf > 1:
            out['mk_last_bf_vs_sp'] = bf / sp
        drifts = []
        for f in races[:3]:
            flucs = parse_flucs(f.get('flucs'))
            if flucs.get('opening') and flucs.get('starting'):
                drifts.append(flucs['starting'] / flucs['opening'])
            else:
                drifts.append(None)
        if drifts[0] is not None:
            out['mk_last_drift'] = drifts[0]
        vals = [d for d in drifts if d is not None]
        if vals:
            out['mk_avg_drift_l3'] = float(np.mean(vals))

    rivals = {normalize_name(str(n)) for n in rival_names if n} - {normalize_name(str(runner.get('name') or ''))}
    if rivals and races:
        net = 0
        for f in races:
            own = _num(f.get('position'))
            for name, position in f.get('top4') or []:
                rival = normalize_name(str(name or ''))
                position = _num(position)
                if rival in rivals and own and position:
                    net += 1 if own < position else -1 if position < own else 0
        out['ft_net_vs_rivals'] = float(net)
    return out


def snapshot_usable(captured_at, race_start_utc):
    """A saved copy may only feed a race it was captured BEFORE (naive UTC both).

    Same rule in training and live scoring so the two always agree.
    """
    if captured_at is None or race_start_utc is None:
        return False
    return captured_at < race_start_utc


def meeting_features(races, conditions, meeting_date, race_no):
    """{tabNo(str): features} for one race of a saved meeting.

    The barrier used is the one saved in the snapshot, in training and live alike.
    """
    race = (races or {}).get(str(race_no)) or {}
    names = [r.get('name') for r in race.values()]
    return {str(tab): runner_features(runner, meeting_date, None, names, conditions)
            for tab, runner in race.items()}


def tab_key(value):
    try:
        return str(int(float(str(value).strip())))
    except (TypeError, ValueError):
        return None
