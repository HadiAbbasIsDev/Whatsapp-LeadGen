#!/usr/bin/env bash
# Baileys -> Kapso transport cutover. Run in a maintenance window; the bot is
# offline between "stop gateway" and the post-start checks (~2-5 minutes).
# Fully reversible via scripts/kapso-rollback.sh (tested June path).
#
# Prereqs (already in .env): KAPSO_API_KEY, KAPSO_PHONE_NUMBER_ID,
#   KAPSO_WEBHOOK_SECRET. Poller mode needs no public URL. If you have a
#   permanent public HTTPS URL instead, set KAPSO_PUBLIC_URL to it and the
#   script registers a real webhook and skips the poller.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NODE_BIN="/usr/local/node-v22.21.1/bin"
export PATH="$NODE_BIN:$PATH"
export OPENCLAW_AUTO_UPDATE=0
VERSION="${OPENCLAW_KAPSO_VERSION:-2026.6.33}"   # extended-stable; 2026.6.8 = June-proven fallback
CONF="$HOME/.openclaw/openclaw.json"
STAMP="$(date +%Y%m%d-%H%M%S)"

env_get() { grep -E "^$1=" "$REPO/.env" | head -1 | cut -d= -f2- | tr -d '"' || true; }

echo "== Preflight"
for v in KAPSO_API_KEY KAPSO_PHONE_NUMBER_ID KAPSO_WEBHOOK_SECRET; do
  [ -n "$(env_get $v)" ] || { echo "FATAL: $v missing from .env"; exit 1; }
done
[ -d "$HOME/.openclaw/extensions/kapso-whatsapp" ] || { echo "FATAL: kapso plugin not installed"; exit 1; }
curl -sS -m 10 -o /dev/null "https://api.kapso.ai/" || { echo "FATAL: api.kapso.ai unreachable"; exit 1; }
KEY="$(env_get KAPSO_API_KEY)"
curl -sS -m 15 -H "X-API-Key: $KEY" -H "User-Agent: Mozilla/5.0 curl/8" \
  "https://api.kapso.ai/platform/v1/whatsapp/phone_numbers" | grep -q '"id"' \
  || { echo "FATAL: Kapso API key rejected"; exit 1; }
echo "   ok"

echo "== Stopping gateway + poller"
pkill -f "kapso_poller.py" 2>/dev/null || true
for pid in $(pgrep -f "openclaw-gateway" || true; pgrep -x openclaw || true); do kill -TERM "$pid" 2>/dev/null || true; done
for i in $(seq 1 10); do pgrep -f "openclaw-gateway" >/dev/null 2>&1 || break; sleep 1; done
if pgrep -f "openclaw-gateway" >/dev/null 2>&1; then
  echo "   TERM ignored; escalating to KILL"
  for pid in $(pgrep -f "openclaw-gateway" || true; pgrep -x openclaw || true); do kill -KILL "$pid" 2>/dev/null || true; done
  sleep 2
fi
pgrep -f "openclaw-gateway" >/dev/null 2>&1 && { echo "FATAL: gateway still running"; exit 1; }

echo "== Config: backup (only if still Baileys-shaped) "
# A re-run after a partial cutover must NOT overwrite the good Baileys backup
# with an already-transformed config — rollback depends on these backups.
if python3 -c '
import json, sys
cfg = json.load(open(sys.argv[1]))
kapso = cfg.get("channels", {}).get("kapso-whatsapp", {})
sys.exit(0 if not kapso.get("enabled") else 1)
' "$CONF"; then
  cp "$CONF" "$CONF.baileys-backup-$STAMP"
  echo "   backup: $CONF.baileys-backup-$STAMP"
else
  echo "   config already Kapso-shaped (re-run); keeping existing baileys backups"
fi

echo "== Installing openclaw@$VERSION (current: $(openclaw --version 2>/dev/null || echo '?'))"
npm install -g "openclaw@$VERSION"
echo "   now: $(openclaw --version)"

echo "== Config: Kapso transform"
python3 - "$CONF" "$REPO/.env" << 'PYEOF'
import json, re, sys
conf_path, env_path = sys.argv[1], sys.argv[2]
env = {}
for line in open(env_path):
    m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line.strip())
    if m: env[m.group(1)] = m.group(2).strip().strip('"')
cfg = json.load(open(conf_path))
plugins = cfg.setdefault("plugins", {})
entries = plugins.setdefault("entries", {})
entries.setdefault("whatsapp", {})["enabled"] = False
entries["kapso-whatsapp"] = {"enabled": True}
plugins["load"] = {"paths": []}                      # 2026.6.x: no bundled whatsapp ext
plugins.get("installs", {}).pop("whatsapp", None)
channels = cfg.setdefault("channels", {})
channels.setdefault("whatsapp", {})["enabled"] = False
channels["kapso-whatsapp"] = {
    "enabled": True,
    "phoneNumberId": env["KAPSO_PHONE_NUMBER_ID"],
    "webhookPath": "/kapso/webhook",
    "baseUrl": "https://api.kapso.ai/meta/whatsapp",
    "dmSecurity": "allowlist",
    # keep the same single-number test allowlist as Baileys (Kapso sends digits-only)
    "allowFrom": ["+923362615506", "923362615506"],
}
# secrets stay out of the channel block; the plugin reads env — inject via config env
cfg.setdefault("env", {})
cfg["env"]["KAPSO_API_KEY"] = env["KAPSO_API_KEY"]
cfg["env"]["KAPSO_WEBHOOK_SECRET"] = env["KAPSO_WEBHOOK_SECRET"]
cfg["env"]["KAPSO_PHONE_NUMBER_ID"] = env["KAPSO_PHONE_NUMBER_ID"]
json.dump(cfg, open(conf_path, "w"), indent=2)
print("   config transformed")
PYEOF

echo "== Flipping WA_TRANSPORT=kapso in .env (keeps python tools in lockstep with the config)"
if grep -q '^WA_TRANSPORT=' "$REPO/.env"; then
  sed -i 's/^WA_TRANSPORT=.*/WA_TRANSPORT=kapso/' "$REPO/.env"
else
  echo 'WA_TRANSPORT=kapso' >> "$REPO/.env"
fi

echo "== Patching category gate into kapso plugin"
python3 "$REPO/openclaw-patches/patch_kapso_gate.py"

echo "== Starting gateway"
cd "$REPO"
MARK="$(wc -l < progress/gateway.log 2>/dev/null || echo 0)"
setsid openclaw gateway >> progress/gateway.log 2>&1 < /dev/null &
sleep 12
if tail -n +"$((MARK + 1))" progress/gateway.log | grep -aq "registered Kapso webhook route"; then
  echo "   kapso webhook route registered"
else
  echo "   WARNING: kapso route not seen in THIS start's log — check progress/gateway.log"
fi

PUBLIC_URL="$(env_get KAPSO_PUBLIC_URL)"
if [ -n "$PUBLIC_URL" ]; then
  echo "== Registering real webhook at $PUBLIC_URL/kapso/webhook"
  openclaw kapso-whatsapp setup \
    --api-key "$KEY" \
    --phone-number-id "$(env_get KAPSO_PHONE_NUMBER_ID)" \
    --webhook-url "$PUBLIC_URL/kapso/webhook" \
    --webhook-secret "$(env_get KAPSO_WEBHOOK_SECRET)" \
    --register-webhook || echo "   WARNING: webhook registration failed — falling back to poller"
else
  echo "== Starting inbound polling relay (no public URL mode)"
  setsid python3 "$REPO/scripts/kapso_poller.py" >> "$REPO/progress/kapso-poller.log" 2>&1 < /dev/null &
  echo "   poller started (log: progress/kapso-poller.log)"
fi

echo
echo "== CUTOVER DONE — test checklist:"
echo "   [ ] send a WhatsApp text to the Kapso number -> agent replies"
echo "   [ ] ask for a product -> image + caption arrives (send_product kapso path)"
echo "   [ ] run complaint flow -> gate blocks (see ~/.openclaw/kapso-gate.log)"
echo "   [ ] db.py set-category -> category appears in Kapso inbox contact metadata"
echo "   [ ] voice note -> transcript or media reaches the agent"
echo "   [ ] memory: ask a question referencing earlier conversation"
echo "Rollback any time: scripts/kapso-rollback.sh"
