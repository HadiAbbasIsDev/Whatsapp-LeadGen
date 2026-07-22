#!/usr/bin/env python3
"""
Re-apply the wa-lead-gen openclaw runtime patches.

openclaw (the WhatsApp gateway) does NOT natively support two things this bot
needs, so we patch its bundled WhatsApp runtime (`dist/login-<hash>.js`):

  1. WhatsApp Business "Lists"/labels  — a probe that captures label IDs and a
     reconciler that applies labels from workspace/data/customers.json.
  2. Reliable image sending            — openclaw's `message send --media` is
     broken for WhatsApp, so a media-queue watcher sends images via the raw
     Baileys socket, plus an auto-image safety net that attaches a product's
     photo whenever the bot mentions it.

These patches live in the GLOBAL openclaw install, so they are wiped by any
`npm update -g`/reinstall. Run this script after installing/updating openclaw:

    python3 openclaw-patches/apply_patches.py

It is idempotent (safe to run repeatedly), backs up the original, and verifies
syntax with `node --check`. Then restart the gateway: `openclaw gateway`.
"""

import datetime
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IMPORTS = open(os.path.join(HERE, "blocks", "imports.js")).read()
RUNTIME_BLOCK = open(os.path.join(HERE, "blocks", "runtime-block.js")).read()

SENTINEL = "__ocWriteFile"  # present only after our patch
IMPORTS_ANCHOR = 'import { randomUUID } from "node:crypto";'
SENDTRACK_ANCHOR = "\t\trememberOutboundMessage(jid, result);\n\t\treturn result;"
SENDTRACK_REPLACE = (
    "\t\trememberOutboundMessage(jid, result);\n"
    "\t\ttry { if (globalThis.__ocAutoSendImages) await globalThis.__ocAutoSendImages(jid, content); } catch {}\n"
    "\t\treturn result;"
)
BLOCK_ANCHOR = 'const detachConnectionUpdate = attachEmitterListener(sock.ev, "connection.update", handleConnectionUpdate);'
BOT_LABELS = '["new customer", "important", "hot leads", "followup", "junk", "complaints", "ahsan", "ahmed", "imran", "rafay"]'
VOICE_FALLBACK_SENTINEL = "__ocDownloadInboundAudioFallback"
LABEL_SYNC_SENTINEL = "__ocProbeStartedAt"  # bump when the label-probe block changes shape
VOICE_FALLBACK_ANCHOR = "\tconst enqueueInboundMessage = async (msg, inbound, enriched) => {"
VOICE_FALLBACK_BLOCK = r'''	const __ocDownloadInboundAudioFallback = async (msg, enriched) => {
		if (!enriched || enriched.mediaPath || enriched.mediaType || enriched.body !== "<media:audio>") return enriched;
		try {
			const maxBytes = (typeof options.mediaMaxMb === "number" && options.mediaMaxMb > 0 ? options.mediaMaxMb : 50) * 1024 * 1024;
			let buffer;
			let lastError;
			for (const ctx of [
				{ reuploadRequest: typeof sock.updateMediaMessage === "function" ? sock.updateMediaMessage.bind(sock) : sock.updateMediaMessage, logger: sock.logger },
				{ logger: sock.logger }
			]) {
				try {
					buffer = await downloadMediaMessage(msg, "buffer", {}, ctx);
					if (buffer) break;
				} catch (e) {
					lastError = e;
				}
			}
			if (!buffer) throw lastError || new Error("downloadMediaMessage returned no buffer");
			const saved = await saveMediaBuffer(buffer, "audio/ogg; codecs=opus", "inbound", maxBytes, "voice-note.ogg");
			inboundLogger.info({ mediaPath: saved.path, mediaType: saved.contentType, size: saved.size }, "[voice-download] saved inbound audio");
			return {
				...enriched,
				mediaPath: saved.path,
				mediaType: saved.contentType || "audio/ogg; codecs=opus",
				mediaFileName: "voice-note.ogg"
			};
		} catch (e) {
			try { inboundLogger.warn({ error: String(e) }, "[voice-download] fallback failed"); } catch {}
			return enriched;
		}
	};
'''
ENRICH_ANCHOR = "\t\t\tconst enriched = await enrichInboundMessage(msg);\n\t\t\tif (!enriched) continue;\n\t\t\tawait enqueueInboundMessage(msg, inbound, enriched);"
ENRICH_REPLACE = "\t\t\tlet enriched = await enrichInboundMessage(msg);\n\t\t\tif (!enriched) continue;\n\t\t\tenriched = await __ocDownloadInboundAudioFallback(msg, enriched);\n\t\t\tawait enqueueInboundMessage(msg, inbound, enriched);"

# Hard category gate: inbound DMs only reach the agent when the chat's DB category
# is in GATE_ALLOWED. Blocked chats (complaints / hot leads / junk / human-owner
# lists) get no read receipt and no agent reply until a human re-categorizes them.
GATE_SENTINEL = "__ocCategoryGateAllows"
RECONCILER_SENTINEL = "removed extra label"  # bump when the reconciler block changes shape
GATE_BLOCK = r'''	const __ocCategoryGateAllows = (msg, inbound) => {
		try {
			if (!inbound || inbound.group || msg?.key?.fromMe) return true; // gate customer DMs only
			const digits = String(inbound.senderE164 || inbound.from || "").replace(/\D/g, "");
			if (!digits) return true;
			const ws = (() => { try { return loadConfig()?.agents?.defaults?.workspace; } catch { return null; } })() || (__ocHomedir() + "/wa-lead-gen/workspace");
			const data = JSON.parse(__ocReadFile(ws.replace(/\/$/, "") + "/data/customers.json", "utf8"));
			const row = (data.customers || []).find((c) => String(c.phone || "").replace(/\D/g, "") === digits);
			const cat = String((row && row.category) || "new customer").trim().toLowerCase();
			const allowed = ["new customer", "important", "followup"];
			if (allowed.includes(cat)) return true;
			inboundLogger.info({ from: inbound.from, category: cat }, "[category-gate] blocked inbound; agent stays silent until the chat returns to an allowed category");
			return false;
		} catch (e) {
			try { inboundLogger.warn({ error: String(e) }, "[category-gate] check failed; allowing message"); } catch {}
			return true; // fail open: a broken gate must not silence the whole bot
		}
	};
'''
GATE_CALL_ANCHOR = "\t\t\tconst inbound = await normalizeInboundMessage(msg);\n\t\t\tif (!inbound) continue;\n\t\t\tawait maybeMarkInboundAsRead(inbound);"
GATE_CALL_REPLACE = "\t\t\tconst inbound = await normalizeInboundMessage(msg);\n\t\t\tif (!inbound) continue;\n\t\t\tif (!__ocCategoryGateAllows(msg, inbound)) continue;\n\t\t\tawait maybeMarkInboundAsRead(inbound);"


def replace_marked_section(src, block_source, start_marker, end_marker, what):
    """Swap the BEGIN..END section in src with the same section from block_source."""
    old_start = src.find(start_marker)
    old_end = src.find(end_marker, old_start)
    new_start = block_source.find(start_marker)
    new_end = block_source.find(end_marker, new_start)
    if min(old_start, old_end, new_start, new_end) < 0:
        sys.exit(f"ERROR: could not refresh {what} block (markers not found)")
    old_end += len(end_marker)
    new_end += len(end_marker)
    return src[:old_start] + block_source[new_start:new_end] + src[old_end:]


def apply_category_gate(src):
    """Insert the category-gate function + its call site. Idempotent."""
    if GATE_SENTINEL in src:
        return src
    if VOICE_FALLBACK_ANCHOR not in src:
        sys.exit("ERROR: could not find anchor for category gate function.")
    src = src.replace(VOICE_FALLBACK_ANCHOR, GATE_BLOCK + VOICE_FALLBACK_ANCHOR, 1)
    if GATE_CALL_ANCHOR not in src:
        sys.exit("ERROR: could not find call-site anchor for category gate.")
    return src.replace(GATE_CALL_ANCHOR, GATE_CALL_REPLACE, 1)


def find_login_file():
    roots = []
    try:
        r = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            roots.append(r.stdout.strip())
    except Exception:
        pass
    roots += [
        "/usr/local/node-v22.21.1/lib/node_modules",
        "/usr/local/lib/node_modules",
        "/usr/lib/node_modules",
        os.path.expanduser("~/.npm-global/lib/node_modules"),
    ]
    candidates = []
    for root in roots:
        for pkg in ("openclaw", "clawdbot"):
            candidates += glob.glob(os.path.join(root, pkg, "dist", "login-*.js"))
    seen, uniq = set(), []
    for c in candidates:
        rp = os.path.realpath(c)
        if rp not in seen and os.path.exists(rp):
            seen.add(rp)
            uniq.append(rp)
    for c in uniq:
        try:
            if BLOCK_ANCHOR in open(c).read():
                return c
        except Exception:
            pass
    return None


def node_bin():
    return shutil.which("node") or "/usr/local/node-v22.21.1/bin/node"


def main():
    target = None
    if len(sys.argv) > 2 and sys.argv[1] == "--target":
        target = sys.argv[2]
    target = target or find_login_file()
    if not target:
        sys.exit("ERROR: could not find openclaw's WhatsApp runtime (login-*.js). Is openclaw installed? Try `npm root -g`.")

    print(f"Target: {target}")
    src = open(target).read()

    if SENTINEL in src:
        refreshed = src
        if "__ocExecFile" not in refreshed:
            refreshed = refreshed.replace(IMPORTS_ANCHOR, IMPORTS_ANCHOR + '\nimport { execFile as __ocExecFile } from "node:child_process";', 1)
        if LABEL_SYNC_SENTINEL not in refreshed:
            refreshed = replace_marked_section(
                refreshed, RUNTIME_BLOCK,
                "\t// --- BEGIN openclaw label-probe (read-only) ---",
                "\t// --- END openclaw label-probe ---",
                "two-way WhatsApp label sync")
        if RECONCILER_SENTINEL not in refreshed:
            refreshed = replace_marked_section(
                refreshed, RUNTIME_BLOCK,
                "\t// --- BEGIN openclaw label-reconciler (customers.json -> WhatsApp labels) ---",
                "\t// --- END openclaw label-reconciler ---",
                "idempotent label reconciler")
        refreshed = apply_category_gate(refreshed)
        refreshed = refreshed.replace(
            'const __ocCats = ["new customer", "important", "hot leads"];',
            f"const __ocCats = {BOT_LABELS};",
        )
        refreshed = refreshed.replace("\n\t\t\t\tif (__ocApplied.get(jid) === cat) continue;", "")
        refreshed = refreshed.replace(
            "\t\t\t\t\t\tawait sock.addChatLabel(jid, labelId);",
            "\t\t\t\t\t\tif (__ocApplied.get(jid) !== cat) await sock.addChatLabel(jid, labelId);",
        )
        refreshed = refreshed.replace(
            'if (lab && lab.name && !lab.deleted) map[String(lab.name).trim().toLowerCase()] = String(lab.id);',
            'if (lab && lab.name && !lab.deleted) { const name = String(lab.name).trim().toLowerCase(); map[name === "complains" ? "complaints" : name] = String(lab.id); }',
        )
        if VOICE_FALLBACK_SENTINEL not in refreshed:
            if VOICE_FALLBACK_ANCHOR not in refreshed:
                sys.exit("ERROR: could not find anchor for inbound voice fallback in already-patched runtime.")
            refreshed = refreshed.replace(VOICE_FALLBACK_ANCHOR, VOICE_FALLBACK_BLOCK + VOICE_FALLBACK_ANCHOR, 1)
        if ENRICH_ANCHOR in refreshed:
            refreshed = refreshed.replace(ENRICH_ANCHOR, ENRICH_REPLACE, 1)
        if refreshed == src:
            print("Already patched (sentinel found). Nothing to do.")
            return
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = f"{target}.refresh-{ts}"
        shutil.copy2(target, backup)
        print(f"Already patched; refreshing label exclusivity. Backup: {backup}")
        open(target, "w").write(refreshed)
        print("Refreshed. Verifying syntax with node --check...")
        try:
            r = subprocess.run([node_bin(), "--check", target], capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                print("OK syntax valid.")
            else:
                shutil.copy2(backup, target)
                sys.exit(f"SYNTAX ERROR after refresh (restored backup):\n{r.stderr}")
        except Exception as e:
            print(f"(could not run node --check: {e})")
        print("Done. Now restart the gateway:  openclaw gateway")
        return

    missing = [n for n, a in [("imports", IMPORTS_ANCHOR), ("sendTrackedMessage", SENDTRACK_ANCHOR), ("runtime block", BLOCK_ANCHOR)] if a not in src]
    if missing:
        sys.exit(f"ERROR: anchors not found: {missing}.\nopenclaw's code likely changed in this version — update openclaw-patches/ to match.")

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{target}.orig-{ts}"
    shutil.copy2(target, backup)
    print(f"Backup: {backup}")

    out = src.replace(IMPORTS_ANCHOR, IMPORTS_ANCHOR + "\n" + IMPORTS.rstrip("\n"), 1)
    out = out.replace(SENDTRACK_ANCHOR, SENDTRACK_REPLACE, 1)
    out = out.replace(BLOCK_ANCHOR, BLOCK_ANCHOR + "\n" + RUNTIME_BLOCK.rstrip("\n"), 1)
    if VOICE_FALLBACK_ANCHOR not in out:
        sys.exit("ERROR: could not find anchor for inbound voice fallback.")
    out = out.replace(VOICE_FALLBACK_ANCHOR, VOICE_FALLBACK_BLOCK + VOICE_FALLBACK_ANCHOR, 1)
    if ENRICH_ANCHOR not in out:
        sys.exit("ERROR: could not find enqueue anchor for inbound voice fallback.")
    out = out.replace(ENRICH_ANCHOR, ENRICH_REPLACE, 1)
    out = apply_category_gate(out)
    open(target, "w").write(out)

    print("Patched. Verifying syntax with node --check...")
    try:
        r = subprocess.run([node_bin(), "--check", target], capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            print("OK syntax valid.")
        else:
            shutil.copy2(backup, target)
            sys.exit(f"SYNTAX ERROR after patch (restored backup):\n{r.stderr}")
    except Exception as e:
        print(f"(could not run node --check: {e})")

    print("Done. Now restart the gateway:  openclaw gateway")


if __name__ == "__main__":
    main()
