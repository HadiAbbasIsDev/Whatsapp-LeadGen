---
name: human_handoff
description: Triggered when a user asks to speak to a real person. Tags the chat as hot leads, saves the lead if useful, runs notify_admins.py, then stops customer-facing replies.
---

<!--
GUARDRAIL: Authorized owner/admin numbers are +923362615506 and +923333392792 (see workspace/data/admins.json — the alert recipient list). Do NOT reference other numbers; the deprecated ones (+923110800256, +923332456988, +923369381947) must never be used.
-->


# Human Handoff Skill

## When to Activate

Activate immediately when the user says anything like:
- "speak to a real person", "talk to a human", "I want an agent"
- "real person", "customer service", "representative", "call me"
- "can someone call me", "contact me"

## Step-by-Step Process

### Step 1 — Tag as hot lead

```
python3 /home/it-admin/wa-lead-gen/workspace/db.py set-category --phone "<customer E.164 phone from channel>" --category "hot leads"
```

Do not ask the customer for more details first. The handoff request itself is enough.

### Step 2 — Save the lead if details are already known

Use the `lead_capture` skill with `intent: "human_handoff"` only for details already
available in the chat. Do not message the customer to collect missing fields.

### Step 3 — Run the admin notification script

Use the shell/exec tool to run this command from the workspace directory:

```
python3 ./notify_admins.py \
  --type hot_lead \
  --name   "<customer name or 'Not provided'>" \
  --phone  "<customer E.164 phone from channel>" \
  --email  "<customer email or 'Not provided'>" \
  --products "<product names discussed, comma-separated, or 'Not specified'>"
```

This script sends the alert to the admin numbers in `workspace/data/admins.json`:
- +923362615506
- +923333392792

**Run this script every time — do not skip it.**

### Step 4 — Stop customer-facing replies

Send ONE final courtesy message to the customer as your last reply:

> "Thank you. One of our team members will personally get back to you shortly."

Then go silent — do NOT continue naturally, confirm, or send anything else. Once
the handoff alert is sent and the chat is tagged `hot leads`, Aliya stays silent
on that thread until/unless the owner manually changes the category.
