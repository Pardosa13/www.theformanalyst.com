"""The race-grouped ranking candidate: grouping, softmax, and fold safety.

Every other candidate in the pipeline is pointwise — one label per horse,
scored without reference to the field. This one is trained on the order within
a race, which needs a grouping on the way in and a conversion back to
probabilities on the way out. Both are what these tests are about.
"""
import os

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

np = pytest.importorskip("numpy")
pd = pytest.importorskip("pandas")
pytest.importorskip("xgboost")

import backtest
from model_classes import (
    ConsensusRegressor,
    RaceGroupedRanker,
    race_group_order,
    race_softmax,
    set_race_context,
)


FIELD = 8


def synthetic_races(n_races=250, seed=0):
    """Races where the runner with the highest 'speed' usually wins."""
    rng = np.random.default_rng(seed)
    rows = n_races * FIELD
    speed = rng.normal(size=rows)
    noise = rng.normal(size=rows)
    race_ids, won = [], np.zeros(rows, dtype=int)
    for r in range(n_races):
        block = slice(r * FIELD, (r + 1) * FIELD)
        won[block][int(np.argmax(speed[block] + 0.5 * rng.normal(size=FIELD)))] = 1
        race_ids.extend([f"race{r}"] * FIELD)
    X = pd.DataFrame({'speed': speed, 'noise': noise})
    return X, pd.Series(won), race_ids


class TestRaceGroupOrder:
    def test_contiguous_races_are_left_alone(self):
        order, groups = race_group_order(['a', 'a', 'b', 'b', 'b'])
        assert list(order) == [0, 1, 2, 3, 4]
        assert list(groups) == [2, 3]

    def test_interleaved_races_are_gathered(self):
        order, groups = race_group_order(['a', 'b', 'a', 'b', 'c', 'a', 'c'])
        assert list(order) == [0, 2, 5, 1, 3, 4, 6]
        assert list(groups) == [3, 2, 2]

    def test_races_keep_first_appearance_order(self):
        """Sorting by race id instead would reshuffle the time axis, and every
        fold boundary in this pipeline is a row position on a chronological
        ordering."""
        order, groups = race_group_order(['z', 'z', 'a', 'a'])
        assert list(order) == [0, 1, 2, 3]
        assert list(groups) == [2, 2]

    def test_group_sizes_account_for_every_row(self):
        race_ids = [f"race{i // 3}" for i in range(30)]
        order, groups = race_group_order(race_ids)
        assert int(groups.sum()) == 30
        assert sorted(order.tolist()) == list(range(30))

    def test_a_single_row_race_is_still_a_group(self):
        _order, groups = race_group_order(['a', 'b', 'b'])
        assert list(groups) == [1, 2]

    def test_empty_input(self):
        order, groups = race_group_order([])
        assert len(order) == 0 and len(groups) == 0


class TestRaceSoftmax:
    def test_each_race_sums_to_one(self):
        scores = np.array([1.0, 2.0, 3.0, 0.5, 0.25])
        race_ids = ['a', 'a', 'a', 'b', 'b']
        probabilities = race_softmax(scores, race_ids)
        assert probabilities[:3].sum() == pytest.approx(1.0)
        assert probabilities[3:].sum() == pytest.approx(1.0)

    def test_order_is_preserved_within_a_race(self):
        probabilities = race_softmax(np.array([3.0, 1.0, 2.0]), ['a'] * 3)
        assert probabilities[0] > probabilities[2] > probabilities[1]

    def test_races_are_independent(self):
        """A race full of huge scores must not drain probability from another."""
        probabilities = race_softmax(np.array([100.0, 99.0, 1.0, 0.0]), ['a', 'a', 'b', 'b'])
        assert probabilities[2:].sum() == pytest.approx(1.0)

    def test_extreme_scores_do_not_overflow(self):
        probabilities = race_softmax(np.array([900.0, 899.0, 898.0]), ['a'] * 3)
        assert np.all(np.isfinite(probabilities))
        assert probabilities.sum() == pytest.approx(1.0)

    def test_very_negative_scores_do_not_underflow_to_zero(self):
        probabilities = race_softmax(np.array([-900.0, -901.0, -902.0]), ['a'] * 3)
        assert probabilities.sum() == pytest.approx(1.0)
        assert probabilities[0] > probabilities[2]

    def test_a_race_of_nothing_but_nan_splits_evenly(self):
        probabilities = race_softmax(np.array([np.nan, np.nan]), ['a', 'a'])
        assert list(probabilities) == pytest.approx([0.5, 0.5])

    def test_interleaved_rows_are_grouped_by_race_not_position(self):
        probabilities = race_softmax(np.array([1.0, 5.0, 2.0, 6.0]), ['a', 'b', 'a', 'b'])
        assert probabilities[0] + probabilities[2] == pytest.approx(1.0)
        assert probabilities[1] + probabilities[3] == pytest.approx(1.0)


class TestRaceGroupedRanker:
    def test_it_learns_the_within_race_order(self):
        X, y, race_ids = synthetic_races()
        split = 1600
        model = RaceGroupedRanker(n_estimators=80, max_depth=3)
        model.fit(X.iloc[:split], y.iloc[:split], race_ids=race_ids[:split])

        probabilities = model.predict_win_probabilities(X.iloc[split:], race_ids[split:])
        held_out = y.iloc[split:].to_numpy()
        hits = sum(
            held_out[i * FIELD:(i + 1) * FIELD][
                int(np.argmax(probabilities[i * FIELD:(i + 1) * FIELD]))
            ]
            for i in range(len(held_out) // FIELD)
        )
        strike_rate = hits / (len(held_out) // FIELD)
        # Picking at random from an eight-runner field hits 12.5%.
        assert strike_rate > 0.35

    def test_output_is_a_probability_per_horse_summing_to_one_per_race(self):
        X, y, race_ids = synthetic_races(n_races=60)
        model = RaceGroupedRanker(n_estimators=40, max_depth=3).fit(X, y, race_ids=race_ids)
        probabilities = model.predict_win_probabilities(X, race_ids)
        for r in range(60):
            assert probabilities[r * FIELD:(r + 1) * FIELD].sum() == pytest.approx(1.0)

    def test_predict_proba_presents_a_classifier_surface(self):
        X, y, race_ids = synthetic_races(n_races=60)
        model = RaceGroupedRanker(n_estimators=40, max_depth=3).fit(X, y, race_ids=race_ids)
        set_race_context(model, race_ids)
        proba = model.predict_proba(X)
        assert proba.shape == (len(X), 2)
        assert np.allclose(proba.sum(axis=1), 1.0)
        # And _predict_win_scores reads it exactly like any other candidate's.
        scores = backtest._predict_win_scores(model, X, race_ids=race_ids)
        assert scores.shape == (len(X),)

    def test_fitting_without_a_grouping_is_refused(self):
        """A ranker fitted with every row in one giant "race" has learned
        nothing about racing; producing that silently would be worse than
        refusing."""
        X, y, _race_ids = synthetic_races(n_races=20)
        with pytest.raises(ValueError, match="race each row belongs to"):
            RaceGroupedRanker().fit(X, y)

    def test_the_race_context_is_an_alternative_to_the_argument(self):
        X, y, race_ids = synthetic_races(n_races=40)
        model = RaceGroupedRanker(n_estimators=20, max_depth=3)
        set_race_context(model, race_ids)
        model.fit(X, y)
        assert model.n_races_ == 40

    def test_the_context_does_not_survive_the_call_that_used_it(self):
        """A stale context could give a later call on a different X of equal
        length the wrong grouping, and would pickle the validation set's race
        ids into the artifact."""
        X, y, race_ids = synthetic_races(n_races=40)
        model = RaceGroupedRanker(n_estimators=20, max_depth=3)
        set_race_context(model, race_ids)
        model.fit(X, y)
        assert getattr(model, '_race_context', None) is None
        set_race_context(model, race_ids)
        model.predict_win_probabilities(X)
        assert getattr(model, '_race_context', None) is None

    def test_no_context_treats_the_batch_as_one_race(self):
        """Which is exactly right for ml_predict, which scores a race at a time."""
        X, y, race_ids = synthetic_races(n_races=40)
        model = RaceGroupedRanker(n_estimators=20, max_depth=3).fit(X, y, race_ids=race_ids)
        one_race = model.predict_win_probabilities(X.iloc[:FIELD])
        assert one_race.sum() == pytest.approx(1.0)

    def test_it_survives_being_cloned(self):
        from sklearn.base import clone

        model = RaceGroupedRanker(n_estimators=33, max_depth=5)
        copy = clone(model)
        assert copy.n_estimators == 33 and copy.max_depth == 5

    def test_it_records_the_feature_contract(self):
        X, y, race_ids = synthetic_races(n_races=40)
        model = RaceGroupedRanker(n_estimators=20, max_depth=3).fit(X, y, race_ids=race_ids)
        assert list(model.feature_names_in_) == ['speed', 'noise']

    def test_it_round_trips_through_joblib(self):
        import io

        import joblib

        X, y, race_ids = synthetic_races(n_races=40)
        model = RaceGroupedRanker(n_estimators=20, max_depth=3).fit(X, y, race_ids=race_ids)
        expected = model.predict_win_probabilities(X, race_ids)
        buffer = io.BytesIO()
        joblib.dump(model, buffer)
        buffer.seek(0)
        restored = joblib.load(buffer)
        assert restored.predict_win_probabilities(X, race_ids) == pytest.approx(expected)


class TestRankerInTheEnsemble:
    def test_a_ranker_member_is_grouped_like_it_would_be_standalone(self):
        """ConsensusRegressor clones its members, and clone() keeps only
        __init__ params — so a context set before fit would be dropped exactly
        when the ranker needs it."""
        from sklearn.ensemble import RandomForestClassifier

        X, y, race_ids = synthetic_races(n_races=80)
        ensemble = ConsensusRegressor([
            ('rf', RandomForestClassifier(n_estimators=15, random_state=0)),
            ('ranker', RaceGroupedRanker(n_estimators=30, max_depth=3)),
        ])
        set_race_context(ensemble, race_ids)
        ensemble.fit(X, y)
        predictions = backtest._predict_win_scores(ensemble, X, race_ids=race_ids)
        assert np.all(np.isfinite(predictions))
        assert len(predictions) == len(X)

    def test_an_ensemble_without_a_ranker_is_unaffected(self):
        from sklearn.ensemble import RandomForestClassifier

        X, y, race_ids = synthetic_races(n_races=60)
        ensemble = ConsensusRegressor([
            ('rf1', RandomForestClassifier(n_estimators=10, random_state=0)),
            ('rf2', RandomForestClassifier(n_estimators=10, random_state=1)),
        ])
        with_context = ConsensusRegressor([
            ('rf1', RandomForestClassifier(n_estimators=10, random_state=0)),
            ('rf2', RandomForestClassifier(n_estimators=10, random_state=1)),
        ])
        ensemble.fit(X, y)
        set_race_context(with_context, race_ids)
        with_context.fit(X, y)
        assert with_context.predict(X) == pytest.approx(ensemble.predict(X))


class TestRankerAsALiveChampion:
    """Live scoring hands the model one race at a time, so a ranker champion
    must score correctly with no grouping told to it."""

    def test_live_scoring_reads_it_like_any_other_champion(self):
        import ml_predict

        X, y, race_ids = synthetic_races(n_races=60)
        model = RaceGroupedRanker(n_estimators=30, max_depth=3).fit(X, y, race_ids=race_ids)

        one_race = X.iloc[:FIELD]
        scores, method = ml_predict._predict_raw_scores(model, one_race)
        assert method == 'predict_proba'
        assert len(scores) == FIELD
        # Scoring one race at a time is exactly when "treat X as one race" is
        # right, so the probabilities are a book.
        assert scores.sum() == pytest.approx(1.0)

    def test_a_ranker_champion_has_no_blend_weight_until_one_is_validated(self):
        import ml_predict

        X, y, race_ids = synthetic_races(n_races=40)
        model = RaceGroupedRanker(n_estimators=20, max_depth=3).fit(X, y, race_ids=race_ids)
        assert ml_predict.model_market_alpha(model) == 1.0


class TestFoldSafety:
    def test_grouping_is_computed_inside_each_fold(self):
        """A global grouping would put runners of a race that straddles a fold
        boundary into one group spanning both sides of it."""
        seen = []

        class RecordingRanker(RaceGroupedRanker):
            def fit(self, X, y, race_ids=None):
                resolved = self._race_ids_for(len(X), race_ids)
                seen.append(list(resolved) if resolved else None)
                self.model_ = None
                self.feature_names_in_ = np.asarray(list(X.columns))
                self.set_race_context(None)
                return self

            def raw_scores(self, X):
                return np.linspace(0.0, 1.0, len(X))

        X, y, race_ids = synthetic_races(n_races=200)
        sp = np.tile([2.2, 3.6, 5.5, 9.0, 14.0, 21.0, 34.0, 51.0], 200)
        backtest._walk_forward_metrics_for_model(RecordingRanker(), X, y, sp, race_ids)

        assert seen, "no fold ever fitted"
        # Every fold saw a strict prefix of the data, never the whole set, and
        # the prefixes grow — that is the expanding window.
        lengths = [len(rows) for rows in seen]
        assert lengths == sorted(lengths)
        assert max(lengths) < len(race_ids)
        for rows in seen:
            assert rows == race_ids[:len(rows)]

    def test_folds_produce_usable_metrics_for_a_real_ranker(self):
        X, y, race_ids = synthetic_races(n_races=200)
        sp = np.tile([2.2, 3.6, 5.5, 9.0, 14.0, 21.0, 34.0, 51.0], 200)
        walk_forward = backtest._walk_forward_metrics_for_model(
            RaceGroupedRanker(n_estimators=25, max_depth=3), X, y, sp, race_ids,
        )
        assert walk_forward['n_splits'] >= 2
        assert all(fold['bets'] > 0 for fold in walk_forward['folds'])
