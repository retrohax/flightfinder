"""Storage for the target list and the movement history (plain CSV files).

targets.csv   aircraft from candidates.csv that watch.py has seen near the watched airport
ignored.csv   aircraft removed with `watch.py --remove`; watch.py won't re-add them
history.csv   one row per new position seen, written by watch.py and finder.py
"""
import csv
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGETS_CSV = HERE / "targets.csv"
IGNORED_CSV = HERE / "ignored.csv"
HISTORY_CSV = HERE / "history.csv"
CANDIDATES_CSV = HERE / "candidates.csv"

TARGET_COLS = ["tail", "icao", "model", "owner", "first_seen", "last_seen", "sightings"]
IGNORED_COLS = ["tail", "icao", "removed"]
HISTORY_COLS = ["tail", "icao", "model", "position_time", "airport", "altitude", "airborne",
                "lat", "lon", "precision", "source", "queried"]
OLD_HISTORY_COLS = [c for c in HISTORY_COLS if c != "airborne"]


def _migrate_history():
    """Add the airborne column to a history.csv written before it existed."""
    import adsb
    with open(HISTORY_CSV, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["airborne"] = adsb.airborne_flag(int(r["altitude"]) if r["altitude"].lstrip("-").isdigit()
                                           else r["altitude"])
    _write(HISTORY_CSV, HISTORY_COLS, rows)
    print(f"{HISTORY_CSV.name}: added the airborne column to {len(rows):,} rows")


def _read(path, cols):
    if not path.exists() or path.stat().st_size == 0:
        return []
    if path == HISTORY_CSV:
        with open(path, newline="") as f:
            if next(csv.reader(f), None) == OLD_HISTORY_COLS:
                _migrate_history()
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != cols:
            sys.exit(f"{path.name} has unexpected columns {reader.fieldnames}; expected {cols}")
        return list(reader)


def _write(path, cols, rows):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def load_candidates():
    """candidates.csv rows keyed by hex (the first row wins if a hex repeats)."""
    if not CANDIDATES_CSV.exists():
        sys.exit(f"{CANDIDATES_CSV.name} not found; run build_candidates.py first")
    by_hex = {}
    with open(CANDIDATES_CSV, newline="") as f:
        for c in csv.DictReader(f):
            by_hex.setdefault(c["icao"].strip().lower(), c)
    return by_hex


def load_targets():
    """{hex: row}"""
    return {r["icao"]: r for r in _read(TARGETS_CSV, TARGET_COLS)}


def save_targets(targets):
    _write(TARGETS_CSV, TARGET_COLS, sorted(targets.values(), key=lambda r: r["tail"]))


def load_ignored():
    """{hex: row}"""
    return {r["icao"]: r for r in _read(IGNORED_CSV, IGNORED_COLS)}


def save_ignored(ignored):
    _write(IGNORED_CSV, IGNORED_COLS, sorted(ignored.values(), key=lambda r: r["tail"]))


def load_history():
    return _read(HISTORY_CSV, HISTORY_COLS)


def latest_position_times():
    """{hex: newest position_time in history.csv}, to skip positions already recorded."""
    latest = {}
    for r in load_history():
        if r["position_time"] > latest.get(r["icao"], ""):
            latest[r["icao"]] = r["position_time"]
    return latest


def append_history(rows):
    """Append row dicts (HISTORY_COLS keys) to history.csv."""
    if not rows:
        return
    _read(HISTORY_CSV, HISTORY_COLS)  # exits if the existing header is different
    new_file = not HISTORY_CSV.exists() or HISTORY_CSV.stat().st_size == 0
    with open(HISTORY_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HISTORY_COLS)
        if new_file:
            w.writeheader()
        w.writerows(rows)
