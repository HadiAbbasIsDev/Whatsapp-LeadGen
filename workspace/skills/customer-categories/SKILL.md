---
name: customer_categories
description: Maintain WhatsApp Business–style customer labels (categories). Whenever an allowed number messages the bot, ensure it is listed in customers.json. New contacts are filed under "new customer". Later they can be moved to "important" or "hot leads".
---

# Customer Categories Skill

This mirrors the labels you use in WhatsApp Business: **new customer**, **important**, **hot leads**.

The canonical list lives in `./data/customers.json`:

```json
{
  "categories": ["new customer", "important", "hot leads"],
  "customers": [
    {
      "phone": "+923362615506",
      "name": null,
      "category": "new customer",
      "first_contact_at": "<ISO 8601 datetime>",
      "last_message_at": "<ISO 8601 datetime>",
      "notes": ""
    }
  ]
}
```

## When to Activate

On **every** incoming message, after you know the sender's phone number.

## What to Do

1. Read `./data/customers.json`.
2. Look for an entry whose `phone` matches the sender's E.164 number (from the WhatsApp channel context).
3. **If no entry exists** (a new allowed person):
   - Append a new customer object.
   - Set `category` to `"new customer"`.
   - Set `first_contact_at` and `last_message_at` to the current ISO 8601 time.
   - Set `name` to their name if known, otherwise `null`.
4. **If an entry exists**:
   - Update `last_message_at` to now.
   - Fill in `name` if you have since learned it and it was `null`.
   - Do **not** change their `category` automatically — only move them when explicitly instructed (e.g. the owner asks to mark someone "important" or "hot leads", or a rule below applies).
5. Write the updated JSON back to `./data/customers.json`.

## Category Meaning

- **new customer** — default for anyone who just started messaging. (This is the only auto-assigned category for now.)
- **important** — manually assigned by the business owner.
- **hot leads** — high-intent prospects. For now, assign only when the owner asks. (Later this can be auto-linked to a "Hot"/"Very Hot" lead_score from the lead_capture skill.)

## Rules

- `category` must always be one of the values in the `categories` array. Never invent a new category unless the owner adds it to that array first.
- Keep one entry per phone number (deduplicate on `phone`).
- Never read the contents of `customers.json` back to the customer; it is internal.
