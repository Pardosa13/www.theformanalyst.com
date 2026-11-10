"""Six years of Betfair horse racing (Jan 2020 on): do the market leads hold up?

Betfair's free monthly ANZ thoroughbred files (no form, prices only) are read
month by month. Every rule below uses only prices known BEFORE the jump: the
best back price at the scheduled off and the pre-play prices traded earlier.
Bets settle at Betfair SP (win) or Betfair place SP (place), 8% commission.
A rule that is real should make money in most YEARS, not just on the total.

  band            win BSP price bands, back every runner (market calibration)
  firm / drift    price at the off 10%+ shorter (firm) or longer (drift) than
                  the average matched earlier; backed to win and for a place
  firm + fav3     firmers that are also in the first three of the market
  place vs win    the win prices at the off say a runner's place chance is
                  well above what the place market said (pre-play average),
                  backed for a place (Harville)
"""
import io
import time
from collections import defaultdict

import numpy as np
import pandas as pd
import requests

from audit.run_audit import emit

URL = "https://betfair-datascientists.github.io/data/assets/ANZ_Thoroughbreds_{y}_{m:02d}.csv"
KEEP = 0.92
COLS = ["LOCAL_MEETING_DATE", "WIN_MARKET_ID", "SELECTION_ID", "WIN_RESULT", "WIN_BSP", "PLACE_RESULT",
        "PLACE_BSP", "WIN_PREPLAY_WEIGHTED_AVERAGE_PRICE_TAKEN", "PLACE_WEIGHTED_AVERAGE_PRICE_TAKEN",
        "BEST_AVAIL_BACK_AT_SCHEDULED_OFF", "WIN_PREPLAY_VOLUME"]


def _months(start=(2020, 1)):
    y, m = start
    today = pd.Timestamp.today()
    while (y, m) <= (today.year, today.month):
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def _load(y, m):
    for attempt in range(4):
        try:
            r = requests.get(URL.format(y=y, m=m), timeout=120)
            if r.status_code == 404:
                return None
            if r.status_code == 200:
                df = pd.read_csv(io.StringIO(r.text), usecols=lambda c: c in COLS, low_memory=False)
                return df
        except Exception:
            pass
        time.sleep(3 * (attempt + 1))
    emit("bfhist", f"{y}-{m:02d}: gave up")
    return None


def _harville_place(p, k):
    """Chance of finishing in the first k (2 or 3) from win chances p (one race)."""
    n = len(p)
    out = p.copy()
    if n < 2:
        return out
    q = p[:, None] * p[None, :] / np.clip(1 - p[:, None], 1e-9, None)   # j first, i second: [j, i]
    np.fill_diagonal(q, 0)
    second = q.sum(axis=0)
    out = p + second
    if k >= 3:
        third = np.zeros(n)
        for j in range(n):
            for kk in range(n):
                if kk == j:
                    continue
                rest = 1 - p[j] - p[kk]
                if rest <= 1e-9:
                    continue
                w = p[j] * p[kk] / max(1 - p[j], 1e-9) / rest
                third += w * p
                third[j] -= w * p[j]
                third[kk] -= w * p[kk]
        out = out + third
    return np.clip(out, 0, 1)


class Tally:
    def __init__(self):
        self.t = defaultdict(lambda: [0, 0.0, 0.0])   # (rule, year) -> bets, profit, variance

    def add(self, rule, year, ret, win_amount, chance):
        ok = np.isfinite(ret) & np.isfinite(win_amount) & np.isfinite(chance)
        if not ok.any():
            return
        r, w, c = ret[ok], win_amount[ok], np.clip(chance[ok], 1e-4, 1 - 1e-4)
        mu = c * w - (1 - c)
        var = c * w ** 2 + (1 - c) - mu ** 2
        cell = self.t[(rule, year)]
        cell[0] += int(ok.sum())
        cell[1] += float(r.sum())
        cell[2] += float(var.sum())

    def rows(self):
        rules = sorted({k[0] for k in self.t})
        years = sorted({k[1] for k in self.t})
        out = []
        for rule in rules:
            n = p = v = 0
            up = active = 0
            per = []
            for y in years:
                b, pr, vr = self.t.get((rule, y), [0, 0.0, 0.0])
                n += b; p += pr; v += vr
                if b >= 100:
                    active += 1
                    up += int(pr > 0)
                per.append(f"{y}:{(100 * pr / b):+.0f}" if b >= 100 else f"{y}:.")
            if n:
                out.append((rule, n, round(100 * p / n, 1), round(p / np.sqrt(v), 2) if v > 0 else None,
                            f"{up}/{active}", " ".join(per)))
        return out


def run(ctx):
    tally = Tally()
    runners = races = 0
    for y, m in _months():
        df = _load(y, m)
        if df is None or df.empty:
            continue
        df = df.rename(columns={"WIN_BSP": "bsp", "PLACE_BSP": "pbsp", "WIN_MARKET_ID": "race",
                                "WIN_PREPLAY_WEIGHTED_AVERAGE_PRICE_TAKEN": "wap",
                                "PLACE_WEIGHTED_AVERAGE_PRICE_TAKEN": "pwap",
                                "BEST_AVAIL_BACK_AT_SCHEDULED_OFF": "off"})
        for c in ("bsp", "pbsp", "wap", "pwap", "off"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df[df.bsp > 1].copy()
        df["won"] = (df.WIN_RESULT.astype(str).str.upper() == "WINNER").astype(float)
        df["placed"] = (df.PLACE_RESULT.astype(str).str.upper() == "WINNER").astype(float)
        df["field"] = df.groupby("race")["bsp"].transform("count")
        df["k"] = np.where(df.field >= 8, 3, np.where(df.field >= 5, 2, 0))
        # Win chance from the price at the off (fair: normalised per race).
        off = df.off.where(df.off > 1)
        ip = 1 / off
        df["p_off"] = ip / ip.groupby(df.race).transform("sum")
        bsp_ip = 1 / df.bsp
        df["p_bsp"] = bsp_ip / bsp_ip.groupby(df.race).transform("sum")
        df["rank_off"] = df.groupby("race")["off"].rank(method="min")
        has_place = (df.pbsp > 1) & (df.k >= 2)
        pip = np.where(has_place, 1 / df.pbsp, np.nan)
        book = pd.Series(pip).groupby(df.race.values).transform("sum").values
        df["q_place"] = np.clip(pip / book * df.k, 1e-4, 0.999)   # place market's fair chance (BSP)
        ppw = np.where(df.pwap > 1, 1 / df.pwap, np.nan)
        pbook = pd.Series(ppw).groupby(df.race.values).transform("sum").values
        df["q_place_pre"] = np.clip(ppw / pbook * df.k, 1e-4, 0.999)
        ret_win = np.where(df.won == 1, (df.bsp - 1) * KEEP, -1.0)
        ret_place = np.where(has_place, np.where(df.placed == 1, (df.pbsp - 1) * KEEP, -1.0), np.nan)
        w_win = (df.bsp.values - 1) * KEEP
        w_place = (df.pbsp.values - 1) * KEEP
        year = y

        for lo, hi in ((1, 2), (2, 4), (4, 8), (8, 16), (16, 40), (40, 1001)):
            mk = ((df.bsp >= lo) & (df.bsp < hi)).values
            tally.add(f"band win BSP {lo}-{hi}", year, ret_win[mk], w_win[mk], df.p_bsp.values[mk])

        move = (df.off / df.wap.where(df.wap > 1)).values
        firm, drift = move <= 0.9, move >= 1.1
        top3 = (df.rank_off <= 3).values
        for name, mk in (("firm 10%+", firm), ("drift 10%+", drift), ("firm 10%+ and top 3 in market", firm & top3)):
            tally.add(f"{name}: win bet", year, ret_win[mk], w_win[mk], df.p_bsp.values[mk])
            tally.add(f"{name}: place bet", year, ret_place[mk], w_place[mk], df.q_place.values[mk])

        # Place vs win market (Harville from prices at the off, against the pre-play place price).
        hp = np.full(len(df), np.nan)
        for _race, idx in df.groupby("race").indices.items():
            p = df.p_off.values[idx]
            kk = int(df.k.values[idx[0]])
            if kk >= 2 and np.isfinite(p).all():
                hp[idx] = _harville_place(p / p.sum(), kk)
        ratio = hp / df.q_place_pre.values
        for lo, hi in ((1.1, 1.25), (1.25, 1.5), (1.5, 99)):
            mk = (ratio >= lo) & (ratio < hi)
            tally.add(f"place vs win: win prices say {lo}-{hi}x the place market", year,
                      ret_place[mk], w_place[mk], df.q_place.values[mk])

        runners += len(df)
        races += df.race.nunique()
        emit("bfhist", f"{y}-{m:02d}: runners={len(df)}")
    emit("bfhist", f"TOTAL runners={runners} races={races}")
    ctx.table("bfhist.rules", ["rule", "bets", "roi_pct", "z_vs_prices", "years_up",
                               "roi_by_year (years with 100+ bets)"], tally.rows(), limit=60)
    emit("bfhist", "done (z: how far from what the prices alone predict; 2+ unlikely to be luck)")
