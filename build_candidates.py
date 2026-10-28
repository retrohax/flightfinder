#!/usr/bin/env python3
"""Build candidates.csv: the single list of aircraft that finder.py looks up
and report.py describes.

Sources (the only place the two are merged):
  - US (N-numbers): the FAA registry in faa.db (build_db.py). It's the
    authority on what is currently registered, and it catches jets that ADS-B
    Exchange has no type code for.
  - Everything else: ADS-B Exchange's aircraft database (basic-ac-db.json.gz,
    updated daily), filtered by ICAO type and registration prefix.

    python build_candidates.py                    # N + VH-
    python build_candidates.py --prefix VH- --prefix ZK-
    python build_candidates.py --refresh          # re-download the ADS-B Exchange db
"""
import argparse
import csv
import os
import gzip
import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import faa

HERE = Path(__file__).resolve().parent
ADSBX_DB_URL = "https://downloads.adsbexchange.com/downloads/basic-ac-db.json.gz"
ADSBX_DB_PATH = HERE / "basic-ac-db.json.gz"
OUT_PATH = HERE / "candidates.csv"

COLS = ["tail", "icao", "model", "registry_model", "year", "owner", "source"]

# FAA model -> (ACFTREF codes, marketing name). Where several marketing names
# share one FAA type certificate the FAA data can't tell them apart, so they're
# listed together.
FAA_MODELS = {
    "GV-SP (G550)":  (["3980203", "3980295"], "Gulfstream G550"),
    "GV-SP":         (["3980101"], "Gulfstream G550"),
    "GV-SP (G500)":  (["3980208"], "Gulfstream G500 (GV-SP)"),
    "G-V":           (["3980116"], "Gulfstream G-V"),
    "GVI (G650ER)":  (["3980050"], "Gulfstream G650ER"),
    "GVI (G650)":    (["3980214"], "Gulfstream G650"),
    "GVI":           (["3980121"], "Gulfstream G650"),
    "GVII-G500":     (["3980225", "3980226"], "Gulfstream G500"),
    "GVII-G600":     (["3980219", "05637SM"], "Gulfstream G600"),
    "GVIII-G700":    (["3980221", "3980222"], "Gulfstream G700"),
    "GVIII-G800":    (["3980321"], "Gulfstream G800"),
    "FALCON 6X":     (["2730185"], "Falcon 6X"),
    "FALCON 7X":     (["2730176"], "Falcon 7X"),  # the Falcon 8X has no FAA model of its own
    "BD-700-1A10":   (["1390006", "1390085"], "Global Express/6000/6500"),
    "BD-700-1A11":   (["1390022"], "Global 5000/5500"),
    "BD-700-2A12":   (["1390052"], "Global 7500/8000"),
    # VIP airliners and borderline-range jets
    "737-7BC":       (["1384509"], "Boeing BBJ"),
    "A319-133":      (["3930351"], "Airbus ACJ319"),
    "A319-133CJ":    (["3903034"], "Airbus ACJ319"),
    "ERJ 190-100 ECJ": (["3260413", "3260411"], "Embraer Lineage 1000"),
    "FALCON 900LX":  (["2730015"], "Falcon 900LX"),
}

# ICAO type designator -> marketing name, for non-US aircraft
ICAO_TYPES = {
    "GLF5": "Gulfstream G550/G-V",
    "GLF6": "Gulfstream G650/G650ER",
    "GA5C": "Gulfstream G500",
    "GA6C": "Gulfstream G600",
    "GA7C": "Gulfstream G700",
    "GA8C": "Gulfstream G800",
    "GLEX": "Global Express/6000/6500",
    "GL5T": "Global 5000/5500",
    "GL7T": "Global 7500/8000",
    "FA6X": "Falcon 6X",
    "FA7X": "Falcon 7X",
    "FA8X": "Falcon 8X",
}


def faa_candidates():
    rows = []
    for faa_model, (codes, name) in FAA_MODELS.items():
        for code in codes:
            for r in faa.owners_of_model(code=code):
                icao = (r.get("mode_s_code_hex") or "").lower()
                if not icao:
                    print(f"N{r['n_number']}: no Mode S hex in the FAA record; left out", file=sys.stderr)
                    continue
                rows.append(["N" + r["n_number"], icao, name, faa_model,
                             r.get("year_mfr") or "", r.get("name") or "", "faa"])
    return rows


def adsbx_candidates(prefixes):
    rows = []
    with gzip.open(ADSBX_DB_PATH, "rt", encoding="utf-8") as f:
        for line in f:
            try:
                a = json.loads(line)
            except json.JSONDecodeError:
                continue
            reg = (a.get("reg") or "").strip().upper()
            t = a.get("icaotype")
            if t in ICAO_TYPES and reg.startswith(prefixes) and a.get("icao"):
                rows.append([reg, a["icao"].strip().lower(), ICAO_TYPES[t], t,
                             a.get("year") or "", (a.get("ownop") or "").strip(), "adsbx"])
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prefix", action="append", metavar="PFX",
                    help="non-US registration prefix to include (repeatable; default VH-)")
    ap.add_argument("--refresh", action="store_true", help="re-download the ADS-B Exchange database")
    ap.add_argument("--out", default=str(OUT_PATH), metavar="FILE")
    args = ap.parse_args()
    prefixes = tuple(p.upper() for p in (args.prefix or ["VH-"]))
    if any(p.startswith("N") for p in prefixes):
        sys.exit("N-numbers always come from the FAA registry; don't pass an N prefix")

    if args.refresh or not ADSBX_DB_PATH.exists():
        print(f"downloading {ADSBX_DB_URL} ...")
        tmp = ADSBX_DB_PATH.with_suffix(".gz.tmp")   # so a failed download leaves the old copy alone
        urllib.request.urlretrieve(ADSBX_DB_URL, tmp)
        os.replace(tmp, ADSBX_DB_PATH)

    rows = faa_candidates() + adsbx_candidates(prefixes)
    rows.sort(key=lambda r: (r[6], r[2], r[0]))
    tmp_out = args.out + ".tmp"   # watch.py may be reading the file, so swap it in whole
    with open(tmp_out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    os.replace(tmp_out, args.out)

    by_src = Counter(r[6] for r in rows)
    print(f"wrote {len(rows):,} candidates to {args.out}: "
          f"{by_src['faa']:,} from the FAA registry, {by_src['adsbx']:,} from ADS-B Exchange "
          f"({', '.join(prefixes)})")


if __name__ == "__main__":
    main()
