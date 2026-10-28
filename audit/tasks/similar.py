"""Find every pattern that looks like the Place Bet rule, and count how many
luck alone would produce.

"Looks like it" (the Place Bet rule: 537 bets, +35%, 7 of 8 months up):
  - at least MIN_BETS bets over the whole period
  - ROI of at least MIN_ROI
  - profit in at least MIN_UP_MONTHS of the months where it had 10+ bets
  - profit in both halves of the period
  - strength z of at least MIN_Z

Every single condition and pair the sweep uses is checked, for all four bet
types. The same check is run on three made-up worlds where results are drawn
from the market's own prices (no edge). If the real data has about as many
"look-alikes" as the made-up worlds, they are luck.
"""
import numpy as np

from audit.run_audit import emit
from audit.tasks import sweep3

MIN_BETS = 300
MIN_ROI = 0.10
MIN_UP_MONTHS = 6
MIN_MONTH_BETS = 10
MIN_Z = 3.0


def _scan(d, M, ret, var):
    var = np.asarray(var, float)
    ok = np.isfinite(ret) & np.isfinite(var) & (var > 0) & (var < 1e4)
    r = np.where(ok, ret, 0.0)
    v = np.where(ok, var, 0.0)
    months = d.date.dt.to_period("M").astype(str).values
    stats = sweep3._month_stats(M, r, v, months, ok)
    order = sorted(stats)
    k = M.shape[1]
    iu = np.triu_indices(k)
    Nm = np.stack([stats[m][0][iu] for m in order])     # months x patterns
    Pm = np.stack([stats[m][1][iu] for m in order])
    Vm = np.stack([stats[m][2][iu] for m in order])
    N, P, V = Nm.sum(0), Pm.sum(0), Vm.sum(0)
    with np.errstate(divide="ignore", invalid="ignore"):
        roi = P / N
        z = P / np.sqrt(V)
    active = Nm >= MIN_MONTH_BETS
    up = (active & (Pm > 0)).sum(0)
    h = len(order) // 2
    first, second = Pm[:h].sum(0), Pm[h:].sum(0)
    hit = ((N >= MIN_BETS) & (roi >= MIN_ROI) & (up >= MIN_UP_MONTHS) & (first > 0) & (second > 0)
           & (np.nan_to_num(z, nan=0) >= MIN_Z))
    return dict(iu=iu, N=N, roi=roi, z=z, up=up, months_active=active.sum(0), Nm=Nm, Pm=Pm,
                first=first, second=second, Nfirst=Nm[:h].sum(0), Nsecond=Nm[h:].sum(0),
                hit=hit, order=order)


def run(ctx):
    d, names, M, bets, fakes = sweep3.prepare(ctx, report=False)
    emit("similar", f"rule: bets>={MIN_BETS} roi>={100 * MIN_ROI:.0f}% up in >={MIN_UP_MONTHS} months "
                    f"(months with {MIN_MONTH_BETS}+ bets) both halves up z>={MIN_Z}")
    for label, (ret, var) in bets.items():
        s = _scan(d, M, ret, var)
        null = [int(_scan(d, M, f[label], var)["hit"].sum()) for f in fakes]
        emit(f"similar.{label}", f"look-alikes REAL={int(s['hit'].sum())} | NO-EDGE worlds={null} "
                                 f"(patterns checked={int((s['N'] >= MIN_BETS).sum())})")
        idx = np.nonzero(s["hit"])[0]
        idx = idx[np.argsort(-s["z"][idx])][:60]
        rows = []
        for i in idx:
            a, b = s["iu"][0][i], s["iu"][1][i]
            nm = names[a] if a == b else f"{names[a]} + {names[b]}"
            months = " ".join(f"{(100 * p / n):+.0f}" if n >= MIN_MONTH_BETS else "." for n, p in zip(s["Nm"][:, i], s["Pm"][:, i]))
            rows.append((nm, int(s["N"][i]), round(100 * s["roi"][i], 1), round(float(s["z"][i]), 2),
                         f"{int(s['up'][i])}/{int(s['months_active'][i])}",
                         round(100 * s["first"][i] / max(s["Nfirst"][i], 1), 1),
                         round(100 * s["second"][i] / max(s["Nsecond"][i], 1), 1), months))
        ctx.table(f"similar.{label}.list", ["pattern", "bets", "roi", "z", "months_up", "roi_1st_half",
                                            "roi_2nd_half", "roi_by_month " + ",".join(s["order"])], rows, limit=60)
    emit("similar", "done")
