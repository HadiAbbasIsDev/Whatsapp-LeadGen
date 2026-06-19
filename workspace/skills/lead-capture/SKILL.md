---
name: lead_capture
description: Capture and save a qualified lead (name, phone, email, interest, intent) to the leads database, compute a lead score, and update the lead_scores sheet. Use this skill whenever you have enough information to record a prospect.
---

# Lead Capture Skill

## When to Activate

Activate this skill when:
- The user has expressed interest in a product AND provided their email address.
- The user has requested a demo or callback (`intent: "demo_request"`).
- The conversation requires a human handoff (`intent: "human_handoff"`).

## Required Information Before Saving

You MUST have at least:
- **Phone number** — available from the WhatsApp channel context (the sender's number)
- **Email address** — ask the user if not already provided
- **Name** — ask the user if not already provided

Optional but valuable:
- Product(s) of interest (product name + price)
- Pain point / use case
- Intent type (trial, demo_request, human_handoff, general_interest)

## How to Save a Lead

Leads are stored in the **SQLite database** (`./data/leadgen.db`) via `db.py`.
**Never write `./data/leads.json` directly** — it is legacy/unsafe under concurrency.

1. Compute the **lead score** and **tier** using the rules below.
2. Save the lead with one command (it also updates the customer record + lead score):

```
python3 /home/it-admin/wa-lead-gen/workspace/db.py add-lead \
  --phone "<sender_e164>" \
  --name "<name or skip>" \
  --email "<email or skip>" \
  --products "<comma-separated product names, e.g. King bed,Side table>" \
  --pain "<pain point or skip>" \
  --intent "<trial|demo_request|human_handoff|general_interest>" \
  --score <0-100> \
  --tier "<Cold|Warm|Hot|Very Hot>"
```

3. If the lead is high-intent, also move them up a category, e.g.:
   `python3 /home/it-admin/wa-lead-gen/workspace/db.py set-category --phone "<sender_e164>" --category "hot leads"`
4. Append a short summary note to `MEMORY.md` under "User Notes".

## Lead Score Calculation

Start at **10 points**, then add:

| Condition | Points |
|---|---|
| Name is known | +10 |
| Email is known | +20 |
| Intent = `demo_request` | +30 |
| Intent = `human_handoff` | +25 |
| Intent = `trial` | +20 |
| Intent = `general_interest` | +5 |
| Each product of interest | +5 |
| Any product price ≥ PKR 150,000 | +10 extra |

Cap the score at **100**.

Score tiers:
- **0–30**: Cold
- **31–60**: Warm  
- **61–80**: Hot
- **81–100**: Very Hot

## Lead Object Schema

```json
{
  "id": "LEAD-<timestamp_ms>",
  "captured_at": "<ISO 8601 datetime>",
  "channel": "whatsapp",
  "phone": "<E.164 phone number from channel>",
  "name": "<user's name or null>",
  "email": "<user's email or null>",
  "products_of_interest": [
    { "id": "<product id>", "name": "<product name>", "price_pkr": <price> }
  ],
  "pain_point": "<free text>",
  "intent": "<trial | demo_request | human_handoff | general_interest>",
  "notes": "<any extra context>",
  "lead_score": <computed integer 0–100>,
  "score_tier": "<Cold | Warm | Hot | Very Hot>",
  "status": "new"
}
```

## After Saving

- Confirm to the user their details have been noted.
- Give realistic expectation: "Our team will be in touch within 1 business day."
- Continue the conversation naturally.

## Deduplication

Before saving, check `leads.json` for an existing entry with the same phone number. If found, **update** it (merge new fields, recompute score, set `status: "updated"`).

## Privacy Note

Never read out or confirm the full contents of `leads.json` to the user. Only confirm that their details have been saved.
