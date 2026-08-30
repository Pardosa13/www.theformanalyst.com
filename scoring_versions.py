"""Champion Score comparability across scoring-formula versions.

The Champion Score formula changed six times:

    champion_score_v2_walk_forward_calibrated   blended_roi + 0.5 * strike_rate
    champion_score_v3_ae_ratio                  ... + (A/E - 1) * 10 instead
    champion_score_v4_ae_ratio
    champion_score_v5_ae_ratio
    champion_score_v6_joint_kelly               ... + a joint-Kelly term
    champion_score_v7_flb_corrected_ae          ... A/E measured against
                                                favourite-longshot-corrected
                                                market probabilities, not 1/SP

Each change shifts every score by a fixed offset. Under v2 a 35% strike rate was
worth a flat +17.7 points to every model regardless of edge; v3 replaced that
term with one centred on zero. So a v2 score of 49 and a v6 score of -26 can
describe models of similar quality. v7 moves every A/E up by roughly the book's
overround (raw 1/SP sums to ~1.10-1.25, so it overstated expected wins and
understated A/E for every model at once) — a uniform shift, but not one that
can be undone by eye, which is why it needs its own version.

Plotting those on one axis is what made a scoring-rule change look like a
collapse in model performance and sent an investigation after the wrong cause.
These helpers exist so no panel, chart or API response can do it silently.

Pure functions with no database or Flask dependency, so they are importable from
anywhere and testable without standing up the app.
"""
from collections import OrderedDict

UNVERSIONED = 'unversioned'


def scores_comparable(*summaries):
    """May these Champion Scores be compared, or drawn on one axis?

    Only when every score present was produced by the same formula. A score whose
    version was never recorded counts as not comparable — it cannot be shown to be.
    Fewer than two actual scores means there is nothing to compare, which is fine.

    Each summary is a mapping with 'champion_score' and 'scoring_formula_version';
    None and empty mappings are ignored.
    """
    versions = [
        (s or {}).get('scoring_formula_version')
        for s in summaries
        if s and (s or {}).get('champion_score') is not None
    ]
    if len(versions) < 2:
        return True
    return all(v is not None and v == versions[0] for v in versions)


def group_by_formula_version(rows):
    """Split (run_id, score, version) rows into one series per formula version.

    A chart of Champion Score over time must render these as separate series
    rather than joining them into a single line. Rows with no score are dropped;
    rows with no version are collected under 'unversioned'. Each series is sorted
    by run id.
    """
    grouped = OrderedDict()
    for run_id, score, version in rows:
        if score is None:
            continue
        grouped.setdefault(version or UNVERSIONED, []).append((run_id, score))
    for series in grouped.values():
        series.sort(key=lambda pair: (pair[0] is None, pair[0]))
    return grouped
