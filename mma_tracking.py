"""UFC Edge Finder picks and results tracking.

build_edge_bets() is the one place UFC edges are worked out: the Edge Finder
page and the weekly sync both use it. Every "value" pick is saved once, the
first time it qualifies, and only while that fight has not started
(mma_tracked_edges). Results are read from mma_fights.winner_name, so a pick
settles as soon as the result is stored; no separate settling job is needed.

model_record() is the ML predictor's record on finished fights, counting only
predictions made before the event day.
"""
import math
from datetime import datetime, timezone

from sqlalchemy import text

from mma_data import calculate_mma_edge, names_match, normalise_name, pairs_match

FIGHTS_SQL = """
    SELECT
        f.id, f.fighter_1_name, f.fighter_2_name,
        f.weight_class, f.is_main_card, f.card_section, f.bout_order, f.is_title_fight,
        e.id  AS event_id,
        e.name AS event_name, e.date AS event_date,
        p.predicted_winner,
        p.f1_win_probability, p.f2_win_probability, p.confidence
    FROM mma_fights f
    JOIN mma_events e ON e.id = f.event_id
    LEFT JOIN mma_predictions p ON p.fight_id = f.id
    WHERE e.is_completed = FALSE
      AND e.date >= CURRENT_DATE - 1
      AND COALESCE(f.is_active, TRUE) = TRUE
      AND COALESCE(f.status, 'confirmed') = 'confirmed'
      AND p.predicted_winner IS NOT NULL
      AND p.f1_win_probability IS NOT NULL
      AND p.f2_win_probability IS NOT NULL
      AND COALESCE(p.model_version, 'catboost_v1') <> 'fallback_5050'
    ORDER BY
        e.date ASC,
        CASE COALESCE(f.card_section, CASE WHEN f.is_main_card THEN 'main_card' ELSE 'prelims' END)
            WHEN 'main_card' THEN 1
            WHEN 'prelims' THEN 2
            WHEN 'early_prelims' THEN 3
            ELSE 4
        END ASC,
        f.bout_order DESC NULLS LAST,
        f.id ASC
"""

# A bookmaker that stops quoting a fight leaves its last row in place, so only
# rows from the latest fetch (24 hours either way) are used.
ODDS_SQL = """
    WITH latest AS (SELECT MAX(fetched_at) AS ts FROM mma_fight_odds)
    SELECT o.event_key, o.fighter_1_name, o.fighter_2_name,
           o.commence_time, o.bookmaker, o.fighter_name, o.odds
    FROM mma_fight_odds o, latest
    WHERE latest.ts IS NOT NULL
      AND o.fetched_at >= latest.ts - INTERVAL '24 hours'
    ORDER BY o.fetched_at DESC
"""


def build_edge_bets(fight_rows, odds_rows):
    """Every fighter with a prediction and a price, with our chance and edge."""
    odds_by_fighter = {}
    for row in odds_rows:
        odds_by_fighter.setdefault(normalise_name(row.fighter_name), []).append(row)

    bets = []
    for fr in fight_rows:
        (fight_id, f1, f2, wc, is_main, card_section, bout_order, is_title,
         event_id, event_name, event_date,
         pred_winner, f1_prob, f2_prob, confidence) = fr
        if f1_prob is None or f2_prob is None:
            continue
        fight_odds_rows = []
        for norm_key, odds_list in odds_by_fighter.items():
            if not (names_match(f1, norm_key) or names_match(f2, norm_key)):
                continue
            fight_odds_rows.extend(o for o in odds_list
                                   if pairs_match(f1, f2, o.fighter_1_name, o.fighter_2_name))
        if not fight_odds_rows:
            continue

        # Same-book opposite price, used to take the bookmaker's margin out.
        best_by_book = {}
        for o in fight_odds_rows:
            key = (o.bookmaker, normalise_name(o.fighter_name))
            if key not in best_by_book or (o.odds or 0) > best_by_book[key]:
                best_by_book[key] = o.odds

        for fighter, prob, opponent, opp_prob in ((f1, float(f1_prob), f2, float(f2_prob)),
                                                  (f2, float(f2_prob), f1, float(f1_prob))):
            candidates = [o for o in fight_odds_rows if names_match(fighter, o.fighter_name)]
            if not candidates:
                continue
            best = max(candidates, key=lambda o: o.odds or 0)
            if not best.odds or best.odds <= 1.0:
                continue
            edge = calculate_mma_edge(
                model_prob=prob, odds=best.odds,
                opponent_odds=best_by_book.get((best.bookmaker, normalise_name(opponent))),
            )
            bets.append({
                'fight_id': fight_id,
                'event_id': event_id,
                'event_name': event_name,
                'event_date': event_date.isoformat() if event_date else None,
                'commence_time': best.commence_time.isoformat() if getattr(best, 'commence_time', None) else None,
                'fighter': fighter,
                'opponent': opponent,
                'weight_class': wc or '',
                'is_main_card': bool(is_main),
                'card_section': card_section or ('main_card' if is_main else 'prelims'),
                'bout_order': bout_order,
                'is_title_fight': bool(is_title),
                'predicted_winner': pred_winner,
                'confidence': confidence or '',
                'model_prob': edge['model_prob'],
                'raw_model_prob': edge['raw_model_prob'],
                'opponent_model_prob': round(opp_prob * 100.0, 1),
                'implied_prob': edge['implied_prob'],
                'odds': edge['odds'],
                'bookmaker': best.bookmaker or '',
                'devigged': edge['devigged'],
                'edge': edge['edge'],
                'edge_pct': edge['edge_pct'],
                'recommendation': edge['recommendation'],
                '_commence': getattr(best, 'commence_time', None),
            })
    bets.sort(key=lambda b: abs(b.get('edge', 0)), reverse=True)
    return bets


def load_edge_bets(conn):
    """(bets, total odds rows) from the database."""
    fights = conn.execute(text(FIGHTS_SQL)).fetchall()
    if not fights:
        return [], conn.execute(text("SELECT COUNT(*) FROM mma_fight_odds")).scalar() or 0
    odds = conn.execute(text(ODDS_SQL)).fetchall()
    total = conn.execute(text("SELECT COUNT(*) FROM mma_fight_odds")).scalar() or 0
    return build_edge_bets(fights, odds), total


def public(bet):
    """A bet without internal fields, ready for JSON."""
    return {k: v for k, v in bet.items() if not k.startswith('_')}


def _dialect(conn):
    """Works for a Connection and for a Flask-SQLAlchemy session."""
    dialect = getattr(conn, 'dialect', None)
    if dialect is None and hasattr(conn, 'get_bind'):
        dialect = conn.get_bind().dialect
    return getattr(dialect, 'name', '')


def ensure_table(conn):
    id_col = "INTEGER PRIMARY KEY AUTOINCREMENT" if _dialect(conn) == "sqlite" else "SERIAL PRIMARY KEY"
    conn.execute(text(f"""
        CREATE TABLE IF NOT EXISTS mma_tracked_edges (
            id {id_col},
            fight_id INTEGER NOT NULL,
            fighter_name VARCHAR(200) NOT NULL,
            opponent_name VARCHAR(200),
            event_name VARCHAR(300),
            event_date DATE,
            commence_time TIMESTAMP,
            odds FLOAT NOT NULL,
            bookmaker VARCHAR(200),
            our_prob FLOAT,
            model_prob FLOAT,
            implied_prob FLOAT,
            edge FLOAT,
            devigged BOOLEAN,
            captured_at TIMESTAMP NOT NULL,
            UNIQUE (fight_id, fighter_name)
        )
    """))


def _iso(value):
    if not value:
        return None
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)[:10]


def _naive_utc(value):
    if value is None:
        return None
    if getattr(value, 'tzinfo', None) is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def record_value_edges(conn, bets, now=None):
    """Save each value pick once, only while its fight is still to start."""
    now = _naive_utc(now) or datetime.utcnow()
    ensure_table(conn)
    saved = 0
    for bet in bets:
        if bet.get('recommendation') != 'value':
            continue
        start = _naive_utc(bet.get('_commence'))
        if start is not None:
            if start <= now:
                continue
        elif not bet.get('event_date') or bet['event_date'] <= now.date().isoformat():
            continue        # no start time: only fights on a later day than today
        result = conn.execute(text("""
            INSERT INTO mma_tracked_edges
                (fight_id, fighter_name, opponent_name, event_name, event_date, commence_time,
                 odds, bookmaker, our_prob, model_prob, implied_prob, edge, devigged, captured_at)
            VALUES (:fight_id, :fighter, :opponent, :event_name, :event_date, :commence,
                    :odds, :bookmaker, :our_prob, :model_prob, :implied_prob, :edge, :devigged, :now)
            ON CONFLICT (fight_id, fighter_name) DO NOTHING
        """), {
            'fight_id': bet['fight_id'], 'fighter': bet['fighter'], 'opponent': bet['opponent'],
            'event_name': bet.get('event_name'), 'event_date': bet.get('event_date'), 'commence': start,
            'odds': bet['odds'], 'bookmaker': bet.get('bookmaker'), 'our_prob': bet.get('model_prob'),
            'model_prob': bet.get('raw_model_prob'), 'implied_prob': bet.get('implied_prob'),
            'edge': bet.get('edge'), 'devigged': bool(bet.get('devigged')), 'now': now,
        })
        saved += result.rowcount or 0
    return saved


# Edge Finder picks are labelled "Tracking" until this many have settled with
# a profit; only then are they called "Value".
PROVEN_AFTER_BETS = 100


def value_proven(record):
    return (record.get('bets') or 0) >= PROVEN_AFTER_BETS and (record.get('profit_units') or 0) > 0


def edge_record(conn, recent=40):
    """Settled and pending Edge Finder value picks."""
    ensure_table(conn)
    rows = conn.execute(text("""
        SELECT t.fight_id, t.fighter_name, t.opponent_name, t.event_name, t.event_date, t.odds,
               t.bookmaker, t.our_prob, t.model_prob, t.implied_prob, t.edge, t.captured_at,
               e.is_completed, f.winner_name, COALESCE(f.is_active, TRUE) AS is_active
        FROM mma_tracked_edges t
        JOIN mma_fights f ON f.id = t.fight_id
        JOIN mma_events e ON e.id = f.event_id
        ORDER BY t.event_date DESC, t.id DESC
    """)).fetchall()
    settled, pending, void = [], 0, 0
    items = []
    for r in rows:
        if r.is_completed and r.winner_name:
            won = names_match(r.fighter_name, r.winner_name)
            profit = (float(r.odds) - 1.0) if won else -1.0
            status = 'won' if won else 'lost'
            settled.append((won, profit, float(r.odds)))
        elif r.is_completed or not r.is_active:
            status, profit = 'void', 0.0       # no contest, draw or fight cancelled
            void += 1
        else:
            status, profit = 'pending', None
            pending += 1
        items.append({
            'event_name': r.event_name, 'event_date': _iso(r.event_date),
            'fighter': r.fighter_name, 'opponent': r.opponent_name, 'odds': r.odds, 'bookmaker': r.bookmaker,
            'our_prob': r.our_prob, 'model_prob': r.model_prob, 'implied_prob': r.implied_prob,
            'edge': r.edge, 'status': status, 'profit': profit, 'winner': r.winner_name,
        })
    bets = len(settled)
    wins = sum(1 for won, _, _ in settled if won)
    profit = sum(p for _, p, _ in settled)
    return {
        'bets': bets, 'wins': wins, 'pending': pending, 'void': void,
        'strike_rate': round(100.0 * wins / bets, 1) if bets else None,
        'profit_units': round(profit, 2),
        'roi_pct': round(100.0 * profit / bets, 1) if bets else None,
        'avg_odds': round(sum(o for _, _, o in settled) / bets, 2) if bets else None,
        'recent': items[:recent],
    }


MODEL_RECORD_SQL = """
    SELECT e.date, e.name, f.fighter_1_name, f.fighter_2_name, f.winner_name,
           p.predicted_winner, p.f1_win_probability
    FROM mma_fights f
    JOIN mma_events e ON e.id = f.event_id
    JOIN mma_predictions p ON p.fight_id = f.id
    WHERE e.is_completed = TRUE AND f.winner_name IS NOT NULL
      AND p.f1_win_probability IS NOT NULL
      AND COALESCE(p.model_version, 'catboost_v1') <> 'fallback_5050'
      AND p.generated_at IS NOT NULL AND p.generated_at < e.date
    ORDER BY e.date DESC, f.id DESC
"""

ODDS_FOR_FIGHT_SQL = """
    SELECT LOWER(fighter_name) AS fighter, commence_time, AVG(odds) AS odds
    FROM mma_fight_odds WHERE odds > 1
    GROUP BY LOWER(fighter_name), commence_time
"""

BANDS = ((0.50, 0.55, '50-55%'), (0.55, 0.65, '55-65%'), (0.65, 0.75, '65-75%'), (0.75, 1.01, '75%+'))


def model_record(conn, recent=40):
    """The ML predictor on finished fights, predicted before the event day.

    Where both fighters have a price from that day (one day either side), the
    bookies' favourite is shown next to the model's pick.
    """
    rows = conn.execute(text(MODEL_RECORD_SQL)).fetchall()
    prices = {}
    for r in conn.execute(text(ODDS_FOR_FIGHT_SQL)).fetchall():
        if r.commence_time is None:
            continue
        prices.setdefault(r.fighter, []).append((r.commence_time.date(), float(r.odds)))

    def price(name, day):
        best = None
        for d, odds in prices.get((name or '').lower(), []):
            if abs((d - day).days) <= 1:
                best = odds
        return best

    fights = right = 0
    ll_model = 0.0
    with_odds = fav_right = model_right_odds = 0
    ll_model_odds = ll_market = 0.0
    bands = {label: [0, 0, 0.0] for _, _, label in BANDS}
    items = []
    for r in rows:
        p1 = float(r.f1_win_probability)
        f1_won = r.winner_name == r.fighter_1_name
        if not f1_won and r.winner_name != r.fighter_2_name:
            continue
        correct = r.predicted_winner == r.winner_name
        p_pick = max(p1, 1 - p1)
        p_winner = p1 if f1_won else 1 - p1
        fights += 1
        right += int(correct)
        ll_model -= math.log(max(p_winner, 0.001))
        for lo, hi, label in BANDS:
            if lo <= p_pick < hi:
                bands[label][0] += 1
                bands[label][1] += int(correct)
                bands[label][2] += p_pick
        o1, o2 = price(r.fighter_1_name, r.date), price(r.fighter_2_name, r.date)
        favourite = None
        if o1 and o2:
            m1 = (1 / o1) / (1 / o1 + 1 / o2)
            favourite = r.fighter_1_name if m1 >= 0.5 else r.fighter_2_name
            with_odds += 1
            fav_right += int(favourite == r.winner_name)
            model_right_odds += int(correct)
            ll_model_odds -= math.log(max(p_winner, 0.001))
            ll_market -= math.log(max(m1 if f1_won else 1 - m1, 0.001))
        if len(items) < recent:
            items.append({
                'event_date': r.date.isoformat() if r.date else None, 'event_name': r.name,
                'fight': f"{r.fighter_1_name} vs {r.fighter_2_name}", 'pick': r.predicted_winner,
                'pick_prob': round(100.0 * p_pick, 1), 'winner': r.winner_name, 'correct': correct,
                'favourite': favourite,
            })
    return {
        'fights': fights,
        'pick_won_pct': round(100.0 * right / fights, 1) if fights else None,
        'log_loss': round(ll_model / fights, 3) if fights else None,
        'with_odds': {
            'fights': with_odds,
            'model_pick_won_pct': round(100.0 * model_right_odds / with_odds, 1) if with_odds else None,
            'favourite_won_pct': round(100.0 * fav_right / with_odds, 1) if with_odds else None,
            'model_log_loss': round(ll_model_odds / with_odds, 3) if with_odds else None,
            'market_log_loss': round(ll_market / with_odds, 3) if with_odds else None,
        },
        'by_confidence': [
            {'band': label, 'fights': n, 'won_pct': round(100.0 * w / n, 1) if n else None,
             'model_said_pct': round(100.0 * s / n, 1) if n else None}
            for label, (n, w, s) in bands.items()
        ],
        'recent': items,
    }
