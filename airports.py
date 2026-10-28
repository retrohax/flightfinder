"""Airport lookup from airports.csv (OurAirports data, downloaded by update_data.py).

Nearest-airport search by great-circle distance, with the aircraft's height above
that airport's field elevation. Adapted from the flightalert project's
airports.py so this folder doesn't depend on it.
"""
import csv
import io
import logging
import math
from dataclasses import dataclass
from enum import Enum

AIRPORT_TYPES = {"large_airport", "medium_airport", "small_airport"}


@dataclass(frozen=True)
class AirportInfo:
    ident: str
    name: str | None
    location: str
    lat: float
    lon: float
    elevation_ft: int


class AirportSearchStatus(str, Enum):
    SKIPPED = "skipped"
    NOT_FOUND = "not_found"
    FOUND = "found"


@dataclass(frozen=True)
class AirportSearchResult:
    status: AirportSearchStatus
    airport: AirportInfo | None = None
    distance_nm: float | None = None
    altitude_agl: int | None = None


_airports = []


def load_airports(airports_csv_path):
    global _airports
    _airports = []

    try:
        with open(airports_csv_path, "r", encoding="utf-8") as f:
            csv_text = f.read()
    except FileNotFoundError:
        raise SystemExit(f"{airports_csv_path} not found; run update_data.py to download it")

    for row in csv.DictReader(io.StringIO(csv_text)):
        if row.get("type", "unknown") not in AIRPORT_TYPES:
            continue

        ident_raw = row.get("ident")
        if not isinstance(ident_raw, str) or not ident_raw.strip():
            continue

        try:
            ident = ident_raw.strip().upper()
            elevation_ft = int(row.get("elevation_ft"))
            lat = float(row.get("latitude_deg"))
            lon = float(row.get("longitude_deg"))
        except (TypeError, ValueError):
            continue

        municipality = row.get("municipality") if isinstance(row.get("municipality"), str) else ""
        country = row.get("iso_country") if isinstance(row.get("iso_country"), str) else ""
        location = ", ".join(part for part in [municipality, country] if part) or "unknown location"

        _airports.append(AirportInfo(ident=ident, name=row.get("name"), location=location,
                                     lat=lat, lon=lon, elevation_ft=elevation_ft))

    logging.debug("Loaded %d airports", len(_airports))


def haversine_nm(lat1, lon1, lat2, lon2):
    """Great-circle distance in nautical miles."""
    r = 3440.065
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def find_nearest_airport(lat, lon, altitude):
    """The closest airport to a position. altitude is feet (an int) or anything
    else for unknown; altitude_agl is that minus the airport's field elevation."""
    if lat is None or lon is None:
        return AirportSearchResult(status=AirportSearchStatus.SKIPPED)

    closest = None
    closest_nm = None
    for airport in _airports:
        distance_nm = haversine_nm(lat, lon, airport.lat, airport.lon)
        if closest_nm is None or distance_nm < closest_nm:
            closest = airport
            closest_nm = distance_nm

    if closest is None:
        return AirportSearchResult(status=AirportSearchStatus.NOT_FOUND)

    altitude_agl = altitude - closest.elevation_ft if isinstance(altitude, int) else None
    return AirportSearchResult(status=AirportSearchStatus.FOUND, airport=closest,
                               distance_nm=closest_nm, altitude_agl=altitude_agl)
