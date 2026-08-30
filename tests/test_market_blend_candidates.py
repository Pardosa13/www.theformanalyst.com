"""The model/market blend: fitting alpha, and the two forms a candidate competes in.

market_probability.blend_probabilities is unit-tested in
test_market_probability.py. These tests cover the pipeline decisions built on
top of it — how alpha is chosen, when a blended variant enters the competition
and when it deliberately does not, and that the live path blends exactly once.
"""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")

import backtest
import ml_predict


# Three complete six-runner books with real overrounds.
RACE_SPS = {
    'r1': [2.2, 3.6, 5.5, 9.0, 14.0, 26.0],
    'r2': [2.8, 3.4, 5.0, 8.5, 16.0, 31.0],
    'r3': [1.9, 4.2, 6.5, 11.0, 19.0, 41.0],
}


class ScriptedModel:
    """Returns fixed probabilities, so the blend's effect is the only variable."""

    def __init__(self, scores):
        self.scores = list(scores)

    def predict_proba(self, X):
        scores = np.asarray(self.scores[: len(X)], dtype=float)
        return np.column_stack([1.0 - scores, scores])


def _frame(winner_index_by_race, model_scores):
    race_ids, sps, wons = [], [], []
    for race_id, sp_list in RACE_SPS.items():
        for position, sp in enumerate(sp_list):
            race_ids.append(race_id)
            sps.append(sp)
            wons.append(1 if position == winner_index_by_race[race_id] else 0)
    X = pd.DataFrame({'feature': np.arange(len(race_ids), dtype=float)})
    return ScriptedModel(model_scores), X, pd.Series(wons), race_ids, np.array(sps, dtype=float)


# A model that is confidently wrong: it likes the longest price in every race,
# and the favourite wins every race. Blending toward the market can only help.
CONFIDENTLY_WRONG = [0.02, 0.03, 0.04, 0.05, 0.10, 0.90] * 3
# And its mirror: a model that already backs the winning favourite, so a blend
# has little to add and the walk-forward folds are what decide.
CONFIDENTLY_RIGHT = [0.90, 0.10, 0.05, 0.04, 0.03, 0.02] * 3
FAVOURITE_WINS = {'r1': 0, 'r2': 0, 'r3': 0}


class TestBlendInEvaluation:
    def test_no_alpha_scores_the_model_as_it_stands(self):
        model, X, y, race_ids, sp = _frame(FAVOURITE_WINS, CONFIDENTLY_WRONG)
        plain = backtest.evaluate_model_on_validation(model, X, y, race_ids, sp)
        explicit = backtest.evaluate_model_on_validation(model, X, y, race_ids, sp, blend_alpha=None)
        assert plain['roi'] == explicit['roi']

    def test_alpha_one_is_identical_to_no_blend(self):
        """alpha = 1.0 must be a true no-op, not "blend with weight 1".

        The blend renormalises within a race, and these candidates emit
        independent per-horse probabilities that do not sum to 1 across a
        field — so running them through at alpha = 1.0 would still change
        log loss and Brier. The reference has to be untouched.
        """
        model, X, y, race_ids, sp = _frame(FAVOURITE_WINS, CONFIDENTLY_WRONG)
        plain = backtest.evaluate_model_on_validation(model, X, y, race_ids, sp)
        alpha_one = backtest.evaluate_model_on_validation(model, X, y, race_ids, sp, blend_alpha=1.0)
        assert alpha_one['log_loss'] == pytest.approx(plain['log_loss'])
        assert alpha_one['brier_score'] == pytest.approx(plain['brier_score'])
        assert alpha_one['roi'] == pytest.approx(plain['roi'])

    def test_blending_toward_the_market_rescues_a_confidently_wrong_model(self):
        model, X, y, race_ids, sp = _frame(FAVOURITE_WINS, CONFIDENTLY_WRONG)
        unblended = backtest.evaluate_model_on_validation(model, X, y, race_ids, sp)
        market_led = backtest.evaluate_model_on_validation(model, X, y, race_ids, sp, blend_alpha=0.1)
        assert unblended['strike_rate'] == 0.0
        assert market_led['strike_rate'] == 100.0

    def test_alpha_zero_bets_the_market_favourite_in_every_race(self):
        model, X, y, race_ids, sp = _frame(FAVOURITE_WINS, CONFIDENTLY_WRONG)
        metrics = backtest.evaluate_model_on_validation(model, X, y, race_ids, sp, blend_alpha=0.0)
        assert metrics['strike_rate'] == 100.0
        # And the picks are the shortest price in each race.
        assert metrics['average_selection_sp'] == pytest.approx(
            np.mean([min(sps) for sps in RACE_SPS.values()])
        )

    def test_selection_frame_reflects_the_blended_picks(self):
        model, X, y, race_ids, sp = _frame(FAVOURITE_WINS, CONFIDENTLY_WRONG)
        unblended = backtest._top_selection_rows(model, X, y, race_ids, sp)
        blended = backtest._top_selection_rows(model, X, y, race_ids, sp, blend_alpha=0.0)
        assert list(unblended['won']) == [0, 0, 0]
        assert list(blended['won']) == [1, 1, 1]


class TestBlendedCandidate:
    def _walk_forward_by_alpha(self, fold_rois_by_alpha):
        return {
            alpha: {
                'n_splits': len(rois), 'n_splits_requested': 3,
                'folds': [{'bets': 100, 'roi': roi, 'strike_rate': 12.0} for roi in rois],
                'roi_std': float(np.std(rois)) if len(rois) > 1 else 0.0,
                'strike_rate_std': 0.0, 'embargo_rows': 50,
            }
            for alpha, rois in fold_rois_by_alpha.items()
        }

    def _result(self, model_scores=CONFIDENTLY_WRONG):
        model, X, y, race_ids, sp = _frame(FAVOURITE_WINS, model_scores)
        metrics = backtest.evaluate_model_on_validation(model, X, y, race_ids, sp)
        return (
            {'model_type': 'random_forest', 'model_name': 'Random Forest',
             'model': model, 'metrics': metrics},
            X, y, race_ids, sp,
        )

    def test_a_winning_blend_becomes_a_separate_candidate(self):
        result, X, y, race_ids, sp = self._result()
        # Every blend weight below 1.0 does better out-of-sample than ignoring
        # the market.
        by_alpha = self._walk_forward_by_alpha(
            {alpha: ([-20.0, -18.0] if alpha >= 1.0 else [5.0, 6.0])
             for alpha in backtest.MARKET_BLEND_ALPHA_GRID}
        )
        blended = backtest._blended_candidate(result, by_alpha, [], X, y, race_ids, sp)
        assert blended is not None
        assert blended['model_type'] == 'random_forest_blended'
        assert blended['blend_alpha'] < 1.0
        assert blended['metrics']['market_blend_alpha'] == blended['blend_alpha']
        assert blended['metrics']['market_blend_base_model_type'] == 'random_forest'
        assert blended['metrics']['market_blend_odds_source'] == 'closing_sp_shin_corrected'

    def test_the_winning_alpha_is_stamped_on_the_artifact(self):
        result, X, y, race_ids, sp = self._result()
        by_alpha = self._walk_forward_by_alpha(
            {alpha: ([-20.0, -18.0] if alpha >= 1.0 else [5.0, 6.0])
             for alpha in backtest.MARKET_BLEND_ALPHA_GRID}
        )
        blended = backtest._blended_candidate(result, by_alpha, [], X, y, race_ids, sp)
        assert getattr(blended['model'], backtest.MARKET_BLEND_ALPHA_ATTR) == blended['blend_alpha']
        # And live scoring reads exactly that attribute back.
        assert ml_predict.model_market_alpha(blended['model']) == blended['blend_alpha']

    def test_stamping_alpha_does_not_leak_onto_the_base_candidate(self):
        """The base candidate must keep competing unblended."""
        result, X, y, race_ids, sp = self._result()
        by_alpha = self._walk_forward_by_alpha(
            {alpha: ([-20.0, -18.0] if alpha >= 1.0 else [5.0, 6.0])
             for alpha in backtest.MARKET_BLEND_ALPHA_GRID}
        )
        backtest._blended_candidate(result, by_alpha, [], X, y, race_ids, sp)
        assert not hasattr(result['model'], backtest.MARKET_BLEND_ALPHA_ATTR)
        assert ml_predict.model_market_alpha(result['model']) == 1.0

    def test_no_variant_when_ignoring_the_market_wins(self):
        """alpha = 1.0 winning is a result, not a failure — and adding the
        variant anyway would just duplicate the base candidate."""
        # A model that already agrees with the market on the holdout, so the
        # walk-forward folds are what decide — which is the case this test is
        # about.
        result, X, y, race_ids, sp = self._result(CONFIDENTLY_RIGHT)
        by_alpha = self._walk_forward_by_alpha(
            {alpha: ([40.0, 42.0] if alpha >= 1.0 else [-200.0, -201.0])
             for alpha in backtest.MARKET_BLEND_ALPHA_GRID}
        )
        assert backtest._blended_candidate(result, by_alpha, [], X, y, race_ids, sp) is None

    def test_the_search_grid_is_recorded_for_audit(self):
        result, X, y, race_ids, sp = self._result()
        by_alpha = self._walk_forward_by_alpha(
            {alpha: ([-20.0, -18.0] if alpha >= 1.0 else [5.0, 6.0])
             for alpha in backtest.MARKET_BLEND_ALPHA_GRID}
        )
        blended = backtest._blended_candidate(result, by_alpha, [], X, y, race_ids, sp)
        search = blended['metrics']['market_blend_alpha_search']
        assert len(search) == len(backtest.MARKET_BLEND_ALPHA_GRID)
        assert {row['alpha'] for row in search} == set(backtest.MARKET_BLEND_ALPHA_GRID)
        assert all('selection_score' in row for row in search)

    def test_alpha_grid_spans_the_whole_range(self):
        grid = backtest.MARKET_BLEND_ALPHA_GRID
        assert grid[0] == 0.0 and grid[-1] == 1.0
        assert len(grid) == 21


class TestWalkForwardAlphaGrid:
    def test_every_alpha_shares_one_fit_per_fold(self):
        """Refitting per alpha would multiply the nightly run's training time
        by the size of the grid; the grid is only affordable because it does
        not."""
        fits = {'count': 0}

        class CountingModel:
            def __init__(self):
                self.scores = None

            def get_params(self, deep=True):
                return {}

            def set_params(self, **_kwargs):
                return self

            def fit(self, X, y):
                fits['count'] += 1
                return self

            def predict_proba(self, X):
                rng = np.random.default_rng(0)
                p = rng.uniform(0.05, 0.4, size=len(X))
                return np.column_stack([1.0 - p, p])

        rows = 900
        rng = np.random.default_rng(1)
        X = pd.DataFrame({'feature': rng.normal(size=rows)})
        y = pd.Series((rng.uniform(size=rows) < 0.15).astype(int))
        race_ids = [f"race{i // 6}" for i in range(rows)]
        sp = np.tile([2.2, 3.6, 5.5, 9.0, 14.0, 26.0], rows // 6)

        alphas = [None, 0.0, 0.5, 1.0]
        results = backtest._walk_forward_metrics_for_alphas(
            CountingModel(), X, y, sp, race_ids, alphas=alphas,
        )
        assert set(results) == set(alphas)
        assert fits['count'] <= backtest.WALK_FORWARD_N_SPLITS
        assert all(results[alpha]['n_splits'] == results[None]['n_splits'] for alpha in alphas)

    def test_the_single_alpha_wrapper_still_returns_one_result(self):
        rows = 900
        rng = np.random.default_rng(2)
        X = pd.DataFrame({'feature': rng.normal(size=rows)})
        y = pd.Series((rng.uniform(size=rows) < 0.15).astype(int))
        race_ids = [f"race{i // 6}" for i in range(rows)]
        sp = np.tile([2.2, 3.6, 5.5, 9.0, 14.0, 26.0], rows // 6)
        model = ScriptedModel(list(np.linspace(0.05, 0.5, rows)))

        class Fittable(ScriptedModel):
            def get_params(self, deep=True):
                return {'scores': self.scores}

            def set_params(self, **kwargs):
                return self

            def fit(self, X, y):
                return self

        out = backtest._walk_forward_metrics_for_model(
            Fittable(model.scores), X, y, sp, race_ids
        )
        assert 'folds' in out and 'roi_std' in out


class TestLiveMarketBlend:
    HORSE_IDS = [1, 2, 3, 4, 5, 6]
    RAW = np.array([0.02, 0.03, 0.04, 0.05, 0.10, 0.90])
    ODDS = {
        1: {'odds': 2.2}, 2: {'odds': 3.6}, 3: {'odds': 5.5},
        4: {'odds': 9.0}, 5: {'odds': 14.0}, 6: {'odds': 26.0},
    }

    def test_no_alpha_leaves_the_model_alone(self):
        out, diagnostics = ml_predict._blend_race_with_live_market(
            self.RAW, self.HORSE_IDS, self.ODDS, 1.0
        )
        assert diagnostics is None
        assert out is self.RAW

    def test_a_blend_moves_the_top_pick_toward_the_favourite(self):
        out, diagnostics = ml_predict._blend_race_with_live_market(
            self.RAW, self.HORSE_IDS, self.ODDS, 0.1
        )
        assert diagnostics['applied'] is True
        assert diagnostics['priced_runners'] == 6
        assert int(np.argmax(self.RAW)) == 5
        assert int(np.argmax(out)) == 0

    def test_an_unpriced_runner_keeps_its_model_probability(self):
        odds = dict(self.ODDS)
        odds.pop(3)
        out, diagnostics = ml_predict._blend_race_with_live_market(
            self.RAW, self.HORSE_IDS, odds, 0.5
        )
        assert diagnostics['priced_runners'] == 5
        assert diagnostics['applied'] is True
        assert np.all(np.isfinite(out))

    def test_a_race_with_one_price_is_not_a_market(self):
        """Blending against a one-runner "book" would hand it probability 1.0."""
        out, diagnostics = ml_predict._blend_race_with_live_market(
            self.RAW, self.HORSE_IDS, {1: {'odds': 2.2}}, 0.5
        )
        assert diagnostics == {'priced_runners': 1, 'applied': False}
        assert out is self.RAW

    def test_no_live_odds_at_all_leaves_scoring_unchanged(self):
        out, diagnostics = ml_predict._blend_race_with_live_market(
            self.RAW, self.HORSE_IDS, {}, 0.5
        )
        assert diagnostics['applied'] is False
        assert out is self.RAW


class TestKellyDoesNotBlendTwice:
    """predict_meeting has already blended by the time these probabilities
    reach staking; blending again would apply the market twice."""

    PREDICTIONS = [
        {'horse_id': 1, 'win_probability': 0.40, 'odds': 3.0},
        {'horse_id': 2, 'win_probability': 0.30, 'odds': 4.0},
        {'horse_id': 3, 'win_probability': 0.20, 'odds': 7.0},
    ]

    def test_the_default_applies_no_blend(self):
        from model_classes import solve_joint_kelly

        assert ml_predict.compute_kelly_stakes_for_race(self.PREDICTIONS) == solve_joint_kelly(
            [(p['horse_id'], p['win_probability'], p['odds']) for p in self.PREDICTIONS],
            ml_predict.KELLY_FRACTION_MULTIPLIER,
            ml_predict.KELLY_MAX_TOTAL_STAKE_PCT,
        )

    def test_an_explicit_alpha_still_blends(self):
        default = ml_predict.compute_kelly_stakes_for_race(self.PREDICTIONS)
        explicit = ml_predict.compute_kelly_stakes_for_race(self.PREDICTIONS, market_alpha=0.3)
        assert explicit != default
