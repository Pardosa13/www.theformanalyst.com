"""Does any pre-race signal know something the Betfair market doesn't?

Fits a race-level (conditional logit) model on Feb-Jun:
    strength = a*log(market prob) + b*log(signal prob)
and checks on Jul-Sep whether it beats the market alone (log loss) and
whether backing its overlays at BSP makes money. b near 0 = no new info.
The market here is the final BSP, so this is an upper bound on what a
pre-race bettor could get.
"""
import numpy as np
import pandas as pd
from scipy.optimize import minimize

from audit.run_audit import emit
from audit.tasks import frame
from audit.tasks.patterns import SPLIT


def _norm(d, col):
    v = d[col].astype(float).clip(lower=1e-4)
    return v / v.groupby(d.race_id).transform("sum")


def fit(X, race, won):
    codes, idx = np.unique(race, return_inverse=True)

    def nll(w):
        s = X @ w
        s = s - np.bincount(idx, weights=np.zeros_like(s))  # keep shape
        m = pd.Series(s).groupby(idx).transform("max").values
        e = np.exp(s - m)
        den = np.bincount(idx, weights=e)[idx]
        return -np.sum(won * (s - m - np.log(den)))
    w0 = np.zeros(X.shape[1]); w0[0] = 1.0
    res = minimize(nll, w0, method="BFGS")
    return res.x, res.fun


def probs(X, w, race):
    s = X @ w
    s = s - pd.Series(s).groupby(race).transform("max").values
    e = np.exp(s)
    return e / pd.Series(e).groupby(race).transform("sum").values


def run(ctx):
    d = frame.build(ctx)
    d = d[d.mkt_p.notna() & (d.date >= pd.Timestamp("2026-02-01"))].copy()
    d["pfai_p"] = np.where(d.pfai_price > 1, 1 / d.pfai_price, np.nan)
    d["assessed_p"] = np.where(d.assessed_price > 1, 1 / d.assessed_price, np.nan)
    d["sp_p"] = np.where(d.sp > 1, 1 / d.sp, np.nan)
    signals = ["a_prob", "pfai_p", "assessed_p", "sp_p", "ml_prob"]
    for sig in signals:
        # only races where every runner has the signal
        ok = d[sig].notna().groupby(d.race_id).transform("all")
        x = d[ok & d[sig].notna()].copy()
        if x.race_id.nunique() < 300:
            emit("edge", f"{sig}: too few races ({x.race_id.nunique()})")
            continue
        x["sig_n"] = _norm(x, sig)
        X = np.column_stack([np.log(x.mkt_p), np.log(x.sig_n)])
        tr = (x.date < SPLIT).values
        te = ~tr
        won = x.won.values.astype(float)
        if tr.sum() < 1000 or te.sum() < 1000:
            emit("edge", f"{sig}: split too thin train={tr.sum()} test={te.sum()}")
            continue
        w, _ = fit(X[tr], x.race_id.values[tr], won[tr])
        p_te = probs(X[te], w, x.race_id.values[te])
        mk = x.mkt_p.values[te]
        sg = x.sig_n.values[te]
        n_races = x[te].race_id.nunique()
        ll = lambda p: -np.sum(won[te] * np.log(np.clip(p, 1e-9, 1))) / n_races
        emit("edge", f"{sig}: races train={x[tr].race_id.nunique()} test={n_races} weights market={w[0]:.3f} signal={w[1]:.3f} | test logloss/race market={ll(mk):.4f} signal_alone={ll(sg):.4f} combined={ll(p_te):.4f}")
        b = x.bsp.values[te]
        r = x.ret_bsp.values[te]
        rows = []
        for th in (1.0, 1.05, 1.1, 1.2, 1.3, 1.5):
            m = p_te * b * 0.92 > th
            for lo, hi in ((1, 5), (5, 15), (15, 1000)):
                mm = m & (b >= lo) & (b < hi)
                if mm.sum() >= 30:
                    rows.append((th, f"{lo}-{hi}", int(mm.sum()), round(100 * r[mm].mean(), 1), int(won[te][mm].sum())))
            if m.sum():
                rows.append((th, "all", int(m.sum()), round(100 * r[m].mean(), 1), int(won[te][m].sum())))
        ctx.table(f"edge.{sig}.overlays_test", ["edge_min", "bsp_band", "bets", "roi_bsp", "wins"], rows)
        # top pick of the signal, test period
        t = x[te].copy()
        t["rk"] = t.groupby("race_id")["sig_n"].rank(ascending=False, method="first")
        tp = t[t.rk == 1]
        emit("edge", f"{sig}: test top pick bets={len(tp)} sr={100*tp.won.mean():.1f}% roi_bsp={100*tp.ret_bsp.mean():.1f}% roi_sp={100*tp.ret_sp.mean():.1f}% avg_bsp={tp.bsp.mean():.2f}")
