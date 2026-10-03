"""Race win probabilities: produced per race, judged per race, used per race.

Four changes, one idea — a horse's chance of winning only means something
against the other runners in its race:

1. Live scoring stores the model's own race win probabilities and prices value
   edge and Kelly stakes from them, never from ml_score (a 0-100 display
   stretch that pins the last runner to 0 and inflates the leaders).
2. Every candidate model is wrapped in a conditional logit whose beta is fitted
   on races the model never trained on, so its numbers sum to 1 per race and
   are as sharp as the results say they should be.
3. Models are judged on race-level log loss — the probability given to the
   runner that actually won — relative to the FLB-corrected market.
4. The market side of a live value edge is the market's fair (Shin-corrected)
   probability, the same reading the nightly validation uses.
"""
import json
import os
import pickle
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("sklearn")
pytest.importorskip("scipy")

from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression

import backtest
import ml_predict
from market_probability import fair_probabilities
from model_classes import (
    ConsensusRegressor,
    RaceConditionalLogit,
    fit_conditional_logit_beta,
    race_conditional_logit,
    race_normalised_win_probabilities,
    race_winner_log_loss,
    set_race_context,
)


FIELD = 8


def synthetic_races(n_races=400, seed=0, strength=1.5):
    """Races won by the runner with the highest speed plus Gumbel noise — the
    exact data-generating process of a conditional logit, so the true winner
    probabilities are known: softmax(strength * speed) within each race."""
    rng = np.random.default_rng(seed)
    rows = n_races * FIELD
    speed = rng.normal(size=rows)
    won = np.zeros(rows, dtype=int)
    race_ids = []
    for r in range(n_races):
        block = slice(r * FIELD, (r + 1) * FIELD)
        won[block][int(np.argmax(strength * speed[block] + rng.gumbel(size=FIELD)))] = 1
        race_ids.extend([f"race{r}"] * FIELD)
    X = pd.DataFrame({"speed": speed, "noise": rng.normal(size=rows)})
    return X, pd.Series(won), race_ids


def race_sums(probabilities, race_ids):
    return pd.Series(probabilities).groupby(pd.Series(race_ids)).sum()


# ── 2. The conditional logit itself ──────────────────────────────────────────

class TestRaceConditionalLogitMaths:
    def test_every_race_sums_to_one(self):
        out = race_conditional_logit([0.5, 0.2, 0.1, 0.3, 0.3], ["a", "a", "a", "b", "b"], beta=1.7)
        sums = race_sums(out, ["a", "a", "a", "b", "b"])
        assert np.allclose(sums.values, 1.0)

    def test_beta_one_is_dividing_by_the_race_total(self):
        out = race_conditional_logit([0.5, 0.2, 0.1], ["a"] * 3, beta=1.0)
        assert np.allclose(out, np.array([0.5, 0.2, 0.1]) / 0.8)

    def test_beta_above_one_sharpens_and_below_one_flattens(self):
        base = [0.5, 0.3, 0.2]
        sharp = race_conditional_logit(base, ["a"] * 3, beta=2.0)
        flat = race_conditional_logit(base, ["a"] * 3, beta=0.5)
        assert sharp[0] > 0.5 > flat[0]
        assert list(np.argsort(sharp)) == list(np.argsort(flat)) == list(np.argsort(base))

    def test_a_zero_or_missing_output_gets_a_floor_not_a_crash(self):
        out = race_conditional_logit([0.6, 0.0, np.nan], ["a"] * 3)
        assert np.all(np.isfinite(out))
        assert out[0] > 0.99

    def test_fitted_beta_recovers_how_timid_a_model_was(self):
        """Outputs that are the true probabilities square-rooted are too flat
        by exactly a factor of two in log space; the fit should find ~2."""
        X, y, race_ids = synthetic_races(n_races=1500, seed=3)
        logits = 1.5 * X["speed"].to_numpy()
        true_p = race_conditional_logit(np.exp(logits), race_ids, 1.0)
        timid = np.sqrt(true_p)
        fit = fit_conditional_logit_beta(timid, y.to_numpy(), race_ids)
        assert fit["status"] == "fitted"
        assert 1.7 < fit["beta"] < 2.3
        assert fit["log_loss"] < fit["identity_log_loss"]

    def test_too_few_races_leaves_beta_at_one(self):
        X, y, race_ids = synthetic_races(n_races=10)
        fit = fit_conditional_logit_beta(np.full(len(X), 0.2), y.to_numpy(), race_ids)
        assert fit["beta"] == 1.0
        assert fit["status"] == "insufficient_races"


class TestRaceWinnerLogLoss:
    def test_it_is_minus_log_of_the_winners_share(self):
        loss, races = race_winner_log_loss([0.6, 0.4, 0.25, 0.75], [1, 0, 0, 1], ["a", "a", "b", "b"])
        assert races == 2
        assert loss == pytest.approx(-(np.log(0.6) + np.log(0.75)) / 2)

    def test_outputs_are_renormalised_per_race_before_scoring(self):
        """A model whose raw numbers do not sum to 1 is scored on the race
        probabilities they imply, not penalised for their scale."""
        loss_raw, _ = race_winner_log_loss([0.3, 0.2], [1, 0], ["a", "a"])
        loss_norm, _ = race_winner_log_loss([0.6, 0.4], [1, 0], ["a", "a"])
        assert loss_raw == pytest.approx(loss_norm)

    def test_dead_heats_no_winner_and_one_horse_races_are_left_out(self):
        loss, races = race_winner_log_loss(
            [0.5, 0.5, 0.5, 0.5, 1.0, 0.7, 0.3],
            [1, 1, 0, 0, 1, 1, 0],
            ["heat", "heat", "none", "none", "solo", "ok", "ok"],
        )
        assert races == 1
        assert loss == pytest.approx(-np.log(0.7))


class RecordingClassifier(LogisticRegression):
    """Logistic regression that remembers the rows of every fit, so a test can
    prove which races the held-out model did and did not see."""

    fitted_on = []

    def fit(self, X, y, sample_weight=None):
        RecordingClassifier.fitted_on.append(set(X.index))
        return super().fit(X, y, sample_weight=sample_weight)


class TestRaceConditionalLogitEstimator:
    def test_fit_refuses_to_guess_the_races(self):
        X, y, _race_ids = synthetic_races(n_races=100)
        with pytest.raises(ValueError, match="race"):
            RaceConditionalLogit(LogisticRegression()).fit(X, y)

    def test_predictions_sum_to_one_per_race_and_beat_plain_renormalisation(self):
        X, y, race_ids = synthetic_races(n_races=900, seed=1)
        train, test = slice(0, 600 * FIELD), slice(600 * FIELD, None)
        model = RaceConditionalLogit(LogisticRegression())
        model.fit(X.iloc[train], y.iloc[train], race_ids=race_ids[train])
        assert model.calibration_["status"] == "fitted"

        set_race_context(model, race_ids[test])
        p = model.predict_proba(X.iloc[test])[:, 1]
        assert np.allclose(race_sums(p, race_ids[test]).values, 1.0)

        bare = LogisticRegression().fit(X.iloc[train], y.iloc[train])
        renormalised = race_conditional_logit(bare.predict_proba(X.iloc[test])[:, 1], race_ids[test], 1.0)
        calibrated_loss, _ = race_winner_log_loss(p, y.iloc[test].to_numpy(), race_ids[test])
        renormalised_loss, _ = race_winner_log_loss(renormalised, y.iloc[test].to_numpy(), race_ids[test])
        assert calibrated_loss < renormalised_loss

    def test_beta_is_fitted_on_the_latest_races_the_held_out_model_never_saw(self):
        X, y, race_ids = synthetic_races(n_races=300, seed=2)
        RecordingClassifier.fitted_on = []
        model = RaceConditionalLogit(RecordingClassifier(), calibration_fraction=0.2)
        model.fit(X, y, race_ids=race_ids)

        held_out_fit, full_refit = RecordingClassifier.fitted_on
        calibration_rows = set(range(240 * FIELD, 300 * FIELD))
        assert held_out_fit.isdisjoint(calibration_rows)
        assert held_out_fit == set(range(240 * FIELD))
        # refit_full: the shipped model still learns from the newest races.
        assert full_refit == set(range(300 * FIELD))
        assert model.calibration_["races"] == 60

    def test_without_context_one_call_is_one_race(self):
        X, y, race_ids = synthetic_races(n_races=200)
        model = RaceConditionalLogit(LogisticRegression()).fit(X, y, race_ids=race_ids)
        p = model.predict_proba(X.iloc[:FIELD])[:, 1]
        assert p.sum() == pytest.approx(1.0)

    def test_context_is_cleared_after_each_call(self):
        X, y, race_ids = synthetic_races(n_races=200)
        model = RaceConditionalLogit(LogisticRegression()).fit(X, y, race_ids=race_ids)
        set_race_context(model, race_ids[:2 * FIELD])
        model.predict_proba(X.iloc[:2 * FIELD])
        assert getattr(model, "_race_context", None) is None

    def test_survives_clone_and_pickle(self):
        X, y, race_ids = synthetic_races(n_races=200)
        model = RaceConditionalLogit(LogisticRegression()).fit(X, y, race_ids=race_ids)
        assert isinstance(clone(model).estimator, LogisticRegression)
        restored = pickle.loads(pickle.dumps(model))
        assert restored.beta_ == model.beta_
        assert np.allclose(
            restored.predict_proba(X.iloc[:FIELD]), model.predict_proba(X.iloc[:FIELD]),
        )

    def test_feature_names_travel_on_the_wrapper(self):
        """backtest._stored_feature_list and ml_predict's feature contract read
        feature_names_in_ off the artifact itself."""
        X, y, race_ids = synthetic_races(n_races=100)
        model = RaceConditionalLogit(LogisticRegression()).fit(X, y, race_ids=race_ids)
        assert list(model.feature_names_in_) == ["speed", "noise"]
        assert backtest._stored_feature_list(model) == ["speed", "noise"]

    def test_an_ensemble_of_calibrated_members_is_still_a_race_book(self):
        X, y, race_ids = synthetic_races(n_races=300)
        ensemble = ConsensusRegressor([
            ("a", RaceConditionalLogit(LogisticRegression())),
            ("b", RaceConditionalLogit(LogisticRegression(C=0.1))),
        ])
        backtest._fit_candidate(ensemble, X, y, race_ids=race_ids)
        p = backtest._predict_win_scores(ensemble, X, race_ids=race_ids)
        assert np.allclose(race_sums(p, race_ids).values, 1.0)

    def test_a_ranker_is_calibrated_too(self):
        pytest.importorskip("xgboost")
        from model_classes import RaceGroupedRanker

        X, y, race_ids = synthetic_races(n_races=400, seed=4)
        model = RaceConditionalLogit(RaceGroupedRanker(n_estimators=40))
        backtest._fit_candidate(model, X, y, race_ids=race_ids)
        p = backtest._predict_win_scores(model, X, race_ids=race_ids)
        assert np.allclose(race_sums(p, race_ids).values, 1.0)
        assert model.calibration_["status"] == "fitted"
        # A pairwise ranker never learns how confident to be, so its softmax
        # is not left at whatever spread its score scale happened to produce.
        assert model.beta_ != 1.0

    def test_fold_clone_reaches_the_calibrated_classifier_inside(self):
        wrapped = RaceConditionalLogit(CalibratedClassifierCV(LogisticRegression(), cv=5))
        cloned = backtest._clone_for_fold_fit(wrapped, pd.Series([0, 0, 0, 1, 1, 1, 1]))
        from sklearn.model_selection import StratifiedKFold
        assert isinstance(cloned.estimator.cv, StratifiedKFold)
        assert cloned.estimator.cv.n_splits == 3

    def test_every_competition_candidate_is_wrapped(self):
        source = Path("backtest.py").read_text()
        start = source.index("def run_model_competition(")
        body = source[start:]
        wrap = "candidates = {mt: RaceConditionalLogit(model) for mt, model in candidates.items()}"
        assert wrap in body
        assert body.index(wrap) < body.index("_fit_candidate(model, X_train, y_won[train_mask], race_ids=race_ids_train)")


class TestLegacyArtifacts:
    def test_a_bare_classifier_is_renormalised_per_race(self):
        X, y, race_ids = synthetic_races(n_races=50)
        bare = LogisticRegression().fit(X, y)
        p = race_normalised_win_probabilities(bare, X, race_ids)
        assert np.allclose(race_sums(p, race_ids).values, 1.0)

    def test_live_scoring_renormalises_an_old_champion(self):
        class OldChampion:
            def predict_proba(self, X):
                p = np.array([0.3, 0.2, 0.1, 0.05])
                return np.column_stack([1 - p, p])

        raw, _method = ml_predict._predict_raw_scores(OldChampion(), pd.DataFrame({"x": range(4)}))
        probabilities, source = ml_predict.race_win_probabilities(OldChampion(), raw)
        assert source == "renormalised_legacy_artifact"
        assert np.allclose(probabilities, np.array([0.3, 0.2, 0.1, 0.05]) / 0.65)

    def test_live_scoring_passes_a_calibrated_champion_through(self):
        X, y, race_ids = synthetic_races(n_races=200)
        model = RaceConditionalLogit(LogisticRegression()).fit(X, y, race_ids=race_ids)
        raw, _method = ml_predict._predict_raw_scores(model, X.iloc[:FIELD])
        probabilities, source = ml_predict.race_win_probabilities(model, raw)
        assert source == "conditional_logit"
        assert np.allclose(probabilities, raw)


# ── 3. Judged per race ───────────────────────────────────────────────────────

class FixedModel:
    def __init__(self, scores):
        self.scores = np.asarray(scores, dtype=float)

    def predict_proba(self, X):
        p = self.scores[: len(X)]
        return np.column_stack([1 - p, p])


def _frame(races):
    race_ids, sps, wons, preds = [], [], [], []
    for race_id, runners in races:
        for sp, won, pred in runners:
            race_ids.append(race_id)
            sps.append(sp)
            wons.append(won)
            preds.append(pred)
    X = pd.DataFrame({"f": np.arange(len(race_ids), dtype=float)})
    return X, pd.Series(wons), race_ids, np.array(sps, dtype=float), preds


BOOKS = [
    ("r1", [2.2, 3.6, 5.5, 9.0, 14.0, 26.0], 1),
    ("r2", [2.8, 3.4, 5.0, 8.5, 16.0, 31.0], 4),
    ("r3", [1.9, 4.2, 6.5, 11.0, 19.0, 41.0], 2),
]


def _races_with(pred_for):
    races = []
    for race_id, sps, winner in BOOKS:
        preds = pred_for(sps)
        races.append((race_id, [(sp, int(i == winner), p) for i, (sp, p) in enumerate(zip(sps, preds))]))
    return races


class TestRaceLevelMetrics:
    def test_a_model_that_is_the_market_scores_zero_against_it(self):
        X, y, race_ids, sp, preds = _frame(_races_with(fair_probabilities))
        metrics = backtest.evaluate_model_on_validation(FixedModel(preds), X, y, race_ids, sp)
        assert metrics["race_log_loss_comparison_races"] == 3
        assert metrics["race_log_loss_vs_market"] == pytest.approx(0.0, abs=1e-9)
        assert metrics["race_log_loss"] == pytest.approx(metrics["market_race_log_loss"])

    def test_a_flat_model_is_worse_than_the_market(self):
        # Favourites win all three: the market saw it coming, a flat model did not.
        races = [
            (race_id, [(price, int(i == 0), 1.0 / len(sps)) for i, price in enumerate(sps)])
            for race_id, sps, _winner in BOOKS
        ]
        X, y, race_ids, sp, preds = _frame(races)
        metrics = backtest.evaluate_model_on_validation(FixedModel(preds), X, y, race_ids, sp)
        assert metrics["race_log_loss"] == pytest.approx(np.log(6))
        assert metrics["race_log_loss_vs_market"] > 0

    def test_champion_score_rewards_beating_the_market_on_race_log_loss(self):
        base = {
            "roi": 0.0, "log_loss": 0.3, "brier_score": 0.08,
            "calibration": {"expected_calibration_error": 0.01},
            "stability": {"roi_last_100": 0.0, "roi_last_250": 0.0},
        }
        better = backtest._selection_score_from_metrics({**base, "race_log_loss_vs_market": -0.05})
        worse = backtest._selection_score_from_metrics({**base, "race_log_loss_vs_market": 0.20})
        assert better - worse == pytest.approx(10.0 * 0.25)

    def test_a_record_without_the_race_metric_is_scored_by_the_legacy_term(self):
        base = {
            "roi": 0.0, "log_loss": 0.3, "brier_score": 0.0,
            "calibration": {"expected_calibration_error": 0.0},
            "stability": {"roi_last_100": 0.0, "roi_last_250": 0.0},
        }
        assert backtest._selection_score_from_metrics(base) == pytest.approx(-3.0)

    def test_race_log_loss_is_a_required_component_under_a_new_version(self):
        assert "race_log_loss" in backtest.REQUIRED_SELECTION_METRIC_COMPONENTS
        assert backtest.SCORING_FORMULA_VERSION == "champion_score_v8_race_log_loss"

    def test_walk_forward_folds_carry_race_log_loss(self):
        source = Path("backtest.py").read_text()
        start = source.index("def _walk_forward_metrics_for_alphas(")
        body = source[start:source.index("\ndef ", start + 1)]
        assert "'race_log_loss_vs_market': fold_metrics.get('race_log_loss_vs_market')" in body

    def test_value_edge_diagnostic_reads_the_fair_market_probability(self):
        X, y, race_ids, sp, preds = _frame(_races_with(fair_probabilities))
        selections = backtest._top_selection_rows(FixedModel(preds), X, y, race_ids, sp)
        assert "market_prob" in selections
        # The model IS the market here, so every top pick's edge is zero and
        # every threshold above zero keeps nothing.
        analysis = backtest._value_edge_backtest(selections)
        kept = {row["min_edge"]: row["bets"] for row in analysis["thresholds"]}
        assert kept[0.0] == 3
        assert kept[0.02] == 0


# ── 1. Live probabilities come from the model ────────────────────────────────

class TestRaceFairProbabilities:
    def test_the_min_max_share_is_not_a_probability(self):
        """The bug being fixed, pinned with the numbers from the review: a 28%
        favourite in a ten-runner race came out at 33%, the last runner at 0."""
        probabilities = [0.28, 0.18, 0.12, 0.10, 0.08, 0.07, 0.06, 0.05, 0.04, 0.02]
        p = np.array(probabilities)
        ml_scores = (p - p.min()) / (p.max() - p.min()) * 100
        legacy = ml_predict.derive_ml_fair_probabilities(ml_scores)
        assert round(legacy[0], 2) == 0.33
        assert legacy[-1] is None

        fair, source = ml_predict.race_fair_probabilities(probabilities, ml_scores)
        assert source == ml_predict.PROBABILITY_SOURCE_MODEL
        assert fair == pytest.approx(probabilities)

    def test_a_late_scratching_spreads_its_share_over_the_field(self):
        fair, source = ml_predict.race_fair_probabilities([0.5, 0.3, None], [100.0, 40.0, None])
        assert source == ml_predict.PROBABILITY_SOURCE_MODEL
        assert fair == pytest.approx([0.625, 0.375, None])

    def test_only_a_race_with_no_stored_probabilities_falls_back(self):
        fair, source = ml_predict.race_fair_probabilities([None, None], [60.0, 40.0])
        assert source == ml_predict.PROBABILITY_SOURCE_LEGACY
        assert fair == pytest.approx([0.6, 0.4])

    def test_nothing_to_go_on_is_said_plainly(self):
        fair, source = ml_predict.race_fair_probabilities([None], [None])
        assert source is None
        assert fair == [None]


class TestMlBook:
    def test_book_prefers_stored_probabilities_and_labels_them(self):
        from app import _derive_ml_race_book

        runners = [
            {"ml_score": 100.0, "p": 0.40},
            {"ml_score": 50.0, "p": 0.35},
            {"ml_score": 0.0, "p": 0.25},
        ]
        book = _derive_ml_race_book(runners, lambda h: h["ml_score"], probability_getter=lambda h: h["p"])
        assert [round(book[i]["ml_fair_probability"], 6) for i in range(3)] == [0.40, 0.35, 0.25]
        assert {entry["probability_source"] for entry in book.values()} == {ml_predict.PROBABILITY_SOURCE_MODEL}
        # The bottom-ranked runner is a real runner with a real chance, not 0.
        assert 2 in book

    def test_book_without_stored_probabilities_is_marked_legacy(self):
        from app import _derive_ml_race_book

        book = _derive_ml_race_book([{"ml_score": 60}, {"ml_score": 40}], lambda h: h["ml_score"])
        assert {entry["probability_source"] for entry in book.values()} == {ml_predict.PROBABILITY_SOURCE_LEGACY}

    def test_meeting_view_hands_only_model_probabilities_to_edge_and_kelly(self):
        source = Path("app.py").read_text()
        start = source.index("def ml_view_meeting(")
        body = source[start:source.index("\ndef ", start + 1)]
        assert "probability_getter=lambda h: None if h.get('is_scratched') else h.get('ml_model_win_probability')" in body
        assert "if book_entry['probability_source'] == PROBABILITY_SOURCE_MODEL:" in body


class TestScoringPersistsProbabilities:
    def test_scoring_writes_the_probability_beside_the_score(self):
        source = Path("ml_shadow_routes.py").read_text()
        start = source.index("def _score_meeting_ml")
        scorer = source[start:source.index("def _reprice_meeting_market", start)]
        assert "return_probabilities=True" in scorer
        assert "pred.ml_win_probability = win_probabilities.get(horse_id)" in scorer
        assert "probabilities_by_race=probabilities_by_race" in scorer

    def test_unrun_meetings_scored_before_probabilities_existed_are_rescored(self):
        source = Path("ml_shadow_routes.py").read_text()
        start = source.index("def ml_shadow_score_visible")
        bulk = source[start:source.index("@app.route('/api/ml-shadow/results", start)]
        assert "needs_probabilities = _meetings_needing_win_probabilities(db)" in bulk
        assert "if meeting.id in scored_ids and meeting.id not in needs_probabilities:" in bulk

    def test_the_column_exists_and_is_migrated(self):
        from models import Prediction

        assert "ml_win_probability" in Prediction.__table__.columns
        assert "ALTER TABLE predictions ADD COLUMN ml_win_probability FLOAT" in Path("app.py").read_text()


# ── 4. The market side of an edge is the market's fair probability ──────────

class TestLiveMarketProbabilities:
    def test_a_complete_book_is_shin_corrected(self):
        prices = [2.2, 3.6, 5.5, 9.0, 14.0, 26.0]
        market, method = ml_predict.live_market_probabilities(prices)
        assert method == ml_predict.MARKET_PROBABILITY_METHOD_SHIN
        assert market == pytest.approx(fair_probabilities(prices))
        assert sum(market) == pytest.approx(1.0)
        # Margin removed: every runner's fair chance is below its raw 1/price.
        assert all(m < 1.0 / p for m, p in zip(market, prices))

    def test_an_incomplete_book_falls_back_to_raw_implied(self):
        market, method = ml_predict.live_market_probabilities([2.2, None, 5.5])
        assert method == ml_predict.MARKET_PROBABILITY_METHOD_RAW
        assert market[1] is None
        assert market[0] == pytest.approx(1 / 2.2)
        assert market[2] == pytest.approx(1 / 5.5)

    def test_an_underround_book_is_normalised(self):
        market, method = ml_predict.live_market_probabilities([2.5, 2.5, 6.0])
        assert method == ml_predict.MARKET_PROBABILITY_METHOD_NORMALISED
        assert sum(market) == pytest.approx(1.0)

    def test_fallback_pricing_path_uses_the_same_reading(self):
        source = Path("app.py").read_text()
        start = source.index("def _apply_joint_kelly_stakes(")
        body = source[start:source.index("\ndef ", start + 1)]
        assert "live_market_probabilities(" in body
        assert "implied_pct = 100.0 / price" not in body


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_browser_port_of_the_market_correction_matches_the_server():
    """The ML meeting page repaints edges from every live poll. Its copy of
    the Shin correction must give the same market probability the server
    stored for the same prices, or the repainted edge disagrees with it."""
    template = Path("templates/MLRaceMeetings.html").read_text()
    start = template.index("var SHIN_MAX_ITERATIONS")
    end = template.index("function updateEdgeCell(")
    js = template[start:end]
    books = [
        [2.2, 3.6, 5.5, 9.0, 14.0, 26.0],
        [1.5, 4.0, 8.0, 21.0],
        [2.5, 2.5, 6.0],
        [2.2, float("nan"), 5.5],
    ]
    script = js + "\nconsole.log(JSON.stringify(%s.map(function(b){ return marketFairPcts(b); })));" % (
        json.dumps(books).replace("NaN", "NaN")
    )
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True).stdout
    js_results = json.loads(out.replace("NaN", "null"))
    for prices, js_pcts in zip(books, js_results):
        clean = [None if (p != p) else p for p in prices]
        py_probs, _method = ml_predict.live_market_probabilities(clean)
        for js_value, py_value in zip(js_pcts, py_probs):
            if py_value is None:
                assert js_value is None
            else:
                assert js_value == pytest.approx(py_value * 100.0, abs=1e-6)


class _Prediction:
    def __init__(self, horse_id, ml_score, ml_win_probability):
        self.horse_id = horse_id
        self.ml_score = ml_score
        self.ml_win_probability = ml_win_probability
        self.kelly_stake_pct = None


class _Horse:
    def __init__(self, horse_id, ml_score, probability):
        self.id = horse_id
        self.is_scratched = False
        self.prediction = _Prediction(horse_id, ml_score, probability)


class _Race:
    def __init__(self, horses):
        self.id = 1
        self.race_number = 1
        self.horses = horses


class _RaceEntity:
    pass


class _Session:
    def __init__(self, race, rows):
        self.race = race
        self.rows = rows

    def query(self, _entity):
        session = self

        class _Q:
            def filter_by(self, **_kwargs):
                return self

            def all(self):
                return [session.race]

        return _Q()

    def execute(self, _statement, _params=None):
        rows = self.rows

        class _R:
            def mappings(self):
                return self

            def all(self):
                return rows

        return _R()


def _priced_session(monkeypatch, horses, prices):
    from datetime import datetime, timedelta, timezone

    rows = [
        {"horse_id": horse.id, "odds": price, "source": "ladbrokes",
         "captured_at": datetime.now(timezone.utc) - timedelta(seconds=30), "is_scratched": False}
        for horse, price in zip(horses, prices) if price is not None
    ]
    fake_models = types.ModuleType("models")
    fake_models.Race = _RaceEntity
    monkeypatch.setitem(sys.modules, "models", fake_models)
    return _Session(_Race(horses), rows)


class TestLivePricing:
    def test_a_race_scored_before_probabilities_existed_quotes_no_edge_or_stake(self, monkeypatch):
        horses = [_Horse(1, 100.0, None), _Horse(2, 50.0, None), _Horse(3, 0.0, None)]
        session = _priced_session(monkeypatch, horses, [3.0, 2.5, 4.0])
        edges, diagnostics = ml_predict.compute_live_market_edges_for_meeting(1, session)
        assert diagnostics["races_without_model_probabilities"] == 1
        assert all(edge["value_edge_pct"] is None for edge in edges.values())
        # An explicit zero, so a stake from an earlier (wrong) pricing is cleared.
        assert all(edge["kelly_stake_pct"] == 0.0 for edge in edges.values())

    def test_fresh_probabilities_win_over_stored_ones(self, monkeypatch):
        horses = [_Horse(1, 100.0, 0.10), _Horse(2, 0.0, 0.90)]
        session = _priced_session(monkeypatch, horses, [2.0, 2.0])
        edges, _diagnostics = ml_predict.compute_live_market_edges_for_meeting(
            1, session, probabilities_by_race={1: {1: 0.7, 2: 0.3}},
        )
        assert edges[1]["ml_fair_probability_pct"] == 70.0
        assert edges[2]["ml_fair_probability_pct"] == 30.0

    def test_the_favourite_is_not_inflated_by_the_display_stretch(self, monkeypatch):
        """A 40% favourite in a five-runner field used to be priced at ~53%."""
        probabilities = [0.40, 0.25, 0.15, 0.12, 0.08]
        p = np.array(probabilities)
        ml_scores = (p - p.min()) / (p.max() - p.min()) * 100
        horses = [_Horse(i + 1, float(s), q) for i, (s, q) in enumerate(zip(ml_scores, probabilities))]
        session = _priced_session(monkeypatch, horses, [2.2, 3.6, 6.0, 8.0, 11.0])
        edges, diagnostics = ml_predict.compute_live_market_edges_for_meeting(1, session)
        assert edges[1]["ml_fair_probability_pct"] == 40.0
        assert edges[5]["ml_fair_probability_pct"] == 8.0
        assert edges[1]["market_probability_method"] == ml_predict.MARKET_PROBABILITY_METHOD_SHIN
        assert diagnostics["races_market_shin_corrected"] == 1

    def test_a_partly_priced_race_reads_the_market_raw(self, monkeypatch):
        horses = [_Horse(1, 100.0, 0.5), _Horse(2, 50.0, 0.3), _Horse(3, 0.0, 0.2)]
        session = _priced_session(monkeypatch, horses, [2.0, 3.0, None])
        edges, _diagnostics = ml_predict.compute_live_market_edges_for_meeting(1, session)
        assert edges[1]["market_probability_method"] == ml_predict.MARKET_PROBABILITY_METHOD_RAW
        assert edges[1]["market_implied_probability_pct"] == 50.0
        assert 3 not in edges
