#!/usr/bin/env python3
"""
Daily follow-up runner — the clock behind AGENTS.md Flows 3, 4 and 5.

The agent only acts when a customer writes. This script supplies the WHEN:

  - category "followup" (Flows 3 & 5): one template follow-up per week of
    silence, 3 sends max, then category -> junk.
  - human-owned categories ahsan/ahmed/imran/rafay (Flow 4): after 7+ days of
    total silence, ONE re-engagement template and cadence_status -> followup
    (owner category preserved); thereafter weekly like Flow 3; after 3 sends
    cadence_status -> junk.

Safety:
  - If the gateway config says dmSecurity=allowlist, ONLY allowlisted numbers
    are ever followed up (mirrors inbound policy during testing).
  - Sends go through send_template.py: approved marketing templates are allowed
    outside the 24h window, so only opted-out (STOP) numbers are skipped. Every
    attempt is audit-logged.
  - At most one send per customer per run; the cron runs once daily.

Usage:
  followup_runner.py                 # normal daily run (cron)
  followup_runner.py --dry-run       # show what would happen, send nothing
  followup_runner.py --min-days 0    # testing: treat everyone as due
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SCRIPTS)
WORKSPACE = os.path.join(REPO, "workspace")
sys.path.insert(0, WORKSPACE)
import kapso  # noqa: E402

DB_FILE = os.path.join(WORKSPACE, "data", "leadgen.db")
DB_PY = os.path.join(WORKSPACE, "db.py")
SEND_TEMPLATE = os.path.join(WORKSPACE, "send_template.py")
OPENCLAW_CONF = os.path.expanduser("~/.openclaw/openclaw.json")
TEAM = {"ahsan", "ahmed", "imran", "rafay"}
MAX_SENDS = 3


def log(msg):
    print(f"{datetime.now(timezone.utc).astimezone().isoformat()} {msg}", flush=True)


def allowlist():
    """Returns (active, digit_set). Mirrors the gateway's inbound allowlist."""
    try:
        ch = json.load(open(OPENCLAW_CONF)).get("channels", {}).get("kapso-whatsapp", {})
        if (ch.get("dmSecurity") or "").lower() != "allowlist":
            return False, set()
        return True, {kapso.digits(n) for n in ch.get("allowFrom", [])}
    except Exception:
        # config unreadable -> fail CLOSED (send to nobody) rather than spam
        return True, set()


def days_since(iso):
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(str(iso))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - then).total_seconds() / 86400.0
    except ValueError:
        return None


def run_db(*args, dry=False):
    if dry:
        log(f"  DRY: db.py {' '.join(args)}")
        return True
    r = subprocess.run(["python3", DB_PY, *args], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        log(f"  [warn] db.py {args[0]} failed: {(r.stdout + r.stderr).strip()[:200]}")
    return r.returncode == 0


def remember(phone, key, value, dry=False):
    run_db("remember", "--phone", phone, "--kind", "cadence", "--key", key,
           "--value", str(value), "--source", "followup_runner", dry=dry)


def send(phone, template, dry=False):
    if dry:
        log(f"  DRY: would send template '{template}' to {phone}")
        return True
    r = subprocess.run(["python3", SEND_TEMPLATE, "--to", phone, "--template", template],
                       capture_output=True, text=True, timeout=90)
    out = (r.stdout + r.stderr).strip()
    log(f"  {out[:200]}")
    return r.returncode == 0 and "[OK]" in out


def cadence(conn, phone):
    rows = conn.execute(
        "SELECT memory_key, value FROM memories WHERE phone=? AND kind='cadence'", (phone,)
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-days", type=float, default=7.0, help="silence threshold (testing)")
    args = ap.parse_args()
    dry = args.dry_run

    template = kapso.env("FOLLOWUP_TEMPLATE") or "renovate_interest_followup"
    guard_on, allowed = allowlist()
    log(f"followup_runner start (template={template}, min_days={args.min_days}, "
        f"allowlist={'ON:' + str(len(allowed)) + ' numbers' if guard_on else 'off'}, dry={dry})")

    conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    customers = conn.execute("SELECT * FROM customers").fetchall()

    sent = skipped = junked = 0
    for c in customers:
        phone, cat = c["phone"], (c["category"] or "").strip().lower()
        cad_status = (c["cadence_status"] or "").strip().lower()
        is_followup = cat == "followup"
        is_team = cat in TEAM
        if not (is_followup or is_team):
            continue
        if is_team and cad_status == "junk":
            continue
        if guard_on and kapso.digits(phone) not in allowed:
            log(f"{phone}: skip — not in allowlist (testing mode)")
            skipped += 1
            continue

        cad = cadence(conn, phone)
        try:
            week = int(cad.get("followup_week") or 0)
        except ValueError:
            week = 0
        silence = days_since(c["last_message_at"])
        since_send = days_since(cad.get("last_followup_date"))
        gap = min(x for x in (silence, since_send) if x is not None) if (silence or since_send) else None
        if gap is None:
            log(f"{phone}: skip — no usable dates")
            skipped += 1
            continue
        if gap < args.min_days:
            log(f"{phone}: not due ({gap:.1f}d since last activity/follow-up)")
            skipped += 1
            continue

        if week >= MAX_SENDS:
            # 3 follow-ups sent, another full week of silence -> junk (Flow 3/4 cap)
            if is_team:
                log(f"{phone}: cap reached — cadence_status -> junk (owner category kept)")
                run_db("set-cadence-status", "--phone", phone, "--cadence-status", "junk", dry=dry)
            else:
                log(f"{phone}: cap reached — category -> junk")
                run_db("set-category", "--phone", phone, "--category", "junk", dry=dry)
            junked += 1
            continue

        label = f"week {week + 1}/{MAX_SENDS}" + (" (human-owned re-engage)" if is_team and week == 0 else "")
        log(f"{phone}: due ({gap:.1f}d silent) — sending {label}")
        if send(phone, template, dry=dry):
            sent += 1
            if is_team and cad_status != "followup":
                run_db("set-cadence-status", "--phone", phone, "--cadence-status", "followup", dry=dry)
            remember(phone, "flow", "human_cold" if is_team else "followup_cadence", dry=dry)
            remember(phone, "followup_week", week + 1, dry=dry)
            remember(phone, "last_followup_date", datetime.now(timezone.utc).astimezone().isoformat(), dry=dry)
        else:
            log(f"{phone}: send refused/failed — cadence NOT advanced")
            skipped += 1

    conn.close()
    log(f"followup_runner done: sent={sent} junked={junked} skipped={skipped}")


if __name__ == "__main__":
    main()
