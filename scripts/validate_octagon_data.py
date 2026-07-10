#!/usr/bin/env python3
"""Validate the Octagon-AI historical CSV snapshot used by mma_sync.py.

Downloads the four required Octagon-AI CSV files from an immutable commit/tag
and prints OCTAGON_DATA_VALID=true only after row-count and schema checks pass.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_RAW = "https://raw.githubusercontent.com/sbalagan22/Octagon-AI"
REQUIRED_FILES = {
    "Fights.csv": "newdata/Fights.csv",
    "Fighters.csv": "newdata/Fighters.csv",
    "Events.csv": "newdata/Events.csv",
    "current_glicko.csv": "newdata/current_glicko.csv",
}
MIN_FIGHTS = 1000
MIN_FIGHTERS = 500
REQUIRED_COLUMNS = {
    "Fights.csv": {
        "Event_Id", "Fighter_Id_1", "Fighter_Id_2", "Result_1", "Fight_Time", "Round",
        "STR_1", "STR_2", "Sig. Str. %_1", "Sig. Str. %_2", "TD_1", "TD_2",
        "KD_1", "KD_2", "SUB_1", "SUB_2", "Ctrl_1", "Ctrl_2",
        "Head_%_1", "Head_%_2", "Body_%_1", "Body_%_2", "Leg_%_1", "Leg_%_2",
        "Distance_%_1", "Distance_%_2", "Clinch_%_1", "Clinch_%_2", "Ground_%_1", "Ground_%_2",
    },
    "Fighters.csv": {"Fighter_Id", "Full Name", "W", "L", "Height", "Reach", "Stance"},
    "Events.csv": {"Event_Id", "Date"},
    "current_glicko.csv": {"Fighter_Id", "Rating", "RD"},
}


def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "the-form-analyst-octagon-validator/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = resp.read()
    if not payload or payload[:80].startswith(b"404") or b"404: Not Found" in payload[:120]:
        raise RuntimeError(f"empty/not-found response for {url}")
    dest.write_bytes(payload)


def _inspect_csv(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            raise RuntimeError(f"{path.name} is empty")
        rows = sum(1 for _ in reader)
    return {"rows": rows, "columns": header}


def validate(ref: str, out_dir: Path | None = None) -> dict:
    if not ref or ref == "main":
        raise RuntimeError("Ref must be an immutable commit SHA or tag, not the moving main branch")
    work_ctx = tempfile.TemporaryDirectory() if out_dir is None else None
    root = Path(work_ctx.name) if work_ctx else out_dir
    assert root is not None
    root.mkdir(parents=True, exist_ok=True)

    try:
        report = {"ref": ref, "files": {}, "urls": {}}
        for fname, repo_path in REQUIRED_FILES.items():
            url = f"{REPO_RAW}/{ref}/{repo_path}"
            dest = root / fname
            _download(url, dest)
            info = _inspect_csv(dest)
            missing = sorted(REQUIRED_COLUMNS[fname] - set(info["columns"]))
            if missing:
                raise RuntimeError(f"{fname} missing required columns: {missing}")
            report["files"][fname] = info
            report["urls"][fname] = url

        fights = report["files"]["Fights.csv"]["rows"]
        fighters = report["files"]["Fighters.csv"]["rows"]
        if fights < MIN_FIGHTS or fighters < MIN_FIGHTERS:
            raise RuntimeError(
                f"Octagon snapshot is too small: fights={fights} fighters={fighters} "
                f"required_fights={MIN_FIGHTS} required_fighters={MIN_FIGHTERS}"
            )
        report["OCTAGON_DATA_VALID"] = True
        report["fights"] = fights
        report["fighters"] = fighters
        return report
    finally:
        if work_ctx:
            work_ctx.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True, help="Immutable Octagon-AI commit SHA or tag to validate")
    parser.add_argument("--out-dir", type=Path, help="Optional directory to keep downloaded CSVs")
    args = parser.parse_args(argv)
    try:
        report = validate(args.ref, args.out_dir)
    except Exception as exc:
        print("OCTAGON_DATA_VALID=false")
        print(f"error={exc}")
        return 1

    print("OCTAGON_DATA_VALID=true")
    print(f"ref={report['ref']}")
    print(f"fights={report['fights']}")
    print(f"fighters={report['fighters']}")
    for fname, info in report["files"].items():
        print(f"{fname}: rows={info['rows']} columns={json.dumps(info['columns'])}")
        print(f"{fname}: url={report['urls'][fname]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
