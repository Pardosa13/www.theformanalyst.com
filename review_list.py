"""Review list: today's runners that fit the angles closest to break even.

Not bets. Nothing in horse racing has made money here over time. These are
the angles that came closest, for a person to check against race replays
before deciding anything.

Found by audit/tasks/closest.py on 72,884 runners (Feb-Oct 2026), settled at
Betfair prices (8% commission). Each angle was close to break even in
Feb-Jun AND again in Jul-Oct. About 470 angles were tested in all, including
every jockey, trainer and jockey + trainer pairing; the people angles did
NOT hold up (those near break even in Feb-Jun did worse than average
after), so none are listed.

Backing every runner lost 7.7% to win and 8.0% for a place.
"""

from collections import namedtuple

Angle = namedtuple("Angle", "key name bet bets roi roi_first roi_second months_up without_top3 note")

# Checked 11 Nov 2026 (audit/tasks/closest.py verify): price bands use the
# price traded BEFORE the jump, not the final BSP. "without_top3" is the return
# with the angle's three biggest results taken out.
# Dropped after checking: "top pick and favourite" (-2.1%, no better than any
# favourite) and "backmarker in a sprint" (+5.1% was three big winners; -6.9%
# without them).
ANGLES = (
    Angle("top_pick_3_6", "Analyzer top pick at $3-$6", "place", 2664, -1.4, -0.3, -2.9, "4/9", -1.6,
          "Any favourite lost 2.1% for a place. Price is the live Ladbrokes win price."),
    Angle("ran_3_2", "Last 5 starts: placings go 3rd, then 2nd (e.g. 3-6-2)", "place", 4037, 1.6, 2.5, 0.4, "3/9", -3.8,
          "Only placings count: unplaced runs in between are skipped. One 119-unit place dividend makes it positive; -1.4% without it."),
    Angle("weight_below", "Carrying 2-3kg below the field average", "win", 6101, 22.5, 32.3, 11.0, "6/9", 8.1,
          "Win bet at long prices (median $23, 8% strike). -9.4% without its 10 biggest winners. Expect long losing runs."),
)
BY_KEY = {a.key: a for a in ANGLES}

COMPONENT_ANGLES = {
    "Ran places: 3rd 2nd": "ran_3_2",
    "Weight vs Field - Below (2-3kg)": "weight_below",
}


def matching_angles(components, analyzer_rank=None, price=None, is_favourite=False):
    """Angle keys a runner fits. components: set of analyzer component names."""
    keys = []
    if analyzer_rank == 1 and price is not None and 3.0 <= float(price) < 6.0:
        keys.append("top_pick_3_6")
    for name, key in COMPONENT_ANGLES.items():
        if name in (components or ()):
            keys.append(key)
    return keys


def angles_for_page():
    return [a._asdict() for a in ANGLES]


_HISTORY_SQL = """
    SELECT h.id AS horse_id, h.race_id, p.score, r.finish_position AS fin, r.sp, r.place_bsp,
           rc.start_time_utc AS start,
           CASE WHEN LOWER(p.notes) LIKE '%ran places:%' OR LOWER(p.notes) LIKE '%below race avg%'
                THEN p.notes END AS notes
    FROM horses h
    JOIN predictions p ON p.horse_id = h.id
    JOIN races rc ON rc.id = h.race_id
    JOIN meetings m ON m.id = rc.meeting_id
    JOIN results r ON r.horse_id = h.id
    WHERE r.finish_position > 0 AND rc.results_incomplete IS NOT TRUE
"""


def history(session, track='', date_from='', date_to='', race_ids=None, stake=10.0):
    """How each angle has gone as a $10 win bet AND a $10 place bet.

    Win bets pay the starting price. Place bets pay the Betfair place price
    after commission; a placed runner with no Betfair price yet uses the
    Ladbrokes place price, and is left out if there is neither.
    The top pick's $3-$6 price is the last Ladbrokes win price saved before
    the jump, or the starting price when none was saved.
    """
    import place_bet

    sql, params = _HISTORY_SQL, {}
    if track:
        sql += ' AND LOWER(m.meeting_name) LIKE :track'
        params['track'] = f'%{track.lower()}%'
    if date_from:
        sql += ' AND m.uploaded_at >= :date_from'
        params['date_from'] = date_from
    if date_to:
        sql += ' AND m.uploaded_at <= :date_to'
        params['date_to'] = date_to
    if race_ids is not None:
        if not race_ids:
            return _history_result({}, 0, 0)
        sql += ' AND rc.id IN (' + ', '.join(f':race{i}' for i in range(len(race_ids))) + ')'
        params.update({f'race{i}': rid for i, rid in enumerate(race_ids)})

    races, seen = {}, set()
    for row in place_bet._rows(session, sql, params):
        if row['horse_id'] in seen:
            continue
        seen.add(row['horse_id'])
        races.setdefault(row['race_id'], []).append(row)

    tops = {rid: max(runners, key=lambda r: r['score'] or 0) for rid, runners in races.items()}
    ladbrokes = place_bet.ladbrokes_place_prices(
        session, [{'race_id': t['race_id'], 'horse_id': t['horse_id'], 'start': t['start']} for t in tops.values()],
        column='odds')

    picks = {a.key: [] for a in ANGLES}
    sp_priced = 0
    for rid, runners in races.items():
        paid = place_bet.places_paid(len(runners))
        top = tops[rid]
        for row in runners:
            price = None
            if row is top:
                price = ladbrokes.get(row['horse_id']) or row['sp']
            keys = matching_angles(place_bet.note_components(row['notes']),
                                   analyzer_rank=1 if row is top else None, price=price)
            if not keys:
                continue
            if 'top_pick_3_6' in keys and row['horse_id'] not in ladbrokes:
                sp_priced += 1
            item = {'horse_id': row['horse_id'], 'race_id': rid, 'start': row['start'],
                    'won': row['fin'] == 1, 'sp': row['sp'] or 0, 'placed': row['fin'] <= paid,
                    'place_bsp': row['place_bsp'] if row['place_bsp'] and row['place_bsp'] > 1 else None}
            for key in keys:
                picks[key].append(item)

    place_bet._add_ladbrokes_place_prices(session, list({id(i): i for items in picks.values() for i in items}.values()))

    stats = {}
    for key, items in picks.items():
        bets = len(items)
        wins = sum(1 for i in items if i['won'])
        win_profit = sum((i['sp'] * stake - stake) if i['won'] else -stake for i in items)
        placed = sum(1 for i in items if i['placed'])
        settled = [p for p in (place_bet._chart_profit(i) for i in items) if p is not None]
        place_profit = sum(p[0] for p in settled) * stake / place_bet.STAKE
        stats[key] = {
            'bets': bets, 'wins': wins,
            'win_rate': round(100.0 * wins / bets, 1) if bets else 0.0,
            'win_profit': round(win_profit, 2),
            'win_roi': round(100.0 * win_profit / (bets * stake), 1) if bets else None,
            'placed': placed,
            'place_rate': round(100.0 * placed / bets, 1) if bets else 0.0,
            'place_bets': len(settled),
            'place_waiting': bets - len(settled),
            'place_estimated': sum(1 for p in settled if p[1]),
            'place_profit': round(place_profit, 2),
            'place_roi': round(100.0 * place_profit / (len(settled) * stake), 1) if settled else None,
        }
    return _history_result(stats, len(races), sp_priced)


def _history_result(stats, races, sp_priced):
    empty = {'bets': 0, 'wins': 0, 'win_rate': 0.0, 'win_profit': 0.0, 'win_roi': None, 'placed': 0,
             'place_rate': 0.0, 'place_bets': 0, 'place_waiting': 0, 'place_estimated': 0,
             'place_profit': 0.0, 'place_roi': None}
    return {
        'races': races,
        'top_pick_priced_at_sp': sp_priced,
        'angles': [{'key': a.key, 'name': a.name, 'suggested_bet': a.bet, **stats.get(a.key, empty)}
                   for a in ANGLES],
    }
