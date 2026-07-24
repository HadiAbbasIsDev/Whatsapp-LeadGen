#!/usr/bin/env python3
"""
Outbound secrets scrubber for the Kapso transport.

Defense against secret exfiltration: even if a prompt-injection makes the agent
read a secret (via exec `cat .env` etc.), it must never be able to SEND it to a
customer. This patches the kapso plugin's outbound text send (sendKapsoText) so
every message body is scrubbed of any known secret VALUE (from .env and
~/.openclaw/openclaw.json) just before it leaves for Kapso.

Design:
  - Redacts only EXACT secret values (>=12 chars) -> "[REDACTED]". No pattern
    guessing, so zero false positives on normal furniture chatter.
  - FAIL-OPEN and exception-proof: any error in the scrubber returns the original
    text. A bug here can never stop the bot from replying — worst case, it just
    doesn't redact. (The real protection is the allowlist + not leaking at all;
    this is defense-in-depth.)
  - Each redaction is logged to ~/.openclaw/kapso-secrets.log so the owner knows
    the bot tried to emit a secret (a strong sign of an attack/bug).

Survives openclaw npm upgrades (plugin lives in the user extensions dir); wiped
by `openclaw plugins update`. Idempotent; re-applied by start-bot.sh / the
dashboard before the gateway starts.
"""

import datetime
import os
import shutil
import subprocess
import sys

PLUGIN_DIR = os.path.expanduser("~/.openclaw/extensions/kapso-whatsapp")
TARGET = os.path.join(PLUGIN_DIR, "dist", "outbound.js")
SENTINEL = "__scScrub"

IMPORT_ANCHOR = 'import { normalizeWhatsAppTarget } from "./targets.js";'
IMPORT_ADD = (
    'import { readFileSync as __scRead, appendFileSync as __scAppend } from "node:fs";\n'
    'import { homedir as __scHome } from "node:os";'
)

SCRUB_FN = r'''
let __scSecrets = null;
function __scLoadSecrets() {
    if (__scSecrets) return __scSecrets;
    const vals = new Set();
    try {
        const env = __scRead(process.env.WA_LEADGEN_ENV || (__scHome() + "/wa-lead-gen/.env"), "utf8");
        for (const line of env.split("\n")) {
            const m = line.match(/^\s*[A-Za-z_][A-Za-z0-9_]*\s*=\s*(.+)\s*$/);
            if (m) { const v = m[1].trim().replace(/^["']|["']$/g, ""); if (v.length >= 12) vals.add(v); }
        }
    } catch {}
    try {
        const conf = JSON.parse(__scRead(__scHome() + "/.openclaw/openclaw.json", "utf8"));
        const walk = (o) => {
            if (!o || typeof o !== "object") return;
            for (const k of Object.keys(o)) {
                const v = o[k];
                if (typeof v === "string" && v.length >= 16 && /token|key|secret|apikey|password|pass/i.test(k)) vals.add(v);
                else if (v && typeof v === "object") walk(v);
            }
        };
        walk(conf);
    } catch {}
    __scSecrets = [...vals].filter(Boolean);
    return __scSecrets;
}
function __scScrub(text) {
    try {
        if (typeof text !== "string" || !text) return text;
        let out = text, hit = false;
        for (const s of __scLoadSecrets()) {
            if (s && out.indexOf(s) !== -1) { out = out.split(s).join("[REDACTED]"); hit = true; }
        }
        if (hit) {
            try { __scAppend(__scHome() + "/.openclaw/kapso-secrets.log",
                JSON.stringify({ ts: new Date().toISOString(), action: "redacted-outbound-secret" }) + "\n"); } catch {}
        }
        // Strip lines that leak internal tool/command mechanics to the customer
        // (script names, tool-run/fail notices). Customers never legitimately see
        // these, so removing whole matching lines has no false positives.
        try {
            const LEAK = /🛠|\brun\s+python3\b|\(workspace\)\s+failed|exec preflight|transcribe_voice\.py|notify_admins\.py|send_product\.py|send_template\.py|cold_outreach\.py|\bdb\.py\b|\bset-category\b/i;
            if (out.indexOf("\n") !== -1 || LEAK.test(out)) {
                const lines = out.split("\n").filter((l) => !LEAK.test(l));
                const stripped = lines.join("\n").trim();
                if (stripped !== out.trim()) {
                    // If the whole message was internal leakage, collapse to a
                    // zero-width space (non-empty so the send API is happy, but
                    // invisible to the customer) rather than sending the leak.
                    out = stripped || "​";
                    try { __scAppend(__scHome() + "/.openclaw/kapso-secrets.log",
                        JSON.stringify({ ts: new Date().toISOString(), action: "stripped-internal-leak" }) + "\n"); } catch {}
                }
            }
        } catch {}
        return out;
    } catch { return text; }
}
'''

BODY_ANCHOR = "body: params.text,"
BODY_REPLACE = "body: __scScrub(params.text),"


def main():
    if not os.path.exists(TARGET):
        sys.exit(f"SKIP: kapso plugin not found at {TARGET} (install it first)")
    src = open(TARGET).read()
    if SENTINEL in src:
        print("Already patched (sentinel found). Nothing to do.")
        return
    missing = [n for n, a in [("import", IMPORT_ANCHOR), ("body", BODY_ANCHOR)] if a not in src]
    if missing:
        sys.exit(f"ERROR: anchors not found: {missing}. The kapso plugin changed — update this patcher.")

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{TARGET}.orig-{ts}"
    shutil.copy2(TARGET, backup)
    print(f"Backup: {backup}")

    out = src.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + "\n" + IMPORT_ADD, 1)
    out = out.replace(IMPORT_ADD, IMPORT_ADD + "\n" + SCRUB_FN.strip("\n"), 1)
    out = out.replace(BODY_ANCHOR, BODY_REPLACE, 1)
    open(TARGET, "w").write(out)

    node = shutil.which("node") or "/usr/local/node-v22.21.1/bin/node"
    try:
        r = subprocess.run([node, "--check", TARGET], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            shutil.copy2(backup, TARGET)
            sys.exit(f"SYNTAX ERROR after patch (restored backup):\n{r.stderr}")
    except FileNotFoundError:
        print("(node not found; skipped syntax check)")
    print("Patched kapso outbound secrets scrubber. Restart the gateway to load it.")


if __name__ == "__main__":
    main()
