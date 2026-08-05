#!/usr/bin/env python3
"""
Send one or more products as a SINGLE WhatsApp message each (photo + details).

Transport is selected by WA_TRANSPORT in the repo .env:

  baileys (default) — openclaw's `message send --media` CLI is broken for
  WhatsApp on this pin, so jobs are enqueued for the in-process Baileys sender
  (patched into the gateway) and this script waits for delivery confirmation.

  kapso — images are sent directly through the Kapso Cloud API as link-based
  media (the catalog's the store website URLs are already public), synchronously.
  Same [OK]/[FAIL] per-product output contract; a per-(phone,product) 5-minute
  dedupe is kept here because the in-gateway dedupe map does not exist on Kapso.

Usage:
  python3 send_product.py --to +923XXXXXXXXX --ids 6203,6201,6205
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import uuid

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(WORKSPACE)
PRODUCTS_FILE = os.path.join(WORKSPACE, "data", "products.json")
DB_IMAGES_DIR = os.path.join(REPO, "database", "images")
TMP_ROOT = os.path.join(REPO, "database", "tmp")

QUEUE_FILE = os.path.expanduser("~/.openclaw/wa-media-queue.jsonl")
DONE_FILE = os.path.expanduser("~/.openclaw/wa-media-done.jsonl")


def safe_phone(to):
    return re.sub(r"[^0-9]", "", to) or "unknown"


def fetch_image(image, user_dir):
    """Return a local file path for the product image, or None if unavailable."""
    image = (image or "").strip()
    if not image:
        return None
    if image.lower().startswith(("http://", "https://")):
        os.makedirs(user_dir, exist_ok=True)
        base = os.path.basename(image.split("?")[0]) or "img"
        if "." not in base:
            base += ".jpg"
        dest = os.path.join(user_dir, base)
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return dest
        try:
            req = urllib.request.Request(image, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f)
            if os.path.getsize(dest) > 0:
                return dest
            os.remove(dest)
        except Exception as e:
            print(f"[warn] download failed for {image}: {e}", file=sys.stderr)
        return None
    if os.path.exists(image):
        return image
    base = os.path.basename(image.replace("\\", "/"))
    if base:
        local = os.path.join(DB_IMAGES_DIR, base)
        if os.path.exists(local):
            return local
    return None


def caption_for(p):
    price = p.get("price")
    amount = price.get("amount") if isinstance(price, dict) else price
    try:
        amount_str = f"{int(amount):,}"
    except (TypeError, ValueError):
        amount_str = str(amount)
    # Sofas are priced PER SEAT — always say so on the price line to avoid confusion.
    per = " per seat" if "sofa" in str(p.get("category", "")).lower() else ""
    lines = [f"{p.get('name', 'Product')} - PKR {amount_str}{per}"]
    if p.get("category"):
        lines.append(f"Category: {p['category']}")
    desc = (p.get("description") or "").strip()
    if desc:
        lines.append(desc[:220].rsplit(" ", 1)[0] + ("…" if len(desc) > 220 else ""))
    if p.get("dimensions"):
        lines.append(f"Dimensions: {p['dimensions']}")
    lines.append(f"Availability: {p.get('availability', 'In Stock')}")
    if p.get("link"):
        lines.append(p["link"])
    return "\n".join(lines)


DEDUPE_FILE = os.path.join(WORKSPACE, "data", ".kapso_sent_dedupe.json")
DEDUPE_WINDOW = 300  # seconds, mirrors the old in-gateway 5-minute dedupe


def _dedupe_load(lock_fh):
    import fcntl
    fcntl.flock(lock_fh, fcntl.LOCK_EX)
    now = time.time()
    try:
        state = json.load(open(DEDUPE_FILE))
    except Exception:
        state = {}
    return {k: v for k, v in state.items() if now - v < DEDUPE_WINDOW}


def _dedupe_save(state):
    try:
        tmp = DEDUPE_FILE + f".tmp.{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, DEDUPE_FILE)
    except Exception:
        pass


def kapso_send_products(to, products):
    """Send each product via the Kapso Cloud API. Returns count of confirmed sends.
    The dedupe key is recorded only AFTER a confirmed send — a failed send must
    stay retryable, and '[OK] <id> sent' must never be printed for an
    undelivered message (AGENTS.md anti-hallucination contract)."""
    from kapso import send_image, send_text
    os.makedirs(os.path.dirname(DEDUPE_FILE), exist_ok=True)
    lock_fh = open(DEDUPE_FILE + ".lock", "w")  # serializes concurrent senders
    state = _dedupe_load(lock_fh)
    sent = 0
    for pid, p in products:
        key = f"{safe_phone(to)}#{pid}"
        if key in state:
            print(f"[OK] {pid} sent")  # dedupe hit: it was genuinely delivered within the window
            sent += 1
            continue
        caption = caption_for(p)
        image = (p.get("image") or "").strip()
        text_only = not image.lower().startswith(("http://", "https://"))
        if text_only:
            # No public image URL — send details as text so the customer still gets the info
            ok, info = send_text(to, caption)
        else:
            ok, info = send_image(to, image, caption)
        if ok:
            sent += 1
            state[key] = time.time()  # claim only after confirmed delivery
            _dedupe_save(state)
            print(f"[OK] {pid} sent" + (" (text-only, no public image)" if text_only else ""))
        else:
            print(f"[FAIL] {pid} send error: {info}", file=sys.stderr)
    lock_fh.close()
    return sent


def enqueue(job):
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)
    with open(QUEUE_FILE, "a") as f:
        f.write(json.dumps(job) + "\n")


def wait_for(job_ids, timeout=20):
    """Poll the done file for each job id. Returns dict id->ok(bool)."""
    results = {}
    deadline = time.time() + timeout
    while time.time() < deadline and len(results) < len(job_ids):
        try:
            with open(DONE_FILE) as f:
                for line in f:
                    try:
                        d = json.loads(line)
                    except Exception:
                        continue
                    if d.get("id") in job_ids and d["id"] not in results:
                        results[d["id"]] = bool(d.get("ok"))
        except FileNotFoundError:
            pass
        if len(results) < len(job_ids):
            time.sleep(0.5)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True, help="Recipient E.164 phone number")
    ap.add_argument("--ids", required=True, help="Comma-separated product id(s)")
    args = ap.parse_args()

    try:
        catalog = {p["id"]: p for p in json.load(open(PRODUCTS_FILE))["catalog"]}
    except Exception as e:
        print(f"[FAIL] could not read products.json: {e}", file=sys.stderr)
        sys.exit(1)

    user_dir = os.path.join(TMP_ROOT, safe_phone(args.to))
    ids = [i.strip() for i in args.ids.split(",") if i.strip()]

    try:
        from kapso import kapso_enabled
        use_kapso = kapso_enabled()
    except Exception:
        use_kapso = False

    if use_kapso:
        products = [(pid, catalog[pid]) for pid in ids if pid in catalog]
        for pid in ids:
            if pid not in catalog:
                print(f"[skip] unknown product id: {pid}", file=sys.stderr)
        if not products:
            print("[FAIL] no valid products to send", file=sys.stderr)
            sys.exit(1)
        sent = kapso_send_products(args.to, products)
        print(f"[done] {sent}/{len(products)} confirmed sent to {args.to}")
        sys.exit(0 if sent else 1)

    jobs = {}  # job_id -> product id
    for pid in ids:
        p = catalog.get(pid)
        if not p:
            print(f"[skip] unknown product id: {pid}", file=sys.stderr)
            continue
        caption = caption_for(p)
        media = fetch_image(p.get("image", ""), user_dir)
        job_id = uuid.uuid4().hex
        enqueue({"id": job_id, "to": args.to, "image": media, "caption": caption, "productId": pid})
        jobs[job_id] = pid

    if not jobs:
        print("[FAIL] no valid products to send", file=sys.stderr)
        sys.exit(1)

    results = wait_for(list(jobs.keys()))
    sent = 0
    for job_id, pid in jobs.items():
        ok = results.get(job_id)
        if ok:
            sent += 1
            print(f"[OK] {pid} sent")
        elif ok is False:
            print(f"[FAIL] {pid} send error", file=sys.stderr)
        else:
            print(f"[PENDING] {pid} queued (no confirmation yet)", file=sys.stderr)

    print(f"[done] {sent}/{len(jobs)} confirmed sent to {args.to}")
    sys.exit(0 if sent else 1)


if __name__ == "__main__":
    main()
