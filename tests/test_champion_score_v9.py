"""Champion Score v9: accuracy against the market decides, ROI only nudges."""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
pytest.importorskip("numpy")

import backtest


def _metrics(fold_gaps, fold_rois, holdout_gap=0.0, holdout_roi=0.0):
    return {
        "roi": holdout_roi, "race_log_loss_vs_market": holdout_gap,
        "calibration": {"expected_calibration_error": 0.0}, "stability": {},
        "walk_forward": {"folds": [
            {"bets": 100, "roi": roi, "race_log_loss_vs_market": gap}
            for gap, roi in zip(fold_gaps, fold_rois)
        ]},
    }


def score(m):
    return backtest._selection_score_from_metrics(m, force_recompute=True)


def test_beating_the_market_outranks_a_lucky_roi():
    # Better than the market by 0.01 nats every fold, but lost money.
    accurate = _metrics([-0.01, -0.01, -0.01], [-15.0, -12.0, -10.0])
    # Worse than the market, but a few long-priced winners made ROI positive.
    lucky = _metrics([0.01, 0.01, 0.01], [25.0, 30.0, 20.0])
    assert score(accurate) > score(lucky)


def test_scale_is_ten_points_per_hundredth_of_a_nat():
    a = _metrics([-0.01] * 3, [0.0] * 3, holdout_gap=-0.01)
    b = _metrics([0.0] * 3, [0.0] * 3, holdout_gap=0.0)
    assert score(a) - score(b) == pytest.approx(10.0)


def test_unsteady_folds_cost_points():
    steady = _metrics([-0.01, -0.01, -0.01], [0.0] * 3, holdout_gap=-0.01)
    swingy = _metrics([-0.04, 0.02, -0.01], [0.0] * 3, holdout_gap=-0.01)
    assert score(steady) > score(swingy)


def test_holdout_cannot_rescue_folds_that_all_lose_to_the_market():
    folds_bad = _metrics([0.01, 0.02, 0.01], [0.0] * 3, holdout_gap=-0.10)
    folds_bad_no_holdout_help = _metrics([0.01, 0.02, 0.01], [0.0] * 3, holdout_gap=0.0133333333)
    assert score(folds_bad) <= score(folds_bad_no_holdout_help) + 1e-6


def test_fold_market_skill_is_negated_gap():
    wf = {"folds": [{"bets": 5, "race_log_loss_vs_market": 0.02},
                    {"bets": 0, "race_log_loss_vs_market": -1.0},
                    {"bets": 5, "race_log_loss_vs_market": None},
                    {"bets": 5, "race_log_loss_vs_market": -0.03}]}
    assert backtest._fold_market_skill(wf) == [-0.02, 0.03]


def test_promotion_bootstrap_reads_fold_log_loss():
    source = open("backtest.py").read()
    assert "_paired_bootstrap_p_value(challenger_fold_skill, champion_fold_skill)" in source
