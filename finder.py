#!/usr/bin/env python3
"""Look up the last known position of every aircraft in targets.csv (built by
watch.py) and add any new position to history.csv.

    python finder.py                 # query every target
    python finder.py TAIL            # test one aircraft: query and print, write nothing
    python finder.py --days 7        # only targets seen near the airport in the last 7 days (default 30)
    python finder.py --limit 5       # only the first 5 targets (testing)
    python finder.py --raw           # also print each raw API response

Targets not seen by watch.py in the last 30 days are skipped.

Requires RAPIDAPI_KEY in the environment. One query per target.
"""
import argparse
import calendar
import json
import logging
import os
import sys
import time

import adsb
import targets as store
from rapidapi import rapidapi_request

MAX_CONSECUTIVE_ERRORS = 5


def age_text(position_time, now):
    hours = (now - position_time) / 3600
    return f"{hours:.1f}h ago" if hours >= 1 else f"{(now - position_time) / 60:.0f}m ago"


def look_up(t, api_key, now, latest, raw):
    """Query one target. Returns (status, history_row_or_None); status is 'new',
    'same' (nothing newer than what's recorded), 'none' (no position) or 'error'."""
    payload = rapidapi_request(adsb.RAPIDAPI_BASE_URL, adsb.RAPIDAPI_HOST, api_key, t["icao"])
    if payload is None:
        return "error", None
    if raw:
        print(json.dumps(payload))
    acs = adsb.aircraft_list(payload)
    pos = adsb.parse_position(payload, acs[0], now) if acs else None
    if pos is None:
        return "none", None
    stamp = adsb.iso(pos["position_time"])
    row = {"tail": t["tail"], "icao": t["icao"], "model": t["model"], "position_time": stamp,
           "airport": pos["airport"], "altitude": pos["altitude"],
           "airborne": pos["airborne"], "lat": pos["lat"], "lon": pos["lon"], "precision": pos["precision"], "source": pos["source"],
           "queried": adsb.iso(now)}
    if stamp <= latest.get(t["icao"], ""):
        return "same", row
    return "new", row


def describe(row, now):
    pos_time = calendar.timegm(time.strptime(row["position_time"], "%Y-%m-%dT%H:%M:%SZ"))
    where = row["airport"] or "no airport within 25nm"
    if row["airport"] and row["airborne"] == "yes":
        where = "near " + where
    return (f"{where:<14} {'AIRBORNE' if row['airborne'] == 'yes' else 'ground' if row['airborne'] == 'no' else '?':<8} "
            f"alt={row['altitude'] or '?'} "
            f"{row['lat']},{row['lon']} {row['precision']} {age_text(pos_time, now)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tail", nargs="?", help="test mode: query just this aircraft, write nothing")
    ap.add_argument("--delay", type=float, default=0.25, help="seconds between API calls")
    ap.add_argument("--days", type=float, default=30,
                    help="only look up targets seen by watch.py in the last DAYS days (default 30)")
    ap.add_argument("--limit", type=int, default=0, help="only look up the first N targets")
    ap.add_argument("--raw", action="store_true", help="print each raw API response")
    args = ap.parse_args()

    api_key = os.getenv("RAPIDAPI_KEY")
    if not api_key:
        sys.exit("RAPIDAPI_KEY is not set")
    adsb.load_airports(str(adsb.AIRPORTS_CSV))
    latest = store.latest_position_times()

    if args.tail:
        tail = args.tail.strip().upper()
        t = next((r for r in store.load_targets().values() if r["tail"] == tail), None)
        if t is None:
            c = next((c for c in store.load_candidates().values() if c["tail"] == tail), None)
            if c is None:
                sys.exit(f"{tail}: not in targets.csv or candidates.csv")
            t = {"tail": c["tail"], "icao": c["icao"].lower(), "model": c["model"]}
        status, row = look_up(t, api_key, time.time(), {}, args.raw)
        print(f"{tail} {t['icao']}  " + (describe(row, time.time()) if row else status))
        return

    tgts = sorted(store.load_targets().values(), key=lambda r: r["tail"])
    if not tgts:
        sys.exit("targets.csv is empty or missing; run watch.py first")
    cutoff = adsb.iso(time.time() - args.days * 86400)
    total = len(tgts)
    tgts = [t for t in tgts if t["last_seen"] >= cutoff]
    if args.limit:
        tgts = tgts[:args.limit]
    print(f"{len(tgts)} targets to look up"
          + (f" ({total - len(tgts)} not seen in {args.days:g} days skipped)" if len(tgts) < total and not args.limit else ""))

    counts, consecutive = {}, 0
    for i, t in enumerate(tgts, 1):
        now = time.time()
        status, row = look_up(t, api_key, now, latest, args.raw)
        counts[status] = counts.get(status, 0) + 1
        if status == "error":
            consecutive += 1
            print(f"[{i}/{len(tgts)}] {t['tail']} {t['icao']}  ERROR (see log line above)")
            if consecutive >= MAX_CONSECUTIVE_ERRORS:
                print(f"\nstopping: {consecutive} errors in a row (rate limit or quota?)")
                break
        else:
            consecutive = 0
            label = {"new": "NEW ", "same": "same", "none": "none"}[status]
            print(f"[{i}/{len(tgts)}] {t['tail']:<8} {label}  " + (describe(row, now) if row else "no position"))
            if status == "new":
                store.append_history([row])  # one row at a time, so an interrupted run loses nothing
                latest[t["icao"]] = row["position_time"]
        time.sleep(args.delay)

    print("\n" + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
