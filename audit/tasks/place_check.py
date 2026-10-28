"""Re-test the Place Bet rule with the correct last start.

The first hunt read 'last start' from a saved field that often held an older
run. Here last start comes from the last10 string. Same rule otherwise: ran
2nd last start + analyzer 'Weight vs Field - Below (2-3kg)', backed for a place
at Betfair place BSP (8% commission).
"""
import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import bf_files, frame
from audit.tasks.hunt2 import notes_conditions

SPLIT = pd.Timestamp("2026-07-01")


def run(ctx):
    d = frame.build(ctx)
    d = d[d.mkt_p.notna() & (d.date >= pd.Timestamp("2026-02-01"))].reset_index(drop=True)
    d = bf_files.attach(d, [(2026, m) for m in range(2, 10)])
    comp = notes_conditions(ctx, d).get("an: Weight vs Field - Below (2-3kg)")
    if comp is None:
        emit("place_check", "#ERROR component not found")
        return
    rows = []
    for name, pos_col in (("CORRECT last start (last10)", "last_pos"), ("OLD saved field (what the hunt used)", "last_pos_saved")):
        for extra, m_extra in (("2-3kg under", comp), ("any weight", np.ones(len(d), bool))):
            m = (d[pos_col] == 2).values & m_extra & np.isfinite(d.ret_place.values)
            for per, pm in (("Feb-Jun", (d.date < SPLIT).values), ("Jul-Sep", (d.date >= SPLIT).values), ("all", np.ones(len(d), bool))):
                mm = m & pm
                n = int(mm.sum())
                if not n:
                    continue
                r = d.ret_place.values[mm]
                v = (d.place_bsp.values[mm] - 1) * 0.92
                z = r.sum() / np.sqrt(np.sum(v))
                rows.append((name, extra, per, n, round(100 * d.placed.values[mm].mean(), 1), round(100 * r.mean(), 1), round(z, 2)))
    agree = (d.last_pos == d.last_pos_saved).mean()
    emit("place_check", f"saved 'form position' equals real last start for {100 * agree:.1f}% of runners")
    ctx.table("place_check.results", ["last_start_source", "weight", "period", "bets", "place_pct", "roi_place_bsp", "z"], rows)
