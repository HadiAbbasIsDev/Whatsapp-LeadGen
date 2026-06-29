#!/usr/bin/env python3
"""Follow-up scheduler for the lead-gen SOP (runs as a supervisord program).

Every TICK_SECONDS it asks db.py for due follow-ups and applies the SOP flowchart:
  - "Not responding" / owner-list chats get a weekly nudge (up to 3 weeks);
  - after the 3rd unanswered nudge the chat is moved to "junk";
  - when a customer replies, the agent calls `db.py clear-followup` and the cycle stops.

Sending is decoupled behind send_message(): until the Kapso channel has credentials
this queues to data/outbox.jsonl (a clean seam, also unit-testable). When Kapso is
live, point send_message() at the Kapso send path and nothing else changes.

State lives entirely in leadgen.db (via db.py), so this process is stateless and safe
to restart at any time.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "db.py")
DATA = os.path.join(HERE, "data")
OUTBOX = os.path.join(DATA, "outbox.jsonl")

TICK_SECONDS = int(os.environ.get("FOLLOWUP_TICK_SECONDS", "3600"))   # how often to scan (default hourly)
FOLLOWUP_DAYS = float(os.environ.get("FOLLOWUP_INTERVAL_DAYS", "7"))  # weekly per the SOP
MAX_STAGE = int(os.environ.get("FOLLOWUP_MAX_STAGE", "3"))            # 3 weeks then junk

PKT = timezone(timedelta(hours=5))


def db(*args):
    """Call the tested db.py CLI (single source of truth) and return the CompletedProcess."""
    return subprocess.run([sys.executable, DB, *args], capture_output=True, text=True)


def send_message(phone, text):
    """Outbound seam. Until Kapso is wired this queues to outbox.jsonl + logs.

    When the Kapso channel has creds, replace the body with the Kapso send call
    (e.g. `openclaw kapso-whatsapp cli ... send` or the channel's send API).
    """
    os.makedirs(DATA, exist_ok=True)
    rec = {"to": phone, "text": text, "queued_at": datetime.now(PKT).isoformat(timespec="seconds")}
    with open(OUTBOX, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[followup] queued -> {phone}: {text[:60]}", flush=True)


def nudge_text(chat):
    name = (chat.get("name") or "").split()[0] or "there"
    if chat.get("owner"):
        return (f"Hi {name}, just following up on your conversation with our team at "
                f"renovate.pk — are you still interested? Happy to help.")
    return (f"Hi {name}, checking in from renovate.pk — were you still looking for "
            f"furniture? I'm here if you have any questions.")


def process_due():
    res = db("due-followups")
    if res.returncode != 0:
        print(f"[followup] db error: {res.stderr.strip()}", flush=True)
        return
    try:
        due = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        print("[followup] could not parse due-followups output", flush=True)
        return

    for chat in due:
        phone = chat.get("phone")
        if not phone:
            continue
        stage = chat.get("followup_stage") or 0
        if stage >= MAX_STAGE:
            # No response after the 3rd weekly nudge -> Junk (SOP rule 3).
            db("set-category", "--phone", phone, "--category", "junk")
            db("clear-followup", "--phone", phone)
            print(f"[followup] no reply after {MAX_STAGE} nudges -> junk: {phone}", flush=True)
        else:
            send_message(phone, nudge_text(chat))
            db("schedule-followup", "--phone", phone, "--days", str(FOLLOWUP_DAYS))  # bumps stage
            print(f"[followup] nudged {phone} (stage {stage} -> {stage + 1})", flush=True)


def main():
    once = "--once" in sys.argv  # for tests / manual runs
    print(f"[followup] scheduler started (tick={TICK_SECONDS}s, interval={FOLLOWUP_DAYS}d, "
          f"max_stage={MAX_STAGE}, once={once})", flush=True)
    while True:
        try:
            process_due()
        except Exception as e:  # never let one bad tick kill the loop
            print(f"[followup] tick error: {e}", flush=True)
        if once:
            return
        time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
