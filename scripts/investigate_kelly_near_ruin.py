#!/usr/bin/env python3
"""Why joint-Kelly staking goes to ~zero in every walk-forward fold.

THE OBSERVATION
---------------
Every walk-forward fold of xgboost_ranker_blended (model 158, run 219) ended
with the simulated bankroll at essentially nothing:

    fold 0: 1946 bets, roi -10.1%, final_bankroll 1.6e-08, max_drawdown 99.9999%
    fold 1: 1826 bets, roi  -7.7%, final_bankroll 3.2e-05, max_drawdown 99.997%
    fold 2: 1910 bets, roi  -7.9%, final_bankroll 1.3e-06, max_drawdown 99.9999%

while the flat one-unit-per-race numbers for the same model (33.6% strike
rate, -10.5% ROI) look like an ordinary, mildly losing strategy.

WHAT THIS SCRIPT IS FOR
-----------------------
Four different causes would produce that table and each needs a different
fix, so this decides between them with measurements rather than a guess:

  A. a sizing bug in solve_joint_kelly (each runner staked as if it were the
     only bet in the race, so a multi-runner race is over-staked);
  B. the KELLY_MAX_TOTAL_STAKE_PCT cap not actually binding across the
     several bets a race gets;
  C. a tail-calibration problem — a handful of very confident, wrong picks
     doing all the damage;
  D. no bug at all: Kelly staking a bet stream whose real expectation is
     negative, at a size set by the model's disagreement with the market.

Everything here is self-contained and offline. The synthetic population is
NOT a claim about racing; it is a control. It reproduces the fold table from
nothing but "a model that ranks about as well as this one and prices about as
badly", which is what makes it evidence: if a made-up model with no tail
problem, no bug, and no connection to model 158 reproduces near-ruin, then
near-ruin is not diagnostic of model 158.

    python scripts/investigate_kelly_near_ruin.py

With DATABASE_URL set it also reads the stored kelly_staking blocks for the
previous champions, which is the one question synthetic data cannot answer:
whether this is new or has always been there.

    DATABASE_URL=... python scripts/investigate_kelly_near_ruin.py --models 158 143
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('DATABASE_URL', 'postgresql://offline/offline')

from model_classes import solve_joint_kelly  # noqa: E402

DEFAULT_MULTIPLIER = float(os.environ.get('ML_KELLY_FRACTION_MULTIPLIER', '0.5'))
DEFAULT_CAP = float(os.environ.get('ML_KELLY_MAX_TOTAL_STAKE_PCT', '0.20'))


def _rule(title):
    print(f"\n{'─' * 78}\n{title}\n{'─' * 78}")


# ─────────────────────────────────────────────────────────────────────────────
# A synthetic race population
# ─────────────────────────────────────────────────────────────────────────────
def make_population(n_races=880, field=10, noise=0.55, overround=1.18,
                    flb=0.10, seed=7):
    """Races with a KNOWN true win probability for every runner.

    Knowing the truth is the whole point: on real data the true probability is
    unobservable, so "the model's edge is imaginary" and "the model is unlucky"
    cannot be told apart. Here they can.

      overround  the book pays out less than 1.0 — the market's own margin.
      flb        favourite-longshot bias: the price shortens favourites and
                 lengthens longshots relative to the truth.
      noise      how far the model's log-probabilities wander from the truth.
                 This is the only knob that varies model QUALITY, and every
                 result below turns on it.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for race_id in range(n_races):
        strength = np.exp(rng.normal(0.0, 1.0, field))
        p_true = strength / strength.sum()

        market = p_true ** (1.0 + flb)
        market /= market.sum()
        sp = 1.0 / (market * overround)

        noisy = np.log(p_true) + rng.normal(0.0, noise, field)
        p_model = np.exp(noisy)
        p_model /= p_model.sum()

        winner = rng.choice(field, p=p_true)
        for i in range(field):
            rows.append({
                'race_id': race_id, 'pred': p_model[i], 'sp': sp[i],
                'won': int(i == winner), 'p_true': p_true[i],
            })
    frame = pd.DataFrame(rows)
    frame['row_id'] = range(len(frame))
    return frame


def simulate(eval_df, multiplier=DEFAULT_MULTIPLIER, cap=DEFAULT_CAP):
    """backtest._simulate_joint_kelly_staking, plus the diagnostics it does
    not keep: what the cap did, and where the damage actually landed."""
    bankroll = 1.0
    peak = 1.0
    max_drawdown = 0.0
    bets_per_race = []
    uncapped_totals = []
    capped_races = 0
    race_log_returns = []
    true_ev = 0.0
    turnover = 0.0
    staked_rows = []

    for race_id, race_df in eval_df.groupby('race_id'):
        keyed = race_df.set_index('row_id')
        priced = list(zip(keyed.index, keyed['pred'], keyed['sp']))

        uncapped = solve_joint_kelly(priced, multiplier, None)
        stakes = solve_joint_kelly(priced, multiplier, cap)
        bets_per_race.append(len(stakes))
        if not stakes:
            continue
        total_uncapped = sum(uncapped.values())
        uncapped_totals.append(total_uncapped)
        if cap is not None and total_uncapped > cap:
            capped_races += 1

        before = bankroll
        race_profit = 0.0
        for row_id, stake_pct in stakes.items():
            row = keyed.loc[row_id]
            stake = bankroll * stake_pct
            race_profit += (stake * (row['sp'] - 1.0)) if row['won'] == 1 else -stake
            turnover += stake_pct
            if 'p_true' in row:
                true_ev += stake_pct * (row['p_true'] * (row['sp'] - 1.0) - (1.0 - row['p_true']))
            staked_rows.append({
                'race_id': race_id, 'stake_pct': stake_pct, 'pred': row['pred'],
                'sp': row['sp'], 'won': row['won'],
            })
        bankroll += race_profit
        peak = max(peak, bankroll)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - bankroll) / peak)
        if before > 0 and bankroll > 0:
            race_log_returns.append(np.log(bankroll / before))

    n_betting_races = len(uncapped_totals)
    return {
        'final_bankroll': bankroll,
        'max_drawdown_pct': max_drawdown * 100.0,
        'bets': sum(bets_per_race),
        'avg_horses_backed_per_race': float(np.mean(bets_per_race)) if bets_per_race else 0.0,
        'betting_races': n_betting_races,
        'cap_bound_pct': (capped_races / n_betting_races * 100.0) if n_betting_races else 0.0,
        'median_uncapped_total_pct': float(np.median(uncapped_totals) * 100.0) if uncapped_totals else 0.0,
        'mean_stake_per_race_pct': (turnover / n_betting_races * 100.0) if n_betting_races else 0.0,
        'true_ev_pct_of_turnover': (true_ev / turnover * 100.0) if turnover else float('nan'),
        'race_log_returns': np.array(race_log_returns),
        'staked': pd.DataFrame(staked_rows),
    }


def flat_top_pick(eval_df):
    """The published flat one-unit-per-race numbers: the TOP PICK only."""
    top = eval_df.loc[eval_df.groupby('race_id')['pred'].idxmax()]
    profits = np.where(top['won'] == 1, top['sp'] - 1.0, -1.0)
    return {
        'bets': len(top),
        'strike_rate': top['won'].mean() * 100.0,
        'roi': profits.mean() * 100.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 1. Is solve_joint_kelly sizing the race correctly?
# ─────────────────────────────────────────────────────────────────────────────
def check_solver_against_numerical_optimum(trials=200, seed=3):
    """Hypothesis A, tested directly rather than by reading the code.

    For one race, staking x_i on mutually exclusive runners, the Kelly
    objective is

        E[log W] = sum_i p_i log(1 - X + x_i O_i) + (1 - sum_i p_i) log(1 - X)

    with X = sum_i x_i. If each runner were being sized as if it were the only
    bet, the closed form would beat that objective's true optimum in exactly
    the wrong direction — it would stake MORE. So maximise it numerically and
    compare, both on the closed form's own set of backed runners and over all
    runners.
    """
    try:
        from scipy.optimize import minimize
    except ImportError:
        print("  scipy is not installed; skipping the numerical check.")
        return

    rng = np.random.default_rng(seed)
    worst_on_support = 0.0
    worst_overall = 0.0
    staked_more_than_optimal = 0

    for _ in range(trials):
        field = int(rng.integers(6, 13))
        strength = np.exp(rng.normal(0.0, 1.0, field))
        p_true = strength / strength.sum()
        market = p_true ** 1.1
        market /= market.sum()
        odds = 1.0 / (market * 1.15)
        model = np.exp(np.log(p_true) + rng.normal(0.0, 0.5, field))
        model /= model.sum()

        def expected_log(x):
            x = np.asarray(x, dtype=float)
            total = x.sum()
            if total >= 0.9999 or np.any(x < -1e-12):
                return -1e6
            wealth = 1.0 - total + x * odds
            if np.any(wealth <= 0):
                return -1e6
            return (np.sum(model * np.log(wealth))
                    + max(0.0, 1.0 - model.sum()) * np.log(max(1e-12, 1.0 - total)))

        # Unmultiplied and uncapped: the raw Kelly allocation is what the
        # objective above is the optimum of. The 0.5 multiplier and the 20%
        # cap are deliberate departures from it, not part of the maths.
        closed = solve_joint_kelly(list(zip(range(field), model, odds)), 1.0, None)
        closed_x = np.array([closed.get(i, 0.0) for i in range(field)])

        def optimise(mask):
            best = None
            for _attempt in range(6):
                start = rng.uniform(0.0, 0.05, field) * mask
                found = minimize(
                    lambda x: -expected_log(x * mask), start,
                    bounds=[(0.0, 0.95)] * field, method='SLSQP',
                    constraints=[{'type': 'ineq', 'fun': lambda x: 0.98 - (x * mask).sum()}],
                )
                if best is None or found.fun < best.fun:
                    best = found
            return np.clip(best.x, 0.0, None) * mask

        support = (closed_x > 0).astype(float)
        if support.any():
            worst_on_support = max(
                worst_on_support, expected_log(optimise(support)) - expected_log(closed_x))
        free = optimise(np.ones(field))
        worst_overall = max(worst_overall, expected_log(free) - expected_log(closed_x))
        if free.sum() < closed_x.sum() - 1e-4:
            staked_more_than_optimal += 1

    print(f"  races tested: {trials}")
    print(f"  worst E[log] shortfall vs the optimum ON THE BACKED SET : {worst_on_support:.2e}")
    print(f"  worst E[log] shortfall vs the UNRESTRICTED optimum      : {worst_overall:.2e}")
    print(f"  races where the closed form staked MORE than optimal    : {staked_more_than_optimal}/{trials}")
    print()
    print("  Reading: zero shortfall on the backed set means the closed form")
    print("  solves the joint problem exactly — runners are NOT being sized as")
    print("  if each were the only bet. The small shortfall against the")
    print("  unrestricted optimum is the `p*O > 1` pre-filter refusing runners")
    print("  whose true KKT threshold is Q/(1-R) < 1, i.e. marginal hedges. That")
    print("  makes it stake LESS than optimal, never more, so it cannot be what")
    print("  empties the bankroll.")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Does the cap bind, and does it matter?
# ─────────────────────────────────────────────────────────────────────────────
def check_cap_enforcement():
    """Hypothesis B. The cap is on the SUM across a race, so the test is
    whether a race backing several runners can commit more than the cap."""
    rng = np.random.default_rng(11)
    worst = 0.0
    multi = 0
    for _ in range(2000):
        field = int(rng.integers(6, 13))
        model = rng.dirichlet(np.ones(field) * 0.7)
        odds = np.clip(rng.lognormal(1.2, 0.7, field), 1.05, 200.0)
        stakes = solve_joint_kelly(list(zip(range(field), model, odds)),
                                   DEFAULT_MULTIPLIER, DEFAULT_CAP)
        if len(stakes) > 1:
            multi += 1
        worst = max(worst, sum(stakes.values()))
    print(f"  random races tested: 2000 ({multi} of them backing >1 runner)")
    print(f"  cap: {DEFAULT_CAP:.2%}   largest total committed to one race: {worst:.6%}")
    print(f"  cap respected jointly: {worst <= DEFAULT_CAP + 1e-12}")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Reproduce the fold table from a model that has no tail problem
# ─────────────────────────────────────────────────────────────────────────────
def reproduce(noises=(0.0, 0.25, 0.45, 0.55, 0.70)):
    population = make_population()
    header = (f"  {'model noise':>12} {'top SR':>8} {'top ROI':>9} {'bets/race':>10} "
              f"{'final bankroll':>15} {'max dd':>10} {'true EV':>9}")
    print(header)
    print(f"  {'-' * (len(header) - 2)}")
    results = {}
    for noise in noises:
        frame = make_population(noise=noise) if noise else make_population(noise=0.0)
        if noise == 0.0:
            frame['pred'] = frame['p_true']  # the model IS the truth
        flat = flat_top_pick(frame)
        kelly = simulate(frame)
        results[noise] = (frame, flat, kelly)
        label = 'PERFECT' if noise == 0.0 else f'{noise:.2f}'
        print(f"  {label:>12} {flat['strike_rate']:>7.1f}% {flat['roi']:>8.1f}% "
              f"{kelly['avg_horses_backed_per_race']:>10.2f} "
              f"{kelly['final_bankroll']:>15.3g} {kelly['max_drawdown_pct']:>9.4f}% "
              f"{kelly['true_ev_pct_of_turnover']:>8.2f}%")
    print()
    print("  'true EV' is the real expectation of the money Kelly actually")
    print("  staked, per unit of turnover — computable here only because the")
    print("  true probabilities are known.")
    return results, population


def stake_size_vs_accuracy(results):
    print(f"  {'model noise':>12} {'median raw Kelly total':>24} {'mean staked/race':>18} {'cap binds':>11}")
    print(f"  {'-' * 68}")
    for noise, (_frame, _flat, kelly) in results.items():
        label = 'PERFECT' if noise == 0.0 else f'{noise:.2f}'
        print(f"  {label:>12} {kelly['median_uncapped_total_pct']:>23.2f}% "
              f"{kelly['mean_stake_per_race_pct']:>17.2f}% {kelly['cap_bound_pct']:>10.1f}%")
    print()
    print("  This is the mechanism in one table. Kelly sizes a bet by how far")
    print("  the model's probability sits above the price. A model that is")
    print("  RIGHT rarely disagrees with the market by much, so it stakes")
    print("  little. A model that is WRONG disagrees constantly, so it stakes")
    print("  heavily — on exactly the runners where its error is largest.")
    print("  Stake size tracks the model's error, not its edge.")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Is the damage a few bets, or all of them?
# ─────────────────────────────────────────────────────────────────────────────
def damage_concentration(kelly):
    """Hypothesis C. If a handful of confident, wrong picks were doing it, the
    loss would sit in the extreme tail of the per-race log-return distribution
    and removing that tail would rescue the bankroll."""
    returns = kelly['race_log_returns']
    if returns.size == 0:
        print("  no betting races")
        return
    total = returns.sum()
    order = np.argsort(returns)  # worst first
    for pct in (1, 5, 10, 25):
        k = max(1, int(len(returns) * pct / 100))
        share = returns[order[:k]].sum() / total * 100.0
        print(f"  worst {pct:>2}% of races ({k:>4} of {len(returns)}) account for "
              f"{share:>6.1f}% of the total log loss")
    losers = (returns < 0).sum()
    print(f"  races that lost money: {losers}/{len(returns)} ({losers / len(returns) * 100:.1f}%)")
    print(f"  median per-race log return: {np.median(returns):+.5f} "
          f"(a median BELOW zero means the bleed is the norm, not the tail)")

    staked = kelly['staked']
    if not staked.empty:
        print()
        print(f"  {'model prob bin':>16} {'bets':>7} {'won':>7} {'stake share':>12} {'net units':>11}")
        print(f"  {'-' * 58}")
        bins = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        staked = staked.assign(bin=pd.cut(staked['pred'], bins, include_lowest=True))
        for name, group in staked.groupby('bin', observed=True):
            net = np.where(group['won'] == 1,
                           group['stake_pct'] * (group['sp'] - 1.0),
                           -group['stake_pct']).sum()
            print(f"  {str(name):>16} {len(group):>7} {int(group['won'].sum()):>7} "
                  f"{group['stake_pct'].sum() / staked['stake_pct'].sum() * 100:>11.1f}% "
                  f"{net:>+11.3f}")
        print()
        print("  If the high-confidence bins hold a small share of the stake and")
        print("  the losses are spread across the low bins, the ruin is not a")
        print("  tail-calibration problem — it is the whole book.")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Can the parameters be tuned out of it?
# ─────────────────────────────────────────────────────────────────────────────
def parameter_sweep(frame):
    multipliers = (0.5, 0.25, 0.10, 0.05, 0.01)
    caps = (0.20, 0.10, 0.05, 0.02)
    print(f"  final bankroll after {frame['race_id'].nunique()} races\n")
    print("  " + "multiplier".rjust(11) + "".join(f"{f'cap {c:.0%}':>16}" for c in caps))
    print("  " + "-" * (11 + 16 * len(caps)))
    for multiplier in multipliers:
        cells = []
        for cap in caps:
            cells.append(f"{simulate(frame, multiplier, cap)['final_bankroll']:>16.3g}")
        print(f"  {multiplier:>11.2f}" + "".join(cells))
    print()
    print("  Every cell loses money. Smaller stakes only slow the bleed, because")
    print("  the sign of the expectation is not a staking parameter. Tuning these")
    print("  two numbers cannot turn a negative-EV bet stream positive; it can")
    print("  only make the loss take longer.")


def what_the_champion_score_saw(folds):
    """Why a model whose bankroll went to 1.6e-08 was promoted anyway.

    _compute_selection_score turns kelly_staking into
        5*clamp(bankroll_growth, -1, 5) - 0.02*max_drawdown_pct
        (-10 more if `ruined`)
    and `ruined` is unreachable: it is only set when the bankroll reaches <= 0
    at the top of a race, but the cap keeps every race's total stake at
    KELLY_MAX_TOTAL_STAKE_PCT of the bankroll, so a race can never lose more
    than that fraction of it. The bankroll approaches zero asymptotically and
    never arrives.
    """
    print(f"  cap = {DEFAULT_CAP:.0%}, so the worst a race can do is multiply the bankroll")
    print(f"  by {1 - DEFAULT_CAP:.2f}. `ruined` (bankroll <= 0) is therefore unreachable, and the")
    print("  -10 ruin penalty never fires however close to nothing the bankroll gets.\n")
    print(f"  {'fold':>6} {'final bankroll':>16} {'max dd':>11} {'ruined':>8} {'Kelly term of Champion Score':>30}")
    print(f"  {'-' * 76}")
    for label, final_bankroll, drawdown in folds:
        growth = max(-1.0, min(final_bankroll - 1.0, 5.0))
        component = growth * 5.0 - drawdown * 0.02
        print(f"  {label:>6} {final_bankroll:>16.3g} {drawdown:>10.4f}% {'False':>8} "
              f"{component:>30.2f}")
    print()
    print("  Total wipeout scores -7.0. A bankroll that merely halved would score")
    print("  5*(-0.5) - 0.02*50 = -3.5. So the whole range from 'lost half the bank'")
    print("  to 'lost all but a hundred-millionth of it' is worth 3.5 points, and")
    print("  every candidate in the run lands in the same narrow band. The term is")
    print("  present in the score but it is not discriminating between candidates,")
    print("  which is why this did not block promotion.")


# ─────────────────────────────────────────────────────────────────────────────
# 6. The one question offline data cannot answer
# ─────────────────────────────────────────────────────────────────────────────
def read_stored_kelly_blocks(model_ids):
    """Is this new, or has every model always done it?

    Read-only. Needs a real DATABASE_URL. A stored block WITHOUT
    avg_horses_backed_per_race predates the joint solver entirely (it was
    written by the deleted single-bet simulator), which is itself the answer
    to 'why has nobody seen this before'.
    """
    url = os.environ.get('DATABASE_URL', '')
    if not url or url.startswith('postgresql://offline'):
        print("  DATABASE_URL is not set — skipped.")
        print("  Run with a real DATABASE_URL to answer this one:")
        print("      DATABASE_URL=... python scripts/investigate_kelly_near_ruin.py "
              f"--models {' '.join(str(m) for m in model_ids)}")
        return
    from sqlalchemy import create_engine, text
    if url.startswith('postgres://'):
        url = url.replace('postgres://', 'postgresql://', 1)
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, model_name, run_id,
                   selection_metrics::json -> 'kelly_staking' AS kelly
            FROM backtest_best_model
            WHERE id = ANY(:ids)
            ORDER BY id
        """), {'ids': list(model_ids)}).fetchall()
    if not rows:
        print(f"  no rows for models {list(model_ids)}")
        return
    for model_id, name, run_id, kelly in rows:
        kelly = kelly or {}
        era = 'joint solver' if 'avg_horses_backed_per_race' in kelly else 'PRE-joint-solver (single-bet simulator)'
        print(f"  model {model_id} ({name}, run {run_id}) — {era}")
        for key in ('final_bankroll', 'bankroll_growth', 'max_drawdown_pct',
                    'ruined', 'avg_horses_backed_per_race', 'races_with_zero_bets'):
            if key in kelly:
                print(f"      {key:<28} {kelly[key]}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--models', nargs='*', type=int, default=[158, 143],
                        help="Model ids whose stored kelly_staking blocks to read (needs DATABASE_URL).")
    args = parser.parse_args(argv)

    print(f"KELLY_FRACTION_MULTIPLIER = {DEFAULT_MULTIPLIER}")
    print(f"KELLY_MAX_TOTAL_STAKE_PCT = {DEFAULT_CAP}")

    _rule("1. Is solve_joint_kelly over-staking a multi-runner race?  (hypothesis A)")
    check_solver_against_numerical_optimum()

    _rule("2. Is the per-race cap enforced across every bet in the race?  (hypothesis B)")
    check_cap_enforcement()

    _rule("3. Reproducing the fold table from model quality alone")
    results, population = reproduce()

    _rule("4. What actually sets the stake size")
    stake_size_vs_accuracy(results)

    _rule("5. Is the damage a few confident wrong picks, or the whole book?  (hypothesis C)")
    damage_concentration(results[0.55][2])

    _rule("6. Can the multiplier and the cap be tuned out of it?")
    parameter_sweep(population)

    _rule("7. What the Champion Score made of the reported folds")
    what_the_champion_score_saw([
        ('0', 1.6e-08, 99.9999),
        ('1', 3.2e-05, 99.997),
        ('2', 1.3e-06, 99.9999),
    ])

    _rule("8. Stored kelly_staking for earlier champions (needs DATABASE_URL)")
    read_stored_kelly_blocks(args.models)
    return 0


if __name__ == '__main__':
    sys.exit(main())
