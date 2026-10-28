"""Write the flight-history globe (a single HTML file) for report.py --map.

The page is built from map_assets/: map_template.html plus three.js, topojson and
a world outline, all inlined so the file works offline.
"""
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import adsb

ASSETS = Path(__file__).resolve().parent / "map_assets"


def airport_details(idents):
    """{ident: {"lat", "lon", "name"}} from airports.csv. The name is the locality:
    "Dallas, TX" (state for the US, Australia and Canada), "Paris, FR" elsewhere,
    or the airport's own name if it has no municipality."""
    wanted = {i.upper() for i in idents if i}
    found = {}
    if not wanted:
        return found
    with open(adsb.AIRPORTS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ident = (row.get("ident") or "").upper()
            if ident not in wanted:
                continue
            try:
                lat, lon = float(row["latitude_deg"]), float(row["longitude_deg"])
            except (TypeError, ValueError):
                continue
            country, region = row.get("iso_country") or "", row.get("iso_region") or ""
            area = region.split("-", 1)[-1] if country in ("US", "AU", "CA") and "-" in region else country
            city = re.split(r"[,(]", row.get("municipality") or "")[0].strip()   # "Dallas, Texas" -> "Dallas"
            name = ", ".join(p for p in (city, area) if p) if city else (row.get("name") or ident)
            found[ident] = {"lat": lat, "lon": lon, "name": name}
    return found


def _js(text):
    """Make text safe to put inside a <script> element."""
    return text.replace("</", "<\\/")


def write_map(path, aircraft, title=None, note=None):
    """aircraft: [{tail, icao, model, owner, rules, moves: [{t, airport, place, lat, lon, airborne}]}]
    A move with "quiet": true is drawn but left out of the aircraft's movement list.
    title and note replace the page's heading and the line under it."""
    data = {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%MZ"), "aircraft": aircraft,
            "title": title, "note": note}
    html = (ASSETS / "map_template.html").read_text(encoding="utf-8")
    parts = {
        "/*THREE*/": (ASSETS / "three.r128.min.js").read_text(encoding="utf-8"),
        "/*TOPOJSON*/": (ASSETS / "topojson-client.3.1.0.min.js").read_text(encoding="utf-8"),
        "/*WORLD*/": (ASSETS / "countries-110m.json").read_text(encoding="utf-8"),
        "/*DATA*/": json.dumps(data, separators=(",", ":")),
    }
    for marker, text in parts.items():
        html = html.replace(marker, _js(text), 1)
    Path(path).write_text(html, encoding="utf-8")
