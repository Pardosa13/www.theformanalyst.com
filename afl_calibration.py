"""Correct the AFL prop model's overconfidence using its own settled picks.

The prop model's chances run too far from 50%: when it said 70%+ for an Over,
those won 42% (Jul 2026, 17,004 settled picks). For each side (Over/Under) a
two-number correction is fitted on settled picks,

    corrected = 1 / (1 + exp(-(a + b * logit(model chance))))

and applied before the edge is worked out. Fitted on the first 60% of picks
and judged on the rest, corrected Overs that beat the price by 10%+ made +8%
on 326 bets (the raw model's Overs lost 31%). A side needs MIN_PICKS settled
picks before it is corrected; until then its chances are used as they are.

afl_model_selections.model_prob always stores the RAW chance, so every refit
learns from the model's own output, never from an earlier correction.
"""
import logging
import math
import time

log = logging.getLogger(__name__)

MIN_PICKS = 300
# Picks priced by the current maths (negative binomial, 10 Nov 2026). Older
# picks came from the bell-curve method, whose overconfidence this correction
# was first fitted to; the new maths matched reality on 2019-2026 games, so no
# correction applies until MIN_PICKS of its own picks have settled.
FIT_FROM = "2026-11-10"
REFIT_SECONDS = 6 * 3600
_cache = {"at": 0.0, "coefficients": {}}


def _logit(p):
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def fit(probabilities, outcomes, iterations=50):
    """(a, b) for corrected = sigmoid(a + b * logit(p)), by Newton's method."""
    xs = [_logit(p) for p in probabilities]
    ys = [float(y) for y in outcomes]
    a, b = 0.0, 1.0
    ridge = 1e-3
    for _ in range(iterations):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            p = 1.0 / (1.0 + math.exp(-(a + b * x)))
            w = p * (1 - p)
            ga += p - y
            gb += (p - y) * x
            haa += w
            hab += w * x
            hbb += w * x * x
        gb += ridge * (b - 1.0)
        hbb += ridge
        det = haa * hbb - hab * hab
        if det <= 1e-12:
            break
        da = (hbb * ga - hab * gb) / det
        db = (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break
    return a, b


def apply(probability, coefficients):
    """Corrected chance (0-1), or the chance unchanged when there is no fit."""
    if not coefficients:
        return probability
    a, b = coefficients
    return 1.0 / (1.0 + math.exp(-(a + b * _logit(probability))))


def coefficients(db, line_type, now=None):
    """Correction for one side, refitted from settled picks every few hours."""
    now = time.time() if now is None else now
    if now - _cache["at"] > REFIT_SECONDS:
        _cache["coefficients"] = _refit(db)
        _cache["at"] = now
    return _cache["coefficients"].get(str(line_type or ""))


def _refit(db):
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(db.text("""
                SELECT line_type, model_prob, (result = 'win')::int AS won
                FROM afl_model_selections
                WHERE settled_at IS NOT NULL AND result IN ('win', 'loss')
                  AND model_prob IS NOT NULL AND odds > 1
                  AND created_at >= :since
            """), {"since": FIT_FROM}).fetchall()
    except Exception as exc:
        log.warning("AFL calibration refit failed; chances used uncorrected: %s", exc)
        return {}
    by_side = {}
    for line_type, model_prob, won in rows:
        by_side.setdefault(str(line_type or ""), ([], []))
        by_side[str(line_type or "")][0].append(float(model_prob) / 100.0)
        by_side[str(line_type or "")][1].append(int(won))
    out = {}
    for side, (probs, wins) in by_side.items():
        if len(probs) < MIN_PICKS:
            continue
        out[side] = fit(probs, wins)
        log.info("AFL calibration side=%s picks=%s a=%.3f b=%.3f", side, len(probs), *out[side])
    return out
