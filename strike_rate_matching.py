import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime

_TITLES = {"mr", "mrs", "ms", "miss"}
# Apprentice claim suffix, e.g. "J Smith (a3)" or "J Smith a1.5" — matched
# before punctuation stripping (parenthesised form) and again as a trailing
# bare token (unparenthesised form some feeds use), otherwise it survives
# normalisation as a fake extra "surname" token and breaks every tier.
_APPRENTICE_CLAIM_PAREN_RE = re.compile(r"\(\s*a\d+(?:\.\d+)?\s*\)", re.IGNORECASE)
_APPRENTICE_CLAIM_TOKEN_RE = re.compile(r"^a\d+(?:\.\d+)?$", re.IGNORECASE)


def _coerce_date(value):
    """Normalise a date/datetime/pandas Timestamp/string into a plain date, or None."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if hasattr(value, "date") and callable(getattr(value, "date")):
        try:
            return value.date()
        except Exception:
            pass
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
            try:
                return datetime.strptime(value.split(" ")[0], fmt).date()
            except ValueError:
                continue
    return None


def normalize_name(name):
    """Normalise jockey/trainer names for strike-rate lookup."""
    if not name:
        return ""
    value = str(name).lower().strip()
    value = re.sub(r"[’‘`´]", "'", value)
    value = _APPRENTICE_CLAIM_PAREN_RE.sub(" ", value)
    # Fold diacritics to their base ASCII letter (e.g. "é" -> "e") instead of
    # letting the character-class strip below drop them entirely (e.g.
    # "José" -> "jos", silently losing the final letter and any chance of an
    # exact match against an un-accented "jose" on the other side).
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(c for c in value if not unicodedata.combining(c))
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    parts = [p for p in re.sub(r"\s+", " ", value).strip().split(" ") if p]
    while parts and parts[0] in _TITLES:
        parts.pop(0)
    if len(parts) > 1 and _APPRENTICE_CLAIM_TOKEN_RE.match(parts[-1]):
        parts.pop()
    return " ".join(parts)


def name_key_parts(name):
    normalised = normalize_name(name)
    parts = normalised.split()
    if not parts:
        return {"exact": "", "all_initials": "", "first_initial": "", "surname": ""}
    surname = parts[-1]
    given = parts[:-1]
    initials = "".join(p[0] for p in given if p)
    return {
        "exact": normalised,
        "all_initials": f"{initials} {surname}".strip() if initials else normalised,
        "first_initial": f"{initials[:1]} {surname}".strip() if initials else normalised,
        "surname": surname,
    }


# ── Tier 4: fuzzy matching ──────────────────────────────────────────────────
# Exact/initials/surname-unique tiers above miss real-world name variants such
# as apprentice claim suffixes ("J Smith (a3)"), middle initials, hyphenated or
# abbreviated first names, and diacritics that survive normalisation
# differently on each side (internal DB vs PuntingForm's EntityName field).
# This tier is a last-resort fallback, gated by a high similarity threshold so
# it only closes the gap on near-misses, not lookalikes.
#
# Scoring the whole "given-name surname" string as one blob was found to merge
# DIFFERENT people who happen to share one component: e.g. "Daniel Bowman" vs
# "Daniel Bowen" (surname differs, given name identical) scored 0.9526 as a
# single string, and "S J Richards" vs "P S Richards" (surname identical,
# initials differ) scored 0.9141 — both above the old 0.90 bar, both wrong.
# Racing has real father/son and sibling combinations sharing a surname, so a
# surname match alone can't be trusted either, and a shared surname with a
# genuinely different first initial (S vs P) is exactly the case that must
# NOT be force-matched.
#
# Surname and initials are now scored on separate axes instead of one blended
# string: initials must be "compatible" (identical, or one is a prefix of the
# other — e.g. "d" vs "dj" covers a middle initial present on only one side,
# which both still refer to the same first name) and, only once that gate
# passes, the surname is fuzzy-scored against a threshold. There is
# deliberately no reverse path (exact surname + fuzzy initials): fuzzing the
# initials themselves is indistinguishable from the father/son-sharing-a-
# surname case above, so a mismatched initial always falls through to
# "unmatched" rather than being force-matched.
#
# Initials-compatibility alone is still too weak: it only looks at first
# letters, so "Jackson" and "John" are "compatible" (both start with "j")
# even though they are unrelated names, and the same is true of "Melissa"/
# "Matthew" and "Ella"/"Eloise". Two full given names being merely
# initial-compatible was letting the surname score alone decide the match —
# and surname typos/variants ("Kelly"/"Kelley", "Morris"/"Morrison",
# "Drew"/"Drews", "Thomson"/"Thompson") routinely score in the 0.95-0.975
# range, overlapping with genuine typos like "Olliver"/"Oliver" (0.9619).
# No single surname-score cutoff separates those two groups.
#
# So when BOTH sides carry a full given-name word (not just an initial), that
# given name must match exactly — only the surname gets fuzzed
# (FUZZY_MATCH_THRESHOLD). When either side has only an initial to go on
# (e.g. "Ms A Thomson", or "A & S Freedman" once its ampersand is stripped),
# there is no given name to corroborate the match, so a stricter bar applies
# (FUZZY_MATCH_THRESHOLD_INITIALS_ONLY) since the surname score is carrying
# the entire decision alone.
FUZZY_MATCH_THRESHOLD = 0.93
FUZZY_MATCH_THRESHOLD_INITIALS_ONLY = 0.98

# Multi-person partnership/team entries (e.g. "A & S Freedman", "J and K
# Smith" — a real pattern for training partnerships) have their "&"/"and"
# conjunction stripped as punctuation during normalisation, which collapses
# them into what looks like one person with two initials. Fuzzy-matching that
# combined entry to a single individual silently attaches a partnership's
# stats to one person's row — "A & S Freedman" wrongly matched "Anthony
# Freeman" 288 times in a single run this way. These must never reach the
# fuzzy tier at all; they fall through to "unmatched" instead.
_PARTNERSHIP_RE = re.compile(r"(?:^|\s)(?:&|and)(?:\s|$)", re.IGNORECASE)


def _is_partnership_name(name):
    if not name:
        return False
    return bool(_PARTNERSHIP_RE.search(str(name)))


def _split_surname_initials(exact_key):
    """exact_key is a normalised, space-joined 'given ... surname' string (see
    name_key_parts). Returns (surname, initials) where initials is the
    first-letter-of-each-token abbreviation of everything before the surname,
    e.g. "daniel bowman" -> ("bowman", "d"), "s j richards" -> ("richards", "sj")."""
    parts = exact_key.split()
    if not parts:
        return "", ""
    surname = parts[-1]
    initials = "".join(p[0] for p in parts[:-1])
    return surname, initials


def _initials_compatible(a, b):
    """True if two initials strings could plausibly belong to the same person:
    identical, or one is a non-empty prefix of the other (a middle initial
    present on only one side). A completely different first initial (e.g.
    "s" vs "p") is never compatible — that's the father/son/sibling collision
    this tier must not force-match."""
    if a == b:
        return True
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def _jaro_similarity(s1, s2):
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0
    match_distance = max(len1, len2) // 2 - 1
    match_distance = max(match_distance, 0)

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    transpositions = 0

    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1

    transpositions //= 2
    return ((matches / len1) + (matches / len2) + ((matches - transpositions) / matches)) / 3.0


def _jaro_winkler_similarity(s1, s2, prefix_weight=0.1, max_prefix=4):
    """Jaro-Winkler similarity in [0.0, 1.0]; pure Python, no extra dependency."""
    jaro = _jaro_similarity(s1, s2)
    prefix_len = 0
    for a, b in zip(s1[:max_prefix], s2[:max_prefix]):
        if a != b:
            break
        prefix_len += 1
    return jaro + (prefix_len * prefix_weight * (1.0 - jaro))


def _surname_prefix_index(exact_map):
    """Bucket exact-key candidates by the first letter of their surname — a
    cheap prefilter so a fuzzy scan only ever compares candidates whose
    surnames could plausibly be near-misses of each other, not a full scan of
    every name on the roster."""
    index = defaultdict(list)
    for exact_key in exact_map:
        surname, _ = _split_surname_initials(exact_key)
        if surname:
            index[surname[:1]].append(exact_key)
    return index


def _first_given_name(exact_key):
    """First given-name token of a normalised 'given ... surname' string, or
    "" if there isn't one (surname-only) or it's just a single-letter
    initial (e.g. the "a" in "a s freedman" / "a thomson") — a single letter
    carries no information to corroborate a match against, so callers should
    treat that the same as "no given name available"."""
    parts = exact_key.split()
    if len(parts) <= 1:
        return ""
    first = parts[0]
    return first if len(first) > 1 else ""


def _fuzzy_match_exact_key(query_exact_key, exact_map, surname_index):
    """Best fuzzy candidate for query_exact_key, restricted to its surname's
    first-letter bucket. A candidate only qualifies if its initials are
    "compatible" with the query's (see _initials_compatible — this rules out
    a genuinely different first initial sharing a surname, e.g. father/son or
    sibling trainers). If both sides also carry a full given-name word, that
    given name must match exactly and the surname is fuzzed against
    FUZZY_MATCH_THRESHOLD; otherwise (either side is initials-only) the
    surname alone must clear the stricter FUZZY_MATCH_THRESHOLD_INITIALS_ONLY.
    Returns (matched_key, score) or (None, 0.0)."""
    query_surname, query_initials = _split_surname_initials(query_exact_key)
    if not query_surname:
        return None, 0.0
    query_given = _first_given_name(query_exact_key)
    bucket = surname_index.get(query_surname[:1], [])
    best_key, best_score = None, 0.0
    for candidate in bucket:
        if candidate == query_exact_key:
            continue
        candidate_surname, candidate_initials = _split_surname_initials(candidate)
        if not _initials_compatible(query_initials, candidate_initials):
            continue
        candidate_given = _first_given_name(candidate)
        if query_given and candidate_given:
            if query_given != candidate_given:
                continue
            threshold = FUZZY_MATCH_THRESHOLD
        else:
            threshold = FUZZY_MATCH_THRESHOLD_INITIALS_ONLY
        score = _jaro_winkler_similarity(query_surname, candidate_surname)
        if score >= threshold and score > best_score:
            best_key, best_score = candidate, score
    if best_key:
        return best_key, best_score
    return None, 0.0


def build_strike_rate_lookup(rows):
    """
    Build lookup maps for rows of (name, wins, runs)-like data.
    Keys are exact normalised, all initials + surname, first initial + surname,
    and unique surname only.
    """
    entries = []
    surname_counts = Counter()
    for row in rows:
        if isinstance(row, dict):
            name = row.get("name") or row.get("Name")
            wins = row.get("L100Wins", row.get("l100_wins", 0))
            runs = row.get("L100Runs", row.get("l100_runs", 0))
        else:
            name, wins, runs = row[0], row[1], row[2]
        keys = name_key_parts(name)
        if not keys["exact"]:
            continue
        data = {
            "L100Wins": int(wins or 0),
            "L100Runs": int(runs or 0),
            "l100_wins": int(wins or 0),
            "l100_runs": int(runs or 0),
            "name": str(name or "").strip(),
        }
        entries.append((keys, data))
        surname_counts[keys["surname"]] += 1

    maps = {"exact": {}, "all_initials": {}, "first_initial": {}, "surname": {}}
    for keys, data in entries:
        maps["exact"].setdefault(keys["exact"], data)
        maps["all_initials"].setdefault(keys["all_initials"], data)
        maps["first_initial"].setdefault(keys["first_initial"], data)
        if surname_counts[keys["surname"]] == 1:
            maps["surname"].setdefault(keys["surname"], data)
    maps["_surname_prefix_index"] = _surname_prefix_index(maps["exact"])

    legacy = dict(maps["exact"])
    legacy["_lookup_meta"] = {"maps": maps}
    legacy["_match_stats"] = defaultdict(int)
    legacy["_fuzzy_audit"] = []
    return legacy


def lookup_strike_rate(name, sr_lookup):
    if not name or not sr_lookup:
        return None, "unmatched"
    meta = sr_lookup.get("_lookup_meta", {}) if isinstance(sr_lookup, dict) else {}
    maps = meta.get("maps")
    keys = name_key_parts(name)
    if maps:
        for kind in ("exact", "all_initials", "first_initial", "surname"):
            data = maps.get(kind, {}).get(keys[kind])
            if data:
                return data, ("initials" if kind in {"all_initials", "first_initial"} else "surname_unique" if kind == "surname" else "exact")
        exact_map = maps.get("exact", {})
        surname_index = maps.get("_surname_prefix_index", {})
        if keys["exact"] and exact_map and not _is_partnership_name(name):
            matched_key, score = _fuzzy_match_exact_key(keys["exact"], exact_map, surname_index)
            if matched_key:
                data = exact_map[matched_key]
                audit = sr_lookup.get("_fuzzy_audit") if isinstance(sr_lookup, dict) else None
                if audit is not None:
                    audit.append({
                        "query": name, "matched_name": data.get("name"), "similarity": round(score, 4),
                    })
                return data, "fuzzy"
    data = sr_lookup.get(keys["exact"])
    return (data, "exact") if data else (None, "unmatched")


def get_sr_win_pct(name, sr_lookup):
    data, method = lookup_strike_rate(name, sr_lookup)
    stats = sr_lookup.get("_match_stats") if isinstance(sr_lookup, dict) else None
    if stats is not None:
        stats[method] += 1
    if not data:
        return -1.0
    runs = data.get("L100Runs", 0)
    wins = data.get("L100Wins", 0)
    if runs < 10:
        return -1.0
    return (wins / runs) * 100.0


def build_strike_rate_history_lookup(rows):
    """
    Build a point-in-time lookup from dated strike-rate snapshot rows.

    Unlike build_strike_rate_lookup (one row per entity = "current" snapshot),
    each row here is (name, wins, runs, snapshot_date); an entity can have many
    dated snapshots. Returns the same multi-key matching structure as
    build_strike_rate_lookup, but each matched entry is a list of
    (snapshot_date, data) tuples sorted ascending by date, so callers can pick
    the snapshot that actually existed as of a given race date instead of
    always using the latest one.
    """
    grouped = defaultdict(list)
    surname_keys = defaultdict(set)
    for row in rows:
        if isinstance(row, dict):
            name = row.get("name") or row.get("Name")
            wins = row.get("l100_wins", row.get("L100Wins", 0))
            runs = row.get("l100_runs", row.get("L100Runs", 0))
            snapshot_date = row.get("snapshot_date")
        else:
            name, wins, runs, snapshot_date = row[0], row[1], row[2], row[3]
        keys = name_key_parts(name)
        snapshot_date = _coerce_date(snapshot_date)
        if not keys["exact"] or snapshot_date is None:
            continue
        data = {
            "L100Wins": int(wins or 0),
            "L100Runs": int(runs or 0),
            "l100_wins": int(wins or 0),
            "l100_runs": int(runs or 0),
            "name": str(name or "").strip(),
        }
        for kind in ("exact", "all_initials", "first_initial"):
            grouped[(kind, keys[kind])].append((snapshot_date, data))
        surname_keys[keys["surname"]].add(keys["exact"])

    maps = {"exact": {}, "all_initials": {}, "first_initial": {}, "surname": {}}
    for (kind, key), entries in grouped.items():
        maps[kind][key] = sorted(entries, key=lambda item: item[0])
    for surname, exact_keys in surname_keys.items():
        if len(exact_keys) == 1:
            maps["surname"][surname] = maps["exact"][next(iter(exact_keys))]
    maps["_surname_prefix_index"] = _surname_prefix_index(maps["exact"])

    legacy = {"_lookup_meta": {"maps": maps}, "_match_stats": defaultdict(int), "_fuzzy_audit": []}
    return legacy


def get_sr_win_pct_asof(name, history_lookup, as_of_date):
    """
    Point-in-time counterpart to get_sr_win_pct: strike rate as it stood on (or
    just before) as_of_date, using history built by build_strike_rate_history_lookup.

    Returns -1.0 (the same "unknown/insufficient data" sentinel get_sr_win_pct
    uses) rather than falling back to a later snapshot — using a snapshot from
    after the race would reintroduce the exact look-ahead leak this exists to
    remove.
    """
    as_of_date = _coerce_date(as_of_date)
    if not name or not history_lookup or not as_of_date:
        return -1.0
    meta = history_lookup.get("_lookup_meta", {}) if isinstance(history_lookup, dict) else {}
    maps = meta.get("maps") or {}
    keys = name_key_parts(name)
    stats = history_lookup.get("_match_stats") if isinstance(history_lookup, dict) else None

    for kind in ("exact", "all_initials", "first_initial", "surname"):
        entries = maps.get(kind, {}).get(keys[kind])
        if not entries:
            continue
        eligible = [data for snapshot_date, data in entries if snapshot_date <= as_of_date]
        if not eligible:
            continue
        data = eligible[-1]
        if stats is not None:
            stats["initials" if kind in {"all_initials", "first_initial"} else "surname_unique" if kind == "surname" else "exact"] += 1
        runs = data.get("L100Runs", 0)
        wins = data.get("L100Wins", 0)
        if runs < 10:
            return -1.0
        return (wins / runs) * 100.0

    exact_map = maps.get("exact", {})
    surname_index = maps.get("_surname_prefix_index", {})
    if keys["exact"] and exact_map and not _is_partnership_name(name):
        matched_key, score = _fuzzy_match_exact_key(keys["exact"], exact_map, surname_index)
        if matched_key:
            eligible = [data for snapshot_date, data in exact_map[matched_key] if snapshot_date <= as_of_date]
            if eligible:
                data = eligible[-1]
                if stats is not None:
                    stats["fuzzy"] += 1
                audit = history_lookup.get("_fuzzy_audit") if isinstance(history_lookup, dict) else None
                if audit is not None:
                    audit.append({
                        "query": name, "matched_name": data.get("name"), "similarity": round(score, 4),
                    })
                runs = data.get("L100Runs", 0)
                wins = data.get("L100Wins", 0)
                if runs < 10:
                    return -1.0
                return (wins / runs) * 100.0

    if stats is not None:
        stats["unmatched"] += 1
    return -1.0


MATCH_TIERS = ("exact", "initials", "surname_unique", "fuzzy", "unmatched")


def log_match_stats(log, jockey_lookup, trainer_lookup):
    """Log a per-run match-rate summary and return it as a plain dict so callers
    can persist it (e.g. backtest.py writes it to the DB) and compare
    unmatched-row percentage run over run to catch matching regressions."""
    j = jockey_lookup.get("_match_stats", {}) if isinstance(jockey_lookup, dict) else {}
    t = trainer_lookup.get("_match_stats", {}) if isinstance(trainer_lookup, dict) else {}
    horse_rows = sum(j.get(k, 0) for k in MATCH_TIERS)
    trainer_rows = sum(t.get(k, 0) for k in MATCH_TIERS)
    jockey_unmatched_pct = (j.get("unmatched", 0) / horse_rows * 100.0) if horse_rows else 0.0
    trainer_unmatched_pct = (t.get("unmatched", 0) / trainer_rows * 100.0) if trainer_rows else 0.0
    log.info(
        "Strike-rate matching summary: total_horse_rows_checked=%s "
        "jockey_exact_matches=%s jockey_initials_matches=%s jockey_surname_unique_matches=%s "
        "jockey_fuzzy_matches=%s jockey_unmatched=%s jockey_unmatched_pct=%.2f "
        "trainer_exact_matches=%s trainer_initials_matches=%s trainer_surname_unique_matches=%s "
        "trainer_fuzzy_matches=%s trainer_unmatched=%s trainer_unmatched_pct=%.2f",
        horse_rows,
        j.get("exact", 0), j.get("initials", 0), j.get("surname_unique", 0),
        j.get("fuzzy", 0), j.get("unmatched", 0), jockey_unmatched_pct,
        t.get("exact", 0), t.get("initials", 0), t.get("surname_unique", 0),
        t.get("fuzzy", 0), t.get("unmatched", 0), trainer_unmatched_pct,
    )

    jockey_fuzzy_audit = jockey_lookup.get("_fuzzy_audit", []) if isinstance(jockey_lookup, dict) else []
    trainer_fuzzy_audit = trainer_lookup.get("_fuzzy_audit", []) if isinstance(trainer_lookup, dict) else []
    for label, audit in (("jockey", jockey_fuzzy_audit), ("trainer", trainer_fuzzy_audit)):
        # audit has one entry per horse-ROW that hit a fuzzy match, so the same
        # (query, matched_name) pair can appear dozens of times per run at the
        # same timestamp. Log each unique pair once with an occurrence count
        # instead of repeating the line per row.
        counts = Counter()
        best_score = {}
        for entry in audit:
            key = (entry["query"], entry["matched_name"])
            counts[key] += 1
            best_score[key] = max(best_score.get(key, 0.0), entry["similarity"])
        unique_pairs = counts.most_common()
        for (query, matched_name), count in unique_pairs[:50]:
            log.info(
                "Fuzzy %s match (tier 4, audit): query=%r matched_name=%r similarity=%.4f matched %s time(s) this run",
                label, query, matched_name, best_score[(query, matched_name)], count,
            )
        if len(unique_pairs) > 50:
            log.info(
                "Fuzzy %s match audit truncated: %s more unique fuzzy match pair(s) not logged individually.",
                label, len(unique_pairs) - 50,
            )

    return {
        'total_horse_rows_checked': horse_rows,
        'jockey': {**{k: j.get(k, 0) for k in MATCH_TIERS}, 'unmatched_pct': jockey_unmatched_pct},
        'trainer': {**{k: t.get(k, 0) for k in MATCH_TIERS}, 'unmatched_pct': trainer_unmatched_pct},
    }
