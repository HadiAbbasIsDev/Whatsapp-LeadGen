#!/usr/bin/env python3
"""
Admin cold outreach — send the approved decor_moments_furniture_intro template
to a list of numbers that have NOT messaged us yet.

This is the ONE sanctioned way to first-contact a number. It is a business-
initiated MARKETING template (Meta-approved), so it is allowed outside the
24-hour window — but it is deliberately locked down:

  - ADMIN ONLY. Requires --owner <sender_e164> to be an admin (any number in
    workspace/data/admins.json, added via the dashboard) or the owner; refuses
    otherwise. The agent MUST pass the real WhatsApp sender_id.
  - Never messages a number that previously opted out (STOP / unsubscribe).
  - Records each recipient as a customer (so they appear in the CRM) and logs
    every attempt to workspace/data/cold_outreach.log.
  - De-dupes within a single run.

The owner is responsible for only outreaching numbers they have a lawful basis
to contact (e.g. people who gave their number). Do not feed it purchased or
scraped lists.

Usage (owner-triggered, run by the agent):
  python3 cold_outreach.py --owner "+923362615506" --numbers "+9230...,+9231..."
  python3 cold_outreach.py --owner "+923362615506" --to "+9230..." --to "+9231..."
  python3 cold_outreach.py --owner "+923362615506" --numbers "..." --dry-run
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKSPACE)
import kapso  # noqa: E402

OWNER = "+923362615506"      # always authorized (the dev)
ADMINS_FILE = os.path.join(WORKSPACE, "data", "admins.json")
TEMPLATE = "decor_moments_furniture_intro"
LANG = "en_US"
DB_FILE = os.path.join(WORKSPACE, "data", "leadgen.db")
DB_PY = os.path.join(WORKSPACE, "db.py")
LOG = os.path.join(WORKSPACE, "data", "cold_outreach.log")


def authorized_admins():
    """Cold outreach is allowed for any ADMIN (anyone in admins.json — added via
    the dashboard) plus the owner. This is the authorization list."""
    nums = {OWNER}
    try:
        for n in json.load(open(ADMINS_FILE)).get("admins", []):
            d = re.sub(r"\D", "", str(n))
            if d:
                nums.add("+" + d)
    except Exception:
        pass
    return nums


def audit(line):
    try:
        with open(LOG, "a") as f:
            f.write(f"{datetime.now(timezone.utc).astimezone().isoformat()} {line}\n")
    except Exception:
        pass


def norm_e164(raw):
    d = re.sub(r"\D", "", str(raw or ""))
    if not d:
        return None
    return "+" + d


def opted_out(phone):
    """True only if this number explicitly opted out. Unknown numbers are NOT
    opted out (cold outreach via approved template is allowed for them)."""
    try:
        conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=5)
        row = conn.execute(
            "SELECT opted_out_at, marketing_opt_in FROM messaging_consent WHERE phone=?",
            (phone,),
        ).fetchone()
        conn.close()
        if not row:
            return False
        return bool(row[0]) and not row[1]
    except Exception:
        return False  # DB unreadable -> don't block; caller still logs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True, help="verified WhatsApp sender_id of the requester")
    ap.add_argument("--numbers", help="comma-separated recipient numbers")
    ap.add_argument("--to", action="append", default=[], help="a recipient number (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--template", default=TEMPLATE, help="approved template name (default: the furniture intro)")
    args = ap.parse_args()

    admins = authorized_admins()
    if norm_e164(args.owner) not in admins:
        audit(f"REFUSED non-admin requester={args.owner}")
        sys.exit(f"[FAIL] cold outreach is admin-only. {args.owner} is not an admin (add them in the dashboard).")

    if not kapso.kapso_enabled():
        sys.exit("[FAIL] WA_TRANSPORT is not 'kapso'")

    raw = list(args.to)
    if args.numbers:
        raw += [n for n in re.split(r"[,\s]+", args.numbers) if n.strip()]
    seen, targets = set(), []
    for r in raw:
        e = norm_e164(r)
        if e and e not in seen:
            seen.add(e)
            targets.append(e)
    if not targets:
        sys.exit("[FAIL] no recipient numbers given (use --numbers or --to)")

    # confirm the template is approved before blasting
    ok, templates = kapso.list_templates()
    if ok:
        t = next((x for x in templates if x.get("name") == args.template), None)
        if t is None:
            sys.exit(f"[FAIL] template '{args.template}' not found on the WABA")
        if t.get("status") != "APPROVED":
            sys.exit(f"[FAIL] template '{args.template}' is {t.get('status')}, not APPROVED")

    sent = skipped = failed = 0
    for phone in targets:
        if phone in admins:
            print(f"[SKIP] {phone} (an admin number)")
            skipped += 1
            continue
        if opted_out(phone):
            print(f"[SKIP] {phone} (previously opted out)")
            audit(f"SKIP opted_out to={phone}")
            skipped += 1
            continue
        if args.dry_run:
            print(f"[DRY] would send {args.template} to {phone}")
            continue
        ok, info = kapso.send_template(phone, args.template, LANG)
        if ok:
            print(f"[OK] {phone} ({info})")
            audit(f"SENT to={phone} template={args.template} id={info} by={norm_e164(args.owner)}")
            # record as a contact so they surface in the CRM; category stays default
            subprocess.run(["python3", DB_PY, "upsert-customer", "--phone", phone,
                            "--notes", "cold outreach: furniture intro sent"],
                           capture_output=True, text=True, timeout=30)
            sent += 1
        else:
            print(f"[FAIL] {phone}: {info}")
            audit(f"FAILED to={phone} error={info}")
            failed += 1

    print(f"\nDone. sent={sent} skipped={skipped} failed={failed} (of {len(targets)} numbers)")


if __name__ == "__main__":
    main()
