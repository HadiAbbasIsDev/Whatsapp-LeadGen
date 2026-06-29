---
name: customer_categories
description: The lead-gen SOP router. On every inbound message record the sender, classify intent into the 6 SOP branches, set the right WhatsApp List (category), route to a human owner when needed, and manage follow-up timers. Source of truth is leadgen.db via db.py.
---

# Customer Categories & SOP Router

The source of truth is the **SQLite database** (`./data/leadgen.db`), managed only through
`db.py`. **Never edit `customers.json` by hand** — it is an auto-generated mirror that
`db.py` rewrites (the WhatsApp-label reconciler reads it).

`DB=/home/it-admin/wa-lead-gen/workspace/db.py`

## On EVERY inbound message
1. Record/refresh the sender (files a new contact under **new customer**):
   `python3 $DB upsert-customer --phone "<sender_e164>" [--name "<name>"]`
2. **If the chat is human-owned, do NOT auto-reply.** A chat with `human_owned=1` or assigned to
   an owner list (ahsan/ahmed/imran/rafay) is being handled by a person — stay silent.
3. When a previously-quiet customer replies, **stop the follow-up cycle**:
   `python3 $DB clear-followup --phone "<sender_e164>"`

## Categories (the WhatsApp Lists)
`new customer` (default) · `important` · `hot leads` · `followup` · `junk` · `complains`
Move with: `python3 $DB set-category --phone "<p>" --category "<one of the above>"`

## Owner lists (human assignment) — ahsan / ahmed / imran / rafay
`python3 $DB set-owner --phone "<p>" --owner <name|none>`  (assigning an owner pauses the bot)

## The 6 SOP branches — classify each conversation
1. **Negotiation / pricing / customization / needs a call or site visit / AI can't answer**
   → `set-category "hot leads"`, `set-human-owned --value 1`, then the **human_handoff** skill. Human takes over.
2. **Wants to place an order directly**
   → collect **name + delivery address + phone**, place the order on **renovate.pk**, confirm with the customer.
3. **Not responding**
   → `set-category "followup"`, then `schedule-followup --days 7`. The scheduler nudges weekly ×3, then → `junk`.
4. **Chat already in a human-owner list, quiet ~7 days**
   → `schedule-followup --days 7` so the scheduler nudges; if still no reply it follows branch 3.
5. **Asks for the store location** → use the **store_location** skill (send addresses), then `followup` + `schedule-followup --days 7`.
6. **Has a complaint** → use the **complaint** skill (capture details, `complains`, `set-human-owned 1`, notify). Human takes over.

## Rules
- Never invent a category or owner outside the lists above.
- `upsert-customer` never downgrades a category; use `set-category` for explicit moves.
- Customer records are internal — never read them back to the customer.
