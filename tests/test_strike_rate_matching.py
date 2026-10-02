import logging

from strike_rate_matching import (
    build_strike_rate_lookup, get_sr_win_pct, lookup_strike_rate, log_match_stats, normalize_name,
)


def test_normalize_strips_titles_punctuation_and_collapses_spaces():
    assert normalize_name(" Ms  K. Lenton ") == "k lenton"
    assert normalize_name("O’Shea") == "o shea"


def test_initials_and_unique_surname_matching():
    lookup = build_strike_rate_lookup([
        ("Chris Waller", 20, 100),
        ("James McDonald", 18, 100),
        ("Craig Williams", 15, 100),
        ("Jenny Duggan", 12, 100),
        ("Dean Yendall", 11, 100),
        ("Michael Kropp", 10, 100),
    ])

    assert lookup_strike_rate("C J Waller", lookup)[0]["name"] == "Chris Waller"
    assert lookup_strike_rate("James McDonald", lookup)[1] == "exact"
    assert lookup_strike_rate("Craig Williams", lookup)[1] == "exact"
    assert lookup_strike_rate("Jenny Duggan", lookup)[0]["name"] == "Jenny Duggan"
    assert lookup_strike_rate("Dean Yendall", lookup)[0]["name"] == "Dean Yendall"
    assert lookup_strike_rate("M A Kropp", lookup)[0]["name"] == "Michael Kropp"
    assert get_sr_win_pct("M A Kropp", lookup) == 10.0


def test_ambiguous_surname_only_does_not_match():
    lookup = build_strike_rate_lookup([
        ("John Smith", 20, 100),
        ("Jane Smith", 10, 100),
    ])

    assert lookup_strike_rate("Smith", lookup) == (None, "unmatched")


def test_apprentice_claim_suffix_strips_to_exact_match():
    assert normalize_name("Jamie Kah (a3)") == "jamie kah"
    lookup = build_strike_rate_lookup([("Jamie Kah", 20, 100)])
    assert lookup_strike_rate("Jamie Kah (a3)", lookup)[1] == "exact"


def test_diacritics_fold_to_ascii_for_exact_match():
    assert normalize_name("José García") == "jose garcia"
    lookup = build_strike_rate_lookup([("Jose Garcia", 12, 100)])
    assert lookup_strike_rate("José García", lookup)[1] == "exact"


def test_fuzzy_tier_matches_close_typo_above_threshold():
    lookup = build_strike_rate_lookup([
        ("Damien Oliver", 18, 100),
        ("Craig Williams", 15, 100),
    ])
    data, method = lookup_strike_rate("Damien J Olliver", lookup)
    assert method == "fuzzy"
    assert data["name"] == "Damien Oliver"


def test_fuzzy_tier_does_not_match_unrelated_name():
    lookup = build_strike_rate_lookup([("Damien Oliver", 18, 100)])
    assert lookup_strike_rate("Zoe Nobody", lookup) == (None, "unmatched")


def test_fuzzy_tier_does_not_merge_different_surname_sharing_given_name():
    # Regression: "Daniel Bowman" vs "Daniel Bowen" scored 0.9526 as a single
    # blended string under the old whole-name Jaro-Winkler comparison and was
    # wrongly force-matched. These are different people who happen to share a
    # first name; the surname itself isn't a close enough match to trust.
    lookup = build_strike_rate_lookup([
        ("Daniel Bowen", 15, 100),
        ("Craig Williams", 15, 100),
    ])
    assert lookup_strike_rate("Daniel Bowman", lookup) == (None, "unmatched")


def test_fuzzy_tier_does_not_merge_different_initials_sharing_surname():
    # Regression: "S J Richards" vs "P S Richards" scored 0.9141 as a single
    # blended string and was wrongly force-matched — a real pattern in racing
    # (father/son or sibling trainers/jockeys sharing a surname). A shared
    # surname must not be enough on its own when the first initial differs.
    lookup = build_strike_rate_lookup([
        ("S J Richards", 12, 100),
        ("P S Richards", 30, 100),
    ])
    assert lookup_strike_rate("A J Richards", lookup) == (None, "unmatched")


def test_fuzzy_tier_does_not_merge_different_given_names_sharing_initial_and_close_surname():
    # Regression (run 142): initials-compatible + a close surname score was
    # not enough to prevent merging unrelated people whose full given names
    # are quite different but happen to share a first letter. All of these
    # surname pairs score in the 0.95-0.9667 range under Jaro-Winkler —
    # overlapping with (and in some cases exceeding) genuine typos like
    # "Olliver"/"Oliver" (0.9619) — so the given name itself must also line
    # up before the surname fuzz is trusted.
    lookup = build_strike_rate_lookup([
        ("Matthew Kelley", 15, 100),
        ("John Morrison", 12, 100),
        ("Eloise Drews", 10, 100),
    ])
    assert lookup_strike_rate("Melissa Kelly", lookup) == (None, "unmatched")
    assert lookup_strike_rate("Jackson Morris", lookup) == (None, "unmatched")
    assert lookup_strike_rate("Ella Drew", lookup) == (None, "unmatched")


def test_fuzzy_tier_applies_stricter_bar_when_only_initials_available():
    # Regression (run 142): "Ms A Thomson" has no full given name to
    # corroborate a match (just the initial "A"), so the surname score alone
    # ("Thomson" vs "Thompson" = 0.975) must clear a stricter bar than the
    # full-given-name-match case, not just FUZZY_MATCH_THRESHOLD.
    lookup = build_strike_rate_lookup([("Adin Thompson", 20, 100)])
    assert lookup_strike_rate("Ms A Thomson", lookup) == (None, "unmatched")


def test_fuzzy_tier_never_matches_partnership_name_to_an_individual():
    # Regression (run 142): "A & S Freedman" is a training partnership (two
    # people). Punctuation-stripping normalisation collapses the "&" away,
    # so without an explicit guard this looks like a single person with two
    # initials and fuzzy-matches "Anthony Freeman" at 0.975 similarity — this
    # wrongly attached one individual's stats to a partnership's horses 288
    # times in a single run. Partnership names must always fall through to
    # "unmatched" rather than being force-matched to either partner.
    lookup = build_strike_rate_lookup([("Anthony Freeman", 8, 50)])
    assert lookup_strike_rate("A & S Freedman", lookup) == (None, "unmatched")
    assert lookup_strike_rate("J and K Smith", lookup) == (None, "unmatched")


def test_log_match_stats_dedupes_repeated_fuzzy_pairs(caplog):
    # Regression: the same (query, matched_name) fuzzy pair was being logged
    # once per horse-row (50+ times at an identical timestamp) instead of once
    # per unique pair with an occurrence count.
    jockey_lookup = build_strike_rate_lookup([("Damien Oliver", 18, 100)])
    for _ in range(52):
        lookup_strike_rate("Damien J Olliver", jockey_lookup)
    trainer_lookup = build_strike_rate_lookup([])

    with caplog.at_level(logging.INFO):
        log_match_stats(logging.getLogger("test"), jockey_lookup, trainer_lookup)

    fuzzy_lines = [r.getMessage() for r in caplog.records if "Fuzzy jockey match" in r.message]
    assert len(fuzzy_lines) == 1
    assert "matched 52 time(s) this run" in fuzzy_lines[0]


def test_puntingform_entity_type_mapping_labels():
    import pytest
    pytest.importorskip("requests")
    pytest.importorskip("sqlalchemy")
    from puntingform_service import PuntingFormService

    service = PuntingFormService.__new__(PuntingFormService)
    service.api_key = "test-key"
    jockey_request = service._fetch_v2_strike_rate_rows.__self__ if False else None
    # Assert via prepared request log-independent implementation details by monkeypatching requests.
    captured = []

    class FakeResponse:
        ok = True
        status_code = 200
        text = "StartDate,EntityId,EntityName\n"
        headers = {"content-type": "text/csv"}

    class FakeSession:
        def send(self, request, timeout):
            captured.append(request.url)
            return FakeResponse()

    import puntingform_service
    original = puntingform_service.requests.Session
    try:
        puntingform_service.requests.Session = lambda: FakeSession()
        service._fetch_v2_strike_rate_rows("jockey", jurisdiction=2)
        service._fetch_v2_strike_rate_rows("trainer", jurisdiction=2)
    finally:
        puntingform_service.requests.Session = original

    assert "entityType=1" in captured[0]
    assert "entityType=2" in captured[1]
