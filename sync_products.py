#!/usr/bin/env python3
"""
Parses database/products and database/images/ then writes workspace/data/products.json.
Run this whenever you add new products to the database folder.

Usage:  python3 sync_products.py
"""

import json, re
from pathlib import Path

BASE     = Path(__file__).parent
DB_FILE  = BASE / "database" / "products"
IMG_DIR  = BASE / "database" / "images"
OUT_FILE = BASE / "workspace" / "data" / "products.json"

FAQ = [
    {
        "question": "Do you offer home delivery?",
        "answer": "Yes! All products include home delivery across Pakistan. Typically 3–7 days for in-stock items."
    },
    {
        "question": "Do you offer installation?",
        "answer": "Yes — installation is available. Please mention it when placing your order."
    },
    {
        "question": "What payment methods do you accept?",
        "answer": "Bank transfer, Easypaisa, JazzCash, and cash on delivery for in-stock items."
    },
    {
        "question": "Do you offer instalment plans?",
        "answer": "Yes — instalment options are available. Ask our team for details."
    },
    {
        "question": "Can I see the furniture in person?",
        "answer": "Absolutely! Reply 'visit' or 'showroom' and I'll share directions and opening hours."
    },
    {
        "question": "How do I speak to a real person?",
        "answer": "Just say 'real person', 'speak to someone', or 'human' — I'll immediately alert our team."
    }
]


def parse_products(text: str) -> list:
    blocks = [b.strip() for b in re.split(r'\n{2,}', text) if b.strip()]
    products = []
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        entry = {}
        for line in lines:
            m = re.match(r'^(link|productname|category|price)\s*:?\s*(.+)$', line, re.IGNORECASE)
            if m:
                entry[m.group(1).lower()] = m.group(2).strip()

        link = entry.get("link", "")
        id_match = re.search(r'item-(\d+)', link)
        if not id_match:
            continue
        item_id = id_match.group(1)

        price_raw = entry.get("price", "0").split('.')[0]
        price_clean = re.sub(r'[^\d]', '', price_raw)
        price_val = int(price_clean) if price_clean else 0

        # Find matching image — use absolute path so openclaw can resolve it
        img_path = None
        for ext in ("jpg", "jpeg", "png", "webp"):
            candidate = IMG_DIR / f"{item_id}.{ext}"
            if candidate.exists():
                img_path = str(candidate.resolve())
                break

        products.append({
            "id": item_id,
            "name": entry.get("productname", "").strip(),
            "category": entry.get("category", "").replace("%", "&").strip(),
            "price": {"amount": price_val, "currency": "PKR"},
            "image": img_path or str((IMG_DIR / f"{item_id}.jpg").resolve()),
            "link": link,
            "availability": "In Stock"
        })
    return products


def main():
    if not DB_FILE.exists():
        raise SystemExit(f"Database file not found: {DB_FILE}")

    text = DB_FILE.read_text(encoding="utf-8")
    catalog = parse_products(text)

    output = {
        "_source": "Auto-synced from database/products — run sync_products.py to refresh",
        "_instructions": "ONLY show products from this file. Never invent products from training memory.",
        "catalog": catalog,
        "faq": FAQ
    }

    OUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[sync] {len(catalog)} products written to {OUT_FILE}")
    for p in catalog:
        img_exists = Path(p["image"]).exists()
        status = "OK" if img_exists else "MISSING IMAGE"
        print(f"  [{status}] {p['id']} — {p['name']} — PKR {p['price']['amount']:,}")


if __name__ == "__main__":
    main()
