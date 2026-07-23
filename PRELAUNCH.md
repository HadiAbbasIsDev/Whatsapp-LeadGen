# Pre-Launch Checklist — before opening the bot to real (public) customers

Right now the bot is **safe because only allowlisted numbers can reach it**
(owner + one tester). Everything below must be handled **before** you remove
that allowlist and let the general public message the bot.

Work top-to-bottom. Items marked **[blocker]** must be done; others are strongly
recommended.

---

## 1. [blocker] Close the secrets gap (exec can still read API keys)

**Why:** The bot must be able to run its scripts (`exec`), and that same power
means a determined prompt-injection attack could make it read secret files —
the Kapso / OpenRouter keys in `.env` and the DeepSeek key + gateway token in
`~/.openclaw/openclaw.json`. openclaw's own audit flagged the plaintext secrets.
Harmless today (allowlist), dangerous once the public can message the bot.

**Options (pick one, then test):**
- Restrict `exec` to an allowlist so the bot can only run the project's own
  scripts, not arbitrary shell like `cat .env`.
- Or move the secrets out of `exec`'s reach (e.g. a secrets store / separate
  sending service the LLM can't read from), so the scripts still work but the
  raw keys aren't catchable.

**Also:** rotate any key that was ever shared in plaintext (DeepSeek, and the
Anthropic key mentioned in `testapi.md`) before going live.

_Context: tool lockdown already done 2026-07-23 — see `[[agent-tool-lockdown]]`
memory / HISTORY. This item is the one residual it left open._

## 2. [blocker] Open the allowlist deliberately

- Decide who can message: keep a growing allowlist, or switch
  `channels.kapso-whatsapp.dmSecurity` to `open` for the true public.
- Live config: `~/.openclaw/openclaw.json` (not the repo template).
- Remember: opening the allowlist is what makes items 1, 3, 4 actually matter.

## 3. [blocker] Test with 2-3 real numbers at once

- The bot has only ever been tested one number at a time. Before public load,
  confirm concurrent chats work: labels, product sends, lead capture, handoff,
  and the complaint/vendor silence flows all behaving per-chat.
- Watch `workspace/data/` writes and `progress/*.log` under simultaneous use.

## 4. WhatsApp / Meta rules for cold outreach

- Free-text replies only work inside the 24-hour window; outside it, only the
  approved templates (`renovate_interest_followup` / `decor_moments_interest_followup`)
  may send. The follow-up runner already enforces this.
- Keep marketing/cold campaigns off until any needed templates are Meta-approved.
- Honor STOP/opt-out immediately (already enforced by `db.py can-message`).

## 5. Reliability so it survives real traffic

- Install the dashboard as a systemd service so it (and via it, the bot)
  auto-starts and restarts on crash — the `@reboot` cron helps but a supervisor
  is sturdier. (`admin/openclaw-admin.service`.)
- Confirm daily backups are still running (`~/wa-lead-gen-backups/backup.log`).
- After any `openclaw` update, re-run the patchers and confirm the category
  gate + label flows still work.

## 6. Polish / smaller items

- Template wording says **"Aliya"** while the bot is named **"Alia"** — decide
  whether to leave it or re-submit the template to Meta for approval.
- Coexistence / WhatsApp-app labels: see the checklist in `toimplement.md`.
- Fill or drop the 2 products with no image (ids 3519, 1454).

---

_Keep this file updated as items are done. Companion to `progress/STATUS.md`
(current state) and `progress/HISTORY.md` (dated log)._
