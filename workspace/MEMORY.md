# Long-Term Memory

This file stores business-wide context only. Customer memory is stored in the
SQLite `memories` table through `db.py remember` / `db.py recall`; do not append
new customer PII here.

---

## CRITICAL: Single-Owner Access Control

This bot is locked to ONE WhatsApp customer: +923362615506. No other phone number may be treated as owner/admin/escalation/sales contact. All handoff/escalation/sales notifications go ONLY to +923362615506.

---

## Business Context

- **Business name:** renovate.pk
- **Industry:** Furniture retail — beds, bedroom sets, sofas, dining sets, office furniture
- **Website:** https://renovate.pk
- **WhatsApp number:** (the number this bot is running on)
- **Human sales contact:** +923362615506
- **Currency:** PKR (Pakistani Rupees)

---

## User Notes

### +923362615506 | Hadi | 2026-06-18
- Interested in: King size 6ft by 6.5ft bed with 2 side tables (item 5978, PKR 85,000)
- Email: xyz@gmail.com
- Status: lead saved (Warm, score 50)

---

## Follow-Up State Tracking

This section tracks automated follow-up cadences per chat (Flows 3, 4, 5).
Each entry records the flow type, current follow-up week, last-sent date, and list tag.
Update after every follow-up message is sent. Remove entry when chat exits follow-up cycle.

Format:
```
### +92XXXXXXXXXX
- flow: non-responsive | store_location | human_cold
- followup_week: 1 | 2 | 3 | done
- last_followup_date: YYYY-MM-DD
- list_tag: followup | junk  (cadence_status value)
- category: (unchanged — owner name for Flow 4, followup/junk for Flows 3/5)
- human_owner: Ahsan | Ahmed | Imran | Rafay  (Flow 4 only)
```

Note: For Flow 4 (human-owned), `category` stays as the owner name — only `cadence_status` changes. For Flows 3 and 5, `category` and `cadence_status` change together.

---

### +923362615506
- flow: (none — re-engaged 2026-07-17, cadence cleared)
- followup_week: done
- last_followup_date: 2026-07-05
- list_tag: null
- category: new customer

---

<!-- The agent will append entries like this:
### +92XXXXXXXXXX | Customer Name | 2026-04-18
- Interested in: King size bed (item-0101)
- Pain point: Looking for bedroom furniture
- Email captured: example@email.com
- Status: lead saved, awaiting follow-up
-->
