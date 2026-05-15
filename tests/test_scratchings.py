from scratchings import compute_is_scratched_final, is_explicit_scratched_value


def test_explicit_scratched_values_only():
    for value in [True, "SCR", "SCRATCHED", "Scratched", "Late Scratching"]:
        assert is_explicit_scratched_value(value)


def test_common_active_values_are_not_truthy_scratches():
    for value in [False, None, "N", "No", "false", "", "Active", "Runner", "Final", "Resulted"]:
        assert not is_explicit_scratched_value(value)


def test_canonical_runner_flag_ignores_non_empty_active_strings():
    assert compute_is_scratched_final({"is_scratched": "N", "status": "Final"}) is False
    assert compute_is_scratched_final({"runner_status": "Active"}) is False
    assert compute_is_scratched_final({"runner_status": "SCR"}) is True
