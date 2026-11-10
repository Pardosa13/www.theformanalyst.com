"""UFC fight model built in this repo from full UFC history (ufcstats.com).

The old model (models/catboost_ufc_model.pkl) was trained elsewhere on data
that no longer exists, and its live inputs were thin. This one is rebuilt
here, every sync, from the public ufcstats.com scrape:

  - every UFC fight since 1994 with real per-round stats (strikes, takedowns,
    knockdowns, submissions attempted, control time) and the result
  - fighter height, reach and date of birth

For each fight, both fighters' records are built from their EARLIER fights
only, then the fight is added. A LightGBM model learns from the differences
between the two fighters. Every fight is used twice (A v B and B v A) so the
model cannot favour the fighter listed first, and the two views are averaged
when predicting.

Judged on fights it never saw (2024 on), see evaluate().
"""
import io
import logging
import math
import re
from collections import defaultdict
from datetime import date, datetime

import numpy as np
import pandas as pd

from mma_glicko import update as glicko_update, DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOL
from mma_name_utils import normalize_name

log = logging.getLogger("mma_model")

MODEL_VERSION = "lgbm_ufcstats_v1"
DATA_URL = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/{name}"
FILES = ("ufc_fight_results.csv", "ufc_fight_stats.csv", "ufc_event_details.csv", "ufc_fighter_tott.csv")
EMA = 0.35          # weight on the latest fight for the "recent form" numbers
PARAMS = dict(objective="binary", learning_rate=0.02, num_leaves=7, min_data_in_leaf=150,
              feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0,
              verbose=-1, seed=11, num_threads=4)
TREES = 520         # chosen on 2023 fights (see evaluate); fixed for live training


# ── Loading ─────────────────────────────────────────────────────────────────

def load_raw(source=None):
    """The four ufcstats files as DataFrames. source: a folder, or None to download."""
    out = {}
    for name in FILES:
        if source:
            out[name] = pd.read_csv(f"{source}/{name}", low_memory=False)
        else:
            import requests
            r = requests.get(DATA_URL.format(name=name), timeout=120)
            r.raise_for_status()
            out[name] = pd.read_csv(io.StringIO(r.text), low_memory=False)
    return out


def _of(s):
    """'12 of 30' -> (12, 30)."""
    m = re.match(r"\s*(\d+)\s+of\s+(\d+)", str(s))
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def _secs(s):
    m = re.match(r"\s*(\d+):(\d+)", str(s))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


def _inches(s):
    s = str(s)
    m = re.match(r"\s*(\d+)'\s*(\d+)", s)
    if m:
        return int(m.group(1)) * 12 + int(m.group(2))
    m = re.match(r"\s*(\d+(?:\.\d+)?)\"", s)
    return float(m.group(1)) if m else np.nan


def _key(name):
    return normalize_name(str(name).strip())


def fights_table(raw):
    """One row per fight, oldest first, with both fighters' summed stats."""
    res = raw["ufc_fight_results.csv"].copy()
    ev = raw["ufc_event_details.csv"].copy()
    st = raw["ufc_fight_stats.csv"].copy()
    for df in (res, ev, st):
        for c in ("EVENT", "BOUT", "FIGHTER"):
            if c in df.columns:
                df[c] = df[c].astype(str).str.strip()
    ev["date"] = pd.to_datetime(ev.DATE, format="%B %d, %Y", errors="coerce")
    res = res.merge(ev[["EVENT", "date", "LOCATION"]], on="EVENT", how="left")
    res = res[res.date.notna()].rename(columns={"TIME FORMAT": "fmt"})
    names = res.BOUT.str.split(r"\s+vs\.\s+", n=1, expand=True, regex=True)
    res["a"], res["b"] = names[0].str.strip(), names[1].str.strip()

    # Sum the round-by-round stats per fighter per fight.
    cols = {"sig": "SIG.STR.", "tot": "TOTAL STR.", "td": "TD", "head": "HEAD", "body": "BODY",
            "leg": "LEG", "dist": "DISTANCE", "clinch": "CLINCH", "ground": "GROUND"}
    for k, c in cols.items():
        pair = st[c].map(_of)
        st[k + "_l"] = pair.map(lambda x: x[0])
        st[k + "_a"] = pair.map(lambda x: x[1])
    st["kd"] = pd.to_numeric(st.KD, errors="coerce").fillna(0)
    st["subatt"] = pd.to_numeric(st["SUB.ATT"], errors="coerce").fillna(0)
    st["ctrl"] = st.CTRL.map(_secs)
    num = [c for c in st.columns if c.endswith("_l") or c.endswith("_a")] + ["kd", "subatt", "ctrl"]
    per = st.groupby(["EVENT", "BOUT", "FIGHTER"], sort=False)[num].sum()
    per = {k: v for k, v in zip(per.index, per.to_dict("records"))}

    rows = []
    for r in res.itertuples(index=False):
        sa = per.get((r.EVENT, r.BOUT, r.a))
        sb = per.get((r.EVENT, r.BOUT, r.b))
        rnd = int(r.ROUND) if str(r.ROUND).isdigit() else 1
        fmt = str(r.fmt)
        rows.append(dict(date=r.date, event=r.EVENT, a=r.a, b=r.b, outcome=str(r.OUTCOME).strip(),
                         method=str(r.METHOD).strip(), weightclass=str(r.WEIGHTCLASS).strip(),
                         rounds_sched=5 if str(fmt).startswith("5 Rnd") else 3,
                         secs=max((rnd - 1) * 300 + _secs(r.TIME), 1), sa=sa, sb=sb,
                         location=str(r.LOCATION)))
    df = pd.DataFrame(rows)
    # Inside an event the file lists the main event first; reverse so the
    # order within a day does not matter much (each fighter fights once a day).
    df = df.iloc[::-1].sort_values("date", kind="stable").reset_index(drop=True)
    return df


def bios_table(raw):
    t = raw["ufc_fighter_tott.csv"].copy()
    t["key"] = t.FIGHTER.map(_key)
    t = t.drop_duplicates("key")
    out = {}
    for r in t.itertuples(index=False):
        dob = pd.to_datetime(r.DOB, format="%b %d, %Y", errors="coerce")
        out[r.key] = dict(height=_inches(r.HEIGHT), reach=_inches(r.REACH),
                          dob=None if pd.isna(dob) else dob,
                          southpaw=1.0 if str(r.STANCE).strip() == "Southpaw" else 0.0)
    return out


# ── Fighter records (earlier fights only) ───────────────────────────────────

class Record:
    def __init__(self):
        self.n = self.wins = self.losses = 0
        self.streak = 0
        self.last = None
        self.secs = 0.0
        self.tot = defaultdict(float)     # career sums
        self.ema = {}                     # recent per-minute rates
        self.ko_wins = self.sub_wins = self.dec_wins = 0
        self.ko_losses = self.sub_losses = 0
        self.five_rounders = 0
        self.rating, self.rd, self.vol = DEFAULT_RATING, DEFAULT_RD, DEFAULT_VOL
        self.opp_ratings = []
        self.results = []

    def features(self, when, bio):
        f = {}
        mins = self.secs / 60.0
        per15 = 15.0 / mins if mins > 0 else 0.0
        t = self.tot
        f["n"] = self.n
        f["win_rate"] = (self.wins + 1.0) / (self.n + 2.0)
        f["streak"] = self.streak
        f["last3"] = float(np.mean(self.results[-3:])) if self.results else 0.5
        f["layoff"] = (when - self.last).days if self.last is not None else np.nan
        f["slpm"] = t["sig_l"] / mins if mins else np.nan
        f["sapm"] = t["opp_sig_l"] / mins if mins else np.nan
        f["sig_acc"] = t["sig_l"] / t["sig_a"] if t["sig_a"] else np.nan
        f["sig_def"] = 1 - t["opp_sig_l"] / t["opp_sig_a"] if t["opp_sig_a"] else np.nan
        f["td15"] = t["td_l"] * per15 if mins else np.nan
        f["td_acc"] = t["td_l"] / t["td_a"] if t["td_a"] else np.nan
        f["td_def"] = 1 - t["opp_td_l"] / t["opp_td_a"] if t["opp_td_a"] else np.nan
        f["kd15"] = t["kd"] * per15 if mins else np.nan
        f["kd_abs15"] = t["opp_kd"] * per15 if mins else np.nan
        f["sub15"] = t["subatt"] * per15 if mins else np.nan
        f["ctrl_pct"] = t["ctrl"] / self.secs if self.secs else np.nan
        f["ctrl_abs_pct"] = t["opp_ctrl"] / self.secs if self.secs else np.nan
        f["head_share"] = t["head_l"] / t["sig_l"] if t["sig_l"] else np.nan
        f["ground_share"] = t["ground_l"] / t["sig_l"] if t["sig_l"] else np.nan
        f["avg_mins"] = mins / self.n if self.n else np.nan
        for k in ("slpm", "sapm", "td15", "ctrl", "kd15"):
            f["ema_" + k] = self.ema.get(k, np.nan)
        f["ko_win_rate"] = self.ko_wins / self.n if self.n else np.nan
        f["sub_win_rate"] = self.sub_wins / self.n if self.n else np.nan
        f["ko_loss_rate"] = self.ko_losses / self.n if self.n else np.nan
        f["sub_loss_rate"] = self.sub_losses / self.n if self.n else np.nan
        f["five_rounders"] = self.five_rounders
        f["rating"] = self.rating
        f["rd"] = self.rd
        f["opp_rating"] = float(np.mean(self.opp_ratings[-5:])) if self.opp_ratings else np.nan
        bio = bio or {}
        dob = bio.get("dob")
        f["age"] = (pd.Timestamp(when) - dob).days / 365.25 if dob is not None else np.nan
        f["height"] = bio.get("height", np.nan)
        f["reach"] = bio.get("reach", np.nan)
        f["southpaw"] = bio.get("southpaw", np.nan)
        return f

    def add(self, when, mine, theirs, score, method, secs, five, opp):
        self.n += 1
        if score == 1:
            self.wins += 1
            self.streak = self.streak + 1 if self.streak > 0 else 1
        elif score == 0:
            self.losses += 1
            self.streak = self.streak - 1 if self.streak < 0 else -1
        self.results.append(score)
        self.last = when
        self.five_rounders += int(five)
        m = method.upper()
        finish_ko = "KO" in m or "DOCTOR" in m
        finish_sub = "SUBMISSION" in m
        if score == 1:
            self.ko_wins += finish_ko
            self.sub_wins += finish_sub
            self.dec_wins += "DECISION" in m
        elif score == 0:
            self.ko_losses += finish_ko
            self.sub_losses += finish_sub
        if mine and theirs:
            self.secs += secs
            for k, v in mine.items():
                self.tot[k] += v
            for k in ("sig_l", "sig_a", "td_l", "td_a", "kd", "ctrl"):
                self.tot["opp_" + k] += theirs.get(k, 0)
            mins = secs / 60.0
            now = {"slpm": mine["sig_l"] / mins, "sapm": theirs["sig_l"] / mins,
                   "td15": mine["td_l"] * 15 / mins, "ctrl": mine["ctrl"] / secs,
                   "kd15": mine["kd"] * 15 / mins}
            for k, v in now.items():
                self.ema[k] = v if k not in self.ema else EMA * v + (1 - EMA) * self.ema[k]
        self.opp_ratings.append(opp.rating)


def _score(outcome):
    """Fighter A's score from 'W/L', 'L/W', 'D/D'; None for no contests."""
    return {"W/L": 1.0, "L/W": 0.0, "D/D": 0.5}.get(outcome)


def _weight_code(wc):
    wc = wc.lower()
    order = ["strawweight", "flyweight", "bantamweight", "featherweight", "lightweight",
             "welterweight", "middleweight", "light heavyweight", "heavyweight"]
    code = next((i for i, w in reversed(list(enumerate(order))) if w in wc), np.nan)
    return code


def pair_row(fa, fb, extra):
    """Model inputs for A against B: differences plus a few raw values."""
    row = dict(extra)
    for k in fa:
        a, b = fa[k], fb[k]
        row["d_" + k] = a - b if (a == a and b == b) else np.nan
    for k in ("n", "age", "rating", "rd", "layoff", "slpm", "sapm"):
        row["a_" + k] = fa[k]
        row["b_" + k] = fb[k]
    return row


def build(fights, bios):
    """Training rows (both orientations) and the records as they stand now."""
    recs = defaultdict(Record)
    rows = []
    for f in fights.itertuples(index=False):
        ka, kb = _key(f.a), _key(f.b)
        ra, rb = recs[ka], recs[kb]
        fa = ra.features(f.date, bios.get(ka))
        fb = rb.features(f.date, bios.get(kb))
        s = _score(f.outcome)
        extra = dict(women=float("women" in f.weightclass.lower()), title=float("title" in f.weightclass.lower()),
                     five=float(f.rounds_sched == 5), weight=_weight_code(f.weightclass))
        if s in (0.0, 1.0):
            rows.append(dict(pair_row(fa, fb, extra), y=s, date=f.date, fid=len(rows) // 2, swap=0, ka=ka, kb=kb))
            rows.append(dict(pair_row(fb, fa, extra), y=1 - s, date=f.date, fid=len(rows) // 2, swap=1, ka=kb, kb=ka))
        if s is None:
            continue
        mine_a = _flat(f.sa)
        mine_b = _flat(f.sb)
        old_a = (ra.rating, ra.rd, ra.vol)
        ra.rating, ra.rd, ra.vol = glicko_update(ra.rating, ra.rd, ra.vol, rb.rating, rb.rd, s)
        rb.rating, rb.rd, rb.vol = glicko_update(rb.rating, rb.rd, rb.vol, old_a[0], old_a[1], 1 - s)
        ra.add(f.date, mine_a, mine_b, s, f.method, f.secs, f.rounds_sched == 5, rb)
        rb.add(f.date, mine_b, mine_a, 1 - s, f.method, f.secs, f.rounds_sched == 5, ra)
    data = pd.DataFrame(rows)
    return data, recs


def _flat(s):
    if not s:
        return None
    return {k: float(v) for k, v in s.items()}


def feature_columns(data):
    return [c for c in data.columns if c not in ("y", "date", "fid", "swap", "ka", "kb")]


# ── Training and prediction ─────────────────────────────────────────────────

def train(data, trees=TREES, until=None):
    import lightgbm as lgb
    d = data if until is None else data[data.date < until]
    cols = feature_columns(d)
    bst = lgb.train(PARAMS, lgb.Dataset(d[cols], d.y.values), num_boost_round=trees)
    return bst, cols


def predict_rows(bst, cols, rows):
    """Chance the first fighter wins, averaged over both orientations."""
    return bst.predict(rows[cols])


def symmetric(p_ab, p_ba):
    return 0.5 * (p_ab + (1.0 - p_ba))


class Live:
    """Everything needed to price an upcoming fight, built from the latest data."""

    def __init__(self, source=None):
        raw = load_raw(source)
        self.fights = fights_table(raw)
        self.bios = bios_table(raw)
        self.data, self.recs = build(self.fights, self.bios)
        self.bst, self.cols = train(self.data)
        self.latest = self.fights.date.max()
        log.info("UFC model trained: fights=%s rows=%s latest=%s", len(self.fights), len(self.data),
                 self.latest.date())

    def known(self, name):
        return _key(name) in self.recs

    def history(self, name):
        rec = self.recs.get(_key(name))
        return rec.n if rec else 0

    def win_chance(self, name_a, name_b, when=None, weightclass="", five_rounds=False):
        """Chance fighter A beats fighter B (both orientations averaged)."""
        when = pd.Timestamp(when or date.today())
        ka, kb = _key(name_a), _key(name_b)
        fa = (self.recs.get(ka) or Record()).features(when, self.bios.get(ka))
        fb = (self.recs.get(kb) or Record()).features(when, self.bios.get(kb))
        extra = dict(women=float("women" in weightclass.lower()), title=float("title" in weightclass.lower()),
                     five=float(five_rounds), weight=_weight_code(weightclass))
        rows = pd.DataFrame([pair_row(fa, fb, extra), pair_row(fb, fa, extra)])
        p = self.bst.predict(rows[self.cols])
        return float(symmetric(p[0], p[1])), fa, fb


# ── Judging ─────────────────────────────────────────────────────────────────

def _ll(y, p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def evaluate(data, train_until="2023-01-01", valid_until="2024-01-01"):
    """Train before train_until, pick trees on the next year, judge the rest."""
    import lightgbm as lgb
    cols = feature_columns(data)
    tr = data[data.date < train_until]
    va = data[(data.date >= train_until) & (data.date < valid_until)]
    te = data[data.date >= valid_until]
    bst = lgb.train(PARAMS, lgb.Dataset(tr[cols], tr.y.values), num_boost_round=3000,
                    valid_sets=[lgb.Dataset(va[cols], va.y.values)],
                    callbacks=[lgb.early_stopping(200, verbose=False)])
    out = {"best_trees": bst.best_iteration}
    full = data[data.date < valid_until]
    bst2 = lgb.train(PARAMS, lgb.Dataset(full[cols], full.y.values), num_boost_round=bst.best_iteration)
    t = te[te.swap == 0]
    s = te[te.swap == 1]
    p = symmetric(bst2.predict(t[cols]), bst2.predict(s[cols]))
    y = t.y.values
    g = 1 / (1 + 10 ** (-t.d_rating.values / 400))
    out.update(test_fights=len(t), model_logloss=_ll(y, p), model_pick_right=float(np.mean((p > 0.5) == (y == 1))),
               rating_logloss=_ll(y, g), rating_pick_right=float(np.mean((g > 0.5) == (y == 1))),
               coin_logloss=_ll(y, np.full(len(y), 0.5)))
    imp = pd.Series(bst2.feature_importance("gain"), index=cols).sort_values(ascending=False)
    out["top_inputs"] = (100 * imp / imp.sum()).round(1).head(12).to_dict()
    return out
