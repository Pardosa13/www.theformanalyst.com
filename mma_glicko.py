"""Keep UFC fighter ratings (Glicko-2) up to date after each event.

The ratings on mma_fighters came once from the seed import and were never
updated, so every fight since then was missing from them. update_ratings()
applies each completed fight after a fighter's last update, in date order,
one fight per rating period. glicko_updated_at records the last fight
counted, so a fight is never counted twice.

Fights on or before the seed cutoff are already inside the seeded ratings
and are skipped for fighters with no glicko_updated_at yet.
"""
import logging
import math
import os
from datetime import date, datetime

log = logging.getLogger("mma_glicko")

SCALE = 173.7178
TAU = 0.5
DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOL = 1500.0, 350.0, 0.06
MAX_RD = 350.0


def _g(phi):
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi ** 2))


def update(rating, rd, vol, opp_rating, opp_rd, score, tau=TAU):
    """One Glicko-2 rating period with a single opponent.

    score: 1 win, 0 loss, 0.5 draw. Returns (rating, rd, vol).
    """
    mu, phi = (rating - DEFAULT_RATING) / SCALE, rd / SCALE
    mu_j, phi_j = (opp_rating - DEFAULT_RATING) / SCALE, opp_rd / SCALE
    g = _g(phi_j)
    e = 1.0 / (1.0 + math.exp(-g * (mu - mu_j)))
    v = 1.0 / (g * g * e * (1.0 - e))
    delta = v * g * (score - e)

    # New volatility (Illinois method, Glickman's step 5).
    a = math.log(vol * vol)

    def f(x):
        ex = math.exp(x)
        return (ex * (delta * delta - phi * phi - v - ex) / (2.0 * (phi * phi + v + ex) ** 2)
                - (x - a) / (tau * tau))

    big_a = a
    if delta * delta > phi * phi + v:
        big_b = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        big_b = a - k * tau
    fa, fb = f(big_a), f(big_b)
    for _ in range(100):
        if abs(big_b - big_a) <= 1e-6:
            break
        c = big_a + (big_a - big_b) * fa / (fb - fa)
        fc = f(c)
        if fc * fb <= 0:
            big_a, fa = big_b, fb
        else:
            fa /= 2.0
        big_b, fb = c, fc
    new_vol = math.exp(big_a / 2.0)

    phi_star = math.sqrt(phi * phi + new_vol * new_vol)
    new_phi = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    new_mu = mu + new_phi * new_phi * g * (score - e)
    return (DEFAULT_RATING + SCALE * new_mu, min(SCALE * new_phi, MAX_RD), new_vol)


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def plan_updates(fights, ratings, seed_cutoff):
    """Work out new ratings without touching the database.

    fights: (fight_date, fid1, fid2, score_for_fid1) in date order.
    ratings: {fid: (rating, rd, vol, updated_at)}.
    Returns {fid: (rating, rd, vol, last_fight_date)} for fighters that changed.
    """
    seed_cutoff = _as_date(seed_cutoff)
    state = {fid: [float(r if r is not None else DEFAULT_RATING),
                   float(rd if rd is not None else DEFAULT_RD),
                   float(v if v is not None else DEFAULT_VOL), _as_date(at)]
             for fid, (r, rd, v, at) in ratings.items()}
    changed = {}
    for fight_date, fid1, fid2, score1 in fights:
        fight_date = _as_date(fight_date)
        if fight_date is None or not fid1 or not fid2 or fid1 == fid2:
            continue
        s1 = state.setdefault(fid1, [DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOL, None])
        s2 = state.setdefault(fid2, [DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOL, None])

        def due(s):
            last = s[3] or seed_cutoff
            return last is None or fight_date > last
        due1, due2 = due(s1), due(s2)
        if not (due1 or due2):
            continue
        new1 = update(s1[0], s1[1], s1[2], s2[0], s2[1], score1)
        new2 = update(s2[0], s2[1], s2[2], s1[0], s1[1], 1.0 - score1)
        for fid, s, new, is_due in ((fid1, s1, new1, due1), (fid2, s2, new2, due2)):
            if is_due:
                s[0], s[1], s[2], s[3] = new[0], new[1], new[2], fight_date
                changed[fid] = (s[0], s[1], s[2], fight_date)
    return changed


FIGHTS_SQL = """
    SELECT e.date, f.fighter_1_id, f.fighter_2_id, f.fighter_1_name, f.fighter_2_name, f.winner_name
    FROM mma_fights f
    JOIN mma_events e ON e.id = f.event_id
    WHERE e.is_completed = TRUE AND e.date <= CURRENT_DATE
      AND f.winner_name IS NOT NULL AND COALESCE(f.is_active, TRUE)
      AND (%s IS NULL OR e.date > %s)
    ORDER BY e.date, f.id
"""


def update_ratings(conn, name_to_id=None, normalize=None, seed_cutoff=None):
    """Apply completed fights to mma_fighters ratings. Returns fighters updated.

    seed_cutoff: last date already inside the seeded ratings. Defaults to
    MMA_GLICKO_SEED_CUTOFF (YYYY-MM-DD), else the day the seed created the
    fighters (earliest mma_fighters.created_at).
    """
    seed_cutoff = _as_date(seed_cutoff or os.environ.get("MMA_GLICKO_SEED_CUTOFF"))
    if seed_cutoff is None:
        # The seed import created every fighter (April 2026) from a ratings file
        # made before that day, so no fight after the earliest created_at can
        # already be inside a seeded rating.
        with conn.cursor() as cur:
            cur.execute("SELECT MIN(created_at) FROM mma_fighters")
            row = cur.fetchone()
        seed_cutoff = _as_date(row[0] if row else None)
    if seed_cutoff is None:
        log.warning("GLICKO_UPDATE skipped: no seed date found")
        return 0
    name_to_id = name_to_id or {}
    normalize = normalize or (lambda n: str(n or "").strip().lower())
    with conn.cursor() as cur:
        cur.execute("SELECT id, glicko_rating, glicko_rd, glicko_vol, glicko_updated_at FROM mma_fighters")
        ratings = {r[0]: (r[1], r[2], r[3], r[4]) for r in cur.fetchall()}
        cur.execute(FIGHTS_SQL, (seed_cutoff, seed_cutoff))
        rows = cur.fetchall()
    fights = []
    for fight_date, fid1, fid2, n1, n2, winner in rows:
        fid1 = fid1 or name_to_id.get(normalize(n1))
        fid2 = fid2 or name_to_id.get(normalize(n2))
        if winner == n1:
            score = 1.0
        elif winner == n2:
            score = 0.0
        else:
            continue
        fights.append((fight_date, fid1, fid2, score))
    changed = plan_updates(fights, ratings, seed_cutoff)
    if changed:
        with conn.cursor() as cur:
            for fid, (rating, rd, vol, last) in changed.items():
                cur.execute(
                    "UPDATE mma_fighters SET glicko_rating=%s, glicko_rd=%s, glicko_vol=%s, glicko_updated_at=%s "
                    "WHERE id=%s", (rating, rd, vol, last, fid))
        conn.commit()
    log.info("GLICKO_UPDATE seed_cutoff=%s fights_after_cutoff=%s fighters_updated=%s",
             seed_cutoff, len(fights), len(changed))
    return len(changed)
