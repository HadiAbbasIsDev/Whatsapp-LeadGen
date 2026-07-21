#!/usr/bin/env bash
# Container entrypoint: generate the openclaw config from env (first run only),
# prepare the catalog + database, then hand off to supervisor (dashboard + gateway).
set -e

REPO="/home/it-admin/wa-lead-gen"
OC="${HOME}/.openclaw"
mkdir -p "$OC" "$REPO/progress" "$REPO/workspace/data"
chown -R node:node "$OC" "$REPO/progress" "$REPO/workspace/data" /home/it-admin/wa-lead-gen-backups

: "${ADMIN_PASS:?Set ADMIN_PASS in .env}"
if [ "${#ADMIN_PASS}" -lt 16 ] || [ "$ADMIN_PASS" = "change-this-to-a-strong-password" ]; then
  echo "[entrypoint] ADMIN_PASS must be at least 16 characters and not the example value" >&2
  exit 1
fi
if [ -n "${ALLOWED_NUMBER:-}" ] && ! printf '%s' "$ALLOWED_NUMBER" | grep -Eq '^\+[1-9][0-9]{7,14}$'; then
  echo "[entrypoint] ALLOWED_NUMBER must use E.164 format (for example +923001234567)" >&2
  exit 1
fi

# 1. Generate ~/.openclaw/openclaw.json on first run (persisted in the volume).
if [ ! -f "$OC/openclaw.json" ]; then
  : "${DEEPSEEK_API_KEY:?Set DEEPSEEK_API_KEY in your .env}"
  : "${ALLOWED_NUMBER:?Set ALLOWED_NUMBER in your .env (E.164, e.g. +9230XXXXXXXX)}"
  export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-unused}"
  export GATEWAY_TOKEN="${GATEWAY_TOKEN:-$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
  python3 - "$REPO/docker/openclaw.config.docker.json" "$OC/openclaw.json" <<'PY'
import os, sys
tpl = open(sys.argv[1]).read()
for var in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "GATEWAY_TOKEN", "ALLOWED_NUMBER"):
    tpl = tpl.replace("${%s}" % var, os.environ.get(var, ""))
open(sys.argv[2], "w").write(tpl)
PY
  chmod 600 "$OC/openclaw.json"
  echo "[entrypoint] generated $OC/openclaw.json (allowlist: ${ALLOWED_NUMBER})"
else
  echo "[entrypoint] using existing $OC/openclaw.json"
  chmod 600 "$OC/openclaw.json"
fi

# 2. Build the product catalog from database/products into the data volume.
python3 "$REPO/sync_products.py" >/dev/null 2>&1 || echo "[entrypoint] sync_products warning"

# 3. Initialise / migrate the SQLite database (idempotent).
python3 "$REPO/workspace/db.py" init >/dev/null 2>&1 || echo "[entrypoint] db init warning"

echo "[entrypoint] starting supervisor (dashboard + gateway)..."
echo "[entrypoint] dashboard: http://localhost:8088  (login: admin / \$ADMIN_PASS)"
echo "[entrypoint] First run: open the dashboard, click Start, then check the logs"
echo "[entrypoint] for the QR code to link WhatsApp:  docker compose logs -f"

# 4. Hand off to supervisor (PID 1 via tini).
exec gosu node supervisord -c "$REPO/docker/supervisord.conf" -n
