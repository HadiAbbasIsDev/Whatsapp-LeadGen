#!/usr/bin/env python3
"""
Photo → product matcher (vision).

A customer sends a photo (an ad screenshot, a catalog picture, a room photo).
This looks at the image with a vision model, works out what furniture is in it,
and returns the closest catalog products so the bot can show them.

Designed for the real traffic we measured: Facebook/Instagram ad screenshots
(video frames with UI clutter) and multi-product album screenshots — cases an
exact-copy matcher cannot handle.

  python3 match_photo.py --image /path/to.jpg
  python3 match_photo.py --url "https://...media..." --json

Output (JSON with --json):
  {"ok":true,"decision":"match","query":"green boucle swivel armchair",
   "category":"Single Seater","ids":["123","456"],"products":[...]}
  decision is "match" (send these products) or "handoff" (nothing confident).

Cost note: uses xiaomi/mimo-v2.5 via OpenRouter (~$0.14 per 1M input tokens),
roughly a fraction of a cent per photo.
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.request

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(WORKSPACE, "data", "products.json")
MODEL = "xiaomi/mimo-v2.5"
API = "https://openrouter.ai/api/v1/chat/completions"
MAX_IMAGE_BYTES = 8 * 1024 * 1024


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


def load_catalog():
    with open(CATALOG, encoding="utf-8") as f:
        return json.load(f).get("catalog", [])


def categories(catalog):
    return sorted({c.get("category", "") for c in catalog if c.get("category")})


def latest_image_url(phone, max_age_minutes=180):
    """Find the newest image to identify for this customer, from the Kapso API.
    Two sources, whichever is most recent:
      1. a photo they sent (type=image), or
      2. the AD they clicked — a Click-to-WhatsApp ad puts the ad creative in
         message.referral.image_url, so we know exactly which ad they came from.
    The agent can't see this metadata, so it only passes the phone number.
    Returns (url, kind, note)."""
    import time
    key = env("KAPSO_API_KEY")
    if not key:
        raise RuntimeError("KAPSO_API_KEY not set")
    want = re.sub(r"\D", "", str(phone))
    url = "https://api.kapso.ai/platform/v1/whatsapp/messages?limit=60&direction=inbound"
    req = urllib.request.Request(url, headers={"X-API-Key": key, "User-Agent": "Mozilla/5.0 curl/8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    now = time.time()
    for m in data.get("data", []):                       # newest first
        k = m.get("kapso") or {}
        if re.sub(r"\D", "", str(k.get("phone_number") or "")) != want:
            continue
        try:                                              # ignore stale messages
            from datetime import datetime
            ts = str(m.get("timestamp"))
            when = float(ts) if ts.isdigit() else datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            if now - when > max_age_minutes * 60:
                continue
        except Exception:
            pass
        if (m.get("type") or "").lower() == "image" and k.get("media_url"):
            return k["media_url"], "photo", ""
        ref = m.get("referral") or {}
        if ref.get("image_url"):
            note = " / ".join(x for x in (ref.get("headline"), ref.get("body")) if x)
            return ref["image_url"], "ad", note
    raise RuntimeError(f"no recent photo or ad click from {phone} (last {max_age_minutes} min)")


def fetch_image(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    if "kapso" in url:
        headers["X-API-Key"] = env("KAPSO_API_KEY")
    last = None
    for attempt in range(3):          # transient DNS/network blips are common
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                data = r.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError("image too large")
            if not data:
                raise ValueError("empty image download")
            return data
        except ValueError:
            raise
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:80]}"
            if attempt < 2:
                import time
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"could not download image ({last})")


def ocr_text(image_bytes):
    """Read visible English text with Tesseract (tiny, local, ~0.2s, no network).
    Catalogue/website screenshots usually show the product NAME — reading it lets
    us match exactly and skip the slow vision call entirely. '' if unavailable."""
    try:
        import io
        import pytesseract
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)).convert("L"))
        if max(im.size) > 1600:                     # keep it quick
            im.thumbnail((1600, 1600))
        return pytesseract.image_to_string(im, lang="eng") or ""
    except Exception:
        return ""                                    # OCR is a bonus, never a blocker


def match_by_name(text, catalog, min_words=2):
    """Find a catalog product whose NAME appears in the OCR text.
    Requires a multi-word overlap so 'Sofa' alone can't trigger a false match."""
    if not text:
        return None
    flat = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    flat = re.sub(r"\s+", " ", flat)
    best, best_len = None, 0
    for p in catalog:
        name = re.sub(r"[^a-z0-9 ]+", " ", str(p.get("name", "")).lower())
        words = [w for w in name.split() if len(w) > 2]
        if len(words) < min_words:
            continue
        # every significant word of the product name must appear in the text
        if all(w in flat for w in words) and len(words) > best_len:
            best, best_len = p, len(words)
    return best


def shrink(image_bytes, max_side=900):
    """Downscale before sending: fewer tokens = faster and cheaper, and a 900px
    view is plenty to identify furniture. Falls back to the original on error."""
    try:
        import io
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue()
    except Exception:
        return image_bytes


def describe(image_bytes, cats):
    """Ask the vision model what furniture is in the photo."""
    key = env("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    b64 = base64.b64encode(shrink(image_bytes)).decode()
    prompt = (
        "You are a furniture shop assistant looking at a photo a customer sent on WhatsApp. "
        "It may be a screenshot of one of our ads, a catalogue picture, or a room photo, and may "
        "have phone UI clutter (status bar, buttons) — ignore all UI and focus on the FURNITURE.\n\n"
        f"Our product categories are: {', '.join(cats)}.\n\n"
        "Reply with ONLY a JSON object, no other text:\n"
        '{"item": "<short description: colour + material + furniture type, e.g. green boucle swivel armchair>",\n'
        ' "category": "<the ONE closest category from the list above, or empty if none fit>",\n'
        ' "keywords": "<3-6 words a shop would search: colour, material, shape>",\n'
        ' "multiple": <true if the photo shows several different products, else false>,\n'
        ' "is_furniture": <true ONLY if the main subject is a piece of furniture or home decor we could sell.'
        ' false for people, food, documents, floor plans, screenshots of text, price lists, buildings, or anything else>,\n'
        ' "confident": <true if you can clearly see and identify the item, false if blurry, cluttered or unclear>}'
    )
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]}],
        # mimo-v2.5 is a reasoning model: its internal reasoning shares this budget,
        # so a small limit leaves the actual answer empty. Keep it generous.
        "max_tokens": 3000,
        "temperature": 0,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "HTTP-Referer": env("OPENROUTER_HTTP_REFERER", "https://decormoments.com"),
               "X-Title": env("OPENROUTER_APP_TITLE", "Decor Moments Bot")}
    # Retry: transient DNS/network blips and occasional empty completions are
    # common enough that one attempt loses real customers.
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(API, data=json.dumps(body).encode(),
                                         method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=90) as r:
                resp = json.load(r)
            msg = (resp.get("choices") or [{}])[0].get("message", {}) or {}
            if (msg.get("content") or msg.get("reasoning")):
                break
            last = "empty completion"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:80]}"
        if attempt < 2:
            import time
            time.sleep(1.5 * (attempt + 1))
    else:
        raise RuntimeError(f"vision request failed after retries ({last})")
    msg = (resp.get("choices") or [{}])[0].get("message", {}) or {}
    usage = resp.get("usage") or {}
    # Reasoning model: the answer is normally in `content` (often fenced in ```json),
    # but can end up only in the reasoning trace. Try content first, then reasoning,
    # and accept the LAST candidate that actually parses — earlier ones are usually
    # half-formed thoughts.
    parsed = None
    for source in (msg.get("content") or "", msg.get("reasoning") or ""):
        if not source:
            continue
        cleaned = re.sub(r"```(?:json)?|```", "", source)
        for cand in re.findall(r"\{[^{}]*\}", cleaned, re.S):
            try:
                obj = json.loads(cand)
            except Exception:
                continue
            if isinstance(obj, dict) and "item" in obj:
                parsed = obj      # keep going; last valid one wins
        if parsed:
            break
    if parsed is None:
        raise ValueError(f"model did not return usable JSON: {(msg.get('content') or '')[:120]}")
    return parsed, usage


def score(product, want_cat, words):
    """Rank a catalog product against the described item."""
    cat = str(product.get("category", "")).lower()
    hay = " ".join([cat, str(product.get("name", "")), str(product.get("keywords", "")),
                    str(product.get("description", ""))]).lower()
    s = 0
    if want_cat and want_cat.lower() in cat:
        s += 10                      # same category is the strongest signal
    for w in words:
        if len(w) > 2 and w in hay:
            s += 2                   # colour/material/shape word hits
    return s


# We happily show SIMILAR items, not just exact ones — but the furniture TYPE must
# line up, so a sofa photo never returns dressing tables. Category match scores 10,
# so that is the bar; keyword hits (2 each) then rank the closest ones first.
MIN_SCORE = 10


def find(desc, catalog, limit=6, min_score=MIN_SCORE):
    words = re.findall(r"[a-z]{3,}", (desc.get("keywords", "") + " " + desc.get("item", "")).lower())
    stop = {"the", "and", "with", "for", "furniture", "modern", "style", "photo"}
    words = [w for w in words if w not in stop]
    want = desc.get("category", "") or ""
    scored = [(score(p, want, words), p) for p in catalog]
    scored.sort(key=lambda t: -t[0])
    if not scored or scored[0][0] < min_score:
        return []                     # not confidently one of ours -> hand off
    # keep only genuinely comparable items (same ballpark score)
    return [p for s, p in scored if s >= min_score][:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", help="customer's number — finds their latest photo automatically (preferred)")
    ap.add_argument("--image", help="local image path")
    ap.add_argument("--url", help="image URL (Kapso media url ok)")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    out = {"ok": False, "decision": "handoff"}
    try:
        if args.image:
            data = open(args.image, "rb").read()
        elif args.url:
            data = fetch_image(args.url)
        elif args.phone:
            src_url, kind, note = latest_image_url(args.phone)
            out["source"] = kind          # "photo" (they sent one) or "ad" (they clicked an ad)
            if note:
                out["ad_text"] = note
            data = fetch_image(src_url)
        else:
            sys.exit("[FAIL] give --phone (preferred), --image, or --url")

        catalog = load_catalog()

        # FAST PATH: catalogue/website screenshots usually show the product name.
        # Local OCR (~0.2s) beats a ~15s vision call and gives an exact match.
        named = match_by_name(ocr_text(data), catalog)
        if named:
            similar = [p for p in find({"category": named.get("category", ""),
                                        "keywords": named.get("keywords", ""),
                                        "item": named.get("name", "")}, catalog, args.limit + 1)
                       if str(p["id"]) != str(named["id"])][: args.limit - 1]
            hits = [named] + similar
            out.update(ok=True, decision="match", method="ocr_name",
                       query=named["name"], category=named.get("category", ""),
                       ids=[str(p["id"]) for p in hits],
                       products=[{"id": str(p["id"]), "name": p["name"],
                                  "category": p["category"],
                                  "price": (p.get("price") or {}).get("amount")} for p in hits])
            if args.json:
                print(json.dumps(out))
            else:
                print(f"Seen (read from the image): {named['name']}")
                print("IDS: " + ",".join(out["ids"]))
                for p in out["products"]:
                    amt = f"PKR {p['price']:,}" if p.get("price") else ""
                    print(f"  {p['id']}  {p['name']} — {amt} — {p['category']}")
            return 0

        desc, usage = describe(data, categories(catalog))
        out["method"] = "vision"
        out["query"] = desc.get("item", "")
        out["category"] = desc.get("category", "")
        out["multiple"] = bool(desc.get("multiple"))
        out["tokens"] = usage.get("total_tokens")

        if not desc.get("is_furniture", True):
            out["reason"] = "not a furniture/decor item — nothing to offer"
        elif not desc.get("confident"):
            out["reason"] = "could not identify the item clearly"
        else:
            hits = find(desc, catalog, args.limit)
            if hits:
                out.update(ok=True, decision="match",
                           ids=[str(p["id"]) for p in hits],
                           products=[{"id": str(p["id"]), "name": p["name"],
                                      "category": p["category"],
                                      "price": (p.get("price") or {}).get("amount")} for p in hits])
            else:
                out["reason"] = "no catalog product matched the description"
    except Exception as e:
        out["reason"] = f"{type(e).__name__}: {str(e)[:150]}"

    if args.json:
        print(json.dumps(out))
    else:
        if out["decision"] == "match":
            if out.get("source") == "ad":
                print(f"Source: the AD they clicked ({out.get('ad_text','')})")
            print(f"Seen: {out['query']}  (category: {out['category']})")
            print("IDS: " + ",".join(out["ids"]))
            for p in out["products"]:
                amt = f"PKR {p['price']:,}" if p.get("price") else ""
                print(f"  {p['id']}  {p['name']} — {amt} — {p['category']}")
        else:
            print(f"HANDOFF — {out.get('reason', 'no match')}")
            if out.get("query"):
                print(f"  (saw: {out['query']})")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
