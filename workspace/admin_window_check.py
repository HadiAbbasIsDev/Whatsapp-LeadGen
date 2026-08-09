#!/usr/bin/env python3
"""
Admin 24-hour context-window keeper.

WhatsApp only lets us send a free-text message (like a handoff alert) to someone
within 24 hours of THEIR last message to us. If an admin goes quiet for 24h, that
window closes and the bot can no longer alert them about hot leads / handoffs.

This watches each admin's Kapso window (the real Kapso clock, which keeps running
even when the bot process is off) and warns them BEFORE it closes:

  at ~6h, ~3h and ~1h left →
     "Please send me a message to be able to receive handoffs.
      X hrs left in context finishing."

The moment an admin replies, their window resets to 24h (Kapso does this), and the
warnings re-arm for the next cycle. State is stored on disk so it survives restarts.

Run from cron every ~15 minutes:
  */15 * * * *  cd /home/it-admin/wa-lead-gen && python3 workspace/admin_window_check.py
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, WORKSPACE)
import kapso  # noqa: E402

ADMINS_FILE = os.path.join(WORKSPACE, "data", "admins.json")
STATE_FILE = os.path.join(WORKSPACE, "data", "admin_window.json")
API = "https://api.kapso.ai/platform/v1/whatsapp/messages"
WINDOW_HOURS = 24
THRESHOLDS = [6, 3, 1]          # warn when this many hours (or fewer) remain


def log(msg):
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    print(f"{ts} [admin-window] {msg}", flush=True)


def admins():
    try:
        nums = json.load(open(ADMINS_FILE)).get("admins", [])
        return ["+" + re.sub(r"\D", "", str(n)) for n in nums if re.sub(r"\D", "", str(n))]
    except Exception:
        return []


def load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    json.dump(state, open(tmp, "w"), indent=2)
    os.replace(tmp, STATE_FILE)


def last_inbound_epoch(phone):
    """Real Kapso clock: epoch seconds of this admin's most recent inbound message,
    or None if we can't find one."""
    want = re.sub(r"\D", "", str(phone))
    key = kapso.env("KAPSO_API_KEY")
    req = urllib.request.Request(f"{API}?limit=60&direction=inbound",
                                 headers={"X-API-Key": key, "User-Agent": "Mozilla/5.0 curl/8"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
    except Exception as e:
        log(f"fetch failed for {phone}: {e}")
        return None
    for m in data.get("data", []):          # newest first
        k = m.get("kapso") or {}
        if re.sub(r"\D", "", str(k.get("phone_number") or "")) != want:
            continue
        ts = str(m.get("timestamp") or "")
        try:
            return float(ts) if ts.isdigit() else \
                datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None
    return None


def main():
    if not kapso.kapso_enabled():
        log("WA_TRANSPORT is not kapso — nothing to do")
        return
    now = datetime.now(timezone.utc).timestamp()
    state = load_state()
    changed = False

    for phone in admins():
        last = last_inbound_epoch(phone)
        rec = state.get(phone) or {}
        if last is None:
            continue

        # Admin replied since we last looked -> Kapso reset their window. Re-arm.
        if rec.get("last_inbound") != last:
            rec = {"last_inbound": last, "warned": []}

        remaining = WINDOW_HOURS - (now - last) / 3600.0
        warned = set(rec.get("warned", []))

        # Fire the highest threshold that now applies and hasn't been sent yet.
        due = [t for t in THRESHOLDS if remaining <= t and t not in warned]
        if due and remaining > 0:
            t = max(due)                     # e.g. hit 6 and 3 at once -> send the 6 message once
            hrs = max(1, int(round(remaining)))
            msg = (f"Please send me a message to be able to receive handoffs. "
                   f"{hrs} hr{'s' if hrs != 1 else ''} left in context finishing.")
            ok, info = kapso.send_text(phone, msg)
            if ok:
                log(f"warned {phone}: ~{hrs}h left")
                warned |= {x for x in THRESHOLDS if remaining <= x}
            else:
                log(f"could not warn {phone}: {info}")
        rec["warned"] = sorted(warned)
        state[phone] = rec
        changed = True

    if changed:
        save_state(state)


if __name__ == "__main__":
    main()
