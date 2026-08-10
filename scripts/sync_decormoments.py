#!/usr/bin/env python3
"""
Sync the Decor Moments catalog from the live Shopify store into
workspace/data/products.json (the file the bot reads).

Decor Moments (decormoments.com) is a Shopify store, so its full catalog is at
/products.json (paginated). Each product's `body_html` holds the rich detail —
description, a "Key Features" list, and a Specifications table (material, frame,
dimensions, etc.). We extract all of it so the bot can answer detail questions
and match what a customer is asking for.

Per product we write: id, name, category, price, dimensions, description,
key_features, specifications, keywords (searchable), image, link, availability.

  python3 scripts/sync_decormoments.py
"""
import html
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


def strip_html(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def spec_table(body):
    """Parse the Specifications table into {feature: detail}."""
    specs = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.I | re.S):
        cells = [strip_html(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.I | re.S)]
        cells = [c for c in cells if c]
        if len(cells) >= 2 and cells[0].lower() not in ("feature", "detail", "specification"):
            specs[cells[0]] = cells[1]
    return specs


def key_features(body, name="", spec_keys=()):
    """Bold labels that look like feature names (e.g. 'Premium Velvet Finish:').
    Excludes the product's own name and the spec-table keys (kept separately)."""
    skip = {"product overview", "key features", "specifications", "feature", "detail"}
    skip |= {(name or "").lower()}
    skip |= {k.lower() for k in spec_keys}
    feats = []
    for b in re.findall(r"<(?:b|strong)[^>]*>(.*?)</(?:b|strong)>", body, re.I | re.S):
        t = strip_html(b).rstrip(":").strip()
        if t and t.lower() not in skip and 2 <= len(t) <= 40 and t not in feats \
           and not re.search(r"\.(jpg|jpeg|png|webp)$", t, re.I):
            feats.append(t)
    return feats[:8]


def short_description(body):
    """First real sentence(s) of the description, plain text, capped."""
    text = strip_html(body)
    # drop a leading "<Name>: <tagline>" heading echo if present
    text = re.sub(r"^[^.]{0,60}?:\s*", "", text, count=1)
    return text[:280].rsplit(" ", 1)[0] + ("…" if len(text) > 280 else "")


def dimensions_of(p, specs):
    for k, v in specs.items():
        if re.search(r"dimension|size|measurement", k, re.I):
            return v
    # Variant options are usually colour/config ("Black", "Pink", "Without mirror",
    # "Estimate") — only use one as a dimension if it actually encodes a size, i.e.
    # it contains a number (e.g. "4 seater - 2.5ft by 5ft", "2 Door - 4' by 6.5'").
    for v in p.get("variants") or []:
        for opt in ("option1", "option2"):
            val = (v.get(opt) or "").strip()
            if val and val.lower() != "default title" and re.search(r"\d", val):
                return val
    return ""


def category_of(p):
    pt = (p.get("product_type") or "").strip()
    if pt:
        return pt
    tags = [t for t in p.get("tags", []) if t]
    return ", ".join(tags[:3]) if tags else "Furniture"


def _int_price(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


SEAT_COUNT_RE = re.compile(r"^\s*(\d+(?:\s*\+\s*\d+)*)\s*seaters?\s*$", re.I)


def seat_count(value):
    """Parse a 'Number of Seats' option value into a total seat count, but ONLY
    for genuine continuous-sofa configurations ('2 seater', '3 + 2 Seater',
    '3 + 2 + 1 + 1 Seater'). Deliberately excludes 'Single Seater' and 'Pair of
    Single Seaters' — those are a separate standalone-armchair SKU bundled onto
    the same product page, priced on different economics, and mixing them in
    would corrupt the per-seat rate (verified on real data: they break the
    otherwise-perfectly-linear PKR/seat pattern of the true sofa configs)."""
    m = SEAT_COUNT_RE.match((value or "").strip())
    if not m:
        return None
    return sum(int(x) for x in m.group(1).split("+"))


def per_seat_price(p, variants):
    """If this product has a genuine seat-count option (e.g. Shopify option
    named 'Number of Seats' with values like '2 seater' / '3 + 2 Seater'),
    return (rate_per_seat, True). Each seat-count config is priced separately
    on the site, but they're normally linear (PKR/seat is constant) — take the
    most common ratio (mode), so one-off pricing typos don't skew it; on a
    genuine tie, use the smallest ratio found (favours the customer). Returns
    (None, False) when no such option exists — the product should then be
    quoted as a flat, whole-set price, never 'per seat' (e.g. a sectional with
    a single 'Default Title' variant has ONE price for the entire set)."""
    ratios = []
    for v in variants:
        for opt in ("option1", "option2", "option3"):
            n = seat_count(v.get(opt))
            if n:
                amt = _int_price(v.get("price"))
                if amt:
                    ratios.append(amt / n)
                break
    if not ratios:
        return None, False
    counts = {}
    for r in ratios:
        counts[r] = counts.get(r, 0) + 1
    best = max(counts.values())
    candidates = sorted(r for r, c in counts.items() if c == best)
    return int(round(candidates[0])), True


def price_of(p):
    variants = p.get("variants") or []
    pool = [v for v in variants if v.get("available")] or variants
    rate, is_per_seat = per_seat_price(p, pool)
    if is_per_seat:
        return {"amount": rate, "currency": "PKR"}, True
    amounts = []
    for v in pool:
        amt = _int_price(v.get("price"))
        if amt is not None:
            amounts.append(amt)
    return {"amount": min(amounts) if amounts else None, "currency": "PKR"}, False


def keywords_of(p, specs, feats, category):
    """Flat, lowercase searchable string so the bot can match customer queries."""
    bag = []
    bag += [category]
    bag += p.get("tags", [])
    bag += list(specs.values())
    bag += feats
    words = re.findall(r"[a-zA-Z]{3,}", " ".join(bag).lower())
    stop = {"the", "and", "with", "for", "high", "quality", "solid", "rich", "natural", "any", "your", "detail"}
    seen, out = set(), []
    for w in words:
        if w not in stop and w not in seen:
            seen.add(w)
            out.append(w)
    return " ".join(out[:40])


def map_product(p):
    body = p.get("body_html", "")
    specs = spec_table(body)
    feats = key_features(body, p.get("title", ""), specs.keys())
    category = category_of(p)
    imgs = p.get("images") or []
    variants = p.get("variants") or []
    price, is_per_seat = price_of(p)
    return {
        "id": str(p.get("id")),
        # WhatsApp Business catalogue items are identified by VARIANT id
        # (message.context.referred_product.product_retailer_id). Keep each
        # variant's id, label and OWN price so that when a customer taps a
        # catalogue card we quote the exact figure they saw, not the base price.
        "variants": [{"id": str(v.get("id")),
                      "title": (v.get("title") or "").strip(),
                      "price": _int_price(v.get("price"))}
                     for v in variants if v.get("id")],
        "name": p.get("title", "").strip(),
        "category": category,
        "price": price,
        # True only when the product genuinely has a 'Number of Seats'-style
        # option (see per_seat_price) — determines whether send_product.py
        # quotes 'PKR X per seat' or the flat whole-set price.
        "per_seat": is_per_seat,
        "dimensions": dimensions_of(p, specs),
        "description": short_description(body),
        "key_features": feats,
        "specifications": specs,
        "keywords": keywords_of(p, specs, feats, category),
        "image": imgs[0]["src"] if imgs and imgs[0].get("src") else "",
        "link": f"{BASE}/products/{p.get('handle','')}" if p.get("handle") else BASE,
        "availability": "In Stock" if any(v.get("available") for v in variants) else "Out of Stock",
    }


def main():
    prods = all_products()
    catalog = [map_product(p) for p in prods if p.get("title")]
    data = {
        "_source": "Auto-synced from decormoments.com Shopify /products.json — run scripts/sync_decormoments.py to refresh",
        "_instructions": ("ONLY show products from this file; never invent products. Prices are PKR. "
                          "Use `keywords`, `category`, `key_features` and `specifications` to match what a "
                          "customer asks for. `dimensions`/`specifications` come from the website — if a field "
                          "is empty we don't have it, so say so or offer a human rather than guessing."),
        "catalog": catalog,
        "faq": [
            {"question": "Do you offer home delivery?",
             "answer": "Yes, we deliver to Karachi, Lahore and Islamabad. Delivery is NOT free — the charge is 10% of the order value or Rs 5000, whichever is lower. Delivery time is 10-20 days. For other cities, our team will assist with delivery options."},
            {"question": "How long does delivery take?", "answer": "Typically 10-20 days."},
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
    n = len(catalog)
    print(f"[OK] wrote {n} products to {OUT}")
    print(f"  with dimensions:   {sum(1 for c in catalog if c['dimensions'])}")
    print(f"  with specs table:  {sum(1 for c in catalog if c['specifications'])}")
    print(f"  with key_features: {sum(1 for c in catalog if c['key_features'])}")
    print(f"  with description:  {sum(1 for c in catalog if c['description'])}")


if __name__ == "__main__":
    main()
