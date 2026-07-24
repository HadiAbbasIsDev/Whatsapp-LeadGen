# Pre-Launch Checklist — before opening the bot to the public

The bot is safe today because only allowlisted numbers can reach it. Everything
below should be handled **before** you switch to "Allow everyone". (You said you'll
do the allow-everyone step yourself — it's Part E item 1 here for completeness.)

---

## PART A — Does the bot follow the flow diagram? (conformance check, 2026-07-24)

Checked the flowchart against the real rules in `workspace/AGENTS.md` + scripts.

| # | Flow (diagram) | Implemented? | Notes |
|---|---|---|---|
| 1 | Hot Lead → tag Hot Leads → human takes over | ✅ Matches | Triggers all present (negotiation, pricing, customization, ask-for-human, can't-answer, call, visit). Now also sends a "team will get back to you" line before going silent. |
| 2 | Direct order → collect name/addr/phone → place order → confirm | ✅ Fixed 2026-07-24 | The bot can't place website orders, so it now collects the details, confirms them, sends the owner a 🛒 ORDER alert, tags Hot Leads, and tells the customer the team will finalise — it never claims the order is placed. Owner places it on renovate.pk. |
| 3 | Not responding → Followup → weekly ×3 → Junk | ✅ Matches | Timing runs via `scripts/followup_runner.py` (7-day, weekly, 3 max, then Junk). |
| 4 | Human lists (Ahsan/Ahmed/Imran/Rafay) dead 7d → follow-up → reply? | ⚠️ Partial | Follow-up send works. BUT the silence gate blocks **inbound** from human-owned chats, so when that client **replies**, the bot doesn't see it. Their reply reaches the human, not the bot — acceptable, but know it. See Part C item 1. |
| 5 | Store location → send address → Followup → weekly ×3 → Junk | ✅ Matches | Addresses exist in `USER.md` (Karachi + Lahore). Bot asks which city first (fine). **Verify the addresses/phones are current** — Part D item 6. |
| 6 | Complaint → capture details → tag Complains → human | ✅ Matches | Now also sends a courtesy line before going silent. |

**Extra flows we added (not in the diagram):** Vendor brush-off (SEVENTH) and
owner-only cold outreach (EIGHTH). Both are fine to keep.

---

## PART B — BLOCKERS (must resolve before public launch)

### 1. Secrets gap — exfiltration path CLOSED (2026-07-24); key rotation still TODO
The bot needs `exec` to run its scripts, so it can technically still *read*
secret files. What matters is it can no longer *send* them: an **outbound
secrets scrubber** (`openclaw-patches/patch_kapso_secrets.py`, patched into the
kapso plugin) redacts any known secret value from every outgoing message before
it reaches the customer. Verified: a message containing the Kapso key goes out as
`[REDACTED]`; normal messages are untouched; redactions are logged to
`~/.openclaw/kapso-secrets.log`. Fails open (never blocks a legitimate reply).
- Key rotation: owner decided NOT to rotate (2026-07-24) — accepted.
- Residual (low): the scrubber matches exact secret values, so a determined
  attacker could in theory obfuscate a key (e.g. base64) to slip past it. The
  allowlist is the first line of defense; this is defense-in-depth. Optional
  future hardening: restrict `exec` to the project scripts only.

### 2. [DONE 2026-07-24] Flow 2 — order handoff
Fixed: the bot now collects name/address/phone, confirms them, alerts the owner
with a 🛒 ORDER handoff (`notify_admins.py --type order`), tags the chat Hot
Leads, and tells the customer the team will finalise. It is explicitly forbidden
from claiming the order is placed. The owner places it on renovate.pk.

---

## PART C — Decisions (settled 2026-07-24)

1. **Human-owned chat replies stay with the human.** ✅ DECIDED: leave as-is —
   when a chat is in Ahsan/Ahmed/Imran/Rafay, the bot stays silent and the human
   handles replies. (Already the behavior; no change.)
2. **"Junk" chats stay silent.** ✅ DECIDED: a Junk customer who messages again
   is ignored until you un-junk them on the dashboard. (Already the behavior; no
   change.)
3. **Bot name vs template name.** ✅ RESOLVED 2026-07-24: bot renamed to **Aliya**
   everywhere to match the approved templates.

---

## PART D — Test every flow live (with 1-2 real numbers, before opening)

Do a real WhatsApp test of each, watching the dashboard + logs:
- [ ] 1. Ask to "speak to a person" / negotiate price → tagged Hot Leads, you get a 🔥 alert, courtesy line sent, bot goes silent.
- [ ] 2. Say "I want to order X" → bot collects name/address/phone, then (after fix) alerts you to place it.
- [ ] 3. Go silent mid-chat → after 7 days, weekly Renovate template goes out (test with `followup_runner.py --min-days 0`), 3 max → Junk.
- [ ] 4. Human-owned chat left 7 days → re-engagement template goes out.
- [ ] 5. Ask "where are your stores?" → correct city address sent → Followup.
- [ ] 6. Raise a complaint → tagged Complains, ⚠️ alert, courtesy line, silence.
- [ ] 7. Pose as a supplier ("we sell you wholesale…") → one polite brush-off → Vendor → silence.
- [ ] 8. Photo/video → no reply to customer, silent 📷 handoff alert to you.
- [ ] 9. Change a label in the WhatsApp app → bot database follows within a few seconds (two-way sync).

---

## PART E — Go-live operations

1. **Open the allowlist** (you're handling this): dashboard → "Who can message
   the bot" → Allow everyone → Apply. Only do this after Parts B & D are done.
2. **Reliability**: install the dashboard as a systemd service so it (and the
   bot) auto-start and restart on crash (`admin/openclaw-admin.service`). The
   `@reboot` cron helps; a supervisor is sturdier.
3. **Backups**: confirm the daily backup is running (`~/wa-lead-gen-backups/`).
4. **After any `openclaw` update**: re-run the patchers and confirm the category
   gate + label flows still work.
5. **Meta rules**: keep cold outreach to numbers you have a lawful basis to
   contact; free-text only works inside the 24h window (templates otherwise).
6. **Verify store addresses** in `USER.md` are current (Karachi + Lahore).
7. **Catalog**: 934 products; 2 have no image (ids 3519, 1454) — fill or drop.

---

_Companion to `progress/STATUS.md` (current state) and `progress/HISTORY.md`
(dated log). Update this as items are done._
