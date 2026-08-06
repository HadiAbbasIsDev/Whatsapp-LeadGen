#!/usr/bin/env python3
"""
Send a WhatsApp interactive message with quick-reply buttons via Kapso.

Usage:
  python3 send_quick_replies.py --to "+923001234567" --text "Where is your showroom?" --buttons "Karachi:/karachi"
  python3 send_quick_replies.py --to "+92..." --text "..." --buttons "Option1:/id1,Option2:/id2"

Buttons: comma-separated "Title:/id" pairs. Max 3 buttons.
Output: "[OK] interactive sent to <phone>" or "[FAIL] <reason>".
"""

import argparse
import json
import os
import sys

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKSPACE)
import kapso


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, help="recipient phone, E.164")
    ap.add_argument("--text", required=True, help="body text")
    ap.add_argument("--buttons", required=True, help="comma-separated Title:/id pairs, e.g. 'Karachi:/karachi,Lahore:/lahore'")
    ap.add_argument("--header", help="optional header text (max 60 chars)")
    ap.add_argument("--footer", help="optional footer text (max 60 chars)")
    args = ap.parse_args()

    if not kapso.kapso_enabled():
        sys.exit("[FAIL] WA_TRANSPORT is not 'kapso'")

    buttons = []
    for pair in args.buttons.split(","):
        pair = pair.strip()
        if not pair:
            continue
        parts = pair.split(":", 1)
        if len(parts) != 2:
            sys.exit(f"[FAIL] invalid button format '{pair}' — use 'Title:/id'")
        buttons.append({"id": parts[1].strip(), "title": parts[0].strip()})

    ok, info = kapso.send_interactive(args.to, args.text, buttons, args.header, args.footer)
    if ok:
        print(f"[OK] interactive sent to {args.to} ({info})")
    else:
        sys.exit(f"[FAIL] {info}")


if __name__ == "__main__":
    main()
