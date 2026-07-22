# Kapso cutover runbook (branch: `Kapso`)

Replaces ONLY the WhatsApp transport (Baileys → Kapso Cloud API). Agent
prompts, memory, skills, DB, tools, and business logic are untouched.

## What changed on this branch

| Piece | Baileys (before) | Kapso (after) |
|---|---|---|
| Inbound | patched gateway (`monitorWebInbox`) | kapso-whatsapp plugin webhook `/kapso/webhook` (real webhook or local polling relay) |
| Category gate | patch in openclaw dist (`apply_patches.py`) | patch in the plugin (`openclaw-patches/patch_kapso_gate.py`) — same allowlist, same fail-open |
| Product images | in-gateway media queue (`wa-media-queue.jsonl`) | direct Cloud API link sends (`send_product.py` kapso path; catalog URLs are public) |
| Category → WhatsApp | labels reconciler (WhatsApp "Lists") | `db.py set-category` PATCHes Kapso **contact metadata** (visible in Kapso inbox) |
| WhatsApp → category | owner edits the chat label on the phone | **gone on Cloud API** — unblock via the admin dashboard (`POST /api/customer/category`), `db.py set-category`, or the agent in an unblocked chat |
| Owner alerts | `openclaw message send --channel whatsapp` | direct Kapso API send (`notify_admins.py`) |
| Voice notes | patched Baileys media download | Kapso delivers media URLs and often a ready transcript (`transcriptSource`) |

The switch is `WA_TRANSPORT` in `.env` (read by `workspace/kapso.py`); the
gateway side is switched by the openclaw config transform in the cutover script.

## Prerequisites

- `.env`: `KAPSO_API_KEY` (verified live), `KAPSO_PHONE_NUMBER_ID`,
  `KAPSO_WEBHOOK_SECRET` — all present.
- **Current account state (2026-07-22): sandbox number only** (id already in
  `.env` as `KAPSO_PHONE_NUMBER_ID`; listed as "Sandbox WhatsApp"). Testing
  works against the sandbox with your phone's active sandbox session.
  **Go-live needs a dedicated production number added in the Kapso dashboard**
  — then update `KAPSO_PHONE_NUMBER_ID`.
- Inbound path: no public URL needed — `scripts/kapso_poller.py` polls the
  platform API and replays messages to the plugin's webhook on loopback,
  HMAC-signed. If/when you have a VPS or named Cloudflare tunnel, set
  `KAPSO_PUBLIC_URL` in `.env` and the cutover registers a real webhook instead.

## Cutover (maintenance window, ~5 min offline)

```bash
git switch Kapso
scripts/kapso-cutover.sh     # stops gateway, installs openclaw@2026.6.33 (extended-stable),
                             # transforms config, patches the gate, starts gateway (+ poller)
```

Pinned version: `2026.6.33` (extended-stable — same June line the plugin was
proven on, plus security fixes). Override: `OPENCLAW_KAPSO_VERSION=2026.6.8`
(the exact June-tested build).

## Test checklist (after cutover)

- [ ] OpenClaw starts; log shows `registered Kapso webhook route /kapso/webhook`
- [ ] Poller log (`progress/kapso-poller.log`) shows polling without errors
- [ ] WhatsApp text → agent replies (inbound + outbound + agent logic)
- [ ] Product request → image + caption arrives (`send_product.py` kapso path)
- [ ] Complaint flow → tagged + bot silent; `~/.openclaw/kapso-gate.log` shows the block
- [ ] `python3 workspace/db.py set-category --phone +92… --category "new customer"`
      → unblocks; category visible in Kapso inbox (contact metadata)
- [ ] Voice note → transcript or media reaches the agent
- [ ] Memory: reference an earlier detail, agent recalls it
- [ ] MCP/tools: any existing tool call still works
- [ ] No secrets in git (`git diff main..Kapso` shows no keys; `.env` untouched by git)

## Known behavior changes (Cloud API reality, not bugs)

1. **No WhatsApp Lists/labels.** Categories live in SQLite (+ Kapso contact
   metadata). Owner re-categorizes via `db.py set-category`, the agent, or the
   admin dashboard — NOT by editing labels on the phone.
2. **24-hour rule**: outside 24h since the customer's last message, free-form
   sends are rejected — cold follow-ups need pre-approved template messages
   (not implemented; keep cold cadences off until templates are approved).
3. **Sandbox limits**: sandbox can only converse with numbers holding an
   active sandbox session; production number required for real customers.
4. Auto-image safety net (bot mentions item-XXXX → photo auto-attached) does
   not exist on Kapso; images depend on the agent calling `send_product.py`.

## Rollback (any time)

```bash
scripts/kapso-rollback.sh    # openclaw@2026.4.9 + Baileys patches + restored config
```

Baileys credentials are untouched by the cutover; the session reconnects
without a QR rescan. (June-tested path.)
