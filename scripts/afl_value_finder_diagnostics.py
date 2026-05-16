#!/usr/bin/env python3
"""Diagnostic checks for AFL Value Finder player/stat matching.

This offline check mirrors the punctuation-insensitive name matching used by
/api/afl/value-finder and compares the current-season rows/averages for Finn
O'Sullivan plus a second player from the bundled 2026 AFLTables export.

Usage:
    python scripts/afl_value_finder_diagnostics.py
    python scripts/afl_value_finder_diagnostics.py --player "Finn O’Sullivan" --player "Finn Callaghan"
"""
from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from pathlib import Path

DEFAULT_PLAYERS = ["Finn O’Sullivan", "Finn Callaghan"]
CSV_PATH = Path(__file__).resolve().parents[1] / "data" / "afl_2026_stats.csv"


def canonical_player_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", " ".join((value or "").lower().split()))
    normalized = normalized.replace("’", "'").replace("`", "'")
    return re.sub(r"[^a-z0-9]", "", normalized)


def player_display(row: dict) -> str:
    return f"{row.get('First.name', '')} {row.get('Surname', '')}".strip()


def as_int(value: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=str(CSV_PATH), help="Path to AFLTables-format 2026 CSV")
    parser.add_argument("--player", action="append", default=[], help="Player name to diagnose; repeatable")
    parser.add_argument("--season", type=int, default=2026)
    args = parser.parse_args()

    players = args.player or DEFAULT_PLAYERS
    csv_path = Path(args.csv)
    rows = list(csv.DictReader(csv_path.open(newline="", encoding="utf-8")))

    ok = True
    for requested_name in players:
        key = canonical_player_name(requested_name)
        matched = [
            r for r in rows
            if as_int(r.get("Season")) == args.season
            and canonical_player_name(player_display(r)) == key
        ]
        matched.sort(key=lambda r: r.get("Date", ""), reverse=True)
        values = [as_int(r.get("Disposals")) for r in matched]
        avg = round(sum(values) / len(values), 1) if values else 0.0
        last5 = values[:5]
        rounds = [r.get("Round") for r in matched]
        teams = sorted({r.get("Playing.for") for r in matched})
        player_ids = sorted({r.get("ID") for r in matched})
        print(
            f"{requested_name}: canonical={key} season={args.season} "
            f"player_ids={player_ids} teams={teams} row_count={len(matched)} "
            f"rounds={rounds} disposals={values} season_avg={avg} "
            f"last5_avg={round(sum(last5) / len(last5), 1) if last5 else 0.0}"
        )
        if requested_name in {"Finn O'Sullivan", "Finn O’Sullivan", "Finn OSullivan"} and avg != 20.7:
            ok = False
            print("ERROR: Finn O'Sullivan expected 2026 disposal average 20.7")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
