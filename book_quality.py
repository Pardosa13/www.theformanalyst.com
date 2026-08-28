"""Race book quality: is a race's starting-price data complete enough to learn from?

WHY THIS EXISTS
---------------
1,489 races (185 manually-uploaded meetings, Nov 2025 - Feb 2026) carry starting
prices for only about 40% of their runners. The missingness is not random — it is
determined by the outcome:

    finish_position   with SP / total   priced
    1 won             1381 / 1381       100.0%
    2 second          1368 / 1368       100.0%
    3 third           1359 / 1359       100.0%
    4 fourth          1334 / 1334       100.0%
    5 unplaced         100 / 8024         1.2%

In those races, having a price is very nearly the same fact as having finished in
the top four. build_training_set drops rows with no usable SP, so what survives is
the placegetters — and a model "betting" into that field cannot lose. This is what
produced the pre-audit +30-42% validation ROI, and a favourite-backing strategy
with no model in it returns +31.95% over the same races.

Those prices were never captured, so there is nothing to backfill from. csv_data's
'form price' is the previous start's price, and the PuntingForm ratings_json prices
are model predictions, not the market. Either would fabricate a market rather than
restore one. The only honest option is to keep these races out of training.

THE GATE
--------
Price coverage — the share of non-scratched runners carrying a usable SP — is the
direct measure of the defect, and it separates the two populations almost perfectly.
Measured over all 8,932 races with results:

    coverage gate    good races rejected    broken races caught
    >= 0.70              0 (0.00%)              1356 (95.6%)
    >= 0.85              0 (0.00%)              1383 (97.5%)
    >= 0.90              2 (0.03%)              1383 (97.5%)

Healthy races sit at coverage 1.000 all the way down to the 0.1st percentile, so
0.85 costs nothing and catches almost everything. 0.90 and above only start
discarding good races for no extra benefit.

A note on the overround threshold this replaced: rejecting races whose book sums
below 1.02 sounds equivalent but measures worse on both sides — it rejects 790
healthy races (10.5%) while catching only 88.6% of the broken ones, because a
small healthy field can legitimately sum near 1.0 while a broken book of three
short-priced placegetters can clear 1.02. Overround is still computed and reported
(a sub-1.0 book is worth knowing about), but coverage decides.
"""

# Share of non-scratched runners that must carry a usable starting price.
MIN_PRICE_COVERAGE = 0.85

# A complete book sums above 1.0 — that margin is the bookmaker's. Below this a
# race is reported as suspicious, but coverage is what decides usability.
SUSPICIOUS_OVERROUND = 1.02

# A book needs at least this many priced runners before its overround means
# anything at all.
MIN_PRICED_FOR_OVERROUND = 2

# finish_position 0 means scratched: no starting price is expected for those.
SCRATCHED_FINISH_POSITION = 0


def usable_sp(sp):
    """A starting price we can compute a return from. Anything at or below 1.0
    pays nothing and is treated as absent."""
    if sp is None:
        return None
    try:
        value = float(sp)
    except (TypeError, ValueError):
        return None
    if value != value or value in (float('inf'), float('-inf')):  # NaN / inf
        return None
    return value if value > 1.0 else None


def race_book_quality(runners):
    """Assess one race's book.

    runners: iterable of (sp, finish_position) for every runner on the card.
    Scratched runners are ignored — they are not expected to have a price.

    Returns a dict with coverage, overround, counts, `usable` (may this race
    become a training target?) and `reason` (None when usable).
    """
    priced, considered = [], 0
    for sp, finish in runners:
        try:
            scratched = int(finish) == SCRATCHED_FINISH_POSITION
        except (TypeError, ValueError):
            scratched = False
        if scratched:
            continue
        considered += 1
        value = usable_sp(sp)
        if value is not None:
            priced.append(value)

    coverage = (len(priced) / considered) if considered else 0.0
    overround = sum(1.0 / p for p in priced) if len(priced) >= MIN_PRICED_FOR_OVERROUND else None

    if considered == 0:
        reason = "no non-scratched runners"
    elif coverage < MIN_PRICE_COVERAGE:
        reason = (f"only {len(priced)}/{considered} runners priced "
                  f"({coverage:.0%} < {MIN_PRICE_COVERAGE:.0%} required) — incomplete "
                  f"starting-price data, and in this dataset the priced runners are "
                  f"the ones that placed")
    else:
        reason = None

    return {
        'priced': len(priced),
        'runners': considered,
        'coverage': coverage,
        'overround': overround,
        'usable': reason is None,
        'reason': reason,
        'suspicious_overround': (overround is not None and overround < SUSPICIOUS_OVERROUND),
    }


def unusable_race_ids(rows):
    """Race ids whose book fails the gate.

    rows: iterable of (race_id, sp, finish_position) across any number of races.
    """
    by_race = {}
    for race_id, sp, finish in rows:
        by_race.setdefault(race_id, []).append((sp, finish))
    return {rid: q for rid, q in ((rid, race_book_quality(r)) for rid, r in by_race.items())
            if not q['usable']}
