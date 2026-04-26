"""
mma_sync.py - Weekly Railway cron job.

What it does (in order):
  1. Scrapes ESPN for upcoming UFC events + fight cards
  2. Re-calculates current fighter EMA stats from Postgres fight history
  3. Loads the trained CatBoost model and generates win probabilities
  4. Writes upcoming events, fights, and predictions to Postgres
  5. Also scrapes ESPN for the most recently completed event to capture results

Railway cron schedule: 0 9 * * 0  (Sundays at 9am UTC)

Environment variables required (already in Railway):
  DATABASE_URL

No new env vars needed.
"""

import os
import sys
import json
import time
import re
import unicodedata
import math
import logging
from datetime import datetime, date

import requests
from bs4 import BeautifulSoup
import pandas as pd
import numpy as np
import joblib
import psycopg2
from psycopg2.extras import execute_values

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger('mma_sync')

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get('DATABASE_URL', '')
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# Model file lives in the repo root alongside this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(BASE_DIR, 'models', 'catboost_ufc_model.pkl')

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

# ── Name normalisation (matches Octagon-AI predict_model.py) ─────────────────

def normalize_name(name):
    if not name:
        return ''
    name = str(name)
    nfkd = unicodedata.normalize('NFKD', name)
    name = ''.join(c for c in nfkd if not unicodedata.combining(c))
    name = name.lower().replace('-', ' ')
    name = re.sub(r"[^a-zA-Z0-9\s]", '', name)
    name = name.replace(' saint ', ' st ').replace(' saint', ' st').replace('saint ', 'st ')
    return ' '.join(name.split())


# ── Glicko-2 constants ────────────────────────────────────────────────────────
TAU = 0.5
MIN_RD = 30.0
MAX_RD = 350.0
DEFAULT_RATING = 1500.0
DEFAULT_RD = 350.0
DEFAULT_VOL = 0.06


# ── Fighter stats tracker (mirrors Octagon-AI FighterStats) ──────────────────

class FighterStats:
    def __init__(self):
        self.total_time_sec = 0
        self.first_fight_date = None
        self.last_fight_date = None
        self.fight_dates = []
        self.ema_slpm = 0
        self.ema_sapm = 0
        self.ema_td_acc = 0.4
        self.ema_td_avg = 1.0
        self.ema_td_def = 0.5
        self.ema_kd_rate = 0.2
        self.ema_sub_rate = 0.2
        self.ema_ctrl_pct = 10.0
        self.ema_sig_str_acc = 0.45
        self.ema_head_pct = 0.7
        self.ema_body_pct = 0.15
        self.ema_leg_pct = 0.15
        self.ema_dist_pct = 0.8
        self.ema_clinch_pct = 0.1
        self.ema_ground_pct = 0.1
        self.wins = 0
        self.losses = 0
        self.draws = 0
        self.total_fights = 0
        self.streak = 0
        self.recent_form = []

    def update(self, result, fight_date, f_time, s_landed, s_absorbed,
               td_landed, td_att, opp_td_att, opp_td_landed, kd, sub, ctrl,
               sig_acc, head_p, body_p, leg_p, dist_p, clin_p, grou_p):
        self.total_fights += 1
        self.total_time_sec += f_time
        if self.first_fight_date is None:
            self.first_fight_date = fight_date
        self.last_fight_date = fight_date
        self.fight_dates.append(fight_date)

        t_min = f_time / 60.0 if f_time > 0 else 1.0
        f_slpm = s_landed / t_min
        f_sapm = s_absorbed / t_min
        f_td_acc = td_landed / td_att if td_att > 0 else 0.4
        f_td_avg = (td_landed / t_min) * 15.0
        f_td_def = 1.0 - (opp_td_landed / opp_td_att) if opp_td_att > 0 else 0.5
        f_kd = (kd / t_min) * 15.0
        f_sub = (sub / t_min) * 15.0
        f_ctrl = (ctrl / f_time) * 100.0 if f_time > 0 else 0

        alpha = 0.3
        if self.total_fights == 1:
            self.ema_slpm = f_slpm
            self.ema_sapm = f_sapm
            self.ema_td_acc = f_td_acc
            self.ema_td_avg = f_td_avg
            self.ema_td_def = f_td_def
            self.ema_kd_rate = f_kd
            self.ema_sub_rate = f_sub
            self.ema_ctrl_pct = f_ctrl
            self.ema_sig_str_acc = sig_acc
            self.ema_head_pct = head_p
            self.ema_body_pct = body_p
            self.ema_leg_pct = leg_p
            self.ema_dist_pct = dist_p
            self.ema_clinch_pct = clin_p
            self.ema_ground_pct = grou_p
        else:
            self.ema_slpm = alpha * f_slpm + (1 - alpha) * self.ema_slpm
            self.ema_sapm = alpha * f_sapm + (1 - alpha) * self.ema_sapm
            self.ema_td_acc = alpha * f_td_acc + (1 - alpha) * self.ema_td_acc
            self.ema_td_avg = alpha * f_td_avg + (1 - alpha) * self.ema_td_avg
            self.ema_td_def = alpha * f_td_def + (1 - alpha) * self.ema_td_def
            self.ema_kd_rate = alpha * f_kd + (1 - alpha) * self.ema_kd_rate
            self.ema_sub_rate = alpha * f_sub + (1 - alpha) * self.ema_sub_rate
            self.ema_ctrl_pct = alpha * f_ctrl + (1 - alpha) * self.ema_ctrl_pct
            self.ema_sig_str_acc = alpha * sig_acc + (1 - alpha) * self.ema_sig_str_acc
            self.ema_head_pct = alpha * head_p + (1 - alpha) * self.ema_head_pct
            self.ema_body_pct = alpha * body_p + (1 - alpha) * self.ema_body_pct
            self.ema_leg_pct = alpha * leg_p + (1 - alpha) * self.ema_leg_pct
            self.ema_dist_pct = alpha * dist_p + (1 - alpha) * self.ema_dist_pct
            self.ema_clinch_pct = alpha * clin_p + (1 - alpha) * self.ema_clinch_pct
            self.ema_ground_pct = alpha * grou_p + (1 - alpha) * self.ema_ground_pct

        if result == 'W':
            self.wins += 1
            self.streak = (self.streak + 1) if self.streak >= 0 else 1
        elif result == 'L':
            self.losses += 1
            self.streak = (self.streak - 1) if self.streak <= 0 else -1
        else:
            self.draws += 1
            self.streak = 0

        self.recent_form.append(result)
        if len(self.recent_form) > 5:
            self.recent_form.pop(0)

    def get_stat_vector(self, current_date):
        win_rate = self.wins / self.total_fights if self.total_fights > 0 else 0.5
        rust_days = (current_date - self.last_fight_date).days if self.last_fight_date else 365
        two_years_ago = current_date - pd.Timedelta(days=730)
        recent_fights = len([d for d in self.fight_dates if d > two_years_ago])
        f_ath_age = (current_date - self.first_fight_date).days / 365.25 if self.first_fight_date else 0

        return {
            'slpm': self.ema_slpm,
            'sapm': self.ema_sapm,
            'td_acc': self.ema_td_acc,
            'td_avg': self.ema_td_avg,
            'td_def': self.ema_td_def,
            'kd_rate': self.ema_kd_rate,
            'sub_rate': self.ema_sub_rate,
            'ctrl_rate': self.ema_ctrl_pct,
            'sig_str_acc': self.ema_sig_str_acc,
            'head_pct': self.ema_head_pct,
            'body_pct': self.ema_body_pct,
            'leg_pct': self.ema_leg_pct,
            'dist_pct': self.ema_dist_pct,
            'clinch_pct': self.ema_clinch_pct,
            'ground_pct': self.ema_ground_pct,
            'exp_time': self.total_time_sec,
            'wins': self.wins,
            'losses': self.losses,
            'streak': self.streak,
            'win_rate': win_rate,
            'rust_days': rust_days,
            'recent_fights_count': recent_fights,
            'ath_age': f_ath_age,
            'recent_form': '-'.join(reversed(self.recent_form)) if self.recent_form else 'N/A',
        }


# ── ESPN scraping ─────────────────────────────────────────────────────────────

def get_soup(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.content, 'html.parser')
    except Exception as e:
        log.warning(f"Failed to fetch {url}: {e}")
        return None


def scrape_fighter_profile(url):
    """Scrape height/reach/stance/record from ESPN fighter profile."""
    if not url or 'espn.com' not in url:
        return {}
    try:
        soup = get_soup(url)
        if not soup:
            return {}
        stats = {}
        header_div = soup.find('div', class_=lambda x: x and 'PlayerHeader' in x)
        if not header_div:
            return {}
        text = header_div.get_text(separator='|', strip=True)
        parts = [p.strip() for p in text.split('|')]

        def get_val(keys):
            for i, p in enumerate(parts):
                if p.lower() in [k.lower() for k in keys]:
                    if i + 1 < len(parts):
                        return parts[i + 1]
            return None

        hw = get_val(['HT/WT', 'Height'])
        if hw:
            sub = hw.split(',')
            if sub:
                stats['Height'] = sub[0].strip()
            if len(sub) > 1:
                stats['Weight'] = sub[1].strip()

        dob = get_val(['Birthdate', 'DOB'])
        if dob:
            stats['DOB'] = dob.split('(')[0].strip()

        reach = get_val(['Reach'])
        if reach:
            stats['Reach'] = reach.replace('"', '').strip()

        stance = get_val(['Stance'])
        if stance:
            stats['Stance'] = stance

        record = get_val(['Record', 'W-L-D'])
        if record:
            stats['Record'] = record

        return stats
    except Exception as e:
        log.warning(f"Profile scrape error {url}: {e}")
        return {}


def _parse_espn_schedule_json(data, seen_ids):
    """
    Extract events from the ESPN __espnfitt__ JSON on a schedule page.
    Returns a list of event dicts with the same keys as scrape_upcoming_events.
    """
    events = []
    today = date.today()
    # ESPN schedule pages embed events under several possible paths
    page = data.get('page', {})
    content = page.get('content', {})
    # Try 'schedule' key first, then 'events'
    schedule = content.get('schedule') or {}
    raw_events = []
    if isinstance(schedule, dict):
        for _week, week_data in schedule.items():
            if isinstance(week_data, list):
                raw_events.extend(week_data)
            elif isinstance(week_data, dict):
                raw_events.extend(week_data.get('events', []))
    if not raw_events:
        raw_events = content.get('events', []) or []

    for ev in raw_events:
        ev_id = str(ev.get('id', ''))
        if not ev_id or ev_id in seen_ids:
            continue
        name = ev.get('name') or ev.get('shortName') or ''
        raw_date = ev.get('date', '')
        try:
            ev_date = pd.to_datetime(raw_date).date()
        except Exception:
            continue
        links = ev.get('links', [])
        ev_url = next((lk.get('href', '') for lk in links if 'href' in lk), '')
        if ev_url and not ev_url.startswith('http'):
            ev_url = 'https://www.espn.com' + ev_url
        venues = ev.get('venues', []) or ev.get('competitions', [{}])
        loc = ''
        if venues:
            v = venues[0]
            addr = v.get('address', v.get('venue', {}).get('address', {}))
            city = addr.get('city', '')
            state = addr.get('state', '')
            loc = ', '.join(filter(None, [city, state])) or 'TBD'
        seen_ids.add(ev_id)
        events.append({
            'event_id': ev_id,
            'event_name': name,
            'date': ev_date,
            'location': loc or 'TBD',
            'url': ev_url,
        })
    return events


def _fetch_espn_schedule_api(year, seen_ids):
    """
    Fetch UFC schedule from the ESPN public scoreboard API for a given year.
    Returns a list of event dicts.
    """
    events = []
    start = f"{year}0101"
    end = f"{year}1231"
    url = (
        f"https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
        f"?limit=100&dates={start}-{end}"
    )
    log.info(f"  Trying ESPN API: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning(f"  ESPN API fetch failed: {e}")
        return events

    for ev in data.get('events', []):
        ev_id = str(ev.get('id', ''))
        if not ev_id or ev_id in seen_ids:
            continue
        name = ev.get('name') or ev.get('shortName') or ''
        raw_date = ev.get('date', '')
        try:
            ev_date = pd.to_datetime(raw_date).date()
        except Exception:
            continue
        links = ev.get('links', [])
        ev_url = next((lk.get('href', '') for lk in links if 'href' in lk), '')
        if ev_url and not ev_url.startswith('http'):
            ev_url = 'https://www.espn.com' + ev_url
        venues = ev.get('venues', [])
        loc = 'TBD'
        if venues:
            addr = venues[0].get('address', {})
            city = addr.get('city', '')
            state = addr.get('state', '')
            loc = ', '.join(filter(None, [city, state])) or 'TBD'
        seen_ids.add(ev_id)
        events.append({
            'event_id': ev_id,
            'event_name': name,
            'date': ev_date,
            'location': loc,
            'url': ev_url,
        })
    return events


def scrape_upcoming_events():
    """Scrape ESPN UFC schedule for upcoming + most recent completed event.

    Tries three strategies in order for each year:
      1. ESPN public scoreboard API (JSON, most reliable)
      2. __espnfitt__ JSON embedded in the schedule HTML page
      3. DOM table parsing (legacy fallback)
    """
    today = date.today()
    current_year = today.year
    next_year = current_year + 1

    events = []
    seen_ids = set()

    for year in [current_year, next_year]:
        year_events = []

        # ── Strategy 1: ESPN public API ───────────────────────────────────────
        year_events = _fetch_espn_schedule_api(year, seen_ids)
        if year_events:
            log.info(f"  ESPN API returned {len(year_events)} events for {year}")
            events.extend(year_events)
            time.sleep(0.5)
            continue

        # ── Strategy 2 & 3: HTML page ─────────────────────────────────────────
        url = f"https://www.espn.com/mma/schedule/_/year/{year}/league/ufc"
        log.info(f"Fetching schedule HTML: {url}")
        soup = get_soup(url)
        if not soup:
            continue

        # Strategy 2: __espnfitt__ JSON
        _PATTERNS = [
            "window['__espnfitt__']=",
            'window["__espnfitt__"]=',
            "window.__espnfitt__ =",
            "window.__espnfitt__=",
        ]
        for script in soup.find_all('script'):
            if not script.string:
                continue
            matched = next((p for p in _PATTERNS if p in script.string), None)
            if matched:
                try:
                    json_str = script.string.split(matched)[1].strip().rstrip(';')
                    data = json.loads(json_str)
                    json_events = _parse_espn_schedule_json(data, seen_ids)
                    if json_events:
                        log.info(f"  __espnfitt__ JSON returned {len(json_events)} events for {year}")
                        year_events = json_events
                except Exception as e:
                    log.warning(f"  Schedule JSON parse error: {e}")
                break

        if year_events:
            events.extend(year_events)
            time.sleep(1)
            continue

        # Strategy 3: DOM table fallback
        log.info(f"  Falling back to DOM parsing for {year}")
        tables = soup.find_all('table', class_='Table')
        for table in tables:
            for row in table.find_all('tr', class_='Table__TR'):
                event_col = row.find('td', class_='event__col')
                if not event_col:
                    continue
                link = event_col.find('a')
                if not link:
                    continue

                event_name = link.get_text(strip=True)
                event_url = link.get('href', '')
                match = re.search(r'/id/(\d+)', event_url)
                event_id = match.group(1) if match else None
                if not event_id or event_id in seen_ids:
                    continue

                if event_url.startswith('/'):
                    event_url = 'https://www.espn.com' + event_url

                date_col = row.find('td', class_='date__col')
                date_text = date_col.get_text(strip=True) if date_col else 'TBD'
                loc_col = row.find('td', class_='location__col')
                location = loc_col.get_text(strip=True) if loc_col else 'TBD'

                try:
                    clean_date = re.sub(r'^[A-Za-z]+,\s*', '', date_text)
                    event_date = datetime.strptime(f"{clean_date} {year}", "%b %d %Y").date()
                except Exception:
                    continue

                seen_ids.add(event_id)
                year_events.append({
                    'event_id': event_id,
                    'event_name': event_name,
                    'date': event_date,
                    'location': location,
                    'url': event_url,
                })

        events.extend(year_events)
        time.sleep(1)

    events.sort(key=lambda x: x['date'])

    past = [e for e in events if e['date'] < today]
    future = [e for e in events if e['date'] >= today]

    result = []
    if past:
        last = past[-1]
        last['is_completed'] = True
        result.append(last)
    for e in future:
        e['is_completed'] = False
        result.append(e)

    return result


def scrape_event_details(event_url, event_id):
    """Scrape fight card from ESPN event page. Returns list of fight dicts."""
    log.info(f"  Scraping event details: {event_url}")
    soup = get_soup(event_url)
    if not soup:
        return []

    fights = []

    # Strategy 1: embedded __espnfitt__ JSON (most reliable)
    # ESPN uses both window['__espnfitt__']= and window.__espnfitt__ = variants
    _ESPNFITT_PATTERNS = [
        "window['__espnfitt__']=",
        'window["__espnfitt__"]=',
        "window.__espnfitt__ =",
        "window.__espnfitt__=",
    ]
    for script in soup.find_all('script'):
        if not script.string:
            continue
        matched_pattern = next(
            (p for p in _ESPNFITT_PATTERNS if p in script.string), None
        )
        if matched_pattern:
            try:
                content = script.string
                json_str = content.split(matched_pattern)[1].strip().rstrip(';')
                data = json.loads(json_str)
                gp = data.get('page', {}).get('content', {}).get('gamepackage', {})
                if 'cardSegs' in gp:
                    for seg in gp['cardSegs']:
                        is_main = seg.get('nm') == 'main'
                        for m in seg.get('mtchs', []):
                            awy = m.get('awy', {})
                            hme = m.get('hme', {})
                            n1 = awy.get('dspNm')
                            n2 = hme.get('dspNm')
                            if not n1 or not n2:
                                continue
                            u1 = awy.get('lnk', '')
                            u2 = hme.get('lnk', '')
                            if u1 and not u1.startswith('http'):
                                u1 = 'https://www.espn.com' + u1
                            if u2 and not u2.startswith('http'):
                                u2 = 'https://www.espn.com' + u2

                            note = m.get('nte', '')
                            is_title = bool(note and 'Title Fight' in note)

                            s1 = scrape_fighter_profile(u1)
                            s2 = scrape_fighter_profile(u2)
                            time.sleep(0.3)

                            result_data = None
                            if m.get('status', {}).get('state') == 'post':
                                winner = None
                                if awy.get('isWin'):
                                    winner = n1
                                elif hme.get('isWin'):
                                    winner = n2
                                result_data = {
                                    'winner': winner,
                                    'method': m.get('dec', {}).get('shrtDspNm'),
                                    'time': m.get('status', {}).get('dspClk'),
                                    'round': m.get('status', {}).get('rd'),
                                }

                            fights.append({
                                'fighter_1': n1,
                                'fighter_2': n2,
                                'fighter_1_url': u1,
                                'fighter_2_url': u2,
                                'f1_stats': s1,
                                'f2_stats': s2,
                                'is_main_card': is_main,
                                'is_title_fight': is_title,
                                'result': result_data,
                                'weight_class': m.get('wght', ''),
                            })
                    return fights
            except Exception as e:
                log.warning(f"  JSON parse error: {e}")

    # Strategy 2: DOM fallback
    panels = soup.select('li.AccordionPanel') or soup.select('div.MMAGamestrip')
    is_main = True
    for node in soup.find_all(['h3', 'li', 'div']):
        classes = node.get('class', [])
        if any(c in classes for c in ('Card__Header__Title', 'Card__Header')):
            txt = node.get_text(strip=True)
            if 'Prelim' in txt:
                is_main = False
            elif 'Main Card' in txt:
                is_main = True
            continue
        if 'AccordionPanel' not in classes and 'MMAGamestrip' not in classes:
            continue
        competitors = node.find_all('div', class_='MMACompetitor')
        if len(competitors) < 2:
            continue
        c1, c2 = competitors[0], competitors[1]
        h1 = c1.find('h2')
        h2 = c2.find('h2')
        n1 = re.sub(r'\d+-\d+-\d+$', '', h1.get_text(strip=True) if h1 else '').strip()
        n2 = re.sub(r'\d+-\d+-\d+$', '', h2.get_text(strip=True) if h2 else '').strip()
        if not n1 or not n2:
            continue
        fights.append({
            'fighter_1': n1,
            'fighter_2': n2,
            'fighter_1_url': '',
            'fighter_2_url': '',
            'f1_stats': {},
            'f2_stats': {},
            'is_main_card': is_main,
            'is_title_fight': False,
            'result': None,
            'weight_class': '',
        })

    return fights


# ── Database helpers ──────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def load_fight_history(conn):
    """Load all historical fights from mma_fights + mma_events for stat calculation."""
    sql = """
        SELECT
            f.fighter_1_id, f.fighter_2_id,
            f.fighter_1_name, f.fighter_2_name,
            f.winner_name, f.method, f.round_ended, f.time_ended,
            e.date,
            -- raw stats (stored as JSON in fighter_1_id/fighter_2_id fallback)
            -- We only have what was seeded from Octagon-AI CSVs
            NULL AS str1, NULL AS str2,
            NULL AS td1, NULL AS td2,
            NULL AS kd1, NULL AS kd2,
            NULL AS sub1, NULL AS sub2,
            NULL AS ctrl1, NULL AS ctrl2,
            NULL AS sig_acc1, NULL AS sig_acc2
        FROM mma_fights f
        JOIN mma_events e ON f.event_id = e.id
        WHERE e.is_completed = TRUE
          AND f.winner_name IS NOT NULL
        ORDER BY e.date ASC
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def load_fighters_bio(conn):
    """Load fighter bio data from mma_fighters."""
    sql = """
        SELECT id, full_name, height_cm, reach_cm, stance,
               glicko_rating, glicko_rd
        FROM mma_fighters
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return {r[0]: {'height': r[2], 'reach': r[3], 'stance': r[4],
                   'glicko': r[5], 'glicko_rd': r[6], 'name': r[1]}
            for r in rows}


def rebuild_stats_from_db(conn):
    """
    Rebuild EMA stats tracker from fight history in Postgres.
    Returns dict: fighter_id -> FighterStats
    Also builds name -> fighter_id map.
    """
    log.info("Rebuilding fighter stats from DB fight history...")
    stats_tracker = {}
    name_to_id = {}

    # Build name map
    sql = "SELECT id, full_name FROM mma_fighters"
    with conn.cursor() as cur:
        cur.execute(sql)
        for fid, name in cur.fetchall():
            norm = normalize_name(name)
            if norm:
                name_to_id[norm] = fid

    # Load fights chronologically
    rows = load_fight_history(conn)
    log.info(f"  Processing {len(rows)} historical fights")

    def pars_time(t_str, r_num):
        try:
            m, s = map(int, str(t_str).split(':'))
            return (int(r_num) - 1) * 300 + m * 60 + s
        except Exception:
            return 300  # default 5 min

    for row in rows:
        fid1, fid2, n1, n2, winner, method, rnd, t_str, fight_date = row[:9]
        if not fight_date:
            continue
        if isinstance(fight_date, str):
            try:
                fight_date = pd.to_datetime(fight_date)
            except Exception:
                continue

        time_sec = pars_time(t_str, rnd) if t_str and rnd else 300
        result_1 = 'W' if winner == n1 else ('L' if winner else 'D')
        result_2 = 'L' if winner == n1 else ('W' if winner else 'D')

        # fid1/fid2 may be NULL when the seed CSV didn't link fighter IDs;
        # fall back to the name map so historical fights still build stats.
        if not fid1:
            fid1 = name_to_id.get(normalize_name(n1))
        if not fid2:
            fid2 = name_to_id.get(normalize_name(n2))

        for fid, result in [(fid1, result_1), (fid2, result_2)]:
            if not fid:
                continue
            if fid not in stats_tracker:
                stats_tracker[fid] = FighterStats()
            # We don't have per-round stats from historical seed — use defaults
            stats_tracker[fid].update(
                result, fight_date, time_sec,
                s_landed=3.5 * (time_sec / 60), s_absorbed=3.5 * (time_sec / 60),
                td_landed=1, td_att=2.5, opp_td_att=2.5, opp_td_landed=1,
                kd=0, sub=0, ctrl=0,
                sig_acc=0.45, head_p=0.7, body_p=0.15, leg_p=0.15,
                dist_p=0.8, clin_p=0.1, grou_p=0.1
            )

    log.info(f"  Stats built for {len(stats_tracker)} fighters")
    return stats_tracker, name_to_id


# ── Prediction model ──────────────────────────────────────────────────────────

def load_model():
    if not os.path.exists(MODEL_PATH):
        log.warning(f"Model not found at {MODEL_PATH}. Predictions will be 50/50.")
        return None
    try:
        model = joblib.load(MODEL_PATH)
        log.info("Model loaded successfully")
        return model
    except Exception as e:
        log.warning(f"Could not load model: {e}")
        return None


def map_weight_class(raw):
    """
    Map ESPN weight class strings to the '### lbs' format the model was trained on.
    Falls back to '155 lbs' (Lightweight) when the value is unrecognised.
    """
    if not raw:
        return '155 lbs'
    lc = str(raw).lower().strip()
    # Already in correct format (e.g. "155 lbs") — pass through directly
    if lc.endswith(' lbs') and lc.split()[0].isdigit():
        return lc
    mapping = {
        'heavyweight': '265 lbs',
        'light heavyweight': '205 lbs',
        'middleweight': '185 lbs',
        'welterweight': '170 lbs',
        'lightweight': '155 lbs',
        'featherweight': '145 lbs',
        'bantamweight': '135 lbs',
        'flyweight': '125 lbs',
        "women's strawweight": '115 lbs',
        "women's flyweight": '125 lbs',
        "women's bantamweight": '135 lbs',
        "women's featherweight": '145 lbs',
    }
    return mapping.get(lc, '155 lbs')


def build_feature_row(st1, st2, b1, b2, g1, g2, is_apex=0, is_altitude=0,
                      weight_class=''):
    """Build a feature dict matching the CatBoost model's expected columns."""
    ath_age1 = st1.get('ath_age', 0)
    ath_age2 = st2.get('ath_age', 0)
    g_diff = float(np.clip(g1.get('rating', 1500) - g2.get('rating', 1500), -250, 250))

    return {
        'glicko_diff': g_diff,
        'glicko_rd_diff': g1.get('rd', 350) - g2.get('rd', 350),
        'age_diff': ath_age1 - ath_age2,
        'height_diff': (b1.get('height') or 175) - (b2.get('height') or 175),
        'reach_diff': (b1.get('reach') or 175) - (b2.get('reach') or 175),
        'slpm_diff': st1['slpm'] - st2['slpm'],
        'sapm_diff': st1['sapm'] - st2['sapm'],
        'td_avg_diff': st1['td_avg'] - st2['td_avg'],
        'td_acc_diff': st1['td_acc'] - st2['td_acc'],
        'td_def_diff': st1['td_def'] - st2['td_def'],
        'kd_diff': st1['kd_rate'] - st2['kd_rate'],
        'sub_diff': st1['sub_rate'] - st2['sub_rate'],
        'ctrl_diff': st1['ctrl_rate'] - st2['ctrl_rate'],
        'sig_acc_diff': st1['sig_str_acc'] - st2['sig_str_acc'],
        'head_pct_diff': st1['head_pct'] - st2['head_pct'],
        'body_pct_diff': st1['body_pct'] - st2['body_pct'],
        'leg_pct_diff': st1['leg_pct'] - st2['leg_pct'],
        'dist_pct_diff': st1['dist_pct'] - st2['dist_pct'],
        'clinch_pct_diff': st1['clinch_pct'] - st2['clinch_pct'],
        'ground_pct_diff': st1['ground_pct'] - st2['ground_pct'],
        'exp_diff': (st1['exp_time'] - st2['exp_time']) / 60.0,
        'streak_diff': st1['streak'] - st2['streak'],
        'win_rate_diff': st1['win_rate'] - st2['win_rate'],
        'rust_diff': st1['rust_days'] - st2['rust_days'],
        'activity_diff': st1['recent_fights_count'] - st2['recent_fights_count'],
        'is_apex': is_apex,
        'is_altitude': is_altitude,
        'stance_1': b1.get('stance') or 'Orthodox',
        'stance_2': b2.get('stance') or 'Orthodox',
        'weight_class': map_weight_class(weight_class),
    }


def predict_fight(model, st1, st2, b1, b2, g1, g2, is_apex=0, is_altitude=0,
                  weight_class=''):
    """Returns probability that fighter 1 wins."""
    if model is None:
        return 0.5
    try:
        features = build_feature_row(st1, st2, b1, b2, g1, g2, is_apex, is_altitude,
                                     weight_class=weight_class)
        df = pd.DataFrame([features])
        prob = model.predict_proba(df)[0][1]
        return float(prob)
    except Exception as e:
        log.warning(f"Prediction error: {e}")
        return 0.5


HIGH_ALT_CITIES = ['salt lake city', 'mexico city', 'denver', 'albuquerque',
                    'bogota', 'quito', 'johannesburg']


def is_altitude(location):
    if not location:
        return 0
    loc = str(location).lower()
    return 1 if any(c in loc for c in HIGH_ALT_CITIES) else 0


def is_apex_event(name, location):
    n = str(name or '').lower()
    l = str(location or '').lower()
    return 1 if ('fight night' in n and 'las vegas' in l) or 'apex' in l else 0


# ── Write to DB ───────────────────────────────────────────────────────────────

def upsert_event(conn, event):
    sql = """
        INSERT INTO mma_events (id, name, date, location, is_completed, espn_url, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            date = EXCLUDED.date,
            location = EXCLUDED.location,
            is_completed = EXCLUDED.is_completed,
            updated_at = NOW()
    """
    with conn.cursor() as cur:
        cur.execute(sql, (
            event['event_id'],
            event['event_name'],
            event['date'],
            event['location'],
            event['is_completed'],
            event.get('url', ''),
        ))
    conn.commit()
  
def parse_round(val):
    """Convert 'R3' or 3 or '3' to int, or None."""
    if val is None:
        return None
    try:
        return int(str(val).replace('R', '').replace('r', '').strip())
    except (ValueError, TypeError):
        return None

def upsert_fight(conn, event_id, fight):
    """Insert fight if not already present. Returns fight DB id."""
    # Check if fight already exists (by event + fighter names)
    sql_check = """
        SELECT id FROM mma_fights
        WHERE event_id = %s
          AND fighter_1_name = %s
          AND fighter_2_name = %s
        LIMIT 1
    """
    with conn.cursor() as cur:
        cur.execute(sql_check, (event_id, fight['fighter_1'], fight['fighter_2']))
        existing = cur.fetchone()
        if existing:
            fight_id = existing[0]
            # Update result if completed
            if fight.get('result'):
                r = fight['result']
                cur.execute("""
                    UPDATE mma_fights SET
                        winner_name = %s, method = %s,
                        round_ended = %s, time_ended = %s
                    WHERE id = %s
                """, (r.get('winner'), r.get('method'),
                      parse_round(r.get('round')), r.get('time'), fight_id))
                conn.commit()
            return fight_id

        cur.execute("""
            INSERT INTO mma_fights
                (event_id, fighter_1_name, fighter_2_name,
                 weight_class, is_main_card, is_title_fight,
                 f1_height, f1_reach, f1_stance, f1_record,
                 f2_height, f2_reach, f2_stance, f2_record,
                 winner_name, method, round_ended, time_ended,
                 created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            RETURNING id
        """, (
            event_id,
            fight['fighter_1'], fight['fighter_2'],
            fight.get('weight_class', ''),
            fight.get('is_main_card', False),
            fight.get('is_title_fight', False),
            fight.get('f1_stats', {}).get('Height'),
            fight.get('f1_stats', {}).get('Reach'),
            fight.get('f1_stats', {}).get('Stance'),
            fight.get('f1_stats', {}).get('Record'),
            fight.get('f2_stats', {}).get('Height'),
            fight.get('f2_stats', {}).get('Reach'),
            fight.get('f2_stats', {}).get('Stance'),
            fight.get('f2_stats', {}).get('Record'),
            fight.get('result', {}).get('winner') if fight.get('result') else None,
            fight.get('result', {}).get('method') if fight.get('result') else None,
            parse_round(fight.get('result', {}).get('round')) if fight.get('result') else None,
            fight.get('result', {}).get('time') if fight.get('result') else None,
        ))
        conn.commit()
        return cur.fetchone()[0]


def upsert_prediction(conn, fight_id, pred):
    sql = """
        INSERT INTO mma_predictions
            (fight_id, predicted_winner, f1_win_probability, f2_win_probability,
             confidence, factors_json, generated_at)
        VALUES (%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (fight_id) DO UPDATE SET
            predicted_winner   = EXCLUDED.predicted_winner,
            f1_win_probability = EXCLUDED.f1_win_probability,
            f2_win_probability = EXCLUDED.f2_win_probability,
            confidence         = EXCLUDED.confidence,
            factors_json       = EXCLUDED.factors_json,
            generated_at       = NOW()
    """
    # Add unique constraint on fight_id to mma_predictions if not present
    with conn.cursor() as cur:
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'mma_predictions_fight_id_key'
                ) THEN
                    ALTER TABLE mma_predictions ADD CONSTRAINT mma_predictions_fight_id_key UNIQUE (fight_id);
                END IF;
            END $$;
        """)
        cur.execute(sql, (
            fight_id,
            pred['winner'],
            pred['f1_prob'],
            pred['f2_prob'],
            pred['confidence'],
            json.dumps(pred['factors']),
        ))
    conn.commit()


# ── Main sync flow ────────────────────────────────────────────────────────────

def main():
    log.info("=== MMA Sync Starting ===")

    if not DATABASE_URL:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    conn = get_conn()

    # Load model
    model = load_model()

    # Rebuild stats from DB fight history
    stats_tracker, name_to_id = rebuild_stats_from_db(conn)

    # Load fighter bio
    fighter_bio = load_fighters_bio(conn)

    # Scrape upcoming events
    log.info("Scraping ESPN schedule...")
    events = scrape_upcoming_events()
    log.info(f"Found {len(events)} events to process")

    today = datetime.utcnow()
    default_stats = FighterStats()
    default_sv = default_stats.get_stat_vector(today)

    for event in events:
        log.info(f"Processing: {event['event_name']} ({event['date']})")

        # Upsert event
        upsert_event(conn, event)

        # Scrape fight card
        fights = scrape_event_details(event['url'], event['event_id'])
        log.info(f"  {len(fights)} fights on card")

        for fight in fights:
            fight_id = upsert_fight(conn, event['event_id'], fight)

            # Skip prediction for completed fights that already have one
            if event['is_completed']:
                log.info(f"  Skipping prediction for completed fight: "
                         f"{fight['fighter_1']} vs {fight['fighter_2']}")
                continue

            # Resolve fighter IDs
            f1_norm = normalize_name(fight['fighter_1'])
            f2_norm = normalize_name(fight['fighter_2'])
            fid1 = name_to_id.get(f1_norm)
            fid2 = name_to_id.get(f2_norm)

            st1 = stats_tracker.get(fid1, default_stats).get_stat_vector(today) if fid1 else default_sv
            st2 = stats_tracker.get(fid2, default_stats).get_stat_vector(today) if fid2 else default_sv

            b1 = fighter_bio.get(fid1, {})
            b2 = fighter_bio.get(fid2, {})

            g1 = {'rating': b1.get('glicko', 1500), 'rd': b1.get('glicko_rd', 350)}
            g2 = {'rating': b2.get('glicko', 1500), 'rd': b2.get('glicko_rd', 350)}

            apex = is_apex_event(event['event_name'], event['location'])
            alt = is_altitude(event['location'])

            prob = predict_fight(model, st1, st2, b1, b2, g1, g2, apex, alt,
                                weight_class=fight.get('weight_class', ''))

            winner = fight['fighter_1'] if prob > 0.5 else fight['fighter_2']
            confidence = f"{max(prob, 1 - prob) * 100:.1f}%"

            pred = {
                'winner': winner,
                'f1_prob': prob,
                'f2_prob': 1.0 - prob,
                'confidence': confidence,
                'factors': {
                    fight['fighter_1']: {
                        'slpm': round(st1['slpm'], 2),
                        'td_avg': round(st1['td_avg'], 2),
                        'ctrl_rate': round(st1['ctrl_rate'], 2),
                        'kd_rate': round(st1['kd_rate'], 2),
                        'wins': st1['wins'],
                        'losses': st1['losses'],
                        'recent_form': st1['recent_form'],
                        'glicko': round(g1['rating']),
                    },
                    fight['fighter_2']: {
                        'slpm': round(st2['slpm'], 2),
                        'td_avg': round(st2['td_avg'], 2),
                        'ctrl_rate': round(st2['ctrl_rate'], 2),
                        'kd_rate': round(st2['kd_rate'], 2),
                        'wins': st2['wins'],
                        'losses': st2['losses'],
                        'recent_form': st2['recent_form'],
                        'glicko': round(g2['rating']),
                    },
                }
            }

            upsert_prediction(conn, fight_id, pred)
            log.info(f"  Predicted: {winner} ({confidence}) — "
                     f"{fight['fighter_1']} vs {fight['fighter_2']}")

        time.sleep(1)

    conn.close()
    log.info("=== MMA Sync Complete ===")


if __name__ == '__main__':
    main()
