#!/usr/bin/env bash
# Daily backup of the lead-gen bot's critical, hard-to-recreate state:
#   - SQLite database (consistent snapshot — safe even in WAL mode)
#   - WhatsApp credentials (so you never have to re-scan the QR after a restore)
#   - live openclaw config + discovered WhatsApp label IDs
#
# Keeps the last 14 backups locally. Set BACKUP_REMOTE to also copy off-machine
# (an rclone remote like "gdrive:bot-backups", or an rsync/ssh target).
#
# Run manually:  bash scripts/backup.sh
# Daily (cron):  see scripts/install-backup-cron.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${BACKUP_DIR:-$HOME/wa-lead-gen-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/$STAMP"
mkdir -p "$OUT"
chmod 700 "$DEST" 2>/dev/null || true

# 1. SQLite DB — consistent snapshot via Python's sqlite backup API (WAL-safe)
DB="$REPO/workspace/data/leadgen.db"
if [ -f "$DB" ]; then
  python3 - "$DB" "$OUT/leadgen.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst:
    src.backup(dst)
src.close(); dst.close()
PY
fi

# 2. WhatsApp credentials (avoids a QR re-scan on restore)
if [ -d "$HOME/.openclaw/credentials" ]; then
  tar czf "$OUT/whatsapp-credentials.tar.gz" -C "$HOME/.openclaw" credentials
fi

# 3. Live config + label IDs (small, speeds up a restore)
cp "$HOME/.openclaw/openclaw.json" "$OUT/openclaw.json" 2>/dev/null || true
cp "$HOME/.openclaw/whatsapp-labels.json" "$OUT/whatsapp-labels.json" 2>/dev/null || true

# 4. Rotate — keep the most recent 14
ls -1dt "$DEST"/*/ 2>/dev/null | tail -n +15 | xargs -r rm -rf

# 5. Optional off-machine copy
if [ -n "${BACKUP_REMOTE:-}" ]; then
  if command -v rclone >/dev/null 2>&1; then
    rclone copy "$OUT" "$BACKUP_REMOTE/$STAMP" || echo "[warn] rclone copy failed"
  else
    rsync -a "$OUT/" "$BACKUP_REMOTE/$STAMP/" || echo "[warn] rsync copy failed (set BACKUP_REMOTE to a valid target / install rclone)"
  fi
fi

echo "[backup] $STAMP -> $OUT ($(du -sh "$OUT" 2>/dev/null | cut -f1))"
