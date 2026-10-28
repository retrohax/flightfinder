#!/usr/bin/env python3
"""Put the flight history recorded in the flightalert tracker log on a 3D globe.

    python trackmap.py                       # reads ../flightalert/tracker.log, writes tracker_map.html
    python trackmap.py --log other.log --out tracks.html
    python trackmap.py --since 14            # only the last 14 days
    python trackmap.py --tracks              # also draw every airborne position

The log is only read, never changed. Each "registration='N..' ... lat= lon="
line the tracker logged is a position. Positions on the ground are collapsed to one
dot per stop, labeled with the nearest airport, and each flight is a smooth arc from
the stop where it took off to the next stop. An aircraft that is still airborne gets
an arc to its last position. With --tracks the airborne positions are drawn too, so
the lines follow the track the tracker saw. The page is the same one `report.py --map`
makes, so the controls are the same.
"""
import argparse
import calendar
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import adsb
import flightmap
import report
import targets as store
from adsb import airport_locations

DEFAULT_LOG = Path(__file__).resolve().parent.parent / "flightalert" / "tracker.log"   # the one thing read from that project
GROUND_STOP_NM = 2   # a ground position farther than this from the last one is a new stop

POSITION = re.compile(r"^(\S+) DEBUG registration='([^']+)' icao='(\w+)' alt_baro=(\S+) .*?"
                      r"lat=(-?[\d.]+) lon=(-?[\d.]+)")


def parse_time(text):
    """'2026-08-06T07:44:17-05:00' or '...-0500' -> epoch seconds."""
    return int(datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z").timestamp())


def read_log(path):
    """{registration: (icao, [(epoch, lat, lon, on_ground), ...])} in time order,
    leaving out consecutive repeats of the same position."""
    found = {}
    with open(path, encoding="utf-8", errors="replace") as f:   # read-only
        for line in f:
            m = POSITION.match(line)
            if not m:
                continue
            stamp, tail, icao, alt, lat, lon = m.groups()
            icao_, rows = found.setdefault(tail, (icao.lower(), []))
            row = (parse_time(stamp), float(lat), float(lon), alt.strip("'\"") == "ground")
            if not rows or rows[-1][1:] != row[1:]:
                rows.append(row)
    return found


def moves_for(rows, tracks=False):
    """Turn raw positions into the globe's moves: one per stop on the ground, and
    the airborne positions only if tracks is set (or the last one, if the aircraft
    is still in the air)."""
    out, last_ground = [], None
    for t, lat, lon, ground in sorted(rows):
        if ground:
            if last_ground and adsb.airports.haversine_nm(last_ground[0], last_ground[1], lat, lon) <= GROUND_STOP_NM:
                continue
            last_ground = (lat, lon)
            near = adsb.find_nearest_airport(lat, lon, None)   # slow, so only for stops
            airport = ""
            if near.status == adsb.AirportSearchStatus.FOUND and near.distance_nm <= adsb.MAX_AIRPORT_NM:
                airport = near.airport.ident
            if not airport:
                # "ground" with no airport nearby: a transponder still in ground mode, not a stop
                last_ground = None
                if tracks:
                    out.append({"t": t, "airport": "", "lat": lat, "lon": lon, "airborne": True, "quiet": True})
                continue
            out.append({"t": t, "airport": airport, "lat": lat, "lon": lon, "airborne": False})
        else:
            last_ground = None
            if tracks:
                out.append({"t": t, "airport": "", "lat": lat, "lon": lon, "airborne": True, "quiet": True})
    if rows and not tracks and last_ground is None:   # the last position wasn't at a stop
        t, lat, lon, _ = sorted(rows)[-1]
        out.append({"t": t, "airport": "", "lat": lat, "lon": lon, "airborne": True})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--log", type=Path, default=DEFAULT_LOG, help=f"tracker log (default {DEFAULT_LOG})")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "tracker_map.html",
                    help="HTML file to write (default tracker_map.html)")
    ap.add_argument("--tracks", action="store_true", help="draw every airborne position, not just takeoff and landing")
    ap.add_argument("--since", type=float, metavar="DAYS", help="only positions from the last DAYS days")
    args = ap.parse_args()

    if not args.log.exists():
        raise SystemExit(f"{args.log} not found")
    adsb.load_airports(str(adsb.AIRPORTS_CSV))
    found = read_log(args.log)
    if not found:
        raise SystemExit(f"no positions in {args.log}")
    cutoff = time.time() - args.since * 86400 if args.since else 0
    cands = store.load_candidates()
    part135 = report.load_part135()

    aircraft = []
    for tail, (icao, rows) in sorted(found.items()):
        moves = moves_for([r for r in rows if r[0] >= cutoff], args.tracks)
        if not moves:
            continue
        places = airport_locations({m["airport"] for m in moves})
        details = flightmap.airport_details({m["airport"] for m in moves})
        for m in moves:
            m["place"] = places.get(m["airport"], "")
            if m["airport"] in details:
                d = details[m["airport"]]
                m["name"], m["lat"], m["lon"] = d["name"], d["lat"], d["lon"]
        c = cands.get(icao, {})
        aircraft.append({"tail": tail, "icao": icao, "model": c.get("model", ""), "owner": c.get("owner", ""),
                         "rules": report.operating_rules(tail, part135) or "", "moves": moves})
        stops = [m for m in moves if not m["airborne"]]
        first, last = (datetime.fromtimestamp(moves[0]["t"], timezone.utc), datetime.fromtimestamp(moves[-1]["t"], timezone.utc))
        print(f"{tail}: {len(rows):,} positions, {len(stops)} ground stops, "
              f"{len(moves) - len(stops):,} airborne, {first:%Y-%m-%d} to {last:%Y-%m-%d}")

    flightmap.write_map(args.out, aircraft, title="Tracker log",
                        note=f"From {args.log.name}. Each arc runs from where a flight took off to where it landed "
                             "(not the exact path). Drag to rotate, scroll to zoom.")
    print(f"globe written to {args.out}")


if __name__ == "__main__":
    main()
