"""New in-repo UFC model vs the old model vs the bookies (read-only).

The new model (mma_model.py) is trained on fights before CUTOFF only, then
priced on every finished fight since. The old model's saved pick and the
bookies' average price (margin removed) are lined up on the same fights.
Lower log loss is better; the bookies are the bar to clear.
"""
import numpy as np
import pandas as pd

import mma_model as mm
from audit.run_audit import emit
from mma_name_utils import normalize_name

CUTOFF = pd.Timestamp("2026-05-01")


def _ll(y, p):
    p = np.clip(np.asarray(p, float), 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def run(ctx):
    raw = mm.load_raw()
    data, _ = mm.build(mm.fights_table(raw), mm.bios_table(raw))
    ev = mm.evaluate(data)
    emit("ufcmodel.history", f"unseen 2024+ fights={ev['test_fights']} pick_right={ev['model_pick_right']:.3f} "
                             f"logloss={ev['model_logloss']:.4f} (coin 0.6931) trees={ev['best_trees']}")
    emit("ufcmodel.history", "top inputs: " + ", ".join(f"{k}={v}" for k, v in ev["top_inputs"].items()))

    bst, cols = mm.train(data, until=CUTOFF)
    te = data[data.date >= CUTOFF]
    a, b = te[te.swap == 0].reset_index(drop=True), te[te.swap == 1].reset_index(drop=True)
    p_new = mm.symmetric(bst.predict(a[cols]), bst.predict(b[cols]))
    new = {}
    for i, r in a.iterrows():
        new[(r.date.date(), frozenset((r.ka, r.kb)))] = (r.ka, p_new[i])

    db = ctx.df("""
        SELECT e.date, f.fighter_1_name f1, f.fighter_2_name f2, f.winner_name w,
               p.f1_win_probability p_old, p.model_version v
        FROM mma_fights f JOIN mma_events e ON e.id = f.event_id
        LEFT JOIN mma_predictions p ON p.fight_id = f.id
        WHERE e.is_completed AND f.winner_name IS NOT NULL AND e.date >= %s
    """, (CUTOFF.date(),))
    odds = ctx.df("""
        SELECT LOWER(fighter_name) n, commence_time::date d, AVG(odds) o
        FROM mma_fight_odds WHERE odds > 1 GROUP BY 1, 2
    """)
    price = {}
    for r in odds.itertuples(index=False):
        price.setdefault(r.n, []).append((r.d, float(r.o)))

    def odds_for(name, day):
        for d, o in price.get(str(name).lower(), []):
            if abs((d - day).days) <= 1:
                return o
        return None

    rows = []
    for r in db.itertuples(index=False):
        k1, k2 = normalize_name(r.f1), normalize_name(r.f2)
        if r.w not in (r.f1, r.f2):
            continue
        hit = None
        for shift in (0, -1, 1):
            hit = new.get((r.date + pd.Timedelta(days=shift), frozenset((k1, k2))))
            if hit:
                break
        if not hit:
            continue
        first, p = hit
        pn = p if first == k1 else 1 - p
        o1, o2 = odds_for(r.f1, r.date), odds_for(r.f2, r.date)
        pm = (1 / o1) / (1 / o1 + 1 / o2) if o1 and o2 else np.nan
        po = float(r.p_old) if r.p_old is not None and r.v not in (None, "fallback_5050") else np.nan
        po = po / 100 if po > 1 else po
        rows.append(dict(y=float(r.w == r.f1), new=pn, old=po, mkt=pm, o1=o1, o2=o2))
    d = pd.DataFrame(rows)
    emit("ufcmodel.2026", f"finished fights since {CUTOFF.date()} matched={len(d)} of {len(db)}")
    if d.empty:
        return
    out = []
    for name, m in (("all matched", d.new.notna()), ("with old model", d.old.notna()),
                    ("with prices", d.mkt.notna()), ("all three", d.old.notna() & d.mkt.notna())):
        s = d[m]
        if not len(s):
            continue
        row = [name, len(s)]
        for c in ("new", "old", "mkt"):
            v = s[c]
            ok = v.notna().all()
            row += [round(100 * float(np.mean((v > 0.5) == (s.y == 1))), 1) if ok else None,
                    round(_ll(s.y.values, v.values), 4) if ok else None]
        out.append(tuple(row))
    ctx.table("ufcmodel.compare", ["fights", "n", "new_pick%", "new_ll", "old_pick%", "old_ll",
                                   "bookie_fav%", "bookie_ll"], out)

    s = d[d.mkt.notna()]
    if len(s):
        for w in (0.15, 0.3, 0.5):
            emit("ufcmodel.blend", f"{int(w * 100)}% new model + bookies: logloss="
                                   f"{_ll(s.y.values, (w * s.new + (1 - w) * s.mkt).values):.4f} "
                                   f"(bookies alone {_ll(s.y.values, s.mkt.values):.4f}, n={len(s)})")
        bets = []
        for r in s.itertuples(index=False):
            for p, o, won in ((r.new, r.o1, r.y == 1), (1 - r.new, r.o2, r.y == 0)):
                if o and p * o >= 1.10:
                    bets.append((o - 1) if won else -1.0)
        if bets:
            emit("ufcmodel.value", f"new model chance x price >= 1.10: bets={len(bets)} "
                                   f"profit={sum(bets):+.1f}u roi={100 * np.mean(bets):+.1f}%")
    emit("ufcmodel", "done")
