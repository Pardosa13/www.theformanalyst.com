"""Which race-day angles come closest to breaking even? (read-only)

For a human short-list, not a betting system. Every angle a person could
check on race morning is scored at Betfair prices (win at BSP, place at
Betfair place SP, 8% commission):

  picks       the site's own top picks and ratings (analyzer, ML, PFAI, ...)
  components  each analyzer component that fired on the runner
  jockey / trainer / jockey+trainer   every rider, stable and pairing

The year is split in two. Angles are CHOSEN on the first half (Feb-Jun) only
when they were within 3% of break-even there, then JUDGED on the second half
(Jul-Oct). An angle near break-even in both halves is worth a human look; one
that only looked good in the half it was chosen on was luck.
"""
import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import trending
from notes_parsing import parse_notes_components

SPLIT = pd.Timestamp("2026-07-01")
CHOOSE_AT = -0.03          # first-half ROI needed to be chosen
MIN_HALF = {"picks": 150, "components": 150, "jockey": 60, "trainer": 40, "jockey+trainer": 25}


def _roi(r):
    r = r[np.isfinite(r)]
    return (len(r), float(r.mean()) if len(r) else np.nan)


def _angles(d, ctx):
    out = {"picks": {}, "components": {}, "jockey": {}, "trainer": {}, "jockey+trainer": {}}
    p = out["picks"]
    fav = d.bsp_rank == 1
    ml = d.ml_prob.where(d.ml_fwd == True)
    d["ml_rank"] = ml.groupby(d.race_id).rank(ascending=False, method="min")
    for name, col in (("analyzer", "a_rank"), ("ML", "ml_rank"), ("PFAI", "pfai_rank"),
                      ("time rating", "time_rank"), ("weight-class rating", "wc_rank")):
        r = d[col]
        p[f"{name} top pick"] = r == 1
        p[f"{name} top 2"] = r <= 2
        p[f"{name} top pick, not favourite"] = (r == 1) & ~fav
        p[f"{name} top pick and favourite"] = (r == 1) & fav
        for lo, hi in ((1, 3), (3, 6), (6, 11), (11, 1001)):
            p[f"{name} top pick, BSP ${lo}-{hi}"] = (r == 1) & (d.bsp >= lo) & (d.bsp < hi)
    # Same angles, but priced and ranked on the average price matched BEFORE
    # the jump (Betfair pre-play), which a person could see on race day.
    pre = d.pre_wap.where(d.pre_wap > 1)
    pre_fav = pre.groupby(d.race_id).rank(method="min") == 1
    for name, col in (("analyzer", "a_rank"), ("ML", "ml_rank"), ("PFAI", "pfai_rank")):
        r = d[col]
        p[f"PRE-JUMP {name} top pick and favourite"] = (r == 1) & pre_fav
        p[f"PRE-JUMP {name} top pick, not favourite"] = (r == 1) & ~pre_fav & pre.notna()
        for lo, hi in ((1, 3), (3, 6), (6, 11), (11, 1001)):
            p[f"PRE-JUMP {name} top pick, ${lo}-{hi}"] = (r == 1) & (pre >= lo) & (pre < hi)
    p["PRE-JUMP favourite"] = pre_fav
    p["analyzer AND ML agree on top pick"] = (d.a_rank == 1) & (d.ml_rank == 1)
    p["analyzer AND PFAI agree on top pick"] = (d.a_rank == 1) & (d.pfai_rank == 1)
    p["favourite"] = fav
    p["2nd favourite"] = d.bsp_rank == 2
    p["3rd favourite"] = d.bsp_rank == 3

    notes = ctx.df("""
        SELECT DISTINCT ON (horse_id) horse_id, notes FROM predictions
        WHERE notes IS NOT NULL AND horse_id = ANY(%s) ORDER BY horse_id, id DESC
    """, ([int(h) for h in d.horse_id.unique()],))
    comps = {}
    nmap = dict(zip(notes.horse_id, notes.notes))
    for i, hid in enumerate(d.horse_id.values):
        for name, score in parse_notes_components(nmap.get(hid)).items():
            try:
                if float(score) == 0:
                    continue
            except (TypeError, ValueError):
                pass
            comps.setdefault(name, []).append(i)
    for name, idx in comps.items():
        m = np.zeros(len(d), bool)
        m[idx] = True
        out["components"][name] = m
    emit("closest", f"components seen={len(comps)} runners with notes={sum(1 for h in d.horse_id if h in nmap)}")

    s = lambda col: d[col].fillna("").astype(str).str.strip().str.lower()
    J, T = s("jockey"), s("trainer")
    keys = {"jockey": J, "trainer": T, "jockey+trainer": (J + " + " + T).where((J != "") & (T != ""), "")}
    for fam, k in keys.items():
        for name, idx in k.groupby(k).indices.items():
            if name and len(idx) >= 2 * MIN_HALF[fam]:
                m = np.zeros(len(d), bool)
                m[idx] = True
                out[fam][name] = m
    return out


def run(ctx):
    d = trending._prepare(ctx)
    first = (d.date < SPLIT).values
    months = d.date.dt.to_period("M").astype(str).values
    emit("closest", f"runners={len(d)} races={d.race_id.nunique()} first half={first.sum()} "
                    f"second half={(~first).sum()} split={SPLIT.date()}")
    fams = _angles(d, ctx)
    for bet, col in (("win at BSP", "ret_bsp"), ("place at Betfair place SP", "ret_place")):
        ret = d[col].values
        for fam, angles in fams.items():
            rows, chosen_b = [], []
            tested = 0
            for name, m in angles.items():
                m = np.asarray(m, bool)
                na, ra = _roi(ret[m & first])
                nb, rb = _roi(ret[m & ~first])
                if na < MIN_HALF[fam] or nb < MIN_HALF[fam] // 2:
                    continue
                tested += 1
                if ra < CHOOSE_AT and fam != "picks":
                    continue
                rr = ret[m]
                ok = np.isfinite(rr)
                per = pd.Series(rr[ok]).groupby(months[m][ok]).agg(["count", "mean"])
                per = per[per["count"] >= 10]
                up = f"{int((per['mean'] > 0).sum())}/{len(per)}"
                if ra >= CHOOSE_AT:
                    chosen_b.append(ret[m & ~first])
                rows.append((name, na, round(100 * ra, 1), nb, round(100 * rb, 1),
                             round(100 * np.nanmean(rr), 1), up))
            rows.sort(key=lambda r: -r[4])
            if fam == "picks":
                rows = [r for r in rows]
            pooled = np.concatenate(chosen_b) if chosen_b else np.array([])
            pooled = pooled[np.isfinite(pooled)]
            emit(f"closest.{fam}", f"[{bet}] tested={tested} chosen (first half {int(100 * CHOOSE_AT)}%+)={len(rows)} "
                                   f"-> their second half together: bets={len(pooled)} "
                                   f"roi={100 * pooled.mean() if len(pooled) else float('nan'):+.1f}%")
            ctx.table(f"closest.{fam}.{'win' if col == 'ret_bsp' else 'place'}",
                      ["angle", "bets_feb_jun", "roi_feb_jun", "bets_jul_oct", "roi_jul_oct", "roi_all", "months_up"],
                      rows, limit=80 if fam == "picks" else 25)
        # Everything, for a baseline.
        n, r = _roi(ret)
        emit("closest.baseline", f"[{bet}] every runner: bets={n} roi={100 * r:+.1f}%")
    emit("closest", "done")
