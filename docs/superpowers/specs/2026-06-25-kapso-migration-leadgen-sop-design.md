# Design — Migrate Whatsapp-LeadGen from Baileys to Kapso + build the full lead-gen SOP

- **Date:** 2026-06-25
- **Status:** Approved design (pre-implementation). Next step: implementation plan via writing-plans.
- **Repo:** `Whatsapp-LeadGen` (OpenClaw furniture/lead-gen bot for renovate.pk)

---

## 1. Context — current state

The bot today runs on OpenClaw's **native bundled `@openclaw/whatsapp` channel (Baileys)**:

- Channel paired by QR; `channels.whatsapp` locked to a single `${ALLOWED_NUMBER}` via allowlist.
- **OpenClaw pinned to `2026.4.9`**, with `openclaw-patches/` baked into the image against *exactly* that version (Dockerfile warns: do not bump without re-testing).
- Baked patches do two Baileys-specific things:
  1. **WhatsApp Lists/labels** — a reconciler reads `workspace/data/customers.json` and applies WhatsApp Business **Lists** to each chat via the chat's **LID** through the raw Baileys socket.
  2. **Reliable media** — works around OpenClaw's broken `message send --media` by pushing images through the raw Baileys socket (`wa-media-queue.jsonl` watcher + an auto-image net that attaches a product photo when a reply mentions `item-XXXX`).
- **Source of truth = SQLite (`leadgen.db`) via `db.py`.** `customers.json` is an **auto-generated mirror** that `db.py` rewrites; the label reconciler consumes that mirror. The live `customer_categories` skill currently supports **3 categories**: `new customer`, `important`, `hot leads`.

The target operating model (provided as a flowchart) is richer than what is coded today — see §6.

## 2. Goals

1. Replace the Baileys **messaging transport** with **Kapso** (BSP on the WhatsApp Cloud API), using OpenClaw's native `@kapso/openclaw-whatsapp` plugin.
2. Connect a **new dedicated client number** via Kapso **Coexistence** BYON (number stays active in the WhatsApp Business app *and* on the Cloud API).
3. **Preserve WhatsApp Lists** by keeping the existing Baileys reconciler alive as a **Coexistence companion** that projects DB state onto the human-facing Lists.
4. Build out the **full lead-gen SOP** from the flowchart (full taxonomy, time-based follow-ups, human-handoff routing).
5. Phased rollout: **local-first** (tunnel) → **VPS** (stable webhook).

## 3. Non-goals

- Migrating an existing live number (greenfield number only — no cutover).
- Syncing WhatsApp Lists *back* into Kapso/Cloud API (Coexistence does not sync labels to the API side; Lists remain an app-side projection).
- Replacing the SQLite source-of-truth model or the admin dashboard framework.

## 4. Locked decisions

| Decision | Choice |
|---|---|
| Transport | Kapso native plugin `@kapso/openclaw-whatsapp` |
| OpenClaw version | **Upgrade** `2026.4.9` → plugin's minimum floor (note: ≥`2026.5.27`; confirm exact in Phase 0) |
| Number | New dedicated number, onboarded via Kapso **Coexistence** embedded signup |
| Labels | Keep Baileys reconciler as a **muzzled Coexistence companion** (hybrid, both stacks live from the start) |
| Sender model | **Phased** — allowlist-locked locally, open to all leads at VPS go-live + per-sender isolation |
| Environment | Local + tunnel first, VPS + Caddy later |
| Scope | Transport migration **+ full SOP** (taxonomy + scheduler + handoff) |

## 5. Architecture — components (each isolated, one job)

**Core principle:** the **SQLite DB (`leadgen.db`) is the single source of truth.** `customers.json`, the WhatsApp Lists, the scheduler, and the handoff gate are all consumers of DB state. This is what makes the fragile Baileys companion safe to depend on — it can drop without affecting routing or follow-ups.

1. **Kapso channel** — `@kapso/openclaw-whatsapp` on upgraded OpenClaw.
   - *Does:* all inbound lead messages → bot; all outbound replies + media via Kapso send API.
   - *Depends:* Kapso WABA (new number, Coexistence), webhook ingress, OpenClaw ≥ floor.
2. **Baileys label-projection companion** — existing reconciler + `runtime-block.js`, channel kept loaded but **muzzled** (inbound disabled so it never replies).
   - *Does:* links as a Coexistence companion device; reads `customers.json`; applies WhatsApp Lists to chats for the human team. Read-only projection of DB state.
   - *Depends:* Business app active on the number (Coexistence), Baileys link health, `customers.json`.
3. **State store** — `db.py` / `leadgen.db`, extended.
   - *Does:* one record per phone — `category` (full taxonomy), `name`, `lead_score`, `last_seen`, follow-up fields (`followup_stage`, `next_followup_at`), `human_owned` flag, `owner` (ahsan/ahmed/imran/rafay). Rewrites `customers.json` mirror.
   - *Depends:* SQLite.
4. **Intent router (skills)** — extend `customer-categories` + `lead-capture`, add branch skills.
   - *Does:* classify each inbound into the 6 SOP branches and apply the correct action + category transition.
   - *Depends:* the agent (LLM) + `db.py`.
5. **Follow-up scheduler** *(new)* — a **supervisord-managed Python loop** reading the DB (NOT OpenClaw cron — avoids the known agentTurn hang/zombie issue).
   - *Does:* weekly nudge (≤3 weeks) for `followup`/location chats; 7-day-dead nudge for owner-list chats; auto-escalate to `junk` after 3 weeks with no reply. Sends via Kapso.
   - *Depends:* `db.py` state, Kapso send.
6. **Human-handoff gate** *(new)*.
   - *Does:* when category ∈ {`hot leads`, `complains`} or chat is owner-assigned → set `human_owned=true` → bot stops auto-replying on that chat + `notify_admins.py` pings the human; timers keep monitoring.
   - *Depends:* `db.py` status flag, a responder gate, `notify_admins.py`.
7. **Order / location / complaint flows** — preserve existing place-order (collect name/address/phone → renovate.pk → place order → confirm); add location-list and complaint branches.
8. **Outbound product media** — `send_product.py` re-pointed from the Baileys media-queue to **Kapso send**; the Baileys media hack is retired. The auto-image *logic* (detect `item-XXXX`) stays.
9. **Config / entrypoint + webhook ingress** — entrypoint enables both channels (Kapso primary, Baileys muzzled), Kapso creds + webhook env, per-sender session scoping (`session.dmScope: per-channel-peer`). Phase-1 tunnel → Phase-4 Caddy + domain.
10. **Admin dashboard** — `admin/app.py`, extended to surface the new taxonomy + timer/handoff state.

## 6. SOP mapping (flowchart → behavior)

Lists/categories: **Hot Leads** (human attention), **Followup** (ongoing), **Junk** (no response after 3 wks), **Complains** (complaints), and human-owner lists **Ahsan / Ahmed / Imran / Rafay**. (`new customer` remains the default on first contact.)

| # | Trigger | Action | Category / state |
|---|---|---|---|
| 1 | Negotiation / pricing / customization / AI-can't-answer / needs call or site visit | Mark **Hot Leads** → human takes over (bot silent) | `hot leads`, `human_owned=true`, notify |
| 2 | Wants to place order directly | Collect name + delivery address + phone → place order on renovate.pk → confirm | (order flow; stays bot-handled) |
| 3 | Not responding | Mark **Followup** → weekly nudge ≤3 wks → if reply, go to #1/#2; else **Junk** | `followup` → `junk` |
| 4 | Chat in a human-owner list, dead 7 days | AI sends follow-up → if reply, #1/#2; else non-responsive flow (#3) | owner list → (#3) |
| 5 | Requires store location | Send all location addresses → Mark **Followup** → weekly ≤3 wks → reply #1/#2 else **Junk** | `followup` → `junk` |
| 6 | Has a complaint | Ask for details → Mark **Complains** → human takes over | `complains`, `human_owned=true`, notify |

## 7. Data flows

- **Inbound:** lead → Kapso (Cloud API) → webhook → ingress → bot (Kapso channel) → `db.py upsert-customer` + intent classify → if `human_owned`: stay silent (timers still monitor); else agent replies via Kapso + applies category transition in DB → `db.py` rewrites `customers.json` → Baileys reconciler mirrors to WhatsApp Lists.
- **Coexistence mirror:** the same inbound also appears in the Business app; the reconciler (companion) sees the chat and sets its List.
- **Follow-up:** scheduler tick → DB query for `next_followup_at ≤ now` → send nudge via Kapso → increment `followup_stage` / set next `+1 week` → after 3 weeks no reply → `category=junk` → mirror → reconciler.
- **Handoff:** intent = hot-lead/complaint/owner-assigned → DB `human_owned=true` + category + owner → `notify_admins` → bot silent → reconciler shows the List → human works in the Business app.
- **Outbound media:** `send_product.py` → Kapso media send → lead.

## 8. Build order (messaging-first, then SOP)

- **Phase 0 — de-risk (no client number yet).** Confirm the plugin's exact OpenClaw floor + config shape; confirm a Baileys companion can link under Coexistence (the fragile assumption); re-validate `apply_patches.py` anchors against the upgraded OpenClaw build. **Exit:** all three confirmed or mitigations chosen.
- **Phase 1 — transport (local).** Upgraded image + Kapso plugin; onboard the new number via Kapso Coexistence BYON; Kapso webhook → tunnel → local container; senders locked to test numbers; Baileys muzzled + reconciler live. **Exit:** two-way messaging + media via Kapso + existing label sync proven; zero double-replies.
- **Phase 2 — SOP state machine (local).** Extend `db.py` taxonomy/fields; author the 6-branch router + transitions; handoff pausing + notify; order/location/complaint flows. **Exit:** each branch behaves per §6.
- **Phase 3 — scheduler (local).** Weekly×3 + 7-day-dead + junk escalation as a supervisord DB loop; validated with compressed intervals. **Exit:** timers fire and transition state correctly.
- **Phase 4 — VPS go-live.** Same image → VPS; stable webhook (Caddy + domain); open senders to all leads; per-sender isolation validated; monitoring + Baileys-link health watch. **Exit:** the 50-msg/3-sender test passes against the live client number.

## 9. Risks / must-verify

| ID | Risk | Mitigation |
|---|---|---|
| R1 | Baileys companion link under Coexistence may be unlinked/flagged by Meta | Fully decoupled from messaging (Kapso runs regardless); DB is SoT so logic is unaffected; monitor link health; internal-tags fallback ready |
| R2 | OpenClaw upgrade breaks baked patch anchors | Re-validate/update `apply_patches.py` anchors in Phase 0 |
| R3 | Scheduler reliability (OpenClaw cron = agentTurn that can hang/leak zombies) | DB-driven **supervisord Python loop**, not OpenClaw cron |
| R4 | Sender isolation / memory partition historically weak (intermittent scoping) | `dmScope: per-channel-peer` + explicit cross-sender-leak validation; don't trust, test |
| R5 | Double-handling (both channels live) | Baileys channel inbound disabled; explicit "Baileys never replies" test |
| R6 | Kapso plugin per-sender session mapping + media send correctness | Verify in Phase 1 |

## 10. Testing

- **Standing bar:** 50-message capability test across 3 senders.
- **Per-branch:** one test for each of the 6 SOP branches (§6).
- **Timers:** compressed-interval tests for weekly×3 and 7-day-dead → junk escalation.
- **Handoff:** bot goes silent + human notified + correct List shown.
- **Label projection:** DB change → WhatsApp List updates; with Baileys down, bot logic still runs and the List re-syncs on reconnect.
- **Isolation:** zero cross-sender leakage (no contact reads back another's data).
- **Webhook:** reliability through the tunnel (Phase 1) and Caddy (Phase 4).

## 11. Open items to confirm in Phase 0

1. Exact minimum OpenClaw version for `@kapso/openclaw-whatsapp` and its config schema.
2. Whether a Baileys companion reliably links to a Coexistence number (and survives), or whether labels should fall back to internal-tags-only.
3. Kapso send API shape for text + media (to re-point `send_product.py`).
4. How the Kapso plugin maps inbound senders → OpenClaw sessions (for isolation).
