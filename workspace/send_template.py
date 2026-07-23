#!/usr/bin/env python3
"""
Send an APPROVED WhatsApp template message via Kapso.

Templates are the ONLY messages WhatsApp accepts outside the 24-hour
customer-service window — use this to re-engage a customer whose window
has closed (e.g. the scheduled follow-up cadences). Free-text sends to
such customers fail; do not retry them as free text.

Usage:
  python3 send_template.py --list
  python3 send_template.py --to "+923001234567" --template decor_moments_interest_followup
  python3 send_template.py --to "+92..." --template <name> --param "value1" --param "value2"

Consent: runs `db.py can-message` first and refuses when the customer has
no active service window and no marketing opt-in. --force skips that check
and is ONLY for owner-directed tests to the owner's own number.

Output: "[OK] template <name> sent to <phone>" or "[FAIL] <reason>".
Every attempt is appended to workspace/data/template_send.log.
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKSPACE)
import kapso  # noqa: E402

DB_PY = os.path.join(WORKSPACE, "db.py")
LOG = os.path.join(WORKSPACE, "data", "template_send.log")


def audit(line):
    try:
        with open(LOG, "a") as f:
            f.write(f"{datetime.now(timezone.utc).astimezone().isoformat()} {line}\n")
    except Exception:
        pass


def consent_allows(phone):
    try:
        r = subprocess.run(["python3", DB_PY, "can-message", "--phone", phone],
                           capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout.strip() or "{}")
        return bool(data.get("allowed")), data.get("reason", "unknown")
    except Exception as e:
        return False, f"consent check failed: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list templates on the WABA")
    ap.add_argument("--to", help="recipient phone, E.164")
    ap.add_argument("--template", help="approved template name")
    ap.add_argument("--lang", default="en_US")
    ap.add_argument("--param", action="append", default=[], help="positional body variable (repeatable)")
    ap.add_argument("--force", action="store_true", help="skip consent check (owner tests only)")
    args = ap.parse_args()

    if not kapso.kapso_enabled():
        sys.exit("[FAIL] WA_TRANSPORT is not 'kapso' — templates go through the Kapso transport only")

    if args.list:
        ok, templates = kapso.list_templates()
        if not ok:
            sys.exit(f"[FAIL] {templates}")
        for t in templates:
            body = next((c.get("text", "") for c in t.get("components", []) if c.get("type") == "BODY"), "")
            print(f"{t['name']}  [{t.get('status')}] lang={t.get('language')}")
            print(f"   {body}")
        return

    if not args.to or not args.template:
        sys.exit("[FAIL] --to and --template are required (or use --list)")

    ok, templates = kapso.list_templates()
    if ok:
        match = next((t for t in templates if t.get("name") == args.template), None)
        if match is None:
            names = ", ".join(t["name"] for t in templates)
            sys.exit(f"[FAIL] no template named '{args.template}'. Available: {names}")
        if match.get("status") != "APPROVED":
            sys.exit(f"[FAIL] template '{args.template}' is {match.get('status')}, not APPROVED")

    if not args.force:
        allowed, reason = consent_allows(args.to)
        if not allowed:
            audit(f"REFUSED to={args.to} template={args.template} reason={reason}")
            sys.exit(f"[FAIL] consent check refused: {reason}")

    sent, info = kapso.send_template(args.to, args.template, args.lang, args.param or None)
    if sent:
        audit(f"SENT to={args.to} template={args.template} id={info}")
        print(f"[OK] template {args.template} sent to {args.to} ({info})")
    else:
        audit(f"FAILED to={args.to} template={args.template} error={info}")
        sys.exit(f"[FAIL] {info}")


if __name__ == "__main__":
    main()
