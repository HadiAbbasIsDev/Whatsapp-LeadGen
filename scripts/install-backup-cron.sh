#!/usr/bin/env bash
# Installs a daily cron job that runs scripts/backup.sh at 03:30 every night.
# Re-running is safe (it replaces any existing wa-lead-gen backup cron line).
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP="$REPO/scripts/backup.sh"
LOGDIR="${BACKUP_DIR:-$HOME/wa-lead-gen-backups}"
mkdir -p "$LOGDIR"

LINE="30 3 * * * BACKUP_DIR=\"$LOGDIR\" /usr/bin/env bash $BACKUP >> $LOGDIR/backup.log 2>&1"

# Build the new crontab: existing lines (minus any old backup line) + our line.
EXISTING="$(crontab -l 2>/dev/null || true)"
{
  printf '%s\n' "$EXISTING" | grep -v 'wa-lead-gen/scripts/backup.sh' || true
  printf '%s\n' "$LINE"
} | grep -v '^[[:space:]]*$' | crontab -

echo "Installed daily backup cron (03:30). Current crontab:"
crontab -l | grep backup.sh
echo ""
echo "Backups go to: $LOGDIR  (set BACKUP_REMOTE in the cron line for off-machine copies)"
