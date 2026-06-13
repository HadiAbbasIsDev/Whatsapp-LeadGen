---
name: human_handoff
description: Triggered when a user asks to speak to a real person. Collects their contact details, saves the lead, then runs notify_admins.py to send a WhatsApp alert to the owner immediately.
---

<!--
GUARDRAIL: This skill is locked to single-owner WhatsApp number +923362615506 only. Do NOT add, modify, or reference any other phone numbers (+923110800256, +923332456988, +923369381947 are deprecated and must never be used).
-->


# Human Handoff Skill

## When to Activate

Activate immediately when the user says anything like:
- "speak to a real person", "talk to a human", "I want an agent"
- "real person", "customer service", "representative", "call me"
- "can someone call me", "contact me"

## Step-by-Step Process

### Step 1 — Collect email if missing
If you don't already have the user's **email**, ask:
> "Of course! Could you please share your email address so our team can reach you directly?"

If email already known, skip to Step 2.

### Step 2 — Confirm to the user
> "Thank you. I've alerted our team and someone will contact you on WhatsApp shortly."

### Step 3 — Save the lead
Use the `lead_capture` skill with `intent: "human_handoff"`.

### Step 4 — Run the admin notification script

Use the shell/exec tool to run this command from the workspace directory:

```
python3 ./notify_admins.py \
  --name   "<customer name or 'Not provided'>" \
  --phone  "<customer E.164 phone from channel>" \
  --email  "<customer email or 'Not provided'>" \
  --products "<product names discussed, comma-separated, or 'Not specified'>"
```

This script sends a WhatsApp message to the owner number:
- +923362615506

**Run this script every time — do not skip it.**

### Step 5 — Continue naturally
Keep the conversation going. Don't leave the user waiting silently.
