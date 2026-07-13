from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from ml_predict import _live_feature_contract_predicates, _log_live_feature_audit


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
