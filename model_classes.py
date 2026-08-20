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


class ConsensusRegressor(BaseEstimator, RegressorMixin):
    """Weighted consensus of model win-likelihood scores."""

    def __init__(self, estimators, weights=None):
        self.estimators = estimators
        self.weights = weights

    def fit(self, X, y):
        self.feature_names_in_ = np.asarray(list(X.columns)) if hasattr(X, 'columns') else None
        self.estimators_ = []
        for _, estimator in self.estimators:
            fitted = clone(estimator)
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
