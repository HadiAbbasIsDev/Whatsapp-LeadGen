# Handoff — Kapso migration + full lead-gen SOP

**For:** Hadi · **Branch base:** `a12db0f "Dockerize: one-container deploy with baked-in patches"`
**What this branch adds:** moves the bot off Baileys onto the **official Kapso** WhatsApp
integration (Cloud API), upgrades OpenClaw, and builds out the **full 6-branch lead-gen SOP**
(the flowchart) with a follow-up scheduler. Last updated 2026-06-29.

> Read these three docs in order: **this file** → `ARCHITECTURE.md` → `KAPSO-GO-LIVE-PROCESS.md`.

---

## 1. TL;DR — what changed and why
- **Transport:** Baileys (QR, ban-risky) → **Kapso official plugin** (`clawhub:@kapso/openclaw-whatsapp`,
  official Meta Cloud API). Webhook-based, per-sender isolation built in.
- **OpenClaw:** upgraded `2026.4.9 → 2026.6.10` (the plugin needs ≥`2026.5.27`).
- **SOP:** the client flowchart is now implemented — full label taxonomy, human-owner routing,
  human handoff (bot goes quiet), and a weekly-nudge → junk follow-up scheduler.
- **Status:** wired + running on the **Kapso sandbox**; not yet tested with a live message, and a few
  branches have gaps (see §6). Everything is built so the data + timer engine is verified.

## 2. The bot flow (simple)
```
 Customer messages WhatsApp
        │
        ▼  save them in the DB (new contact = "new customer")
   Already owned by a human?  ──yes──►  bot stays silent
        │ no
        ▼  AI: "what does this lead need?"  → one of 6 branches:
   1 BUY/NEGOTIATE → mark HOT LEADS → alert a human (bot quiet)
   2 ORDER NOW     → collect name/address/phone → order on renovate.pk → confirm
   3 NO REPLY      → mark FOLLOWUP → nudge weekly ×3 → still silent → JUNK
   4 OWNER LIST 7d → send a nudge → else → the NO-REPLY flow
   5 STORE LOCATION→ send addresses → mark FOLLOWUP (→ weekly ×3 → JUNK)
   6 COMPLAINT     → capture details → mark COMPLAINS → alert a human (bot quiet)

 Background (hourly): the scheduler chases "due" leads and junks them after 3 weeks.
 Per customer it tracks: list (new/Hot Leads/Followup/Junk/Complains), owner, next-followup time.
```

## 3. What changed since `a12db0f` (file by file)
| File | Change |
|---|---|
| `Dockerfile` | OpenClaw → `2026.6.10`; install official Kapso plugin from ClawHub **with retries + fatal-if-missing**; **normalize CRLF** on `.sh` (Windows-clone fix); `rm` the build-time partial `openclaw.json` so the entrypoint regenerates a full config. |
| `docker/openclaw.config.docker.json` | Migrated to the 2026.6.10 plugin schema: `plugins.entries` adds `kapso-whatsapp`; removed the obsolete `plugins.load`/`plugins.installs` blocks; kept `gateway.mode: local`. |
| `docker/supervisord.conf` | Added the **`[program:followup]`** scheduler process. |
| `workspace/db.py` | Full taxonomy (`+ followup, junk, complains`) + owner lists (`ahsan/ahmed/imran/rafay`) + columns `owner, human_owned, followup_stage, next_followup_at` + migration + commands `set-owner / set-human-owned / schedule-followup / clear-followup / due-followups`; `customers.json` mirror extended. |
| `workspace/followup_scheduler.py` *(new)* | Hourly loop: weekly nudge ×3 → `junk`. Send is behind a **seam** (`data/outbox.jsonl`) until pointed at Kapso send. |
| `workspace/skills/customer-categories/SKILL.md` | Rewritten as the **6-branch SOP router**. |
| `workspace/skills/human-handoff/SKILL.md` | Now **pauses the bot** (`human_owned`) + configurable owners; removed the hardcoded single-number conflict. |
| `workspace/skills/store-location/`, `complaint/` *(new)* | The location + complaint branches. |
| `docs/*` *(new)* | This handoff, `ARCHITECTURE.md`, `KAPSO-GO-LIVE-PROCESS.md`, the design spec. |

## 4. How to build & run
```bash
cp .env.example .env          # fill: DEEPSEEK_API_KEY, ALLOWED_NUMBER, ADMIN_PASS
docker compose up -d --build  # builds OpenClaw 2026.6.10 + Kapso plugin + SOP
```
- Dashboard: `http://localhost:8088` (`admin` / `ADMIN_PASS`).
- Verify the gateway: `docker exec wa-lead-gen sh -lc "openclaw kapso-whatsapp doctor"`.
- The container runs three processes via supervisord: **gateway** (the bot), **followup** (scheduler), **dashboard**.

## 5. Connecting Kapso (sandbox today, prod later)
The official plugin is **webhook-based**, so Kapso needs a public HTTPS URL that reaches the
gateway's `/kapso/webhook` (gateway HTTP port `18789`).
- **Sandbox test (what's wired now):** a `cloudflared` tunnel → `:18789`, then
  `openclaw kapso-whatsapp setup --api-key … --phone-number-id 597907523413541 --webhook-url <tunnel>/kapso/webhook --webhook-secret … --register-webhook --write-config`.
  You still activate your test phone in the **Kapso UI → WhatsApp → Sandbox** (add number, text the 6-char code).
- **Production:** swap the throwaway tunnel for **Tailscale Funnel or a domain (Caddy)**, connect a real
  number via **Coexistence BYON**, and persist the channel config into the template (see §6).
- Full step-by-step: **`KAPSO-GO-LIVE-PROCESS.md`**.

## 6. DONE vs PENDING (honest)
**Done & verified:** OpenClaw upgrade, Kapso plugin loads + webhook registered (sandbox), the
6-branch router, DB taxonomy/owner/handoff state, the weekly→junk scheduler logic, dashboard.

**Pending / known gaps:**
- **No live message test yet** — built ≠ proven; one real reply de-risks everything.
- **Branch 2 (renovate.pk order placement)** — the skill describes it but the actual web-order step isn't built/verified.
- **Branch 5 addresses** — wire the real store addresses (currently placeholder text).
- **Human handoff is a soft rule** (AI instruction), not a hard code gate — should become enforced.
- **Scheduler send** — still queues to `outbox.jsonl`; point it at Kapso send.
- **Durability** — the sandbox channel config + webhook live in the running container/volume, **not** the
  template, so a `down -v` rebuild needs re-wiring. Persist into `docker/openclaw.config.docker.json` + `.env`.
- **Native WhatsApp Lists** (the labels your human team sees in WhatsApp) — deferred; needs Baileys under
  **Coexistence** + re-validating `openclaw-patches/` against 2026.6.10 (the Baileys runtime moved). The bot
  works without it because the **DB is the source of truth**; Lists are just a mirror.
- `plugins.allow` is empty (works, logs a warning) — set it explicitly once you confirm nothing needed drops.

## 7. Gotchas that bit us (so they don't bite you)
- **CRLF:** cloning on Windows makes `.sh` shebangs `bash\r` → container crash-loops. The Dockerfile now strips CR.
- **ClawHub 503:** the Kapso plugin install hits transient rate-limit 503s → the Dockerfile retries and fails hard if it can't install (so it never silently ships without the plugin).
- **exit 78:** build-time `openclaw plugins install` writes a *partial* `~/.openclaw/openclaw.json` (no `gateway.mode`); with the volume + entrypoint "only-generate-if-missing", that pre-empted the real config. Fixed by `rm`-ing it at the end of the build.
- **Tunnel fragility:** `cloudflared` quick tunnels die on restart and change URL each time → the webhook then points at a dead URL. Use a stable tunnel for anything real.

## 8. Where things live
- Bot brain: `workspace/skills/*` + `workspace/db.py` (state) + `workspace/followup_scheduler.py` (timers).
- Config: `docker/openclaw.config.docker.json` (template) → entrypoint generates `~/.openclaw/openclaw.json`.
- Secrets: `.env` (gitignored — never commit).
- Docs: `docs/HANDOFF.md` (this), `docs/ARCHITECTURE.md`, `docs/KAPSO-GO-LIVE-PROCESS.md`, `docs/superpowers/specs/…`.
