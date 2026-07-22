#!/usr/bin/env bash
# Roll back the Kapso cutover to the proven Baileys setup (June-tested path):
# pin openclaw 2026.4.9, restore the pre-cutover config, re-apply the Baileys
# patches, restart. Existing WhatsApp creds reconnect without a QR rescan.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_BIN="/usr/local/node-v22.21.1/bin"
export PATH="$NODE_BIN:$PATH"
export OPENCLAW_AUTO_UPDATE=0
CONF="$HOME/.openclaw/openclaw.json"

echo "== Restoring pre-cutover config (verify BEFORE stopping anything)"
BACKUP="$(ls -1t "$CONF".baileys-backup-* 2>/dev/null | head -1 || true)"
if [ -z "$BACKUP" ]; then
  echo "FATAL: no $CONF.baileys-backup-* found."
  echo "  Restore manually from one of: $CONF.last-good / $CONF.bak* (Baileys-era copies)"
  exit 1
fi
# Sanity: the backup must actually be Baileys-shaped
python3 -c '
import json, sys
cfg = json.load(open(sys.argv[1]))
ok = cfg.get("channels", {}).get("whatsapp", {}).get("enabled") and not cfg.get("channels", {}).get("kapso-whatsapp", {}).get("enabled")
sys.exit(0 if ok else 1)
' "$BACKUP" || { echo "FATAL: newest backup $BACKUP is not Baileys-shaped — pick one manually"; exit 1; }

echo "== Stopping gateway + poller"
pkill -f "kapso_poller.py" 2>/dev/null || true
for pid in $(pgrep -f "openclaw-gateway" || true; pgrep -x openclaw || true); do kill -TERM "$pid" 2>/dev/null || true; done
for i in $(seq 1 10); do pgrep -f "openclaw-gateway" >/dev/null 2>&1 || break; sleep 1; done

cp "$BACKUP" "$CONF"
echo "   restored: $BACKUP"

echo "== Reinstalling openclaw@2026.4.9 + Baileys patches"
npm install -g openclaw@2026.4.9
python3 "$REPO/openclaw-patches/apply_patches.py"

echo "== Flipping WA_TRANSPORT=baileys in .env"
if grep -q '^WA_TRANSPORT=' "$REPO/.env"; then
  sed -i 's/^WA_TRANSPORT=.*/WA_TRANSPORT=baileys/' "$REPO/.env"
else
  echo 'WA_TRANSPORT=baileys' >> "$REPO/.env"
fi

echo "== Starting gateway"
cd "$REPO"
setsid openclaw gateway >> progress/gateway.log 2>&1 < /dev/null &
sleep 12
tail -3 progress/gateway.log
echo "== ROLLBACK DONE — send a test message to confirm Baileys is back."
