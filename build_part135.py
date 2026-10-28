#!/usr/bin/env python3
"""Download the FAA's list of aircraft on Part 135 charter certificates and
write part135.csv, which report.py uses to label aircraft Part 135 or Part 91.

    python build_part135.py             # download and convert
    python build_part135.py --xlsx F    # convert a spreadsheet you already downloaded

Source: "FAA-certificated Aircraft Operators (Legal Part 135 holders)",
https://www.faa.gov/about/officeorg/headquartersoffices/avs/faa-certificated-aircraft-operators-legal-part-135-holders
The FAA updates it about weekly and says it can lag; an aircraft that isn't
listed may still be flown by a charter operator (check with the local FSDO).
"""
import argparse
import csv
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
URL = ("https://www.faa.gov/about/officeorg/headquartersoffices/avs/"
       "faa-certificated-aircraft-operators-legal-part-135-holders.xlsx")
XLSX_PATH = HERE / "part135.xlsx"
OUT_PATH = HERE / "part135.csv"
COLS = ["cfr", "chdo", "designator", "operator", "model", "tail", "serial"]
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
HEADER = ["CFR", "CHDO", "DSGN", "Name", "Aircraft M/M/S", "Registration No.", "Serial No."]


def _column(ref):
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_rows(path):
    """Rows of the first worksheet as lists of strings (standard library only)."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
    sheet = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
    for row in ET.fromstring(z.read(sheet)).iter(f"{NS}row"):
        vals = {}
        for c in row.findall(f"{NS}c"):
            v = c.find(f"{NS}v")
            if c.get("t") == "inlineStr":
                vals[_column(c.get("r"))] = "".join(t.text or "" for t in c.iter(f"{NS}t"))
            elif v is not None:
                vals[_column(c.get("r"))] = shared[int(v.text)] if c.get("t") == "s" else v.text
        if vals:
            yield [vals.get(i, "").strip() for i in range(max(vals) + 1)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xlsx", metavar="FILE", help="convert this spreadsheet instead of downloading")
    args = ap.parse_args()

    path = Path(args.xlsx) if args.xlsx else XLSX_PATH
    if not args.xlsx:
        # faa.gov refuses requests that don't look like a browser
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36"})
        print(f"downloading {URL}")
        with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
            f.write(r.read())

    rows = read_rows(path)
    header = next(rows, None)
    if header is None or header[:len(HEADER)] != HEADER:
        sys.exit(f"unexpected spreadsheet columns {header}; expected {HEADER}")
    out = []
    for r in rows:
        r = r + [""] * (len(HEADER) - len(r))
        if r[5]:  # a few rows have no registration
            out.append([r[0], r[1], r[2], r[3], r[4], r[5].upper(), r[6]])
    with open(OUT_PATH, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(out)
    print(f"wrote {len(out):,} rows ({len({r[5] for r in out}):,} aircraft) to {OUT_PATH}")


if __name__ == "__main__":
    main()
