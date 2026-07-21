#!/usr/bin/env python3
"""Small, fail-safe maintenance daemon for local container deployments."""

import os
import sqlite3
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VOICE_CACHE = HERE / "voice_cache"
DB_PATH = HERE / "data" / "leadgen.db"


def env_int(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def clean_voice_cache(retention_days):
    """Remove only regular cache files older than the configured retention."""
    cutoff = time.time() - retention_days * 86400
    deleted = 0
    for folder_name in ("audio", "transcripts"):
        folder = VOICE_CACHE / folder_name
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime <= cutoff:
                    path.unlink()
                    deleted += 1
            except OSError as exc:
                print(f"[maintenance] could not remove {path.name}: {exc}", flush=True)
    return deleted


def clean_expired_memories():
    if not DB_PATH.is_file():
        return 0
    with sqlite3.connect(DB_PATH, timeout=30) as conn:
        result = conn.execute(
            "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at <= datetime('now')"
        )
        return result.rowcount


def main():
    retention = env_int("VOICE_RETENTION_DAYS", 7, 0, 3650)
    interval = env_int("MAINTENANCE_INTERVAL_SECONDS", 21600, 300, 86400)
    while True:
        deleted = clean_voice_cache(retention)
        expired = clean_expired_memories()
        print(f"[maintenance] voice retention={retention}d; deleted={deleted}; expired memories={expired}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
