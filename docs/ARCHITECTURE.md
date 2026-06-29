# Whatsapp-LeadGen — Architecture

Plain-English + diagrams. Last updated: 2026-06-29.

## In one sentence
A lead messages a WhatsApp number → **Kapso** (official WhatsApp Cloud API) forwards it through a
**tunnel** to our **bot in Docker** → the bot (DeepSeek brain + SOP skills) decides what the lead
needs, replies, and files them into the right list/timer in a **SQLite database**.

---

## 1. The journey of a message
```
  Lead's WhatsApp
       |  "do you have dining tables?"
       v
 [ KAPSO (BSP) ]  official WhatsApp Cloud API; sandbox number 597907523413541
       |  POST webhook (whatsapp.message.received)
       v
 [ cloudflared TUNNEL ]  https://<id>.trycloudflare.com  ->  localhost:18789/kapso/webhook
       |
       v
 [ Docker: wa-lead-gen ]  OpenClaw gateway -> kapso plugin -> THE BOT
       |  reply
       v
 [ KAPSO ]  -->  Lead's WhatsApp
```
Kapso is the post office (speaks official WhatsApp). The tunnel is the public address that lets
Kapso reach the bot running locally.

## 2. Inside the container (one container, 3 processes via supervisord)
```
  supervisord (PID 1)
  |- [gateway]   OpenClaw 2026.6.10  :18789
  |     |- kapso-whatsapp plugin   <- /kapso/webhook  (messaging)
  |     |- whatsapp plugin (Baileys) -- native Lists (deferred)
  |     '- THE AGENT  -> model: DeepSeek v4 Pro
  |            '- skills: SOP-router, handoff, complaint, store-location,
  |                       lead-capture, product-catalog
  |               '- runs db.py (reads/writes state)
  |- [followup]  followup_scheduler.py  (weekly nudge x3 -> Junk; sends via outbox seam)
  '- [dashboard] Flask admin  :8088
  STATE (volumes): leadgen.db . ~/.openclaw (config+creds) . backups
```

## 3. The bot's brain — the SOP flowchart in code
```
 Inbound -> record sender (db.py) -> "What does the lead need?"
   |- 1 Negotiation / can't answer / wants a call -> [Hot Leads] -> HUMAN (bot silent)
   |- 2 Place an order -> collect name+address+phone -> order on renovate.pk -> confirm
   |- 3 Not responding -> [Followup] -> nudge weekly x3 -> still silent -> [Junk]
   |- 4 In owner list (Ahsan/Ahmed/Imran/Rafay) quiet 7d -> nudge -> else branch 3
   |- 5 Wants store location -> send addresses -> [Followup] -> (weekly x3 -> Junk)
   '- 6 Complaint -> capture details -> [Complains] -> HUMAN (bot silent)

 Lists: new customer . Hot Leads . Followup . Junk . Complains
        + owner lists: Ahsan . Ahmed . Imran . Rafay
```
"Human" paths make the bot go quiet so a person takes over; quiet leads are chased automatically
and eventually marked junk.

## 4. Where the truth lives
```
  [ leadgen.db (SQLite) ]  <- SINGLE SOURCE OF TRUTH
     category . owner . human_owned . followup_stage . next_followup_at . leads
        | every write regenerates
        v
  [ customers.json ]  -> WhatsApp native Lists (Baileys projection, deferred)

  THE AGENT (skills) writes it ;  THE SCHEDULER (timers) reads/writes it
```
The database decides everything. The WhatsApp "Lists" are a mirror for the human team — if the
mirror breaks, the bot keeps working.

---

## Status
- **Live now (sandbox):** Kapso messaging, bot + DeepSeek, all 6 SOP branches, DB state, scheduler, dashboard.
- **Stubbed (seam):** scheduler's outbound nudges queue to `data/outbox.jsonl` until pointed at Kapso send.
- **Deferred to go-live:** native WhatsApp Lists (Baileys/Coexistence), `plugins.allow` hardening,
  persisting channel config into the template, stable webhook (Tailscale Funnel / domain), VPS deploy,
  real client number via Coexistence BYON.

See `docs/KAPSO-GO-LIVE-PROCESS.md` for the full go-live runbook.
