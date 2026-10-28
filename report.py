#!/usr/bin/env python3
"""Print the movements of each aircraft in history.csv.

One block per aircraft, with a line each time its location changes
(most recently active aircraft first).

    python report.py                 # every aircraft
    python report.py TAIL            # just one (several tails allowed)
    python report.py --since 7       # only movements in the last 7 days
    python report.py --min-moves 3   # skip aircraft with fewer than 3 location changes
    python report.py --days 7        # only aircraft with a ground position in the last 7 days (default 30)
    python report.py --map flights.html   # also write a 3D globe of the movements
    python report.py --airborne      # also show airborne and en route positions

Unless you pass --airborne, only positions on the ground at an airport are shown,
and aircraft with no such position are left out. An aircraft is only listed if it
has such a position in the last 30 days (--days), but then its whole history is shown.

Times are UTC. "en route" means no airport within 25 nm. An airport is only
reported as a location if the aircraft was on the ground. An airborne position
shows as "near XXXX (airborne)": it may be arriving or departing there, or just
passing. If an aircraft's last contact was airborne, it is marked as such, since
we don't know where it landed.

Aircraft on the FAA's operator list (part135.csv, built by build_part135.py) are
marked with the certificate they're listed under: "Part 135 (On-demand charter)",
or "Part 121/135" for air carriers holding both. Nothing is shown for any other
aircraft, since not being on the list doesn't tell us what it is.
"""
import argparse
import calendar
import csv
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import adsb
import flightmap
import targets as store
from adsb import airport_locations


PART135_CSV = Path(__file__).resolve().parent / "part135.csv"
ADSBX_URL = "https://globe.adsbexchange.com/?icao="


def load_part135():
    """{tail: [(cfr, operator), ...]} from part135.csv (build_part135.py), or None."""
    if not PART135_CSV.exists():
        print(f"warning: {PART135_CSV.name} not found (run build_part135.py); "
              "Part 135 and 121 marks not shown", file=sys.stderr)
        return None
    listed = defaultdict(list)
    with open(PART135_CSV, newline="") as f:
        for r in csv.DictReader(f):
            listed[r["tail"]].append((r["cfr"], r["operator"]))
    return listed


CERTIFICATE_LABELS = {"135": "Part 135 (On-demand charter)", "121/135": "Part 121/135"}


def operating_rules(tail, part135):
    """What the FAA list says an aircraft is, or None if it isn't on the list
    (which says nothing about what it is). The certificate types are labelled as
    listed, so an aircraft on several certificates shows each one."""
    if part135 is None or not part135.get(tail):
        return None
    cfrs = sorted({cfr for cfr, _ in part135[tail]}, key=lambda c: (c == "135", c))
    return "; ".join(CERTIFICATE_LABELS.get(c, f"Part {c}") for c in cfrs)


def movements(rows):
    """Collapse consecutive rows with the same (airport, airborne) state."""
    out, last_key = [], None
    for r in sorted(rows, key=lambda r: r["position_time"]):
        airborne = r["airborne"] == "yes"
        key = (r["airport"], airborne)
        if key != last_key:
            out.append((r, airborne))
            last_key = key
    return out


def map_move(r, airborne, places, coords):
    """One movement as the globe wants it. A ground position is drawn at its airport."""
    airport = coords.get(r["airport"]) if r["airport"] else None
    lat, lon = (airport["lat"], airport["lon"]) if airport and not airborne else (float(r["lat"]), float(r["lon"]))
    t = calendar.timegm(time.strptime(r["position_time"], "%Y-%m-%dT%H:%M:%SZ"))
    return {"t": t, "airport": r["airport"], "place": places.get(r["airport"], ""),
            "name": airport["name"] if airport else "", "lat": lat, "lon": lon, "airborne": airborne}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tails", nargs="*", help="only these tail numbers")
    ap.add_argument("--since", type=float, metavar="DAYS", help="only positions from the last DAYS days")
    ap.add_argument("--min-moves", type=int, default=0, metavar="N",
                    help="skip aircraft with fewer than N location changes")
    ap.add_argument("--days", type=float, default=30, metavar="DAYS",
                    help="only list aircraft with a ground position in the last DAYS days (default 30)")
    ap.add_argument("--map", metavar="FILE", help="also write an interactive globe (HTML) of the aircraft listed")
    ap.add_argument("--airborne", action="store_true",
                    help="also show airborne and en route positions")
    args = ap.parse_args()

    history = store.load_history()
    if not history:
        raise SystemExit("history.csv is empty or missing; run watch.py (and finder.py) first")
    wanted = {t.upper() for t in args.tails}
    cutoff = adsb.iso(time.time() - args.since * 86400) if args.since else ""
    targets = {r["tail"]: r for r in store.load_targets().values()}
    part135 = load_part135()

    by_tail = defaultdict(list)
    for r in history:
        if (not wanted or r["tail"] in wanted) and r["position_time"] >= cutoff:
            by_tail[r["tail"]].append(r)
    if not by_tail:
        raise SystemExit("no matching history")

    places = airport_locations({r["airport"] for rows in by_tail.values() for r in rows})
    loc_w = max([len("Location")] + [len(v) for v in places.values()])
    order = sorted(by_tail, key=lambda t: max(r["position_time"] for r in by_tail[t]), reverse=True)
    recent = adsb.iso(time.time() - args.days * 86400)
    shown = 0
    mapped = []
    coords = flightmap.airport_details({r["airport"] for rows in by_tail.values() for r in rows}) if args.map else {}
    for tail in order:
        rows = by_tail[tail]
        moves = movements(rows)
        if not args.airborne:
            # only positions known to be on the ground at an airport
            rows = [r for r in rows if r["airport"] and r["airborne"] != "yes"]
            moves = movements(rows)
            if not moves:
                continue
        if not any(r["position_time"] >= recent for r in by_tail[tail]
                   if r["airport"] and r["airborne"] != "yes"):
            continue
        if len(moves) - 1 < args.min_moves:
            continue
        shown += 1
        first = by_tail[tail][0]
        t = targets.get(tail, {})
        print(f"\n{tail}  {first['model']}  {t.get('owner') or '(none on file)'}")
        print(f"    {ADSBX_URL}{first['icao']}")
        rules = operating_rules(tail, part135)
        if rules:
            print(f"    {rules}")
        if t:
            print(f"    watched: first {t['first_seen']}, last {t['last_seen']}, {t['sightings']} sightings")
        if args.map:
            mapped.append({"tail": tail, "icao": first["icao"], "model": first["model"],
                           "owner": t.get("owner") or "", "rules": rules or "",
                           "moves": [map_move(r, airborne, places, coords) for r, airborne in moves]})
        for i, (r, airborne) in enumerate(moves):
            when = r["position_time"].replace("T", " ")[:16] + "Z"
            if r["airport"] and airborne:
                what = f"near {r['airport']:<6} {places.get(r['airport'], ''):<{loc_w}} (airborne)"
            elif r["airport"]:
                what = f"{r['airport']:<11} {places.get(r['airport'], ''):<{loc_w}}"
            else:
                what = f"{'en route':<11} {r['lat']},{r['lon']}" + (" (airborne)" if airborne else "")
            note = ""
            if i == len(moves) - 1 and airborne:
                note = "   <- last contact; still airborne, landing location unknown"
            print(f"    {when}  {what}{note}".rstrip())
    print(f"\n{shown} aircraft")
    if args.map:
        flightmap.write_map(args.map, mapped)
        print(f"globe written to {args.map}")
    if part135 is not None:
        print(f"Part 135 and 121 marks are from the FAA operator list downloaded "
              f"{datetime.fromtimestamp(PART135_CSV.stat().st_mtime):%Y-%m-%d}. An aircraft that isn't "
              "marked is not on that list.")


if __name__ == "__main__":
    main()
