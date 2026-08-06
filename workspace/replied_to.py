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
    """The message id this customer's most recent message was replying to."""
    want = re.sub(r"\D", "", str(phone))
    data = api_get(f"{API}?limit=40&direction=inbound")
    for m in data.get("data", []):                       # newest first
        k = m.get("kapso") or {}
        if re.sub(r"\D", "", str(k.get("phone_number") or "")) != want:
            continue
        ctx = m.get("context") or {}
        return ctx.get("id"), str(k.get("content") or "")   # None if not a reply
    return None, ""


def outbound_content(msg_id):
    data = api_get(f"{API}?limit=100&direction=outbound")
    for m in data.get("data", []):
        if m.get("id") == msg_id:
            return str((m.get("kapso") or {}).get("content") or "")
    return ""


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
        qid, their_text = quoted_id(args.phone)
        out["their_message"] = their_text[:120]
        if not qid:
            out["reason"] = "not replying to a specific message"
        else:
            content = outbound_content(qid)
            catalog = json.load(open(CATALOG, encoding="utf-8")).get("catalog", [])
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
        print(f"PRODUCT: {out['id']}  {out['name']} — {price} — {out['category']}")
        if out.get("dimensions"):
            print(f"  Dimensions: {out['dimensions']}")
        if out.get("link"):
            print(f"  {out['link']}")
    else:
        print(f"NO_REPLY_CONTEXT — {out.get('reason')}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
