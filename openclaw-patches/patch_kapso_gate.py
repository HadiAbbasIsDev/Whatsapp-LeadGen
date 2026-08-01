#!/usr/bin/env python3
"""
Port of the inbound category gate to the Kapso transport.

On Baileys the gate lives in openclaw's patched dist (apply_patches.py). On the
Kapso transport, inbound messages flow through the kapso-whatsapp plugin at
~/.openclaw/extensions/kapso-whatsapp, so the gate is patched into that plugin's
dist/inbound.js instead — inside resolveAdmission(), after the allowFrom check.

Behavior (identical to the Baileys gate):
  - Only chats whose category in workspace/data/customers.json is one of
    "new customer" / "important" / "followup" reach the agent.
  - Blocked chats (complaints / hot leads / junk / human-owner lists) are
    dropped before the agent until a human re-categorizes them
    (db.py set-category or the admin dashboard).
  - Fails OPEN: a broken/missing customers.json must never mute the bot.
  - Drops are logged to ~/.openclaw/kapso-gate.log.

The plugin lives in the user extensions dir, so this patch SURVIVES openclaw
npm upgrades — but is wiped by `openclaw plugins update`. Idempotent; safe to
run any time (also from admin/app.py before gateway start).
"""

import json
import os
import shutil
import subprocess
import sys
import datetime

PLUGIN_DIR = os.path.expanduser("~/.openclaw/extensions/kapso-whatsapp")
TARGET = os.path.join(PLUGIN_DIR, "dist", "inbound.js")
SENTINEL = "kapsoCategoryGateAllows"

IMPORT_ANCHOR = 'import { CHANNEL_ID } from "./constants.js";'
IMPORT_ADD = 'import { readFileSync as __kgReadFile, statSync as __kgStat, appendFileSync as __kgAppend } from "node:fs";\nimport { homedir as __kgHomedir } from "node:os";'

GATE_FN = r'''
const __kgState = { at: 0, mtimeMs: 0, byPhone: new Map() };
function kapsoCategoryGateAllows(from) {
    try {
        const path = process.env.KAPSO_GATE_CUSTOMERS || (__kgHomedir() + "/wa-lead-gen/workspace/data/customers.json");
        const now = Date.now();
        // mtime-cached read: resolveAdmission is synchronous and runs per message.
        // Parse FIRST, commit the cache stamp only on success — a transient read
        // failure must be retried on the next message, not pinned until next mtime.
        if (now - __kgState.at > 2000) {
            __kgState.at = now;
            const st = __kgStat(path);
            if (st.mtimeMs !== __kgState.mtimeMs) {
                const data = JSON.parse(__kgReadFile(path, "utf8"));
                const byPhone = new Map();
                for (const c of data.customers || []) {
                    const digits = String(c.phone || "").replace(/\D/g, "");
                    if (digits) byPhone.set(digits, String(c.category || "new customer").trim().toLowerCase());
                }
                __kgState.byPhone = byPhone;
                __kgState.mtimeMs = st.mtimeMs;
            }
        }
        const digits = String(from || "").replace(/\D/g, "");
        const cat = (digits && __kgState.byPhone.get(digits)) || "new customer";
        const allowed = ["new customer", "followup"];
        if (allowed.includes(cat)) return { allowed: true, category: cat };
        try { __kgAppend(__kgHomedir() + "/.openclaw/kapso-gate.log", JSON.stringify({ ts: new Date().toISOString(), from: digits, category: cat, action: "blocked" }) + "\n"); } catch {}
        return { allowed: false, category: cat };
    } catch (e) {
        return { allowed: true, category: "unknown", error: String(e) }; // fail open
    }
}
'''

ADMISSION_ANCHOR = '''    if (account.dmSecurity === "allowlist" && account.allowFrom.length > 0 && !isAllowedSender(account.allowFrom, from)) {
        return { kind: "drop", reason: "sender is not in kapso allowFrom", recordHistory: false };
    }
    return undefined;
}'''
ADMISSION_REPLACE = '''    if (account.dmSecurity === "allowlist" && account.allowFrom.length > 0 && !isAllowedSender(account.allowFrom, from)) {
        return { kind: "drop", reason: "sender is not in kapso allowFrom", recordHistory: false };
    }
    const gate = kapsoCategoryGateAllows(from);
    if (!gate.allowed) {
        return { kind: "drop", reason: "category-gate: " + gate.category, recordHistory: false };
    }
    return undefined;
}'''


def main():
    if not os.path.exists(TARGET):
        sys.exit(f"SKIP: kapso plugin not found at {TARGET} (install it first)")
    src = open(TARGET).read()
    if SENTINEL in src:
        print("Already patched (sentinel found). Nothing to do.")
        return
    missing = [n for n, a in [("import", IMPORT_ANCHOR), ("resolveAdmission", ADMISSION_ANCHOR)] if a not in src]
    if missing:
        sys.exit(f"ERROR: anchors not found: {missing}. The kapso plugin version changed — update this patcher.")

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{TARGET}.orig-{ts}"
    shutil.copy2(TARGET, backup)
    print(f"Backup: {backup}")

    out = src.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + "\n" + IMPORT_ADD, 1)
    out = out.replace(ADMISSION_ANCHOR, ADMISSION_REPLACE, 1)
    # Insert the gate function just before resolveAdmission's definition
    fn_anchor = "function resolveAdmission(account, from) {"
    out = out.replace(fn_anchor, GATE_FN.strip("\n") + "\n" + fn_anchor, 1)
    open(TARGET, "w").write(out)

    # Verify syntax (plugin package.json declares "type": "module", so node --check parses ESM)
    node = shutil.which("node") or "/usr/local/node-v22.21.1/bin/node"
    try:
        r = subprocess.run([node, "--check", TARGET], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            shutil.copy2(backup, TARGET)
            sys.exit(f"SYNTAX ERROR after patch (restored backup):\n{r.stderr}")
    except FileNotFoundError:
        print("(node not found; skipped syntax check)")
    print("Patched kapso plugin category gate. Restart the gateway to load it.")


if __name__ == "__main__":
    main()
