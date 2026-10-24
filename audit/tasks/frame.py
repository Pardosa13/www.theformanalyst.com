"""Build one clean row per runner that actually ran, for pattern hunting.

Duplicate horse rows (same race, same name) are collapsed to the row that
holds the run result. Only races with complete results are used.
"""
import json
import re

import numpy as np
import pandas as pd

from audit.run_audit import emit

SQL = """
with ran as (
  select distinct on (h.race_id, lower(trim(h.horse_name)))
         h.id horse_id, h.race_id, h.horse_name, h.barrier, h.weight, h.jockey, h.trainer, h.csv_data,
         r.finish_position fin, r.sp, r.bsp
  from horses h join results r on r.horse_id=h.id
  where r.finish_position>0
  order by h.race_id, lower(trim(h.horse_name)), h.is_scratched, h.id desc)
select ran.*, rc.meeting_id, rc.race_number, rc.distance race_distance, rc.race_class, rc.track_condition,
       rc.start_time_utc, m.date meeting_date, m.meeting_name,
       p.score a_score, p.calibrated_win_prob a_prob, p.pfai_score, p.ml_win_probability ml_prob,
       (p.ml_scored_at < rc.start_time_utc) ml_fwd
from ran join races rc on rc.id=ran.race_id join meetings m on m.id=rc.meeting_id
left join lateral (select * from predictions p where p.horse_id=ran.horse_id order by p.id desc limit 1) p on true
where not rc.results_incomplete
"""

RATINGS_SQL = "select id race_id, race_number, ratings_json, speed_maps_json from races where ratings_json is not null or speed_maps_json is not null"


def _num(v):
    try:
        if v is None or v == "":
            return np.nan
        return float(str(v).replace("$", "").replace(",", ""))
    except Exception:
        return np.nan


def _load_json(v):
    while isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return None
    return v


def _record(s):
    """'12:3-2-1' -> (starts, wins, places)."""
    m = re.match(r"\s*(\d+)\s*:\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)", str(s or ""))
    if not m:
        return np.nan, np.nan
    n, w = int(m.group(1)), int(m.group(2))
    return n, w


def ratings_by_race(ctx):
    """race_id -> {tabNo: dict of PuntingForm ratings/speed-map values}. Also
    reports how many PuntingForm race ids each blob holds."""
    df = ctx.df(RATINGS_SQL)
    out, multi, total = {}, 0, 0
    for race_id, race_no, rj, sj in df.itertuples(index=False):
        total += 1
        d = _load_json(rj) or {}
        items = d.get("payLoad") or [] if isinstance(d, dict) else []
        # Blobs hold the whole meeting: keep only this race's runners.
        items = [it for it in items if str(it.get("raceNo")) == str(race_no)]
        rids = {it.get("raceId") for it in items}
        if len(rids) > 1:
            multi += 1
        per = out.setdefault(race_id, {})
        for it in items:
            per.setdefault(it.get("tabNo"), {}).update({
                "pfai_price": _num(it.get("pfaiPrice")), "pfai_rank": _num(it.get("pfaiRank")),
                "time_rank": _num(it.get("timeRank")), "wc_rank": _num(it.get("weightClassRank")),
                "settle": _num(it.get("predictedSettlePostion")), "run_style": it.get("runStyle"),
                "class_change": _num(it.get("classChange")),
                "neural_price": _num(it.get("neuralPrice")), "pf_score": _num(it.get("pfScore")),
                "wc_price": _num(it.get("weightClassPrice")), "time_price": _num(it.get("timePrice")),
            })
        s = _load_json(sj) or {}
        for race in (s.get("payLoad") or [] if isinstance(s, dict) else []):
            if race.get("raceNo") is not None and str(race.get("raceNo")) != str(race_no):
                continue
            for it in race.get("items") or []:
                per.setdefault(it.get("tabNo"), {}).update({
                    "assessed_price": _num(it.get("assessedPrice")), "sm_settle": _num(it.get("settle")),
                    "sm_speed": _num(it.get("speed")),
                })
    emit("frame.ratings", f"races_with_blobs={total} blobs_with_multiple_pf_race_ids={multi}")
    return out


def build(ctx):
    df = ctx.df(SQL)
    emit("frame", f"rows={len(df)} races={df.race_id.nunique()}")
    cd = df.pop("csv_data").map(lambda v: _load_json(v) or {})
    g = lambda k: cd.map(lambda d: d.get(k))
    df["tab"] = g("horse number").map(_num)
    df["age"] = g("horse age").map(_num)
    df["sex"] = g("horse sex").fillna("").str.strip().str.lower()
    df["claim"] = g("horse claim").map(_num)
    df["last10"] = g("horse last10").fillna("").astype(str)
    rec = g("horse record").map(_record)
    df["career_starts"] = rec.map(lambda t: t[0])
    df["career_wins"] = rec.map(lambda t: t[1])
    df["last_pos"] = g("form position").map(_num)
    df["last_price"] = g("form price").map(_num)
    df["last_margin"] = g("form margin").map(_num)
    df["last_dist"] = g("form distance").map(_num)
    df["last_date"] = pd.to_datetime(g("form meeting date"), errors="coerce", dayfirst=True, format="mixed")
    df["last_cond"] = g("form track condition").fillna("").astype(str)
    df["prize_money"] = g("race prizemoney").map(_num)
    df["dist"] = df["race_distance"].map(_num)
    df["date"] = pd.to_datetime(df["meeting_date"])
    df["days_since"] = (df["date"] - df["last_date"]).dt.days
    df["track"] = df["meeting_name"].fillna("").str.replace(r"[_\-]?\d{6,8}.*$", "", regex=True).str.strip().str.lower()
    df["won"] = (df["fin"] == 1).astype(int)
    df["field"] = df.groupby("race_id")["horse_id"].transform("count")

    pf = ratings_by_race(ctx)
    for col in ("pfai_price", "pfai_rank", "time_rank", "wc_rank", "settle", "run_style",
                "class_change", "assessed_price", "sm_settle", "sm_speed",
                "neural_price", "pf_score", "wc_price", "time_price"):
        df[col] = [(pf.get(r, {}).get(int(t)) or {}).get(col) if t == t else None
                   for r, t in zip(df.race_id, df.tab)]
    for col in ("pfai_price", "pfai_rank", "time_rank", "wc_rank", "settle", "class_change",
                "assessed_price", "sm_settle", "sm_speed", "neural_price", "pf_score", "wc_price", "time_price"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Market: BSP implied probability normalised within the race.
    df["bsp_ip"] = 1 / df["bsp"]
    df["bsp_book"] = df.groupby("race_id")["bsp_ip"].transform("sum")
    df["bsp_n"] = df.groupby("race_id")["bsp"].transform("count")
    df["mkt_p"] = np.where(df["bsp_n"] == df["field"], df["bsp_ip"] / df["bsp_book"], np.nan)
    df["bsp_rank"] = df.groupby("race_id")["bsp"].rank(method="min")
    df["sp_rank"] = df.groupby("race_id")["sp"].rank(method="min")
    df["a_rank"] = df.groupby("race_id")["a_score"].rank(ascending=False, method="min")
    # Returns for a 1-unit win bet. BSP pays less 8% commission on winnings.
    df["ret_sp"] = np.where(df.won == 1, df.sp - 1, -1.0)
    df["ret_bsp"] = np.where(df.bsp.isna(), np.nan, np.where(df.won == 1, (df.bsp - 1) * 0.92, -1.0))
    # Lay 1 unit liability-free style: win +0.92 if loses, lose (bsp-1) if wins.
    df["ret_lay"] = np.where(df.bsp.isna(), np.nan, np.where(df.won == 1, -(df.bsp - 1), 0.92))
    return df
