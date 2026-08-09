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
from kapso import env, list_messages, list_conversations, digits  # noqa: E402

STATE_FILE = os.path.expanduser("~/.openclaw/kapso-poller-state.json")
LOCK_FILE = os.path.expanduser("~/.openclaw/kapso-poller.lock")
SEEN_MAX = 1000
PAGE_LIMIT = 50
MAX_PAGES = 10
WATERMARK_GRACE = 120  # seconds
# Backlog handling (e.g. bot off overnight): a message older than this when we
# pick it up is a catch-up, not real-time. For those we skip any chat a HUMAN
# already replied to while we were off (their last outbound is newer than the
# message) — the bot only answers still-unanswered messages, like a human would.
STALE_SECONDS = 300
ANSWERED_GRACE = 5     # seconds; outbound must be meaningfully after the inbound


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


_CATALOG_CACHE = None


def _catalog():
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        try:
            with open(os.path.join(REPO, "workspace", "data", "products.json"), encoding="utf-8") as f:
                _CATALOG_CACHE = json.load(f).get("catalog", [])
        except Exception:
            _CATALOG_CACHE = []
    return _CATALOG_CACHE


def order_summary_text(order):
    """WhatsApp's native catalog checkout (type=order) carries NO readable text —
    only a list of {product_retailer_id, item_price, quantity}. Without this, the
    poller sends the agent nothing usable and a real order goes silently unseen.
    Build a plain-English summary the agent can act on (SECOND FLOW)."""
    items = order.get("product_items") if isinstance(order, dict) else None
    if not items:
        return None
    catalog = _catalog()
    by_variant = {}
    for p in catalog:
        for v in (p.get("variants") or []):
            if v.get("id"):
                by_variant[str(v["id"])] = (p, v)

    lines = ["Customer placed an order via the WhatsApp catalog:"]
    total = 0
    for it in items:
        rid = str(it.get("product_retailer_id") or "")
        qty = it.get("quantity") or 1
        price = it.get("item_price")
        p, v = by_variant.get(rid, (None, None))
        name = p["name"] if p else f"(unknown catalog item {rid})"
        variant = f" [{v['title']}]" if v and v.get("title") else ""
        price_str = f"PKR {price:,.0f}" if isinstance(price, (int, float)) else "PKR ?"
        lines.append(f"- {name}{variant} x{qty} — {price_str}")
        if isinstance(price, (int, float)):
            total += price * qty
    if total:
        lines.append(f"Total: PKR {total:,.0f}")
    return "\n".join(lines)


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
    if not text and mtype == "order" and isinstance(m.get("order"), dict):
        # Native catalog checkout has no text field at all — synthesize one so
        # the agent actually sees what was ordered (see order_summary_text).
        text = order_summary_text(m["order"])
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


def post_event(event):
    """Deliver ONE event. Returns 'delivered', 'uncertain', or 'down'.

    The kapso-whatsapp plugin holds the webhook response open until the full
    agent turn completes (long: reasoning-model latency), so: a read timeout
    AFTER the request was accepted means the gateway IS processing it —
    treating that as failure and retrying is exactly what duplicates agent
    replies. Only a connection error (gateway not listening) is retryable."""
    secret = env("KAPSO_WEBHOOK_SECRET")
    port = env("KAPSO_GATEWAY_PORT") or "18789"
    path = env("KAPSO_WEBHOOK_PATH") or "/kapso/webhook"
    body = json.dumps(event).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": f"sha256={sig}",
            "X-Webhook-Event": "whatsapp.message.received",
        })
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            resp = r.read().decode(errors="replace")
            log(f"delivered {event['message']['id'][:40]}…: {resp[:120]}")
            return "delivered"
    except urllib.error.HTTPError as e:
        # Gateway answered (4xx/5xx): it received the event; do not re-deliver
        log(f"gateway rejected {event['message']['id'][:40]}… HTTP {e.code}: {e.read()[:120]}")
        return "uncertain"
    except (TimeoutError, OSError) as e:
        import socket
        if isinstance(e, (ConnectionRefusedError, ConnectionResetError)) or "refused" in str(e).lower():
            log(f"gateway down ({e}); will retry next cycle")
            return "down"
        if isinstance(e, (socket.timeout, TimeoutError)) or "timed out" in str(e).lower():
            log(f"response timeout for {event['message']['id'][:40]}… — gateway accepted it, marking delivered")
            return "uncertain"
        log(f"delivery error ({e}); will retry next cycle")
        return "down"
    except Exception as e:
        log(f"delivery error ({e}); will retry next cycle")
        return "down"


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


def answered_outbound_map():
    """digits(phone) -> last-outbound epoch, for each active conversation. Used to
    detect chats a human already replied to while the bot was off. {} on failure."""
    out = {}
    try:
        ok, convs = list_conversations(limit=100, phone_number_id=env("KAPSO_PHONE_NUMBER_ID"))
        if not ok:
            return {}
        for c in convs:
            k = c.get("kapso") if isinstance(c.get("kapso"), dict) else {}
            phone = digits(first(c.get("phone_number"), k.get("phone_number"), "") or "")
            lo = to_epoch_seconds(first(k.get("last_outbound_at"), c.get("last_outbound_at")))
            if phone and lo:
                out[phone] = max(out.get(phone, 0), lo)
    except Exception as e:
        log(f"answered-map fetch failed: {e}")
    return out


def drop_already_answered(candidates, discard):
    """From a backlog, remove messages a human already answered while we were off.
    Only applies to STALE (old) messages; fresh real-time messages pass through."""
    now = int(time.time())
    if not any(ts < now - STALE_SECONDS for ts, _, _ in candidates):
        return candidates
    amap = answered_outbound_map()
    if not amap:
        return candidates  # can't tell — fail open, deliver (never drop silently)
    kept = []
    for ts, mid, event in candidates:
        sender = digits((event.get("message") or {}).get("from", "") or "")
        last_out = amap.get(sender, 0)
        if ts < now - STALE_SECONDS and last_out > ts + ANSWERED_GRACE:
            discard.append(mid)
            log(f"skip {mid[:34]}… — chat {sender} already answered (human replied after this while bot was off)")
        else:
            kept.append((ts, mid, event))
    return kept


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

    # Catch-up: drop backlog messages a human already answered while the bot was off.
    candidates = drop_already_answered(candidates, discard)

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
    candidates.sort(key=lambda x: x[0])
    for ts, mid, event in candidates:
        # One event per POST: seen/watermark advance per message, immediately,
        # so a crash or slow turn can only ever affect a single message.
        outcome = post_event(event)
        if outcome == "down":
            break  # gateway not listening; keep order, retry next cycle
        state["seen"].append(mid)
        state["watermark"] = max(state["watermark"], ts)
        save_state(state)
        changed = False  # already persisted
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
