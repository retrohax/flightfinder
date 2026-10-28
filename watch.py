#!/usr/bin/env python3
"""Watch the airspace around an airport and build targets.csv.

Every --interval minutes, ask ADS-B Exchange for all aircraft within --radius
nm (default 15) of the airport (an ICAO code from airports.csv), below
--max-alt (default 6,000 ft above ground). Any aircraft that is in candidates.csv
is added to targets.csv (or has its last_seen updated), and its position is
recorded in history.csv.

    python watch.py ICAO                # poll every 15 minutes until stopped
    python watch.py ICAO --once         # one query, then exit (e.g. for cron)
    python watch.py ICAO --once --raw   # also print the raw response
    python watch.py ICAO --max-alt 4000      # ignore aircraft above 4,000 ft AGL (default 6,000; 0 = no limit)
    python watch.py --list              # show the targets
    python watch.py --remove TAIL1 TAIL2     # drop targets (they're added again if seen)
    python watch.py --ignore TAIL       # drop a target and never add it again
    python watch.py --unignore TAIL     # allow an ignored aircraft back in
    python watch.py --purge 60          # drop targets not seen for 60 days

Requires RAPIDAPI_KEY in the environment. One query per cycle: at 15 minutes
that's about 2,900 a month.
"""
import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

import adsb
import targets as store
from rapidapi import rapidapi_area_request

MAX_CONSECUTIVE_ERRORS = 5


def process(payload, cands, tgts, ignored, latest, now, max_alt=None):
    """Fold one area response into the targets. Returns (aircraft_seen, matched,
    new_targets, history_rows, too_high); tgts and latest are updated in place.
    A candidate more than max_alt feet above ground level (if given) is counted in
    too_high and otherwise ignored; one with no altitude is kept. Height above
    ground is the barometric altitude minus the field elevation of the nearest
    airport in airports.csv (the raw altitude if there is no position)."""
    seen = matched = too_high = 0
    new, rows = [], []
    for ac in adsb.aircraft_list(payload):
        seen += 1
        h = (ac.get("hex") or "").strip().lower()
        c = cands.get(h)
        if c is None or h in ignored:
            continue
        pos = adsb.parse_position(payload, ac, now)
        alt = ac.get("alt_baro")
        height = pos["agl"] if pos and pos["agl"] is not None else alt
        if max_alt is not None and isinstance(height, (int, float)) and height > max_alt:
            too_high += 1
            continue
        matched += 1
        t = tgts.get(h)
        if t is None:
            t = tgts[h] = {"tail": c["tail"], "icao": h, "model": c["model"], "owner": c["owner"],
                           "first_seen": adsb.iso(now), "last_seen": adsb.iso(now), "sightings": "0"}
            new.append(t)
        t["last_seen"] = adsb.iso(now)
        t["sightings"] = str(int(t["sightings"]) + 1)

        if pos is None:
            continue
        stamp = adsb.iso(pos["position_time"])
        if stamp <= latest.get(h, ""):
            continue
        latest[h] = stamp
        rows.append({"tail": c["tail"], "icao": h, "model": c["model"], "position_time": stamp,
                     "airport": pos["airport"], "altitude": pos["altitude"],
                     "airborne": pos["airborne"], "lat": pos["lat"], "lon": pos["lon"], "precision": pos["precision"], "source": pos["source"],
                     "queried": adsb.iso(now)})
    return seen, matched, new, rows, too_high


def manage(args):
    """--list / --remove / --unignore / --purge. These need no API key."""
    tgts, ignored = store.load_targets(), store.load_ignored()
    by_tail = {r["tail"]: h for h, r in {**ignored, **tgts}.items()}

    if args.unignore:
        for tail in args.unignore:
            h = by_tail.get(tail.upper())
            if h in ignored:
                del ignored[h]
                print(f"{tail.upper()}: no longer ignored")
            else:
                print(f"{tail.upper()}: not in the ignore list")
        store.save_ignored(ignored)
    if args.remove:
        for tail in args.remove:
            tail = tail.upper()
            h = next((h for h, r in tgts.items() if r["tail"] == tail), None)
            if h is None:
                print(f"{tail}: not a target")
                continue
            del tgts[h]
            print(f"{tail}: removed")
        store.save_targets(tgts)
    if args.ignore:
        tail_to_hex = {c["tail"]: h for h, c in store.load_candidates().items()}
        for tail in args.ignore:
            tail = tail.upper()
            h = by_tail.get(tail) or tail_to_hex.get(tail)
            if h is None:
                print(f"{tail}: not a target or a candidate")
                continue
            was_target = tgts.pop(h, None) is not None
            ignored[h] = {"tail": tail, "icao": h, "removed": adsb.iso(time.time())}
            print(f"{tail}: {'removed and ' if was_target else ''}ignored")
        store.save_targets(tgts)
        store.save_ignored(ignored)
    if args.purge is not None:
        cutoff = adsb.iso(time.time() - args.purge * 86400)
        old = [h for h, r in tgts.items() if r["last_seen"] < cutoff]
        for h in old:
            print(f"{tgts[h]['tail']}: last seen {tgts[h]['last_seen']}; purged")
            del tgts[h]
        store.save_targets(tgts)
        print(f"{len(old)} purged, {len(tgts)} targets left")
    if args.list:
        print(f"{len(tgts)} targets ({len(ignored)} ignored)\n")
        print(f"{'Tail':<8} {'Model':<26} {'First seen':<21} {'Last seen':<21} {'Hits':>5}  Registrant")
        for r in sorted(tgts.values(), key=lambda r: r["last_seen"], reverse=True):
            print(f"{r['tail']:<8} {r['model']:<26} {r['first_seen']:<21} {r['last_seen']:<21} "
                  f"{r['sightings']:>5}  {r['owner'] or '(none on file)'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--interval", type=float, default=15, metavar="MIN", help="minutes between queries")
    ap.add_argument("--radius", type=int, default=15, metavar="NM", help="search radius, default 15, max 250")
    ap.add_argument("--max-alt", type=int, default=6000, metavar="FEET",
                    help="skip aircraft more than this many feet above ground level "
                    "(default 6000; 0 = no limit)")
    ap.add_argument("airport", nargs="?", metavar="ICAO", help="ICAO code of the airport to watch")
    ap.add_argument("--lat", type=float, help="centre on this latitude instead of the airport's")
    ap.add_argument("--lon", type=float, help="centre on this longitude instead of the airport's")
    ap.add_argument("--once", action="store_true", help="query once and exit")
    ap.add_argument("--raw", action="store_true", help="print each raw API response")
    ap.add_argument("--list", action="store_true", help="print the targets and exit")
    ap.add_argument("--remove", nargs="+", metavar="TAIL", help="remove targets (they can be added again)")
    ap.add_argument("--ignore", nargs="+", metavar="TAIL",
                    help="remove targets and never add them again")
    ap.add_argument("--unignore", nargs="+", metavar="TAIL", help="let an ignored aircraft be added again")
    ap.add_argument("--purge", type=float, metavar="DAYS", help="remove targets last seen more than DAYS ago")
    args = ap.parse_args()

    if args.list or args.remove or args.ignore or args.unignore or args.purge is not None:
        manage(args)
        return
    if not args.airport and (args.lat is None or args.lon is None):
        ap.error("give an airport's ICAO code, or both --lat and --lon")
    if not 1 <= args.radius <= 250:
        sys.exit("--radius must be 1-250 nm")
    api_key = os.getenv("RAPIDAPI_KEY")
    if not api_key:
        sys.exit("RAPIDAPI_KEY is not set")

    adsb.load_airports(str(adsb.AIRPORTS_CSV))
    if args.airport:
        found = adsb.airport_position(args.airport)
        if not found:
            sys.exit(f"{args.airport.upper()} is not in airports.csv")
        args.lat = found[0] if args.lat is None else args.lat
        args.lon = found[1] if args.lon is None else args.lon
        print(f"watching {args.airport.upper()} ({found[2]})")
    cands = store.load_candidates()
    tgts, ignored, latest = store.load_targets(), store.load_ignored(), store.latest_position_times()
    print(f"{len(cands):,} candidates, {len(tgts)} targets, {len(ignored)} ignored; "
          f"{args.radius} nm around {args.lat},{args.lon} below "
          + (f"{args.max_alt:,} ft AGL" if args.max_alt else "any altitude") + f", every {args.interval:g} min")

    errors, next_run = 0, time.monotonic()
    while True:
        now = time.time()
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d %H:%MZ")
        payload = rapidapi_area_request(adsb.RAPIDAPI_BASE_URL, adsb.RAPIDAPI_HOST, api_key,
                                        args.lat, args.lon, args.radius)
        if payload is None:
            errors += 1
            print(f"{stamp}  ERROR (no response; see log line above)")
            if args.once:
                sys.exit(1)   # so cron's log shows the failure
            if errors >= MAX_CONSECUTIVE_ERRORS:
                sys.exit(f"stopping: {errors} errors in a row (rate limit or quota?)")
        else:
            errors = 0
            if args.raw:
                print(json.dumps(payload))
            seen, matched, new, rows, high = process(payload, cands, tgts, ignored, latest, now,
                                                     args.max_alt or None)
            store.save_targets(tgts)
            store.append_history(rows)
            print(f"{stamp}  {seen} aircraft, {matched} candidates ({high} too high), {len(new)} new "
                  f"targets ({len(tgts)} total), {len(rows)} positions recorded")
            for t in new:
                print(f"    NEW {t['tail']:<8} {t['model']}  {t['owner'] or '(none on file)'}")
        if args.once:
            return
        next_run += args.interval * 60
        time.sleep(max(0.0, next_run - time.monotonic()))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
