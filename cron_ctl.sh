#!/usr/bin/env bash
# cron_ctl.sh — run the whole flightfinder system from cron.
#
#   ./cron_ctl.sh start ICAO        install the cron jobs, watching that airport
#   ./cron_ctl.sh stop              remove them
#   ./cron_ctl.sh status            installed? and the last lines of each log
#   ./cron_ctl.sh run JOB [ICAO]    run one job now: watch (needs ICAO) | daily | weekly
#
# Jobs (edit the schedules below, then run start again):
#   watch   every 15 minutes   watch.py ICAO --once                         -> watch.log
#   daily   04:30              finder.py; report.py --map flights.html;
#                              trackmap.py                                  -> daily.log
#   weekly  Sunday 03:00       update_data.py (FAA registry, candidates,
#                              Part 135 list)                               -> weekly.log
#
# start writes cron.crontab in this directory and adds it to your crontab between
# BEGIN/END marker lines; stop removes only that block, leaving your other entries
# alone. Cron doesn't see your shell environment, so put the key in cron.env
# (chmod 600):  RAPIDAPI_KEY=your_key
cd "$(dirname "$0")" || exit 1
DIR=$PWD
WATCH_SCHEDULE="*/15 * * * *"
DAILY_SCHEDULE="30 4 * * *"
WEEKLY_SCHEDULE="0 3 * * 0"
CRONFILE=$DIR/cron.crontab
BEGIN="# BEGIN flightfinder"
END="# END flightfinder"

installed() { crontab -l 2>/dev/null | grep -qF "$BEGIN"; }
others()    { crontab -l 2>/dev/null | sed "/^$BEGIN\$/,/^$END\$/d"; }
stamp()     { echo "=== $(date '+%F %T %Z') $*"; }

load_env() {
  for f in cron.env watch.env; do
    if [ -f "$f" ]; then set -a; . "./$f"; set +a; break; fi
  done
}

case "$1" in
  start)
    AIRPORT=$2
    if [ -z "$AIRPORT" ]; then
      echo "usage: $0 start ICAO   (the airport to watch, e.g. a code from airports.csv)"
      exit 1
    fi
    if [ ! -f cron.env ] && [ ! -f watch.env ]; then
      echo "cron.env not found. Create it with a line:  RAPIDAPI_KEY=your_key   (then chmod 600 cron.env)"
      exit 1
    fi
    chmod 600 cron.env 2>/dev/null
    {
      echo "$BEGIN"
      echo "PYTHON=$(command -v python3)"
      echo "$WATCH_SCHEDULE $DIR/cron_ctl.sh run watch $AIRPORT >> $DIR/watch.log 2>&1"
      echo "$DAILY_SCHEDULE $DIR/cron_ctl.sh run daily >> $DIR/daily.log 2>&1"
      echo "$WEEKLY_SCHEDULE $DIR/cron_ctl.sh run weekly >> $DIR/weekly.log 2>&1"
      echo "$END"
    } > "$CRONFILE"
    new=$({ others; cat "$CRONFILE"; })
    printf '%s\n' "$new" | crontab - || exit 1
    echo "installed; see $CRONFILE"
    echo "If watch.py is still running in a terminal, stop it (Ctrl+C) so you don't poll twice."
    ;;
  stop)
    if installed; then
      new=$(others)
      printf '%s\n' "$new" | crontab -
      echo "removed from your crontab (cron.crontab is kept)"
    else
      echo "not installed"
    fi
    ;;
  status)
    if installed; then
      echo "installed:"
      crontab -l | sed -n "/^$BEGIN\$/,/^$END\$/p" | sed 's/^/  /'
    else
      echo "not installed"
    fi
    for log in watch daily weekly; do
      if [ -f $log.log ]; then echo "$log.log:"; tail -n 3 $log.log | cut -c1-160 | sed 's/^/  /'; fi
    done
    ;;
  run)
    JOB=$2
    PY=${PYTHON:-python3}
    load_env
    if [ -z "$RAPIDAPI_KEY" ] && [ "$JOB" != weekly ]; then
      echo "$(date -u +%FT%TZ)  ERROR RAPIDAPI_KEY is not set (put it in cron.env)"
      exit 1
    fi
    case "$JOB" in
      watch)
        # -n: if the previous run is somehow still going, skip this one rather than overlap
        if [ -z "$3" ]; then echo "usage: $0 run watch ICAO"; exit 2; fi
        exec flock -n "$DIR/watch.lock" "$PY" watch.py "$3" --once
        ;;
      daily|weekly)
        # one daily/weekly job at a time (the weekly rebuild can take a few minutes); wait up to an hour
        exec 9>"$DIR/jobs.lock"
        flock -w 3600 9 || { stamp "$JOB: another job held the lock for an hour; skipped"; exit 1; }
        rc=0
        if [ "$JOB" = weekly ]; then
          stamp weekly update_data.py
          "$PY" update_data.py || rc=1
        else
          stamp daily finder.py
          "$PY" finder.py || rc=1
          stamp daily report.py --map flights.html
          "$PY" report.py --map flights.html > report.txt || rc=1
          echo "report written to report.txt, globe to flights.html"
          stamp daily trackmap.py
          "$PY" trackmap.py || rc=1
        fi
        stamp "$JOB finished, status $rc"
        exit $rc
        ;;
      *)
        echo "usage: $0 run watch|daily|weekly"
        exit 2
        ;;
    esac
    ;;
  *)
    echo "usage: $0 start ICAO | stop | status | run watch ICAO | run daily | run weekly"
    ;;
esac
