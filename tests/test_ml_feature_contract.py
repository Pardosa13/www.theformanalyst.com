from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from ml_predict import FEATURE_NAMES, _live_feature_contract_predicates, _log_live_feature_audit


class DummyModel:
    def __init__(self, feature_names):
        self._form_analyst_expected_features = list(feature_names)
        self._form_analyst_expected_feature_count = len(feature_names)
        self.n_features_in_ = len(feature_names)
        self._form_analyst_model_version = "older-than-code"


def test_live_feature_contract_passes_with_same_146_stored_and_final_features_in_order():
    feature_names = [f"stored_feature_{idx}" for idx in range(146)]
    model = DummyModel(feature_names)
    raw_X = pd.DataFrame([{name: float(idx) for idx, name in enumerate(feature_names)}])
    final_X = raw_X.reindex(columns=feature_names, fill_value=0)

    contract = _live_feature_contract_predicates(model, raw_X, final_X)

    assert contract["stored_count_matches"] is True
    assert contract["final_count_matches"] is True
    assert contract["names_match"] is True
    assert contract["order_matches"] is True
    assert contract["missing_features_empty"] is True
    assert contract["extra_features_empty"] is True
    assert contract["duplicate_features_empty"] is True
    assert contract["model_n_features_matches"] is True
    assert contract["expected_feature_count_matches"] is True
    assert contract["feature_hash_matches"] is True
    assert contract["genuine_contract_matches"] is True
    assert contract["legacy_stored_matches_code"] is False

    _log_live_feature_audit(
        model,
        meeting_id=1667,
        race=SimpleNamespace(race_number=1),
        feature_rows=[{name: float(idx) for idx, name in enumerate(feature_names)}],
        raw_X=raw_X,
        final_X=final_X,
    )


def test_live_contract_tolerates_generated_superset_for_older_146_feature_artifact():
    """The live generator now produces the full 204-feature set; a champion
    promoted on the older 146-feature contract must keep scoring — the extra
    generated columns are dropped by the reindex to the stored contract and
    must be reported, not treated as a contract failure."""
    stored = [f"stored_feature_{idx}" for idx in range(146)]
    generated = stored + [f"new_audit_feature_{idx}" for idx in range(58)]
    model = DummyModel(stored)
    raw_X = pd.DataFrame([{name: float(idx) for idx, name in enumerate(generated)}])
    final_X = raw_X.reindex(columns=stored)

    contract = _live_feature_contract_predicates(model, raw_X, final_X)

    assert contract["extra_features_empty"] is False
    assert sorted(contract["extra_features"]) == sorted(generated[146:])
    assert contract["missing_features_empty"] is True
    assert contract["names_match"] is True
    assert contract["order_matches"] is True
    assert contract["genuine_contract_matches"] is True

    _log_live_feature_audit(
        model,
        meeting_id=1667,
        race=SimpleNamespace(race_number=1),
        feature_rows=[{name: float(idx) for idx, name in enumerate(generated)}],
        raw_X=raw_X,
        final_X=final_X,
    )


def test_live_contract_still_fails_when_stored_features_are_not_generated():
    stored = [f"stored_feature_{idx}" for idx in range(10)]
    generated = stored[:-1]  # one stored feature missing from live generation
    model = DummyModel(stored)
    raw_X = pd.DataFrame([{name: float(idx) for idx, name in enumerate(generated)}])
    final_X = raw_X.reindex(columns=stored)

    contract = _live_feature_contract_predicates(model, raw_X, final_X)

    assert contract["missing_features"] == [stored[-1]]
    assert contract["names_match"] is False
    assert contract["genuine_contract_matches"] is False

    with pytest.raises(RuntimeError):
        _log_live_feature_audit(
            model,
            meeting_id=1667,
            race=SimpleNamespace(race_number=1),
            feature_rows=[{name: float(idx) for idx, name in enumerate(generated)}],
            raw_X=raw_X,
            final_X=final_X,
        )


def test_live_feature_contract_passes_with_full_feature_contract():
    """A model trained on the full audit feature set must pass the live
    contract audit — the 146-feature cap is lifted."""
    assert len(FEATURE_NAMES) == 207
    model = DummyModel(FEATURE_NAMES)
    raw_X = pd.DataFrame([{name: float(idx) for idx, name in enumerate(FEATURE_NAMES)}])
    final_X = raw_X.reindex(columns=FEATURE_NAMES)

    contract = _live_feature_contract_predicates(model, raw_X, final_X)

    assert contract["genuine_contract_matches"] is True
    assert contract["missing_features_empty"] is True
    assert contract["extra_features_empty"] is True
    assert contract["order_matches"] is True


class TestMetadataVersionPredicate:
    """The artifact-vs-serving-code version stamp.

    This predicate spent its whole life comparing a real artifact version
    against `globals().get('MODEL_VERSION')` — a name never defined in
    ml_predict — so it read None and could only ever report False. It never
    detected drift; it just always claimed drift.
    """

    FEATURES = [f"stored_feature_{idx}" for idx in range(5)]

    def _contract(self, monkeypatch, serving_version, artifact_version):
        import importlib

        import ml_predict as module

        if serving_version is None:
            monkeypatch.delenv("ML_MODEL_VERSION", raising=False)
        else:
            monkeypatch.setenv("ML_MODEL_VERSION", serving_version)
        module = importlib.reload(module)

        model = DummyModel(self.FEATURES)
        model._form_analyst_model_version = artifact_version
        raw_X = pd.DataFrame([{name: float(i) for i, name in enumerate(self.FEATURES)}])
        return module._live_feature_contract_predicates(
            model, raw_X, raw_X.reindex(columns=self.FEATURES)
        )

    def test_unconfigured_serving_version_reports_none_not_a_mismatch(self, monkeypatch):
        contract = self._contract(monkeypatch, None, "20260815")
        assert contract["metadata_version_matches"] is None

    def test_matching_versions_can_actually_pass(self, monkeypatch):
        contract = self._contract(monkeypatch, "20260815", "20260815")
        assert contract["metadata_version_matches"] is True

    def test_genuinely_different_versions_still_report_false(self, monkeypatch):
        contract = self._contract(monkeypatch, "20260901", "20260815")
        assert contract["metadata_version_matches"] is False

    def test_version_stamp_never_blocks_scoring(self, monkeypatch):
        """It is diagnostic only — it is not part of genuine_contract_matches."""
        for serving, artifact in ((None, "20260815"), ("20260901", "20260815")):
            contract = self._contract(monkeypatch, serving, artifact)
            assert contract["genuine_contract_matches"] is True

    def test_unconfigured_version_is_not_listed_among_failed_predicates(self, monkeypatch):
        """A None must not be swept up by the `not contract[name]` filter and
        blamed for a failure it had no part in."""
        import importlib

        import ml_predict as module

        monkeypatch.delenv("ML_MODEL_VERSION", raising=False)
        module = importlib.reload(module)

        model = DummyModel(self.FEATURES)
        model._form_analyst_model_version = "20260815"
        raw_X = pd.DataFrame([{name: float(i) for i, name in enumerate(self.FEATURES)}])
        # Drop a stored feature so the contract genuinely fails and the audit
        # enumerates failed_predicates.
        final_X = raw_X.reindex(columns=self.FEATURES[:-1])

        with pytest.raises(RuntimeError) as excinfo:
            module._log_live_feature_audit(
                model,
                meeting_id=1962,
                race=SimpleNamespace(race_number=8),
                feature_rows=[{name: float(i) for i, name in enumerate(self.FEATURES)}],
                raw_X=raw_X,
                final_X=final_X,
            )
        assert "metadata_version_matches" not in str(excinfo.value)
