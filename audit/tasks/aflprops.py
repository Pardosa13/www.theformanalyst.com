"""Can the AFL player-prop model be fixed by correcting its overconfidence?

Settled picks (afl_model_selections) hold the model's chance, the price and
the result. Fitted on the first 60% of picks by date, judged on the rest:

  market   the bookmaker's chance with its margin taken out where the
           opposite side (Over/Under, same line and bookmaker) is known
  model    the model's chance as stored
  calib    the model's chance corrected on earlier picks
  blend    model and market combined, weights fitted on earlier picks

Log loss: lower is better. A bet is any pick whose corrected chance beats
the price by the margin shown.
"""
import numpy as np
import pandas as pd
from scipy.optimize import minimize

from audit.run_audit import emit

SQL = """
select s.id, s.commence_time, s.round, s.market, s.line_type, s.line, s.odds, s.model_prob,
       (s.result = 'win')::int as won, s.profit_units,
       (select max(p.odds) from afl_player_props p
         where p.event_id = s.event_id and p.bookmaker = s.bookmaker and p.market = s.market
           and lower(p.player_name) = lower(s.player_name) and p.line = s.line
           and p.line_type = case s.line_type when 'Over' then 'Under' else 'Over' end) as opp_odds
from afl_model_selections s
where s.settled_at is not null and s.result in ('win', 'loss') and s.odds > 1 and s.model_prob is not null
order by s.commence_time, s.id
"""


def _logit(p):
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def _fit(X, y):
    def nll(w):
        z = X @ w
        return np.sum(np.logaddexp(0, z) - y * z) + 1e-3 * np.sum(w[1:] ** 2)
    return minimize(nll, np.zeros(X.shape[1]), method="BFGS").x


def _pred(X, w):
    return 1 / (1 + np.exp(-(X @ w)))


def _ll(p, y):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def run(ctx):
    d = ctx.df(SQL)
    d["odds"] = d.odds.astype(float)
    d["opp_odds"] = pd.to_numeric(d.opp_odds, errors="coerce")
    raw = 1 / d.odds
    has_opp = d.opp_odds > 1
    d["mkt"] = np.where(has_opp, raw / (raw + 1 / d.opp_odds.where(has_opp, 1)), raw)
    d["model"] = d.model_prob.astype(float) / 100.0
    d["won"] = d.won.astype(float)
    emit("aflprops", f"settled picks={len(d)} with opposite side={int(has_opp.sum())} "
                     f"({100 * has_opp.mean():.0f}%) from {d.commence_time.min()} to {d.commence_time.max()}")
    cut = int(len(d) * 0.6)
    rows = []
    bets = []
    for side in ("Over", "Under", "both"):
        m = (d.line_type == side).values if side != "both" else np.ones(len(d), bool)
        tr = m.copy(); tr[cut:] = False
        te = m.copy(); te[:cut] = False
        if tr.sum() < 200 or te.sum() < 100:
            emit("aflprops", f"{side}: too few picks train={int(tr.sum())} test={int(te.sum())}")
            continue
        lm, lk = _logit(d.model.values), _logit(d.mkt.values)
        y = d.won.values
        Xc = np.column_stack([np.ones(len(d)), lm])
        Xb = np.column_stack([np.ones(len(d)), lm, lk])
        wc, wb = _fit(Xc[tr], y[tr]), _fit(Xb[tr], y[tr])
        pc, pb = _pred(Xc, wc), _pred(Xb, wb)
        rows.append((side, int(te.sum()), round(100 * y[te].mean(), 1),
                     round(_ll(d.mkt.values[te], y[te]), 4), round(_ll(d.model.values[te], y[te]), 4),
                     round(_ll(pc[te], y[te]), 4), round(_ll(pb[te], y[te]), 4),
                     f"model={wb[1]:.2f} market={wb[2]:.2f}"))
        ret = np.where(y == 1, d.odds.values - 1, -1.0)
        for name, p in (("calib", pc), ("blend", pb)):
            for th in (1.0, 1.05, 1.10, 1.20):
                sel = te & (p * d.odds.values > th)
                if sel.sum() >= 20:
                    bets.append((side, name, th, int(sel.sum()), round(100 * y[sel].mean(), 1),
                                 round(100 * ret[sel].mean(), 1)))
    ctx.table("aflprops.logloss_test", ["side", "test_picks", "win_pct", "market", "model", "calib", "blend",
                                        "blend_weights"], rows)
    ctx.table("aflprops.bets_test", ["side", "prob", "chance_x_price_over", "bets", "win_pct", "roi_pct"], bets)
    emit("aflprops", "done")
