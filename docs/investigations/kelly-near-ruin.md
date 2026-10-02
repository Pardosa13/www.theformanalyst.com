# Why joint-Kelly staking goes to ~zero in every walk-forward fold

Investigation of the `kelly_staking` block reported for `xgboost_ranker_blended`
(model 158, promoted from run 219). **No staking parameters have been changed.**

| fold | bets | roi | final_bankroll | max_drawdown_pct |
|---|---|---|---|---|
| 0 | 1946 | -10.1% | 1.6e-08 | 99.9999% |
| 1 | 1826 | -7.7% | 3.2e-05 | 99.997% |
| 2 | 1910 | -7.9% | 1.3e-06 | 99.9999% |

Reproduce everything below with:

```bash
python scripts/investigate_kelly_near_ruin.py
```

---

## Answer

**There is no bug driving this.** Kelly is compounding a bet stream whose real
expectation is negative, at a stake size set by how far the model disagrees
with the market. Model 158 disagrees with the market a lot and is not right
when it does, so it stakes heavily on its own errors, ~880 times per fold. The
bankroll going to 1e-08 is the arithmetic consequence, not a symptom.

The three suspected mechanisms were each tested and each is ruled out. What is
left is the plainest reading of the numbers: **flat ROI already said this model
loses money, and Kelly is what happens when you compound a loss instead of
adding it up.**

Two secondary findings are real and worth fixing regardless (see the end):
the `ruined` flag can never fire, and the flat-ROI number everyone reads is
measured on a different set of bets from the Kelly number.

---

## Ruled out: a sizing bug in `solve_joint_kelly` (each runner staked as if it were the only bet)

Tested against the thing the closed form claims to solve, rather than by
reading it. For one race with mutually exclusive runners the Kelly objective is

```
E[log W] = Σ p_i·log(1 − X + x_i·O_i) + (1 − Σ p_i)·log(1 − X),    X = Σ x_i
```

Maximising that numerically (SLSQP, multi-start) over 200 random races and
comparing with `solve_joint_kelly`:

```
worst E[log] shortfall vs the optimum ON THE BACKED SET : 0.00e+00
worst E[log] shortfall vs the UNRESTRICTED optimum      : 2.20e-02
races where the closed form staked MORE than optimal    : 1/200
```

Zero shortfall on its own backed set: the runners it backs are sized as the
exact joint optimum, not independently. The allocation is correct.

The small gap against the unrestricted optimum is a genuine (minor) deviation
worth recording, but it points the wrong way to explain ruin. The docstring
says the `p·O > 1` break-even pre-filter makes the loop's positivity check a
guard rather than a second filter. The true KKT inclusion threshold is
`p_i·O_i > Q/(1−R)`, and — as the docstring itself notes — `Q/(1−R) < 1`
always holds for the included set. So the pre-filter sits *above* the real
threshold and refuses marginal hedge bets that the optimum would take. **It
under-stakes, never over-stakes.**

## Ruled out: the `KELLY_MAX_TOTAL_STAKE_PCT` cap not binding jointly

```
random races tested: 2000 (618 of them backing >1 runner)
cap: 20.00%   largest total committed to one race: 20.000000%
```

The cap is applied to the sum after the multiplier and is respected exactly.

It is also mostly irrelevant here, which is the more interesting half: at
model 158's error level the cap binds in only **6.3%** of betting races. The
typical race commits ~10% of bankroll because that is what raw Kelly asked
for, not because the cap let it. Lowering the cap does not address a stake
size the cap was not setting.

## Ruled out: a few very confident, very wrong picks

The 0.8–0.9 calibration bin with 3 samples and a 0% win rate is a real
observation but it is not where the money goes.

```
worst  1% of races (   8 of 878) account for    6.9% of the total log loss
worst  5% of races (  43 of 878) account for   37.1% of the total log loss
worst 10% of races (  87 of 878) account for   70.7% of the total log loss
races that lost money: 630/878 (71.8%)
median per-race log return: -0.06526
```

A tail problem looks like a handful of races carrying most of the loss. This
is the opposite: **the median race loses money.** The bleed is the norm.

By confidence bin, the high-confidence bets barely exist and barely matter:

```
  model prob bin    bets     won  stake share   net units
   (-0.001, 0.2]    2510     120        47.5%      -6.669
      (0.2, 0.4]     486      78        34.6%      -4.379
      (0.4, 0.6]     114      38        13.5%      -1.538
      (0.6, 0.8]      30      11         4.3%      -2.118
      (0.8, 1.0]       1       1         0.2%      +0.038
```

82% of the stake sits below a 0.4 model probability, and every bin except the
one-bet top bin loses. Fixing the tail changes nothing.

---

## What is actually happening

The same simulator, the same solver, the same cap, the same market — varying
only how accurate the model is:

```
 model noise   top SR   top ROI  bets/race  final bankroll     max dd   true EV
     PERFECT    30.5%    -26.3%       2.10            1.44   12.4478%     8.62%
        0.25    30.3%    -20.6%       3.34         0.00501   99.5357%    -9.25%
        0.45    28.1%    -20.9%       3.59        1.29e-08  100.0000%   -11.46%
        0.55    26.5%    -24.5%       3.57        6.01e-12  100.0000%   -11.84%
        0.70    25.3%    -21.7%       3.51        5.04e-17  100.0000%   -12.08%
```

A model that knows the true probabilities exactly ends at **1.44× with a 12%
drawdown**. Add noise and the identical code goes to zero. Nothing in the
staking machinery changed between those rows — only the model.

`true EV` is the real expectation of the money Kelly staked, per unit of
turnover. It is computable in this synthetic population because the true
probabilities are known, and it is the number the whole thing turns on: the
perfect model's staked book is genuinely +8.6%; every noisy model's is around
−10% to −12%, matching the reported fold ROIs.

And here is the mechanism, which is the part worth internalising:

```
 model noise   median raw Kelly total   mean staked/race   cap binds
     PERFECT                    0.08%              0.12%        0.0%
        0.25                    2.27%              2.83%        0.0%
        0.45                    6.96%              7.73%        1.6%
        0.55                    9.61%             10.20%        6.3%
        0.70                   13.52%             13.27%       22.1%
```

**Kelly sizes a bet by how far the model's probability sits above the price.**
A model that is right rarely disagrees with an efficient market by much, so it
stakes almost nothing — 0.08% of bankroll — and only on the rare genuine
overlay. A model that is wrong disagrees constantly, so it stakes a hundred
times more, on precisely the runners where its error is largest. **Stake size
tracks the model's error, not its edge**, and the selection `p·O > 1` is a
selection *on that error*: it filters for runners where the model happens to
be most optimistic relative to the market. Kelly's premise is that `p` is the
true probability. When it is not, the violation both makes the bets
negative-EV and makes the stakes large, and those two compound.

Sanity check on the arithmetic: mean stake ~10.2% of bankroll per race at
−11.8% true EV gives ~−1.2% expected per race; over 878 races that is
`0.988^878 ≈ 3e-05`, with the geometric drag from the stake lumpiness taking
it several orders further down. The reported folds sit inside that range —
fold 0's `1.6e-08` lands between the 0.45 and 0.55 rows above.

A caveat on how much to read into the synthetic numbers: the population is a
**control, not a fit**. It is not tuned to reproduce model 158's exact strike
rate and ROI, and it does not — its top-pick ROI runs −20% to −26% against the
real −10.5%, because its 18% overround and its favourite-longshot exponent
were picked to be plausible rather than matched. That does not weaken the
argument, it is the argument: near-total ruin falls out of a made-up model
with no tail problem, no bug, and no connection to model 158, so near-ruin is
not diagnostic of model 158. What the synthetic data establishes is the
mechanism and its direction; the real fold numbers are what establish the
magnitude.

---

## Why parameter tuning is not the fix

Final bankroll over the same 880 races, sweeping both knobs:

```
 multiplier      cap 20%      cap 10%       cap 5%       cap 2%
       0.50     6.01e-12     1.83e-08      0.00011       0.0441
       0.25     1.86e-05     3.34e-05      0.00063       0.0417
       0.10       0.0262       0.0262       0.0273       0.0786
       0.05        0.184        0.184        0.184        0.203
       0.01        0.728        0.728        0.728        0.728
```

Every cell loses money. The sign of the expectation is not a staking
parameter. `KELLY_FRACTION_MULTIPLIER = 0.5` and
`KELLY_MAX_TOTAL_STAKE_PCT = 0.20` are not obviously wrong for a model backing
2–3 runners a race — they are simply irrelevant to a negative-EV book, where
their only effect is how many races it takes to lose.

That is why this stops at a writeup. Lowering the multiplier would make the
next fold table look survivable while changing nothing about whether the
model can stake profitably, which is worse than the current honest −7.

---

## Is this new, or has every model always done it?

The evidence available offline says it is not specific to model 158: the
synthetic population reproduces the fold table from a made-up model with no
tail problem and no connection to this champion, across the whole range of
plausible model quality. Any model whose flat ROI is around −10% will produce
this table.

`git log` narrows down why nobody saw it before. Both
`_simulate_joint_kelly_staking` and the per-fold `kelly_staking` in the
walk-forward metrics arrived in the same commit
(`233ff6b`, 2026-08-19, "Add opponent-quality form feature and joint Kelly
staking"). Before that, per-fold Kelly numbers did not exist, and the stored
blocks were written by the deleted single-bet simulator — the format
`_compute_selection_score` still detects by the absence of
`avg_horses_backed_per_race`. **So this is almost certainly pre-existing and
newly visible rather than new.**

Confirming it needs production, which this investigation could not reach:

```bash
DATABASE_URL=... python scripts/investigate_kelly_near_ruin.py --models 158 143
```

Section 8 of the script prints each model's stored block and labels whether it
predates the joint solver. If 143's block has no
`avg_horses_backed_per_race`, its numbers are not comparable and the honest
answer is "nobody has ever measured a previous champion this way".

---

## Two things worth fixing regardless of the above

**1. `ruined` can never be true.** `_simulate_joint_kelly_staking` sets it
when the bankroll reaches `<= 0` at the top of a race, but the cap keeps every
race's total stake at 20% of the current bankroll, so a race can lose at most
20% of it. The bankroll approaches zero asymptotically and never arrives. The
−10 ruin penalty in `_compute_selection_score` is therefore unreachable:

```
  fold   final bankroll      max dd   ruined   Kelly term of Champion Score
     0          1.6e-08    99.9999%    False                          -7.00
     1          3.2e-05    99.9970%    False                          -7.00
     2          1.3e-06    99.9999%    False                          -7.00
```

Total wipeout scores −7.0; a bankroll that merely halved scores −3.5. The
entire range from "lost half the bank" to "lost all but a hundred-millionth of
it" is worth 3.5 points, and every candidate in a run lands in the same narrow
band. The term is in the score but is not discriminating between candidates,
which is why this did not block promotion. A practical ruin threshold (say,
bankroll below 1% of its starting value) would make the penalty reachable and
the term informative.

**2. The flat ROI and the Kelly number are not measuring the same bets.**
`evaluate_model_on_validation` computes flat ROI and strike rate on the
**per-race top pick only** (`idxmax`), while `_simulate_joint_kelly_staking`
stakes **every runner the solver backs** — 2.2–3.3 per race for this model.
The Kelly bet population is strictly larger and includes lower-ranked, longer
runners, so its expectation is worse than the −10.5% headline, not equal to it.

Reading the two side by side as though they describe one strategy is what
makes the pictures look contradictory. They are not contradictory; they are
different strategies. The synthetic table above shows how far apart they can
sit: the perfect model has a **−26.3% flat top-pick ROI** (flat-betting a
favourite into an 18% overround loses, however good the model is) and a
**+44% Kelly result** — opposite conclusions, same model, both correct. Flat
top-pick ROI mostly measures the market's margin; Kelly measures whether the
model can find an overlay. Labelling both as "ROI" in the same report invites
exactly this confusion.
