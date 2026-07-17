"""
mma_sync.py - Weekly Railway cron job.

What it does (in order):
  1. Scrapes ESPN for upcoming UFC events + fight cards
  2. Re-calculates current fighter EMA stats from Postgres fight history
  3. Loads the trained CatBoost model and generates win probabilities
  4. Writes upcoming events, fights, and predictions to Postgres
  5. Also scrapes ESPN for the most recently completed event to capture results
  6. Fetches UFC h2h fight odds from The Odds API → mma_fight_odds table

Railway cron schedule: 0 9 * * 0  (Sundays at 9am UTC)

Environment variables required:
  DATABASE_URL  – already in Railway
  ODDS_API_KEY  – optional; if set, UFC fight odds are fetched and stored
"""

import os
import sys
import json
import time
import re
import unicodedata
import math
import logging
from datetime import datetime, date, timedelta

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

# Historical fight data source of truth.
#
# The model was originally trained on sbalagan22/Octagon-AI's newdata/*.csv
# files, and that dataset was one-time seeded into Postgres via mma_seed.py
# (see mma_fights / mma_fighters / mma_events). The upstream GitHub repo has
# since been taken down (confirmed 404, not a transient CDN issue), so it can
# no longer be re-pulled on every sync run. Postgres is now the sole source
# of historical fight data: it holds the original seed and is kept current
# automatically, since this script writes each newly-completed fight back to
# mma_fights as part of its normal sync flow (see rebuild_stats_from_db()).
MIN_COMPLETE_DB_FIGHTS_FOR_PRIMARY_HISTORY = 1000
MIN_DB_FIGHTERS_FOR_PRIMARY_HISTORY = 500

MIN_COMPLETE_UFC_BOUTS = 4
MIN_COMPLETE_CARD_RATIO = 0.75
CANONICAL_CARD_SOURCES = {'espn_scoreboard_json', 'espn_summary_json', 'espn_embedded_json'}
ESPN_SCOREBOARD_URL = 'https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard'

# ── Tunable model constants ───────────────────────────────────────────────────
# These are reasonable-default heuristics, not values fit/validated against
# held-out data in this repo (there is no in-repo training or backtesting
# pipeline for the CatBoost model — see mma_backtest.py for read-only
# accuracy reporting on completed fights). Revisit if/when that changes.

# Exponential-moving-average smoothing factor used by FighterStats.update()
# for rolling striking/grappling stats. Higher = more weight on recent fights.
EMA_SMOOTHING_ALPHA = 0.3

# Glicko-2 rating-diff feature is clipped to +/-250 before being handed to the
# model, since Octagon-AI's training data rarely has ratings further apart
# than this and unclipped outliers could push predictions outside the range
# the model was actually trained on.
GLICKO_DIFF_CLIP = 250

# Fight cities considered "high altitude" for the is_altitude feature — UFC
# events here are anecdotally associated with faster cardio fade.
HIGH_ALTITUDE_CITIES = [
    'salt lake city', 'mexico city', 'denver', 'albuquerque', 'bogota',
    'quito', 'johannesburg',
]


HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/126.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': 'https://www.espn.com/mma/',
}

# ── Name normalisation (matches Octagon-AI predict_model.py) ─────────────────
# Canonical implementation lives in mma_name_utils.py and is shared with
# mma_data.py so the two modules can't silently drift apart on alias tables.
from mma_name_utils import (
    normalize_name,
    normalized_name_aliases,
    names_match,
    unordered_pair_key,
    pairs_match,
)


def is_placeholder_fighter_name(name):
    """Return True for ESPN/card placeholder names that are not real fighters."""
    norm = normalize_name(name)
    return not norm or norm in {'tba', 'tbd', 'opponent tba', 'opponent tbd'}


def fight_has_placeholder(fight):
    return (
        is_placeholder_fighter_name(fight.get('fighter_1'))
        or is_placeholder_fighter_name(fight.get('fighter_2'))
    )


def canonical_bout_uid(event_id, fight):
    """Return the official source bout ID scoped to the ESPN event.

    Name-derived keys are only a last-resort fallback for providers that do not
    expose a stable bout identifier (for example Odds API fallback seeding).
    """
    for key in ('bout_uid', 'competition_id', 'match_id', 'id'):
        value = fight.get(key)
        if value:
            return f"espn:{event_id}:{value}"

    f1_id = fight.get('fighter_1_espn_id') or fight.get('fighter_1_id') or ''
    f2_id = fight.get('fighter_2_espn_id') or fight.get('fighter_2_id') or ''
    if f1_id and f2_id:
        ordered = ':'.join(sorted([str(f1_id), str(f2_id)]))
        return f"event-fighters:{event_id}:{ordered}"

    names = ':'.join(sorted([normalize_name(fight.get('fighter_1')), normalize_name(fight.get('fighter_2'))]))
    return f"fallback-names:{event_id}:{names}"


def fight_status(fight):
    raw = str(fight.get('status') or '').lower()
    if raw in {'cancelled', 'canceled', 'postponed'}:
        return 'cancelled'
    if fight_has_placeholder(fight):
        return 'placeholder'
    return 'completed' if fight.get('result') else 'confirmed'


def has_sufficient_feature_data(fid, stats_tracker, fighter_bio=None):
    if not fid:
        return False
    if fid in stats_tracker and getattr(stats_tracker[fid], 'total_fights', 0) > 0:
        return True
    bio = (fighter_bio or {}).get(fid, {})
    return fighter_feature_source(bio).get('usable', False)


def fighter_feature_source(bio):
    """Return persisted feature-source diagnostics for an mma_fighters row.

    The prediction model consumes engineered current-form features.  Those are
    persisted on mma_fighters by the Octagon refresh, so inference must not
    require the transient in-memory CSV/DB history rebuild to contain the fighter.
    """
    total_fights = int(bio.get('total_fights') or 0)
    required = [
        'glicko', 'glicko_rd', 'slpm', 'sapm', 'td_acc', 'td_avg', 'td_def',
        'kd_rate', 'sub_rate', 'ctrl_rate', 'sig_str_acc', 'streak', 'win_rate',
    ]
    usable = total_fights > 0 and all(bio.get(k) is not None for k in required)
    return {'source': 'mma_fighters', 'total_fights': total_fights, 'usable': usable}


def stat_vector_from_fighter_bio(bio, current_date=None):
    """Build the model stat vector from persisted mma_fighters columns.

    Defaults mirror FighterStats for truly new fighters; established fighters use
    stored EMA/Glicko values even when raw historical rows/CSV are unavailable.
    """
    total_fights = int(bio.get('total_fights') or 0)
    win_rate = bio.get('win_rate')
    wins = int(round(float(win_rate or 0.5) * total_fights)) if total_fights else 0
    return {
        'slpm': float(bio.get('slpm') if bio.get('slpm') is not None else 0.0),
        'sapm': float(bio.get('sapm') if bio.get('sapm') is not None else 0.0),
        'td_acc': float(bio.get('td_acc') if bio.get('td_acc') is not None else 0.4),
        'td_avg': float(bio.get('td_avg') if bio.get('td_avg') is not None else 1.0),
        'td_def': float(bio.get('td_def') if bio.get('td_def') is not None else 0.5),
        'kd_rate': float(bio.get('kd_rate') if bio.get('kd_rate') is not None else 0.2),
        'sub_rate': float(bio.get('sub_rate') if bio.get('sub_rate') is not None else 0.2),
        'ctrl_rate': float(bio.get('ctrl_rate') if bio.get('ctrl_rate') is not None else 10.0),
        'sig_str_acc': float(bio.get('sig_str_acc') if bio.get('sig_str_acc') is not None else 0.45),
        'head_pct': 0.7, 'body_pct': 0.15, 'leg_pct': 0.15,
        'dist_pct': 0.8, 'clinch_pct': 0.1, 'ground_pct': 0.1,
        'exp_time': total_fights * 15 * 60,
        'wins': wins, 'losses': max(total_fights - wins, 0),
        'streak': int(bio.get('streak') or 0),
        'win_rate': float(win_rate if win_rate is not None else 0.5),
        'rust_days': 365,
        'recent_fights_count': min(total_fights, 5),
        'ath_age': 0,
        'recent_form': bio.get('recent_form') or 'N/A',
    }


def stat_vector_for_fighter(fid, stats_tracker, fighter_bio, current_date, default_sv):
    if fid in stats_tracker and getattr(stats_tracker[fid], 'total_fights', 0) > 0:
        return stats_tracker[fid].get_stat_vector(current_date)
    bio = fighter_bio.get(fid, {})
    if fighter_feature_source(bio).get('usable'):
        return stat_vector_from_fighter_bio(bio, current_date)
    return default_sv


def best_name_match_key(name, lookup):
    """Find a normalised key using exact/alias, containment, then unique last-name matching."""
    aliases = normalized_name_aliases(name)
    for alias in aliases:
        if alias in lookup:
            return alias

    containment = [key for key in lookup if key and any(alias in key or key in alias for alias in aliases)]
    if len(containment) == 1:
        return containment[0]

    lasts = {alias.split()[-1] for alias in aliases if alias.split()}
    last_matches = [key for key in lookup if key.split() and key.split()[-1] in lasts]
    return last_matches[0] if len(last_matches) == 1 else None


def extract_espn_id(espn_url):
    if not espn_url:
        return None
    m = re.search(r'/id/(\d+)(?:[/?]|$)', str(espn_url))
    return m.group(1) if m else None


def resolve_fighter_id(name, name_to_id, espn_id=None, espn_to_id=None):
    """Resolve canonical ESPN competitor to the existing mma_fighters/CSV ID.

    ESPN ID is preferred when a persisted mapping exists; normalized aliases are
    only a fallback and never create duplicate fighter rows.
    """
    if espn_id and espn_to_id and str(espn_id) in espn_to_id:
        return espn_to_id[str(espn_id)]
    for alias in normalized_name_aliases(name):
        if alias in name_to_id:
            return name_to_id[alias]
    key = best_name_match_key(name, name_to_id)
    return name_to_id.get(key) if key else None


def prediction_gate_reasons(fight, fid1, fid2, stats_tracker, feature_count=None, odds_matched=None, fighter_bio=None):
    reasons = []
    status = fight_status(fight)
    if status == 'cancelled':
        reasons.append('cancelled status')
    if status == 'placeholder':
        reasons.append('TBA fighter')
    if status != 'confirmed':
        reasons.append('inactive fight')
    for side, label, fid in (('fighter_1', fight.get('fighter_1'), fid1), ('fighter_2', fight.get('fighter_2'), fid2)):
        if not (fight.get(f'{side}_espn_id') or fight.get(f'{side}_id') or fight.get(f'{side}_url')):
            reasons.append(f'missing ESPN fighter ID:{label}')
        if not fid:
            reasons.append(f'missing fighter database row:{label}')
        elif not has_sufficient_feature_data(fid, stats_tracker, fighter_bio):
            reasons.append(f'insufficient feature count:{label}')
    if feature_count is not None and feature_count < 1:
        reasons.append('missing model inputs')
    if odds_matched is False:
        reasons.append('failed Odds API matchup')
    return reasons


def build_espn_to_fighter_id(conn, stats_tracker=None):
    """Map ESPN athlete IDs stored in mma_fighters.espn_url to canonical DB IDs.

    If a migration produced duplicate fighter rows with the same ESPN profile,
    prefer the row that already has historical stats, then keep the first stable
    ID. This relinks current ESPN card ingestion to legacy completed history.
    """
    mapping = {}
    rows = []
    with conn.cursor() as cur:
        cur.execute("SELECT id, full_name, espn_url FROM mma_fighters")
        rows = cur.fetchall()
        name_best = {}
        for fid, name, _espn_url in rows:
            norm = normalize_name(name)
            if not norm:
                continue
            current = name_best.get(norm)
            if current is None or getattr((stats_tracker or {}).get(fid), 'total_fights', 0) > getattr((stats_tracker or {}).get(current), 'total_fights', 0):
                name_best[norm] = fid
        for fid, name, espn_url in rows:
            espn_id = extract_espn_id(espn_url)
            if not espn_id:
                continue
            same_name_hist = name_best.get(normalize_name(name))
            if same_name_hist and getattr((stats_tracker or {}).get(same_name_hist), 'total_fights', 0) > getattr((stats_tracker or {}).get(fid), 'total_fights', 0):
                fid = same_name_hist
            current = mapping.get(espn_id)
            if current is None:
                mapping[espn_id] = fid
                continue
            cur_hist = getattr((stats_tracker or {}).get(current), 'total_fights', 0)
            new_hist = getattr((stats_tracker or {}).get(fid), 'total_fights', 0)
            if new_hist > cur_hist:
                mapping[espn_id] = fid
    return mapping


def build_name_to_fighter_id(conn, stats_tracker=None):
    """Map normalized names to the fighter row with the best historical link."""
    mapping = {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, full_name FROM mma_fighters")
        for fid, name in cur.fetchall():
            norm = normalize_name(name)
            if not norm:
                continue
            current = mapping.get(norm)
            if current is None:
                mapping[norm] = fid
                continue
            cur_hist = getattr((stats_tracker or {}).get(current), 'total_fights', 0)
            new_hist = getattr((stats_tracker or {}).get(fid), 'total_fights', 0)
            if new_hist > cur_hist:
                mapping[norm] = fid
    return mapping


def _alias_historical_stats_to_canonical_fighters(conn, stats_tracker):
    """Copy stat objects onto duplicate canonical fighter IDs when unambiguous."""
    rows = []
    with conn.cursor() as cur:
        cur.execute("SELECT id, full_name, espn_url FROM mma_fighters")
        rows = cur.fetchall()

    by_espn = {}
    by_name = {}
    for fid, name, espn_url in rows:
        espn_id = extract_espn_id(espn_url)
        if espn_id:
            by_espn.setdefault(espn_id, []).append(fid)
        norm = normalize_name(name)
        if norm:
            by_name.setdefault(norm, []).append(fid)

    for ids in list(by_espn.values()) + [ids for ids in by_name.values() if len(ids) == 2]:
        source = next((fid for fid in ids if fid in stats_tracker), None)
        if not source:
            continue
        for fid in ids:
            stats_tracker.setdefault(fid, stats_tracker[source])


def _db_history_row_count(conn, db_id=None, name=None):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM mma_fights f
            JOIN mma_events e ON f.event_id = e.id
            WHERE e.is_completed = TRUE
              AND e.date <= CURRENT_DATE
              AND f.winner_name IS NOT NULL
              AND (
                    (%s IS NOT NULL AND (f.fighter_1_id = %s OR f.fighter_2_id = %s))
                 OR (%s IS NOT NULL AND (
                        LOWER(f.fighter_1_name) = LOWER(%s)
                     OR LOWER(f.fighter_2_name) = LOWER(%s)
                 ))
              )
            """,
            (db_id, db_id, db_id, name, name, name),
        )
        return cur.fetchone()[0]


def log_history_lookup(conn, name, espn_id, db_id, stats_tracker, name_to_id, espn_to_id):
    aliases = sorted(normalized_name_aliases(name))
    keys = []
    if espn_id:
        keys.append(f"espn:{espn_id}->{espn_to_id.get(str(espn_id))}")
    keys.extend([f"name:{a}->{name_to_id.get(a)}" for a in aliases])
    matched_key = db_id if db_id in stats_tracker else None
    rows = _db_history_row_count(conn, db_id, name)
    log.info(
        "HISTORY_LOOKUP fighter=%s espn_id=%s db_id=%s lookup_keys=%s matched_key=%s rows=%s",
        name, espn_id, db_id, keys, matched_key, rows,
    )


def _log_established_fighter_history_assertion(conn, stats_tracker):
    names = ['Conor McGregor', 'Max Holloway', 'Robert Whittaker', 'Cody Garbrandt', 'Cory Sandhagen']
    counts = {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, full_name FROM mma_fighters")
        for fid, full_name in cur.fetchall():
            if normalize_name(full_name) in {normalize_name(n) for n in names}:
                counts[full_name] = getattr(stats_tracker.get(fid), 'total_fights', 0)
    if counts and all(v == 0 for v in counts.values()):
        raise RuntimeError(f"Established-fighter history assertion failed: {counts}")
    log.info("HISTORY_ASSERT established_counts=%s", counts)


def diagnose_odds_match(fight, event, odds_fights_by_date):
    event_date = event.get('date')
    try:
        event_date = pd.to_datetime(event_date).date()
    except Exception:
        try:
            event_date = datetime.fromisoformat(str(event_date)[:10]).date()
        except Exception:
            event_date = None
    candidates = odds_fights_by_date.get(event_date, []) if event_date else []
    for cand in candidates:
        if pairs_match(fight['fighter_1'], fight['fighter_2'], cand['fighter_1'], cand['fighter_2']):
            log.info(
                "ODDS_MATCH fight=%s vs %s matched=true bookmaker_count=%s prices=%s reason=%s",
                fight['fighter_1'], fight['fighter_2'], cand.get('bookmaker_count', 0), cand.get('prices', []),
                "canonical_name_pair",
            )
            return {'matched': True, 'reason': 'canonical_name_pair'}
    reason = 'no_odds_api_event_same_date' if not candidates else 'no_canonical_pair_match'
    log.info(
        "ODDS_MATCH fight=%s vs %s matched=false bookmaker_count=0 prices=[] reason=%s",
        fight['fighter_1'], fight['fighter_2'], reason,
    )
    return {'matched': False, 'reason': reason}


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

        alpha = EMA_SMOOTHING_ALPHA
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


def _current_commit_sha():
    """Best-effort deployed commit SHA for production cron diagnostics."""
    for key in ('RAILWAY_GIT_COMMIT_SHA', 'GIT_COMMIT_SHA', 'SOURCE_VERSION', 'COMMIT_SHA'):
        if os.environ.get(key):
            return os.environ[key]
    try:
        import subprocess
        return subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=BASE_DIR, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return 'unknown'


def _espn_page_metrics(soup):
    title = ''
    if soup:
        title_node = soup.find('title')
        title = title_node.get_text(' ', strip=True) if title_node else ''
    text = soup.get_text(' ', strip=True) if soup else ''
    record_candidates = len(re.findall(r'\b(?:\d+|--)-(?:\d+|--)(?:-(?:\d+|--))?(?:\s*\(NC\))?\b', text))
    return {
        'page_title': title,
        'h2': len(soup.find_all('h2')) if soup else 0,
        'h3': len(soup.find_all('h3')) if soup else 0,
        'record_candidates': record_candidates,
        'script_count': len(soup.find_all('script')) if soup else 0,
    }


def _log_espn_parser(parser_name, invoked, response_meta, soup, bouts):
    metrics = _espn_page_metrics(soup)
    log.info(
        "ESPN_PARSER parser=%s invoked=%s status=%s final_url=%s content_type=%s "
        "bytes=%s title=%r h2=%s h3=%s record_candidates=%s bouts=%s",
        parser_name,
        str(bool(invoked)).lower(),
        response_meta.get('status'),
        response_meta.get('final_url'),
        response_meta.get('content_type'),
        response_meta.get('byte_length'),
        metrics['page_title'],
        metrics['h2'],
        metrics['h3'],
        metrics['record_candidates'],
        len(bouts or []),
    )


def _log_failed_heading_diagnostics(html, soup):
    metrics = _espn_page_metrics(soup)
    sample = (html or '')[:500].replace('\n', '\\n').replace('\r', '\\r')
    lower = (html or '').lower()
    log.warning(
        "ESPN_HEADING_ZERO_DIAGNOSTIC title=%r scripts=%s contains_access_denied=%s "
        "contains_captcha=%s contains_enable_javascript=%s contains_fightcenter=%s "
        "contains_ufc_329=%s sample=%r",
        metrics['page_title'],
        metrics['script_count'],
        'access denied' in lower,
        'captcha' in lower,
        'enable javascript' in lower or 'enable javascript' in lower.replace(' ', ''),
        'fightcenter' in lower,
        'ufc 329' in lower,
        sample,
    )


def _save_espn_debug_html(event_id, html):
    if os.environ.get('MMA_ESPN_DEBUG') != '1':
        return
    path = os.path.join(tempfile.gettempdir(), f"mma_espn_{event_id}.html")
    try:
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(html or '')
        log.warning("ESPN_DEBUG_HTML_SAVED event_id=%s path=%s bytes=%s", event_id, path, len((html or '').encode('utf-8')))
    except Exception as exc:
        log.warning("ESPN_DEBUG_HTML_SAVE_FAILED event_id=%s error=%s", event_id, exc)


def is_espn_waf_challenge(html, response_meta=None):
    lower = (html or '').lower()
    status = (response_meta or {}).get('status')
    return (
        'awswafcookiedomainlist' in lower
        or 'gokuprops' in lower
        or ('enable javascript' in lower and status == 202)
    )


def _scoreboard_url_for_year(year):
    return f"{ESPN_SCOREBOARD_URL}?limit=100&dates={year}0101-{year}1231"


def _log_scoreboard_event_structure(event):
    competitions = event.get('competitions') or []
    comp_ids = [str(c.get('id') or c.get('$ref') or '') for c in competitions]
    competitors = []
    refs = []
    links = event.get('links') or []
    for comp in competitions:
        if comp.get('$ref'):
            refs.append(comp.get('$ref'))
        if isinstance(comp.get('competition'), dict) and comp['competition'].get('$ref'):
            refs.append(comp['competition'].get('$ref'))
        for link in comp.get('links') or []:
            if link.get('href'):
                refs.append(link.get('href'))
        names = []
        for c in comp.get('competitors') or []:
            athlete = c.get('athlete') or {}
            names.append(athlete.get('displayName') or c.get('displayName') or c.get('name') or c.get('$ref') or '')
            if c.get('$ref'):
                refs.append(c.get('$ref'))
            if athlete.get('$ref'):
                refs.append(athlete.get('$ref'))
        if names:
            competitors.append(names)
    log.info(
        "ESPN_SCOREBOARD_EVENT_STRUCTURE event_id=%s event_keys=%s competition_count=%s competition_ids=%s competitors=%s links=%s refs=%s status=%s date=%s",
        event.get('id'), sorted(event.keys()), len(competitions), comp_ids, competitors, links, refs, event.get('status'), event.get('date')
    )


def _fetch_espn_scoreboard_event(event_id, year=None):
    years = [year] if year else [date.today().year, date.today().year + 1, date.today().year - 1]
    for yr in years:
        url = _scoreboard_url_for_year(yr)
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning(f"  ESPN scoreboard API fetch failed for {yr}: {e}")
            continue
        events = data.get('events') or []
        log.info("ESPN_SCOREBOARD_API url=%s events=%s", url, len(events))
        for ev in events:
            if str(ev.get('id')) == str(event_id):
                _log_scoreboard_event_structure(ev)
                return ev, url
    return None, None


def _fetch_espn_scoreboard_card(event_id):
    ev, url = _fetch_espn_scoreboard_event(event_id)
    if not ev:
        return []
    fights = _parse_espn_competitions(ev.get('competitions') or [])
    if fights:
        for f in fights:
            f['card_source'] = 'espn_scoreboard_json'
            f['verified'] = True
        log.info("  ESPN scoreboard API canonical card: %s fights source=%s endpoint=%s", len(fights), 'espn_scoreboard_json', url)
        return _enrich_fights_with_profiles(fights)
    return []


def _fetch_espn_event_html(event_url):
    meta = {'status': None, 'final_url': event_url, 'content_type': '', 'byte_length': 0}
    try:
        r = requests.get(event_url, headers=HEADERS, timeout=15, allow_redirects=True)
        meta.update({
            'status': getattr(r, 'status_code', None),
            'final_url': getattr(r, 'url', event_url),
            'content_type': getattr(r, 'headers', {}).get('content-type', ''),
            'byte_length': len(getattr(r, 'content', b'') or b''),
        })
        r.raise_for_status()
        html = (getattr(r, 'content', b'') or b'').decode(getattr(r, 'encoding', None) or 'utf-8', errors='replace')
        if is_espn_waf_challenge(html, meta):
            log.warning("ESPN_WAF_CHALLENGE=true status=%s final_url=%s bytes=%s", meta.get('status'), meta.get('final_url'), meta.get('byte_length'))
            return None, html, meta
        return BeautifulSoup(html, 'html.parser'), html, meta
    except Exception as e:
        log.warning(f"Failed to fetch {event_url}: {e}")
        return None, '', meta


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
        for _, week_data in schedule.items():
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
        venues = ev.get('venues', []) or []
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
    url = _scoreboard_url_for_year(year)
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



def _card_section_from_text(text, is_main=False):
    """Normalize ESPN card labels for stable API grouping."""
    label = str(text or '').strip().lower()
    if 'early' in label and 'prelim' in label:
        return 'early_prelims'
    if 'prelim' in label:
        return 'prelims'
    if is_main or 'main' in label:
        return 'main_card'
    return 'prelims'

def _parse_espn_card_segs(gp):
    """
    Parse fight data from an ESPN gamepackage dict that contains 'cardSegs'.
    Returns a list of partial fight dicts (no fighter profile stats yet –
    caller is responsible for calling scrape_fighter_profile if needed).
    """
    fights = []
    for seg in gp.get('cardSegs', []):
        seg_name = str(seg.get('nm') or seg.get('name') or seg.get('title') or '').lower()
        is_main = 'main' in seg_name
        card_section = _card_section_from_text(seg_name, is_main)
        for m in seg.get('mtchs', []):
            awy = m.get('awy', {})
            hme = m.get('hme', {})
            n1 = awy.get('dspNm')
            n2 = hme.get('dspNm')
            if not n1 or not n2 or (is_placeholder_fighter_name(n1) and is_placeholder_fighter_name(n2)):
                continue
            u1 = awy.get('lnk', '')
            u2 = hme.get('lnk', '')
            if u1 and not u1.startswith('http'):
                u1 = 'https://www.espn.com' + u1
            if u2 and not u2.startswith('http'):
                u2 = 'https://www.espn.com' + u2

            note = m.get('nte', '')
            status_type = m.get('status', {}).get('type', {}) if isinstance(m.get('status'), dict) else {}
            status_name = (status_type.get('name') or status_type.get('state') or m.get('status', {}).get('state') or '')
            is_title = bool(note and 'Title Fight' in note)

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
                'fighter_1_espn_id': awy.get('id') or awy.get('athleteId'),
                'fighter_2_espn_id': hme.get('id') or hme.get('athleteId'),
                'bout_uid': m.get('id') or m.get('uid') or m.get('guid'),
                'status': status_name,
                'is_main_card': is_main,
                'card_section': card_section,
                'is_title_fight': is_title,
                'result': result_data,
                'weight_class': m.get('wght', ''),
                '_source_complete': True,
            })
    return fights


def _enrich_fights_with_profiles(fights):
    """Add f1_stats / f2_stats by scraping each fighter's ESPN profile URL."""
    for fight in fights:
        fight['f1_stats'] = scrape_fighter_profile(fight.get('fighter_1_url', ''))
        fight['f2_stats'] = scrape_fighter_profile(fight.get('fighter_2_url', ''))
        time.sleep(0.3)
    return fights


def _fetch_espn_event_api(event_id):
    """
    Strategy 0: Fetch fight card directly from the ESPN public summary API.

    ESPN's fightcenter HTML pages now use client-side rendering for upcoming
    events, so the __espnfitt__ JSON may not be present in the initial HTML.
    The public JSON API at site.api.espn.com always returns the full card.

    Returns a list of fight dicts (with f1_stats / f2_stats populated), or [].
    """
    url = (
        f"https://site.api.espn.com/apis/site/v2/sports/mma/ufc/summary"
        f"?event={event_id}"
    )
    log.info(f"  Trying ESPN summary API: {url}")
    response_meta = {'status': None, 'final_url': url, 'content_type': '', 'byte_length': 0}
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
        response_meta.update({
            'status': getattr(r, 'status_code', None),
            'final_url': getattr(r, 'url', url),
            'content_type': getattr(r, 'headers', {}).get('content-type', ''),
            'byte_length': len(getattr(r, 'content', b'') or b''),
        })
        if r.status_code == 404:
            log.warning(
                "  ESPN summary API returned 404 for event %s; continuing with fallback sources",
                event_id,
            )
            _log_espn_parser('summary_api', True, response_meta, None, [])
            return []
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning(f"  ESPN summary API failed: {e}")
        _log_espn_parser('summary_api', True, response_meta, None, [])
        return []

    # Try multiple JSON paths that ESPN uses for the gamepackage
    gp = (
        data.get('gamepackage')
        or data.get('page', {}).get('content', {}).get('gamepackage', {})
        or {}
    )

    if gp.get('cardSegs'):
        fights = _parse_espn_card_segs(gp)
        for f in fights:
            f['card_source'] = 'espn_summary_json'
            f['verified'] = True
        if fights:
            log.info(f"  ESPN summary API (cardSegs): {len(fights)} fights")
            _log_espn_parser('summary_api', True, response_meta, None, fights)
            return _enrich_fights_with_profiles(fights)

    # Alternative structure: competitions list under 'card'
    competitions = (
        data.get('card', {}).get('competitions', [])
        or data.get('competitions', [])
        or []
    )
    if competitions:
        fights = _parse_espn_competitions(competitions)
        for f in fights:
            f['card_source'] = 'espn_summary_json'
            f['verified'] = True
        if fights:
            log.info(f"  ESPN summary API (competitions): {len(fights)} fights")
            _log_espn_parser('summary_api', True, response_meta, None, fights)
            return _enrich_fights_with_profiles(fights)

    log.info("  ESPN summary API returned no fights")
    _log_espn_parser('summary_api', True, response_meta, None, [])
    return []


def _parse_espn_competitions(competitions):
    """
    Parse fight data from ESPN competitions-style JSON (alternative to cardSegs).
    Each competition represents one fight with two competitors.
    """
    fights = []
    for bout_order, comp in enumerate(competitions):
        competitors = comp.get('competitors', [])
        if len(competitors) < 2:
            continue
        # Normalise to home/away; when homeAway is absent fall back to list
        # order (ESPN consistently places the "home" fighter first).
        home = next((c for c in competitors if c.get('homeAway') == 'home'), competitors[0])
        away = next((c for c in competitors if c.get('homeAway') == 'away'), competitors[1])

        away_athlete = away.get('athlete', {})
        home_athlete = home.get('athlete', {})
        n1 = away_athlete.get('displayName', '') or away.get('displayName', '')
        n2 = home_athlete.get('displayName', '') or home.get('displayName', '')
        if not n1 or not n2 or (is_placeholder_fighter_name(n1) and is_placeholder_fighter_name(n2)):
            continue

        links1 = away_athlete.get('links', []) or away.get('links', [])
        links2 = home_athlete.get('links', []) or home.get('links', [])
        u1 = links1[0].get('href', '') if links1 else ''
        u2 = links2[0].get('href', '') if links2 else ''

        type_info = comp.get('type', {})
        type_text = (type_info.get('text') or type_info.get('description') or '').lower()
        is_main = 'main' in type_text
        card_section = _card_section_from_text(type_text, is_main)

        notes = comp.get('notes', []) or []
        is_title = any('title' in (n.get('headline', '') + n.get('text', '')).lower()
                       for n in notes)

        weight_class = (
            comp.get('weightClass', {}).get('displayName', '')
            or type_info.get('text', '')
        )

        result_data = None
        status = comp.get('status', {})
        if status.get('type', {}).get('state') == 'post':
            winner = None
            for c in competitors:
                if c.get('winner'):
                    winner = (c.get('athlete', {}).get('displayName', '')
                              or c.get('displayName', ''))
                    break
            result_data = {
                'winner': winner,
                'method': comp.get('method', {}).get('shortDisplayName', ''),
                'time': status.get('displayClock', ''),
                'round': comp.get('round', {}).get('number'),
            }

        fights.append({
            'fighter_1': n1,
            'fighter_2': n2,
            'fighter_1_url': u1,
            'fighter_2_url': u2,
            'fighter_1_espn_id': away_athlete.get('id') or away.get('id'),
            'fighter_2_espn_id': home_athlete.get('id') or home.get('id'),
            'bout_uid': comp.get('id') or comp.get('uid') or comp.get('guid'),
            'status': (status.get('type', {}).get('name') or status.get('type', {}).get('state') or ''),
            'is_main_card': is_main,
            'card_section': card_section,
            'bout_order': bout_order,
            'is_title_fight': is_title,
            'result': result_data,
            'weight_class': weight_class,
            '_source_complete': True,
        })
    return fights



def _next_nonempty_text_after(node, limit=6):
    """Return nearby text after a BeautifulSoup node, skipping blank strings."""
    checked = 0
    for sib in node.next_siblings:
        if checked >= limit:
            break
        checked += 1
        txt = sib.get_text(" ", strip=True) if hasattr(sib, 'get_text') else str(sib).strip()
        if txt:
            return txt
    return ''


def _parse_espn_fightcenter_heading_dom(soup):
    """Parse ESPN's current Fightcenter server-rendered heading layout.

    In July 2026 ESPN no longer wraps bouts in the old AccordionPanel /
    MMACompetitor structure.  The initial HTML is still server-rendered, but the
    fight card is exposed as a stream of headings: an h3 card segment, an h2
    weight class, then two h2 fighter names, each immediately followed by an
    MMA record.  This parser follows that semantic heading structure instead of
    brittle CSS class names.
    """
    record_re = re.compile(r'^(?:\d+|--)-(?:\d+|--)(?:-(?:\d+|--))?(?:\s*\(NC\))?$')
    fights = []
    current_segment = ''
    current_weight = ''
    pending = None

    for heading in soup.find_all(['h2', 'h3']):
        text = heading.get_text(' ', strip=True)
        if not text:
            continue

        if heading.name == 'h3':
            if 'main card' in text.lower() or 'prelim' in text.lower():
                current_segment = text
                pending = None
            continue

        following_text = _next_nonempty_text_after(heading)
        is_fighter = bool(record_re.match(following_text))
        if not is_fighter:
            # ESPN emits the bout division / descriptor as an h2 before the two
            # fighter h2 headings (for example "Welterweight - Main Event").
            if not any(skip in text.lower() for skip in ('latest videos', 'mma news')):
                current_weight = text
            pending = None
            continue

        link = heading.find('a', href=True)
        url = link['href'] if link else ''
        if url and url.startswith('/'):
            url = 'https://www.espn.com' + url
        fighter = {'name': text, 'url': url}

        if pending is None:
            pending = fighter
            continue

        is_main = 'prelim' not in current_segment.lower()
        fights.append({
            'fighter_1': pending['name'],
            'fighter_2': fighter['name'],
            'fighter_1_url': pending['url'],
            'fighter_2_url': fighter['url'],
            'f1_stats': {},
            'f2_stats': {},
            'is_main_card': is_main,
            'is_title_fight': 'title' in current_weight.lower(),
            'result': None,
            'weight_class': current_weight,
            '_source_complete': True,
        })
        pending = None

    return fights

def scrape_event_details(event_url, event_id):
    """Scrape fight card from ESPN event page. Returns list of fight dicts."""
    log.info(f"  Scraping event details: {event_url}")

    # Strategy 0: ESPN scoreboard JSON API is the canonical source when it exposes competitions.
    fights = _fetch_espn_scoreboard_card(event_id)
    if fights:
        return fights

    # Strategy 1: ESPN public summary API (works when fightcenter HTML uses
    # client-side rendering and no longer embeds __espnfitt__ JSON)
    fights = _fetch_espn_event_api(event_id)
    if fights:
        return fights

    soup, html, response_meta = _fetch_espn_event_html(event_url)
    if not soup:
        if is_espn_waf_challenge(html, response_meta):
            log.warning("  ESPN WAF challenge detected for event %s; aborting all Fightcenter DOM parsers and preserving existing card", event_id)
            return []
        _log_espn_parser('embedded_json', False, response_meta, soup, [])
        _log_espn_parser('legacy_dom', False, response_meta, soup, [])
        _log_espn_parser('heading_dom', False, response_meta, soup, [])
        return []
    _save_espn_debug_html(event_id, html)

    fights = []

    # Strategy 1: embedded __espnfitt__ JSON (most reliable for completed events)
    # ESPN uses both window['__espnfitt__']= and window.__espnfitt__ = variants
    _ESPNFITT_PATTERNS = [
        "window['__espnfitt__']=",
        'window["__espnfitt__"]=',
        "window.__espnfitt__ =",
        "window.__espnfitt__=",
    ]
    log.info("ESPN_PARSER_START parser=embedded_json invoked=true")
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
                    raw = _parse_espn_card_segs(gp)
                    for f in raw:
                        f['card_source'] = 'espn_embedded_json'
                        f['verified'] = True
                    if raw:
                        _log_espn_parser('embedded_json', True, response_meta, soup, raw)
                        _log_espn_parser('legacy_dom', False, response_meta, soup, [])
                        _log_espn_parser('heading_dom', False, response_meta, soup, [])
                        return _enrich_fights_with_profiles(raw)
            except Exception as e:
                log.warning(f"  JSON parse error: {e}")
    _log_espn_parser('embedded_json', True, response_meta, soup, [])

    # Strategy 2: DOM fallback
    log.info("ESPN_PARSER_START parser=legacy_dom invoked=true")
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
    _log_espn_parser('legacy_dom', True, response_meta, soup, fights)

    if not fights:
        log.info("ESPN_PARSER_START parser=heading_dom invoked=true")
        fights = _parse_espn_fightcenter_heading_dom(soup)
        heading_metrics = _espn_page_metrics(soup)
        log.info(
            "ESPN_HEADING_PARSER invoked=true h2=%s h3=%s record_candidates=%s bouts=%s",
            heading_metrics['h2'], heading_metrics['h3'],
            heading_metrics['record_candidates'], len(fights)
        )
        _log_espn_parser('heading_dom', True, response_meta, soup, fights)
        if not fights:
            _log_failed_heading_diagnostics(html, soup)
    else:
        _log_espn_parser('heading_dom', False, response_meta, soup, [])

    if not fights:
        log.warning(
            "  ESPN fightcenter DOM parser returned 0 fights for event %s; preserving existing verified card and odds-only enrichment",
            event_id,
        )

    return fights


# ── Database helpers ──────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL)


def load_fight_history(conn):
    """Load all historical fights from mma_fights + mma_events for stat calculation."""
    sql = """
        SELECT
            f.id AS fight_id,
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
          AND e.date <= CURRENT_DATE
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
               glicko_rating, glicko_rd, glicko_vol,
               ema_slpm, ema_sapm, ema_td_acc, ema_td_avg, ema_td_def,
               ema_kd_rate, ema_sub_rate, ema_ctrl_pct, ema_sig_str_acc,
               streak, win_rate, total_fights, recent_form
        FROM mma_fighters
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    return {r[0]: {
                'height': r[2], 'reach': r[3], 'stance': r[4],
                'glicko': r[5], 'glicko_rd': r[6], 'glicko_vol': r[7], 'name': r[1],
                'slpm': r[8], 'sapm': r[9], 'td_acc': r[10], 'td_avg': r[11],
                'td_def': r[12], 'kd_rate': r[13], 'sub_rate': r[14],
                'ctrl_rate': r[15], 'sig_str_acc': r[16], 'streak': r[17],
                'win_rate': r[18], 'total_fights': r[19], 'recent_form': r[20],
            } for r in rows}


def rebuild_stats_from_db(conn):
    """
    Rebuild EMA stats tracker from fight history in Postgres.
    Returns dict: fighter_id -> FighterStats
    Also builds name -> fighter_id map.
    """
    log.info("Rebuilding fighter stats from DB fight history...")
    stats_tracker = {}
    name_to_id = {}
    espn_to_id = {}

    # Build identity maps. ESPN profile IDs are canonical when present;
    # names are only a fallback, and duplicate names are resolved after history
    # counts are known so newly-created ESPN rows do not disconnect old fights.
    sql = "SELECT id, full_name, espn_url FROM mma_fighters"
    with conn.cursor() as cur:
        cur.execute(sql)
        for fid, name, espn_url in cur.fetchall():
            norm = normalize_name(name)
            if norm and norm not in name_to_id:
                name_to_id[norm] = fid
            espn_id = extract_espn_id(espn_url)
            if espn_id and espn_id not in espn_to_id:
                espn_to_id[espn_id] = fid

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
        fight_row_id, fid1, fid2, n1, n2, winner, method, rnd, t_str, fight_date = row[:10]
        if not fight_date:
            continue
        try:
            fight_date = pd.Timestamp(fight_date)
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

    _alias_historical_stats_to_canonical_fighters(conn, stats_tracker)
    # Re-point normalized names/ESPN IDs at rows that actually carry historical stats.
    name_to_id = build_name_to_fighter_id(conn, stats_tracker)
    espn_to_id = build_espn_to_fighter_id(conn, stats_tracker)
    _log_established_fighter_history_assertion(conn, stats_tracker)
    log.info("HISTORICAL_SOURCE source=completed_fight_database completed_fights=%s fighters=%s",
             len(rows), len(stats_tracker))
    log.info(f"  Stats built for {len(stats_tracker)} fighters")
    return stats_tracker, name_to_id, espn_to_id


def postgres_history_is_complete_enough(conn):
    """Return True only when Postgres has a full historical MMA seed."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*)
            FROM mma_fights f
            JOIN mma_events e ON f.event_id = e.id
            WHERE e.is_completed = TRUE
              AND e.date <= CURRENT_DATE
              AND f.winner_name IS NOT NULL
        """)
        fight_count = int(cur.fetchone()[0] or 0)
        cur.execute("SELECT COUNT(*) FROM mma_fighters")
        fighter_count = int(cur.fetchone()[0] or 0)
    complete = (
        fight_count >= MIN_COMPLETE_DB_FIGHTS_FOR_PRIMARY_HISTORY
        and fighter_count >= MIN_DB_FIGHTERS_FOR_PRIMARY_HISTORY
    )
    log.info(
        "POSTGRES_HISTORY_COMPLETENESS completed_fights=%s fighters=%s required_fights=%s required_fighters=%s complete=%s",
        fight_count, fighter_count, MIN_COMPLETE_DB_FIGHTS_FOR_PRIMARY_HISTORY,
        MIN_DB_FIGHTERS_FOR_PRIMARY_HISTORY, str(complete).lower(),
    )
    return complete


# ── Prediction model ──────────────────────────────────────────────────────────

MODEL_FEATURE_NAMES = [
    'glicko_diff', 'glicko_rd_diff', 'age_diff', 'height_diff', 'reach_diff',
    'slpm_diff', 'sapm_diff', 'td_avg_diff', 'td_acc_diff', 'td_def_diff',
    'kd_diff', 'sub_diff', 'ctrl_diff', 'sig_acc_diff', 'head_pct_diff',
    'body_pct_diff', 'leg_pct_diff', 'dist_pct_diff', 'clinch_pct_diff',
    'ground_pct_diff', 'exp_diff', 'streak_diff', 'win_rate_diff',
    'rust_diff', 'activity_diff', 'is_apex', 'is_altitude', 'stance_1',
    'stance_2', 'weight_class',
]

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
    g_diff = float(np.clip(g1.get('rating', 1500) - g2.get('rating', 1500),
                            -GLICKO_DIFF_CLIP, GLICKO_DIFF_CLIP))

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


MODEL_VERSION_CATBOOST = 'catboost_v1'
# Written when the real model is unavailable/errors and predict_fight() has to
# return a bare coin-flip. Kept distinct from MODEL_VERSION_CATBOOST so a
# broken model can't silently masquerade as a real prediction downstream (the
# UI and the edge-finder both need to be able to tell the two apart).
MODEL_VERSION_FALLBACK = 'fallback_5050'


def predict_fight(model, st1, st2, b1, b2, g1, g2, is_apex=0, is_altitude=0,
                  weight_class=''):
    """Returns (probability that fighter 1 wins, used_fallback)."""
    if model is None:
        return 0.5, True
    try:
        features = build_feature_row(st1, st2, b1, b2, g1, g2, is_apex, is_altitude,
                                     weight_class=weight_class)
        df = pd.DataFrame([features])
        prob = model.predict_proba(df)[0][1]
        return float(prob), False
    except Exception as e:
        log.warning(f"Prediction error: {e}")
        return 0.5, True


def is_altitude(location):
    if not location:
        return 0
    loc = str(location).lower()
    return 1 if any(c in loc for c in HIGH_ALTITUDE_CITIES) else 0


def is_apex_event(name, location):
    n = str(name or '').lower()
    l = str(location or '').lower()
    return 1 if ('fight night' in n and 'las vegas' in l) or 'apex' in l else 0


# ── ESPN headshot helpers ─────────────────────────────────────────────────────

def fetch_scoreboard_fighters():
    """Extract fighter ESPN profile URLs from the scoreboard API competitions.

    The scoreboard API is still live and includes competitor/athlete data in
    each event's ``competitions`` array.  This lets us populate headshot URLs
    even when the per-event summary API returns 404.

    Returns a dict mapping normalised fighter name → (display_name, espn_url).
    """
    today = date.today()
    result = {}

    for year in [today.year, today.year + 1]:
        start = f"{year}0101"
        end = f"{year}1231"
        url = (
            f"https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
            f"?limit=100&dates={start}-{end}"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            log.warning(f"  Scoreboard fighters fetch failed for {year}: {e}")
            continue

        for ev in data.get('events', []):
            for comp in ev.get('competitions', []):
                for cmp in comp.get('competitors', []):
                    athlete = cmp.get('athlete', {})
                    name = (athlete.get('displayName', '')
                            or cmp.get('displayName', ''))
                    if not name:
                        continue
                    links = athlete.get('links', []) or cmp.get('links', [])
                    espn_url = links[0].get('href', '') if links else ''
                    if not espn_url:
                        # Build URL from athlete ID when links are absent
                        athlete_id = athlete.get('id', '') or cmp.get('id', '')
                        if athlete_id:
                            espn_url = (
                                f'https://www.espn.com/mma/fighter/_/id/{athlete_id}'
                            )
                    if espn_url and not espn_url.startswith('http'):
                        espn_url = 'https://www.espn.com' + espn_url
                    if name and espn_url:
                        result[normalize_name(name)] = (name, espn_url)

    log.info(f"  Scoreboard: extracted ESPN data for {len(result)} fighters")
    return result


def espn_headshot_url(espn_url):
    """Derive ESPN CDN headshot URL from an ESPN fighter profile URL.

    Matches the approach used in sbalagan22/Octagon-AI (HomeClient.tsx /
    FightCard.tsx): extract the numeric fighter ID from the URL path and
    build the standard ESPN combiner endpoint URL.

    Example:
        https://www.espn.com/mma/fighter/_/id/3090197/gilbert-burns
        -> https://a.espncdn.com/combiner/i?img=/i/headshots/mma/players/full/3090197.png&w=500&h=360
    """
    if not espn_url:
        return None
    m = re.search(r'/id/(\d+)(?:[/?]|$)', espn_url)
    if not m:
        return None
    fighter_id = m.group(1)
    return (
        f'https://a.espncdn.com/combiner/i?img=/i/headshots/mma/players/full/'
        f'{fighter_id}.png&w=500&h=360'
    )


def upsert_fighter_espn_info(conn, name, espn_url, fighter_id=None, commit=True):
    """Persist ESPN URL/headshot on the canonical fighter row.

    Prefer the resolved mma_fighters.id. Normalized-name matching is only a
    fallback for external ESPN-only records that have not resolved to a CSV ID.
    """
    if not espn_url:
        return
    headshot = espn_headshot_url(espn_url)
    if not headshot:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH matched AS (
                    SELECT id
                    FROM mma_fighters
                    WHERE (%s IS NOT NULL AND id = %s)
                       OR (%s IS NULL AND (
                            LOWER(full_name) = LOWER(%s)
                         OR LOWER(full_name) LIKE '%%' || LOWER(%s) || '%%'
                         OR LOWER(%s) LIKE '%%' || LOWER(full_name) || '%%'
                       ))
                    ORDER BY CASE WHEN %s IS NOT NULL AND id = %s THEN 0
                                  WHEN LOWER(full_name) = LOWER(%s) THEN 1
                                  ELSE 2 END
                    LIMIT 1
                )
                UPDATE mma_fighters mf
                SET espn_url = %s, headshot_url = %s
                FROM matched
                WHERE mf.id = matched.id
                  AND (mf.espn_url IS DISTINCT FROM %s OR mf.headshot_url IS DISTINCT FROM %s)
                """,
                (fighter_id, fighter_id, fighter_id, name, name, name,
                 fighter_id, fighter_id, name, espn_url, headshot, espn_url, headshot),
            )
        if commit:
            conn.commit()
    except Exception as e:
        log.warning(f"Could not update ESPN info for {name}: {e}")
        conn.rollback()


def link_fight_fighters(conn, fight_id, fid1, fid2, commit=True):
    """Back-fill fighter_1_id / fighter_2_id on mma_fights when known.

    Overwrites links because official bout upserts may replace one side of a matchup.
    """
    if not fid1 and not fid2:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE mma_fights
                SET fighter_1_id = %s,
                    fighter_2_id = %s
                WHERE id = %s
                """,
                (fid1, fid2, fight_id),
            )
        if commit:
            conn.commit()
    except Exception as e:
        log.warning(f"Could not link fighter IDs for fight {fight_id}: {e}")
        conn.rollback()




def ensure_mma_integrity_schema(conn):
    """Add bout identity/status columns and active-bout constraints."""
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE mma_fights ADD COLUMN IF NOT EXISTS bout_uid VARCHAR(200)")
        cur.execute("ALTER TABLE mma_fights ADD COLUMN IF NOT EXISTS status VARCHAR(30) DEFAULT 'confirmed'")
        cur.execute("ALTER TABLE mma_fights ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE")
        cur.execute("ALTER TABLE mma_fights ADD COLUMN IF NOT EXISTS card_source VARCHAR(50) DEFAULT 'legacy'")
        cur.execute("ALTER TABLE mma_fights ADD COLUMN IF NOT EXISTS verified BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE mma_fights ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()")
        cur.execute("ALTER TABLE mma_fights ADD COLUMN IF NOT EXISTS bout_order INTEGER")
        cur.execute("ALTER TABLE mma_fights ADD COLUMN IF NOT EXISTS card_section VARCHAR(30)")
        cur.execute("UPDATE mma_fights SET card_source = 'legacy' WHERE card_source IS NULL")
        cur.execute("UPDATE mma_fights SET verified = TRUE WHERE verified IS NULL AND card_source <> 'odds_api'")
        cur.execute("""
            UPDATE mma_fights
            SET bout_uid = 'legacy:' || event_id || ':' || id::text
            WHERE bout_uid IS NULL OR TRIM(bout_uid) = ''
        """)
        cur.execute("""
            ALTER TABLE mma_fights ALTER COLUMN bout_uid SET NOT NULL
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_mma_fights_event_bout_uid
            ON mma_fights (event_id, bout_uid)
        """)
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_mma_fights_one_active_bout
            ON mma_fights (event_id, bout_uid)
            WHERE is_active = TRUE
        """)
    conn.commit()


def upsert_event(conn, event, commit=True):
    sql = """
        INSERT INTO mma_events (id, name, date, location, is_completed, espn_url, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            date = EXCLUDED.date,
            location = EXCLUDED.location,
            is_completed = EXCLUDED.is_completed,
            espn_url = EXCLUDED.espn_url,
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
    if commit:
        conn.commit()
  
def parse_round(val):
    """Convert 'R3' or 3 or '3' to int, or None."""
    if val is None:
        return None
    try:
        return int(str(val).replace('R', '').replace('r', '').strip())
    except (ValueError, TypeError):
        return None

def upsert_fight(conn, event_id, fight, commit=True):
    """Insert/update a fight by canonical event-scoped bout identifier."""
    bout_uid = canonical_bout_uid(event_id, fight)
    status = fight_status(fight)
    is_active = status in {'confirmed', 'completed'}
    r = fight.get('result') or {}

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO mma_fights
                (event_id, bout_uid, status, is_active, card_source, verified,
                 fighter_1_name, fighter_2_name,
                 weight_class, is_main_card, card_section, bout_order, is_title_fight,
                 f1_height, f1_reach, f1_stance, f1_record,
                 f2_height, f2_reach, f2_stance, f2_record,
                 winner_name, method, round_ended, time_ended,
                 created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
            ON CONFLICT (event_id, bout_uid) DO UPDATE SET
                status = EXCLUDED.status,
                is_active = EXCLUDED.is_active,
                card_source = EXCLUDED.card_source,
                verified = EXCLUDED.verified,
                fighter_1_name = EXCLUDED.fighter_1_name,
                fighter_2_name = EXCLUDED.fighter_2_name,
                weight_class = EXCLUDED.weight_class,
                is_main_card = EXCLUDED.is_main_card,
                card_section = EXCLUDED.card_section,
                bout_order = EXCLUDED.bout_order,
                is_title_fight = EXCLUDED.is_title_fight,
                f1_height = EXCLUDED.f1_height,
                f1_reach = EXCLUDED.f1_reach,
                f1_stance = EXCLUDED.f1_stance,
                f1_record = EXCLUDED.f1_record,
                f2_height = EXCLUDED.f2_height,
                f2_reach = EXCLUDED.f2_reach,
                f2_stance = EXCLUDED.f2_stance,
                f2_record = EXCLUDED.f2_record,
                winner_name = EXCLUDED.winner_name,
                method = EXCLUDED.method,
                round_ended = EXCLUDED.round_ended,
                time_ended = EXCLUDED.time_ended,
                updated_at = NOW()
            RETURNING id
        """, (
            event_id, bout_uid, status, is_active,
            fight.get('card_source', 'espn_scoreboard_json'), bool(fight.get('verified', True)),
            fight['fighter_1'], fight['fighter_2'],
            fight.get('weight_class', ''),
            fight.get('is_main_card', False),
            fight.get('card_section') or ('main_card' if fight.get('is_main_card') else 'prelims'),
            fight.get('bout_order'),
            fight.get('is_title_fight', False),
            fight.get('f1_stats', {}).get('Height'),
            fight.get('f1_stats', {}).get('Reach'),
            fight.get('f1_stats', {}).get('Stance'),
            fight.get('f1_stats', {}).get('Record'),
            fight.get('f2_stats', {}).get('Height'),
            fight.get('f2_stats', {}).get('Reach'),
            fight.get('f2_stats', {}).get('Stance'),
            fight.get('f2_stats', {}).get('Record'),
            r.get('winner'), r.get('method'), parse_round(r.get('round')), r.get('time'),
        ))
        fight_id = cur.fetchone()[0]
    if commit:
        conn.commit()
    return fight_id




def active_fight_count(conn, event_id):
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM mma_fights
            WHERE event_id = %s
              AND COALESCE(is_active, TRUE) = TRUE
              AND COALESCE(status, 'confirmed') IN ('confirmed', 'completed')
            """,
            (event_id,),
        )
        row = cur.fetchone()
    return int(row[0] or 0) if row else 0


def event_card_fetch_is_complete(conn, event_id, fights):
    """Return True only when stale cleanup is safe for this event payload."""
    if not fights:
        return False
    if any(not fight.get('_source_complete', False) for fight in fights):
        return False
    confirmed = [f for f in fights if fight_status(f) in {'confirmed', 'completed'}]
    if len(confirmed) < MIN_COMPLETE_UFC_BOUTS:
        return False
    existing_active = active_fight_count(conn, event_id)
    if existing_active and len(confirmed) < math.ceil(existing_active * MIN_COMPLETE_CARD_RATIO):
        return False
    return True


def invalidate_fight_predictions(conn, fight_ids):
    if not fight_ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM mma_predictions WHERE fight_id = ANY(%s)", (list(fight_ids),))
        return cur.rowcount


def deactivate_stale_event_bouts(conn, event_id, seen_bout_uids, payload_complete, commit=True):
    """Deactivate active fights for this event absent from a complete payload."""
    if not payload_complete:
        log.info("  Skipping stale cleanup for event %s: incomplete card payload", event_id)
        return 0
    if not seen_bout_uids:
        log.info("  Skipping stale cleanup for event %s: no bout IDs seen", event_id)
        return 0

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE mma_fights
            SET is_active = FALSE, status = 'cancelled', updated_at = NOW()
            WHERE event_id = %s
              AND COALESCE(is_active, TRUE) = TRUE
              AND bout_uid <> ALL(%s)
            RETURNING id
            """,
            (event_id, list(seen_bout_uids)),
        )
        stale_ids = [row[0] for row in cur.fetchall()]
    invalidate_fight_predictions(conn, stale_ids)
    if commit:
        conn.commit()
    if stale_ids:
        log.info("  Deactivated %s stale/cancelled fight(s) for event %s", len(stale_ids), event_id)
    return len(stale_ids)


def deactivate_unverified_event_bouts(conn, event_id, commit=True):
    """Deactivate only active rows whose provenance proves they are unverified.

    This is intentionally provenance-based: no fighter-name matching and no
    external fetch. It is safe for one-off cleanup after Odds API-only rows were
    accidentally activated.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE mma_fights
            SET is_active = FALSE, status = 'cancelled', updated_at = NOW()
            WHERE event_id = %s
              AND COALESCE(is_active, TRUE) = TRUE
              AND (card_source = 'odds_api' OR COALESCE(verified, FALSE) = FALSE)
            RETURNING id
            """,
            (event_id,),
        )
        ids = [row[0] for row in cur.fetchall()]
    invalidate_fight_predictions(conn, ids)
    if commit:
        conn.commit()
    return len(ids)


def prune_event_fights(conn, event_id, keep_fight_ids):
    """Backward-compatible wrapper: empty keep list never deactivates."""
    if not keep_fight_ids:
        log.info("  No source fights to prune for event %s; preserving existing fights", event_id)
        return 0
    # Legacy callers cannot prove payload completeness or bout UID coverage.
    log.info("  Legacy prune call ignored for event %s; use deactivate_stale_event_bouts", event_id)
    return 0


def deactivate_duplicate_active_matchups(conn, event_id, current_fight_id, bout_uid, fid1, fid2, f1, f2):
    """Ensure a replacement leaves one active matchup for the same fighters/event."""
    params = [event_id, current_fight_id, bout_uid]
    if fid1 and fid2:
        ordered = sorted([str(fid1), str(fid2)])
        clause = "AND ARRAY[fighter_1_id, fighter_2_id]::text[] <@ ARRAY[%s, %s]::text[] AND ARRAY[%s, %s]::text[] <@ ARRAY[fighter_1_id, fighter_2_id]::text[]"
        params.extend([ordered[0], ordered[1], ordered[0], ordered[1]])
    else:
        n1, n2 = normalize_name(f1), normalize_name(f2)
        clause = "AND (LOWER(REGEXP_REPLACE(fighter_1_name, '[^a-zA-Z0-9 ]', '', 'g')) IN (%s, %s) AND LOWER(REGEXP_REPLACE(fighter_2_name, '[^a-zA-Z0-9 ]', '', 'g')) IN (%s, %s))"
        params.extend([n1, n2, n1, n2])
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE mma_fights
            SET is_active = FALSE, status = 'cancelled', updated_at = NOW()
            WHERE event_id = %s
              AND id <> %s
              AND bout_uid <> %s
              AND COALESCE(is_active, TRUE) = TRUE
              {clause}
            RETURNING id
            """,
            tuple(params),
        )
        duplicate_ids = [row[0] for row in cur.fetchall()]
    invalidate_fight_predictions(conn, duplicate_ids)
    return len(duplicate_ids)


def upsert_prediction(conn, fight_id, pred, commit=True):
    sql = """
        INSERT INTO mma_predictions
            (fight_id, predicted_winner, f1_win_probability, f2_win_probability,
             confidence, factors_json, model_version, generated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (fight_id) DO UPDATE SET
            predicted_winner   = EXCLUDED.predicted_winner,
            f1_win_probability = EXCLUDED.f1_win_probability,
            f2_win_probability = EXCLUDED.f2_win_probability,
            confidence         = EXCLUDED.confidence,
            factors_json       = EXCLUDED.factors_json,
            model_version      = EXCLUDED.model_version,
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
            pred.get('model_version', MODEL_VERSION_CATBOOST),
        ))
    if commit:
        conn.commit()


# ── Main sync flow ────────────────────────────────────────────────────────────

def main():
    log.info("=== MMA Sync Starting ===")
    log.info("MMA_SYNC_COMMIT=%s", _current_commit_sha())

    if not DATABASE_URL:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    conn = get_conn()
    ensure_mma_integrity_schema(conn)

    # Load model
    model = load_model()

    # Build stats from Postgres fight history (mma_fights / mma_fighters), which
    # holds the original Octagon-AI seed plus every fight this cron has synced
    # since. If history is too thin (e.g. a fresh DB not yet seeded), keep the
    # cron non-fatal and use the engineered fighter features already persisted
    # on mma_fighters for prediction.
    postgres_history_is_complete_enough(conn)
    try:
        stats_tracker, name_to_id, espn_to_id = rebuild_stats_from_db(conn)
        fighter_bio = load_fighters_bio(conn)
    except Exception as e:
        log.warning(
            "Postgres fight history is unavailable (%s); continuing with persisted mma_fighters features.",
            e,
        )
        stats_tracker = {}
        name_to_id = build_name_to_fighter_id(conn, stats_tracker)
        espn_to_id = build_espn_to_fighter_id(conn, stats_tracker)
        fighter_bio = load_fighters_bio(conn)

    # ── Pre-fetch Odds API events for diagnostics / odds-only enrichment ─────
    # Do not use these rows as canonical card bouts. ESPN is the card source of
    # truth; when ESPN returns zero canonical bouts, the existing verified card
    # must remain unchanged.
    odds_api_key = os.environ.get('ODDS_API_KEY', '')
    odds_fights_by_date = {}  # date -> list[{fighter_1, fighter_2}]
    if odds_api_key:
        try:
            from mma_data import fetch_mma_events as _fetch_odds_events
            _odds_events = _fetch_odds_events(api_key=odds_api_key)
            for _ev in _odds_events:
                _ct = _ev.get('commence_time', '')
                _f1 = _ev.get('home_team', '')
                _f2 = _ev.get('away_team', '')
                if not _f1 or not _f2:
                    continue
                try:
                    _d = pd.to_datetime(_ct).date()
                    odds_fights_by_date.setdefault(_d, []).append(
                        {'fighter_1': _f1, 'fighter_2': _f2}
                    )
                except Exception:
                    pass
            log.info(
                f"  Odds API pre-fetch: {len(_odds_events)} fights across "
                f"{len(odds_fights_by_date)} dates"
            )
        except Exception as _e:
            log.warning(f"  Odds API pre-fetch failed: {_e}")

    # Scrape upcoming events
    log.info("Scraping ESPN schedule...")
    events = scrape_upcoming_events()
    log.info(f"Found {len(events)} events to process")

    # Pre-fetch fighter ESPN data from the scoreboard API.  The per-event
    # summary API (/ufc/summary?event=ID) has started returning 404 for many
    # events, so we can no longer rely on it for fighter profile URLs.  The
    # scoreboard API is still live and embeds competitor athlete data (ESPN IDs
    # + profile links) inside each event's competitions array.
    log.info("Pre-fetching fighter ESPN data from scoreboard...")
    scoreboard_fighters = fetch_scoreboard_fighters()

    today = pd.Timestamp.now()
    default_stats = FighterStats()
    default_sv = default_stats.get_stat_vector(today)

    for event in events:
        log.info(f"Processing: {event['event_name']} ({event['date']})")

        # Scrape fight card
        fights = scrape_event_details(event['url'], event['event_id'])
        log.info(f"  {len(fights)} fights on card")

        has_placeholder_fights = any(fight_has_placeholder(_fight) for _fight in fights)
        if not fights and not event['is_completed']:
            log.warning(
                "  ESPN returned 0 canonical fights; NOT seeding Odds API-only fights. "
                "Keeping existing verified card unchanged; Odds API will only enrich already verified matchups."
            )
        elif has_placeholder_fights and not event['is_completed']:
            log.warning(
                "  ESPN returned placeholder/TBA fights; NOT replacing card with Odds API-only fights."
            )

        if not event['is_completed']:
            before_count = len(fights)
            fights = [_fight for _fight in fights if not fight_has_placeholder(_fight)]
            if len(fights) != before_count:
                log.info(
                    f"  Dropped {before_count - len(fights)} placeholder/TBA fight(s) "
                    "from upcoming card"
                )

        payload_complete = event_card_fetch_is_complete(conn, event['event_id'], fights)
        seen_bout_uids = set()

        try:
            upsert_event(conn, event, commit=False)

            for fight in fights:
                # If ESPN URLs are still missing, try scoreboard lookup by name.
                for side in ('fighter_1', 'fighter_2'):
                    url_key = f'{side}_url'
                    if not fight.get(url_key):
                        sb_key = best_name_match_key(fight[side], scoreboard_fighters)
                        sb_entry = scoreboard_fighters.get(sb_key) if sb_key else None
                        if sb_entry:
                            fight[url_key] = sb_entry[1]

                bout_uid = canonical_bout_uid(event['event_id'], fight)
                fight_id = upsert_fight(conn, event['event_id'], fight, commit=False)
                seen_bout_uids.add(bout_uid)

                # Resolve fighter IDs (needed for headshot linking + predictions)
                fid1 = resolve_fighter_id(fight['fighter_1'], name_to_id, fight.get('fighter_1_espn_id'), espn_to_id)
                fid2 = resolve_fighter_id(fight['fighter_2'], name_to_id, fight.get('fighter_2_espn_id'), espn_to_id)
                log_history_lookup(conn, fight['fighter_1'], fight.get('fighter_1_espn_id'), fid1, stats_tracker, name_to_id, espn_to_id)
                log_history_lookup(conn, fight['fighter_2'], fight.get('fighter_2_espn_id'), fid2, stats_tracker, name_to_id, espn_to_id)
                odds_match = diagnose_odds_match(fight, event, odds_fights_by_date)

                # Back-fill fighter_1_id / fighter_2_id on the fight row
                link_fight_fighters(conn, fight_id, fid1, fid2, commit=False)
                deactivate_duplicate_active_matchups(
                    conn, event['event_id'], fight_id, bout_uid,
                    fid1, fid2, fight['fighter_1'], fight['fighter_2'],
                )

                # Persist ESPN profile URLs + derived headshot URLs on fighter rows
                if fight.get('fighter_1_url'):
                    upsert_fighter_espn_info(conn, fight['fighter_1'], fight['fighter_1_url'], fid1, commit=False)
                if fight.get('fighter_2_url'):
                    upsert_fighter_espn_info(conn, fight['fighter_2'], fight['fighter_2_url'], fid2, commit=False)

                # Skip prediction for completed fights that already have one
                if event['is_completed']:
                    log.info(f"  Skipping prediction for completed fight: "
                             f"{fight['fighter_1']} vs {fight['fighter_2']}")
                    continue

                feature_count = len(build_feature_row(
                    stat_vector_for_fighter(fid1, stats_tracker, fighter_bio, today, default_sv),
                    stat_vector_for_fighter(fid2, stats_tracker, fighter_bio, today, default_sv),
                    fighter_bio.get(fid1, {}), fighter_bio.get(fid2, {}),
                    {'rating': fighter_bio.get(fid1, {}).get('glicko', 1500), 'rd': fighter_bio.get(fid1, {}).get('glicko_rd', 350)},
                    {'rating': fighter_bio.get(fid2, {}).get('glicko', 1500), 'rd': fighter_bio.get(fid2, {}).get('glicko_rd', 350)},
                    is_apex_event(event['event_name'], event['location']), is_altitude(event['location']),
                    fight.get('weight_class', ''),
                ))
                for _fid, _name in ((fid1, fight['fighter_1']), (fid2, fight['fighter_2'])):
                    _src = fighter_feature_source(fighter_bio.get(_fid, {}))
                    if _fid in stats_tracker and getattr(stats_tracker[_fid], 'total_fights', 0) > 0:
                        _src = {'source': 'history_rebuild', 'total_fights': getattr(stats_tracker[_fid], 'total_fights', 0), 'usable': True}
                    log.info(
                        "FEATURE_SOURCE fighter=%s source=%s total_fights=%s usable=%s",
                        _name, _src['source'], _src['total_fights'], str(_src['usable']).lower(),
                    )
                gate_reasons = prediction_gate_reasons(fight, fid1, fid2, stats_tracker, feature_count=feature_count, fighter_bio=fighter_bio)
                if gate_reasons:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM mma_predictions WHERE fight_id = %s", (fight_id,))
                    log.info("PREDICTION_GATE fight=%s vs %s eligible=false reasons=%s",
                             fight['fighter_1'], fight['fighter_2'], gate_reasons)
                    log.info("UFC329_DIAG fight=%s vs %s canonical_espn_ids=%s|%s db_fighter_ids=%s|%s history_counts=%s|%s feature_count=%s odds_api_match=%s prediction_eligible=false",
                             fight['fighter_1'], fight['fighter_2'],
                             fight.get('fighter_1_espn_id') or fight.get('fighter_1_id'),
                             fight.get('fighter_2_espn_id') or fight.get('fighter_2_id'),
                             fid1, fid2,
                             getattr(stats_tracker.get(fid1), 'total_fights', 0) if fid1 else 0,
                             getattr(stats_tracker.get(fid2), 'total_fights', 0) if fid2 else 0,
                             feature_count, odds_match['matched'])
                    continue
                log.info("PREDICTION_GATE fight=%s vs %s eligible=true reasons=[]",
                         fight['fighter_1'], fight['fighter_2'])
                log.info("UFC329_DIAG fight=%s vs %s canonical_espn_ids=%s|%s db_fighter_ids=%s|%s history_counts=%s|%s feature_count=%s odds_api_match=%s prediction_eligible=true",
                         fight['fighter_1'], fight['fighter_2'],
                         fight.get('fighter_1_espn_id') or fight.get('fighter_1_id'),
                         fight.get('fighter_2_espn_id') or fight.get('fighter_2_id'),
                         fid1, fid2,
                         getattr(stats_tracker.get(fid1), 'total_fights', 0) if fid1 else 0,
                         getattr(stats_tracker.get(fid2), 'total_fights', 0) if fid2 else 0,
                         feature_count, odds_match['matched'])

                st1 = stat_vector_for_fighter(fid1, stats_tracker, fighter_bio, today, default_sv)
                st2 = stat_vector_for_fighter(fid2, stats_tracker, fighter_bio, today, default_sv)

                b1 = fighter_bio.get(fid1, {})
                b2 = fighter_bio.get(fid2, {})

                g1 = {'rating': b1.get('glicko', 1500), 'rd': b1.get('glicko_rd', 350)}
                g2 = {'rating': b2.get('glicko', 1500), 'rd': b2.get('glicko_rd', 350)}

                apex = is_apex_event(event['event_name'], event['location'])
                alt = is_altitude(event['location'])

                prob, used_fallback = predict_fight(model, st1, st2, b1, b2, g1, g2, apex, alt,
                                    weight_class=fight.get('weight_class', ''))
                if used_fallback:
                    log.warning("PREDICTION_FALLBACK fight=%s vs %s — model unavailable/errored, writing 50/50",
                                fight['fighter_1'], fight['fighter_2'])

                winner = fight['fighter_1'] if prob > 0.5 else fight['fighter_2']
                confidence = f"{max(prob, 1 - prob) * 100:.1f}%"

                pred = {
                    'winner': winner,
                    'f1_prob': prob,
                    'f2_prob': 1.0 - prob,
                    'confidence': confidence,
                    'model_version': MODEL_VERSION_FALLBACK if used_fallback else MODEL_VERSION_CATBOOST,
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
                        '_diagnostics': {
                            'odds_api_match': odds_match['matched'],
                            'odds_api_reason': odds_match['reason'],
                        },
                    }
                }

                upsert_prediction(conn, fight_id, pred, commit=False)
                log.info(f"  Predicted: {winner} ({confidence}) — "
                         f"{fight['fighter_1']} vs {fight['fighter_2']}")

            deactivate_stale_event_bouts(conn, event['event_id'], seen_bout_uids, payload_complete, commit=False)
            conn.commit()
        except Exception:
            conn.rollback()
            log.exception("  Event sync failed; rolled back partial card update for %s", event['event_id'])
            continue
        time.sleep(1)

    # Bulk-update headshot URLs for all fighters found in the scoreboard.
    # This catches any fighter whose headshot_url is still NULL in the DB but
    # whose ESPN ID was returned by the scoreboard (e.g. fighters on cards that
    # the per-event summary API returned 404 for).
    if scoreboard_fighters:
        log.info(f"Bulk-updating headshots for {len(scoreboard_fighters)} scoreboard fighters...")
        conn2 = get_conn()
        try:
            for _display_name, _espn_url in scoreboard_fighters.values():
                upsert_fighter_espn_info(conn2, _display_name, _espn_url)
        finally:
            conn2.close()

    conn.close()

    # ── 6. Sync UFC fight odds from The Odds API ──────────────────────────────
    if odds_api_key:
        log.info("Fetching UFC fight odds from The Odds API …")
        try:
            from mma_data import fetch_mma_fight_odds
            from mma_models import upsert_mma_fight_odds
            from sqlalchemy import create_engine, text
            from types import SimpleNamespace

            odds_rows = fetch_mma_fight_odds(api_key=odds_api_key)
            if odds_rows:
                odds_engine = create_engine(DATABASE_URL)
                odds_db = SimpleNamespace(engine=odds_engine, text=text)
                count = upsert_mma_fight_odds(odds_db, odds_rows)
                log.info(f"  ✓ UFC odds synced: {count} rows")
            else:
                log.info("  - No UFC odds returned (no upcoming events?)")
        except Exception as exc:
            log.error(f"  ✗ UFC odds sync failed: {exc}")
    else:
        log.info("  - UFC odds: skipped (ODDS_API_KEY not configured)")

    log.info("=== MMA Sync Complete ===")


if __name__ == '__main__':
    main()
