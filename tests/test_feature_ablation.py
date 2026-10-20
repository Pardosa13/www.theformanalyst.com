"""feature_ablation: variant building and an end-to-end run on synthetic races."""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")
pytest.importorskip("lightgbm")

import feature_ablation
import ml_predict


def test_variants_cover_every_group_and_keep_column_order():
    cols = list(ml_predict.FEATURE_NAMES)
    variants, present = feature_ablation.build_variants(cols)
    assert variants['all'] == cols
    assert not set(variants['baseline']) & set(present['past_runs'])
    assert set(present['pf_ratings']) <= set(variants['baseline'])
    for name, vcols in variants.items():
        assert vcols == [c for c in cols if c in set(vcols)], name
    assert set(variants) >= {'+past_runs', '+track_bias', '+ratings_prices', '+pf_form_feed',
                             'all-past_runs', 'all-pf_ratings'}
    # Every group column is a real feature
    for group, gcols in present.items():
        assert set(gcols) <= set(cols), group


def test_end_to_end_flags_a_group_that_carries_signal():
    rng = np.random.default_rng(0)
    cols = list(ml_predict.FEATURE_NAMES)
    n_races, runners = 360, 8
    n = n_races * runners
    X = pd.DataFrame(rng.normal(size=(n, len(cols))), columns=cols)
    race_ids = np.repeat(np.arange(n_races), runners)
    # The winner of each race is the runner with the best (lowest) pr_avg_margin_l3, plus noise.
    strength = -X['pr_avg_margin_l3'].to_numpy() + rng.normal(scale=0.3, size=n)
    won = np.zeros(n, dtype=int)
    for r in range(n_races):
        idx = np.where(race_ids == r)[0]
        won[idx[np.argmax(strength[idx])]] = 1
    sp = rng.uniform(2.0, 15.0, size=n)
    dates = pd.date_range('2026-01-01', periods=n_races, freq='D').repeat(runners)

    report = feature_ablation.run_feature_ablation(
        X, pd.Series(won), list(sp), list(race_ids), list(dates), run_id=1)

    res = report['results']
    assert res['baseline'] and res['+past_runs'] and res['all']
    # Adding the group with the signal must beat the baseline on log loss vs market
    assert res['+past_runs']['race_log_loss_vs_market'] < res['baseline']['race_log_loss_vs_market']
    assert res['all-past_runs']['race_log_loss_vs_market'] > res['all']['race_log_loss_vs_market']
    assert report['gain_share_by_group_pct']['past_runs'] > 20
    assert report['verdicts']['past_runs']['verdict'] == 'KEEP'
    # Pure-noise groups must not be marked KEEP
    assert report['verdicts']['ratings_prices']['verdict'] != 'KEEP'
