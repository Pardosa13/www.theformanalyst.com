"""Which maths prices AFL player stats best? Judged on 2019-2026 games, no odds needed.

For every player game with 8+ earlier games that season, each method gives a
chance that the stat beats a line, using ONLY earlier games. Lines are set
around the player's own average (avg - 2.5 ... avg + 2.5, on the .5), the way
bookmakers set them. Lower log loss and a calibration close to the truth win.

  current   the site's method today: 60% the player's hit rate over the line,
            40% a bell curve (normal, spread from his games, at least 2)
  poisson   counts with variance = mean
  negbin    counts with the player's own extra spread (negative binomial)
  negbin60  60% hit rate + 40% negative binomial
"""
import math

import numpy as np
import pandas as pd
from scipy import stats

from audit.run_audit import emit

STATS = ("disposals", "kicks", "handballs", "marks", "tackles", "goals")
MIN_PRIOR = 8
OFFSETS = (-2.5, -1.5, -0.5, 0.5, 1.5, 2.5)


def _current(vals, mean, line, stat):
    sd = max(np.std(vals, ddof=1) if len(vals) > 1 else math.sqrt(mean), 2.0)
    if stat == "goals":
        p_model = 1 - stats.poisson.cdf(math.floor(line), max(mean, 0.01))
    else:
        p_model = 1 - stats.norm.cdf(line, mean, sd)
    hit = (np.sum(vals > line) + 1.0) / (len(vals) + 2.0)
    w = 0.60 if len(vals) >= 8 else 0.35
    return w * hit + (1 - w) * p_model


def _negbin(vals, mean, line):
    var = np.var(vals, ddof=1) if len(vals) > 1 else mean
    mean = max(mean, 0.01)
    k = math.floor(line)
    if var <= mean * 1.0001:
        return 1 - stats.poisson.cdf(k, mean)
    r = mean * mean / (var - mean)
    p = r / (r + mean)
    return 1 - stats.nbinom.cdf(k, r, p)


def run(ctx):
    cols = ", ".join(STATS)
    d = ctx.df(f"""
        SELECT player_id, season, match_date, time_on_ground_percentage AS tog, {cols}
        FROM afl_player_stats
        WHERE player_id IS NOT NULL AND match_date IS NOT NULL AND season >= 2019
        ORDER BY player_id, match_date
    """)
    d = d[(d.tog.isna()) | (d.tog >= 50)]
    emit("afldist", f"player games={len(d)} players={d.player_id.nunique()} seasons={sorted(d.season.unique().tolist())}")
    rng = np.random.default_rng(3)
    out = []
    for stat in STATS:
        rows = {m: [] for m in ("current", "poisson", "negbin", "negbin60")}
        ys = []
        for (pid, season), g in d.groupby(["player_id", "season"], sort=False):
            v = g[stat].astype(float).to_numpy()
            ok = ~np.isnan(v)
            v = v[ok]
            for i in range(MIN_PRIOR, len(v)):
                prior = v[:i]
                mean = prior.mean()
                # one line per game, around the player's own average
                line = math.floor(mean) + 0.5 + rng.choice(OFFSETS)
                if stat == "goals":
                    line = rng.choice((0.5, 1.5, 2.5))
                if line <= 0:
                    continue
                y = float(v[i] > line)
                ys.append(y)
                rows["current"].append(_current(prior, mean, line, stat))
                rows["poisson"].append(1 - stats.poisson.cdf(math.floor(line), max(mean, 0.01)))
                nb = _negbin(prior, mean, line)
                rows["negbin"].append(nb)
                hit = (np.sum(prior > line) + 1.0) / (len(prior) + 2.0)
                rows["negbin60"].append(0.6 * hit + 0.4 * nb)
        y = np.array(ys)
        if not len(y):
            continue
        for m, ps in rows.items():
            p = np.clip(np.array(ps), 1e-4, 1 - 1e-4)
            ll = float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
            hi = p >= 0.65
            out.append((stat, m, len(y), round(ll, 4),
                        round(100 * p.mean(), 1), round(100 * y.mean(), 1),
                        int(hi.sum()), round(100 * p[hi].mean(), 1) if hi.any() else None,
                        round(100 * y[hi].mean(), 1) if hi.any() else None))
    ctx.table("afldist.methods", ["stat", "method", "lines", "log_loss", "said_over_pct", "actual_over_pct",
                                  "said_65plus", "said_65plus_avg", "actual_when_said_65plus"], out)
    emit("afldist", "done")
