"""Query helpers for the FAA registry database built by build_db.py.

    import faa
    faa.owner_of("N12345")
    faa.owners_of_model(mfr="BOMBARDIER", model="BD-700-2A12")
    faa.history("N12345")
    faa.find_models("bombardier", "700")
    faa.search_owner("flexjet")

All functions return plain dicts / lists of dicts.
"""
import re
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "faa.db"

_con = None


def connect(path=None):
    """Return a shared read-only connection (rows as sqlite3.Row)."""
    global _con
    if _con is None or path is not None:
        p = Path(path or DB_PATH)
        if not p.exists():
            raise FileNotFoundError(f"{p} not found - run build_db.py first")
        _con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        _con.row_factory = sqlite3.Row
    return _con


def _q(sql, params=()):
    return [dict(r) for r in connect().execute(sql, params)]


def norm_n(tail):
    """'N12345', 'n 12345', '12345' -> '12345'."""
    t = re.sub(r"\s+", "", str(tail)).upper()
    return t[1:] if t.startswith("N") else t


def _clean(d):
    """Drop None values so results are easy to read."""
    return {k: v for k, v in d.items() if v is not None}


def owner_of(tail):
    """Current registration for a tail number, or None.

    Falls back to the deregistered file (key 'deregistered': True) if the
    aircraft is no longer on the active register.
    """
    n = norm_n(tail)
    rows = _q("SELECT * FROM v_aircraft WHERE n_number = ?", (n,))
    if rows:
        return _clean(rows[0])
    rows = _q(
        "SELECT d.*, a.mfr AS mfr, a.model AS model FROM dereg d "
        "LEFT JOIN acftref a ON a.code = d.mfr_mdl_code "
        "WHERE d.n_number = ? ORDER BY d.cancel_date DESC LIMIT 1",
        (n,),
    )
    if rows:
        r = _clean(rows[0])
        r["deregistered"] = True
        return r
    return None


def history(tail):
    """All deregistered records for a tail (most recent first) plus the
    current record, if any. Useful for past owners."""
    n = norm_n(tail)
    out = []
    cur = _q("SELECT * FROM v_aircraft WHERE n_number = ?", (n,))
    out += [dict(_clean(r), current=True) for r in cur]
    out += [
        _clean(r)
        for r in _q(
            "SELECT d.*, a.mfr AS mfr, a.model AS model FROM dereg d "
            "LEFT JOIN acftref a ON a.code = d.mfr_mdl_code "
            "WHERE d.n_number = ? ORDER BY d.cancel_date DESC",
            (n,),
        )
    ]
    return out


def find_models(mfr=None, model=None, limit=200):
    """Search the aircraft reference table (case-insensitive substring match).
    Returns code/mfr/model plus the number of currently registered aircraft."""
    sql = (
        "SELECT a.code, a.mfr, a.model, a.type_acft, a.no_seats, "
        "(SELECT COUNT(*) FROM master m WHERE m.mfr_mdl_code = a.code) AS registered "
        "FROM acftref a WHERE 1=1"
    )
    params = []
    if mfr:
        sql += " AND a.mfr LIKE ?"
        params.append(f"%{mfr}%")
    if model:
        sql += " AND a.model LIKE ?"
        params.append(f"%{model}%")
    sql += " ORDER BY a.mfr, a.model LIMIT ?"
    params.append(limit)
    return _q(sql, params)


def owners_of_model(mfr=None, model=None, code=None, exact=False):
    """Every currently registered aircraft of a make/model.

    Pass `code` (ACFTREF code) for an exact model, or mfr/model strings for a
    substring match (exact=True for equality). Returns one row per aircraft.
    """
    sql = (
        "SELECT n_number, name, street, street2, city, state, zip_code, country, "
        "serial_number, year_mfr, mfr, model, fract_owner, type_registrant, mode_s_code_hex, "
        "other_names_1, other_names_2, other_names_3, other_names_4, other_names_5 "
        "FROM v_aircraft WHERE 1=1"
    )
    params = []
    if code:
        sql += " AND mfr_mdl_code = ?"
        params.append(code)
    if mfr:
        sql += " AND mfr = ?" if exact else " AND mfr LIKE ?"
        params.append(mfr if exact else f"%{mfr}%")
    if model:
        sql += " AND model = ?" if exact else " AND model LIKE ?"
        params.append(model if exact else f"%{model}%")
    if not (code or mfr or model):
        raise ValueError("give code, mfr and/or model")
    sql += " ORDER BY name, n_number"
    return [_clean(r) for r in _q(sql, params)]


def search_owner(text, limit=500):
    """Aircraft whose owner name (or co-owner names) matches. Uses the FTS
    index for whole-word terms (e.g. 'flexjet'); falls back to LIKE."""
    con = connect()
    terms = re.findall(r"[A-Za-z0-9]+", text)
    if terms:
        try:
            q = " ".join(f'"{t}"' for t in terms)
            ids = [
                r[0]
                for r in con.execute(
                    "SELECT rowid FROM master_fts WHERE master_fts MATCH ? LIMIT ?",
                    (q, limit),
                )
            ]
            if ids:
                marks = ",".join("?" * len(ids))
                return [
                    _clean(r)
                    for r in _q(
                        f"SELECT v.* FROM v_aircraft v JOIN master m "
                        f"ON m.n_number = v.n_number WHERE m.rowid IN ({marks}) "
                        f"ORDER BY v.name, v.n_number",
                        ids,
                    )
                ]
        except sqlite3.OperationalError:
            pass
    return [
        _clean(r)
        for r in _q(
            "SELECT * FROM v_aircraft WHERE name LIKE ? ORDER BY name, n_number LIMIT ?",
            (f"%{text}%", limit),
        )
    ]


def by_serial(serial, code=None):
    """Find aircraft by manufacturer serial number (optionally within a model)."""
    sql = "SELECT * FROM v_aircraft WHERE serial_number = ?"
    params = [str(serial).strip()]
    if code:
        sql += " AND mfr_mdl_code = ?"
        params.append(code)
    return [_clean(r) for r in _q(sql, params)]


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        sys.exit("usage: python faa.py N12345 | python faa.py model <mfr> [model]")
    if sys.argv[1] == "model":
        res = owners_of_model(*sys.argv[2:4])
    else:
        res = owner_of(sys.argv[1])
    print(json.dumps(res, indent=2))
