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
        // Strip lines that leak internal mechanics or the model's own reasoning
        // to the customer: script/file names, tool-run/fail notices, category
        // checks, and "thinking out loud" ("Category is X — I can reply", "the
        // customer is asking...", "per AGENTS.md..."). None of these ever appear
        // in a genuine furniture reply, so removing whole matching lines is safe.
        try {
            const LEAK = /🛠|\brun\s+python3\b|\(workspace\)\s+failed|exec preflight|transcribe_voice\.py|notify_admins\.py|send_product\.py|send_template\.py|cold_outreach\.py|\bdb\.py\b|\bset-category\b|\bget-customer\b|\bupsert-customer\b|AGENTS\.md|SOUL\.md|SKILL\.md|IDENTITY\.md|products\.json|customers\.json|\bcategory is\b|current category|chat'?s category|per AGENTS|as per AGENTS|\bI can (reply|respond)\b|\bI (should|must|need to|will|can) (reply|respond|tag|mark|check the category)\b|the (customer|user) is (asking|request|want|looking)|not dump the|admins? notified|owner (is )?notified|team notified|now tagged|(^|[^a-z])tagg?ed (as )?["'`]?(hot ?leads?|complaints?|vendor|follow-?up|important|junk|new customer)|marked (as )?(hot ?leads?|complaints?|vendor)|(stay|go|going|remain|remaining|staying)\s+silent|the chat is now|until (the|an) (owner|human|agent|team)|owner reopen|human (takes over|will take over|will follow up)|flow complete|hand(ed|ing)?[ -]?off|going (quiet|silent)|\bre-?engag(ing|e|ed)\b|after being in|\bin follow-?up\b|\blet me (respond|reply|check|look|see|handle|pull|fetch|run|verify|confirm|update|tag|mark|use|search)\b|\bi(?:'ll| will| should| must| need to| can) (respond|reply|tag|mark|check|look up|search|run)\b|\bthe (customer|user|client) (is|was|has|wants|seems|sent)\b|\b(he|she|they) (is|are) (re-?engaging|asking|replying)\b|furniture sales whatsapp bot|conversation routing flows|handoff silence|whatsapp compliance gate|\b(first|second|third|fourth|fifth|sixth|seventh|eighth) flow\b|\bsender_id\b|mandatory image rule|never claim a send|action classification|\bAGENTS\b\s*:|system prompt|my instructions (are|say)|instruction file|customer database|\binbound message\b|\brecord (this|the) (inbound|message)\b|\bi(?:'ll|'ve|\s+(?:will|need to|have to|going to|should|must))\s+(?:record|log|check|update|save|store|fetch|pull|verify|run|query|tag|mark)\b|\bthen (check|record|update|run|query)\b|\bdatabase\b|\bhot ?leads?\b|\bvalid categor(y|ies)\b|\bcategor(y|ies) (remains|stays|is set|are)\b|\bdb schema\b|\bschema\b|acknowledged the customer|\bvalid options are\b|free-?text is blocked|24-?hour window|service window|can'?t deliver (any )?message|\bthe developers?\b|\bauthoris?ed to change\b|cold outreach|i don'?t have (the |enough )?context|no context (on|about|for|regarding)|previous conversation is from|\bconversation is from\b|\d+ days? ago|i (don'?t|do not) have (the )?(context|information|details|history)|i lack (the )?context|without (more |enough )?context|i couldn'?t process (that|this) message|unsupported media|looks like a sticker|i (can'?t|cannot) (view|open|see|process) (the|this|that) (image|photo|sticker|media|file)|did not produce a response|idle timeout|timeoutseconds|models\.providers|self-hosted provider|slow local|llm request timed out|\btimed out\b|please try again, or increase|the model (did not|didn'?t|failed)|\bagent run\b|run-specific timeout|\bstep [0-9]+\b|\bno reply context\b|reply context —|normal result for|match_photo|replied_to\.py|referred_product|product_retailer_id|ad'?s creative|ad click flow|whatsapp attaches|raw envelope|openclaw receives|no message text|inbound metadata|(actual )?message body|re-?send with the text|test event|\bNO_REPLY\b|handoff flow|following the \w+ flow|\bis a (new|returning|repeat|regular) customer\b|\b(he|she|they) (wants?|needs?|says?|said|mentioned|clicked|replied|prefers?|intends?)\b|\b(he|she|they) is (a|looking|new|interested|asking|deflecting|declining|brushing)\b|\blet me (ask|find|figure|clarify|note|gather|work out|greet|welcome|acknowledge|politely)\b/i;
            // A line naming THREE OR MORE of our internal labels is an internal
            // dump (e.g. "valid options are new customer, important, hot leads,
            // followup, junk, complaints, vendor, ahsan..."). Counting distinct
            // labels avoids false-flagging a customer who is simply called Ahmed.
            const LABEL = /\b(new customer|important|hot ?leads?|follow-?up|junk|complaints?|vendor|ahsan|ahmed|imran|rafay)\b/gi;
            // Never expose staff/owner/admin personal numbers to a customer. Our
            // public business + showroom numbers are allowed; anything else that
            // looks like a Pakistani mobile is redacted rather than dropped, so
            // the surrounding sentence still makes sense.
            const PUBLIC_NUMS = ["923326189654", "923059756149"];
            out = out.replace(/(\+?92[\s-]?3\d{2}[\s-]?\d{7})|(\b03\d{2}[\s-]?\d{7}\b)/g, (mm) => {
                const digits = mm.replace(/\D/g, "").replace(/^0/, "92");
                return PUBLIC_NUMS.includes(digits) ? mm : "our team";
            });
            const labelDump = (line) => {
                const m = String(line).match(LABEL);
                return !!m && new Set(m.map((s) => s.toLowerCase())).size >= 3;
            };
            if (out.indexOf("\n") !== -1 || LEAK.test(out) || labelDump(out)) {
                const lines = out.split("\n").filter((l) => !LEAK.test(l) && !labelDump(l));
                const stripped = lines.join("\n").trim();
                if (stripped !== out.trim()) {
                    // If the WHOLE message was internal narration, return "" so the
                    // caller suppresses the send entirely — an empty chat bubble is
                    // worse for the customer than no message at all.
                    out = stripped;
                    try { __scAppend(__scHome() + "/.openclaw/kapso-secrets.log",
                        JSON.stringify({ ts: new Date().toISOString(),
                                         action: stripped ? "stripped-internal-leak" : "suppressed-empty-leak" }) + "\n"); } catch {}
                }
            }
        } catch {}
        return out;
    } catch { return text; }
}
'''

BODY_ANCHOR = """    const client = await (params.clientFactory ?? createKapsoClient)(account, params.signal);
    const response = await client.messages.sendText({
        phoneNumberId: account.phoneNumberId,
        to,
        body: params.text,"""
BODY_REPLACE = """    const client = await (params.clientFactory ?? createKapsoClient)(account, params.signal);
    const __scBody = __scScrub(params.text);
    // Nothing left after scrubbing => the message was pure internal narration.
    // Skip the send rather than deliver an empty bubble to the customer.
    if (typeof __scBody === "string" && !__scBody.trim()) {
        return { messageId: `${to}:suppressed:${Date.now()}`, response: null, to };
    }
    const response = await client.messages.sendText({
        phoneNumberId: account.phoneNumberId,
        to,
        body: __scBody,"""


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
    # Also scrub MEDIA captions — a leak/error sent as a photo caption would
    # otherwise bypass the text scrubber entirely.
    cap_before = out.count("caption: params.text")
    out = out.replace("caption: params.text", "caption: __scScrub(params.text)")
    out = out.replace("{ caption: params.text }", "{ caption: __scScrub(params.text) }")
    print(f"scrubbed {cap_before} media-caption site(s)")
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
