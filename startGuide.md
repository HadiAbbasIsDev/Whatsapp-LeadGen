# Start Guide — renovate.pk WhatsApp Bot ("Alia")

A plain-English guide to starting, checking, and fixing the bot. No deep
technical knowledge needed.

## What this bot is made of

The bot has **three parts**, all running on this machine:

| Part | What it does | Must it run? |
|---|---|---|
| **Gateway** (`openclaw gateway`) | The bot's brain — receives messages, runs the AI agent, sends replies | **Yes, always** |
| **Poller** (`scripts/kapso_poller.py`) | Fetches incoming WhatsApp messages from Kapso and hands them to the gateway | **Yes** (on Kapso without a public URL — the current setup) |
| **Dashboard** (`admin/app.py`) | Web page at http://localhost:8088 to start/stop the bot and manage customers | Optional but handy |

Which WhatsApp connection is active is controlled by one line in the `.env`
file: `WA_TRANSPORT=kapso` (official Kapso/Cloud API — current) or
`WA_TRANSPORT=baileys` (the old linked-device setup).

## Starting the bot (after a reboot, or any time)

Nothing starts automatically when the laptop boots. To start everything:

```bash
cd /home/it-admin/wa-lead-gen
bash scripts/start-bot.sh
```

That's it. The script is safe to run repeatedly — it skips whatever is
already running, re-applies the needed patches, starts the gateway, and (on
Kapso) starts the poller. Add `--with-dashboard` to also start the web
dashboard:

```bash
bash scripts/start-bot.sh --with-dashboard
```

**Alternative:** start the dashboard first (`python3 admin/app.py`), open
http://localhost:8088, and click **Start** — it starts the gateway and the
poller for you.

**Optional — true auto-start on boot:** run `crontab -e` and add this line:

```
@reboot bash /home/it-admin/wa-lead-gen/scripts/start-bot.sh
```

## How to check it's working

1. **Quick check:** send a WhatsApp message to the bot's number. On Kapso
   (current), that is the **Kapso sandbox number** — your phone must have an
   active sandbox session with it. A reply within ~10–60 seconds = healthy.
2. **Process check:**
   ```bash
   pgrep -f "openclaw-gatewa[y]" && echo gateway OK
   pgrep -f "kapso_pol[l]er" && echo poller OK
   ```
3. **Dashboard check:** http://localhost:8088 → shows running / connected /
   poller status and last inbound time.

## Where the logs are

| Log | What's in it |
|---|---|
| `progress/gateway.log` | Gateway + agent activity |
| `progress/kapso-poller.log` | Every message the poller fetched and delivered |
| `~/.openclaw/kapso-gate.log` | Messages blocked because the chat is tagged (complaints etc.) |
| `workspace/data/category_audit.log` | Every customer category change, with timestamps |

## Bot not replying? Check in this order

1. **Is it blocked on purpose?** If the chat is tagged `complaints`,
   `hot leads`, `junk`, or a person's name, the bot stays silent by design.
   Check: `python3 workspace/db.py get-customer --phone +92...`
   Unblock: `python3 workspace/db.py set-category --phone +92... --category "new customer"`
2. **Is everything running?** `bash scripts/start-bot.sh` (it reports health
   at the end and restarts anything that died).
3. **Kapso sandbox session expired?** The sandbox only talks to phones with
   an active session — rejoin the sandbox from the Kapso dashboard.
4. **Still stuck?** Look at the last lines of `progress/gateway.log` and
   `progress/kapso-poller.log` for errors.

## Switching between Kapso and the old Baileys setup

- **To Kapso** (current): `bash scripts/kapso-cutover.sh` — see
  `docs/KAPSO-CUTOVER.md` for the full checklist.
- **Back to Baileys**: `bash scripts/kapso-rollback.sh` — restores the old
  setup exactly; no QR re-scan needed.

Only one transport is active at a time. While on Kapso, the old bot number
(+923092082395) does not answer; customers use the Kapso number.

## Going to production (one-time, later)

The bot currently uses Kapso's **sandbox** number (testing only). For real
customers: add a dedicated number in the Kapso dashboard, put its id in
`.env` as `KAPSO_PHONE_NUMBER_ID`, and restart (`bash scripts/start-bot.sh`
after stopping, or the dashboard Stop/Start). Cold follow-up campaigns need
Meta-approved template messages — keep them off until those are approved.
