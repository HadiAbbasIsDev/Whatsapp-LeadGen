#!/usr/bin/env python3
"""
ADMIN-ONLY lead list by label.

Lets an admin ask things like "give me the numbers of all hot leads" or
"list the junk leads from the last 36 hours" and get a clean, copy-pasteable
list of phone numbers.

  python3 list_leads.py --requester "+923362615506" --category "hot leads"
  python3 list_leads.py --requester "+92..." --category junk --hours 36
  python3 list_leads.py --requester "+92..." --counts          # all label totals

Hard-gated: refuses unless --requester is a number in workspace/data/admins.json,
so a customer can never extract the customer list through the bot.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(WORKSPACE, "data", "leadgen.db")
ADMINS_FILE = os.path.join(WORKSPACE, "data", "admins.json")
PKT = timezone(timedelta(hours=5))

# Kept in sync with db.py CATEGORIES
CATEGORIES = ["new customer", "important", "hot leads", "followup", "junk",
              "complaints", "vendor", "ahsan", "ahmed", "imran", "rafay",
              "order confirmed"]


def digits(v):
    return re.sub(r"\D", "", str(v or ""))


def is_admin(phone):
    d = digits(phone)
    if not d:
        return False
    try:
        admins = json.load(open(ADMINS_FILE)).get("admins", [])
    except Exception:
        return False
    return d in {digits(a) for a in admins}


def norm_category(value):
    """Accept loose wording: 'hot lead', 'Hot Leads', 'follow up', 'junk'."""
    v = re.sub(r"[\s_-]+", " ", str(value or "").strip().lower())
    if v in CATEGORIES:
        return v
    aliases = {
        "hot lead": "hot leads", "hotleads": "hot leads", "hot": "hot leads",
        "follow up": "followup", "follow-up": "followup", "followups": "followup",
        "complaint": "complaints", "vendors": "vendor",
        "new": "new customer", "new customers": "new customer",
        "order": "order confirmed", "orders": "order confirmed",
        "confirmed": "order confirmed",
    }
    return aliases.get(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requester", required=True,
                    help="the sender_id of whoever asked (must be an admin)")
    ap.add_argument("--category", help="label to list, e.g. 'hot leads' / junk")
    ap.add_argument("--hours", type=float,
                    help="only those whose label changed in the last N hours")
    ap.add_argument("--counts", action="store_true",
                    help="show how many leads are in every label")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()

    if not is_admin(args.requester):
        print("[FAIL] this is admin-only. Ask the owner to add you in the dashboard.")
        return 1

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row

    if args.counts:
        rows = conn.execute(
            "SELECT category, COUNT(*) n FROM customers GROUP BY category ORDER BY n DESC"
        ).fetchall()
        total = sum(r["n"] for r in rows)
        print(f"Leads by label (total {total}):")
        for r in rows:
            print(f"  {r['category'] or 'unlabelled'}: {r['n']}")
        return 0

    if not args.category:
        print("[FAIL] give --category (or --counts). "
              f"Valid labels: {', '.join(CATEGORIES)}")
        return 1
    cat = norm_category(args.category)
    if not cat:
        print(f"[FAIL] '{args.category}' is not a label. "
              f"Valid labels: {', '.join(CATEGORIES)}")
        return 1

    sql = "SELECT phone, name, updated_at FROM customers WHERE category=?"
    params = [cat]
    if args.hours:
        cutoff = (datetime.now(PKT) - timedelta(hours=args.hours)).isoformat(timespec="seconds")
        sql += " AND updated_at >= ?"
        params.append(cutoff)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()

    window = f" (label changed in the last {args.hours:g}h)" if args.hours else ""
    if not rows:
        print(f"No leads labelled '{cat}'{window}.")
        return 0
    print(f"{len(rows)} lead(s) labelled '{cat}'{window}:")
    for r in rows:
        name = (r["name"] or "").strip()
        print(f"  {r['phone']}" + (f"  — {name}" if name else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
