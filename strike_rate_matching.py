import re
from collections import Counter, defaultdict

_TITLES = {"mr", "mrs", "ms", "miss"}


def normalize_name(name):
    """Normalise jockey/trainer names for strike-rate lookup."""
    if not name:
        return ""
    value = str(name).lower().strip()
    value = re.sub(r"[’‘`´]", "'", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    parts = [p for p in re.sub(r"\s+", " ", value).strip().split(" ") if p]
    while parts and parts[0] in _TITLES:
        parts.pop(0)
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

    legacy = dict(maps["exact"])
    legacy["_lookup_meta"] = {"maps": maps}
    legacy["_match_stats"] = defaultdict(int)
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


def log_match_stats(log, jockey_lookup, trainer_lookup):
    j = jockey_lookup.get("_match_stats", {}) if isinstance(jockey_lookup, dict) else {}
    t = trainer_lookup.get("_match_stats", {}) if isinstance(trainer_lookup, dict) else {}
    horse_rows = sum(j.get(k, 0) for k in ("exact", "initials", "surname_unique", "unmatched"))
    log.info(
        "Strike-rate matching summary: total_horse_rows_checked=%s jockey_exact_matches=%s jockey_initials_matches=%s jockey_surname_unique_matches=%s jockey_unmatched=%s trainer_exact_matches=%s trainer_initials_matches=%s trainer_surname_unique_matches=%s trainer_unmatched=%s",
        horse_rows,
        j.get("exact", 0), j.get("initials", 0), j.get("surname_unique", 0), j.get("unmatched", 0),
        t.get("exact", 0), t.get("initials", 0), t.get("surname_unique", 0), t.get("unmatched", 0),
    )
