---
name: customer_categories
description: Maintain WhatsApp Business–style customer labels (categories) in the SQLite database via db.py. Whenever an allowed number messages the bot, record it. New contacts are filed under "new customer". Later they can be moved to "important" or "hot leads".
---

# Customer Categories Skill

This mirrors the labels in WhatsApp Business: **new customer**, **important**, **hot leads**.

The source of truth is a **SQLite database** (`./data/leadgen.db`), managed only through
`db.py`. **Never edit `customers.json` directly** — it is an auto-generated mirror that
`db.py` rewrites (the WhatsApp-label sync reads it). Editing it by hand is unsafe under
concurrent customers.

## When to Activate

On **every** incoming message, after you know the sender's phone number.

## What to Do

Run **one** command with the exec/shell tool — it inserts a new customer (filed under
**"new customer"**) or updates an existing one (refreshes last-seen), safely and atomically:

```
python3 /home/it-admin/wa-lead-gen/workspace/db.py upsert-customer --phone "<sender_e164>"
```

If you have learned the customer's name, include it (it won't overwrite an existing name with blank):

```
python3 /home/it-admin/wa-lead-gen/workspace/db.py upsert-customer --phone "<sender_e164>" --name "<name>"
```

To **move** a customer to another category (only when the owner asks, or per a rule below):

```
python3 /home/it-admin/wa-lead-gen/workspace/db.py set-category --phone "<sender_e164>" --category "hot leads"
```

(valid categories: `new customer`, `important`, `hot leads`)

## Category Meaning

- **new customer** — default for anyone who just started messaging (the only auto-assigned one).
- **important** — assigned only when the business owner asks.
- **hot leads** — high-intent prospects; assign when the owner asks (later this can auto-link to a high lead_score).

## Rules

- Category must be one of: `new customer`, `important`, `hot leads`. Never invent a new one.
- Do not change a customer's category automatically on a normal message — only `upsert-customer`
  (which never downgrades an existing category). Use `set-category` only on explicit instruction.
- One record per phone number (the DB deduplicates on phone automatically).
- Never read customer records back to the customer; they are internal.
