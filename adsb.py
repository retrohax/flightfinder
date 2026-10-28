"""Helpers for reading ADS-B Exchange API responses, shared by watch.py and finder.py."""
import csv
from datetime import datetime, timezone
from pathlib import Path

import airports
from airports import AirportSearchStatus, find_nearest_airport, load_airports  # noqa: F401

RAPIDAPI_HOST = "adsbexchange-com1.p.rapidapi.com"
RAPIDAPI_BASE_URL = "https://adsbexchange-com1.p.rapidapi.com/v2"
AIRPORTS_CSV = Path(__file__).resolve().parent / "airports.csv"   # update_data.py downloads it
MAX_AIRPORT_NM = 25  # farther than this and the airport is left blank


def iso(ts):
    """Epoch seconds -> '2026-10-21T08:15:00Z'."""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def airborne_flag(altitude):
    """'no' if the aircraft reports being on the ground, 'yes' if it reports an
    altitude, '' if it reports neither."""
    if altitude == "ground":
        return "no"
    return "yes" if isinstance(altitude, (int, float)) else ""


def aircraft_list(payload):
    """Aircraft objects in a response. /hex/ returns the aircraft itself; the
    area endpoints wrap them as {"ac": [...]}."""
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("ac"), list):
        return payload["ac"]
    return [payload] if payload.get("hex") else []


def airport_position(ident):
    """(lat, lon, name) of an airport in airports.csv, or None. Call load_airports first."""
    ident = ident.strip().upper()
    for a in airports._airports:
        if a.ident == ident:
            return a.lat, a.lon, a.name or ident
    return None


def airport_locations(idents):
    """{ident: 'San Jose, US'}: the municipality and country from airports.csv
    (the airport name if there's no municipality)."""
    wanted = {i.upper() for i in idents if i}
    places = {}
    if not wanted:
        return places
    with open(AIRPORTS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ident = (row.get("ident") or "").upper()
            if ident in wanted:
                place = ", ".join(p for p in (row.get("municipality"), row.get("iso_country")) if p)
                places[ident] = place or row.get("name") or "unknown location"
    return places


def parse_position(payload, ac, fallback_time):
    """The position in one aircraft object, or None if it has none.

    Returns a dict: lat, lon, altitude (feet, "ground", or ""), agl (feet above the
    nearest airport's field elevation, or None if there's no altitude), airborne ("yes",
    "no" or "" if unknown; airborne means the airport is only the nearest one,
    not where the aircraft is), position_time
    (epoch seconds when the position was received), airport (nearest within
    MAX_AIRPORT_NM, else ""), precision ("exact", or "approx" for the rounded
    rr_lat/rr_lon) and source ("direct", "nested" or "rr").
    """
    pos, source, lat_key, lon_key = ac, "direct", "lat", "lon"
    if ac.get("lat") is None or ac.get("lon") is None:
        last = ac.get("lastPosition")
        if isinstance(last, dict) and last.get("lat") is not None and last.get("lon") is not None:
            pos, source = last, "nested"
        elif ac.get("rr_lat") is not None and ac.get("rr_lon") is not None:
            lat_key, lon_key, source = "rr_lat", "rr_lon", "rr"
        else:
            return None
    try:
        lat, lon = float(pos[lat_key]), float(pos[lon_key])
    except (TypeError, ValueError):
        return None

    alt = pos.get("alt_baro", ac.get("alt_baro"))
    now_ms = ac.get("now", payload.get("now"))
    seen_pos = pos.get("seen_pos", ac.get("seen_pos", ac.get("seen")))
    if isinstance(now_ms, (int, float)):
        stamp = now_ms / 1000 - (seen_pos if isinstance(seen_pos, (int, float)) else 0)
    else:
        stamp = fallback_time

    near = find_nearest_airport(lat, lon, alt if isinstance(alt, int) else None)
    agl = near.altitude_agl if near.status == AirportSearchStatus.FOUND else None
    airport = ""
    if near.status == AirportSearchStatus.FOUND and near.distance_nm <= MAX_AIRPORT_NM:
        airport = near.airport.ident
    return {"lat": round(lat, 5), "lon": round(lon, 5), "altitude": alt if alt is not None else "",
            "airborne": airborne_flag(alt), "agl": agl, "position_time": int(stamp), "airport": airport,
            "precision": "approx" if source == "rr" else "exact", "source": source}
