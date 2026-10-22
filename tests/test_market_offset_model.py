"""Market-offset model: start from the market's price, learn only the correction."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("lightgbm")

import backtest
import ml_predict
from model_classes import (
    MARKET_BLEND_ALPHA_ATTR, MARKET_BLEND_BETA_ATTR, MARKET_OFFSET_COL, USES_MARKET_OFFSET_ATTR,
    MarketOffsetClassifier, RaceConditionalLogit, model_uses_market,
)


def _races(n_races=400, seed=0, signal=0.0):
    """Synthetic races where the market is right except for one feature it ignores."""
    rng = np.random.default_rng(seed)
    rows, y, races, sp = [], [], [], []
    for race in range(n_races):
        n = 8
        market = rng.dirichlet(np.ones(n) * 2)
        hidden = rng.normal(size=n)
        true = market * np.exp(signal * hidden)
        true /= true.sum()
        winner = rng.choice(n, p=true)
        for i in range(n):
            rows.append({'hidden': hidden[i], 'noise': rng.normal(), MARKET_OFFSET_COL: market[i]})
            y.append(int(i == winner)); races.append(race); sp.append(1.0 / (market[i] * 1.15))
    return pd.DataFrame(rows), np.array(y), races, np.array(sp)


def test_offset_model_finds_what_the_market_misses():
    X, y, races, _sp = _races(n_races=1500, signal=0.8)
    model = RaceConditionalLogit(MarketOffsetClassifier(n_estimators=150))
    model.fit(X, y, race_ids=races)
    X_test, y_test, races_test, _ = _races(n_races=500, seed=1, signal=0.8)
    p = model.predict_win_probabilities(X_test, race_ids=races_test)
    from model_classes import race_winner_log_loss
    model_ll, _ = race_winner_log_loss(p, y_test, races_test)
    market_ll, _ = race_winner_log_loss(X_test[MARKET_OFFSET_COL].to_numpy(), y_test, races_test)
    assert model_ll < market_ll


def test_offset_model_stays_close_to_the_market_when_there_is_nothing_to_find():
    X, y, races, _sp = _races(n_races=800, signal=0.0)
    model = MarketOffsetClassifier(n_estimators=100).fit(X, y)
    p = model.predict_proba(X)[:, 1]
    q = X[MARKET_OFFSET_COL].to_numpy()
    # Correlation with the market's own view stays very high.
    assert np.corrcoef(np.log(p), np.log(q))[0, 1] > 0.9


def test_missing_market_values_start_level():
    X, y, _races_, _sp = _races(n_races=200)
    model = MarketOffsetClassifier(n_estimators=20).fit(X, y)
    race = X.iloc[:8].copy()
    race[MARKET_OFFSET_COL] = np.nan
    p = model.predict_proba(race)[:, 1]
    assert np.all(np.isfinite(p))


def test_market_column_is_built_from_starting_prices():
    X = pd.DataFrame({'a': [1, 2, 3, 4]})
    out = backtest._with_market_column(X, [2.0, 3.0, 5.0, None], ['r1', 'r1', 'r1', 'r2'])
    assert MARKET_OFFSET_COL in out.columns and MARKET_OFFSET_COL not in X.columns
    assert out[MARKET_OFFSET_COL].iloc[:3].sum() == pytest.approx(1.0)
    assert np.isnan(out[MARKET_OFFSET_COL].iloc[3])


def test_market_column_is_an_optional_live_input():
    """Live scoring can build it, but an ordinary model is never 'missing' it."""
    assert MARKET_OFFSET_COL in backtest._live_optional_feature_names()
    assert MARKET_OFFSET_COL not in ml_predict.FEATURE_NAMES
    live = backtest._live_scoring_feature_names()
    assert backtest._model_era_mismatch(
        {'scoring_formula_version': backtest.SCORING_FORMULA_VERSION}, live + [MARKET_OFFSET_COL], live,
    ) is None
    assert backtest._model_era_mismatch(
        {'scoring_formula_version': backtest.SCORING_FORMULA_VERSION}, live, live,
    ) is None


def test_model_uses_market():
    plain = SimpleNamespace()
    assert model_uses_market(plain) is False
    blended = SimpleNamespace(**{MARKET_BLEND_ALPHA_ATTR: 0.2, MARKET_BLEND_BETA_ATTR: 0.7})
    assert model_uses_market(blended) is True
    old_alpha = SimpleNamespace(**{MARKET_BLEND_ALPHA_ATTR: 0.15})
    assert model_uses_market(old_alpha) is True
    no_blend = SimpleNamespace(**{MARKET_BLEND_ALPHA_ATTR: 1.0})
    assert model_uses_market(no_blend) is False
    offset = SimpleNamespace(**{USES_MARKET_OFFSET_ATTR: True})
    assert model_uses_market(offset) is True


def test_competition_saves_each_candidates_own_input_list():
    source = open('backtest.py').read()
    assert "competitor_columns = list(competitor.get('feature_names') or X.columns)" in source
    assert "'feature_names': list(X_mkt.columns)," in source


def test_live_scoring_fills_the_market_column_for_offset_models():
    source = open('ml_predict.py').read()
    assert "if MARKET_OFFSET_COL in model_features:" in source
    assert "X_raw[MARKET_OFFSET_COL] = " in source


def test_offset_model_survives_save_and_load(tmp_path):
    import joblib
    X, y, races, _sp = _races(n_races=150)
    model = RaceConditionalLogit(MarketOffsetClassifier(n_estimators=10))
    model.fit(X, y, race_ids=races)
    setattr(model, USES_MARKET_OFFSET_ATTR, True)
    path = tmp_path / 'm.pkl'
    joblib.dump(model, path)
    loaded = joblib.load(path)
    assert model_uses_market(loaded)
    assert np.allclose(loaded.predict_win_probabilities(X.iloc[:8]), model.predict_win_probabilities(X.iloc[:8]))


def test_offset_model_passes_the_feature_contract():
    live = backtest._live_scoring_feature_names()
    model = SimpleNamespace(feature_names_in_=live + [MARKET_OFFSET_COL])
    assert backtest._artifact_feature_contract_ok(model, live + [MARKET_OFFSET_COL])
