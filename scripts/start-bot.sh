#!/usr/bin/env bash
# One-command startup for the WhatsApp bot — safe to run any time (skips
# anything already running). Works for both transports (WA_TRANSPORT in .env).
#
#   bash scripts/start-bot.sh              # start gateway (+ poller on kapso)
#   bash scripts/start-bot.sh --with-dashboard   # also start the admin dashboard
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/usr/local/node-v22.21.1/bin:$PATH"
export OPENCLAW_AUTO_UPDATE=0
cd "$REPO"

TRANSPORT="$(grep -E '^WA_TRANSPORT=' .env | cut -d= -f2- | tr -d '"' || echo baileys)"
PUBLIC_URL="$(grep -E '^KAPSO_PUBLIC_URL=' .env | cut -d= -f2- | tr -d '"' || true)"
echo "== Transport: ${TRANSPORT:-baileys}"

echo "== Re-applying runtime patches (idempotent)"
if [ "$TRANSPORT" != "kapso" ]; then
  python3 openclaw-patches/apply_patches.py 2>&1 | tail -1 || true    # Baileys runtime patches
fi
python3 openclaw-patches/patch_kapso_gate.py 2>&1 | tail -1 || true   # Kapso gate (no-op if plugin absent)

# The gateway process name differs across openclaw versions ("openclaw-gateway"
# on 2026.4.9, plain "openclaw" on 2026.6.x) — the loopback port is the
# version-proof way to detect it.
gateway_up() { ss -tln 2>/dev/null | grep -q ":18789 "; }

echo "== Gateway"
if gateway_up; then
  echo "   already running (listening on 18789)"
else
  setsid openclaw gateway >> progress/gateway.log 2>&1 < /dev/null &
  sleep 8
  if gateway_up; then
    echo "   started"
  else
    echo "   FAILED — check progress/gateway.log"; exit 1
  fi
fi

if [ "$TRANSPORT" = "kapso" ] && [ -z "$PUBLIC_URL" ]; then
  echo "== Inbound polling relay (kapso, no public URL)"
  if pgrep -f "kapso_pol[l]er" >/dev/null; then
    echo "   already running (pid $(pgrep -f 'kapso_pol[l]er' | head -1))"
  else
    setsid python3 scripts/kapso_poller.py >> progress/kapso-poller.log 2>&1 < /dev/null &
    sleep 3
    pgrep -f "kapso_pol[l]er" >/dev/null && echo "   started" || { echo "   FAILED — check progress/kapso-poller.log"; exit 1; }
  fi
fi

if [ -f "$REPO/label-bridge/auth/creds.json" ]; then
  echo "== Label bridge (WhatsApp app labels)"
  if pgrep -f "label-bridge/bridge[.]js" >/dev/null; then
    echo "   already running"
  else
    setsid node label-bridge/bridge.js >> progress/label-bridge.log 2>&1 < /dev/null &
    sleep 2
    pgrep -f "label-bridge/bridge[.]js" >/dev/null && echo "   started" || echo "   FAILED — check progress/label-bridge.log"
  fi
fi

if [ "${1:-}" = "--with-dashboard" ]; then
  echo "== Admin dashboard"
  if pgrep -f "admin/app[.]py" >/dev/null; then
    echo "   already running"
  else
    setsid python3 admin/app.py >> progress/admin.log 2>&1 < /dev/null &
    sleep 2
    echo "   started on http://localhost:8088"
  fi
fi

echo
echo "== Health"
echo "   gateway:  $(gateway_up && echo RUNNING || echo DOWN)"
if [ "$TRANSPORT" = "kapso" ] && [ -z "$PUBLIC_URL" ]; then
  echo "   poller:   $(pgrep -f 'kapso_pol[l]er' >/dev/null && echo RUNNING || echo DOWN)"
fi
echo "   last log: $(tail -1 progress/gateway.log 2>/dev/null | cut -c1-100)"
echo "Done. Send a WhatsApp message to the bot's number to confirm it replies."
