#!/usr/bin/env python3
"""
Shared Kapso (WhatsApp Cloud API) helper for the lead-gen bot.

Used by send_product.py (image sends), db.py (category -> contact metadata),
notify_admins.py (owner alerts) and scripts/kapso_poller.py (inbound relay).

Configuration comes from the repo .env (plus process env, which wins):
  KAPSO_API_KEY          platform API key (X-API-Key header)
  KAPSO_PHONE_NUMBER_ID  the Kapso phone number id the bot sends from
  KAPSO_BASE_URL         default https://api.kapso.ai
  WA_TRANSPORT           "kapso" enables the Kapso paths; anything else = Baileys

All senders are best-effort with bounded retries; callers decide how to report.
NOTE: api.kapso.ai sits behind Cloudflare and 403s the default python-urllib
User-Agent (error 1010) — every request must send a browser-style UA.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(WORKSPACE)
ENV_FILE = os.path.join(REPO, ".env")
UA = "Mozilla/5.0 (X11; Linux x86_64) wa-lead-gen/1.0"
GRAPH_VERSION = "v23.0"  # matches the kapso-whatsapp plugin's bundled SDK


def _load_env():
    env = {}
    try:
        with open(ENV_FILE) as f:
            for line in f:
                m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)=(.*)$', line.strip())
                if m:
                    env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    env.update({k: v for k, v in os.environ.items() if k.startswith("KAPSO_") or k == "WA_TRANSPORT"})
    return env


_ENV = _load_env()


def env(key, default=None):
    return _ENV.get(key, default)


def kapso_enabled():
    return (env("WA_TRANSPORT") or "").strip().lower() == "kapso"


def base_url():
    return (env("KAPSO_BASE_URL") or "https://api.kapso.ai").rstrip("/")


def digits(phone):
    return re.sub(r"\D", "", str(phone or ""))


def _request(method, url, body=None, timeout=20, retries=3):
    """Returns (ok, status, parsed_json_or_text). Retries 429/5xx with backoff."""
    key = env("KAPSO_API_KEY")
    if not key:
        return False, 0, "KAPSO_API_KEY not configured"
    data = json.dumps(body).encode() if body is not None else None
    last = (False, 0, "no attempt")
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "X-API-Key": key,
            "User-Agent": UA,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                try:
                    return True, r.status, json.loads(raw) if raw.strip() else {}
                except ValueError:
                    return True, r.status, raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")[:500]
            last = (False, e.code, raw)
            if e.code == 429 or e.code >= 500:
                retry_after = e.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 30) if retry_after else 2 * attempt
                except ValueError:
                    delay = 2 * attempt
                time.sleep(delay)
                continue
            return last
        except Exception as e:  # network errors
            last = (False, 0, str(e))
            time.sleep(2 * attempt)
    return last


def send_message(to, payload_type, payload, reply_to_id=None):
    """Low-level send through the Kapso meta proxy. Returns (ok, info).
    At-most-once: message POSTs are NOT auto-retried — a timeout after the
    server already processed the send would duplicate the customer message.
    Callers see [FAIL] and retry deliberately if appropriate."""
    pnid = env("KAPSO_PHONE_NUMBER_ID")
    if not pnid:
        return False, "KAPSO_PHONE_NUMBER_ID not configured"
    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "+" + digits(to),
        "type": payload_type,
        payload_type: payload,
    }
    if reply_to_id:
        body["context"] = {"message_id": reply_to_id}
    url = f"{base_url()}/meta/whatsapp/{GRAPH_VERSION}/{pnid}/messages"
    ok, status, resp = _request("POST", url, body, retries=1)
    if ok:
        try:
            msg_id = resp.get("messages", [{}])[0].get("id")
        except (AttributeError, IndexError):
            msg_id = None
        return True, msg_id or "sent"
    return False, f"HTTP {status}: {resp}"


def send_text(to, text):
    return send_message(to, "text", {"body": str(text)[:4096]})


def send_template(to, name, language="en_US", body_params=None):
    """Send an APPROVED template message. Templates are the only message type
    WhatsApp accepts outside the 24-hour customer-service window.
    body_params: list of strings for positional {{1}}.. body variables, if any."""
    payload = {"name": name, "language": {"code": language}}
    if body_params:
        payload["components"] = [{
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in body_params],
        }]
    return send_message(to, "template", payload)


def list_templates():
    """Return (ok, templates) for the WABA that owns KAPSO_PHONE_NUMBER_ID."""
    pnid = env("KAPSO_PHONE_NUMBER_ID")
    ok, _, resp = _request("GET", f"{base_url()}/platform/v1/whatsapp/phone_numbers", timeout=15)
    if not ok:
        return False, f"phone_numbers lookup failed: {resp}"
    waba = next((p.get("business_account_id") for p in (resp.get("data") or [])
                 if str(p.get("id")) == str(pnid)), None)
    if not waba:
        return False, f"no WABA found for phone number id {pnid}"
    ok, _, resp = _request("GET", f"{base_url()}/meta/whatsapp/{GRAPH_VERSION}/{waba}/message_templates", timeout=15)
    if not ok:
        return False, f"template list failed: {resp}"
    return True, resp.get("data") or []


def send_image(to, image_url, caption=None):
    payload = {"link": image_url}
    if caption:
        payload["caption"] = str(caption)[:1024]  # Cloud API caption hard limit
    return send_message(to, "image", payload)


def set_contact_metadata(phone, metadata):
    """Push metadata (e.g. {'category': 'hot leads'}) onto the Kapso contact.
    Replaces the Baileys WhatsApp-label sync; shows in the Kapso inbox."""
    ident = "+" + digits(phone)
    url = f"{base_url()}/platform/v1/whatsapp/contacts/{urllib.request.quote(ident)}"
    # PATCH is idempotent; keep it time-bounded (callers may run in CLI paths)
    ok, status, resp = _request("PATCH", url, {"contact": {"metadata": metadata}}, timeout=10, retries=2)
    if ok:
        return True, "updated"
    if status == 404:  # contact not created yet (customer never messaged via Kapso)
        return False, "contact_not_found"
    return False, f"HTTP {status}: {resp}"


def list_messages(limit=20, after=None, phone_number_id=None, direction=None):
    """List platform messages, newest first. Returns (ok, data_list, paging)."""
    params = [f"limit={int(limit)}"]
    if after:
        params.append("after=" + urllib.request.quote(str(after)))
    if phone_number_id:
        params.append("phone_number_id=" + urllib.request.quote(str(phone_number_id)))
    if direction:
        params.append("direction=" + urllib.request.quote(direction))
    url = f"{base_url()}/platform/v1/whatsapp/messages?" + "&".join(params)
    ok, status, resp = _request("GET", url)
    if ok and isinstance(resp, dict):
        return True, resp.get("data", []), resp.get("paging", {})
    return False, [], {"error": f"HTTP {status}: {resp}"}


def list_phone_numbers():
    ok, status, resp = _request("GET", f"{base_url()}/platform/v1/whatsapp/phone_numbers")
    if ok and isinstance(resp, dict):
        return True, resp.get("data", [])
    return False, [{"error": f"HTTP {status}: {resp}"}]


if __name__ == "__main__":
    # Smoke test: python3 kapso.py [numbers|messages]
    what = sys.argv[1] if len(sys.argv) > 1 else "numbers"
    if what == "numbers":
        ok, nums = list_phone_numbers()
        print(json.dumps({"ok": ok, "numbers": [{k: n.get(k) for k in ("id", "name", "phone_number")} for n in nums]}, indent=2))
    elif what == "messages":
        ok, msgs, paging = list_messages(limit=3)
        print(json.dumps({"ok": ok, "count": len(msgs), "sample_keys": sorted(msgs[0].keys()) if msgs else []}, indent=2))
