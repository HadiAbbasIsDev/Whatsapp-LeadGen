#!/usr/bin/env python3
"""
Kapso inbound polling relay — webhook-free inbound for hosts with no public URL.

Kapso normally pushes inbound WhatsApp messages to a public HTTPS webhook. This
laptop is NATed with no stable public URL (the June migration died on ephemeral
tunnels), but Kapso officially supports listing messages via the platform API.

This relay polls GET /platform/v1/whatsapp/messages and replays each new
inbound message to the kapso-whatsapp plugin's own webhook endpoint on
loopback, signed with the same HMAC scheme Kapso uses — so the plugin's whole
inbound pipeline (signature check, normalization, admission/category gate,
agent dispatch) runs unchanged. When a real public URL exists later (VPS or
named tunnel), register the real webhook and stop this relay; nothing else
changes.

Delivery semantics: at-least-once. A message id is only marked seen AFTER the
gateway accepted it (or when it is unmappable/outbound), so a gateway outage
never loses messages — they are retried next cycle. A crash in the tiny window
between webhook POST and state save can redeliver a message once on restart.

Env (.env): KAPSO_API_KEY, KAPSO_PHONE_NUMBER_ID, KAPSO_WEBHOOK_SECRET,
            KAPSO_GATEWAY_PORT (default 18789), KAPSO_WEBHOOK_PATH (default
            /kapso/webhook), KAPSO_POLL_SECONDS (default 5),
            KAPSO_DISPLAY_NUMBER (optional: the bot's own number, to drop
            outbound echoes that arrive without a direction field)

Usage:
  python3 scripts/kapso_poller.py            # run forever
  python3 scripts/kapso_poller.py --once     # single poll cycle
  python3 scripts/kapso_poller.py --dry-run  # poll + print; mutates NOTHING
"""

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "workspace"))
from kapso import env, list_messages, digits  # noqa: E402

STATE_FILE = os.path.expanduser("~/.openclaw/kapso-poller-state.json")
LOCK_FILE = os.path.expanduser("~/.openclaw/kapso-poller.lock")
SEEN_MAX = 1000
PAGE_LIMIT = 50
MAX_PAGES = 10
WATERMARK_GRACE = 120  # seconds


def log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} [kapso-poller] {msg}", flush=True)


def acquire_single_instance_lock():
    """Two pollers would double-dispatch every message; refuse to start."""
    fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("FATAL: another kapso_poller.py instance holds the lock — exiting")
        sys.exit(1)
    fh.write(str(os.getpid()))
    fh.flush()
    return fh  # keep open for process lifetime


def load_state():
    try:
        s = json.load(open(STATE_FILE))
        return {"seen": s.get("seen", []), "watermark": s.get("watermark", 0)}
    except Exception:
        return {"seen": [], "watermark": 0}


def save_state(state):
    try:
        tmp = STATE_FILE + f".tmp.{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump({"seen": state["seen"][-SEEN_MAX:], "watermark": state["watermark"]}, f)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log(f"warn: could not save state: {e}")


def to_epoch_seconds(value):
    """Best-effort: numeric epoch (s or ms) or ISO-8601 string -> epoch seconds."""
    if value is None:
        return None
    try:
        n = float(value)
        return int(n / 1000) if n > 9_999_999_999 else int(n)
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def first(*vals):
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
        if v is not None and not isinstance(v, (dict, list)):
            return v
    return None


def msg_id(m):
    return str(first(m.get("id"), m.get("message_id"), m.get("wamid")) or "")


def msg_ts(m):
    return to_epoch_seconds(first(m.get("timestamp"), m.get("created_at"), m.get("inserted_at"))) or 0


def map_message(m):
    """Map a platform-API message object to the webhook message shape the
    kapso-whatsapp plugin's normalizeKapsoWebhook() accepts.
    Requirements (plugin webhook.js isKapsoWebhookMessage): string id, type,
    timestamp; sender via message.from or conversation.phone_number.
    Returns None for messages that must never be dispatched (outbound, no id,
    no sender)."""
    if not isinstance(m, dict):
        return None
    kapso_extra = m.get("kapso") if isinstance(m.get("kapso"), dict) else {}
    direction = str(first(m.get("direction"), kapso_extra.get("direction"), "") or "").lower()
    if direction and direction != "inbound":
        return None
    mid = msg_id(m)
    if not mid:
        return None
    sender = first(m.get("from"), m.get("phone_number"), m.get("wa_id"),
                   (m.get("contact") or {}).get("phone_number") if isinstance(m.get("contact"), dict) else None)
    conv = m.get("conversation") if isinstance(m.get("conversation"), dict) else {}
    conv_phone = first(conv.get("phone_number"), conv.get("phoneNumber"))
    if not sender and not conv_phone:
        return None
    own = digits(env("KAPSO_DISPLAY_NUMBER") or "")
    if own and digits(sender or "") == own:
        return None  # our own outbound echoed with a missing direction field
    ts = msg_ts(m) or int(time.time())
    mtype = str(first(m.get("message_type"), m.get("type"), "text"))
    text = first(
        (m.get("text") or {}).get("body") if isinstance(m.get("text"), dict) else m.get("text"),
        m.get("content") if isinstance(m.get("content"), str) else None,
        (m.get("content") or {}).get("text") if isinstance(m.get("content"), dict) else None,
        m.get("body"),
    )
    msg = {
        "id": mid,
        "type": mtype,
        "timestamp": str(ts),
        "from": "+" + digits(sender) if sender else None,
        "kapso": {**kapso_extra, "direction": "inbound"},
    }
    if text:
        msg["text"] = {"body": str(text)}
    # media/transcript passthrough — the plugin reads kapso.mediaUrl / kapso.media_data
    for k in ("media_url", "mediaUrl", "media_data", "mediaData", "transcript", "transcription", "content"):
        if k in m and k not in msg["kapso"]:
            msg["kapso"][k] = m[k]
    event = {
        "event": "whatsapp.message.received",
        "phone_number_id": str(first(m.get("phone_number_id"), env("KAPSO_PHONE_NUMBER_ID")) or ""),
        "message": msg,
    }
    if conv:
        event["conversation"] = conv
    return event


def post_events(events):
    secret = env("KAPSO_WEBHOOK_SECRET")
    port = env("KAPSO_GATEWAY_PORT") or "18789"
    path = env("KAPSO_WEBHOOK_PATH") or "/kapso/webhook"
    body = json.dumps({"data": events}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"sha256={sig}",
            "X-Webhook-Event": "whatsapp.message.received",
        })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = r.read().decode(errors="replace")
            log(f"delivered {len(events)} event(s): {resp[:200]}")
            return True
    except Exception as e:
        log(f"ERROR delivering to gateway (will retry next cycle): {e}")
        return False


def fetch_new(state):
    """Fetch pages (newest first) until we hit a seen/older-than-watermark
    message or run out of pages. Returns messages oldest-first."""
    msgs = []
    after = None
    for _ in range(MAX_PAGES):
        ok, batch, paging = list_messages(
            limit=PAGE_LIMIT,
            after=after,
            phone_number_id=env("KAPSO_PHONE_NUMBER_ID"),
            direction="inbound",
        )
        if not ok:
            log(f"poll failed: {paging.get('error')}")
            return None
        msgs.extend(batch)
        hit_known = any(
            msg_id(m) in state["seen"] or (state["watermark"] and msg_ts(m) < state["watermark"] - WATERMARK_GRACE)
            for m in batch
        )
        after = (paging.get("cursors") or {}).get("after") if isinstance(paging, dict) else None
        if hit_known or len(batch) < PAGE_LIMIT or not after:
            break
    msgs.reverse()
    return msgs


def poll_cycle(state, dry_run=False):
    msgs = fetch_new(state)
    if msgs is None:
        return

    # First cycle ever: establish the baseline watermark and deliver nothing —
    # even when the account is empty (watermark = now), so the FIRST real
    # message after cutover is delivered, not treated as history.
    if not state["watermark"]:
        state["watermark"] = max((msg_ts(m) for m in msgs), default=0) or int(time.time())
        if dry_run:
            log(f"DRY RUN: would set baseline watermark {state['watermark']} ({len(msgs)} historical message(s) skipped)")
            return
        save_state(state)
        log(f"baseline watermark {state['watermark']} set; {len(msgs)} historical message(s) skipped")
        return

    candidates = []   # (ts, mid, event) — deliverable
    discard = []      # mids that must never be delivered (outbound/unmappable/history)
    for m in msgs:
        mid = msg_id(m)
        if not mid or mid in state["seen"]:
            continue
        ts = msg_ts(m)
        if ts and ts < state["watermark"] - WATERMARK_GRACE:
            discard.append(mid)  # pre-watermark history
            continue
        event = map_message(m)
        if event is None:
            discard.append(mid)
        else:
            candidates.append((ts or int(time.time()), mid, event))

    if dry_run:
        for _, mid, event in candidates:
            log(f"DRY RUN: would deliver {mid}: {json.dumps(event)[:300]}")
        if not candidates:
            log(f"DRY RUN: nothing new ({len(discard)} non-deliverable)")
        return

    changed = False
    if discard:
        state["seen"].extend(discard)
        changed = True
    if candidates:
        candidates.sort(key=lambda x: x[0])
        # seen/watermark advance ONLY after the gateway accepted the batch —
        # a delivery failure leaves state untouched so the batch retries.
        if post_events([e for _, _, e in candidates]):
            state["seen"].extend(mid for _, mid, _ in candidates)
            state["watermark"] = max(state["watermark"], max(ts for ts, _, _ in candidates))
            changed = True
    if changed:
        save_state(state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    for v in ("KAPSO_API_KEY", "KAPSO_PHONE_NUMBER_ID", "KAPSO_WEBHOOK_SECRET"):
        if not env(v):
            log(f"FATAL: {v} missing from .env")
            sys.exit(1)
    lock = acquire_single_instance_lock() if not args.dry_run else None
    interval = max(int(env("KAPSO_POLL_SECONDS") or 5), 3)
    state = load_state()
    log(f"starting (interval={interval}s, watermark={state['watermark']}, seen={len(state['seen'])}, dry_run={args.dry_run})")
    while True:
        try:
            poll_cycle(state, dry_run=args.dry_run)
        except Exception as e:
            log(f"cycle error: {e}")
        if args.once:
            break
        time.sleep(interval)
    if lock:
        lock.close()


if __name__ == "__main__":
    main()
