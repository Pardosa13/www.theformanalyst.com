"""Shared estimator classes and staking maths used by backtest.py and ml_predict.py.

These classes must live in their own importable module rather than being
defined inline in backtest.py. joblib/pickle records a class's location as
`<module>.<ClassName>` at pickle time. When backtest.py is executed directly
(`python backtest.py`, the nightly training job), Python treats it as the
`__main__` module, so any class defined inside it gets pickled as
`__main__.ClassName`. The live web app is a separate process (gunicorn),
with its own distinct `__main__` (gunicorn's entry point, not backtest.py) —
unpickling there looks for the class on gunicorn's `__main__` and fails,
because that class was never defined there. Giving the class a stable module
path both processes can import normally fixes this for every future pickle.
"""
import sys

from sklearn.base import BaseEstimator, RegressorMixin, clone
import numpy as np


# Attribute name under which a model artifact carries its validated
# market-blend weight. Defined here, in the module both backtest.py and
# ml_predict.py already import, for the same reason ConsensusRegressor is: the
# nightly job writes this attribute and the web app reads it, and a name that
# exists twice is a name that can drift. alpha travels with the pkl exactly
# like _form_analyst_feature_medians does.
MARKET_BLEND_ALPHA_ATTR = '_form_analyst_market_blend_alpha'


class ConsensusRegressor(BaseEstimator, RegressorMixin):
    """Weighted consensus of model win-likelihood scores."""

    def __init__(self, estimators, weights=None):
        self.estimators = estimators
        self.weights = weights

    def set_race_context(self, race_ids):
        """Pass a race grouping down to any member that needs one.

        Held on the ensemble as well as pushed to the members, because fit()
        clones its members and clone() keeps only __init__ params — a context
        set before fit would otherwise be dropped on the floor exactly when a
        race-grouped member needs it most.
        """
        self._race_context = None if race_ids is None else [str(r) for r in race_ids]
        for member in getattr(self, 'estimators_', None) or []:
            set_race_context(member, self._race_context)
        return self

    def fit(self, X, y):
        self.feature_names_in_ = np.asarray(list(X.columns)) if hasattr(X, 'columns') else None
        race_context = getattr(self, '_race_context', None)
        self.estimators_ = []
        for _, estimator in self.estimators:
            fitted = clone(estimator)
            set_race_context(fitted, race_context)
            fitted.fit(X, y)
            self.estimators_.append(fitted)
        if self.weights is None:
            self.weights_ = np.ones(len(self.estimators_), dtype=float)
        else:
            self.weights_ = np.asarray(self.weights, dtype=float)
            if len(self.weights_) != len(self.estimators_) or np.sum(self.weights_) <= 0:
                self.weights_ = np.ones(len(self.estimators_), dtype=float)
        self.weights_ = self.weights_ / np.sum(self.weights_)
        return self

    def predict(self, X):
        # A ranker clears its context once fitted, so re-arm the members here:
        # the grouping the ensemble was told about applies to whatever X it is
        # now being asked to score.
        race_context = getattr(self, '_race_context', None)
        if race_context is not None and len(race_context) == len(X):
            for est in self.estimators_:
                set_race_context(est, race_context)
        preds = []
        for est in self.estimators_:
            if hasattr(est, 'predict_proba'):
                proba = np.asarray(est.predict_proba(X), dtype=float)
                preds.append(proba[:, 1] if proba.ndim == 2 and proba.shape[1] > 1 else proba.ravel())
            else:
                preds.append(np.asarray(est.predict(X), dtype=float))
        return np.average(np.column_stack(preds), axis=1, weights=self.weights_)


# Backward-compat shim for artifacts pickled BEFORE this class moved out of
# backtest.py's __main__ module. Any ensemble/ensemble_equal_weight/
# ensemble_catboost_weighted model saved while backtest.py was running as
# `__main__` (i.e. every such artifact saved by the nightly job so far,
# including the current active champion) was pickled with class path
# `__main__.ConsensusRegressor`. Unpickling looks up that exact module+name
# pair, so those old artifacts would still fail to load in the web app even
# after this fix, since gunicorn's own __main__ is a different module and we
# cannot change what module it is. Instead, register this same class object
# under the name `ConsensusRegressor` on whichever module is __main__ in the
# current process — harmless in backtest.py itself (where the normal import
# below already binds the same name) and the fix that makes old artifacts
# unpickle correctly in the web app process without a database migration or
# retraining.
_main_module = sys.modules.get('__main__')
if _main_module is not None and not hasattr(_main_module, 'ConsensusRegressor'):
    _main_module.ConsensusRegressor = ConsensusRegressor


# ─────────────────────────────────────────────
# RACE-GROUPED RANKING
# ─────────────────────────────────────────────
# Every other candidate in this pipeline is POINTWISE: each horse gets its own
# win/loss label and is scored independently of the field it ran against. That
# throws away the one structural fact the problem hands you for free — exactly
# one runner wins each race — and asks the model to learn an absolute
# probability when all any bettor needs is the order within a race.
#
# A learning-to-rank model is trained on that structure directly: the loss is
# defined over PAIRS within a race (did the actual winner outrank the actual
# losers?), so a race where the model got the order right costs nothing even if
# its absolute numbers are miles off, and a race where it ranked the winner
# last is penalised no matter how confident it was.
#
# Two things this needs that a pointwise fit does not:
#
#   1. GROUPING. The runners of a race must sit together in the matrix, with a
#      `group` array giving each race's size in order, so the ranker knows
#      where one race ends and the next begins.
#   2. A CONVERSION. Raw ranker output is an arbitrary real number, not a
#      probability, so it cannot feed Kelly staking, the A/E calculation or the
#      market blend as-is. A per-race softmax turns the scores into exactly the
#      Plackett-Luce first-place probabilities the rest of the pipeline
#      already consumes from every other candidate.
#
# Both live in here so the wrapper presents the same predict_proba surface as
# any classifier, and every downstream consumer stays unchanged.


def race_group_order(race_ids):
    """Row order that makes each race contiguous, plus the group-size array.

    Returns `(order, groups)`: `order` is an index array that gathers each
    race's rows together, and `groups` gives the races' sizes in the order they
    appear after that gather.

    Races are ordered by where they FIRST appear in the input, and rows within
    a race keep their input order — so a chronologically ordered slice stays
    chronologically ordered. That matters more than it looks: sorting by race
    id instead (a plain lexicographic sort) would reshuffle the time axis, and
    every fold boundary in this pipeline is defined by row position on a
    chronological ordering.
    """
    first_seen = {}
    rows_by_race = {}
    for position, race_id in enumerate(race_ids):
        key = str(race_id)
        if key not in first_seen:
            first_seen[key] = position
            rows_by_race[key] = []
        rows_by_race[key].append(position)
    ordered_races = sorted(rows_by_race, key=lambda key: first_seen[key])
    order = [position for key in ordered_races for position in rows_by_race[key]]
    groups = [len(rows_by_race[key]) for key in ordered_races]
    return np.asarray(order, dtype=int), np.asarray(groups, dtype=int)


def race_softmax(scores, race_ids):
    """Per-race softmax over raw ranker scores.

    This is the Plackett-Luce first-place probability: exp(s_i) normalised over
    the runners of the same race. It is what makes a ranker interchangeable
    with every pointwise candidate downstream — one win probability per horse,
    summing to 1 within a race.

    The max is subtracted within each race before exponentiating (the standard
    log-sum-exp shift) so a race whose scores all sit far from zero cannot
    overflow or underflow to a row of zeros the argmax cannot rank.
    """
    scores = np.asarray(scores, dtype=float)
    out = np.zeros(scores.shape[0], dtype=float)
    positions_by_race = {}
    for position, race_id in enumerate(race_ids):
        positions_by_race.setdefault(str(race_id), []).append(position)
    for positions in positions_by_race.values():
        race_scores = scores[positions]
        finite = np.isfinite(race_scores)
        if not finite.any():
            out[positions] = 1.0 / len(positions)
            continue
        shifted = np.zeros(len(positions))
        shifted[finite] = np.exp(race_scores[finite] - np.max(race_scores[finite]))
        total = shifted.sum()
        out[positions] = (shifted / total) if total > 0 else (1.0 / len(positions))
    return out


class RaceGroupedRanker(BaseEstimator):
    """XGBRanker trained on race groups, presenting a classifier's surface.

    fit() needs to know which race each row belongs to. It is taken from, in
    order: an explicit `race_ids=` argument, or a race context previously set
    with set_race_context(). There is no third option — a ranker fitted with
    every row in one giant "race" has learned nothing about racing, and
    silently producing that would be far worse than refusing.

    predict_proba() returns the two-column shape a classifier does, so
    _predict_win_scores and ConsensusRegressor read it without knowing what it
    is. Which race each row belongs to comes from the race context; with no
    context set, X is treated as a SINGLE race, which is exactly right for
    ml_predict's per-race scoring loop and would be wrong for a whole
    validation set — so backtest.py always sets the context explicitly.
    """

    def __init__(self, objective='rank:pairwise', n_estimators=200, max_depth=4,
                 learning_rate=0.06, subsample=0.9, colsample_bytree=0.9,
                 min_child_weight=1.0, reg_lambda=1.0, random_state=42):
        self.objective = objective
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.subsample = subsample
        self.colsample_bytree = colsample_bytree
        self.min_child_weight = min_child_weight
        self.reg_lambda = reg_lambda
        self.random_state = random_state

    # ── race context ────────────────────────────────────────────────────────
    # A mutable per-call annotation, not model state: set it, call, and it is
    # cleared. It exists because sklearn's fit(X, y) / predict(X) signatures
    # have nowhere to put a grouping, and the walk-forward loop calls both
    # through code that must stay generic across every candidate.
    def set_race_context(self, race_ids):
        self._race_context = None if race_ids is None else [str(r) for r in race_ids]
        return self

    def _race_ids_for(self, n_rows, explicit=None):
        if explicit is not None:
            return [str(r) for r in explicit]
        context = getattr(self, '_race_context', None)
        if context is not None and len(context) == n_rows:
            return context
        return None

    def fit(self, X, y, race_ids=None):
        from xgboost import XGBRanker

        resolved = self._race_ids_for(len(X), race_ids)
        if resolved is None:
            raise ValueError(
                "RaceGroupedRanker.fit needs the race each row belongs to. Pass "
                "race_ids=, or call set_race_context() first. Fitting every row as "
                "one race would train a ranker that has learned nothing about racing."
            )

        order, groups = race_group_order(resolved)
        X_sorted = X.iloc[order] if hasattr(X, 'iloc') else np.asarray(X)[order]
        y_sorted = np.asarray(y, dtype=float)[order]

        self.feature_names_in_ = np.asarray(list(X.columns)) if hasattr(X, 'columns') else None
        self.n_races_ = int(len(groups))
        self.model_ = XGBRanker(
            objective=self.objective,
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            min_child_weight=self.min_child_weight,
            reg_lambda=self.reg_lambda,
            random_state=self.random_state,
            n_jobs=-1,
        )
        self.model_.fit(X_sorted, y_sorted, group=groups)
        self.set_race_context(None)
        return self

    def raw_scores(self, X):
        """The ranker's own output: an arbitrary real number per row, only
        meaningful relative to the other runners of the same race."""
        return np.asarray(self.model_.predict(X), dtype=float)

    def predict_win_probabilities(self, X, race_ids=None):
        resolved = self._race_ids_for(len(X), race_ids)
        if resolved is None:
            # No context: treat X as one race. Correct for ml_predict, which
            # scores a race at a time; backtest always passes a context.
            resolved = ['__single_race__'] * len(X)
        try:
            return race_softmax(self.raw_scores(X), resolved)
        finally:
            # The context describes the rows of ONE call. Leaving it set would
            # let a later call on a different X of coincidentally equal length
            # inherit the wrong grouping, and would pickle a stray copy of the
            # validation set's race ids into the artifact.
            self.set_race_context(None)

    def predict(self, X):
        return self.predict_win_probabilities(X)

    def predict_proba(self, X):
        probabilities = self.predict_win_probabilities(X)
        return np.column_stack([1.0 - probabilities, probabilities])


def set_race_context(model, race_ids):
    """Tell every race-aware estimator inside `model` how its rows are grouped.

    Dispatches to the estimator's own set_race_context when it has one (a
    RaceGroupedRanker, or a ConsensusRegressor that must pass it on to its
    members), and otherwise does nothing. Safe and free to call on every
    candidate, so call sites do not have to know which candidates are ranker-
    backed — which is the point: run_model_competition treats them all alike.
    """
    setter = getattr(model, 'set_race_context', None)
    if callable(setter):
        setter(race_ids)
    return model


# Same backward-compat shim as ConsensusRegressor above: an artifact pickled
# while backtest.py was running as __main__ records its class as
# __main__.RaceGroupedRanker, which gunicorn's own __main__ has never heard of.
if _main_module is not None and not hasattr(_main_module, 'RaceGroupedRanker'):
    _main_module.RaceGroupedRanker = RaceGroupedRanker


# ─────────────────────────────────────────────
# JOINT (MULTI-OUTCOME) KELLY STAKING
# ─────────────────────────────────────────────
# backtest.py's validation bankroll simulation and ml_predict.py's live stake
# recommendations must allocate identically, or the staking plan the nightly
# run validates is not the one the site displays. backtest.py is a cron job and
# ml_predict.py is request-path code, so neither imports the other; the solver
# therefore lives here, in the module they both already import, instead of
# being copy-pasted into each and drifting apart silently.

DEFAULT_KELLY_FRACTION_MULTIPLIER = 0.5
DEFAULT_KELLY_MAX_TOTAL_STAKE_PCT = 0.20


def solve_joint_kelly(probs_odds,
                      kelly_fraction_multiplier=DEFAULT_KELLY_FRACTION_MULTIPLIER,
                      max_total_stake_pct=DEFAULT_KELLY_MAX_TOTAL_STAKE_PCT):
    """Simultaneous Kelly allocation across the mutually exclusive runners of
    one race (Smoczynski & Tomkins 2010 closed-form solution to the KKT
    conditions of the multi-outcome Kelly objective).

    Betting several runners in the same race is not the same problem as sizing
    several independent bets: only one runner can win, so every stake is partly
    hedged by the others and the optimal size of each depends on the whole set.
    Runners are considered in descending order of expected value, and only ones
    clearing the break-even test p*O > 1 are eligible at all — that test is
    what leaves a runner with exactly zero stake rather than a token one, so a
    race where nothing is overpriced gets no bet, and how many runners are
    backed varies race to race. The stakes-stay-positive check in the loop is
    the formal KKT condition; given the break-even filter it is a guard rather
    than a second filter, since Q/(1-R) < 1 < p*O whenever the reserve leaves
    room. For a single included runner the formula collapses to the familiar
    x = (pO - 1) / (O - 1).

    A NOTE ON THE FAVOURITE-LONGSHOT CORRECTION
    -------------------------------------------
    market_probability.fair_probabilities corrects a price's reading as a
    PROBABILITY (overround + favourite-longshot bias) and callers apply it
    before they get here, to whatever probability they hand in. The odds passed
    to this function are deliberately left raw, in both roles they play:

      * as the payoff (O - 1 per unit staked) — a winning bet is settled at the
        price actually offered, not at a de-biased one; and
      * inside `reserve = sum(1/O)` — that term is the cost of covering the
        included set, a payoff-structure quantity in the Smoczynski & Tomkins
        closed form, not an estimate of anybody's probability. Substituting
        corrected probabilities for it would not de-bias the solver, it would
        stop solving the Kelly problem.

    So the correction reaches staking through `win_probability`, which is where
    a probability estimate belongs, and nowhere else.

    probs_odds: iterable of (key, win_probability, decimal_odds).
    Returns {key: stake_fraction_of_bankroll} containing only backed runners;
    anything absent from the result gets no stake.
    """
    candidates = []
    for key, prob, odds in probs_odds:
        try:
            prob = float(prob)
            odds = float(odds)
        except (TypeError, ValueError):
            continue
        if not (np.isfinite(prob) and np.isfinite(odds)):
            continue
        # p*O > 1 is the break-even test: below it the bet has negative
        # expectation and can never earn a positive Kelly stake.
        if odds > 1.0 and 0.0 < prob < 1.0 and (prob * odds) > 1.0:
            candidates.append((key, prob, odds))
    candidates.sort(key=lambda item: item[1] * item[2], reverse=True)
    if not candidates:
        return {}

    included = []
    for candidate in candidates:
        trial = included + [candidate]
        reserve = sum(1.0 / odds for _, _, odds in trial)
        # reserve >= 1 means the backed set covers the whole book; the
        # closed form divides by (1 - reserve) and stops being meaningful.
        if reserve >= 1.0:
            break
        no_winner_prob = 1.0 - sum(prob for _, prob, _ in trial)
        stakes = {
            key: prob - (no_winner_prob / (odds * (1.0 - reserve)))
            for key, prob, odds in trial
        }
        if all(stake > 0 for stake in stakes.values()):
            included = trial
        else:
            break

    if not included:
        return {}

    reserve = sum(1.0 / odds for _, _, odds in included)
    no_winner_prob = 1.0 - sum(prob for _, prob, _ in included)
    scaled = {
        key: max(0.0, (prob - (no_winner_prob / (odds * (1.0 - reserve)))) * kelly_fraction_multiplier)
        for key, prob, odds in included
    }
    total = sum(scaled.values())
    if max_total_stake_pct is not None and total > max_total_stake_pct:
        shrink = max_total_stake_pct / total
        scaled = {key: stake * shrink for key, stake in scaled.items()}
    return scaled
