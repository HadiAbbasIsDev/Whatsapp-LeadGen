#!/usr/bin/env python3
"""
TEST-ONLY inbound injector. Feeds a synthetic inbound WhatsApp message to the
local gateway (signed exactly like the real poller does), so a flow can be
exercised end-to-end as if the owner texted the bot. The bot's reply goes to the
owner's real WhatsApp. Use ONLY for owner-directed pre-launch testing.

  python3 scripts/inject_test.py --from "+923362615506" --text "I want to talk to a human"
  python3 scripts/inject_test.py --from "+92..." --type image   # simulate a photo
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import kapso_poller as P  # reuse its signed post_event + env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="+923362615506")
    ap.add_argument("--text", default="")
    ap.add_argument("--type", default="text", choices=("text", "image", "video"))
    ap.add_argument("--id", default=None)
    args = ap.parse_args()

    digits = P.digits(args.frm)
    mid = args.id or f"TEST-{int(time.time()*1000)}-{digits[-4:]}"
    msg = {
        "id": mid,
        "type": args.type,
        "timestamp": str(int(time.time())),
        "from": "+" + digits,
        "kapso": {"direction": "inbound"},
    }
    if args.type == "text":
        msg["text"] = {"body": args.text}
    else:
        # minimal media marker so the agent hits the IMAGE/VIDEO rule
        msg["kapso"]["mediaUrl"] = "https://example.com/test-media.jpg"
        if args.text:
            msg["text"] = {"body": args.text}

    event = {
        "event": "whatsapp.message.received",
        "phone_number_id": str(P.env("KAPSO_PHONE_NUMBER_ID") or ""),
        "message": msg,
    }
    print(f"[inject] {args.type} from {msg['from']} id={mid}: {args.text[:60]}")
    result = P.post_event(event)
    print(f"[inject] gateway result: {result}")
    sys.exit(0 if result in ("delivered", "uncertain") else 1)


if __name__ == "__main__":
    main()
