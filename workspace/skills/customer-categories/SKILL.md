---
name: customer_categories
description: Maintain WhatsApp Business–style customer labels (categories) in the SQLite database via db.py. Whenever an allowed number messages the bot, record it without overwriting existing handoff/follow-up categories. New contacts default to "new customer".
---

# Customer Categories Skill

This mirrors the labels in WhatsApp Business: **new customer**, **important**, **hot leads**, **followup**, **junk**, **complaints**, and human-owner lists.

The source of truth is a **SQLite database** (`./data/leadgen.db`), managed only through
`db.py`. **Never edit `customers.json` directly** — it is an auto-generated mirror that
`db.py` rewrites (the WhatsApp-label sync reads it). Editing it by hand is unsafe under
concurrent customers.

## When to Activate

On **every** incoming message, after you know the sender's phone number.

## What to Do

Run this command with the exec/shell tool to create/update the customer record.
`upsert-customer` does not change an existing category, so it is safe for chats
already tagged as complaints, hot leads, followup, junk, or a human owner list:

```
python3 /home/it-admin/wa-lead-gen/workspace/db.py upsert-customer --phone "<sender_e164>"
```

If you have learned the customer's name, include it in the upsert (it won't overwrite an existing name with blank):

```
python3 /home/it-admin/wa-lead-gen/workspace/db.py upsert-customer --phone "<sender_e164>" --name "<name>"
```

Then check the current category before deciding whether the agent may reply:

```
python3 /home/it-admin/wa-lead-gen/workspace/db.py get-customer --phone "<sender_e164>"
```

To **move** a customer to another category (only when the owner asks, or per a routing flow):

```
python3 /home/it-admin/wa-lead-gen/workspace/db.py set-category --phone "<sender_e164>" --category "hot leads"
```

(valid categories: `new customer`, `important`, `hot leads`, `followup`, `junk`, `complaints`, `vendor`, `ahsan`, `ahmed`, `imran`, `rafay`)

## Category Meaning

- **new customer** — default for anyone who just started messaging (the only auto-assigned one).
- **important** — human-handled / bot silent. Assigned automatically when a customer sends a photo/video (media handoff), or manually by the owner. Bot does not reply until the owner moves it back to `new customer`/`followup`.
- **hot leads** — high-intent prospects or human handoff requests; once assigned, the agent must stop replying.
- **followup** — non-human-owned chat in an active follow-up cadence.
- **junk** — follow-up exhausted; stop engaging.
- **complaints** — active customer complaint handed to a human; once assigned, the agent must stop replying.
- **ahsan / ahmed / imran / rafay** — human-owned chats. Do not reply normally; only the 7-day scheduled re-engagement flow may send one follow-up.

## Rules

- Runtime enforcement: the gateway itself now drops inbound messages from chats
  whose category is NOT `new customer` or `followup` — you will never even see a
  message from an `important` / `complaints` / `hot leads` / `junk` / `vendor` /
  human-owned chat (all of those are human-handled, bot silent).
  The rules below stay as defense-in-depth for the turn in which a category changes.
- Category must be one of the valid categories above. Never invent a new one.
- Do not change a customer's category automatically on a normal message. Use `upsert-customer`
  only; it never downgrades an existing category. Use `set-category` only on explicit owner instruction or a routing-flow action.
- Never set an existing customer back to `new customer`.
- If `get-customer` returns `category` = `important`, `complaints`, `hot leads`, or `vendor`, do not send a customer-facing reply.
- If `get-customer` returns `category` = `ahsan`, `ahmed`, `imran`, or `rafay`, do not send a normal reply. Follow only the 7-day human-owned cold flow in AGENTS.md. Exception: if `cadence_status` is `followup` from that flow and the client is responding to the scheduled follow-up, route the reply into FIRST FLOW or SECOND FLOW.
- One record per phone number (the DB deduplicates on phone automatically).
- Never read customer records back to the customer; they are internal.
