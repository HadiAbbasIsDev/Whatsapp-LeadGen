---
name: store_location
description: Trigger when a customer asks where the store is, for an address, directions, a showroom, or to visit in person. Sends all renovate.pk location addresses, then marks the chat for follow-up.
---

# Store Location Skill

`DB=/home/it-admin/wa-lead-gen/workspace/db.py`

## When to Activate
- "where are you located", "store address", "directions", "can I visit", "showroom", "branch", "location"

## Steps
1. **Send ALL our location addresses** (from `BUSINESS.md` / the configured locations list).
   If addresses are not yet configured, say a team member will share them shortly and hand off
   via the `human_handoff` skill.
2. **Mark the chat for follow-up** so we re-engage if they go quiet:
   ```
   python3 $DB set-category --phone "<sender_e164>" --category followup
   python3 $DB schedule-followup --phone "<sender_e164>" --days 7
   ```
3. Offer to help further — product info, directions, or booking a visit.

When the customer replies again, `clear-followup` (handled by the SOP router) stops the cycle.
