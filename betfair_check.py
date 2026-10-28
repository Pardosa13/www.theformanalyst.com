"""Honest results: every pick type settled at the bookie SP AND at Betfair SP.

The bookie SP carries a 20-30% margin, so a pick can look poor at SP and
still be close to break-even at Betfair, or look fine at SP only because of
a few big winners. Betfair SP (results.bsp) is the fairest price we hold.
Betfair returns are after 8% commission on winnings.

Only races with complete results count. A pick only counts if it was known
before the race started (scored, captured or flagged before start time).
"""
from sqlalchemy import text

BETFAIR_COMMISSION = 0.08

_BASE = """
    FROM results r
    JOIN horses h ON h.id = r.horse_id
    JOIN races rc ON rc.id = h.race_id
    JOIN meetings m ON m.id = rc.meeting_id
    LEFT JOIN predictions p ON p.horse_id = h.id
    WHERE r.finish_position > 0 AND r.sp > 1
      AND rc.results_incomplete IS NOT TRUE
      AND (:date_from IS NULL OR m.date >= CAST(:date_from AS DATE))
      AND (:date_to IS NULL OR m.date <= CAST(:date_to AS DATE))
"""

# Each selection returns the columns fin, sp, bsp (one row per bet).
_SELECTIONS = [
    ('all', 'Every runner', 'Baseline: what backing everything returns.',
     "SELECT r.finish_position AS fin, r.sp, r.bsp" + _BASE),
    ('favourite', 'Market favourite', 'Shortest SP in each race.',
     """SELECT fin, sp, bsp FROM (SELECT r.finish_position AS fin, r.sp, r.bsp,
            ROW_NUMBER() OVER (PARTITION BY rc.id ORDER BY r.sp, h.id) AS rk""" + _BASE + ") x WHERE rk = 1"),
    ('analyzer_top', 'Analyzer top pick', 'Highest analyzer score in each race.',
     """SELECT fin, sp, bsp FROM (SELECT r.finish_position AS fin, r.sp, r.bsp,
            ROW_NUMBER() OVER (PARTITION BY rc.id ORDER BY p.score DESC, h.id) AS rk""" + _BASE
     + " AND p.score IS NOT NULL) x WHERE rk = 1"),
    ('ml_top', 'ML top pick', 'Highest ML win chance, scored before the jump.',
     """SELECT fin, sp, bsp FROM (SELECT r.finish_position AS fin, r.sp, r.bsp,
            ROW_NUMBER() OVER (PARTITION BY rc.id ORDER BY p.ml_win_probability DESC, h.id) AS rk""" + _BASE
     + " AND p.ml_win_probability IS NOT NULL AND p.ml_scored_at < rc.start_time_utc) x WHERE rk = 1"),
]
for lo, hi in ((0, 5), (5, 10), (10, 20), (20, 1000)):
    label = f'Value edge {lo}-{hi} pts' if hi < 1000 else f'Value edge {lo}+ pts'
    _SELECTIONS.append((
        f'value_{lo}', label, 'Model chance beat the market by this much, captured before the jump.',
        "SELECT r.finish_position AS fin, r.sp, r.bsp" + _BASE
        + f" AND p.value_edge_pct >= {lo} AND p.value_edge_pct < {hi}"
          " AND p.value_edge_captured_at < rc.start_time_utc"))
_SELECTIONS.append((
    'kelly', 'Kelly-staked runners (flat 1 unit)', 'Any runner the Kelly solver put money on.',
    "SELECT r.finish_position AS fin, r.sp, r.bsp" + _BASE + " AND p.kelly_stake_pct > 0"))

_AGG = """
    SELECT COUNT(*) AS bets,
           SUM(CASE WHEN fin = 1 THEN 1 ELSE 0 END) AS wins,
           SUM(CASE WHEN fin = 1 THEN sp - 1 ELSE -1 END) AS profit_sp,
           COUNT(bsp) AS bsp_bets,
           SUM(CASE WHEN bsp IS NULL THEN 0 WHEN fin = 1 THEN (bsp - 1) * :keep ELSE -1 END) AS profit_bsp
    FROM ({sel}) s
"""


def _summarise(row):
    bets = int(row.bets or 0)
    bsp_bets = int(row.bsp_bets or 0)
    return {
        'bets': bets,
        'wins': int(row.wins or 0),
        'strike_rate': (100.0 * (row.wins or 0) / bets) if bets else None,
        'roi_sp': (100.0 * float(row.profit_sp or 0) / bets) if bets else None,
        'bsp_bets': bsp_bets,
        'roi_bsp': (100.0 * float(row.profit_bsp or 0) / bsp_bets) if bsp_bets else None,
    }


def pick_type_results(session, date_from=None, date_to=None):
    """One row per pick type with ROI at bookie SP and at Betfair SP."""
    params = {'date_from': date_from or None, 'date_to': date_to or None, 'keep': 1 - BETFAIR_COMMISSION}
    rows = []
    for key, label, note, sel in _SELECTIONS:
        row = session.execute(text(_AGG.format(sel=sel)), params).fetchone()
        rows.append({'key': key, 'label': label, 'note': note, **_summarise(row)})
    bsp_through = session.execute(text(
        "SELECT MAX(m.date) FROM results r JOIN horses h ON h.id = r.horse_id "
        "JOIN races rc ON rc.id = h.race_id JOIN meetings m ON m.id = rc.meeting_id WHERE r.bsp IS NOT NULL"
    )).scalar()
    return {'rows': rows, 'bsp_through': bsp_through, 'commission_pct': BETFAIR_COMMISSION * 100}


# A Best Bet counts only if it was flagged (posted) or qualified before the
# race's start time. Before 15 Oct 2026 some were recorded after the jump.
_BEST_BET_BASE = """
    FROM predictions p
    JOIN horses h ON h.id = p.horse_id
    JOIN races rc ON rc.id = h.race_id
    JOIN meetings m ON m.id = rc.meeting_id
    JOIN results r ON r.horse_id = h.id
    WHERE (p.best_bet_flagged_at IS NOT NULL OR p.best_bet_qualified_at IS NOT NULL)
      AND r.finish_position > 0 AND r.sp > 1
      AND rc.results_incomplete IS NOT TRUE
      AND COALESCE(p.best_bet_routes, '') NOT LIKE 'place%'
"""
_PRE_RACE = """ AND rc.start_time_utc IS NOT NULL AND COALESCE(
        CASE WHEN p.best_bet_flagged_at IS NULL THEN p.best_bet_qualified_at
             WHEN p.best_bet_qualified_at IS NULL THEN p.best_bet_flagged_at
             WHEN p.best_bet_flagged_at < p.best_bet_qualified_at THEN p.best_bet_flagged_at
             ELSE p.best_bet_qualified_at END, rc.start_time_utc) < rc.start_time_utc"""


def best_bets_record(session):
    """Settled Best Bets known before the jump: totals, by route and by month."""
    params = {'keep': 1 - BETFAIR_COMMISSION}
    sel = "SELECT r.finish_position AS fin, r.sp, r.bsp" + _BEST_BET_BASE + _PRE_RACE
    total = _summarise(session.execute(text(_AGG.format(sel=sel)), params).fetchone())
    excluded = session.execute(text(
        "SELECT COUNT(*)" + _BEST_BET_BASE + " AND NOT (1=1" + _PRE_RACE + ")"), {}).scalar() or 0

    by_route = []
    route_rows = session.execute(text(
        "SELECT COALESCE(NULLIF(p.best_bet_routes, ''), 'posted') AS route, r.finish_position AS fin, r.sp, r.bsp"
        + _BEST_BET_BASE + _PRE_RACE)).fetchall()
    by_month_raw = session.execute(text(
        "SELECT m.date AS d, r.finish_position AS fin, r.sp, r.bsp" + _BEST_BET_BASE + _PRE_RACE)).fetchall()

    def fold(rows, keyfn):
        out = {}
        for row in rows:
            k = keyfn(row)
            agg = out.setdefault(k, {'bets': 0, 'wins': 0, 'profit_sp': 0.0, 'bsp_bets': 0, 'profit_bsp': 0.0})
            won = row.fin == 1
            agg['bets'] += 1
            agg['wins'] += int(won)
            agg['profit_sp'] += (row.sp - 1) if won else -1.0
            if row.bsp is not None:
                agg['bsp_bets'] += 1
                agg['profit_bsp'] += (row.bsp - 1) * (1 - BETFAIR_COMMISSION) if won else -1.0
        result = []
        for k, a in out.items():
            result.append({
                'key': k, 'bets': a['bets'], 'wins': a['wins'],
                'strike_rate': 100.0 * a['wins'] / a['bets'],
                'roi_sp': 100.0 * a['profit_sp'] / a['bets'],
                'bsp_bets': a['bsp_bets'],
                'roi_bsp': (100.0 * a['profit_bsp'] / a['bsp_bets']) if a['bsp_bets'] else None,
            })
        return result

    # A horse can qualify by several routes; count it under each route.
    exploded = []
    for row in route_rows:
        for route in str(row.route).split(','):
            if route.strip().startswith('place_'):
                continue  # a place bet: tracked by place_bet.py, not as a win
            exploded.append(type('R', (), {'route': route.strip() or 'posted', 'fin': row.fin, 'sp': row.sp, 'bsp': row.bsp}))
    by_route = sorted(fold(exploded, lambda r: r.route), key=lambda x: -x['bets'])
    by_month = sorted(fold(by_month_raw, lambda r: str(r.d)[:7]), key=lambda x: x['key'], reverse=True)
    return {'total': total, 'excluded_after_start': int(excluded), 'by_route': by_route, 'by_month': by_month,
            'commission_pct': BETFAIR_COMMISSION * 100}
