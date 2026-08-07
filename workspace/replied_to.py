#!/usr/bin/env python3
"""
What did the customer just REPLY to?

On WhatsApp a customer often long-presses one of the products we sent and
replies "tell me about this" / "is this available?" / "price?". The reply
carries the quoted message id, but the agent never sees it — so without this
the bot guesses the wrong product.

This looks up the customer's latest inbound message, reads its quoted-message
id, finds that outbound message, and reports which product it was.

  python3 replied_to.py --phone "+923362615506"
  python3 replied_to.py --phone "+92..." --json

Prints "PRODUCT: <id> <name>" when the reply points at a product we sent,
or "NO_REPLY_CONTEXT" when they were not replying to anything.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(WORKSPACE, "data", "products.json")
API = "https://api.kapso.ai/platform/v1/whatsapp/messages"


def env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v
    try:
        for line in open(os.path.join(os.path.dirname(WORKSPACE), ".env")):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return default


def api_get(url):
    req = urllib.request.Request(url, headers={"X-API-Key": env("KAPSO_API_KEY"),
                                               "User-Agent": "Mozilla/5.0 curl/8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def quoted_id(phone):
    """What the customer's most recent message was replying to.
    Returns (quoted_message_id, referred_product_retailer_id, their_text).
    `referred_product` appears when they reply to an item in our WhatsApp
    Business catalogue (the products shown on the business profile)."""
    want = re.sub(r"\D", "", str(phone))
    data = api_get(f"{API}?limit=40&direction=inbound")
    mine = [m for m in data.get("data", [])
            if re.sub(r"\D", "", str((m.get("kapso") or {}).get("phone_number") or "")) == want]
    if not mine:
        return None, None, ""
    # Customers often add a follow-up line after the reply ("...final price" then
    # "with high quality"), so scan their few most recent messages for the one
    # that actually carries the reply context rather than only the newest.
    for m in mine[:5]:
        ctx = m.get("context") or {}
        ref = (ctx.get("referred_product") or {}).get("product_retailer_id")
        if ctx.get("id") or ref:
            return ctx.get("id"), ref, str((m.get("kapso") or {}).get("content") or "")
    return None, None, str((mine[0].get("kapso") or {}).get("content") or "")


def product_by_variant(retailer_id, catalog):
    """WhatsApp catalogue items are keyed by Shopify VARIANT id.
    Returns (product, variant) so we can quote the exact price/option the
    customer saw on the catalogue card."""
    rid = str(retailer_id)
    for p in catalog:
        for v in (p.get("variants") or []):
            if str(v.get("id")) == rid:
                return p, v
        if rid == str(p.get("id")):
            return p, None
    return None, None


def find_message(msg_id, direction, max_pages=6):
    """Locate a message by id, paging back through history — the quoted message
    can be hours old and well past the first page."""
    after = None
    for _ in range(max_pages):
        url = f"{API}?limit=100&direction={direction}"
        if after:
            url += "&after=" + urllib.request.quote(str(after))
        data = api_get(url)
        for m in data.get("data", []):
            if m.get("id") == msg_id:
                return m
        after = ((data.get("paging") or {}).get("cursors") or {}).get("after")
        if not after:
            break
    return None


def outbound_content(msg_id):
    m = find_message(msg_id, "outbound")
    return str((m.get("kapso") or {}).get("content") or "") if m else ""


def inbound_message(msg_id):
    """The customer's OWN message they replied to — they often send a photo and
    then reply to it ('this two chairs final price')."""
    return find_message(msg_id, "inbound")


def product_from_text(text, catalog):
    """Our product captions start with '<name> - PKR ...', so match on the name."""
    if not text:
        return None
    flat = re.sub(r"\s+", " ", text.lower())
    best, best_len = None, 0
    for p in catalog:
        name = str(p.get("name", "")).lower().strip()
        if len(name) > 3 and name in flat and len(name) > best_len:
            best, best_len = p, len(name)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    out = {"ok": False}
    try:
        qid, referred, their_text = quoted_id(args.phone)
        out["their_message"] = their_text[:120]
        catalog = json.load(open(CATALOG, encoding="utf-8")).get("catalog", [])

        # They tapped a product on our WhatsApp Business profile/catalogue.
        if referred:
            p, variant = product_by_variant(referred, catalog)
            if p:
                # quote the variant they actually tapped, not the base price
                price = (variant or {}).get("price") or (p.get("price") or {}).get("amount")
                out.update(ok=True, source="whatsapp_catalog", id=str(p["id"]),
                           name=p["name"], category=p.get("category", ""),
                           price=price, variant=(variant or {}).get("title", ""),
                           dimensions=p.get("dimensions", ""), link=p.get("link", ""))
            else:
                out["reason"] = f"replied to catalogue item {referred}, not found in products.json (re-sync?)"
        elif not qid:
            out["reason"] = "not replying to a specific message"
        else:
            content = outbound_content(qid)
            if not content:
                # Not one of ours — they replied to their OWN message. If that was
                # a photo, hand the exact image back so it can be re-identified.
                own = inbound_message(qid) or {}
                k = own.get("kapso") or {}
                if (own.get("type") or "").lower() == "image" and k.get("media_url"):
                    out.update(ok=True, source="customer_photo", photo_url=k["media_url"],
                               their_message=their_text[:120])
                    if args.json:
                        print(json.dumps(out))
                    else:
                        print(f"PHOTO: {k['media_url']}")
                        print("  (they replied to a photo THEY sent — re-identify it with:")
                        print(f"   match_photo.py --url \"{k['media_url']}\")")
                    return 0
            p = product_from_text(content, catalog)
            if p:
                out.update(ok=True, id=str(p["id"]), name=p["name"],
                           category=p.get("category", ""),
                           price=(p.get("price") or {}).get("amount"),
                           dimensions=p.get("dimensions", ""),
                           link=p.get("link", ""))
            else:
                out["reason"] = "replied to a message that was not a product"
                out["quoted_text"] = content[:160]
    except Exception as e:
        out["reason"] = f"{type(e).__name__}: {str(e)[:120]}"

    if args.json:
        print(json.dumps(out))
    elif out["ok"]:
        price = f"PKR {out['price']:,}" if out.get("price") else ""
        variant = f" [{out['variant']}]" if out.get("variant") else ""
        print(f"PRODUCT: {out['id']}  {out['name']}{variant} — {price} — {out['category']}")
        if out.get("dimensions"):
            print(f"  Dimensions: {out['dimensions']}")
        if out.get("link"):
            print(f"  {out['link']}")
    else:
        print(f"NO_REPLY_CONTEXT — {out.get('reason')}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
