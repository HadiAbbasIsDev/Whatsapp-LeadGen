#!/usr/bin/env bash
# ============================================================
#  renovate.pk — START THE DASHBOARD
#  Double-click this (or run it) to open the control panel.
#  From the dashboard you can see if WhatsApp + the bot are
#  connected, start/stop the bot, scan the WhatsApp QR, and
#  manage customers.
# ============================================================
set -uo pipefail

# repo root = one level up from this Start/ folder
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
export PATH="/usr/local/node-v22.21.1/bin:$PATH"

URL="http://localhost:8088"

echo "== Starting renovate.pk dashboard =="

if pgrep -f "admin/app[.]py" >/dev/null; then
  echo "   dashboard already running"
else
  setsid python3 admin/app.py >> progress/admin.log 2>&1 < /dev/null &
  # wait until it answers
  for i in $(seq 1 20); do
    if curl -s -o /dev/null "$URL"; then break; fi
    sleep 0.5
  done
  echo "   dashboard started"
fi

echo
echo "   Open this in your browser:  $URL"
echo "   Login:  admin  /  (password in admin/admin_password.txt)"
echo

# try to open the browser automatically (ignore errors on headless machines)
( xdg-open "$URL" >/dev/null 2>&1 || sensible-browser "$URL" >/dev/null 2>&1 || true ) &

echo "Done. Leave this window open or close it — the dashboard keeps running."
