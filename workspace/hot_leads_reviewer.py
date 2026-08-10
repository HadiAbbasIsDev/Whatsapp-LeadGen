#!/usr/bin/env python3
"""
Hot-leads auto-reviewer.

Hot-leads chats are SILENCED — the live bot never sees them (the gate drops
them), so the "customer said it's too expensive / not interested -> junk" rule
never fires for them. A human is meant to handle these, but many just go cold.

This job reads each hot-leads conversation (read-only, via the Kapso API), asks
the model whether the customer has CLEARLY signalled they won't buy (too
expensive / out of budget / doesn't want to order / declined), and if so moves
that chat from `hot leads` to `junk`. It NEVER sends the customer a message — it
only reads and relabels — so it cannot break the hot-leads silence.

Safety:
- Fail-closed: any error / uncertain / no clear signal -> leave as hot leads.
- Only moves on an EXPLICIT price-objection or disinterest, never on ambiguity,
  ongoing negotiation, or a chat that's hot-leads for another reason (sent a
  photo, asked for a human).
- Every decision is logged to progress/hot-leads-review.log.

  python3 hot_leads_reviewer.py --dry-run     # show what it WOULD do, change nothing
  python3 hot_leads_reviewer.py               # apply
  python3 hot_leads_reviewer.py --limit 10    # bound how many chats/run (cost)
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(WORKSPACE)
DB_PY = os.path.join(WORKSPACE, "db.py")
DB_FILE = os.path.join(WORKSPACE, "data", "leadgen.db")
LOG = os.path.join(REPO, "progress", "hot-leads-review.log")
API = "https://api.kapso.ai/platform/v1/whatsapp/messages"
OR_API = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "xiaomi/mimo-v2.5"


def env(key, default=""):
    v = os.environ.get(key)
    if v:
        return v
    try:
        for line in open(os.path.join(REPO, ".env")):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return default


def log(msg):
    line = f"{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def hot_leads_phones():
    import sqlite3
    conn = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True, timeout=10)
    rows = conn.execute("SELECT phone FROM customers WHERE category='hot leads'").fetchall()
    conn.close()
    return [r[0] for r in rows]


def api_get(url):
    req = urllib.request.Request(url, headers={"X-API-Key": env("KAPSO_API_KEY"),
                                               "User-Agent": "Mozilla/5.0 curl/8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _ts(m):
    t = str(m.get("timestamp") or "")
    try:
        return float(t) if t.isdigit() else datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def transcript(phone, max_lines=24):
    """Build a recent 'Customer:/Shop:' transcript for ONE chat (read-only), using
    the API's phone_number filter so we get that exact conversation — both
    directions — even for chats that went quiet long ago.
    Returns (text, customer_line_count, last_speaker)."""
    import time
    want = re.sub(r"\D", "", str(phone))
    try:
        data = api_get(f"{API}?limit=40&phone_number={want}")
    except Exception:
        return "", 0, False
    msgs = []
    human_active = False
    now = time.time()
    for m in data.get("data", []):
        k = m.get("kapso") or {}
        if re.sub(r"\D", "", str(k.get("phone_number") or "")) != want:
            continue
        # A human replying from the WhatsApp Business app shows origin=business_app
        # (the bot/API use cloud_api). If a human replied in the last 24h, they are
        # actively handling this chat — leave it alone.
        if str(k.get("direction") or "").lower() == "outbound" \
           and str(k.get("origin") or "") == "business_app" \
           and (now - _ts(m)) < 24 * 3600:
            human_active = True
        body = str(k.get("content") or (m.get("text") or {}).get("body") or "").strip()
        if not body:
            continue
        direction = str(k.get("direction") or m.get("direction") or "").lower()
        who = "Shop" if direction == "outbound" else "Customer"
        msgs.append((_ts(m), who, body))
    msgs.sort(key=lambda x: x[0])
    lines = [f"{who}: {body}" for _, who, body in msgs[-max_lines:]]
    cust = sum(1 for _, who, _ in msgs if who == "Customer")
    return "\n".join(lines), cust, human_active


def judge_not_interested(convo):
    """Ask the model: has the customer CLEARLY said they won't buy? Fail-closed."""
    key = env("OPENROUTER_API_KEY")
    if not key or not convo.strip():
        return False, "no api key / empty convo"
    prompt = (
        "You are reviewing a WhatsApp chat between a furniture shop and a customer.\n"
        "Decide if the CUSTOMER has clearly and explicitly signalled they will NOT "
        "buy — because it is too expensive / over their budget, they don't want to "
        "order, or they declined/said no. Consider English AND Roman Urdu "
        "('bohot mehnga', 'nahi chahiye', 'budget nahi', 'rehne do', 'nahi lena').\n\n"
        "Say YES only when it is CLEAR and explicit. Say NO if they are still asking "
        "questions, negotiating, interested, just went quiet, or the chat is about "
        "something else (sent a photo, asked to speak to a person). When unsure, NO.\n\n"
        f"CHAT:\n{convo}\n\n"
        'Reply with ONLY JSON: {"not_interested": true/false, "reason": "<short>"}'
    )
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1200,
        "temperature": 0,
    }
    try:
        req = urllib.request.Request(
            OR_API, data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                     "HTTP-Referer": "https://decormoments.com", "X-Title": "Decor Moments Hot-Leads Review"})
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.load(r)
    except Exception as e:
        return False, f"model error: {type(e).__name__}"
    msg = (resp.get("choices") or [{}])[0].get("message", {}) or {}
    text = (msg.get("content") or "") or (msg.get("reasoning") or "")
    m = re.search(r"\{[^{}]*not_interested[^{}]*\}", re.sub(r"```(?:json)?|```", "", text), re.S)
    if not m:
        return False, "unparseable model reply"
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return False, "bad json"
    return bool(obj.get("not_interested")), str(obj.get("reason", ""))[:120]


def set_junk(phone):
    r = subprocess.run(["python3", DB_PY, "set-category", "--phone", phone,
                        "--category", "junk"], capture_output=True, text=True, timeout=30)
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only, change nothing")
    ap.add_argument("--limit", type=int, default=40, help="max chats to review this run")
    args = ap.parse_args()

    phones = hot_leads_phones()[: args.limit]
    log(f"reviewing {len(phones)} hot-leads chats (dry_run={args.dry_run})")
    moved = 0
    for phone in phones:
        try:
            convo, cust_lines, human_active = transcript(phone)
        except Exception as e:
            log(f"  {phone}: skip (transcript error {type(e).__name__})")
            continue
        if human_active:
            log(f"  {phone}: skip (a human replied in the last 24h — actively handled)")
            continue
        if cust_lines == 0:
            log(f"  {phone}: skip (no customer text — likely a photo/human handoff)")
            continue
        not_interested, reason = judge_not_interested(convo)
        if not not_interested:
            log(f"  {phone}: keep hot leads ({reason})")
            continue
        if args.dry_run:
            log(f"  {phone}: WOULD move -> junk ({reason})")
            moved += 1
            continue
        if set_junk(phone):
            log(f"  {phone}: moved hot leads -> junk ({reason})")
            moved += 1
        else:
            log(f"  {phone}: FAILED to set junk")
    log(f"done. {'would move' if args.dry_run else 'moved'} {moved}/{len(phones)} to junk")


if __name__ == "__main__":
    main()
