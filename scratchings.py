"""Strict scratching normalisation helpers.

PuntingForm and bookmaker payloads can include several status-like fields whose
values are strings such as "N", "Final", or "Active".  Those values must never
be treated with normal truthiness when deciding if a runner is scratched.
"""

from __future__ import annotations

from typing import Any, Mapping


EXPLICIT_SCRATCHED_STRINGS = {
    "scr",
    "scratched",
    "late scratching",
}

EXPLICIT_NOT_SCRATCHED_STRINGS = {
    "",
    "n",
    "no",
    "false",
    "active",
    "runner",
    "final",
    "resulted",
}

# Fields that commonly carry runner-level scratch/status information.  The
# order intentionally prioritises dedicated scratch fields over generic status.
SCRATCH_STATUS_FIELD_NAMES = (
    "is_scratched_final",
    "is_scratched",
    "scratched",
    "scratch_status",
    "scratching_status",
    "runner_status",
    "runnerStatus",
    "status",
    "Status",
)


def _normalise_status_text(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())


def is_explicit_scratched_value(value: Any) -> bool:
    """Return True only for explicit scratched values.

    This deliberately rejects truthy-but-ambiguous strings such as "N", "No",
    "false", "Active", "Runner", "Final", and "Resulted".
    """
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, str):
        return _normalise_status_text(value) in EXPLICIT_SCRATCHED_STRINGS
    return False


def is_explicit_active_value(value: Any) -> bool:
    """Return True when a status-like value explicitly means not scratched."""
    if value is False or value is None:
        return True
    if value is True:
        return False
    if isinstance(value, str):
        return _normalise_status_text(value) in EXPLICIT_NOT_SCRATCHED_STRINGS
    return False


def compute_is_scratched_final(raw_runner: Mapping[str, Any] | None) -> bool:
    """Compute the canonical runner scratch flag from raw runner fields."""
    if not raw_runner:
        return False

    for field_name in SCRATCH_STATUS_FIELD_NAMES:
        if field_name in raw_runner and is_explicit_scratched_value(raw_runner.get(field_name)):
            return True
    return False


def extract_debug_scratch_fields(raw_runner: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return all status/scratch-like fields for import/debug output."""
    if not raw_runner:
        return {}
    debug_fields: dict[str, Any] = {}
    for key, value in raw_runner.items():
        key_lower = str(key).lower()
        if "scratch" in key_lower or "status" in key_lower:
            debug_fields[str(key)] = value
    return debug_fields
