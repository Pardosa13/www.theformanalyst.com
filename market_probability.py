"""Market-implied win probabilities, corrected for the favourite-longshot bias.

WHY THIS EXISTS
---------------
`1/SP` is not a win probability. Two separate distortions sit between a
starting price and the probability the market actually believes:

1. **Overround.** A bookmaker's book sums to more than 1.0 (typically
   1.10-1.25 on Australian thoroughbred racing). Every `1/SP` is inflated by
   roughly that margin.
2. **Favourite-longshot bias (FLB).** The margin is not spread evenly. Bettors
   systematically overbet longshots and underbet favourites, so a bookmaker
   loads far more of the margin onto the long prices. Dividing the whole book
   by its sum ("naive normalisation") removes the *average* margin but leaves
   the bias: it still overstates the longshots and understates the favourites.

Shin (1992, 1993) models this as a market maker protecting itself against a
proportion `z` of insider money. Given the raw reciprocals it recovers both the
insider proportion and the underlying (bias-free) probabilities in one step, so
a single per-race solve fixes both distortions at once.

WHERE IT MATTERS
----------------
Anywhere a market price is being read as a probability:

* The A/E ratio (actual winners vs winners the market expected) in
  backtest.evaluate_model_on_validation. Dividing by raw `1/SP` measures the
  model against an inflated, biased benchmark — A/E comes out systematically
  low, and by an amount that varies with the field's price shape.
* Blending the model's own opinion with the market's
  (`blend_probabilities` below). Blending in a biased market probability
  imports the bias into the blend.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not touch the *payoff* odds. Staking maths (model_classes.solve_joint
_kelly) is settled at the price actually offered, so `SP` stays `SP` wherever
it is a payoff — only its reading as a probability is corrected here.

DEFENSIVE STYLE
---------------
Same posture as book_quality.unusable_race_ids: a race whose prices cannot
support the solve is not an error, it is a race that falls back to naive
normalisation with a warning. Missing/scratched/nonsense prices are dropped
from the solve and returned as None rather than being guessed at.
"""
import logging

import numpy as np

log = logging.getLogger(__name__)

# Shin's solver is a fixed-point iteration on z. It converges in a handful of
# steps on any real book; these bounds only exist so a pathological input
# cannot spin.
SHIN_MAX_ITERATIONS = 100
SHIN_CONVERGENCE_TOL = 1e-10

# z is the share of money Shin's model attributes to insiders. Published
# estimates for bookmaker and pari-mutuel racing markets sit at roughly
# 0.01-0.05. Outside this band the number is reported (not rejected) — a real
# book can legitimately sit outside it, but a run whose average z is pinned to
# a boundary usually means the SP data feeding it is wrong.
PLAUSIBLE_Z_RANGE = (0.01, 0.05)

# A price at or below evens-on-the-whole-field is not a price. Matches the
# `odds > 1.0` guard the Kelly solver already applies.
MIN_USABLE_ODDS = 1.0001


def _usable_odds(sp_list):
    """Split raw SPs into (index, odds) pairs that can be priced, and the rest.

    Scratched runners, missing prices, non-numeric junk and prices at or below
    1.0 are all "not priced" — the same condition, so they take the same path:
    excluded from the solve, returned as None.
    """
    usable = []
    for index, value in enumerate(sp_list or []):
        try:
            odds = float(value)
        except (TypeError, ValueError):
            continue
        if not np.isfinite(odds) or odds < MIN_USABLE_ODDS:
            continue
        usable.append((index, odds))
    return usable


def shin_z(raw_probs, max_iterations=SHIN_MAX_ITERATIONS, tol=SHIN_CONVERGENCE_TOL):
    """Insider proportion `z` for one race's raw `1/SP` reciprocals.

    Solves Shin's normalisation condition

        sum_i ( sqrt(z^2 + 4*(1-z) * pi_i^2 / B) - z ) / (2*(1-z))  =  1

    where `pi_i = 1/SP_i` and `B = sum_i pi_i` (the book). The left-hand side
    is monotone decreasing in z over [0, 1) — at z = 0 it equals B (which is
    >= 1 for any real book, i.e. the overround) and it falls to 1 as the
    insider share absorbs the margin — so a plain bisection is both the
    simplest solve and the one that cannot diverge.

    Returns None when there is nothing to solve: fewer than two prices, or a
    book that already sums to 1.0 or below (an underround, where Shin's model
    has no margin to attribute to anyone).
    """
    raw = np.asarray(raw_probs, dtype=float)
    raw = raw[np.isfinite(raw) & (raw > 0)]
    if raw.size < 2:
        return None
    book = float(raw.sum())
    if book <= 1.0 + tol:
        return None

    def implied_sum(z):
        # At z -> 1 the expression below is 0/0; the limit is the book itself,
        # and the bisection never evaluates the endpoint, so clamping is enough.
        if z >= 1.0 - 1e-12:
            return 1.0
        inner = np.sqrt((z * z) + (4.0 * (1.0 - z) * (raw * raw) / book))
        return float(np.sum((inner - z) / (2.0 * (1.0 - z))))

    low, high = 0.0, 1.0 - 1e-9
    # Sanity: the root must be bracketed. implied_sum(0) == book > 1 and
    # implied_sum(~1) == 1, so it always is for a real overround book; bail
    # rather than return a bisection artefact if that ever stops holding.
    if implied_sum(low) < 1.0:
        return None
    for _ in range(max_iterations):
        mid = 0.5 * (low + high)
        value = implied_sum(mid)
        if abs(value - 1.0) < tol:
            return float(mid)
        if value > 1.0:
            low = mid
        else:
            high = mid
        if (high - low) < tol:
            break
    candidate = 0.5 * (low + high)
    # Accept the bisection's best effort only if it actually normalises. A
    # book so extreme that 100 halvings still leave the sum off is the
    # "solver did not converge" case the caller falls back on.
    if abs(implied_sum(candidate) - 1.0) > 1e-6:
        return None
    return float(candidate)


def fair_probabilities(sp_list, race_id=None, return_z=False):
    """FLB-corrected win probabilities for one race's starting prices.

    sp_list: decimal odds per runner, in field order. Scratched/missing/
    unusable entries may be None or any non-price value.

    Returns a list the same length as `sp_list`: a probability for every
    priced runner and None wherever the price was unusable. The probabilities
    of the priced runners sum to 1.0.

    With return_z=True, returns `(probabilities, z)` where z is the solved
    insider proportion, or None when the race fell back to naive
    normalisation. Callers that aggregate z across a run (the nightly
    validation log) use this; everything else takes the plain list.

    Fallbacks, in order:
      * a single priced runner        -> probability 1.0 (nothing to correct)
      * an underround/degenerate book -> naive normalisation, no warning
        (there is no margin for Shin to attribute, so the two agree)
      * a solve that does not converge -> naive normalisation, with a warning
    """
    usable = _usable_odds(sp_list)
    out = [None] * len(sp_list or [])
    if not usable:
        return (out, None) if return_z else out

    indices = [index for index, _ in usable]
    raw = np.array([1.0 / odds for _, odds in usable], dtype=float)

    if len(usable) == 1:
        out[indices[0]] = 1.0
        return (out, None) if return_z else out

    book = float(raw.sum())
    naive = raw / book

    z = shin_z(raw)
    if z is None:
        # Only an overround book that failed to converge is worth a warning:
        # an underround (book <= 1.0) has no margin to decompose and naive
        # normalisation is the right answer there, not a degraded one.
        if book > 1.0:
            log.warning(
                "Shin solve did not converge for race %s (book=%.4f, runners=%s); "
                "falling back to naive 1/SP normalisation.",
                race_id if race_id is not None else "<unknown>", book, len(usable),
            )
        for index, probability in zip(indices, naive):
            out[index] = float(probability)
        return (out, None) if return_z else out

    inner = np.sqrt((z * z) + (4.0 * (1.0 - z) * (raw * raw) / book))
    fair = (inner - z) / (2.0 * (1.0 - z))
    total = float(fair.sum())
    if not np.isfinite(total) or total <= 0:
        log.warning(
            "Shin solve produced a degenerate book for race %s (z=%.6f, sum=%.6f); "
            "falling back to naive 1/SP normalisation.",
            race_id if race_id is not None else "<unknown>", z, total,
        )
        for index, probability in zip(indices, naive):
            out[index] = float(probability)
        return (out, None) if return_z else out
    # The solve targets a sum of 1.0 to within SHIN_CONVERGENCE_TOL; divide
    # through anyway so callers can rely on an exact normalisation.
    fair = fair / total
    for index, probability in zip(indices, fair):
        out[index] = float(probability)
    return (out, float(z)) if return_z else out


def summarise_z(z_values, label="", warn=True):
    """Aggregate solved z values across a run and report whether they look sane.

    A `z` near 0 on most races with occasional spikes is the normal shape. A
    run whose average z is pinned at ~0 everywhere is the signature of SP data
    that is not really a book (partial coverage, previous-start prices, model
    prices) rather than of an unusually honest market, so it is worth a log
    line at the point of measurement instead of being noticed months later.

    Returns a dict safe to store alongside a run's metrics; never raises.
    """
    values = [float(z) for z in (z_values or []) if z is not None and np.isfinite(z)]
    summary = {
        'races_solved': len(values),
        'mean_z': float(np.mean(values)) if values else None,
        'median_z': float(np.median(values)) if values else None,
        'min_z': float(np.min(values)) if values else None,
        'max_z': float(np.max(values)) if values else None,
        'plausible_range': list(PLAUSIBLE_Z_RANGE),
    }
    if not values:
        return summary
    mean_z = summary['mean_z']
    summary['mean_z_in_plausible_range'] = bool(
        PLAUSIBLE_Z_RANGE[0] <= mean_z <= PLAUSIBLE_Z_RANGE[1]
    )
    if warn and not summary['mean_z_in_plausible_range']:
        log.warning(
            "Shin insider proportion looks implausible%s: mean_z=%.5f median_z=%.5f "
            "over %s solved races (expected roughly %.2f-%.2f). A mean pinned near "
            "zero usually means the prices being fed in are not a complete book.",
            f" ({label})" if label else "", mean_z, summary['median_z'],
            len(values), PLAUSIBLE_Z_RANGE[0], PLAUSIBLE_Z_RANGE[1],
        )
    return summary


# ─────────────────────────────────────────────
# MODEL / MARKET BLEND
# ─────────────────────────────────────────────
# Benter's result: a model built from form data and the market's own price are
# not two rival opinions to choose between, they are two estimates to combine.
# The combination that works is linear in log-space (a logit/geometric blend),
# renormalised within the race:
#
#     log p_blend_i = alpha * log p_model_i + (1 - alpha) * log p_market_i
#
# alpha = 1.0 ignores the market entirely (pure model), alpha = 0.0 ignores the
# model entirely (pure market). Anything between is a genuine combination, and
# which value wins is an empirical question answered out-of-sample by the grid
# search in backtest.run_model_competition — not a constant to be guessed here.

# Probabilities are clipped before the log so a zero (or a 1.0) from a model
# cannot send the blend to -inf and take the whole race's renormalisation with
# it. Matches the 1e-6 clip evaluate_model_on_validation already applies.
BLEND_PROB_FLOOR = 1e-6


def blend_probabilities(model_probs, market_probs, alpha):
    """Geometric (log-space) blend of model and market probabilities.

    Both inputs are per-runner sequences for ONE race, in the same order.
    `market_probs` entries may be None (an unpriced runner) — those runners
    keep their model probability untouched, so a partially-priced race still
    produces a full set of probabilities rather than dropping runners.

    The result is renormalised to sum to 1.0 across the runners that have a
    usable model probability, which is what every downstream consumer
    (evaluate_model_on_validation's per-race argmax, Kelly staking) expects.

    alpha outside [0, 1] is clamped: the grid search only ever passes values
    in range, but a corrupted stored alpha on a model artifact must degrade to
    "pure model" rather than produce an extrapolated blend nobody validated.
    """
    try:
        alpha = float(alpha)
    except (TypeError, ValueError):
        alpha = 1.0
    if not np.isfinite(alpha):
        alpha = 1.0
    alpha = min(1.0, max(0.0, alpha))

    model = np.asarray(
        [np.nan if p is None else p for p in (model_probs or [])], dtype=float
    )
    if model.size == 0:
        return []
    market_list = list(market_probs or [])
    market_list += [None] * (model.size - len(market_list))
    market = np.asarray(
        [np.nan if p is None else p for p in market_list[:model.size]], dtype=float
    )

    model_valid = np.isfinite(model) & (model > 0)
    if not model_valid.any():
        return [None if not ok else float(model[i]) for i, ok in enumerate(model_valid)]

    log_model = np.full(model.size, np.nan)
    log_model[model_valid] = np.log(np.clip(model[model_valid], BLEND_PROB_FLOOR, 1.0))

    # A runner the market did not price gets alpha = 1.0 for itself: its model
    # probability passes through, while the priced runners in the same race
    # still blend. Mixing the two in one race is fine — the renormalisation
    # below puts them back on a common scale.
    blend_mask = model_valid & np.isfinite(market) & (market > 0)
    log_blend = np.array(log_model, copy=True)
    if blend_mask.any():
        log_market = np.log(np.clip(market[blend_mask], BLEND_PROB_FLOOR, 1.0))
        log_blend[blend_mask] = (alpha * log_model[blend_mask]) + ((1.0 - alpha) * log_market)

    # Subtract the max before exponentiating (standard log-sum-exp shift): the
    # blend's logs are unnormalised, and on a large field they can sit low
    # enough that a plain exp underflows to zero for every runner.
    finite = np.isfinite(log_blend)
    shifted = np.zeros(model.size)
    shifted[finite] = np.exp(log_blend[finite] - np.max(log_blend[finite]))
    total = float(shifted[finite].sum())
    if total <= 0 or not np.isfinite(total):
        # Nothing survived the exponentiation — return the model's own
        # probabilities rather than a row of zeros the argmax cannot rank.
        return [float(model[i]) if model_valid[i] else None for i in range(model.size)]

    out = []
    for i in range(model.size):
        out.append(float(shifted[i] / total) if finite[i] else None)
    return out


def blend_probabilities_by_race(model_probs, market_probs, race_ids, alpha):
    """`blend_probabilities` applied race by race over flat, row-aligned arrays.

    The renormalisation in `blend_probabilities` is only meaningful within a
    race, so a flat validation frame has to be split before blending and
    stitched back afterwards. Doing that in one place keeps every caller
    (the alpha grid search, the blended Track E candidates, live scoring) on
    the same grouping rather than each re-deriving it.

    Returns a numpy array of the same length as the inputs. Rows the blend
    could not produce a probability for keep their original model probability,
    so the array is always fully populated and safe to hand to the existing
    evaluation code.
    """
    model = np.asarray(model_probs, dtype=float)
    out = np.array(model, copy=True)
    if model.size == 0:
        return out
    market_list = list(market_probs)
    race_list = list(race_ids)

    by_race = {}
    for position, race_id in enumerate(race_list):
        by_race.setdefault(race_id, []).append(position)

    for positions in by_race.values():
        blended = blend_probabilities(
            [model[i] for i in positions],
            [market_list[i] for i in positions],
            alpha,
        )
        for position, probability in zip(positions, blended):
            if probability is not None and np.isfinite(probability):
                out[position] = probability
    return out


def fair_probabilities_by_race(sp_values, race_ids):
    """`fair_probabilities` applied race by race over flat, row-aligned arrays.

    Returns `(probabilities, z_values)`: a list of per-row FLB-corrected market
    probabilities (None where the row had no usable price) and the list of
    solved z values, one per race that solved, for summarise_z.
    """
    sp_list = list(sp_values)
    race_list = list(race_ids)
    out = [None] * len(sp_list)
    z_values = []

    by_race = {}
    for position, race_id in enumerate(race_list):
        by_race.setdefault(race_id, []).append(position)

    for race_id, positions in by_race.items():
        probabilities, z = fair_probabilities(
            [sp_list[i] for i in positions], race_id=race_id, return_z=True
        )
        if z is not None:
            z_values.append(z)
        for position, probability in zip(positions, probabilities):
            out[position] = probability
    return out, z_values
