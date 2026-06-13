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
        print("Already patched (sentinel found). Nothing to do.")
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
