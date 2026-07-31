#!/usr/bin/env python3
"""
Sync the Decor Moments catalog from the live Shopify store into
workspace/data/products.json (the file the bot reads).

Decor Moments (decormoments.com) is a Shopify store, so its full catalog is
available at /products.json (paginated). We map each product to the same shape
the bot already expects: id, name, category, price, dimensions, image, link,
availability. Re-run any time to refresh from the live store.

  python3 scripts/sync_decormoments.py
"""
import json
import os
import re
import urllib.request

BASE = "https://decormoments.com"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "workspace", "data", "products.json")


def fetch_page(page):
    req = urllib.request.Request(f"{BASE}/products.json?limit=250&page={page}",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r).get("products", [])


def all_products():
    out, page = [], 1
    while page <= 50:
        got = fetch_page(page)
        if not got:
            break
        out += got
        page += 1
    return out


def category_of(p):
    pt = (p.get("product_type") or "").strip()
    if pt:
        return pt
    tags = [t for t in p.get("tags", []) if t]
    return ", ".join(tags[:3]) if tags else "Furniture"


def price_of(p):
    # lowest available variant price (fall back to first variant)
    variants = p.get("variants") or []
    avail = [v for v in variants if v.get("available")]
    pool = avail or variants
    amounts = []
    for v in pool:
        try:
            amounts.append(int(round(float(v.get("price")))))
        except (TypeError, ValueError):
            pass
    return {"amount": min(amounts) if amounts else None, "currency": "PKR"}


def dimensions_of(p):
    # use a size-like variant option if present (skip Shopify's "Default Title")
    for v in p.get("variants") or []:
        for k in ("option1", "option2"):
            val = (v.get(k) or "").strip()
            if val and val.lower() != "default title":
                return val
    return ""


def map_product(p):
    handle = p.get("handle", "")
    imgs = p.get("images") or []
    variants = p.get("variants") or []
    available = any(v.get("available") for v in variants)
    return {
        "id": str(p.get("id")),
        "name": p.get("title", "").strip(),
        "category": category_of(p),
        "price": price_of(p),
        "dimensions": dimensions_of(p),
        "image": imgs[0]["src"] if imgs and imgs[0].get("src") else "",
        "link": f"{BASE}/products/{handle}" if handle else BASE,
        "availability": "In Stock" if available else "Out of Stock",
    }


def main():
    prods = all_products()
    catalog = [map_product(p) for p in prods if p.get("title")]
    data = {
        "_source": "Auto-synced from decormoments.com Shopify /products.json — run scripts/sync_decormoments.py to refresh",
        "_instructions": "ONLY show products from this file. Never invent products from training memory. Prices are in PKR.",
        "catalog": catalog,
        "faq": [
            {"question": "Do you offer home delivery?",
             "answer": "Yes, we deliver to Karachi, Lahore and Islamabad. Delivery is NOT free — the charge is 10% of the order value or Rs 5000, whichever is lower. Delivery time is 10-20 days. For other cities, our team will assist with delivery options."},
            {"question": "How long does delivery take?",
             "answer": "Typically 10-20 days."},
            {"question": "What payment methods do you accept?",
             "answer": "Bank transfer, Easypaisa, JazzCash, and cash on delivery for in-stock items."},
            {"question": "Can I see the furniture in person?",
             "answer": "Reply 'visit' or 'showroom' and I'll connect you with our team for details."},
            {"question": "How do I speak to a real person?",
             "answer": "Just say 'real person', 'speak to someone', or 'human' — I'll immediately alert our team."},
        ],
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    priced = sum(1 for c in catalog if c["price"]["amount"] is not None)
    print(f"[OK] wrote {len(catalog)} products to {OUT} ({priced} with prices)")
    cats = {}
    for c in catalog:
        cats[c["category"]] = cats.get(c["category"], 0) + 1
    for k, v in sorted(cats.items(), key=lambda x: -x[1])[:12]:
        print(f"  {v:4}  {k}")


if __name__ == "__main__":
    main()
