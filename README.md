# WhatsApp Lead Generation Bot

A self-hosted WhatsApp AI sales assistant powered by **OpenClaw (clawdbot)** and an **OpenRouter**-backed LLM.

When anyone messages your WhatsApp number, **Aria** (the bot) will:
- Answer product questions from the local catalog
- Qualify interested prospects
- Capture leads (name + email) into a local JSON database
- Remember returning users across sessions (built-in memory engine)
- Escalate complex queries to the owner (+923362615506) — the single registered owner contact

---

## Access & Security

The bot is locked to a single WhatsApp contact: **+923362615506**. This is the ONLY number that can receive alerts, escalations, or administrative messages. Do NOT add other numbers to the allowlist or configuration.

- No other phone number may be treated as owner, admin, escalation, or sales contact.
- The bot only talks to the customer and must never send messages or alerts to any number other than +923362615506.
- The legacy numbers +923110800256, +923332456988, and +923369381947 are stale and must NOT be used as owner/admin/escalation/sales/handoff contacts.

---

## Runtime patches (required for labels + images)

Stock openclaw cannot apply WhatsApp Business labels and its `message send --media`
is broken for WhatsApp. This repo patches the global openclaw install to fix both.
**These patches are wiped whenever openclaw is updated/reinstalled** — re-apply them:

```bash
python3 openclaw-patches/apply_patches.py   # idempotent; backs up + verifies
openclaw gateway                            # restart the gateway
```

`setup.sh` runs this automatically. See [openclaw-patches/README.md](openclaw-patches/README.md)
for details and the sanitized live-config template.

> **Using Docker?** The patches are **baked into the image at build time** (and openclaw
> is pinned), so they're permanent — you don't run this manually. See below.

---

## Run with Docker (recommended)

The whole bot runs in one container — openclaw (pinned + pre-patched), the SQLite
data store, and the admin dashboard, managed by supervisor.

```bash
git clone <this-repo> && cd wa-lead-gen
cp .env.example .env          # then edit .env: DEEPSEEK_API_KEY, ALLOWED_NUMBER, ADMIN_PASS
docker compose up -d --build
```

Then **link WhatsApp once** (scan the QR with the phone that will run the bot):

```bash
docker compose exec bot openclaw channels login --channel whatsapp
docker compose restart        # pick up the linked session
```

- **Admin dashboard:** http://localhost:8088 (login `admin` / your `ADMIN_PASS`) —
  Start/Stop the bot, see status, monitor customers by label.
- **Logs:** `docker compose logs -f` (dashboard) · `docker compose exec bot tail -f progress/gateway.log` (gateway).
- **Data persists** in named volumes (`openclaw-data` = WhatsApp creds/config/labels,
  `db-data` = SQLite + catalog, `backups`). Container restarts keep your session and data.

**Notes & limits**
- Each deployment links its **own** WhatsApp number (a linked session can't be shared)
  and uses its **own** API keys in `.env`.
- The gateway auto-starts with the container; the dashboard's kill-switch still works
  (it drives supervisor).
- `.env` is gitignored — never commit your keys.
- To back up: `docker compose exec bot bash scripts/backup.sh` (writes to the `backups` volume).

---

## Project Structure

```
wa-lead-gen/
├── openclaw.json                   # Main OpenClaw configuration
├── setup.sh                        # One-time setup script
└── workspace/
    ├── AGENTS.md                   # Core agent instructions (conversation flow)
    ├── SOUL.md                     # Aria's personality & hard limits
    ├── USER.md                     # Your business profile — edit this
    ├── MEMORY.md                   # Long-term memory (auto-managed)
    ├── data/
    │   ├── products.json           # ← Your product catalog — edit this
    │   └── leads.json              # Captured leads (auto-managed)
    └── skills/
        ├── product-catalog/
        │   └── SKILL.md            # Product lookup & FAQ handling
        └── lead-capture/
            └── SKILL.md            # Lead qualification & storage
```

---

## Quick Start

### Prerequisites

- Node.js 22+ (`node --version`)
- An [OpenRouter](https://openrouter.ai) API key
- A WhatsApp account / number to link (a spare number is recommended)

### 1. Run Setup

```bash
cd wa-lead-gen
bash setup.sh
```

This installs the `openclaw` CLI, saves your API key, and installs the WhatsApp plugin.

### 2. Customise Your Products

Edit `workspace/data/products.json` with your actual products, prices, and FAQ answers.

### 3. Fill in Business Details

Edit `workspace/USER.md` — add your business name, escalation email, and timezone.

### 4. Link WhatsApp

```bash
openclaw channels login --channel whatsapp
```

Scan the QR code using WhatsApp → Linked Devices → Link a Device.

### 5. Start the Bot

```bash
openclaw gateway
```

Send any message to your linked WhatsApp number — Aria will respond.

---

## Memory

Memory is handled entirely by OpenClaw's built-in engine (SQLite, no extra setup):

| What's stored | Where |
|---|---|
| Long-term user facts | `workspace/MEMORY.md` |
| Daily session notes | `workspace/memory/YYYY-MM-DD.md` (auto-created) |
| Semantic search index | SQLite db inside `~/.openclaw/` |

The agent automatically searches memory at the start of each session to personalise responses for returning users.

---

## Leads

Captured leads are stored in `workspace/data/leads.json`:

```json
{
  "leads": [
    {
      "id": "LEAD-1713340800000",
      "captured_at": "2026-04-17T10:00:00Z",
      "channel": "whatsapp",
      "phone": "+911234567890",
      "name": "Jane Doe",
      "email": "jane@example.com",
      "products_of_interest": ["P002"],
      "pain_point": "Spreadsheet tracking is too slow",
      "intent": "trial",
      "status": "new"
    }
  ]
}
```

---

## Changing the LLM Model

Edit `openclaw.json` and change the `model.primary` value:

```json5
 {
  primary: "arcee-ai/trinity-large-preview:free"
  // primary: "openrouter/openai/gpt-4o"
  // primary: "openrouter/google/gemini-2.0-flash-001"
  // primary: "openrouter/auto"   ← cost-optimised auto-routing (default)
}
```

---

## Restricting Access

To prevent anyone from messaging the bot, switch to allowlist mode in `openclaw.json`:

```json5
channels: {
  whatsapp: {
    dmPolicy: "allowlist",
    allowFrom: ["+923362615506"]
  }
}
```

`allowFrom` should ONLY contain the owner: `["+923362615506"]`. Do NOT add other phone numbers.

---

## Updating the Product Catalog

Just edit `workspace/data/products.json`. Changes take effect on the next message —
no restart required (the agent reads the file on demand).
