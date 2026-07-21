# TOOLS.md — WhatsApp List Tagging (DB Proxy)

## WhatsApp List / Label Tagging

This bot does NOT have direct access to the WhatsApp Business API label management endpoint.
Instead, chat labels are persisted in the SQLite database via `db.py` as a
**functional substitute**. The DB writes are atomic and the `customers.json` mirror stays in
sync — but the labels will NOT appear as WhatsApp Business labels in the WhatsApp app.

## Two-Field Design

The customers table has two independent fields for chat state:

| Field | Purpose | Who changes it |
|---|---|---|
| `category` | Primary WhatsApp List tag — exactly ONE value per chat | Bot (all flows) |
| `cadence_status` | Follow-up cycle state — separate from category | Bot (Flows 3, 4, 5) |

**Critical rule:** For human-owned chats (category = ahsan/ahmed/imran/rafay), the bot
NEVER changes `category` — it only writes `cadence_status`. The owner tag is preserved
permanently. For all other chats, `category` and `cadence_status` change together.

## Valid Categories

| Category | Purpose |
|---|---|
| `new customer` | Default for any first-time sender (auto-assigned by `upsert-customer`) |
| `important` | Owner-designated important contacts |
| `hot leads` | High-intent chat needing human attention (FIRST FLOW) |
| `followup` | Non-human-owned chat in weekly follow-up cycle (THIRD / FIFTH FLOW) |
| `junk` | Non-human-owned chat, no response after 3 weeks; stop engaging |
| `complaints` | Active customer complaint, handed to human (SIXTH FLOW) |
| `ahsan` | Human-owned chat — Ahsan has taken over |
| `ahmed` | Human-owned chat — Ahmed has taken over |
| `imran` | Human-owned chat — Imran has taken over |
| `rafay` | Human-owned chat — Rafay has taken over |

## Valid Cadence Statuses

| cadence_status | Meaning |
|---|---|
| `null` | Not in any follow-up cycle |
| `followup` | Actively in weekly follow-up cadence |
| `junk` | Follow-up cycle exhausted; stop engaging |

## Commands

```bash
# Set category (overwrites — one value, never appended). Logs old → new.
/usr/bin/python3 /home/it-admin/wa-lead-gen/workspace/db.py set-category --phone "<E.164>" --category "hot leads"

# Check current category before any customer-facing reply.
/usr/bin/python3 /home/it-admin/wa-lead-gen/workspace/db.py get-customer --phone "<E.164>"

# Set cadence_status (separate from category — for human-owned chats). Logs old → new.
/usr/bin/python3 /home/it-admin/wa-lead-gen/workspace/db.py set-cadence-status --phone "<E.164>" --cadence-status "followup"

# Clear cadence_status (when chat re-engages)
/usr/bin/python3 /home/it-admin/wa-lead-gen/workspace/db.py set-cadence-status --phone "<E.164>" --cadence-status "null"

# List chats by category or cadence status
/usr/bin/python3 /home/it-admin/wa-lead-gen/workspace/db.py list-customers --category "followup"
```

## Tagging by Flow

| Flow | category change? | cadence_status change? |
|---|---|---|
| FIRST (Hot Lead) | → "hot leads" | (unchanged) |
| SECOND (Order) | (none unless fails) | (none) |
| THIRD (Non-responsive) | → "followup", then → "junk" | → "followup", then → "junk" |
| FOURTH (Human cold) | **NEVER** (stays owner name) | → "followup", then → "junk" |
| FIFTH (Store location) | → "followup", then → "junk" | → "followup", then → "junk" |
| SIXTH (Complaint) | → "complaints" | (unchanged) |

## Reply-Suppression Rules

- If `category` is `complaints` or `hot leads`, the chat is already handed to a human. Do not send any customer-facing reply.
- If `category` is `ahsan`, `ahmed`, `imran`, or `rafay`, a human owns the chat. Do not reply to normal incoming messages.
- Human-owned chats may receive exactly one scheduled re-engagement message only after 7 full inactive days, then use `cadence_status` for the weekly follow-up state while preserving the owner category.
- If a human-owned chat has `cadence_status = followup` because of that scheduled re-engagement and the client responds, route the response into FIRST FLOW or SECOND FLOW.

## Validation

Every `set-category` and `set-cadence-status` call prints the old value and new value
in its JSON output. If a write would result in multiple active values, the DB schema
prevents it (PRIMARY KEY on phone ensures one row per chat; UPSERT overwrites).

## TODO — Swap for Real WhatsApp Business API Labels

Once WhatsApp Business API label management is wired up:
1. Replace `db.py set-category` calls with the actual WhatsApp API label endpoint.
2. Keep `db.py set-cadence-status` as the source of truth for follow-up cycle state
   (WhatsApp labels don't have an equivalent).
3. Keep the DB writes as a fallback audit log.
