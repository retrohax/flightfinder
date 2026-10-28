#!/usr/bin/env python3
"""Build faa.db (SQLite) from the FAA Releasable Aircraft Database files.

Usage:
    python build_db.py                 # read loose *.txt files next to this script
    python build_db.py --zip           # read from ReleasableAircraft.zip instead
    python build_db.py --out other.db
"""
import argparse
import csv
import io
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

# file -> table
TABLES = {
    "MASTER.txt": "master",
    "ACFTREF.txt": "acftref",
    "ENGINE.txt": "engine",
    "DEREG.txt": "dereg",
    "RESERVED.txt": "reserved",
    "DEALER.txt": "dealer",
    "DOCINDEX.txt": "docindex",
}

INDEXES = [
    "CREATE INDEX idx_master_mdl    ON master(mfr_mdl_code)",
    "CREATE INDEX idx_master_name   ON master(name)",
    "CREATE INDEX idx_master_serial ON master(serial_number)",
    "CREATE INDEX idx_master_eng    ON master(eng_mfr_mdl)",
    "CREATE INDEX idx_dereg_nnum    ON dereg(n_number)",
    "CREATE INDEX idx_dereg_mdl     ON dereg(mfr_mdl_code)",
    "CREATE INDEX idx_dereg_name    ON dereg(name)",
    "CREATE INDEX idx_dereg_serial  ON dereg(serial_number)",
    "CREATE INDEX idx_acftref_mm     ON acftref(mfr, model)",
    "CREATE INDEX idx_reserved_nnum ON reserved(n_number)",
    "CREATE INDEX idx_docindex_coll  ON docindex(collateral)",
]

VIEW = """
CREATE VIEW v_aircraft AS
SELECT m.n_number, m.serial_number, m.mfr_mdl_code,
       a.mfr AS mfr, a.model AS model,
       a.type_acft AS acft_type, a.no_seats, a.ac_weight,
       m.year_mfr, m.type_registrant, m.name, m.street, m.street2,
       m.city, m.state, m.zip_code, m.country, m.fract_owner,
       m.other_names_1, m.other_names_2, m.other_names_3,
       m.other_names_4, m.other_names_5,
       m.status_code, m.certification, m.cert_issue_date,
       m.last_action_date, m.air_worth_date, m.expiration_date,
       m.mode_s_code_hex,
       e.mfr AS eng_mfr, e.model AS eng_model, e.horsepower, e.thrust
FROM master m
LEFT JOIN acftref a ON a.code = m.mfr_mdl_code
LEFT JOIN engine  e ON e.code = m.eng_mfr_mdl
"""


def norm_col(name):
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def open_text(fname, zf):
    if zf is not None:
        raw = zf.open(fname)
    else:
        raw = open(HERE / fname, "rb")
    # utf-8-sig drops the BOM; 'replace' guards against stray non-UTF-8 bytes
    return io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")


def load_file(con, fname, table, zf):
    with open_text(fname, zf) as fh:
        reader = csv.reader(fh)
        header = next(reader)
        cols, keep, seen = [], [], set()
        for i, h in enumerate(header):
            c = norm_col(h)
            if not c:  # trailing comma -> empty column
                continue
            while c in seen:
                c += "_"
            seen.add(c)
            cols.append(c)
            keep.append(i)
        n = len(header)
        con.execute(f"DROP TABLE IF EXISTS {table}")
        con.execute(f"CREATE TABLE {table} ({', '.join(f'{c} TEXT' for c in cols)})")
        sql = f"INSERT INTO {table} VALUES ({','.join('?' * len(cols))})"

        def rows():
            for r in reader:
                if not r:
                    continue
                r += [""] * (n - len(r))
                yield [(r[i].strip() or None) for i in keep]

        cur = con.executemany(sql, rows())
        count = cur.rowcount
    # N-numbers: stored padded in the files; keep trimmed + uppercase
    if "n_number" in cols:
        con.execute(f"UPDATE {table} SET n_number = UPPER(TRIM(n_number))")
    return count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="store_true", help="read ReleasableAircraft.zip")
    ap.add_argument("--out", default=str(HERE / "faa.db"))
    args = ap.parse_args()

    out = Path(args.out)
    tmp = out.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)

    zf = zipfile.ZipFile(HERE / "ReleasableAircraft.zip") if args.zip else None
    con = sqlite3.connect(tmp)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")

    for fname, table in TABLES.items():
        print(f"loading {fname:14s} -> {table} ...", end=" ", flush=True)
        n = load_file(con, fname, table, zf)
        con.commit()
        print(f"{n:,} rows")

    # primary-key-style lookups
    con.execute("CREATE UNIQUE INDEX idx_master_nnum ON master(n_number)")
    con.execute("CREATE UNIQUE INDEX idx_acftref_code ON acftref(code)")
    con.execute("CREATE UNIQUE INDEX idx_engine_code ON engine(code)")
    for stmt in INDEXES:
        con.execute(stmt)
    con.execute(VIEW)

    # optional full-text search on owner names
    try:
        con.execute(
            "CREATE VIRTUAL TABLE master_fts USING fts5("
            "name, other_names, content='', tokenize='unicode61')"
        )
        con.execute(
            "INSERT INTO master_fts(rowid, name, other_names) "
            "SELECT rowid, name, "
            "COALESCE(other_names_1,'')||' '||COALESCE(other_names_2,'')||' '||"
            "COALESCE(other_names_3,'')||' '||COALESCE(other_names_4,'')||' '||"
            "COALESCE(other_names_5,'') FROM master"
        )
        print("built FTS5 index on owner names")
    except sqlite3.OperationalError as e:
        print(f"FTS5 unavailable, skipping ({e})", file=sys.stderr)

    con.commit()
    con.execute("ANALYZE")
    con.close()
    tmp.replace(out)
    print(f"done: {out} ({out.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
