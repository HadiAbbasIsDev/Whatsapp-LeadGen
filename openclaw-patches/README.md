# openclaw runtime patches

This bot relies on two capabilities that stock **openclaw** does not provide, so
we patch its bundled WhatsApp runtime. These patches live in the **global openclaw
install** (`/usr/local/.../node_modules/openclaw/dist/login-<hash>.js`), which means
**they are wiped by any `npm update -g` / reinstall of openclaw, or when you move to
a new machine.** This folder makes them reproducible.

## What the patches add

1. **WhatsApp Business "Lists" / labels** — stock openclaw can't set them.
   - A *label probe* captures each label's ID into `~/.openclaw/whatsapp-labels.json`.
   - A *reconciler* reads `workspace/data/customers.json` and applies the matching
     label to each customer's chat via the chat's **LID** (required by the Lists UI).
2. **Reliable product images** — openclaw's `message send --media` is **broken for
   WhatsApp** (it silently drops the media).
   - A *media-queue watcher* polls `~/.openclaw/wa-media-queue.jsonl` and sends images
     through the raw Baileys socket (`sock.sendMessage(jid, {image, caption})`).
   - An *auto-image safety net* attaches a product's photo automatically whenever the
     bot's reply mentions it (`item-XXXX` / "Item XXXX"), deduped with the queue.

`workspace/send_product.py` enqueues jobs for the media-queue watcher.

## How to apply (after installing or updating openclaw)

```bash
python3 openclaw-patches/apply_patches.py
openclaw gateway        # restart the gateway so the patch loads
```

The patcher is **idempotent** (safe to re-run), finds the openclaw install
automatically, backs up the original file, injects the blocks from `blocks/`, and
verifies the result with `node --check` (auto-restores on failure).

If it reports *"anchors not found"*, openclaw changed the surrounding code in that
version — update the anchor strings in `apply_patches.py` and the blocks in `blocks/`.

## Files

- `apply_patches.py` — idempotent patcher.
- `blocks/imports.js` — `fs`/`os` imports added to the runtime.
- `blocks/runtime-block.js` — label probe + reconciler + media-queue + auto-image net.
- `openclaw.config.template.json` — sanitized copy of `~/.openclaw/openclaw.json`
  (the **live** config the gateway uses — the project's root `openclaw.json` is just a
  template). Fill in `YOUR_*` placeholders and copy it to `~/.openclaw/openclaw.json`.

## Config notes

- Model: `deepseek/deepseek-v4-pro` via a custom `openai-completions` provider
  (`baseUrl: https://api.deepseek.com`, key in `env.DEEPSEEK_API_KEY`).
- Access is locked to a single owner/customer number via `channels.whatsapp.allowFrom`.
- Run the gateway with node on PATH:
  `export PATH="/usr/local/node-v22.21.1/bin:$PATH"; openclaw gateway`.
