"""Review angles: race-day angles worth watching. Not bets.

Nothing in horse racing has made money here over time. These angles came
closest. They were taken off the Best Bets page on 14 Nov 2026 and now only
show, with their running win and place record, on the Data and ML Data pages
(history(), /api/data/review-angles).

Where they came from:
- audit/tasks/closest.py, 11 Nov 2026: 72,884 runners (Feb-Oct 2026), about
  470 angles, each judged in Feb-Jun and again in Jul-Oct.
- A second read-only check on 14 Nov 2026 of about 60 angles and pairings,
  the same two halves plus November.

What both checks found:
- Backing every runner lost 7.7% to win at the Betfair price, but 26.9% at
  the starting price. The starting price is far worse on long shots.
- Angles at short prices sit near break even at Betfair prices and still
  lose at the starting price.
- Angles that showed a profit were all long shots (median $15-$26, 5-8%
  winners) and only at the Betfair price. A few very big winners carry them;
  expect long losing runs. Cut down to under $10 they all lose.
- Jockey, trainer and jockey + trainer angles did not hold up.

"All three agree" (analyzer pick, ML pick and favourite are one horse) was
looked at about 40 ways on 15 Nov 2026 (1,052 bets, May-Nov):
- It breaks even at the Betfair price and loses at the starting price. That
  is about 3 points better than any favourite, well inside luck.
- The ML model's agreement is what matters. A favourite the analyzer picks
  but the ML does not lost 19.7% (430 bets): the one clear result, as a
  horse to avoid.
- It did better when the analyzer's top two were close, in fields of 8+,
  and on Saturdays. Each of those is one of 40 cuts on a few hundred bets,
  so each could be luck. They are listed to be tested on new races.
- Before 16 Oct 2026 the ML scores were worked out after the race, so the
  earlier-check figures may flatter the ML. The page counts only races the
  ML scored before the jump.

The "audit" text on each angle is that earlier Betfair-price result
(Feb-Jun / Jul-Oct). It was chosen with hindsight, so the running record on
the page is the honest test.
"""

import logging
import time
from collections import namedtuple

logger = logging.getLogger(__name__)

COMMISSION = 0.08
LEAD_BIG = 1.15      # analyzer top pick scores at least 15% more than its second pick
CHUNK_RACES = 500

# Analyzer component names (notes_parsing.py) -> a lower-case SQL LIKE that
# every matching note must contain. It only narrows which notes are fetched;
# the notes parser's own pattern decides.
COMPONENT_LIKE = {
    "Ran places: 3rd 2nd": "%ran places:%3rd%2nd%",
    "Weight vs Field - Below (2-3kg)": "%10.0%weight%below race avg%",
    "Running Position - Backmarker Sprint": "%backmarker in sprint%",
    "Trainer - Cold (L100 <5% SR)": "%trainer cold%",
    "Track Win Rate - Low (1-15%)": "%low win rate%at this track%",
    "API Sectional - Last 200m Average": "%last 200m (rank%average%",
    "Last Start - Photo Win (<0.5L)": "%last start - photo win%",
    "Days Since Run - Quick Backup (≤7 days)": "%quick backup%",
}

WB = "Weight vs Field - Below (2-3kg)"
BM = "Running Position - Backmarker Sprint"
LONG_SHOT_FLAGS = (WB, BM, "Trainer - Cold (L100 <5% SR)", "Track Win Rate - Low (1-15%)",
                   "API Sectional - Last 200m Average", "Last Start - Photo Win (<0.5L)",
                   "Days Since Run - Quick Backup (≤7 days)")

# key      stored nowhere; identifies the angle on the page
# name     plain words; "{model}" becomes "Analyzer" or "ML" (the page's top pick)
# group    'steady' (short prices), 'agree' (the models and the market), 'long' (long shots)
#          or 'lay' (bet AGAINST the horse on Betfair: you win when it loses)
# comps    analyzer components that must ALL be in the notes
# flags    'top' = the page's top pick, 'a_top' / 'ml_top' = analyzer / ML top pick, 'fav' = favourite,
#          'a_other' / 'ml_other' = that model picked a different horse in this race,
#          'lead_small' / 'lead_big' = the analyzer's top pick scores under / at least 15% more than its 2nd,
#          'saturday' = the meeting is on a Saturday,
#          'ratings_3' = top pick of at least 3 of: analyzer, PFAI, time rating, weight-class rating
# at_least (n, names): at least n of these components
# price    (low, high): low <= price < high. Last Ladbrokes win price before the jump, else starting price
# field    (low, high) runners in the race, both included
Angle = namedtuple("Angle", "key name group comps flags at_least price field audit audit_ml",
                   defaults=((), (), None, None, None, "", None))

# "audit" is what the earlier checks found, at Betfair prices, in plain words.
# "audit_ml" replaces it on the ML Data page where the angle means something
# different there (the top pick is the ML model's).
ANGLES = (
    Angle("top_pick_3_6", "{model} top pick at $3-$6", "steady", flags=("top",), price=(3.0, 6.0),
          audit="Place bets lost 0.3% in Feb-Jun and 2.9% in Jul-Oct.",
          audit_ml="Jul-Oct, 1,361 bets: win bets lost 4.4%, place bets lost 4.9%."),
    Angle("ran_3_2", "Last 5 starts: its placings go 3rd, then 2nd (e.g. 3-6-2)", "steady",
          comps=("Ran places: 3rd 2nd",),
          audit="Place bets made 2.5% in Feb-Jun and 0.4% in Jul-Oct. One huge dividend did it."),
    Angle("weight_below", "Carrying 2-3kg less than the field average", "long", comps=(WB,),
          audit="Win bets made 32.3% in Feb-Jun and 11.0% in Jul-Oct. Typical price $17. Lost 9.4% without its 10 biggest winners."),
    # ---- added 14 Nov 2026 ----
    Angle("fav_field_8_11", "Favourite in a race with 8 to 11 runners", "steady", flags=("fav",), field=(8, 11),
          audit="Win bets lost 2.7% in Feb-Jun and made 1.3% in Jul-Oct. Place bets lost 1.5% then 0.3%."),
    Angle("all_agree_fav", "Analyzer pick, ML pick and favourite are all the same horse", "agree",
          flags=("a_top", "ml_top", "fav"),
          audit="May-Oct, 854 bets: win bets broke even, place bets made 0.4%. A plain favourite lost 2.7% and 3.9%."),
    Angle("ml_top_fav", "ML top pick is also the favourite", "agree", flags=("ml_top", "fav"),
          audit="Jul-Oct, 1,801 bets: win bets broke even, place bets lost 4.5%."),
    # ---- added 15 Nov 2026: a closer look at "all three agree" (about 40 cuts; see AGREE_NOTE) ----
    Angle("agree_close", "All three agree, and the analyzer's 2nd pick is close behind (within 15%)", "agree",
          flags=("a_top", "ml_top", "fav", "lead_small"),
          audit="494 bets: win bets made 8.7%, place bets made 4.0%."),
    Angle("agree_clear", "All three agree, and the analyzer has it well clear (15% or more)", "agree",
          flags=("a_top", "ml_top", "fav", "lead_big"),
          audit="360 bets: win bets lost 11.9%, place bets lost 4.5%."),
    Angle("agree_field_8", "All three agree, in a race with 8 or more runners", "agree",
          flags=("a_top", "ml_top", "fav"), field=(8, 99),
          audit="663 bets: win bets made 2.6%, place bets made 4.1%. With 7 or fewer runners they lost 8.9% and 12.4%."),
    Angle("agree_saturday", "All three agree, on a Saturday", "agree",
          flags=("a_top", "ml_top", "fav", "saturday"),
          audit="262 bets: win bets made 11.5%, place bets made 6.6%. On other days win bets lost 5.1%."),
    Angle("ml_fav_not_analyzer", "ML pick is the favourite, but the analyzer picks another horse", "agree",
          flags=("ml_top", "fav", "a_other"),
          audit="1,038 bets: win bets made 1.2%, place bets lost 7.8%."),
    Angle("analyzer_fav_not_ml", "Analyzer pick is the favourite, but the ML picks another horse", "agree",
          flags=("a_top", "fav", "ml_other"),
          audit="430 bets: win bets lost 19.7%, place bets lost 9.4%. The clearest result: one to avoid."),
    Angle("fav_neither", "Favourite that neither the analyzer nor the ML picks", "agree",
          flags=("fav", "a_other", "ml_other"),
          audit="1,673 bets: win bets lost 2.1%, place bets lost 2.2%."),
    # ---- added 15 Nov 2026: the one LAY angle (bet AGAINST the horse on Betfair) ----
    Angle("lay_fav_ml_other_3of4", "LAY: Favourite the ML does not pick, but 3 or more ratings do", "lay",
          flags=("fav", "ml_other", "ratings_3"),
          audit="269 lay bets: made 8.4% per $1 risked (+16.0% in Feb-Jun, +5.2% in Jul-Oct). The horse won 27% "
                "of the time; any favourite wins 34%. Best of 32 lay ideas tested, so it is flattered."),
    Angle("photo_under_6", "Won a photo finish last start, now under $6", "steady",
          comps=("Last Start - Photo Win (<0.5L)",), price=(1.0, 6.0),
          audit="Win bets lost 8.7% in Feb-Jun and made 0.7% in Jul-Oct. Place bets lost 7.6% then made 2.4%."),
    Angle("ran_3_2_3_8", "Last 5 starts: its placings go 3rd, then 2nd, and it is now $3-$8", "steady",
          comps=("Ran places: 3rd 2nd",), price=(3.0, 8.0),
          audit="Win bets made 4.8% in Feb-Jun and lost 4.4% in Jul-Oct. Place bets lost 2.1% then 3.3%."),
    Angle("weight_below_10_30", "Carrying 2-3kg less than the field average, now $10-$30", "long", comps=(WB,),
          price=(10.0, 30.0),
          audit="Win bets made 13.8% in Feb-Jun and 9.6% in Jul-Oct. Place bets lost 7.9% then made 6.2%."),
    Angle("track_low_win", "Has won at this track before, but in under 15% of its runs there", "long",
          comps=("Track Win Rate - Low (1-15%)",),
          audit="Win bets made 9.4% in Feb-Jun and 13.5% in Jul-Oct. Typical price $16."),
    Angle("backmarker_sprint", "Settles at the back in a sprint race", "long", comps=(BM,),
          audit="Win bets made 5.2% in Feb-Jun and 4.9% in Jul-Oct. Lost 6.9% without its 3 biggest winners."),
    Angle("trainer_cold", "Trainer out of form (under 5 winners in last 100 runners)", "long",
          comps=("Trainer - Cold (L100 <5% SR)",),
          audit="Win bets made 8.3% in Feb-Jun and 24.0% in Jul-Oct. Typical price $26."),
    Angle("last_200_average", "Final 200m speed rated average", "long",
          comps=("API Sectional - Last 200m Average",),
          audit="Win bets made 21.3% in Feb-Jun and 10.7% in Jul-Oct. Only 1,200 bets."),
    Angle("quick_backup", "Racing again within 7 days", "long",
          comps=("Days Since Run - Quick Backup (≤7 days)",),
          audit="Win bets made 24.2% in Feb-Jun but only 2.2% in Jul-Oct."),
    Angle("weight_below_backmarker", "2-3kg less than the field average AND settles at the back in a sprint", "long",
          comps=(WB, BM),
          audit="Win bets made 118% in Feb-Jun and 72% in Jul-Oct, on only 710 bets. November lost."),
    Angle("two_long_flags", "Fits two or more of the long-shot angles in this table", "long",
          at_least=(2, LONG_SHOT_FLAGS),
          audit="Win bets made 54.3% in Feb-Jun and 37.2% in Jul-Oct. Typical price $19, 7 winners in 100."),
)
BY_KEY = {a.key: a for a in ANGLES}

MODEL_NAMES = {"analyzer": "Analyzer", "ml": "ML"}


def angle_name(angle, source="analyzer"):
    return angle.name.format(model=MODEL_NAMES.get(source, "Analyzer"))


def matching_angles(components, flags=(), price=None, starters=None, source="analyzer"):
    """Angle keys a runner fits.

    components: set of analyzer component names in its notes.
    flags: any of 'a_top', 'ml_top', 'fav'. price: its win price, or None.
    """
    components = components or ()
    flags = set(flags)
    if ("ml_top" if source == "ml" else "a_top") in flags:
        flags.add("top")
    keys = []
    for a in ANGLES:
        if not _fits_before_price(a, components, flags, starters):
            continue
        if a.price and (price is None or not a.price[0] <= float(price) < a.price[1]):
            continue
        keys.append(a.key)
    return keys


def _fits_before_price(a, components, flags, starters):
    if any(f not in flags for f in a.flags):
        return False
    if any(c not in components for c in a.comps):
        return False
    if a.at_least and sum(1 for c in a.at_least[1] if c in components) < a.at_least[0]:
        return False
    if a.field and (starters is None or not a.field[0] <= starters <= a.field[1]):
        return False
    return True


def _angle_components(notes):
    """Names in COMPONENT_LIKE found in the notes.

    Runs only the notes parser's own patterns for those names, and only when
    the note holds that component's key words. The full parser tries every
    pattern on every runner, which is far too slow here.
    """
    global _ANGLE_PATTERNS
    if not notes:
        return set()
    if _ANGLE_PATTERNS is None:
        import re
        from notes_parsing import _COMPILED_NOTES_COMPONENT_PATTERNS
        found = []
        for pattern, name in _COMPILED_NOTES_COMPONENT_PATTERNS:
            if name not in COMPONENT_LIKE:
                continue
            # An optional sign and spaces at the very start never change
            # whether a pattern is found, but make the search many times slower.
            source = pattern.pattern
            if source.startswith(_OPTIONAL_START):
                pattern = re.compile(source[len(_OPTIONAL_START):], pattern.flags)
            words = max(COMPONENT_LIKE[name].split('%'), key=len)
            found.append((words, pattern, name))
        _ANGLE_PATTERNS = found
    lowered = notes.lower()
    found = set()
    for words, pattern, name in _ANGLE_PATTERNS:
        at = lowered.find(words)
        if at < 0:
            continue
        # The sectional pattern opens with "<number>: Last 200m (Rank", so the
        # search can start just before those words instead of at the top.
        start = max(0, at - 40) if name in _STARTS_WITH_ITS_WORDS else 0
        if pattern.search(notes, start):
            found.add(name)
    return found


_ANGLE_PATTERNS = None
_OPTIONAL_START = r'[+\-]?\s*'
_STARTS_WITH_ITS_WORDS = {"API Sectional - Last 200m Average"}


def _ladbrokes_prices(session, horse_ids, column):
    """{horse_id: last Ladbrokes price saved before the jump}. column: 'odds' (win) or 'place_odds'.

    One index lookup per runner (race_id, horse_id, newest first), so the
    database never reads a runner's whole odds history.
    """
    if column not in ('odds', 'place_odds'):
        raise ValueError(column)
    from sqlalchemy import text
    horse_ids = sorted(set(horse_ids))
    prices = {}
    try:
        for first in range(0, len(horse_ids), 1000):
            chunk = horse_ids[first:first + 1000]
            params = {f'h{i}': hid for i, hid in enumerate(chunk)}
            rows = session.execute(text(
                f'SELECT h.id AS horse_id, (SELECT o.{column} FROM live_odds_snapshots o '
                f'WHERE o.race_id = h.race_id AND o.horse_id = h.id AND o.{column} > 1 '
                f'AND (rc.start_time_utc IS NULL OR o.captured_at <= rc.start_time_utc) '
                f'ORDER BY o.captured_at DESC LIMIT 1) AS price '
                f'FROM horses h JOIN races rc ON rc.id = h.race_id '
                f'WHERE h.id IN ({", ".join(f":h{i}" for i in range(len(chunk)))})'), params).mappings()
            for row in rows:
                if row['price']:
                    prices[row['horse_id']] = float(row['price'])
    except Exception:
        # No odds table (e.g. a fresh database): no Ladbrokes prices.
        session.rollback()
        return {}
    return prices


def _history_sql():
    likes = ' OR '.join(f'LOWER(p.notes) LIKE :like{i}' for i in range(len(COMPONENT_LIKE)))
    return f"""
    SELECT h.id AS horse_id, h.race_id, LOWER(TRIM(h.horse_name)) AS horse, p.id AS pred_id,
           p.score, p.ml_score,
           CASE WHEN p.ml_score IS NOT NULL AND p.ml_scored_at IS NOT NULL AND rc.start_time_utc IS NOT NULL
                     AND p.ml_scored_at < rc.start_time_utc THEN 1 ELSE 0 END AS ml_ok,
           r.finish_position AS fin, r.sp, r.bsp, r.place_bsp, m.date AS meeting_date,
           CASE WHEN {likes} THEN p.notes END AS notes
    FROM horses h
    JOIN predictions p ON p.horse_id = h.id
    JOIN races rc ON rc.id = h.race_id
    JOIN meetings m ON m.id = rc.meeting_id
    JOIN results r ON r.horse_id = h.id
    WHERE r.finish_position > 0 AND rc.results_incomplete IS NOT TRUE
    """


def _chunk_rows(session, race_ids, state):
    """One row per runner that ran in these races (newest prediction, duplicate horse rows collapsed)."""
    from sqlalchemy import text
    sql = _history_sql() + ' AND rc.id IN (' + ', '.join(f':race{i}' for i in range(len(race_ids))) + ')'
    params = {f'race{i}': rid for i, rid in enumerate(race_ids)}
    params.update({f'like{i}': frag for i, frag in enumerate(COMPONENT_LIKE.values())})
    if not state.get('place_bsp', True):
        sql = sql.replace('r.place_bsp', 'NULL AS place_bsp')
    try:
        rows = list(session.execute(text(sql), params).mappings())
    except Exception:
        if not state.get('place_bsp', True):
            raise
        # Before bsp_import has added results.place_bsp there is no place price.
        session.rollback()
        state['place_bsp'] = False
        rows = list(session.execute(text(sql.replace('r.place_bsp', 'NULL AS place_bsp')), params).mappings())
    best = {}
    for row in rows:
        key = (row['race_id'], row['horse'])
        kept = best.get(key)
        if kept is None or (row['horse_id'], row['pred_id']) > (kept['horse_id'], kept['pred_id']):
            best[key] = row
    races = {}
    for row in best.values():
        races.setdefault(row['race_id'], []).append(row)
    return races


def _ratings_for(session, horse_ids):
    """{horse_id: {'pf': rank, 't': rank, 'w': rank}} from the saved PuntingForm ratings.

    Only called for the few runners an angle needs it for: a race's saved
    ratings can hold the whole meeting, so they are never read for every race.
    """
    from types import SimpleNamespace
    from sqlalchemy import text
    import best_bet_rules
    horse_ids = sorted(set(horse_ids))
    out = {}
    try:
        for first in range(0, len(horse_ids), 200):
            chunk = horse_ids[first:first + 200]
            params = {f'h{i}': hid for i, hid in enumerate(chunk)}
            rows = session.execute(text(
                'SELECT h.id AS horse_id, h.csv_data, rc.id AS race_id, rc.race_number, rc.ratings_json '
                'FROM horses h JOIN races rc ON rc.id = h.race_id '
                f'WHERE rc.ratings_json IS NOT NULL AND h.id IN ({", ".join(f":h{i}" for i in range(len(chunk)))})'),
                params).mappings()
            by_race = {}
            for row in rows:
                if row['race_id'] not in by_race:
                    by_race[row['race_id']] = best_bet_rules.ratings_ranks(
                        SimpleNamespace(race_number=row['race_number'], ratings_json=row['ratings_json']))
                tab = best_bet_rules._tab(SimpleNamespace(csv_data=row['csv_data']))
                ranks = by_race[row['race_id']].get(tab)
                if ranks:
                    out[row['horse_id']] = ranks
    except Exception as e:
        session.rollback()
        logger.warning("Review angles: saved ratings unavailable: %s", e)
        return {}
    return out


def _is_saturday(value):
    """True when a meeting date (a date, or 'YYYY-MM-DD...' text) is a Saturday."""
    from datetime import date
    try:
        if not hasattr(value, 'weekday'):
            value = date.fromisoformat(str(value)[:10])
        return value.weekday() == 5
    except (TypeError, ValueError):
        return False


def _top(runners, value):
    """Rows sharing the best value (None when nobody has one)."""
    scored = [(value(r), r) for r in runners if value(r) is not None]
    if not scored:
        return ()
    best = max(v for v, _ in scored)
    return [r for v, r in scored if v == best][:1]


def history(session, race_ids, source='analyzer', stake=10.0):
    """How each angle has gone as a $10 win bet AND a $10 place bet over these races.

    Win bets are shown two ways: at the starting price, and at the Betfair
    price after commission. Place bets pay the Betfair place price after
    commission; a placed runner with no Betfair price yet uses the Ladbrokes
    place price, and is left out if there is neither.
    A price band uses the last Ladbrokes win price saved before the jump, or
    the starting price when none was saved. The favourite is the runner with
    the shortest starting price.
    Lay figures (for the 'lay' group) risk the same amount on every bet at
    the Betfair price: lose it when the horse wins, win the backer's stake
    less commission when it loses.
    """
    import place_bet

    race_ids = list(race_ids or [])
    picks = {a.key: [] for a in ANGLES}
    timing = {'rows': 0.0, 'prices': 0.0}
    state, races_seen, band_bets, band_at_sp = {}, 0, 0, 0

    for first in range(0, len(race_ids), CHUNK_RACES):
        started = time.time()
        races = _chunk_rows(session, race_ids[first:first + CHUNK_RACES], state)
        timing['rows'] += time.time() - started
        waiting = []          # (angle, item) that still need a price
        need_ratings = []     # (angle, item, is the analyzer's top pick) that still need the saved ratings
        for rid, runners in races.items():
            if source == 'ml' and not any(r['ml_ok'] for r in runners):
                continue
            races_seen += 1
            starters = len(runners)
            paid = place_bet.places_paid(starters)
            a_top = {id(r) for r in _top(runners, lambda r: r['score'])}
            ml_top = {id(r) for r in _top(runners, lambda r: r['ml_score'] if r['ml_ok'] else None)}
            priced = [r['sp'] for r in runners if r['sp'] and r['sp'] > 1]
            fav_sp = min(priced) if priced else None
            # How far the analyzer's top pick is ahead of its second pick.
            scores = sorted((r['score'] for r in runners if r['score'] is not None), reverse=True)
            lead = None
            if len(scores) > 1 and scores[1] > 0:
                lead = 'lead_big' if scores[0] >= LEAD_BIG * scores[1] else 'lead_small'
            saturday = _is_saturday(runners[0]['meeting_date'])
            for row in runners:
                flags = set()
                if id(row) in a_top:
                    flags.add('a_top')
                    if lead:
                        flags.add(lead)
                elif a_top:
                    flags.add('a_other')
                if id(row) in ml_top:
                    flags.add('ml_top')
                elif ml_top:
                    flags.add('ml_other')
                if fav_sp is not None and row['sp'] == fav_sp:
                    flags.add('fav')
                if ('ml_top' if source == 'ml' else 'a_top') in flags:
                    flags.add('top')
                components = _angle_components(row['notes'])
                if not components and not flags - {'a_other', 'ml_other'}:
                    continue
                if saturday:
                    flags.add('saturday')
                item = None
                for a in ANGLES:
                    by_ratings = 'ratings_3' in a.flags
                    if not _fits_before_price(a, components, flags | {'ratings_3'} if by_ratings else flags, starters):
                        continue
                    if item is None:
                        item = {'horse_id': row['horse_id'], 'won': row['fin'] == 1, 'sp': row['sp'] or 0,
                                'bsp': row['bsp'] if row['bsp'] and row['bsp'] > 1 else None,
                                'placed': row['fin'] <= paid,
                                'place_bsp': row['place_bsp'] if row['place_bsp'] and row['place_bsp'] > 1 else None}
                    if by_ratings:
                        need_ratings.append((a, item, 'a_top' in flags))
                    elif a.price:
                        waiting.append((a, item))
                    else:
                        picks[a.key].append(item)
        started = time.time()
        ratings = _ratings_for(session, [item['horse_id'] for _a, item, _top_pick in need_ratings])
        for a, item, analyzer_top in need_ratings:
            ranks = ratings.get(item['horse_id'])
            # No saved PFAI rank means the race has no ratings to count.
            if ranks and 'pf' in ranks and int(analyzer_top) + sum(
                    1 for key in ('pf', 't', 'w') if ranks.get(key) == 1) >= 3:
                picks[a.key].append(item)
        ladbrokes = _ladbrokes_prices(session, [item['horse_id'] for _a, item in waiting], 'odds')
        timing['prices'] += time.time() - started
        for a, item in waiting:
            price = ladbrokes.get(item['horse_id']) or item['sp']
            if price and a.price[0] <= price < a.price[1]:
                picks[a.key].append(item)
                band_bets += 1
                band_at_sp += 0 if item['horse_id'] in ladbrokes else 1

    started = time.time()
    # Placed with no Betfair place price yet: the Ladbrokes place price stands in.
    items = {id(i): i for found in picks.values() for i in found}.values()
    place_prices = _ladbrokes_prices(
        session, [i['horse_id'] for i in items if i['placed'] and not i['place_bsp']], 'place_odds')
    for i in items:
        i['ladbrokes_place'] = place_prices.get(i['horse_id']) if i['placed'] and not i['place_bsp'] else None
    timing['place_prices'] = time.time() - started
    logger.info("REVIEW_ANGLES_TIMING source=%s races=%d %s", source, races_seen,
                ' '.join(f'{k}={v:.2f}s' for k, v in timing.items()))

    angles = []
    for a in ANGLES:
        found = picks[a.key]
        bets = len(found)
        wins = sum(1 for i in found if i['won'])
        win_profit = sum((i['sp'] * stake - stake) if i['won'] else -stake for i in found)
        # Betfair win price after commission, in units of one stake.
        betfair = sorted(((i['bsp'] - 1) * (1 - COMMISSION) if i['won'] else -1.0) for i in found if i['bsp'])
        without3 = betfair[:-3] if len(betfair) > 3 else []
        # The starting price on those same bets, so the two prices can be compared fairly.
        sp_same = [(i['sp'] - 1) if i['won'] else -1.0 for i in found if i['bsp']]
        # Laying it instead (betting it loses), risking 1 unit a bet: lose the unit
        # when it wins, keep the backer's stake less commission when it loses.
        lay = [(-1.0 if i['won'] else (1 - COMMISSION) / (i['bsp'] - 1)) for i in found if i['bsp']]
        lay_prices = sorted(i['bsp'] for i in found if i['bsp'])
        placed = sum(1 for i in found if i['placed'])
        settled = [p for p in (place_bet._chart_profit(i) for i in found) if p is not None]
        place_profit = sum(p[0] for p in settled) * stake / place_bet.STAKE
        angles.append({
            'key': a.key, 'name': angle_name(a, source), 'group': a.group,
            'audit': (a.audit_ml if source == 'ml' and a.audit_ml else a.audit),
            'bets': bets, 'wins': wins,
            'win_rate': round(100.0 * wins / bets, 1) if bets else 0.0,
            'win_profit': round(win_profit, 2),
            'win_roi': round(100.0 * win_profit / (bets * stake), 1) if bets else None,
            'betfair_bets': len(betfair),
            'betfair_roi': round(100.0 * sum(betfair) / len(betfair), 1) if betfair else None,
            'betfair_roi_without_top3': round(100.0 * sum(without3) / len(without3), 1) if without3 else None,
            'sp_roi_on_betfair_bets': round(100.0 * sum(sp_same) / len(sp_same), 1) if sp_same else None,
            'lay_bets': len(lay),
            'lay_horse_lost_rate': round(100.0 * sum(1 for v in lay if v > 0) / len(lay), 1) if lay else None,
            'lay_typical_price': round(lay_prices[len(lay_prices) // 2], 2) if lay_prices else None,
            'lay_roi': round(100.0 * sum(lay) / len(lay), 1) if lay else None,
            'lay_profit': round(sum(lay) * stake, 2),
            'placed': placed,
            'place_rate': round(100.0 * placed / bets, 1) if bets else 0.0,
            'place_bets': len(settled),
            'place_waiting': bets - len(settled),
            'place_estimated': sum(1 for p in settled if p[1]),
            'place_profit': round(place_profit, 2),
            'place_roi': round(100.0 * place_profit / (len(settled) * stake), 1) if settled else None,
        })
    return {'races': races_seen, 'source': source, 'band_bets': band_bets, 'band_bets_at_sp': band_at_sp,
            'angles': angles}
