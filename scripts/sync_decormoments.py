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


DIM_JUNK = {"", "estimate", "estimated", "n/a", "na", "-", "tbd",
            "estimated size", "estimated size (imperial range)"}
# a real measurement: a number followed by a unit, OR an "A x B" / "A by B" pair
MEASUREMENT_RE = re.compile(
    r"\d\s*(?:ft|feet|foot|in\b|inch|inches|cm|mm|\bm\b|['\"”′″])"
    r"|\d\s*(?:x|×|by)\s*\d", re.I)
DIM_AXIS_RE = re.compile(r"\b(width|depth|height|length|diameter)\b", re.I)
DIM_LETTER_RE = re.compile(r"^\s*([WDHL])\b", re.I)
LETTER_AXIS = {"W": "Width", "D": "Depth", "H": "Height", "L": "Length"}


def _is_measurement(v):
    v = (v or "").strip()
    return bool(v) and v.lower() not in DIM_JUNK and MEASUREMENT_RE.search(v) is not None


def _axis_label(k, axis_word):
    """Build a readable axis label from a spec key, keeping any descriptive
    prefix. 'W (Width)' -> 'Width'; 'Desk W (Width)' -> 'Desk Width';
    'Shelf W (Width)' -> 'Shelf Width'; 'H (Total System Height)' -> 'Height'."""
    key_clean = re.sub(r"\([^)]*\)", "", k).strip()          # drop the "(Width)" part
    prefix = re.sub(r"\b[WDHL]\b\s*$", "", key_clean, flags=re.I).strip()
    if not prefix or prefix.lower() == axis_word.lower():
        return axis_word
    if prefix.lower().endswith(axis_word.lower()):
        return prefix
    return f"{prefix} {axis_word}"


def _dims_from_spec_axes(specs):
    """Assemble per-axis rows (W/Width, D/Depth, H/Height, L/Length) into one
    clean string. Handles tables like: 'Dimension | Estimate', 'W (Width) | 60 in
    (152 cm)', 'D (Depth) | 30 in (76 cm)' — where the real numbers live in the
    axis rows, NOT the 'Dimension' header row (whose value is often 'Estimate').
    Keeps descriptive prefixes so 'Desk W' and 'Shelf W' don't both become 'Width'."""
    parts = []
    for k, v in specs.items():
        val = (v or "").strip()
        if not _is_measurement(val):
            continue
        axis = DIM_AXIS_RE.search(k)
        letter = DIM_LETTER_RE.match(k)
        if axis:
            axis_word = axis.group(1).capitalize()
        elif letter:
            axis_word = LETTER_AXIS.get(letter.group(1).upper())
        else:
            continue
        entry = f"{_axis_label(k, axis_word)}: {val}"
        if entry not in parts:
            parts.append(entry)
    return ", ".join(parts)


def _dims_from_options(p):
    """Some products encode size in an option's values, one size PER
    configuration — dining sets ('Dinning Table Seats': '4 seater - 2.5ft by
    5ft' ...) and bedroom wardrobes ('Cupboard & Wardrobe': 'Without Cupboard',
    "2 Door - 4' by 6.5'", ...). Present every SIZED value clearly (dropping
    non-size choices like 'Without Cupboard'), keeping the config as a
    meaningful label. Requires >=2 sized values so a stray number in, say, a
    colour option can't masquerade as a dimension."""
    for opt in p.get("options") or []:
        vals = [str(x).strip() for x in (opt.get("values") or []) if str(x).strip()]
        sized = [v for v in vals if _is_measurement(v)]
        if len(sized) < 2:
            continue
        out = []
        for v in sized:
            m = re.match(r"\s*(.+?)\s*[-–—:]\s*(.+)", v)   # "<config> - <size>"
            if m and MEASUREMENT_RE.search(m.group(2)):
                cfg = re.sub(r"\s+", " ", m.group(1)).strip()
                size = re.sub(r"\s+by\s+", " x ", m.group(2).strip(), flags=re.I)
                out.append(f"{cfg}: {size}")
            else:
                out.append(re.sub(r"\s+by\s+", " x ", v, flags=re.I))
        if out:
            return "; ".join(out)
    return ""


def dimensions_of(p, specs):
    # 1. Real per-axis measurements from the spec table (W/D/H/L rows).
    axes = _dims_from_spec_axes(specs)
    if axes:
        return axes
    # 2. A single 'Dimensions/Size/Measurement' spec value — but only if it is an
    #    actual measurement, never a placeholder like 'Estimate'.
    for k, v in specs.items():
        if re.search(r"dimension|size|measurement", k, re.I) and _is_measurement(v):
            return v.strip()
    # 3. Per-configuration size options (dining sets etc.).
    return _dims_from_options(p)


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
SINGLE_SEATER_RE = re.compile(r"^\s*single\s*seater\s*$", re.I)
PAIR_SEATER_RE = re.compile(r"^\s*pair\s*of\s*single\s*seaters?\s*$", re.I)


def seat_count(value):
    """Parse a 'Number of Seats' option value into a total seat count — covers
    every real form seen on the site: 'Single Seater' (1), 'Pair of Single
    Seaters' (2), 'N seater', and 'N + M [+ ...] Seater' (summed)."""
    v = (value or "").strip()
    if SINGLE_SEATER_RE.match(v):
        return 1
    if PAIR_SEATER_RE.match(v):
        return 2
    m = SEAT_COUNT_RE.match(v)
    if not m:
        return None
    return sum(int(x) for x in m.group(1).split("+"))


def per_seat_price(p, variants):
    """Figure out how to price a product that has a seat-count option.

    Three cases:
    1. A genuine 1-seat purchase exists ('Single Seater' variant, in stock) —
       that IS something a customer can actually buy standalone, so quote its
       own price 'per seat'. Uses the CHEAPEST such 1-seat variant's own price
       (not a bulk/marginal rate computed from larger configs — verified: for
       Single Seater 45k / 2 seater 70k / 3 seater 105k / 3+2 175k / 3+2+1+1
       245k, the 2-seater-and-up rate is a clean 35k/seat, but nothing is
       actually SOLD at 35k, so quoting that would advertise an unbuyable
       price; 45k is the real minimum purchase).
    2. A seat-count option exists but the SMALLEST configuration needs 2+
       seats (e.g. only '2 seater' / '3 seater', no standalone chair) —
       calling this 'PKR X per seat' would imply a single seat is buyable
       when it is not (confirmed real case: Sleevo/Veloura/Convertix BedSofa,
       2 Seater is the minimum purchase). Quote the FLAT price of the
       cheapest configuration instead, labelled with which configuration it
       is, so the customer knows the starting price is for that whole unit.
    3. No seat-count option at all — handled by the caller (price_of), not
       here: quote the flat single-variant price, no seat/config label.

    Returns (amount, per_seat: bool, config_label: str|None).
    """
    priced = []          # (price, seat_count) for every seat-labelled variant
    for v in variants:
        for opt in ("option1", "option2", "option3"):
            n = seat_count(v.get(opt))
            if n:
                amt = _int_price(v.get("price"))
                if amt:
                    priced.append((amt, n, (v.get(opt) or "").strip()))
                break
    if not priced:
        return None, False, None

    singles = [t for t in priced if t[1] == 1]
    if singles:
        amt, n, _ = min(singles, key=lambda t: t[0])
        return int(round(amt / n)), True, None

    amt, n, label = min(priced, key=lambda t: t[0])
    return amt, False, label


def price_of(p):
    variants = p.get("variants") or []
    pool = [v for v in variants if v.get("available")] or variants
    amount, is_per_seat, config_label = per_seat_price(p, pool)
    if amount is not None:
        return {"amount": amount, "currency": "PKR"}, is_per_seat, config_label
    amounts = []
    for v in pool:
        amt = _int_price(v.get("price"))
        if amt is not None:
            amounts.append(amt)
    return {"amount": min(amounts) if amounts else None, "currency": "PKR"}, False, None


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
    price, is_per_seat, config_label = price_of(p)
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
        # True only when a genuine standalone 1-seat purchase exists (see
        # per_seat_price) — determines whether send_product.py quotes
        # 'PKR X per seat' or the flat starting price.
        "per_seat": is_per_seat,
        # Set when the product has a seat-count option but the CHEAPEST
        # buyable configuration needs 2+ seats (e.g. 'Sleevo BedSofa' only
        # offers 2/3 seater, no standalone chair) — the flat price shown is
        # for THIS configuration, not a single seat, so send_product.py
        # labels it (e.g. "PKR 66,000 (2 seater)") instead of implying a
        # smaller unit is purchasable.
        "price_config": config_label,
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
