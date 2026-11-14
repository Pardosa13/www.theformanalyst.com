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

The "audit" text on each angle is that earlier Betfair-price result
(Feb-Jun / Jul-Oct). It was chosen with hindsight, so the running record on
the page is the honest test.
"""

import logging
import time
from collections import namedtuple

logger = logging.getLogger(__name__)

COMMISSION = 0.08
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
# group    'steady' (short prices) or 'long' (long shots, Betfair price only)
# comps    analyzer components that must ALL be in the notes
# flags    'top' = the page's top pick, 'a_top' / 'ml_top' = analyzer / ML top pick, 'fav' = favourite
# at_least (n, names): at least n of these components
# price    (low, high): low <= price < high. Last Ladbrokes win price before the jump, else starting price
# field    (low, high) runners in the race, both included
Angle = namedtuple("Angle", "key name group comps flags at_least price field audit",
                   defaults=((), (), None, None, None, ""))

ANGLES = (
    Angle("top_pick_3_6", "{model} top pick at $3-$6", "steady", flags=("top",), price=(3.0, 6.0),
          audit="Analyzer: place -0.3% / -2.9%"),
    Angle("ran_3_2", "Last 5 starts: placings go 3rd, then 2nd (e.g. 3-6-2)", "steady",
          comps=("Ran places: 3rd 2nd",), audit="Place +2.5% / +0.4%"),
    Angle("weight_below", "Carrying 2-3kg below the field average", "long", comps=(WB,),
          audit="Win +32.3% / +11.0% (median $17)"),
    # ---- added 14 Nov 2026 ----
    Angle("fav_field_8_11", "Favourite in a field of 8 to 11", "steady", flags=("fav",), field=(8, 11),
          audit="Win -2.7% / +1.3%, place -1.5% / -0.3%"),
    Angle("all_agree_fav", "Analyzer pick, ML pick and favourite are the same horse", "steady",
          flags=("a_top", "ml_top", "fav"), audit="Win -0.9%, place +1.2% (Jul-Oct only, 819 bets)"),
    Angle("ml_top_fav", "ML top pick is the favourite", "steady", flags=("ml_top", "fav"),
          audit="Win 0.0%, place -4.5% (Jul-Oct only)"),
    Angle("photo_under_6", "Won a photo finish last start, now under $6", "steady",
          comps=("Last Start - Photo Win (<0.5L)",), price=(1.0, 6.0),
          audit="Win -8.7% / +0.7%, place -7.6% / +2.4%"),
    Angle("ran_3_2_3_8", "Placings go 3rd then 2nd, at $3-$8", "steady",
          comps=("Ran places: 3rd 2nd",), price=(3.0, 8.0), audit="Win +4.8% / -4.4%, place -2.1% / -3.3%"),
    Angle("weight_below_10_30", "2-3kg below the field average, at $10-$30", "long", comps=(WB,),
          price=(10.0, 30.0), audit="Win +13.8% / +9.6%, place -7.9% / +6.2%"),
    Angle("track_low_win", "Has won at this track, but under 15% of the time", "long",
          comps=("Track Win Rate - Low (1-15%)",), audit="Win +9.4% / +13.5% (median $16)"),
    Angle("backmarker_sprint", "Backmarker in a sprint", "long", comps=(BM,),
          audit="Win +5.2% / +4.9%; three big winners, -6.9% without them"),
    Angle("trainer_cold", "Trainer is cold (under 5% winners in last 100)", "long",
          comps=("Trainer - Cold (L100 <5% SR)",), audit="Win +8.3% / +24.0% (median $26)"),
    Angle("last_200_average", "Last 200m sectional rated average", "long",
          comps=("API Sectional - Last 200m Average",), audit="Win +21.3% / +10.7% (1,200 bets)"),
    Angle("quick_backup", "Backing up within 7 days", "long",
          comps=("Days Since Run - Quick Backup (≤7 days)",), audit="Win +24.2% / +2.2%"),
    Angle("weight_below_backmarker", "2-3kg below the field average AND a backmarker in a sprint", "long",
          comps=(WB, BM), audit="Win +118% / +72% on 710 bets; November lost"),
    Angle("two_long_flags", "Two or more long-shot flags together", "long", at_least=(2, LONG_SHOT_FLAGS),
          audit="Win +54.3% / +37.2% (median $19, 7% winners)"),
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
           r.finish_position AS fin, r.sp, r.bsp, r.place_bsp,
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
            for row in runners:
                flags = set()
                if id(row) in a_top:
                    flags.add('a_top')
                if id(row) in ml_top:
                    flags.add('ml_top')
                if fav_sp is not None and row['sp'] == fav_sp:
                    flags.add('fav')
                if ('ml_top' if source == 'ml' else 'a_top') in flags:
                    flags.add('top')
                components = _angle_components(row['notes'])
                if not flags and not components:
                    continue
                item = None
                for a in ANGLES:
                    if not _fits_before_price(a, components, flags, starters):
                        continue
                    if item is None:
                        item = {'horse_id': row['horse_id'], 'won': row['fin'] == 1, 'sp': row['sp'] or 0,
                                'bsp': row['bsp'] if row['bsp'] and row['bsp'] > 1 else None,
                                'placed': row['fin'] <= paid,
                                'place_bsp': row['place_bsp'] if row['place_bsp'] and row['place_bsp'] > 1 else None}
                    if a.price:
                        waiting.append((a, item))
                    else:
                        picks[a.key].append(item)
        started = time.time()
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
        placed = sum(1 for i in found if i['placed'])
        settled = [p for p in (place_bet._chart_profit(i) for i in found) if p is not None]
        place_profit = sum(p[0] for p in settled) * stake / place_bet.STAKE
        angles.append({
            'key': a.key, 'name': angle_name(a, source), 'group': a.group, 'audit': a.audit,
            'bets': bets, 'wins': wins,
            'win_rate': round(100.0 * wins / bets, 1) if bets else 0.0,
            'win_profit': round(win_profit, 2),
            'win_roi': round(100.0 * win_profit / (bets * stake), 1) if bets else None,
            'betfair_bets': len(betfair),
            'betfair_roi': round(100.0 * sum(betfair) / len(betfair), 1) if betfair else None,
            'betfair_roi_without_top3': round(100.0 * sum(without3) / len(without3), 1) if without3 else None,
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
