"""Do our picks beat the closing price?

If a pick's price shortens between early and the jump, the pick knew
something the early market didn't, and backing it early makes money even
when the final price leaves no edge. This needs far fewer bets than ROI,
because every bet's price move counts, not just the winners.

Part A (Feb-Sep): Betfair's pre-jump volume-weighted average price (WAP) vs BSP.
Part B (from 15 Oct): Ladbrokes snapshots 60+ min out vs the last one before the jump.
Each pick is compared with all runners at the same price, because long shots
drift and favourites firm on their own.
"""
import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import bf_files, frame

MONTHS = [(2026, m) for m in range(2, 10)]
BANDS = [1, 2, 3, 5, 8, 13, 21, 1000]


def _band(price):
    return pd.cut(price, BANDS, right=False, labels=False)


def _moves(d, early, late, label):
    """Mean log(early/late) for each pick group, minus the same-price baseline."""
    d = d[(d[early] > 1) & (d[late] > 1)].copy()
    d["mv"] = np.log(d[early] / d[late])           # > 0: shortened (beat the close)
    d["band"] = _band(d[late])
    base = d.groupby("band")["mv"].mean()
    d["excess"] = d["mv"] - d["band"].map(base)
    d["ret_early"] = np.where(d.won == 1, d[early] - 1, -1.0)
    d["ret_late"] = np.where(d.won == 1, d[late] - 1, -1.0)
    groups = {
        "all runners": np.ones(len(d), bool),
        "market fav (late)": d.late_rank == 1,
        "analyzer top": d.a_rank == 1,
        "analyzer 2nd": d.a_rank == 2,
        "pfai top": d.pfai_rank == 1,
        "time rank 1": d.time_rank == 1,
        "wclass rank 1": d.wc_rank == 1,
        "ML top (scored pre-race)": (d.ml_rank == 1) & d.ml_fwd.fillna(False).astype(bool),
    }
    rows = []
    for name, m in groups.items():
        m = np.asarray(m, bool)
        n = int(m.sum())
        if n < 30:
            continue
        ex = d.excess.values[m]
        se = ex.std(ddof=1) / np.sqrt(n) if n > 1 else np.nan
        rows.append((name, n, round(100 * d.mv.values[m].mean(), 2), round(100 * ex.mean(), 2),
                     round(ex.mean() / se, 2) if se else np.nan,
                     round(100 * (d.mv.values[m] > 0).mean(), 1),
                     round(100 * d.ret_early.values[m].mean(), 1), round(100 * d.ret_late.values[m].mean(), 1)))
    emit(f"clv.{label}", "pct_move = avg % the price shortened (early vs late); excess = vs all runners at the same price; z>2 = real")
    return rows


def run(ctx):
    d = frame.build(ctx)
    d["ml_rank"] = d.groupby("race_id")["ml_prob"].rank(ascending=False, method="min")
    d = d[d.date >= pd.Timestamp("2026-02-01")]
    a = bf_files.attach(d, MONTHS)
    a["late_rank"] = a.groupby("race_id")["bsp"].rank(method="min")
    cols = ["group", "bets", "pct_move", "excess_pct", "z", "pct_shortened", "roi_at_early", "roi_at_late"]
    ctx.table("clv.A_betfair_wap_vs_bsp", cols, _moves(a, "pre_wap", "bsp", "A"))
    # Raw ROI at Betfair prices after 8% commission, early (WAP) vs BSP, by pick.
    for col, nm in (("pre_wap", "WAP"), ("pre_max", "best price matched pre-jump")):
        x = a[(a[col] > 1) & (a.bsp > 1)]
        rows = []
        for name, m in (("all", np.ones(len(x), bool)), ("fav", x.late_rank == 1), ("analyzer top", x.a_rank == 1), ("pfai top", x.pfai_rank == 1)):
            m = np.asarray(m, bool)
            r_e = np.where(x.won[m] == 1, (x[col][m] - 1) * 0.92, -1).mean()
            r_b = np.where(x.won[m] == 1, (x.bsp[m] - 1) * 0.92, -1).mean()
            rows.append((name, int(m.sum()), round(100 * r_e, 1), round(100 * r_b, 1)))
        ctx.table(f"clv.A_roi_{nm}", ["group", "bets", f"roi_{col}", "roi_bsp"], rows)

    # Part B: Ladbrokes snapshots.
    snap = ctx.df("""
      with s as (select o.horse_id, o.odds, o.captured_at, rc.start_time_utc st
        from live_odds_snapshots o join races rc on rc.id=o.race_id
        where o.odds>1 and rc.start_time_utc is not null and o.captured_at < rc.start_time_utc
          and o.captured_at > rc.start_time_utc - interval '12 hours'),
      e as (select distinct on (horse_id) horse_id, odds early from s where captured_at <= st - interval '60 minutes' order by horse_id, captured_at desc),
      l as (select distinct on (horse_id) horse_id, odds late from s where captured_at >= st - interval '10 minutes' order by horse_id, captured_at desc)
      select e.horse_id, early, late from e join l using(horse_id)""")
    b = d.merge(snap, on="horse_id", how="inner")
    b["late_rank"] = b.groupby("race_id")["late"].rank(method="min")
    emit("clv.B", f"runners with early+late Ladbrokes price: {len(b)} races={b.race_id.nunique()}")
    ctx.table("clv.B_ladbrokes_60m_vs_jump", cols, _moves(b, "early", "late", "B"))
