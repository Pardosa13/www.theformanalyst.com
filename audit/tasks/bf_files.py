"""Betfair's free monthly ANZ thoroughbred files, read straight from the web.

The app only imports the win BSP. These files also hold the place market
(PLACE_BSP, PLACE_RESULT) and the prices matched before the jump (max, min,
volume-weighted average), which the audit uses for place betting and
"did the price shorten?" tests. Nothing is written to the database.
"""
import io

import numpy as np
import pandas as pd
import requests

from audit.run_audit import emit

URL = "https://betfair-datascientists.github.io/data/assets/ANZ_Thoroughbreds_{y}_{m:02d}.csv"
COLS = {
    "WIN_MARKET_ID": "market_id", "SELECTION_ID": "selection_id",
    "PLACE_BSP": "place_bsp", "PLACE_RESULT": "place_result",
    "WIN_PREPLAY_MAX_PRICE_TAKEN": "pre_max", "WIN_PREPLAY_MIN_PRICE_TAKEN": "pre_min",
    "WIN_PREPLAY_WEIGHTED_AVERAGE_PRICE_TAKEN": "pre_wap", "WIN_PREPLAY_VOLUME": "pre_vol",
    "WIN_BSP_VOLUME": "bsp_vol", "PLACE_BSP_VOLUME": "place_bsp_vol",
}


def load(months):
    frames = []
    for y, m in months:
        try:
            r = requests.get(URL.format(y=y, m=m), timeout=120)
            if r.status_code != 200:
                emit("bf_files", f"{y}-{m:02d}: HTTP {r.status_code}")
                continue
            df = pd.read_csv(io.StringIO(r.text), dtype=str)
            df = df[[c for c in COLS if c in df.columns]].rename(columns=COLS)
            frames.append(df)
            emit("bf_files", f"{y}-{m:02d}: rows={len(df)}")
        except Exception as e:  # keep going on a bad month
            emit("bf_files", f"{y}-{m:02d}: #ERROR {e}")
    if not frames:
        return pd.DataFrame(columns=list(COLS.values()))
    out = pd.concat(frames, ignore_index=True)
    for c in ("place_bsp", "pre_max", "pre_min", "pre_wap", "pre_vol", "bsp_vol", "place_bsp_vol"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out["selection_id"] = pd.to_numeric(out["selection_id"], errors="coerce")
    out["placed"] = (out["place_result"].fillna("").str.upper() == "WINNER").astype(int)
    return out.drop_duplicates(["market_id", "selection_id"])


def attach(d, months):
    """Add the place market and pre-jump prices to the audit frame `d`."""
    bf = load(months)
    d = d.copy()
    d["market_id"] = d["market_id"].astype(str)
    d["selection_id"] = pd.to_numeric(d["betfair_selection_id"], errors="coerce")
    d = d.merge(bf, on=["market_id", "selection_id"], how="left")
    d["market_vol"] = d.groupby("race_id")["bsp_vol"].transform("sum") + d.groupby("race_id")["pre_vol"].transform("sum")
    has = d.place_bsp > 1
    d["ret_place"] = np.where(has, np.where(d.placed == 1, (d.place_bsp - 1) * 0.92, -1.0), np.nan)
    d["ret_place_lay"] = np.where(has, np.where(d.placed == 1, -(d.place_bsp - 1), 0.92), np.nan)
    emit("bf_files", f"matched place_bsp={has.mean():.2f} pre_wap={(d.pre_wap > 1).mean():.2f} of {len(d)} runners")
    return d
