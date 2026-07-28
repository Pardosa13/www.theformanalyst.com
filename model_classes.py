"""Custom sklearn-style estimator classes shared by backtest.py and ml_predict.py.

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
