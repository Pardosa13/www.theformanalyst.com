"""Best Bets rule from 15 Nov 2026: a favourite the ML model also picks.

This replaced the place rules on the Best Bets page (place_bet.py still keeps
the place rules' record for the ML Data page). It is a short-list for a
person to check against race replays, not a proven winner.

Where it came from: a read-only sweep on 15 Nov 2026 of every mix of the
favourite and five top picks (analyzer, ML, PFAI, time rating, weight-class
rating) over about 77,000 runners, Feb-Nov 2026, at Betfair prices after 8%
commission. What it found:

- More ratings agreeing made a favourite WORSE, not better: none of the four
  non-ML ratings -3.4%, three or more -8.1%, all four -12.0%. When everything
  lines up the price is already too short.
- The ML model is the exception. A favourite the ML also picks about broke
  even (+0.7% on 1,889 bets), and did best when the other ratings mostly
  did NOT agree (+4.0% on 1,061 bets): the one rule kept.
- Nothing here is a proven profit. The rule loses at the starting price.
  Before 16 Oct 2026 the ML scores were worked out after the race, so the
  ML figures may flatter it.

The rule needs the live market (who is favourite), so picks are made and
recorded when the Best Bets page is opened before the jump. Route keys
start with "agree_": betfair_check.py counts every route that does not
start with "place" or "lay" in the win record.

The page also lists one LAY bet (LAY_RULES): a favourite to bet AGAINST.
"""
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Rule:
    key: str        # stored in Prediction.best_bet_routes; never a substring of another key
    name: str       # plain words, shown on the page
    short: str      # toggle button
    bet: str        # 'win' or 'place': the bet the sweep found closer to break even
    bets: int       # bets in the sweep
    win_pct: float
    place_pct: float
    win_roi: float      # Betfair price after commission
    place_roi: float    # Betfair place price after commission
    note: str = ''


RULES = (
    Rule('agree_ml_fav_quiet',
         'Favourite + ML top pick, and at most one other rating has it on top',
         'ML + favourite, quiet', 'win', 1061, 38.2, 65.3, 4.0, -5.1,
         'In profit in both halves of the year (+9.2%, +3.6%). The best win result in the sweep.'),
)
RULES_BY_KEY = {rule.key: rule for rule in RULES}
ROUTE_KEYS = tuple(rule.key for rule in RULES)

# LAY bets: bet AGAINST the horse on Betfair. Kept apart from RULES so a lay
# is never shown, counted or posted as a horse to back. For a lay rule,
# win_pct is how often the horse WON (the lay lost) and win_roi is the lay
# return per $1 risked; the place figures are unused.
# The best of 32 lay ideas checked read-only on 15 Nov 2026: 269 lay bets,
# +8.4% per $1 risked (+16.0% Feb-Jun, +5.2% Jul-Oct), against -3.2% for
# laying every favourite. Picked with hindsight, so it is flattered.
LAY_RULES = (
    Rule('lay_fav_ml_other_3of4',
         'LAY: Favourite the ML does not pick, but 3 or more ratings do',
         'LAY: ML against it', 'lay', 269, 27.1, 0.0, 8.4, 0.0,
         'The horse won 27% of the time; any favourite wins 34%. Ahead in both halves of the year '
         '(+16.0%, +5.2%). On races the ML scored before the jump: 39 lay bets, +16.9%.'),
)
LAY_RULES_BY_KEY = {rule.key: rule for rule in LAY_RULES}
ALL_RULES = RULES + LAY_RULES

# Rules that made picks for a short time and were taken off the page. Their
# keys stay reserved so an old saved route is never mistaken for a new rule.
#   agree_ml_pfai_fav       favourite + ML + PFAI (15 Nov 2026 only)
#   agree_fav_3of4_mid      favourite, 3+ ratings, 8-11 runners (15 Nov 2026 only)
#   agree_fav_3of4_odds_on  favourite under $2, 3+ ratings (15 Nov 2026 only)


def _load(value):
    while isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value


def _whole(value):
    """'3', 3 or '3.0' -> 3; anything else -> None."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return int(number) if number == int(number) else None


def ratings_ranks(race):
    """{tab number: {'pf': rank, 't': rank, 'w': rank}} from the race's saved PuntingForm ratings.

    pf = PFAI, t = time rating, w = weight-class rating. The saved blob can
    hold the whole meeting, so only this race's runners are kept.
    """
    blob = _load(getattr(race, 'ratings_json', None))
    items = (blob.get('payLoad') or []) if isinstance(blob, dict) else []
    out = {}
    for item in items:
        if not isinstance(item, dict) or str(item.get('raceNo')) != str(getattr(race, 'race_number', None)):
            continue
        tab = _whole(item.get('tabNo'))
        if tab is None:
            continue
        ranks = out.setdefault(tab, {})
        for key, field in (('pf', 'pfaiRank'), ('t', 'timeRank'), ('w', 'weightClassRank')):
            rank = _whole(item.get(field))
            if rank is not None and (key not in ranks or rank < ranks[key]):
                ranks[key] = rank
    return out


def _tab(horse):
    csv_data = _load(getattr(horse, 'csv_data', None))
    return _whole(csv_data.get('horse number')) if isinstance(csv_data, dict) else None


def race_signals(race):
    """Which top picks each runner holds: {horse_id: signals}, has_ratings.

    signals: a (analyzer top), ml (ML top), pf / t / w (PFAI, time and
    weight-class rank 1), n4 (how many of a, pf, t, w) and ml_other (the ML's
    top pick is a different runner). Scratched runners are left out.
    has_ratings is False when the race has no saved PFAI ranks: the rules
    count ratings, so they make no picks there.
    """
    active = [h for h in getattr(race, 'horses', []) or []
              if not getattr(h, 'is_scratched', False) and getattr(h, 'prediction', None)]
    ranks = ratings_ranks(race)
    by_horse = {h.id: ranks.get(_tab(h), {}) for h in active}
    has_ratings = any('pf' in r for r in by_horse.values())

    a_top = max(active, key=lambda h: h.prediction.score or 0).id if active else None
    with_ml = [h for h in active if h.prediction.ml_score is not None]
    ml_top = max(with_ml, key=lambda h: h.prediction.ml_score).id if with_ml else None

    signals = {}
    for h in active:
        r = by_horse[h.id]
        s = {'a': h.id == a_top, 'ml': h.id == ml_top,
             'pf': r.get('pf') == 1, 't': r.get('t') == 1, 'w': r.get('w') == 1}
        s['n4'] = sum(1 for key in ('a', 'pf', 't', 'w') if s[key])
        s['ml_other'] = ml_top is not None and h.id != ml_top
        signals[h.id] = s
    return signals, has_ratings


def rules_for(signals, is_favourite, price, starters, has_ratings=True):
    """Rules a runner fits, in RULES order. signals: one entry of race_signals().

    price and starters are not used by the one rule left; they stay so a
    rule that needs them can be added without touching the callers.
    """
    if not signals or not is_favourite or not has_ratings:
        return []
    keys = []
    if signals['ml'] and signals['n4'] <= 1:
        keys.append('agree_ml_fav_quiet')
    return [RULES_BY_KEY[key] for key in keys]


def lay_rules_for(signals, is_favourite, has_ratings=True):
    """Lay rules a runner fits: the favourite, top pick of 3+ ratings, and the ML picks another horse."""
    if not signals or not is_favourite or not has_ratings:
        return []
    if signals['n4'] >= 3 and signals['ml_other']:
        return [LAY_RULES_BY_KEY['lay_fav_ml_other_3of4']]
    return []


def recorded_rules(routes):
    """Rules named in a saved Prediction.best_bet_routes value: back rules, then lay rules."""
    saved = {r.strip() for r in str(routes or '').split(',')}
    return [rule for rule in ALL_RULES if rule.key in saved]


def bet_type(rules):
    """'lay' when any rule is a lay rule; else 'win' when any is a win rule; else 'place'.

    Lay comes first on purpose: a horse must never be posted as a bet to
    back when it was listed as one to bet against.
    """
    if any(rule.bet == 'lay' for rule in rules):
        return 'lay'
    return 'win' if any(rule.bet == 'win' for rule in rules) else 'place'


def lay_return(won, betfair_price, commission=0.08):
    """Return of a lay risking 1 unit: -1 when the horse wins, else the backer's stake less commission."""
    return -1.0 if won else (1 - commission) / (betfair_price - 1)


def lay_record(session):
    """Live record of each lay rule: picks recorded on Best Bets before the jump.

    {key: {'bets', 'priced', 'horse_lost_rate', 'roi'}}. roi is the return
    per 100 risked at the Betfair price, over the bets that have one.
    """
    from sqlalchemy import text
    out = {}
    for rule in LAY_RULES:
        rows = session.execute(text(
            "SELECT r.finish_position AS fin, r.bsp FROM predictions p "
            "JOIN horses h ON h.id = p.horse_id JOIN races rc ON rc.id = h.race_id "
            "JOIN results r ON r.horse_id = h.id "
            "WHERE p.best_bet_routes LIKE :route AND r.finish_position > 0 "
            "AND rc.results_incomplete IS NOT TRUE AND p.best_bet_qualified_at IS NOT NULL "
            "AND rc.start_time_utc IS NOT NULL AND p.best_bet_qualified_at < rc.start_time_utc"),
            {'route': f'%{rule.key}%'}).fetchall()
        priced = [lay_return(row[0] == 1, row[1]) for row in rows if row[1] and row[1] > 1]
        out[rule.key] = {
            'bets': len(rows),
            'priced': len(priced),
            'horse_lost_rate': 100.0 * sum(1 for row in rows if row[0] != 1) / len(rows) if rows else None,
            'roi': 100.0 * sum(priced) / len(priced) if priced else None,
        }
    return out


CHART_STAKE = 10.0


def chart(session):
    """Running profit of the picks and of the lay bets, for the Best Bets graph.

    Only bets listed on the Best Bets page before the jump count.
    - Picks: a $10 win bet at the starting price.
    - Lay bets: risking $10 at the Betfair price. Until the monthly Betfair
      price loads, the starting price stands in and the bet is marked as an
      estimate (the Betfair price is usually a little higher, so an estimated
      lay win is a little too big).

    Returns {'labels': [dates], 'series': [{'key', 'name', 'kind', 'bets',
    'won', 'profit', 'estimated', 'points': [running profit per date or
    None before the first bet]}]}.
    """
    from sqlalchemy import text
    series, days = [], set()
    for rule in ALL_RULES:
        rows = session.execute(text(
            "SELECT m.date AS day, rc.start_time_utc AS start, r.finish_position AS fin, r.sp, r.bsp "
            "FROM predictions p JOIN horses h ON h.id = p.horse_id JOIN races rc ON rc.id = h.race_id "
            "JOIN meetings m ON m.id = rc.meeting_id JOIN results r ON r.horse_id = h.id "
            "WHERE p.best_bet_routes LIKE :route AND r.finish_position > 0 "
            "AND rc.results_incomplete IS NOT TRUE AND p.best_bet_qualified_at IS NOT NULL "
            "AND rc.start_time_utc IS NOT NULL AND p.best_bet_qualified_at < rc.start_time_utc "
            "ORDER BY rc.start_time_utc, h.id"),
            {'route': f'%{rule.key}%'}).fetchall()
        lay = rule.bet == 'lay'
        by_day, bets, good, profit, estimated = {}, 0, 0, 0.0, 0
        for day, _start, fin, sp, bsp in rows:
            won = fin == 1
            if lay:
                price = bsp if bsp and bsp > 1 else sp
                if not price or price <= 1:
                    continue                      # no price at all yet: cannot be settled
                estimated += 0 if bsp and bsp > 1 else 1
                result = lay_return(won, price) * CHART_STAKE
                good += 0 if won else 1           # a lay is a good result when the horse loses
            else:
                if won and not sp:
                    continue
                result = ((sp - 1) if won else -1.0) * CHART_STAKE
                good += 1 if won else 0
            bets += 1
            profit += result
            key = str(day)[:10] if day else ''
            by_day[key] = round(profit, 2)
            days.add(key)
        series.append({'key': rule.key, 'name': rule.name, 'kind': 'lay' if lay else 'pick', 'bets': bets,
                       'won': good, 'profit': round(profit, 2), 'estimated': estimated, 'by_day': by_day})
    labels = sorted(days)
    for s in series:
        by_day, last, points = s.pop('by_day'), None, []
        for label in labels:
            last = by_day.get(label, last)
            points.append(last)
        s['points'] = points
    return {'labels': labels, 'series': series}
