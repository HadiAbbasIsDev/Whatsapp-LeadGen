# WhatsApp Lead Generation Bot

A self-hosted WhatsApp AI sales assistant powered by **OpenClaw (clawdbot)** and an **OpenRouter**-backed LLM.

When anyone messages your WhatsApp number, **Aria** (the bot) will:
- Answer product questions from the local catalog
- Qualify interested prospects
- Capture leads (name + email) into a local JSON database
- Remember returning users across sessions (built-in memory engine)
- Escalate complex queries to your human sales team

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
    allowFrom: ["+911234567890", "+441234567890"]
  }
}
```

---

## Updating the Product Catalog

Just edit `workspace/data/products.json`. Changes take effect on the next message —
no restart required (the agent reads the file on demand).
