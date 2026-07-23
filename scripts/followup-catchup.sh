#!/usr/bin/env bash
# Shutdown-proof wrapper for the daily follow-up runner.
#
# The laptop is often off at any fixed hour, so instead of one fixed cron
# time, cron calls this hourly (and shortly after every boot). It runs the
# follow-up runner at most once per ~24h, only during business hours, and
# stamps each completed run — so a night shutdown just delays the run to
# the first business hour the laptop is on. All cadence progress (which
# week each customer is on) lives in the SQLite DB and survives reboots.
set -u
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$REPO/progress/.followup_last_run"
LOG="$REPO/progress/followup-runner.log"

# only send during business hours (9:00-21:00 local) — no 3am marketing
hour=$(date +%H)
[ "$hour" -ge 9 ] && [ "$hour" -lt 21 ] || exit 0

now=$(date +%s)
last=$(cat "$STAMP" 2>/dev/null || echo 0)
# 23h55m: keeps a stable daily rhythm without drifting earlier each day
[ $((now - last)) -ge 86100 ] || exit 0

if /usr/bin/python3 "$REPO/scripts/followup_runner.py" >> "$LOG" 2>&1; then
  echo "$now" > "$STAMP"
fi
