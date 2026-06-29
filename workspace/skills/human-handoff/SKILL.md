---
name: human_handoff
description: Trigger when a user asks to speak to a real person, or for SOP branches that require a human (hot leads, complaints). Collects contact details, PAUSES the bot for that chat, and alerts the human owner.
---

# Human Handoff Skill

`DB=/home/it-admin/wa-lead-gen/workspace/db.py`

## When to Activate
- "speak to a real person", "talk to a human", "agent", "representative", "call me", "contact me"
- Any SOP branch that hands off to a human (**hot leads**, **complaints**).

## Steps
1. **Collect email if missing:**
   > "Of course! Could you share your email so our team can reach you directly?"
2. **Confirm to the user:**
   > "Thank you. I've alerted our team and someone will contact you on WhatsApp shortly."
3. **Save the lead** via the `lead_capture` skill (`intent: "human_handoff"`).
4. **Pause the bot for this chat and alert the owner:**
   ```
   python3 $DB set-human-owned --phone "<sender_e164>" --value 1
   # (optional) route to a specific owner list:
   # python3 $DB set-owner --phone "<sender_e164>" --owner <ahsan|ahmed|imran|rafay>
   python3 ./notify_admins.py \
     --name   "<customer name or 'Not provided'>" \
     --phone  "<sender_e164>" \
     --email  "<customer email or 'Not provided'>" \
     --products "<products discussed or 'Not specified'>"
   ```
   **Run the notify script every time — do not skip it.**
5. **Continue naturally** — don't leave the user waiting silently.

## Notes
- A `human_owned` chat is **not auto-answered** by the bot until a human releases it
  (`db.py set-human-owned --value 0`).
- The owner alert number(s) live in `notify_admins.py` configuration, **not hardcoded here**.
  They are set at the Kapso-credentials step (one number per owner: Ahsan/Ahmed/Imran/Rafay).
