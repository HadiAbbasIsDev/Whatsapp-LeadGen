# Whatsapp-LeadGen — Kapso go-live process (runbook)

End-to-end steps to take the bot from "built locally" to a live client bot on Kapso.
Last updated: 2026-06-25.

---

## ✅ DONE (built + verified locally)
- OpenClaw upgraded `2026.4.9 → 2026.6.10`; official Kapso plugin installed from ClawHub
  (`clawhub:@kapso/openclaw-whatsapp@0.1.4`), **gateway ready, plugin loaded, webhook route
  `/kapso/webhook` registered**.
- Dockerfile hardened: CRLF normalize, retrying+fatal Kapso install, build-time config cleanup.
- Full SOP built + baked:
  - `db.py` taxonomy (`new customer/important/hot leads/followup/junk/complains`) + owner lists
    (ahsan/ahmed/imran/rafay) + `human_owned`/`followup_stage`/`next_followup_at` + commands.
  - `followup_scheduler.py` (supervisord program): weekly nudge ×3 → `junk` (send via a seam).
  - Skills: SOP router, human-handoff (pause + configurable owners), store-location, complaint.
- Container healthy: gateway + dashboard (`:8088`) + followup scheduler all running.

## ⏳ DEFERRED (intentionally, slot into the steps below)
- `plugins.allow` hardening (Step 8).
- Baileys label patch re-validation for 2026.6.10 (Step 7, needs Coexistence).

---

## THE REMAINING PROCESS

### Step 0 — Finish hardening the build
- Apply any **confirmed** findings from the adversarial review workflow (bugs / SOP gaps).
- Rebuild + re-verify gateway health. *(me)*

### Step 1 — Kapso number + credentials  *(you provide, me wire)*
- Decide the test number:
  - **A spare/test Kapso number** (NOT one a live bot already uses — the webhook is
    phone-number-scoped, so registering it **hijacks** that number's inbound). 
  - or a **new dedicated number** (the real plan; via Coexistence BYON).
- Get: **`KAPSO_API_KEY`** and the **`KAPSO_PHONE_NUMBER_ID`** for that number.
- ⚠️ Consent: no test sends to real people without the owner's OK.

### Step 2 — Public webhook endpoint  *(me)*
The official plugin is **webhook-based** — Kapso POSTs to a public HTTPS URL that must reach
the gateway's `/kapso/webhook` (on the gateway HTTP server, port `18789`, currently loopback).
- **Local (phase 1):** stand up a tunnel (Tailscale Funnel / cloudflared / ngrok) →
  container `:18789`; publish `18789` in `docker-compose.yml`. Webhook URL = `https://<tunnel>/kapso/webhook`.
- **VPS (phase 2):** Caddy + a real domain → gateway port; webhook URL = `https://<domain>/kapso/webhook`.

### Step 3 — Wire the Kapso channel  *(me)*
Either via the one-shot setup (registers the webhook with Kapso):
```bash
openclaw kapso-whatsapp setup \
  --api-key "kapso_..." \
  --phone-number-id "<PHONE_NUMBER_ID>" \
  --webhook-url "https://<host>/kapso/webhook" \
  --webhook-secret "$(openssl rand -hex 32)" \
  --register-webhook --write-config
```
…then persist it durably in the config **template** (`docker/openclaw.config.docker.json`)
under `channels["kapso-whatsapp"]` (`apiKey`, `phoneNumberId`, `webhookSecret`, `webhookPath`,
`defaultTo`, `dmSecurity`, `allowFrom`) + the env equivalents in `.env`
(`KAPSO_API_KEY`, `KAPSO_PHONE_NUMBER_ID`, `KAPSO_WEBHOOK_SECRET`) so a rebuild keeps it.
- Set `dmSecurity: allowlist` + `allowFrom: [<your test numbers>]` for the locked test phase.

### Step 4 — Owner numbers + notifications  *(you provide, me wire)*
- Real phone numbers for the human-owner lists **Ahsan / Ahmed / Imran / Rafay**.
- The owner-alert number in `notify_admins.py` (currently the old hardcoded one).
- Point the **scheduler's send seam** (`followup_scheduler.py send_message()`) at the Kapso
  send path so weekly nudges actually go out (until now they queue to `data/outbox.jsonl`).

### Step 5 — First send/receive smoke test  *(me, with your consent)*
- Message the Kapso number from a test phone → confirm the bot receives (webhook) and replies (send).
- Confirm per-sender isolation (the plugin does this by default) and media send.

### Step 6 — SOP regression test  *(me)*
- The standing bar: **50-message capability test across 3 senders**.
- One test per SOP branch: hot-lead handoff (bot goes silent), order capture, not-responding →
  followup, store-location, complaint → Complains+handoff, owner-list 7-day-dead.
- Timer test with **compressed intervals** (weekly×3 → junk).
- Verify a `human_owned` chat is never auto-answered.

### Step 7 — (Hybrid) Coexistence + Baileys labels  *(optional, your call)*
- If you want native WhatsApp **Lists** for the human team: onboard the number via Kapso
  **Coexistence** (keeps the Business app alive) and re-validate the `openclaw-patches/`
  reconciler against 2026.6.10's plugin layout (the Baileys runtime moved to `~/.openclaw/`).
- Until then, segmentation lives in the DB/dashboard (a projection), not in WhatsApp.

### Step 8 — `plugins.allow` hardening  *(me)*
- Set an explicit `plugins.allow` (e.g. `whatsapp`, `kapso-whatsapp`, `memory-core`,
  `file-transfer`) after confirming nothing needed is dropped (don't break the model providers).

### Step 9 — VPS go-live (phase 2)  *(me)*
- Ship the same image to a VPS; stable webhook (Caddy + domain); **open senders to all leads**;
  monitoring + (if hybrid) Baileys link-health watch.

### Step 10 — Sync + document  *(me)*
- This is a separate repo (`HadiAbbasIsDev/Whatsapp-LeadGen`) we can't push to — land changes
  via fork+PR if needed; otherwise keep this runbook + the spec as the record.

---

## What blocks what
- **Steps 3–6 are blocked only on Step 1 (Kapso key + phone_number_id) + Step 2 (a webhook URL).**
- Everything else (the SOP) is already built and testable offline.
