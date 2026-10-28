#!/usr/bin/env python3
"""Download the source files and rebuild everything derived from them (the weekly job).

    python update_data.py

0. airports.csv from OurAirports (airport positions, names, field elevations)
1. ReleasableAircraft.zip from the FAA registry  -> build_db.py --zip      -> faa.db
2. the ADS-B Exchange aircraft database          -> build_candidates.py --refresh -> candidates.csv
3. the FAA Part 135 operator list                -> build_part135.py      -> part135.csv

    python update_data.py --airports-only     # just step 0

Each download goes to a temporary file and replaces the old one only if it looks
complete, and a step that fails stops the run, so a bad download never wipes
a good copy. targets.csv, ignored.csv and history.csv are not touched.
"""
import argparse
import csv
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ZIP_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"
ZIP_PATH = HERE / "ReleasableAircraft.zip"
AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"   # OurAirports
AIRPORTS_PATH = HERE / "airports.csv"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36"   # the FAA refuses requests that don't look like a browser


def fetch(url, path):
    """Download url to a temporary file next to path and return it, if it arrived whole."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    print(f"downloading {url}", flush=True)
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        expected = int(r.headers.get("Content-Length") or 0)
        size = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            size += len(chunk)
    if expected and size != expected:
        sys.exit(f"incomplete download ({size:,} of {expected:,} bytes); keeping the old {path.name}")
    return tmp, size


def download_zip():
    tmp, size = fetch(ZIP_URL, ZIP_PATH)
    try:
        with zipfile.ZipFile(tmp) as z:
            bad = z.testzip()
            if bad or "MASTER.txt" not in z.namelist():
                sys.exit(f"downloaded zip is damaged or missing MASTER.txt ({bad}); keeping the old zip")
    except zipfile.BadZipFile:
        sys.exit("downloaded file is not a zip; keeping the old zip")
    os.replace(tmp, ZIP_PATH)
    print(f"saved {size:,} bytes", flush=True)


def download_airports():
    tmp, size = fetch(AIRPORTS_URL, AIRPORTS_PATH)
    with open(tmp, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = sum(1 for r in reader if r.get("ident"))
        needed = {"ident", "type", "latitude_deg", "longitude_deg", "elevation_ft", "municipality", "iso_country"}
        if rows < 50000 or not needed <= set(reader.fieldnames or ()):
            sys.exit(f"airports.csv looks wrong ({rows:,} rows); keeping the old copy")
    os.replace(tmp, AIRPORTS_PATH)
    print(f"saved {size:,} bytes, {rows:,} airports", flush=True)


def run(*cmd):
    print("\n$ " + " ".join(cmd), flush=True)
    subprocess.run([sys.executable, *cmd], cwd=HERE, check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--airports-only", action="store_true", help="just download airports.csv")
    args = ap.parse_args()

    download_airports()
    if args.airports_only:
        return
    download_zip()
    run("build_db.py", "--zip")
    run("build_candidates.py", "--refresh")
    run("build_part135.py")
    print("\nupdate complete")


if __name__ == "__main__":
    main()
