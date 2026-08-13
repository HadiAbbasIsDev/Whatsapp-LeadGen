#!/usr/bin/env python3
"""
ADMIN-ONLY bulk relabel.

For requests like "all leads that haven't responded in 48h and are labelled
New Customer or Followup — relabel them as Junk". Doing that one customer at a
time costs one LLM turn each and blows the agent's run timeout, so this does the
whole thing in ONE command.

  # preview (safe, changes nothing)
  python3 bulk_relabel.py --requester "+92..." --from "new customer,followup" \
      --to junk --no-reply-hours 48

  # actually apply
  python3 bulk_relabel.py --requester "+92..." --from "new customer,followup" \
      --to junk --no-reply-hours 48 --apply

Safety:
  - refuses unless --requester is in workspace/data/admins.json
  - DRY RUN by default; nothing changes without --apply
  - never touches human-owned chats (ahsan/ahmed/imran/rafay) or `order confirmed`
    unless they are named explicitly in --from
  - goes through db.py set-category, so WhatsApp labels + the inbound gate stay
    in sync and every change is logged
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(WORKSPACE, "data", "leadgen.db")
ADMINS_FILE = os.path.join(WORKSPACE, "data", "admins.json")
DB_PY = os.path.join(WORKSPACE, "db.py")
PKT = timezone(timedelta(hours=5))

CATEGORIES = ["new customer", "important", "hot leads", "followup", "junk",
              "complaints", "vendor", "ahsan", "ahmed", "imran", "rafay",
              "order confirmed"]
# Never swept up implicitly — a human owns these, or the sale is done.
PROTECTED = {"ahsan", "ahmed", "imran", "rafay", "order confirmed", "complaints"}

ALIASES = {"hot lead": "hot leads", "hotleads": "hot leads", "follow up": "followup",
           "follow-up": "followup", "followups": "followup", "new": "new customer",
           "new customers": "new customer", "complaint": "complaints",
           "vendors": "vendor", "order": "order confirmed", "orders": "order confirmed"}


def digits(v):
    return re.sub(r"\D", "", str(v or ""))


def is_admin(phone):
    d = digits(phone)
    if not d:
        return False
    try:
        return d in {digits(a) for a in json.load(open(ADMINS_FILE)).get("admins", [])}
    except Exception:
        return False


def norm_cat(value):
    v = re.sub(r"[\s_-]+", " ", str(value or "").strip().lower())
    return v if v in CATEGORIES else ALIASES.get(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requester", required=True, help="sender_id of the admin asking")
    ap.add_argument("--from", dest="from_cats", required=True,
                    help="comma-separated labels to move FROM, e.g. 'new customer,followup'")
    ap.add_argument("--to", required=True, help="label to move TO, e.g. junk")
    ap.add_argument("--no-reply-hours", type=float, required=True,
                    help="only chats with no inbound message from the customer in the last N hours")
    ap.add_argument("--apply", action="store_true", help="actually make the changes (default: preview)")
    ap.add_argument("--include-buyers", action="store_true",
                    help="also sweep customers who have an order/lead on record (default: protect them)")
    ap.add_argument("--limit", type=int, default=1000)
    args = ap.parse_args()

    if not is_admin(args.requester):
        print("[FAIL] admin-only. Ask the owner to add you in the dashboard.")
        return 1

    to_cat = norm_cat(args.to)
    if not to_cat:
        print(f"[FAIL] '{args.to}' is not a valid label. Valid: {', '.join(CATEGORIES)}")
        return 1
    from_cats = []
    for raw in args.from_cats.split(","):
        if not raw.strip():
            continue
        c = norm_cat(raw)
        if not c:
            print(f"[FAIL] '{raw.strip()}' is not a valid label. Valid: {', '.join(CATEGORIES)}")
            return 1
        from_cats.append(c)
    if not from_cats:
        print("[FAIL] give at least one --from label")
        return 1

    # Protected labels only move when named explicitly (they are here, by definition).
    swept_protected = [c for c in from_cats if c in PROTECTED]

    cutoff = (datetime.now(PKT) - timedelta(hours=args.no_reply_hours)).isoformat(timespec="seconds")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    q = ("SELECT c.phone, c.name, c.category, mc.last_inbound_at "
         "FROM customers c LEFT JOIN messaging_consent mc ON mc.phone = c.phone "
         f"WHERE c.category IN ({','.join('?' * len(from_cats))}) "
         "AND (mc.last_inbound_at IS NULL OR mc.last_inbound_at < ?) "
         "ORDER BY mc.last_inbound_at IS NULL, mc.last_inbound_at ASC LIMIT ?")
    rows = conn.execute(q, (*from_cats, cutoff, args.limit)).fetchall()
    rows = [r for r in rows if r["category"] != to_cat]

    # Never junk someone who has actually ordered / been captured as a lead just
    # because they went quiet — a real PKR 975,000 catalog order once sat in
    # 'new customer' with no reply, and a blind 48h sweep would have binned it.
    buyers = {digits(r[0]) for r in conn.execute("SELECT phone FROM leads")}
    try:
        alerts = json.load(open(os.path.join(WORKSPACE, "data", "admin_alerts.json")))
        buyers |= {digits(a.get("customer_phone")) for a in alerts.get("alerts", [])
                   if a.get("handoff_type") == "order"}
    except Exception:
        pass
    skipped_buyers = [r for r in rows if digits(r["phone"]) in buyers]
    if not args.include_buyers:
        rows = [r for r in rows if digits(r["phone"]) not in buyers]

    label = f"no customer reply in {args.no_reply_hours:g}h"
    if not rows:
        print(f"No chats match: {' / '.join(from_cats)} with {label}. Nothing to do.")
        return 0

    if not args.apply:
        print(f"PREVIEW — {len(rows)} chat(s) would move to '{to_cat}' ({' / '.join(from_cats)}, {label}):")
        for r in rows[:25]:
            nm = f"  — {r['name']}" if (r["name"] or "").strip() else ""
            print(f"  {r['phone']}  [{r['category']}]{nm}")
        if len(rows) > 25:
            print(f"  … and {len(rows) - 25} more")
        if skipped_buyers:
            print(f"PROTECTED (has an order/lead on record, NOT moved): {len(skipped_buyers)}")
            for r in skipped_buyers[:10]:
                print(f"  {r['phone']}  [{r['category']}]")
        if swept_protected:
            print(f"NOTE: includes human-owned/finished labels: {', '.join(swept_protected)}")
        print("\nNothing changed. Re-run with --apply to make these changes.")
        return 0

    moved = failed = 0
    for r in rows:
        p = subprocess.run(["python3", DB_PY, "set-category", "--phone", r["phone"],
                            "--category", to_cat], capture_output=True, text=True, timeout=60)
        if p.returncode == 0:
            moved += 1
        else:
            failed += 1
            print(f"  [FAIL] {r['phone']}: {(p.stderr or p.stdout).strip()[:100]}")
    print(f"Done. Moved {moved} chat(s) to '{to_cat}'." + (f" {failed} failed." if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
