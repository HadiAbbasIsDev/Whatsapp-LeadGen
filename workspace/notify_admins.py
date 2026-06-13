#!/usr/bin/env python3
"""
Sends a WhatsApp alert to all admin numbers when a customer requests a human handoff.
Called by the human_handoff skill.

Usage:
  python3 notify_admins.py --name "Wahaj" --phone "+921234567890" --email "a@b.com" --products "King size bed"
"""

import argparse
import subprocess
import sys
import json
import time
from datetime import datetime

ADMINS = [
    "+923110800256",
    "+923332456988",
    "+923369381947",
]

ALERTS_FILE = "data/admin_alerts.json"


def send_whatsapp(number: str, message: str, retries: int = 3) -> bool:
    for attempt in range(1, retries + 1):
        result = subprocess.run(
            ["openclaw", "message", "send",
             "--channel", "whatsapp",
             "--target", number,
             "--message", message],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f"  [OK] Sent to {number}")
            return True
        err = result.stderr.strip()
        if attempt < retries:
            print(f"  [retry {attempt}] {number}: {err}", file=sys.stderr)
            time.sleep(3)
        else:
            print(f"  [FAIL] {number}: {err}", file=sys.stderr)
    return False


def log_alert(args, results):
    try:
        with open(ALERTS_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        data = {"alerts": []}

    data["alerts"].append({
        "alert_id": f"ALERT-{int(datetime.now().timestamp() * 1000)}",
        "created_at": datetime.utcnow().isoformat() + "Z",
        "type": "human_handoff",
        "customer_phone": args.phone,
        "customer_name": args.name,
        "customer_email": args.email,
        "products_of_interest": args.products,
        "admins_notified": results,
    })

    with open(ALERTS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name",     default="Not provided")
    parser.add_argument("--phone",    default="Not provided")
    parser.add_argument("--email",    default="Not provided")
    parser.add_argument("--products", default="Not specified")
    args = parser.parse_args()

    now = datetime.now().strftime("%Y-%m-%d %H:%M PKT")

    message = (
        f"🚨 *HUMAN HANDOFF REQUEST*\n\n"
        f"A customer wants to speak with a real person.\n\n"
        f"👤 *Name:* {args.name}\n"
        f"📱 *Phone:* {args.phone}\n"
        f"📧 *Email:* {args.email}\n"
        f"🛋️ *Interested in:* {args.products}\n"
        f"🕐 *Time:* {now}\n\n"
        f"Please follow up as soon as possible.\n"
        f"— Aria (renovate.pk Bot)"
    )

    print(f"Notifying {len(ADMINS)} admins...")
    results = {}
    for number in ADMINS:
        results[number] = send_whatsapp(number, message)

    log_alert(args, results)

    success = sum(results.values())
    print(f"Done: {success}/{len(ADMINS)} delivered.")
    sys.exit(0 if success > 0 else 1)


if __name__ == "__main__":
    main()
