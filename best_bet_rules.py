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
start with "place" in the win record.
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


def recorded_rules(routes):
    """Rules named in a saved Prediction.best_bet_routes value, in RULES order."""
    saved = {r.strip() for r in str(routes or '').split(',')}
    return [rule for rule in RULES if rule.key in saved]


def bet_type(rules):
    """'win' when any of the rules is a win rule, else 'place'."""
    return 'win' if any(rule.bet == 'win' for rule in rules) else 'place'
