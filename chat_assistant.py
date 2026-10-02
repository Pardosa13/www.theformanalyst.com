"""chat_assistant.py — the Racing Form Assistant behind the site's chat widget.

The model answers from tool results only. Every statistic is computed here over
ALL settled runners (not one runner per race), with profit/ROI at starting
price on level $10 stakes, so the numbers it quotes can be checked against the
Data pages.

Settled history is loaded once with a lean query and cached in-process for a
few minutes, so a conversation does not rescan the whole results table on
every question.
"""
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import or_, text

from models import db, Meeting, Race, Horse, Prediction, Result

STAKE = 10.0
MAX_TOOL_ROUNDS = 6
MAX_TOOL_RESULT_CHARS = 40000
HISTORY_MESSAGES = 12
SETTLED_CACHE_SECONDS = 600

SYSTEM_PROMPT = """You are the Racing Form Assistant for The Form Analyst, an Australian thoroughbred form and ratings site. You answer questions from the site's own database through tools.

Scores on the site:
- Analyzer score: the site's original rules-based rating. Higher is better.
- ML score: the machine-learning model's rating, the site's main model. Higher is better.
- PFAI: PuntingForm's AI rating, an outside opinion. Higher is better.
- Assessed odds: the site's fair price for a runner. A market price longer than the assessed odds is an overlay.
- "All signals agree" means Analyzer, PFAI and ML all rank the same runner first in the race.

Form data on every runner:
- last10: the horse's last ten finishes, most recent on the right (x = spell, 0 = finished 10th or worse).
- Records read "starts:wins-seconds-thirds", e.g. "6:2-1-0". There are records for career, firm, good, soft,
  heavy and synthetic tracks, this track, this distance, track and distance, first up and second up.
- The track condition is stored as a category (firm, good, soft, heavy, synthetic), not a number. If the user
  gives a rating such as "Heavy 10", take it as given. The scores were already worked out for the stored condition.
- For wet-track questions, use each runner's soft and heavy records alongside the scores. Treat a record of
  fewer than 3 starts as thin evidence and say so.

How to answer:
- Every number you give must come from a tool result in this conversation. Never estimate, recall or invent a figure, price, horse name or result.
- If no tool covers the question (for example weather forecasts or sectional times), say the assistant can't look that up yet. Don't guess.
- Statistics are over all settled runners, with profit and ROI at starting price on level $10 stakes. Say how many runners a figure is based on, and call a sample under 50 runners small.
- A high strike rate is not the same as a profit. Lead with ROI when the question is about betting.
- Use today's date (given with each question) for "today", "tomorrow" and "this weekend".
- For a quaddie, use the get_quaddie tool. It takes the last four races of the meeting.
- For questions about a whole meeting ("best bets on the card", "top wet-trackers"), use get_meeting_runners
  once rather than calling get_race_card for every race.
- Only admins can see Best Bets. If get_best_bets is not available to you, say Best Bets is an admin page.
- Write in plain Australian English. Be brief: a short answer, then a small table or list when it helps. Use Markdown.
- When you suggest a bet, add one short line that betting carries risk. Don't repeat it in every message."""

STATUS_LABELS = {
    'find_meetings': 'Finding meetings…',
    'get_race_card': 'Reading the race card…',
    'get_meeting_runners': 'Reading the whole meeting…',
    'get_quaddie': 'Building quaddie legs…',
    'model_performance': 'Checking model results…',
    'people_stats': 'Checking trainer/jockey results…',
    'value_analysis': 'Checking prices against results…',
    'component_performance': 'Reading component analysis…',
    'track_specialists': 'Finding track specialists…',
    'horse_profile_stats': 'Checking age and sex results…',
    'class_change_stats': 'Checking class changes…',
    'get_best_bets': 'Finding Best Bets…',
}


def _tool(name, description, properties, required=()):
    return {
        'name': name,
        'description': description,
        'eager_input_streaming': True,
        'input_schema': {
            'type': 'object',
            'properties': properties,
            'required': list(required),
        },
    }


DAYS = {'type': 'integer', 'description': 'Only include meetings from the last N days. Omit for all history.'}
MODEL = {'type': 'string', 'enum': ['ml', 'analyzer'], 'description': 'Which model to rank by. Default ml.'}

TOOLS = [
    _tool('find_meetings', 'List meetings on the site by date and/or track. Returns meeting ids for other tools.', {
        'date': {'type': 'string', 'description': 'YYYY-MM-DD'},
        'track': {'type': 'string', 'description': 'Part of the track name, e.g. "Flemington"'},
    }),
    _tool('get_race_card', 'One race: every runner with Analyzer, ML and PFAI scores and ranks, assessed odds, '
          'last ten starts, career/track/distance/condition records, live Ladbrokes win price when the race has '
          'not run, and the result once it has.', {
              'meeting_id': {'type': 'integer'},
              'race_number': {'type': 'integer'},
          }, required=('meeting_id', 'race_number')),
    _tool('get_meeting_runners', 'Every race at a meeting in one call: each runner\'s Analyzer, ML and PFAI ranks, '
          'assessed odds, last ten starts, and its record on a track condition (the race\'s own condition by '
          'default). Use this for whole-card questions.', {
              'meeting_id': {'type': 'integer'},
              'condition': {'type': 'string', 'enum': ['firm', 'good', 'soft', 'heavy', 'synthetic'],
                            'description': 'Record to show. Default: each race\'s stored condition.'},
          }, required=('meeting_id',)),
    _tool('get_quaddie', 'Quaddie selections for a meeting: the last four races, top runners per leg by model score, '
          'and the number of combinations.', {
              'meeting_id': {'type': 'integer'},
              'per_leg': {'type': 'integer', 'description': 'Runners per leg, 1-5. Default 3.'},
              'model': MODEL,
          }, required=('meeting_id',)),
    _tool('model_performance', 'How a model performs on settled races: wins, strike rate, place rate and ROI, '
          'grouped by the runner\'s rank in its race or by score band.', {
              'model': MODEL,
              'group_by': {'type': 'string', 'enum': ['rank', 'score_band'], 'description': 'Default rank.'},
              'days': DAYS,
          }),
    _tool('people_stats', 'Trainer or jockey results on settled races: runners, wins, strike rate, ROI.', {
        'kind': {'type': 'string', 'enum': ['trainer', 'jockey']},
        'name': {'type': 'string', 'description': 'Part of a name to look up one person.'},
        'min_runners': {'type': 'integer', 'description': 'Default 20.'},
        'sort_by': {'type': 'string', 'enum': ['roi', 'strike_rate', 'wins'], 'description': 'Default roi.'},
        'days': DAYS,
    }, required=('kind',)),
    _tool('value_analysis', 'Compares the site\'s assessed odds with the starting price across ALL settled runners '
          '(winners and losers), grouped by how much longer or shorter the market was.', {'days': DAYS}),
    _tool('component_performance', 'Which scoring components (factors in the Analyzer notes) have been profitable, '
          'from the nightly component analysis.', {
              'source': {'type': 'string', 'enum': ['ml', 'analyzer'],
                         'description': 'Rank runners by ML or Analyzer score. Default analyzer.'},
          }),
    _tool('track_specialists', 'Horses with the best record at one track (minimum starts).', {
        'track': {'type': 'string', 'description': 'Part of the track name. Omit for all tracks.'},
        'horse': {'type': 'string', 'description': 'Part of a horse name.'},
        'min_starts': {'type': 'integer', 'description': 'Default 3.'},
    }),
    _tool('horse_profile_stats', 'Results by horse age and sex (e.g. 3yo mares): strike rate and ROI.', {'days': DAYS}),
    _tool('class_change_stats', 'Results for horses stepping up or down in class, from the Analyzer notes.', {'days': DAYS}),
]

BEST_BETS_TOOL = _tool(
    'get_best_bets', 'Admin only. Today\'s Best Bets qualifiers by the model rule: maiden races where Analyzer, '
    'PFAI and ML all rank the same runner first. Live Ladbrokes market badges are on the Best Bets page.', {
        'date': {'type': 'string', 'description': 'YYYY-MM-DD. Default today.'},
    })


def tools_for(user):
    return TOOLS + ([BEST_BETS_TOOL] if getattr(user, 'is_admin', False) else [])


# ── Shared helpers ──────────────────────────────────────────────────────────

def _parse_price(value):
    try:
        price = float(str(value).replace('$', '').strip())
        return price if price > 0 else None
    except (TypeError, ValueError):
        return None


CONDITIONS = ('firm', 'good', 'soft', 'heavy', 'synthetic')
_RECORD_FIELDS = [
    ('career', 'horse record'), ('firm', 'horse record firm'), ('good', 'horse record good'),
    ('soft', 'horse record soft'), ('heavy', 'horse record heavy'), ('synthetic', 'horse record synthetic'),
    ('track', 'horse record track'), ('distance', 'horse record distance'),
    ('track_distance', 'horse record track distance'),
    ('first_up', 'horse record first up'), ('second_up', 'horse record second up'),
]


def parse_record(value):
    """'6:2-1-0' -> {'record': '6:2-1-0', 'starts': 6, 'wins': 2, 'places': 3, ...}; None if absent."""
    parts = re.split(r'[:\-]', str(value or '').strip())
    if len(parts) != 4:
        return None
    try:
        starts, wins, seconds, thirds = (int(float(p)) for p in parts)
    except ValueError:
        return None
    places = wins + seconds + thirds
    return {
        'record': f'{starts}:{wins}-{seconds}-{thirds}', 'starts': starts, 'wins': wins, 'places': places,
        'win_pct': round(wins / starts * 100) if starts else None,
        'place_pct': round(places / starts * 100) if starts else None,
    }


def condition_key(value):
    """Map a stored track condition ('Heavy', 'heavy 10', 'Soft7') to a record key."""
    text_value = str(value or '').lower()
    return next((c for c in CONDITIONS if c in text_value), None)


def runner_form(horse):
    csv_data = horse.csv_data if isinstance(horse.csv_data, dict) else {}
    records = {}
    for key, field in _RECORD_FIELDS:
        parsed = parse_record(csv_data.get(field))
        if parsed:
            records[key] = parsed['record']
    return {'last10': (csv_data.get('horse last10') or horse.form or '').strip() or None, 'records': records}


def summarise(runners):
    """Wins, strike rate, place rate and level-stakes ROI at SP for a list of runners."""
    n = len(runners)
    wins = sum(1 for r in runners if r['finish'] == 1)
    places = sum(1 for r in runners if r['finish'] in (1, 2, 3))
    # A winner without a recorded SP can't be priced; leave it out of ROI.
    priced = [r for r in runners if r['finish'] != 1 or r['sp']]
    profit = sum((r['sp'] * STAKE - STAKE) if r['finish'] == 1 else -STAKE for r in priced)
    return {
        'runners': n,
        'wins': wins,
        'strike_rate_pct': round(wins / n * 100, 1) if n else 0.0,
        'place_rate_pct': round(places / n * 100, 1) if n else 0.0,
        'profit_at_10_level': round(profit, 2),
        'roi_pct': round(profit / (len(priced) * STAKE) * 100, 1) if priced else None,
    }


_settled_cache = {'at': 0.0, 'rows': None}


def settled_runners():
    """Every non-scratched runner with a recorded finish, one lean dict each."""
    now = time.monotonic()
    if _settled_cache['rows'] is not None and now - _settled_cache['at'] < SETTLED_CACHE_SECONDS:
        return _settled_cache['rows']
    q = (db.session.query(
            Horse.id, Horse.race_id, Horse.horse_name, Horse.trainer, Horse.jockey,
            Race.race_number, Race.race_class, Meeting.id, Meeting.track, Meeting.meeting_name,
            Meeting.date, Meeting.uploaded_at, Prediction.score, Prediction.ml_score,
            Prediction.predicted_odds, Result.finish_position, Result.sp)
         .join(Race, Horse.race_id == Race.id)
         .join(Meeting, Race.meeting_id == Meeting.id)
         .join(Result, Result.horse_id == Horse.id)
         .outerjoin(Prediction, Prediction.horse_id == Horse.id)
         .filter(Result.finish_position > 0)
         .filter(or_(Horse.is_scratched.is_(None), Horse.is_scratched.is_(False))))
    rows = []
    for (hid, race_id, name, trainer, jockey, race_no, race_class, mid, track, mname,
         mdate, uploaded, score, ml, pred_odds, finish, sp) in q.all():
        day = mdate or (uploaded.date() if uploaded else None)
        rows.append({
            'horse_id': hid, 'race_id': race_id, 'horse': name, 'trainer': trainer, 'jockey': jockey,
            'race_number': race_no, 'race_class': race_class, 'meeting_id': mid,
            'track': track or _track_from_name(mname), 'date': day,
            'score': score, 'ml_score': ml, 'assessed': _parse_price(pred_odds),
            'finish': finish, 'sp': sp if sp and sp > 0 else None,
        })
    _settled_cache.update(at=now, rows=rows)
    return rows


def _track_from_name(meeting_name):
    parts = (meeting_name or '').split('_')
    return parts[1] if len(parts) > 1 else (meeting_name or 'Unknown')


def _within(rows, days):
    if not days:
        return rows
    cutoff = (datetime.utcnow() - timedelta(days=int(days))).date()
    return [r for r in rows if r['date'] and r['date'] >= cutoff]


def _model_key(model):
    return 'score' if model == 'analyzer' else 'ml_score'


# ── Tools ───────────────────────────────────────────────────────────────────

def find_meetings(date=None, track=None):
    q = Meeting.query
    if date:
        try:
            q = q.filter(Meeting.date == datetime.strptime(date, '%Y-%m-%d').date())
        except ValueError:
            return {'error': 'date must be YYYY-MM-DD'}
    if track:
        q = q.filter(or_(Meeting.track.ilike(f'%{track}%'), Meeting.meeting_name.ilike(f'%{track}%')))
    meetings = q.order_by(Meeting.date.desc().nullslast(), Meeting.uploaded_at.desc()).limit(30).all()
    return {'meetings': [{
        'meeting_id': m.id, 'name': m.meeting_name, 'track': m.track or _track_from_name(m.meeting_name),
        'date': m.date.isoformat() if m.date else None, 'races': len(m.races),
    } for m in meetings]}


def _live_prices(meeting, race_number):
    """{normalised runner name: win price} from Ladbrokes, or {} if unavailable."""
    try:
        from ladbrokes import match_race_uuid, fetch_race_odds
        if not (meeting.track and meeting.date):
            return {}
        uuid = match_race_uuid(meeting.track, meeting.date.isoformat(), race_number)
        if not uuid:
            return {}
        payload = fetch_race_odds(uuid) or {}
        return {k: v.get('win') for k, v in (payload.get('odds') or {}).items() if v.get('win')}
    except Exception:
        return {}


def _condition_notes(notes, cond):
    """The Analyzer's own lines about today's track condition (admins see notes on the site)."""
    if not notes or not cond:
        return None
    lines = [l.strip() for l in notes.splitlines()
             if cond in l.lower() and ('rate' in l.lower() or 'runs' in l.lower() or 'data' in l.lower())]
    return lines[:4] or None


def get_race_card(meeting_id, race_number, show_notes=False):
    from app import parse_pfai_score_from_horse, top_signal_horse_ids, signals_all_agree_top, is_maiden_race
    from ladbrokes import normalize_runner_name

    meeting = db.session.get(Meeting, meeting_id)
    if not meeting:
        return {'error': 'Meeting not found'}
    race = next((r for r in meeting.races if r.race_number == race_number), None)
    if not race:
        return {'error': f'Race {race_number} not found at this meeting',
                'races': sorted(r.race_number for r in meeting.races)}

    active = [h for h in race.horses if not h.is_scratched]
    results = {h.id: h.result for h in race.horses if getattr(h, 'result', None)}
    has_run = any(res.finish_position and res.finish_position > 0 for res in results.values())
    live = {} if has_run else _live_prices(meeting, race_number)

    def ranks(values):
        ordered = sorted((v, hid) for hid, v in values.items() if v is not None)[::-1]
        return {hid: i + 1 for i, (_, hid) in enumerate(ordered)}

    pfai = {h.id: parse_pfai_score_from_horse(h, h.prediction) for h in active if h.prediction}
    analyzer_rank = ranks({h.id: h.prediction.score for h in active if h.prediction})
    ml_rank = ranks({h.id: h.prediction.ml_score for h in active if h.prediction})
    pfai_rank = ranks(pfai)
    top_ids = top_signal_horse_ids(race.horses)

    cond = condition_key(race.track_condition)
    runners = []
    for h in active:
        p = h.prediction
        res = results.get(h.id)
        form = runner_form(h)
        runners.append({
            'horse': h.horse_name, 'barrier': h.barrier, 'jockey': h.jockey, 'trainer': h.trainer,
            'last10': form['last10'], 'records': form['records'],
            'condition_notes': _condition_notes(p.notes, cond) if (p and show_notes) else None,
            'analyzer_score': round(p.score, 1) if p and p.score is not None else None,
            'analyzer_rank': analyzer_rank.get(h.id),
            'ml_score': round(p.ml_score, 1) if p and p.ml_score is not None else None,
            'ml_rank': ml_rank.get(h.id),
            'pfai': pfai.get(h.id), 'pfai_rank': pfai_rank.get(h.id),
            'assessed_odds': p.predicted_odds if p else None,
            'win_probability': p.win_probability if p else None,
            'all_signals_agree': signals_all_agree_top(h.id, top_ids),
            'live_win_price': live.get(normalize_runner_name(h.horse_name)),
            'finish': res.finish_position if res else None,
            'sp': res.sp if res else None,
        })
    runners.sort(key=lambda r: (r['ml_rank'] or 99, r['analyzer_rank'] or 99))
    return {
        'meeting': meeting.meeting_name, 'meeting_id': meeting.id,
        'date': meeting.date.isoformat() if meeting.date else None,
        'race_number': race.race_number, 'distance': race.distance, 'class': race.race_class,
        'is_maiden': is_maiden_race(race.race_class), 'track_condition': race.track_condition,
        'condition_record_key': cond,
        'has_run': has_run, 'live_prices_available': bool(live),
        'scratched': [h.horse_name for h in race.horses if h.is_scratched],
        'runners': runners,
    }


def get_meeting_runners(meeting_id, condition=None):
    from app import parse_pfai_score_from_horse, top_signal_horse_ids, signals_all_agree_top

    meeting = db.session.get(Meeting, meeting_id)
    if not meeting:
        return {'error': 'Meeting not found'}

    def ranks(values):
        ordered = sorted((v, hid) for hid, v in values.items() if v is not None)[::-1]
        return {hid: i + 1 for i, (_, hid) in enumerate(ordered)}

    races = []
    for race in sorted(meeting.races, key=lambda r: r.race_number):
        active = [h for h in race.horses if not h.is_scratched and h.prediction]
        cond = condition or condition_key(race.track_condition)
        a_rank = ranks({h.id: h.prediction.score for h in active})
        m_rank = ranks({h.id: h.prediction.ml_score for h in active})
        p_rank = ranks({h.id: parse_pfai_score_from_horse(h, h.prediction) for h in active})
        top_ids = top_signal_horse_ids(race.horses)
        runners = []
        for h in active:
            form = runner_form(h)
            cond_rec = parse_record((h.csv_data or {}).get(f'horse record {cond}')) if cond else None
            runners.append({
                'horse': h.horse_name, 'barrier': h.barrier,
                'ranks': {'analyzer': a_rank.get(h.id), 'ml': m_rank.get(h.id), 'pfai': p_rank.get(h.id)},
                'assessed_odds': h.prediction.predicted_odds,
                'all_agree': signals_all_agree_top(h.id, top_ids),
                'last10': form['last10'],
                'condition_record': cond_rec['record'] if cond_rec else '0:0-0-0',
                'condition_win_pct': cond_rec['win_pct'] if cond_rec else None,
                'condition_place_pct': cond_rec['place_pct'] if cond_rec else None,
            })
        runners.sort(key=lambda r: (r['ranks']['ml'] or 99, r['ranks']['analyzer'] or 99))
        races.append({'race_number': race.race_number, 'distance': race.distance, 'class': race.race_class,
                      'track_condition': race.track_condition, 'record_shown': cond, 'runners': runners})
    return {'meeting': meeting.meeting_name, 'meeting_id': meeting.id,
            'date': meeting.date.isoformat() if meeting.date else None, 'races': races}


def get_quaddie(meeting_id, per_leg=3, model='ml'):
    meeting = db.session.get(Meeting, meeting_id)
    if not meeting:
        return {'error': 'Meeting not found'}
    races = sorted(meeting.races, key=lambda r: r.race_number)
    if len(races) < 4:
        return {'error': 'A quaddie needs at least four races'}
    per_leg = max(1, min(int(per_leg or 3), 5))
    key = _model_key(model)
    legs = []
    for race in races[-4:]:
        scored = [h for h in race.horses if not h.is_scratched and h.prediction]
        used = key
        if not any(getattr(h.prediction, key) is not None for h in scored):
            used = 'score'  # no ML scores for this race yet
        scored = [h for h in scored if getattr(h.prediction, used) is not None]
        scored.sort(key=lambda h: getattr(h.prediction, used), reverse=True)
        legs.append({
            'race_number': race.race_number, 'ranked_by': 'ml' if used == 'ml_score' else 'analyzer',
            'selections': [{'horse': h.horse_name, 'barrier': h.barrier,
                            'score': round(getattr(h.prediction, used), 1),
                            'assessed_odds': h.prediction.predicted_odds} for h in scored[:per_leg]],
        })
    combos = 1
    for leg in legs:
        combos *= max(len(leg['selections']), 1)
    return {'meeting': meeting.meeting_name, 'legs': legs, 'combinations': combos}


def model_performance(model='ml', group_by='rank', days=None):
    key = _model_key(model)
    rows = [r for r in _within(settled_runners(), days) if r[key] is not None]
    groups = defaultdict(list)
    if group_by == 'score_band':
        bands = ([(100, '100+'), (80, '80-99'), (60, '60-79'), (40, '40-59'), (float('-inf'), 'under 40')]
                 if model == 'analyzer' else
                 [(80, '80+'), (60, '60-79'), (40, '40-59'), (20, '20-39'), (float('-inf'), 'under 20')])
        for r in rows:
            groups[next(label for floor, label in bands if r[key] >= floor)].append(r)
        order = [label for _, label in bands]
    else:
        by_race = defaultdict(list)
        for r in rows:
            by_race[r['race_id']].append(r)
        for race_rows in by_race.values():
            race_rows.sort(key=lambda r: r[key], reverse=True)
            for i, r in enumerate(race_rows):
                groups['top pick' if i == 0 else '2nd pick' if i == 1 else '3rd pick' if i == 2 else '4th or lower'].append(r)
        order = ['top pick', '2nd pick', '3rd pick', '4th or lower']
    return {
        'model': model, 'group_by': group_by, 'days': days, 'races': len({r['race_id'] for r in rows}),
        'groups': [{'group': g, **summarise(groups[g])} for g in order if groups[g]],
    }


def people_stats(kind, name=None, min_runners=20, sort_by='roi', days=None):
    field = 'trainer' if kind == 'trainer' else 'jockey'
    groups = defaultdict(list)
    for r in _within(settled_runners(), days):
        if r[field]:
            groups[r[field].strip()].append(r)
    if name:
        needle = name.lower()
        rows = [{field: k, **summarise(v)} for k, v in groups.items() if needle in k.lower()]
        return {'kind': kind, 'matches': sorted(rows, key=lambda x: -x['runners'])[:10]}
    min_runners = max(int(min_runners or 20), 1)
    rows = [{field: k, **summarise(v)} for k, v in groups.items() if len(v) >= min_runners]
    sort_key = {'roi': 'roi_pct', 'strike_rate': 'strike_rate_pct', 'wins': 'wins'}.get(sort_by, 'roi_pct')
    rows.sort(key=lambda x: (x[sort_key] if x[sort_key] is not None else float('-inf')), reverse=True)
    return {'kind': kind, 'min_runners': min_runners, 'qualifying': len(rows), 'top': rows[:15]}


def value_analysis(days=None):
    rows = [r for r in _within(settled_runners(), days) if r['assessed'] and r['sp']]
    bands = [(1.5, 'SP 1.5x+ our price (big overlay)'), (1.1, 'SP 1.1-1.5x our price (overlay)'),
             (0.9, 'SP within 10% of our price'), (0.0, 'SP shorter than our price (underlay)')]
    groups = defaultdict(list)
    for r in rows:
        ratio = r['sp'] / r['assessed']
        groups[next(label for floor, label in bands if ratio >= floor)].append(r)
    return {'runners_with_both_prices': len(rows),
            'groups': [{'group': label, **summarise(groups[label])} for _, label in bands if groups[label]]}


def component_performance(source='analyzer'):
    row = db.session.execute(text(
        'SELECT payload FROM component_analysis_cache ORDER BY computed_at DESC LIMIT 1'
    )).fetchone()
    if not row:
        return {'error': 'Component analysis has not been built yet (it runs with the nightly backtest).'}
    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    if source == 'ml':
        payload = payload.get('ml_source') or {}
        if not payload:
            return {'error': 'The ML version is built by the nightly backtest and is not ready yet.'}
    comps = [c for c in payload.get('components', []) if c.get('appearances', 0) >= 30]
    comps.sort(key=lambda c: c.get('roi', 0), reverse=True)
    return {'source': source, 'components_with_30plus_runners': len(comps),
            'best': comps[:10], 'worst': comps[-5:][::-1] if len(comps) > 10 else []}


def track_specialists(track=None, horse=None, min_starts=3):
    groups = defaultdict(list)
    for r in settled_runners():
        if track and track.lower() not in (r['track'] or '').lower():
            continue
        if horse and horse.lower() not in (r['horse'] or '').lower():
            continue
        groups[(r['horse'], r['track'])].append(r)
    min_starts = max(int(min_starts or 3), 1)
    rows = [{'horse': h, 'track': t, **summarise(v)} for (h, t), v in groups.items() if len(v) >= min_starts]
    rows.sort(key=lambda x: (x['wins'], x['strike_rate_pct']), reverse=True)
    return {'specialists': rows[:20]}


_profile_cache = {'at': 0.0, 'map': None}


def horse_profile_stats(days=None):
    now = time.monotonic()
    if _profile_cache['map'] is None or now - _profile_cache['at'] > SETTLED_CACHE_SECONDS:
        q = db.session.query(Horse.id, Horse.csv_data['horse age'].as_string(),
                             Horse.csv_data['horse sex'].as_string()).join(Result, Result.horse_id == Horse.id)
        _profile_cache.update(at=now, map={hid: (age, sex) for hid, age, sex in q.all() if age and sex})
    groups = defaultdict(list)
    for r in _within(settled_runners(), days):
        age_sex = _profile_cache['map'].get(r['horse_id'])
        if age_sex:
            groups[f'{age_sex[0]}yo {age_sex[1]}'].append(r)
    rows = [{'profile': k, **summarise(v)} for k, v in groups.items() if len(v) >= 30]
    rows.sort(key=lambda x: x['roi_pct'] if x['roi_pct'] is not None else float('-inf'), reverse=True)
    return {'profiles': rows}


_CLASS_RE = re.compile(r'Stepping (DOWN|UP)\s+([\d.]+)\s+class points')
_class_cache = {'at': 0.0, 'map': None}


def _class_band(direction, points):
    if direction == 'DOWN':
        return ('Major drop (30+)' if points >= 30 else 'Significant drop (20-29)' if points >= 20
                else 'Moderate drop (10-19)' if points >= 10 else 'Small drop (under 10)')
    return ('Significant rise (20+)' if points >= 20 else 'Moderate rise (10-19)' if points >= 10
            else 'Small rise (under 10)')


def class_change_stats(days=None):
    now = time.monotonic()
    if _class_cache['map'] is None or now - _class_cache['at'] > SETTLED_CACHE_SECONDS:
        q = (db.session.query(Prediction.horse_id, Prediction.notes)
             .join(Result, Result.horse_id == Prediction.horse_id)
             .filter(Prediction.notes.like('%class points%')))
        bands = {}
        for hid, notes in q.all():
            m = _CLASS_RE.search(notes or '')
            if m:
                bands[hid] = _class_band(m.group(1), float(m.group(2)))
        _class_cache.update(at=now, map=bands)
    groups = defaultdict(list)
    for r in _within(settled_runners(), days):
        groups[_class_cache['map'].get(r['horse_id'], 'No class change noted')].append(r)
    order = ['Major drop (30+)', 'Significant drop (20-29)', 'Moderate drop (10-19)', 'Small drop (under 10)',
             'No class change noted', 'Small rise (under 10)', 'Moderate rise (10-19)', 'Significant rise (20+)']
    return {'groups': [{'group': g, **summarise(groups[g])} for g in order if groups[g]]}


def get_best_bets(date=None, today=None):
    from app import top_signal_horse_ids, signals_all_agree_top, is_maiden_race, parse_pfai_score_from_horse
    try:
        day = datetime.strptime(date, '%Y-%m-%d').date() if date else today
    except ValueError:
        return {'error': 'date must be YYYY-MM-DD'}
    picks = []
    for meeting in Meeting.query.filter(Meeting.date == day).all():
        for race in sorted(meeting.races, key=lambda r: r.race_number):
            if not is_maiden_race(race.race_class):
                continue
            top_ids = top_signal_horse_ids(race.horses)
            for h in race.horses:
                if not h.is_scratched and signals_all_agree_top(h.id, top_ids):
                    picks.append({
                        'meeting': meeting.meeting_name, 'meeting_id': meeting.id,
                        'race_number': race.race_number, 'horse': h.horse_name,
                        'analyzer_score': h.prediction.score, 'ml_score': h.prediction.ml_score,
                        'pfai': parse_pfai_score_from_horse(h, h.prediction),
                        'assessed_odds': h.prediction.predicted_odds,
                    })
    return {'date': day.isoformat() if day else None, 'rule': 'maiden race, Analyzer + PFAI + ML agree on top pick',
            'picks': picks}


# ── Input checking and dispatch ─────────────────────────────────────────────

_INT_ARGS = {'meeting_id', 'race_number', 'per_leg', 'days', 'min_runners', 'min_starts'}
_STR_ARGS = {'date', 'track', 'name', 'horse', 'kind', 'model', 'group_by', 'sort_by', 'source', 'condition'}
_ENUMS = {'kind': {'trainer', 'jockey'}, 'model': {'ml', 'analyzer'}, 'group_by': {'rank', 'score_band'},
          'sort_by': {'roi', 'strike_rate', 'wins'}, 'source': {'ml', 'analyzer'}, 'condition': set(CONDITIONS)}
_HANDLERS = {
    'find_meetings': find_meetings, 'get_race_card': get_race_card, 'get_quaddie': get_quaddie,
    'get_meeting_runners': get_meeting_runners,
    'model_performance': model_performance, 'people_stats': people_stats, 'value_analysis': value_analysis,
    'component_performance': component_performance, 'track_specialists': track_specialists,
    'horse_profile_stats': horse_profile_stats, 'class_change_stats': class_change_stats,
}


def _clean_args(tool_def, raw):
    """Validate model-supplied tool input; returns (args, error)."""
    if not isinstance(raw, dict):
        return None, 'tool input must be an object'
    allowed = tool_def['input_schema']['properties']
    args = {}
    for k, v in raw.items():
        if k not in allowed or v is None:
            continue
        if k in _INT_ARGS:
            if isinstance(v, bool) or not isinstance(v, (int, float, str)):
                return None, f'{k} must be a whole number'
            try:
                v = int(v)
            except ValueError:
                return None, f'{k} must be a whole number'
        elif k in _STR_ARGS:
            if not isinstance(v, str):
                return None, f'{k} must be text'
            v = v.strip()
            if k in _ENUMS and v not in _ENUMS[k]:
                return None, f'{k} must be one of {sorted(_ENUMS[k])}'
        args[k] = v
    missing = [k for k in tool_def['input_schema']['required'] if k not in args]
    if missing:
        return None, f'missing {", ".join(missing)}'
    return args, None


def run_tool(name, raw_input, user, today):
    tool_def = next((t for t in tools_for(user) if t['name'] == name), None)
    if not tool_def:
        return {'error': f'Unknown or unavailable tool: {name}'}
    args, error = _clean_args(tool_def, raw_input)
    if error:
        return {'error': error}
    try:
        if name == 'get_best_bets':
            return get_best_bets(today=today, **args)
        if name == 'get_race_card':
            return get_race_card(show_notes=bool(getattr(user, 'is_admin', False)), **args)
        return _HANDLERS[name](**args)
    except Exception as e:  # a failed lookup should not end the conversation
        db.session.rollback()
        return {'error': f'lookup failed: {type(e).__name__}'}


def _to_tool_content(result):
    out = json.dumps(result, default=str, separators=(',', ':'))
    if len(out) > MAX_TOOL_RESULT_CHARS:
        out = out[:MAX_TOOL_RESULT_CHARS] + '…(truncated)'
    return out


# ── Conversation ────────────────────────────────────────────────────────────

def build_messages(history, today):
    """Turn stored chat rows into a valid Messages API history.

    The API needs the first message to be the user's and roles to alternate,
    so leading assistant rows are dropped and same-role runs are merged (a
    failed reply leaves two user rows in a row). Today's date rides on the
    final user turn, leaving the cached system prompt unchanged.
    """
    merged = []
    for role, content in history[-HISTORY_MESSAGES:]:
        if role not in ('user', 'assistant') or not content:
            continue
        if not merged and role != 'user':
            continue
        if merged and merged[-1]['role'] == role:
            merged[-1]['content'] += '\n\n' + content
        else:
            merged.append({'role': role, 'content': content})
    if merged and merged[-1]['role'] == 'user':
        date_line = f"(Today is {today.strftime('%A %d %B %Y')} in Melbourne, {today.isoformat()}.)"
        merged[-1] = {'role': 'user', 'content': [
            {'type': 'text', 'text': date_line},
            {'type': 'text', 'text': merged[-1]['content']},
        ]}
    return merged


def stream_reply(client, model, history, user, today):
    """Yield chat events: {'type': 'text'|'status'|'error', ...}.

    Runs the tool loop, streaming the model's text as it is written.
    """
    messages = build_messages(history, today)
    if not messages:
        yield {'type': 'error', 'text': 'Nothing to answer.'}
        return
    tools = tools_for(user)
    system = [{'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}]
    wrote_text = False
    json_retries = 0
    rounds = 0
    while rounds < MAX_TOOL_ROUNDS:
        try:
            with client.beta.messages.stream(
                model=model, max_tokens=16000, system=system, tools=tools, messages=messages,
                output_config={'effort': 'medium'},
                betas=['server-side-fallback-2026-07-01'], fallbacks='default',
            ) as stream:
                for event in stream:
                    if event.type == 'text' and event.text:
                        wrote_text = True
                        yield {'type': 'text', 'text': event.text}
                response = stream.get_final_message()
            json_retries = 0
        except ValueError:
            # Tool input JSON the SDK could not parse at all; re-issue the turn.
            json_retries += 1
            if json_retries > 2:
                yield {'type': 'error', 'text': 'Something went wrong reading that request. Please try again.'}
                return
            continue
        rounds += 1

        tool_uses = [b for b in response.content if b.type == 'tool_use']
        if response.stop_reason == 'refusal':
            if not wrote_text:
                yield {'type': 'text', 'text': "Sorry, I can't help with that one. Try rephrasing the question."}
            return
        if not tool_uses:
            return
        if response.stop_reason == 'max_tokens':
            yield {'type': 'error', 'text': 'That answer got too long. Try a narrower question.'}
            return

        results = []
        for block in tool_uses:
            yield {'type': 'status', 'text': STATUS_LABELS.get(block.name, 'Looking that up…')}
            result = run_tool(block.name, block.input, user, today)
            results.append({'type': 'tool_result', 'tool_use_id': block.id, 'content': _to_tool_content(result),
                            **({'is_error': True} if isinstance(result, dict) and 'error' in result else {})})
        messages.append({'role': 'assistant', 'content': response.content})
        messages.append({'role': 'user', 'content': results})
        if wrote_text:
            yield {'type': 'text', 'text': '\n\n'}

    yield {'type': 'text', 'text': '\n\n_I stopped after several lookups. Try a more specific question._'}
