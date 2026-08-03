#!/usr/bin/env python3
"""
Deterministic catalog search — returns the product ids that ACTUALLY match a
customer's request, so the bot never has to eyeball products.json and guess.
Matches by CATEGORY (with a synonym map), filtered by price, cheapest first.

The bot runs this, then passes the printed ids straight to send_product.py.

  python3 search_products.py --query "sofa" --max-price 100000
  python3 search_products.py --query "bed" --min-price 50000 --max-price 150000 --limit 10

Prints:  IDS: id1,id2,...      (ready for send_product.py --ids)
         then a human-readable list (name — PKR price — category).
Exit 0 with "IDS:" (possibly empty) always; the caller decides what to send.
"""
import argparse
import json
import os
import re

CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "products.json")

# query term -> category substrings that count as a match (lowercased "contains")
SYNONYMS = {
    "sofa": ["sofa"], "sofas": ["sofa"], "sofa set": ["sofa set"], "sofa sets": ["sofa set"],
    "sectional": ["l shaped", "l-shaped"], "l shaped": ["l shaped"], "l-shaped": ["l shaped"],
    "sofa bed": ["sofa bed", "sofacumbed"], "sofa cum bed": ["sofa bed", "sofacumbed"], "sofacumbed": ["sofa bed", "sofacumbed"],
    "bed": ["bedroom"], "beds": ["bedroom"], "bedroom": ["bedroom"], "bed set": ["bedroom"], "bedroom set": ["bedroom"],
    "chair": ["single seater"], "chairs": ["single seater"], "accent chair": ["single seater"],
    "single seater": ["single seater"], "seater": ["single seater"], "armchair": ["single seater"],
    "settee": ["settee"], "bench": ["settee"], "benches": ["settee"],
    "coffee table": ["coffee", "center"], "center table": ["center", "coffee"], "centre table": ["center", "coffee"],
    "dressing": ["dressing"], "dressing table": ["dressing"], "vanity": ["dressing"],
    "console": ["console"], "chest": ["chest", "console"], "drawers": ["chest", "console"], "chest of drawers": ["chest"],
    "wardrobe": ["cupboard", "wardrobe"], "cupboard": ["cupboard", "wardrobe"], "almari": ["cupboard", "wardrobe"], "almirah": ["cupboard", "wardrobe"],
    "tv": ["media", "tv con"], "tv console": ["tv con", "media"], "media": ["media"], "media wall": ["media"], "tv unit": ["tv con", "media"],
    "study": ["study"], "study table": ["study"], "desk": ["study"],
    "painting": ["painting", "art", "canvas", "hand"], "paintings": ["painting", "art", "canvas", "hand"],
    "art": ["painting", "art", "canvas", "hand"], "canvas": ["canvas", "painting"], "wall art": ["painting", "art"],
    "ottoman": ["ottoman", "puffy"], "puffy": ["puffy", "ottoman"], "pouffe": ["puffy", "ottoman"], "stool": ["puffy", "ottoman"],
    "table": ["table"], "tables": ["table"],
    "entrance table": ["entrance"], "entrance": ["entrance"],
}


def load():
    with open(CATALOG, encoding="utf-8") as f:
        return json.load(f).get("catalog", [])


def match_substrs(query):
    q = re.sub(r"\s+", " ", query.lower()).strip()
    if q in SYNONYMS:
        return SYNONYMS[q]
    # try each word of a multi-word query against the map
    for w in q.split():
        if w in SYNONYMS:
            return SYNONYMS[w]
    # fallback: match the query text as a category substring directly
    return [q]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True, help="what the customer asked for, e.g. 'sofa'")
    ap.add_argument("--max-price", type=int, default=None, dest="max_price")
    ap.add_argument("--min-price", type=int, default=None, dest="min_price")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    subs = match_substrs(args.query)
    out = []
    for p in load():
        cat = str(p.get("category", "")).lower()
        if not any(s in cat for s in subs):
            continue
        amt = (p.get("price") or {}).get("amount")
        if amt is not None:
            if args.max_price and amt > args.max_price:
                continue
            if args.min_price and amt < args.min_price:
                continue
        if str(p.get("availability", "")).lower().startswith("out"):
            continue
        out.append(p)

    out.sort(key=lambda p: (p.get("price") or {}).get("amount") or 0)
    out = out[: args.limit]

    print("IDS: " + ",".join(str(p["id"]) for p in out))
    print(f"({len(out)} match{'' if len(out) == 1 else 'es'} for '{args.query}'"
          + (f", under {args.max_price:,}" if args.max_price else "")
          + (f", over {args.min_price:,}" if args.min_price else "") + ")")
    for p in out:
        amt = (p.get("price") or {}).get("amount")
        print(f"  {p['id']}  {p['name']} — PKR {amt:,} — {p['category']}" if amt else f"  {p['id']}  {p['name']} — {p['category']}")


if __name__ == "__main__":
    main()
