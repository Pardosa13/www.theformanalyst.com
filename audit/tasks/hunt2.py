"""Wide edge hunt: every signal we hold, alone and in pairs, at Betfair prices.

Bet types: back win, lay win, back place, lay place (Betfair BSP, 8% commission).
Signals: every analyzer note component, every track / jockey / trainer / sire
with enough runners, run number in the prep (1st-6th up), horses for courses
(track and track+distance records), first/second-up records, going records,
jockey changes and upgrades, market size, price moves before the jump,
plus every condition of the first hunt (prices, ranks, ratings, barriers,
distance, field, age, sex, weight, days since, last start).

Search Feb-Jun, test Jul-Sep (never used to pick). A pattern is "proven" only if
it passes a Benjamini-Hochberg luck test on the test months. The same hunt on
made-up results (winners and placings drawn from the market's own odds) shows
how many "proven" patterns pure luck produces.
"""
import math
import sys

import numpy as np
import pandas as pd

from audit.run_audit import emit
from audit.tasks import bf_files, frame
from audit.tasks.patterns import conditions as base_conditions

sys.path.insert(0, "/app")
SPLIT = pd.Timestamp("2026-07-01")
MONTHS = [(2026, m) for m in range(2, 10)]
MIN_N = 300           # runners over the whole period for a single condition
MIN_SEARCH = 150      # bets in the search months
MIN_TEST = 75         # bets in the test months
Q = 0.10              # at most 1 in 10 proven patterns expected to be luck


def notes_conditions(ctx, d):
    try:
        from notes_parsing import parse_notes_components
    except Exception as e:
        emit("hunt2", f"#ERROR notes parser unavailable: {e}")
        return {}
    notes = ctx.df("""select distinct on (p.horse_id) p.horse_id, p.notes from predictions p
                      where p.notes is not null order by p.horse_id, p.id desc""")
    comp = {}
    for hid, txt in notes.itertuples(index=False):
        try:
            for name in parse_notes_components(txt):
                comp.setdefault(name, set()).add(hid)
        except Exception:
            continue
    out = {}
    hid = d.horse_id.values
    for name, ids in comp.items():
        m = np.isin(hid, list(ids))
        if m.sum() >= MIN_N:
            out[f"an: {name}"] = m
    emit("hunt2", f"analyzer components with {MIN_N}+ runners: {len(out)} (of {len(comp)})")
    return out


def extra_conditions(d):
    c = {}

    def add(name, m):
        m = np.asarray(m, dtype=bool)
        if m.sum() >= MIN_N:
            c[name] = m
    for col, label in (("track", "track"), ("jockey", "jockey"), ("trainer", "trainer"), ("sire", "sire")):
        for v, n in d[col].value_counts().items():
            if n >= MIN_N and v:
                add(f"{label} {v}", d[col] == v)
    for k in range(1, 6):
        add(f"{k} up", d.nth_up == k)
    add("6+ up", d.nth_up >= 6)
    tr_pct = d.rec_track_w / d.rec_track_n.where(d.rec_track_n > 0)
    add("never raced at track", d.rec_track_n == 0)
    add("track winner", d.rec_track_w >= 1)
    add("track specialist (3+ starts, 33%+ wins)", (d.rec_track_n >= 3) & (tr_pct >= 0.33))
    add("track+distance winner", d.rec_trackdist_w >= 1)
    add("distance winner", d.rec_dist_w >= 1)
    add("never won at distance (3+ tries)", (d.rec_dist_n >= 3) & (d.rec_dist_w == 0))
    up1 = d.rec_1up_w / d.rec_1up_n.where(d.rec_1up_n > 0)
    up2 = d.rec_2up_w / d.rec_2up_n.where(d.rec_2up_n > 0)
    add("1st up, good 1st-up record (2+ starts, 33%+)", (d.nth_up == 1) & (d.rec_1up_n >= 2) & (up1 >= 0.33))
    add("1st up, never won 1st up (3+ tries)", (d.nth_up == 1) & (d.rec_1up_n >= 3) & (d.rec_1up_w == 0))
    add("2nd up, good 2nd-up record", (d.nth_up == 2) & (d.rec_2up_n >= 2) & (up2 >= 0.33))
    wet = d.track_condition.fillna("").str.lower().str.contains("soft|heavy")
    wet_n = d.rec_soft_n.fillna(0) + d.rec_heavy_n.fillna(0)
    wet_w = d.rec_soft_w.fillna(0) + d.rec_heavy_w.fillna(0)
    add("wet track, proven wet winner", wet & (wet_w >= 1))
    add("wet track, never won wet (3+ tries)", wet & (wet_n >= 3) & (wet_w == 0))
    add("wet track, unraced wet", wet & (wet_n == 0))
    jch = d.jockey.fillna("").str.lower() != d.last_jockey
    add("jockey change", jch & (d.last_jockey != ""))
    tr = d[d.date < SPLIT]
    js = tr.groupby(tr.jockey.fillna("").str.lower())["won"].agg(["sum", "count"])
    prior = tr.won.mean()
    jsr = (js["sum"] + 50 * prior) / (js["count"] + 50)
    up = d.jockey.fillna("").str.lower().map(jsr).fillna(prior) - d.last_jockey.map(jsr).fillna(prior)
    add("jockey upgrade (+3% strike)", jch & (up >= 0.03))
    add("jockey downgrade (-3% strike)", jch & (up <= -0.03))
    top = set(jsr.sort_values(ascending=False).head(20).index)
    add("top-20 jockey booked, wasn't on last start", jch & d.jockey.fillna("").str.lower().isin(top) & ~d.last_jockey.isin(top))
    wc = d.weight - d.last_weight
    add("weight down 2kg+", wc <= -2)
    add("weight up 2kg+", wc >= 2)
    q = d.groupby("race_id")["market_vol"].transform("first")
    try:
        bins = pd.qcut(q, 5, labels=False, duplicates="drop")
        for k in sorted(pd.Series(bins).dropna().unique()):
            add(f"market size quintile {int(k) + 1} of 5", bins == k)
    except Exception:
        pass
    mv = np.log(d.pre_wap.where(d.pre_wap > 1) / d.bsp)
    add("firmed: BSP 10%+ shorter than pre-jump average", mv >= 0.1)
    add("drifted: BSP 10%+ longer than pre-jump average", mv <= -0.1)
    add("steady pre-jump price", mv.abs() < 0.03)
    return c


def _stats(M, r, v):
    n = M.T @ np.ones(M.shape[0], np.float32)
    N = M.T @ M
    P = (M * r[:, None]).T @ M
    V = (M * v[:, None]).T @ M
    return n, N, P, V


def scan(d, names, M, ret, var, label):
    """Singles and pairs: search months then test months."""
    ok = np.isfinite(ret)
    tr = (d.date < SPLIT).values & ok
    te = (d.date >= SPLIT).values & ok
    r = np.nan_to_num(ret).astype(np.float32)
    v = np.nan_to_num(var).astype(np.float32)
    Ms, Mt = M[tr], M[te]
    _, Ns, Ps, Vs = _stats(Ms, r[tr], v[tr])
    _, Nt, Pt, Vt = _stats(Mt, r[te], v[te])
    with np.errstate(divide="ignore", invalid="ignore"):
        zs = Ps / np.sqrt(Vs)
        roi_s = Ps / Ns
        zt = Pt / np.sqrt(Vt)
        roi_t = Pt / Nt
    k = len(names)
    iu = np.triu_indices(k)                      # diagonal = singles
    mask = (Ns[iu] >= MIN_SEARCH) & (roi_s[iu] > 0) & (zs[iu] > 2.0) & (Nt[iu] >= MIN_TEST)
    a, b = iu[0][mask], iu[1][mask]
    tested = int(((Ns[iu] >= MIN_SEARCH)).sum())
    zt_c = zt[a, b]
    p = 0.5 * np.array([math.erfc(z / math.sqrt(2)) for z in np.nan_to_num(zt_c, nan=-9)])
    order = np.argsort(p)
    m = len(p)
    passed = np.zeros(m, bool)
    if m:
        thresh = Q * (np.arange(1, m + 1)) / m
        below = p[order] <= thresh
        if below.any():
            passed[order[: np.max(np.nonzero(below)) + 1]] = True
    held = (roi_t[a, b] > 0) & (zt_c > 1.0)
    rows = []
    for i in np.argsort(-np.nan_to_num(zt_c, nan=-9))[:40]:
        x, y = a[i], b[i]
        nm = names[x] if x == y else f"{names[x]} + {names[y]}"
        rows.append((nm, int(Ns[x, y]), round(100 * roi_s[x, y], 1), round(float(zs[x, y]), 2),
                     int(Nt[x, y]), round(100 * roi_t[x, y], 1), round(float(zt_c[i]), 2), bool(passed[i])))
    return dict(label=label, tested=tested, passed_search=int(mask.sum()), held=int(held.sum()),
                proven=int(passed.sum()), rows=rows)


def run(ctx):
    d = frame.build(ctx)
    d = d[d.mkt_p.notna() & (d.date >= pd.Timestamp("2026-02-01"))].reset_index(drop=True)
    d = bf_files.attach(d, MONTHS)
    conds = {}
    conds.update(base_conditions(d))
    conds.update(extra_conditions(d))
    conds.update(notes_conditions(ctx, d))
    names = list(conds)
    M = np.column_stack([conds[n] for n in names]).astype(np.float32)
    emit("hunt2", f"runners={len(d)} races={d.race_id.nunique()} single conditions={len(names)} pairs={len(names) * (len(names) - 1) // 2}")

    b, pb = d.bsp.values, d.place_bsp.values
    bets = {
        "back win": (d.ret_bsp.values, (b - 1) * 0.92),
        "lay win": (d.ret_lay.values, (b - 1)),
        "back place": (d.ret_place.values, (pb - 1) * 0.92),
        "lay place": (d.ret_place_lay.values, (pb - 1)),
    }
    emit("hunt2", "overall ROI: " + ", ".join(f"{k}={100 * np.nanmean(v[0]):.2f}%" for k, v in bets.items()))
    summary = []
    for label, (ret, var) in bets.items():
        res = scan(d, names, M, ret, var, label)
        emit(f"hunt2.{label}", f"pairs+singles tested={res['tested']} passed search={res['passed_search']} held in test={res['held']} PROVEN (luck-adjusted)={res['proven']}")
        ctx.table(f"hunt2.{label}.top_test", ["pattern", "n_search", "roi_search", "z_search", "n_test", "roi_test", "z_test", "proven"], res["rows"])
        summary.append([label, res["held"], res["proven"]])

    # No-edge world: finishing order drawn from the market's odds (Plackett-Luce via Gumbel).
    rng = np.random.default_rng(5)
    places = np.where(d.field >= 8, 3, np.where(d.field >= 5, 2, 1))
    for k in range(2):
        g = np.log(d.mkt_p.values) + rng.gumbel(size=len(d))
        rank = pd.Series(-g).groupby(d.race_id.values).rank(method="first").values
        won = rank == 1
        placed = rank <= places
        fake = {
            "back win": (np.where(won, (b - 1) * 0.92, -1.0), (b - 1) * 0.92),
            "lay win": (np.where(won, -(b - 1), 0.92), (b - 1)),
            "back place": (np.where(np.isnan(pb), np.nan, np.where(placed, (pb - 1) * 0.92, -1.0)), (pb - 1) * 0.92),
            "lay place": (np.where(np.isnan(pb), np.nan, np.where(placed, -(pb - 1), 0.92)), (pb - 1)),
        }
        for i, (label, (ret, var)) in enumerate(fake.items()):
            res = scan(d, names, M, ret, var, label)
            summary[i].append(f"null{k}: held={res['held']} proven={res['proven']}")
    for row in summary:
        emit("hunt2.SUMMARY", f"{row[0]}: real held={row[1]} proven={row[2]} | " + " | ".join(row[3:]))
