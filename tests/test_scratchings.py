from scratchings import compute_is_scratched_final, is_explicit_scratched_value, resolve_official_scratched_set


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


def test_v1_snapshot_takes_precedence_over_conflicting_v2_updates():
    final_set, source, conflicts_ignored = resolve_official_scratched_set(
        {(1, 3), (2, 7)},
        {(1, 3), (3, 12)},
        v1_available=True,
        v2_available=True,
    )

    assert final_set == {(1, 3), (2, 7)}
    assert source == "v1_scratchings"
    assert conflicts_ignored == 1


def test_v2_updates_used_only_when_v1_unavailable():
    final_set, source, conflicts_ignored = resolve_official_scratched_set(
        set(),
        {(3, 12)},
        v1_available=False,
        v2_available=True,
    )

    assert final_set == {(3, 12)}
    assert source == "v2_updates_scratchings"
    assert conflicts_ignored == 0
