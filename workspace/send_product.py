#!/usr/bin/env python3
"""
Send one or more products as a SINGLE WhatsApp message each (photo + details).

openclaw's `message send --media` CLI is broken for WhatsApp (it drops the
media), so this script does NOT use it. Instead it:
  1. Looks the product up in data/products.json.
  2. Fetches its image into a per-user temp folder (database/tmp/<phone>/), cached.
  3. Builds the caption (name, price, dimensions, availability, link).
  4. Enqueues a job for the in-process Baileys sender (patched into the gateway),
     which sends image + caption as ONE real media message.
  5. Waits briefly for delivery confirmation.

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
    lines = [f"{p.get('name', 'Product')} - PKR {amount_str}"]
    if p.get("category"):
        lines.append(f"Category: {p['category']}")
    if p.get("dimensions"):
        lines.append(f"Dimensions: {p['dimensions']}")
    lines.append(f"Availability: {p.get('availability', 'In Stock')}")
    if p.get("link"):
        lines.append(p["link"])
    return "\n".join(lines)


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
