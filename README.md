# flightfinder

A searchable copy of the FAA aircraft registry, plus a small system for finding
and tracking long-range business jets that operate from a given airport. It uses
public data only: the FAA Releasable Aircraft Database, ADS-B Exchange, and the
FAA's Part 135 operator list.

## Routine maintenance

Three build scripts turn outside data into the files everything else reads.
`python3 update_data.py` does all of it in one go (download the zip, then steps 1 to 3),
and `cron_ctl.sh` runs it weekly (see "Running everything from cron"). By hand,
re-run them in this order:

| Step | Command | What it refreshes | How often |
|---|---|---|---|
| 1 | Download `ReleasableAircraft.zip` from the FAA's Releasable Aircraft Database page into this folder, then `python3 build_db.py --zip` | `faa.db`: registrants, models, Mode S hex codes | About monthly, and after any big registry change |
| 2 | `python3 build_candidates.py --refresh` | `candidates.csv`: the aircraft that `watch.py` matches against. `--refresh` also re-downloads the ADS-B Exchange database, for the VH- jets and the hex codes | After step 1 |
| 3 | `python3 build_part135.py` | `part135.csv`: the FAA's Part 135 list, which `report.py` uses to mark Part 135 aircraft | About weekly (the FAA updates it about weekly) |

- **Steps 1 and 2 go together.** Step 2 reads `faa.db`, so a new registry download
  only matters once `build_candidates.py` has been re-run.
- **Step 3 stands alone.** It doesn't depend on the others.
- **`watch.py` picks up the new list on its own when run from cron** (each run
  starts fresh). If you run it in a terminal loop instead, restart it: that mode
  reads `candidates.csv` once at startup.
- **Your targets survive.** `targets.csv`, `ignored.csv` and `history.csv` are
  never touched by the build scripts. An aircraft that has left the candidate list
  (for example, deregistered) stays in `targets.csv` until you remove it.
- **Changing what counts as a candidate:** edit `FAA_MODELS` or `ICAO_TYPES` in
  `build_candidates.py` and run step 2 (no need to redo step 1).
- **None of these use RapidAPI queries.** Only `watch.py` and `finder.py` do.

## The pipeline

```
FAA registry files ──build_db.py──▶ faa.db ──┐
                                              ├─ build_candidates.py ─▶ candidates.csv
ADS-B Exchange aircraft db ───────────────────┘                              │
                                                                             ▼
          watch.py  (polls the airspace around an airport every 15 minutes)
              │  any candidate seen there becomes a target
              ▼
          targets.csv ──▶ finder.py (last known position of each target)
              │                         │
              └──── positions seen ─────┴──▶ history.csv ──▶ report.py
```

1. **Registry**: load the FAA files into SQLite and query them from Python.
2. **Candidates**: one list of every aircraft that could plausibly do the trip.
3. **Watch**: poll the area around an airport; candidates that show up become targets.
4. **Follow**: look up each target's last position and keep a history.
5. **Report**: print each target's movements. You decide which ones look wrong
   and remove them.

## Setup

- Python 3 with the standard library only.
- Nothing is borrowed from other projects, except that `trackmap.py` reads
  `../flightalert/tracker.log` (read-only; `--log FILE` points it elsewhere).
  `airports.py` (nearest-airport lookup) is adapted from the flightalert project
  and lives here; `airports.csv` (OurAirports data) is downloaded by
  `python3 update_data.py` (`--airports-only` for just that file).
- `RAPIDAPI_KEY` must be set for anything that calls ADS-B Exchange
  (`watch.py`, `finder.py`). The host is
  `adsbexchange-com1.p.rapidapi.com`. The plan allows 10,000 queries a month.
- Run `python3 update_data.py` once to download `airports.csv` and the FAA
  registry zip and build everything (or put `ReleasableAircraft.zip`, or the
  extracted `.txt` files, in this folder yourself).

## 1. The registry database

```
python3 build_db.py --zip    # reads ReleasableAircraft.zip (update_data.py downloads it)
python3 build_db.py          # or reads loose MASTER.txt, ACFTREF.txt, ... files if you've extracted them
```

This builds `faa.db`, with one table per FAA file (`master`, `acftref`,
`engine`, `dereg`, `reserved`, `dealer`, `docindex`), a `v_aircraft` view that
joins the registration to its model and engine, and a full-text index on owner
names. Column names are snake_case, and empty values are NULL. N-numbers are
stored without the "N". The new database is built in a temporary file and then
swapped in, so a failed run leaves the old one alone.

`faa.py` queries it (read-only):

```python
import faa
faa.owner_of("<N-number>")                # current registration; falls back to the deregistered file
faa.history("<N-number>")                 # current record plus earlier deregistrations
faa.find_models("bombardier", "700")      # model codes and how many are registered
faa.owners_of_model(code="3980203")       # every aircraft of a model (or mfr=/model=)
faa.search_owner("flexjet")               # aircraft by owner name
faa.by_serial("5139")                     # by manufacturer serial number
```

**Limits of the registry.**

- The registrant is the legal owner on file. Many jets are registered to an
  owner trust (TVPX, Bank of Utah, Wilmington Trust) or a bank, whose
  beneficiary the FAA doesn't record.
- Blank names (about 270 among the jets) are owners that asked for privacy, or
  records still pending.
- Registrations are tied to US citizenship, so a foreign owner usually
  appears behind a trustee. A trustee registration is about as common on
  charter jets as on private ones, so it says little about use.

## 2. The candidate list

```
python3 build_candidates.py                   # N-numbers + VH- (Australia)
python3 build_candidates.py --prefix VH- --prefix ZK-
python3 build_candidates.py --refresh         # re-download the ADS-B Exchange database
```

`candidates.csv` is the single list everything else uses (2,509 rows when it
was last built). A jet qualifies if it has the range for the long-haul trips you
care about (with at most one stop) and can carry a VIP plus up to 10 people:

- **US aircraft** come from the FAA registry, selected by the FAA model codes
  in `FAA_MODELS` at the top of `build_candidates.py`. This list includes
  the borderline types (Boeing BBJ, Airbus ACJ319, Embraer Lineage 1000,
  Falcon 900LX). It catches jets that ADS-B Exchange has no type code for.
- **Non-US aircraft** come from the ADS-B Exchange database, filtered by ICAO
  type and by registration prefix. The default is VH- only. N prefixes are
  rejected, because those always come from the FAA.

Columns: `tail, icao, model, registry_model, year, owner, source`. The `icao`
column is the aircraft's Mode S hex code, which is what the API is queried with.

To change what counts as a candidate, edit `FAA_MODELS` (FAA) or `ICAO_TYPES`
(everything else) and re-run.

## 3. Watching an airport: `watch.py`

```
python3 watch.py ICAO             # poll every 15 minutes until stopped (ICAO = the airport's code)
python3 watch.py ICAO --once --raw  # one query; print the raw response (try this first)
python3 watch.py ICAO --radius 25  # a different radius (default 15 nm, max 250)
python3 watch.py ICAO --max-alt 4000  # skip aircraft above 4,000 ft AGL (default 6,000; 0 = no limit)
python3 watch.py ICAO --interval 30
```

Each cycle asks ADS-B Exchange for every aircraft within the radius of the airport.
Candidates more than `--max-alt` feet above ground level are skipped
completely: not added as targets and not recorded, since they're too high to be
arriving or departing. Height above ground is the barometric altitude minus the
field elevation (`airports.csv`) of the nearest airport. An aircraft that
reports no altitude is kept.
Aircraft that are in `candidates.csv` are added to `targets.csv`, or have their
`last_seen` time and sighting count updated. Their positions also go into
`history.csv`, at no extra cost. One query per cycle is about 2,900 a month at 15
minutes.

### Running everything from cron

`watch.py` keeps nothing in memory between cycles that isn't in the CSV files, so
`--once` from cron behaves the same as the loop. (Cron is slightly safer: a
`--remove` you make between runs can't be overwritten by a loop holding an old copy.)
`cron_ctl.sh` runs the whole system this way:

| Job | When (local time) | What it runs | Log |
|---|---|---|---|
| `watch` | every 15 minutes | `watch.py ICAO --once` | `watch.log` |
| `daily` | 04:30 | `finder.py`, then `report.py --map flights.html` (text report to `report.txt`), then `trackmap.py` | `daily.log` |
| `weekly` | Sunday 03:00 | `update_data.py`: downloads `airports.csv` and the FAA registry zip, rebuilds `faa.db`, refreshes `candidates.csv` and `part135.csv` | `weekly.log` |

```
echo 'RAPIDAPI_KEY=your_key' > cron.env      # cron doesn't see your shell's environment
./cron_ctl.sh start ICAO                     # install the jobs, watching that airport
./cron_ctl.sh status                         # installed? last lines of each log
./cron_ctl.sh stop                           # remove them
./cron_ctl.sh run daily                      # run one job now, as cron would (watch | daily | weekly)
```

`start` writes `cron.crontab` here and adds it to your crontab between
`BEGIN`/`END flightfinder` lines; `stop` removes only that block and leaves your
other entries alone. Change the schedules at the top of `cron_ctl.sh` and run
`start` again. `start` records the `python3` it finds, so cron uses the same one.

- A `watch` run is skipped if the previous one is still going, and a failed query
  makes it exit with an error. `daily` and `weekly` take turns (they wait for each
  other), so a long rebuild never overlaps the daily reports.
- `update_data.py` downloads to a temporary file and keeps the old copy unless
  the new one is complete and valid. The build scripts swap files in whole, so
  `watch.py` never reads a half-written `candidates.csv`.
- `daily` uses about 15 API queries (one per target); `watch` about 2,900 a month.
- Stop any terminal `watch.py` loop first so you don't poll twice. The logs grow
  slowly (about 10 KB a day for `watch.log`); truncate them whenever you like.

Managing the target list:

```
python3 watch.py --list                  # show the targets
python3 watch.py --remove TAIL           # drop a target (it is added again if seen)
python3 watch.py --ignore TAIL           # drop it and never add it again (works on any candidate)
python3 watch.py --unignore TAIL         # reverse --ignore
python3 watch.py --purge 60              # drop targets not seen for 60 days
```

Files: `targets.csv` (`tail, icao, model, owner, first_seen, last_seen,
sightings`) and `ignored.csv`. All times are UTC.

**What it can't see.** Only aircraft that are transmitting at the moment of the
query appear. A jet parked with its transponder off, or one that departs and
leaves the radius between polls, can be missed. A small radius sees less
traffic; a large one also picks up jets that only fly past.

## 4. Following the targets: `finder.py`

```
python3 finder.py                 # look up targets seen in the last 30 days (--days N), add new positions to history.csv
python3 finder.py TAIL            # test one aircraft: print the result, write nothing
python3 finder.py --limit 5 --raw
```

It queries `/hex/{icao}/` for each target and records the position only if it
is newer than what `history.csv` already holds. The API returns the last
known position even for aircraft that haven't flown recently, sometimes months
old. The `position_time` column is when the position was actually received,
not when you asked.

It stops after 5 consecutive errors (usually a rate limit or exhausted quota).
Expect thin data: run every few days, it records where each jet was then, not
what it did in between.

`history.csv` columns: `tail, icao, model, position_time, airport, altitude,
airborne, lat, lon, precision, source, queried`.

- `airport` is the nearest airport within 25 nm, otherwise blank. It is only
  where the aircraft was if `airborne` is `no`.
- `altitude` is feet, or `ground`.
- `airborne` is `yes` (the aircraft reported an altitude), `no` (it reported
  `ground`) or blank (neither). An airborne position near an airport may be an
  arrival or departure, or just a jet passing by, so don't read it as a landing.
- `precision` is `exact`, or `approx` when only the rounded `rr_lat`/`rr_lon`
  was available (about 25 nm of error: good only for the region).
- `source` is where the position was found: `direct`, `nested` (under
  `lastPosition`) or `rr`.

## 5. Reading the history: `report.py`

```
python3 report.py                  # every aircraft, most recently active first
python3 report.py TAIL1 TAIL2      # just these aircraft
python3 report.py --since 7        # last 7 days only
python3 report.py --min-moves 3    # skip aircraft that have barely moved
python3 report.py --days 7         # only aircraft with a ground position in the last 7 days
                                   # (default 30; their whole history is still shown)
python3 report.py --airborne       # also show airborne and en route positions
                                   # (hidden by default; aircraft with no ground
                                   # position are left out)
```

Each aircraft's heading shows its tail number, model and registrant, with a link
to the aircraft on the ADS-B Exchange globe (it uses the ICAO hex code) on the line below.
For each aircraft it prints a line every time its location changes: the UTC
time, the airport code, and the city and country. An airport is shown as a
location only for a position on the ground. An airborne position shows as
`near XXXX (airborne)`, and `en route` means no airport within 25 nm. If an
aircraft's last contact was airborne, the line is marked as such, because we
don't know where it landed.

```
N12345  Example Jet 7X  EXAMPLE LEASING TRUSTEE
    https://globe.adsbexchange.com/?icao=abc123
    watched: first 2026-01-05T19:40:40Z, last 2026-01-05T19:55:40Z, 2 sightings
    2026-01-02 17:30Z  KAAA        Alpha City, US
    2026-01-03 09:40Z  near KAAA   Alpha City, US   (airborne)
    2026-01-03 11:10Z  en route    53.0,8.0 (airborne)   <- last contact; still airborne, landing location unknown
```

When a jet's movements don't fit what you know of the people you're tracking,
remove it with `watch.py --remove` or `--ignore`.

### The globe: `report.py --map`

```
python3 report.py --map flights.html     # the usual report, plus a 3D globe
```

Writes one self-contained HTML file (three.js and a world outline are inlined
from `map_assets/`, so it works offline) and prints `globe written to ...`. Open
it in a browser. It shows the aircraft the report lists, with the same filters
(`--days`, `--since`, `--min-moves`, `--airborne`, tails):

- one color per aircraft, a dot at each position and an arc between consecutive
  positions. **The arcs are straight great-circle hops between known positions,
  not the path flown.** Airborne positions (with `--airborne`) are small dim dots.
- each aircraft's registration is printed in its color at the midpoint of its longest
  hop (short hops that bunch up near an airport are stacked so they stay readable)
- drag to rotate, scroll to zoom; airports are labeled with their code and locality (city and state or country)
- hover an airport dot to list, oldest first, each aircraft and the date and time it was on the ground there
  (only aircraft that are ticked, and only up to the slider's time)
- the list on the left toggles aircraft on and off; click a name for its
  movements, an ADS-B Exchange link, and a *focus* link that turns the globe to it
- the slider at the bottom hides everything after a point in time; the play
  button replays the movements

## Part 135 / 121 marking

`python3 build_part135.py` downloads the FAA's list of aircraft on Part 135
charter certificates (a spreadsheet, updated about weekly) and writes
`part135.csv`. `report.py` then adds a line under each aircraft that is on the
list, using the certificate type it's listed under:

- `Part 135 (On-demand charter)`
- `Part 121/135` for air carriers that hold both (mostly cargo and regional airlines)

Nothing is printed for an aircraft that isn't on the list. Not being listed
doesn't say what an aircraft is (private, managed, or charter under a foreign
certificate), so the report doesn't guess. The list can also lag, and it
describes the aircraft, not any one flight. A managed aircraft off the list can
still fly under its manager's callsign. Re-run
`build_part135.py` now and then to refresh it. VH- and other non-US aircraft
are never marked.

## Other scripts

| Script | Purpose |
|---|---|
| `update_data.py` | The weekly job: downloads `airports.csv` and the FAA registry zip, then runs `build_db.py --zip`, `build_candidates.py --refresh` and `build_part135.py`. |
| `cron_ctl.sh` | Installs and removes the cron jobs; see "Running everything from cron". |
| `rapidapi.py` | The API calls: `rapidapi_request` (by hex) and `rapidapi_area_request` (aircraft near a point). |
| `adsb.py`, `targets.py` | Shared response parsing and the CSV storage code. |
| `trackmap.py` | `python3 trackmap.py` reads the flightalert project's `tracker.log` (read-only) and writes `tracker_map.html`, the same globe as `report.py --map`. Each ground stop is a dot, and each flight is a smooth arc from where it took off to where it landed (not the exact path); an aircraft still in the air gets an arc to its last position. A "ground" report with no airport nearby is treated as airborne. `--tracks` also draws every airborne position, `--log FILE`, `--out FILE`, `--since DAYS`. |
| `flightmap.py`, `map_assets/` | Builds the globe page for `report.py --map` (template, three.js, world outline). |

## Notes on the data

- **Distances are nautical miles.**
- **ADS-B Exchange LADD flag.** If the aircraft's database entry has `faa_ladd`
  set, the owner has asked the FAA to limit public flight-data display. It
  suggests the owner wants discretion, but some charter aircraft use it too.
- **Charter or private?** The FAA registry can't say. Two public checks help:
  the FAA's list of aircraft on Part 135 charter certificates
  ([FAA-certificated Aircraft Operators](https://www.faa.gov/about/officeorg/headquartersoffices/avs/faa-certificated-aircraft-operators-legal-part-135-holders)),
  and the callsign in the ADS-B history. A jet flying as its tail number is usually
  a private operation, and an operator's own callsign (its prefix plus a number)
  means that operator is flying it. A managed
  aircraft can be absent from the Part 135 list and still fly under the
  operator's callsign, if it is flown privately for its owner.
- **The ADS-B Exchange hex endpoint** returns records far older than 48 hours;
  some were 17 months old. Always check the position time.
