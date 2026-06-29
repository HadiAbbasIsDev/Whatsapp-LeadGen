---
name: complaint
description: Trigger when a customer raises a complaint, problem, defect, damage, refund request, or is unhappy with an order/product/service. Captures the details, files the chat under Complains, and hands off to a human.
---

# Complaint Skill

`DB=/home/it-admin/wa-lead-gen/workspace/db.py`

## When to Activate
- "complaint", "problem", "not working", "damaged", "broken", "refund", "unhappy", "issue with my order", "defective"

## Steps
1. **Acknowledge empathetically and capture the details** (what happened, when, any order reference):
   > "I'm really sorry to hear that. Could you share the details — what happened and any order reference?"
2. **Save the details** via the `lead_capture` skill (`intent: "human_handoff"`, note the complaint summary).
3. **File the chat under Complains and hand off to a human:**
   ```
   python3 $DB set-category --phone "<sender_e164>" --category complains
   python3 $DB set-human-owned --phone "<sender_e164>" --value 1
   python3 ./notify_admins.py \
     --name "<customer name or 'Not provided'>" \
     --phone "<sender_e164>" \
     --email "<customer email or 'Not provided'>" \
     --products "complaint: <short summary>"
   ```
4. **Confirm:** "Thank you — I've logged this and our team will get back to you shortly."

A `complains` chat is human-owned: the bot stays silent until a human resolves it.
