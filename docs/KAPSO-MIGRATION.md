# Kapso migration runbook (for the future VPS deployment)

We **tested the full Kapso migration** and it works — but only on a host with stable
internet + a permanent public URL. We rolled back to the Baileys setup for now because
this laptop's WiFi can't reach `api.kapso.ai` reliably and the free tunnel kept dying.
Do this migration **once you have a VPS**.

## Why Kapso (vs the current Baileys/personal connection)
- **Official WhatsApp Cloud API** (Meta Business Partner) — compliant, no ban risk.
  Baileys uses a personal/linked-device connection that can get flagged.
- Trade-off: it's webhook-based and needs a public URL; the in-WhatsApp "Lists" labels
  go away (replaced by Kapso contact metadata — see below).

## Hard prerequisites (all required)
1. **A VPS / always-on server** with stable internet (a laptop on WiFi does NOT work —
   outbound to `api.kapso.ai` must be reliable).
2. **A permanent public HTTPS URL** to the gateway (domain + TLS, the VPS public IP, or
   a *named* Cloudflare tunnel). NOT a `trycloudflare.com` quick tunnel — those are
   ephemeral, die often, and change URL, which silently breaks the webhook.
3. **A production (non-sandbox) Kapso number.** The sandbox number requires an
   "active sandbox session" to send and is for testing only.

## What's already in place
- Kapso plugin installed at `~/.openclaw/extensions/kapso-whatsapp` (dormant on 2026.4.9).
- Kapso secrets in `.env`: `KAPSO_API_KEY`, `KAPSO_WEBHOOK_SECRET`.
- A working Kapso config snapshot saved at `~/.openclaw/openclaw.json.kapso-config`.
- Proven: Kapso contact-metadata label sync (see below).

## Migration steps (on the VPS)
```bash
# 1. Kapso requires openclaw >= 2026.5.27 (our Baileys patches need 2026.4.9 — so
#    moving to Kapso means leaving the Baileys patches behind).
npm install -g openclaw@2026.6.8

# 2. Install the Kapso plugin
openclaw plugins install clawhub:@kapso/openclaw-whatsapp

# 3. Configure + register the webhook in one go (use your STABLE public URL + a real
#    phone_number_id from `openclaw kapso-whatsapp cli whatsapp numbers list`):
openclaw kapso-whatsapp setup \
  --api-key "$KAPSO_API_KEY" \
  --phone-number-id "<PROD_PHONE_NUMBER_ID>" \
  --webhook-url "https://<your-stable-domain>/kapso/webhook" \
  --webhook-secret "$KAPSO_WEBHOOK_SECRET" \
  --register-webhook --write-config

# 4. Lock to allowed senders + base URL
openclaw config set 'channels["kapso-whatsapp"].dmSecurity' '"allowlist"' --strict-json
openclaw config set 'channels["kapso-whatsapp"].allowFrom' '["+923362615506","923362615506"]' --strict-json
openclaw config set 'channels["kapso-whatsapp"].baseUrl' '"https://api.kapso.ai/meta/whatsapp"' --strict-json

# 5. Disable Baileys (it's a separate external plugin on 2026.6.8 anyway)
openclaw config set 'channels["whatsapp"].enabled' false --strict-json

# 6. Restart
openclaw gateway restart
```

## Code changes still needed for Kapso (not yet built)
- **Product images**: `workspace/send_product.py` uses the Baileys media-queue, which
  does NOT exist on Kapso. Rewrite it to send via Kapso's media API (the plugin's
  outbound send, or `POST .../meta/whatsapp/<phone_number_id>/messages`).
- **Label sync** (proven working via API, just needs wiring into `db.py`): instead of
  the Baileys `addChatLabel` reconciler, push the category into Kapso contact metadata:
  ```
  PATCH https://api.kapso.ai/platform/v1/whatsapp/contacts/<phone>
  X-API-Key: <KAPSO_API_KEY>
  { "contact": { "metadata": { "category": "hot leads" } } }
  ```
  The category then shows in the Kapso inbox. DB + admin dashboard are unchanged.

## Gotchas we hit (so you don't again)
- Ephemeral `trycloudflare.com` tunnels die and change URL → webhook breaks silently.
- Sandbox numbers: "Active sandbox session required to send messages" — use a real number.
- Inbound (Kapso → bot) was proven working; outbound failed only due to the laptop's
  flaky network to `api.kapso.ai`. A VPS fixes this.
- WhatsApp 24-hour rule: the bot can freely reply within 24h of the customer's last
  message; cold-starting a chat needs pre-approved template messages.

## Rolling back to Baileys (what we did)
```bash
npm install -g openclaw@2026.4.9
python3 openclaw-patches/apply_patches.py
# in ~/.openclaw/openclaw.json: channels.whatsapp.enabled=true, remove kapso-whatsapp
# channel/plugin entries, plugins.load.paths -> bundled whatsapp ext, update.auto={enabled:false}
openclaw gateway   # Baileys reconnects with existing creds (no QR rescan)
```
