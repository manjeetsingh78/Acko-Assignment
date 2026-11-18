#!/usr/bin/env bash
# Incident drill: breaks and repairs the mock services on a fixed schedule,
# so you can test Pulse and run a repeatable demo.
#   bash scripts/incident-drill.sh          full drill (about 7 minutes)
#   bash scripts/incident-drill.sh --fast   short drill (under 3 minutes)
#   bash scripts/incident-drill.sh --reset  put every service back to normal
set -eu
HOST="${HOST:-localhost}"
port() { case "$1" in quotes) echo 7001;; payments) echo 7002;; claims) echo 7003;; notifications) echo 7004;; esac; }
UNIT=60
[ "${1:-}" = "--fast" ] && UNIT=20

ctl() { # service action [query]
  curl -fsS -X POST "http://$HOST:$(port "$1")/control/$2${3:+?$3}" >/dev/null && \
  echo "$(date +%H:%M:%S)  $1 -> $2${3:+ ($3)}"
}
reset_all() { for s in quotes payments claims notifications; do ctl "$s" reset; done; }

if [ "${1:-}" = "--reset" ]; then reset_all; exit 0; fi

trap 'echo; echo "Interrupted: resetting all services"; reset_all' INT
echo "Incident drill starting (step length ${UNIT}s). Press Ctrl+C to stop and reset."
reset_all
echo "Step 1: claims goes down";                  ctl claims down;               sleep $((UNIT * 2))
echo "Step 2: claims recovers";                   ctl claims reset;              sleep "$UNIT"
echo "Step 3: payments becomes slow (3 s)";       ctl payments slow "ms=3000";   sleep "$UNIT"
echo "Step 4: payments recovers";                 ctl payments reset;            sleep "$UNIT"
echo "Step 5: quotes becomes flaky (50% errors)"; ctl quotes flaky "rate=0.5";   sleep $((UNIT * 2))
echo "Step 6: quotes recovers";                   ctl quotes reset
echo "Drill finished. All services are back to normal."
