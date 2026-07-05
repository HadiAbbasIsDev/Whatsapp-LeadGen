#!/usr/bin/env python3
"""
SQLite data layer for the lead-gen bot — the concurrency-safe source of truth for
customers and leads (replaces hand-edited customers.json / leads.json).

Uses WAL mode + busy_timeout so multiple customers messaging at once can't corrupt
or lose data. Every write also regenerates customers.json (an atomic mirror) so the
in-gateway WhatsApp-label reconciler keeps working unchanged.

CLI (used by the agent's skills):
  python3 db.py init
  python3 db.py upsert-customer --phone +923... [--name N] [--email E] [--category C] [--notes ...]
  python3 db.py set-category --phone +923... --category "hot leads"
  python3 db.py set-cadence-status --phone +923... --cadence-status "followup"
  python3 db.py touch --phone +923...
  python3 db.py add-lead --phone +923... [--name N] [--email E] [--products "a,b"] \
                         [--pain ...] [--intent trial] [--score 60] [--tier Hot] [--notes ...]
  python3 db.py counts
  python3 db.py list-customers [--category "hot leads"]
  python3 db.py export-customers
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB_PATH = os.path.join(DATA, "leadgen.db")
CUSTOMERS_JSON = os.path.join(DATA, "customers.json")
LEADS_JSON = os.path.join(DATA, "leads.json")

CATEGORIES = ["new customer", "important", "hot leads", "followup", "junk", "complaints", "ahsan", "ahmed", "imran", "rafay"]
PKT = timezone(timedelta(hours=5))  # Pakistan time, matches existing timestamps


def now_iso():
    return datetime.now(PKT).isoformat(timespec="seconds")


def connect():
    os.makedirs(DATA, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS customers (
            phone            TEXT PRIMARY KEY,
            name             TEXT,
            email            TEXT,
            category         TEXT DEFAULT 'new customer',
            previous_owner   TEXT,
            cadence_status   TEXT,
            lead_score       INTEGER,
            status           TEXT,
            first_contact_at TEXT,
            last_message_at  TEXT,
            notes            TEXT,
            updated_at       TEXT
        );
        CREATE TABLE IF NOT EXISTS leads (
            id                   TEXT PRIMARY KEY,
            captured_at          TEXT,
            phone                TEXT,
            name                 TEXT,
            email                TEXT,
            products_of_interest TEXT,
            pain_point           TEXT,
            intent               TEXT,
            lead_score           INTEGER,
            score_tier           TEXT,
            status               TEXT,
            notes                TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_customers_category ON customers(category);
        CREATE INDEX IF NOT EXISTS idx_leads_phone ON leads(phone);
        """
    )
    # Add cadence_status and previous_owner columns to existing tables (safe if already present)
    try:
        conn.execute("ALTER TABLE customers ADD COLUMN cadence_status TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE customers ADD COLUMN previous_owner TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()


def norm_category(cat):
    c = (cat or "").strip().lower()
    return c if c in CATEGORIES else None


CADENCE_STATUSES = [None, "followup", "junk"]


def norm_cadence_status(s):
    v = (s or "").strip().lower()
    if v in ("", "none", "null"):
        return None
    if v in ("followup", "junk"):
        return v
    return None  # invalid → treat as clear


def export_customers(conn):
    """Write customers.json atomically so the label reconciler stays in sync."""
    rows = conn.execute(
        "SELECT phone, name, category, first_contact_at, last_message_at, notes FROM customers ORDER BY last_message_at DESC"
    ).fetchall()
    out = {
        "categories": CATEGORIES,
        "customers": [
            {
                "phone": r["phone"],
                "name": r["name"],
                "category": r["category"] or "new customer",
                "first_contact_at": r["first_contact_at"],
                "last_message_at": r["last_message_at"],
                "notes": r["notes"] or "",
            }
            for r in rows
        ],
    }
    tmp = CUSTOMERS_JSON + ".tmp." + str(os.getpid())  # unique per process (no concurrent clobber)
    with open(tmp, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CUSTOMERS_JSON)  # atomic


# ---- operations ------------------------------------------------------
def upsert_customer(conn, phone, name=None, email=None, notes=None, status=None):
    """Write non-category fields only. Category is managed EXCLUSIVELY by set_category().
    This function MUST NOT write to the category column."""
    ts = now_iso()
    conn.execute(
        "INSERT INTO customers (phone,name,email,status,first_contact_at,last_message_at,notes,updated_at) "
        "VALUES (:phone,:name,:email,:status,:ts,:ts,:notes,:ts) "
        "ON CONFLICT(phone) DO UPDATE SET "
        "  name=COALESCE(excluded.name, customers.name), "
        "  email=COALESCE(excluded.email, customers.email), "
        "  status=COALESCE(excluded.status, customers.status), "
        "  notes=COALESCE(NULLIF(excluded.notes,''), customers.notes), "
        "  last_message_at=excluded.last_message_at, "
        "  updated_at=excluded.updated_at",
        {"phone": phone, "name": name, "email": email, "status": status, "ts": ts, "notes": notes or ""},
    )
    conn.commit()
    export_customers(conn)


LOCK_DIR = os.path.join(DATA, ".locks")


def _acquire_lock(phone, timeout=5):
    """Per-phone mutex using atomic mkdir. Returns True if lock acquired."""
    os.makedirs(LOCK_DIR, exist_ok=True)
    lock_path = os.path.join(LOCK_DIR, phone.replace("+", ""))
    deadline = time.time() + timeout
    while True:
        try:
            os.makedirs(lock_path, exist_ok=False)
            return True
        except FileExistsError:
            if time.time() > deadline:
                print(json.dumps({"error": "lock_timeout", "phone": phone}), file=sys.stderr)
                return False
            time.sleep(0.1)


def _release_lock(phone):
    lock_path = os.path.join(LOCK_DIR, phone.replace("+", ""))
    try:
        os.rmdir(lock_path)
    except OSError:
        pass


HUMAN_OWNER_CATS = {"ahsan", "ahmed", "imran", "rafay"}


def set_category(conn, phone, category):
    """SOLE writer of the category column. Per-phone locked, deduped, logged."""
    cat = norm_category(category)
    if not cat:
        sys.exit(f"Invalid category '{category}'. Must be one of: {CATEGORIES}")
    if not _acquire_lock(phone):
        sys.exit(f"Could not acquire lock for {phone} — another write in progress")
    try:
        ts = now_iso()
        # Ensure row exists
        conn.execute(
            "INSERT OR IGNORE INTO customers (phone, first_contact_at, last_message_at, updated_at) VALUES (?,?,?,?)",
            (phone, ts, ts, ts))
        old = conn.execute("SELECT category FROM customers WHERE phone=?", (phone,)).fetchone()
        old_cat = old["category"] if old else None
        # Dedup: skip if already at target category
        if old_cat == cat:
            print(json.dumps({"ok": True, "phone": phone, "category": {"old": old_cat, "new": cat},
                               "dedup": "skipped — already at target", "previous_owner": None}))
            return
        # If moving FROM a human-owner category, save owner to previous_owner
        prev_owner = None
        if old_cat in HUMAN_OWNER_CATS and cat in ("followup", "junk", "complaints", "hot leads"):
            prev_owner = old_cat
        conn.execute(
            "UPDATE customers SET category=?, previous_owner=COALESCE(?, previous_owner), last_message_at=?, updated_at=? WHERE phone=?",
            (cat, prev_owner, ts, ts, phone))
        conn.commit()
        export_customers(conn)
        print(json.dumps({"ok": True, "phone": phone, "category": {"old": old_cat, "new": cat},
                           "previous_owner": prev_owner}))
    finally:
        _release_lock(phone)


def set_cadence_status(conn, phone, cadence_status):
    """Set cadence_status independently of category. Does NOT touch the category column."""
    cs = norm_cadence_status(cadence_status)
    # Ensure row exists without touching category
    ts = now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO customers (phone, first_contact_at, last_message_at, updated_at) VALUES (?,?,?,?)",
        (phone, ts, ts, ts))
    old = conn.execute("SELECT category, cadence_status FROM customers WHERE phone=?", (phone,)).fetchone()
    old_cat = old["category"] if old else None
    old_cs = old["cadence_status"] if old else None
    conn.execute("UPDATE customers SET cadence_status=?, updated_at=? WHERE phone=?", (cs, ts, phone))
    conn.commit()
    export_customers(conn)
    print(json.dumps({"ok": True, "phone": phone,
                       "cadence_status": {"old": old_cs, "new": cs},
                       "category": old_cat}))


def touch(conn, phone):
    upsert_customer(conn, phone)


def add_lead(conn, phone, name=None, email=None, products=None, pain=None, intent=None, score=None, tier=None, notes=None, status="new"):
    lead_id = "LEAD-" + str(int(datetime.now(PKT).timestamp() * 1000))
    if products and not products.strip().startswith("["):
        prod_json = json.dumps([p.strip() for p in products.split(",") if p.strip()])
    else:
        prod_json = products or "[]"
    conn.execute(
        "INSERT INTO leads (id,captured_at,phone,name,email,products_of_interest,pain_point,intent,lead_score,score_tier,status,notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (lead_id, now_iso(), phone, name, email, prod_json, pain, intent, score, tier, status, notes),
    )
    conn.commit()
    # keep the customer record in sync (name/email/score)
    upsert_customer(conn, phone, name=name, email=email)
    if score is not None:
        conn.execute("UPDATE customers SET lead_score=? WHERE phone=?", (score, phone))
        conn.commit()
    print(json.dumps({"ok": True, "lead_id": lead_id}))


def counts(conn):
    rows = conn.execute("SELECT category, COUNT(*) n FROM customers GROUP BY category").fetchall()
    c = {cat: 0 for cat in CATEGORIES}
    for r in rows:
        key = (r["category"] or "new customer").strip().lower()
        if key in c:
            c[key] += r["n"]
    total = conn.execute("SELECT COUNT(*) n FROM customers").fetchone()["n"]
    print(json.dumps({"counts": c, "total": total}))


def list_customers(conn, category=None):
    if category:
        rows = conn.execute("SELECT * FROM customers WHERE LOWER(category)=? ORDER BY last_message_at DESC", (category.strip().lower(),)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM customers ORDER BY last_message_at DESC").fetchall()
    print(json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False))


def do_init(conn):
    init_schema(conn)
    imported_c = imported_l = 0
    # import existing customers.json
    if os.path.exists(CUSTOMERS_JSON):
        try:
            d = json.load(open(CUSTOMERS_JSON))
            for c in d.get("customers", []):
                if not c.get("phone"):
                    continue
                exists = conn.execute("SELECT 1 FROM customers WHERE phone=?", (c["phone"],)).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO customers (phone,name,email,first_contact_at,last_message_at,notes,updated_at) VALUES (?,?,?,?,?,?,?)",
                    (c["phone"], c.get("name"), c.get("email"),
                     c.get("first_contact_at") or now_iso(), c.get("last_message_at") or now_iso(), c.get("notes", ""), now_iso()),
                )
                # Set category through the sole writer
                set_category(conn, c["phone"], norm_category(c.get("category")) or "new customer")
                imported_c += 1
        except Exception as e:
            print(f"[warn] could not import customers.json: {e}", file=sys.stderr)
    # import existing leads.json
    if os.path.exists(LEADS_JSON):
        try:
            d = json.load(open(LEADS_JSON))
            for l in d.get("leads", []):
                if not l.get("id"):
                    continue
                exists = conn.execute("SELECT 1 FROM leads WHERE id=?", (l["id"],)).fetchone()
                if exists:
                    continue
                prods = l.get("products_of_interest")
                conn.execute(
                    "INSERT INTO leads (id,captured_at,phone,name,email,products_of_interest,pain_point,intent,lead_score,score_tier,status,notes) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (l["id"], l.get("captured_at"), l.get("phone"), l.get("name"), l.get("email"),
                     json.dumps(prods) if not isinstance(prods, str) else prods, l.get("pain_point"),
                     l.get("intent"), l.get("lead_score"), l.get("score_tier"), l.get("status", "new"), l.get("notes")),
                )
                imported_l += 1
        except Exception as e:
            print(f"[warn] could not import leads.json: {e}", file=sys.stderr)
    conn.commit()
    export_customers(conn)
    print(json.dumps({"ok": True, "db": DB_PATH, "imported_customers": imported_c, "imported_leads": imported_l}))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")
    sub.add_parser("counts")
    sub.add_parser("export-customers")

    p = sub.add_parser("upsert-customer"); p.add_argument("--phone", required=True)
    p.add_argument("--name"); p.add_argument("--email"); p.add_argument("--category"); p.add_argument("--notes"); p.add_argument("--status")

    p = sub.add_parser("set-category"); p.add_argument("--phone", required=True); p.add_argument("--category", required=True)
    p = sub.add_parser("set-cadence-status"); p.add_argument("--phone", required=True); p.add_argument("--cadence-status", required=True, dest="cadence_status")
    p = sub.add_parser("touch"); p.add_argument("--phone", required=True)
    p = sub.add_parser("list-customers"); p.add_argument("--category")

    p = sub.add_parser("add-lead"); p.add_argument("--phone", required=True)
    for opt in ("name", "email", "products", "pain", "intent", "tier", "notes", "status"):
        p.add_argument("--" + opt)
    p.add_argument("--score", type=int)

    args = ap.parse_args()
    # Retry on transient SQLite lock contention so writes are never dropped.
    last_err = None
    for attempt in range(8):
        conn = connect()
        try:
            dispatch(conn, args)
            return
        except sqlite3.OperationalError as e:
            last_err = e
            if "locked" in str(e).lower() or "busy" in str(e).lower():
                time.sleep(0.25 * (attempt + 1))
                continue
            raise
        finally:
            conn.close()
    print(f"[FAIL] database busy after retries: {last_err}", file=sys.stderr)
    sys.exit(1)


def dispatch(conn, args):
    if args.cmd == "init":
        do_init(conn)
    elif args.cmd == "upsert-customer":
        upsert_customer(conn, args.phone, args.name, args.email, args.notes, args.status)
        if args.category:
            set_category(conn, args.phone, args.category)
        else:
            print(json.dumps({"ok": True}))
    elif args.cmd == "set-category":
        set_category(conn, args.phone, args.category)
    elif args.cmd == "set-cadence-status":
        set_cadence_status(conn, args.phone, args.cadence_status)
    elif args.cmd == "touch":
        touch(conn, args.phone); print(json.dumps({"ok": True}))
    elif args.cmd == "add-lead":
        add_lead(conn, args.phone, args.name, args.email, args.products, args.pain, args.intent, args.score, args.tier, args.notes, args.status or "new")
    elif args.cmd == "counts":
        counts(conn)
    elif args.cmd == "list-customers":
        list_customers(conn, args.category)
    elif args.cmd == "export-customers":
        export_customers(conn); print(json.dumps({"ok": True}))


if __name__ == "__main__":
    main()
