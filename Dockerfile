# wa-lead-gen — WhatsApp furniture sales bot (openclaw + DeepSeek)
#
# The openclaw runtime patches (labels + media-queue + auto-image) are BAKED into
# this image at build time. openclaw is upgraded to 2026.6.10 to run the OFFICIAL
# Kapso channel plugin (clawhub:@kapso/openclaw-whatsapp, requires >= 2026.5.27).
# NOTE: the baked patches target the Baileys runtime; their anchors must be
# re-validated against 2026.6.10 (the build keeps the patch step non-fatal until then).
FROM node:22-bookworm-slim

# System deps: python3 + flask (dashboard), supervisor (process manager),
# procps (ps/pgrep used by the dashboard), tini (clean PID 1), ca-certificates.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-flask supervisor procps tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# openclaw upgraded for the official Kapso plugin (requires >= 2026.5.27). Never auto-update.
RUN npm install -g openclaw@2026.6.10

# Match the project's expected absolute paths so the hardcoded script paths in the
# skills/instructions work unchanged inside the container.
ENV HOME=/home/it-admin \
    OPENCLAW_AUTO_UPDATE=0 \
    PYTHONUNBUFFERED=1
WORKDIR /home/it-admin/wa-lead-gen

# App code
COPY . /home/it-admin/wa-lead-gen

# Repo cloned on Windows ships .sh with CRLF, which breaks `#!/usr/bin/env bash`.
RUN sed -i 's/\r$//' docker/entrypoint.sh scripts/*.sh 2>/dev/null || true

# Bundled Baileys channel (kept for the labels projection / hybrid).
RUN openclaw plugins install @openclaw/whatsapp 2>/dev/null || true

# Official Kapso channel plugin from ClawHub — WITH RETRIES (ClawHub returns transient
# 503 "rate limit" errors) and FATAL if it never installs. The whole image exists to run
# this plugin, so a transient blip must never silently ship an image without it.
RUN for i in 1 2 3 4 5 6; do \
      openclaw plugins install clawhub:@kapso/openclaw-whatsapp && break; \
      echo "[build] kapso install attempt $i failed; retrying in $((i*10))s..."; \
      sleep $((i*10)); \
    done; \
    openclaw plugins list 2>/dev/null | grep -qi kapso \
      || { echo "[build] FATAL: kapso-whatsapp not installed after retries"; exit 1; }

# Bake the runtime patches. NON-FATAL: the Baileys runtime moved on 2026.6.10, so the
# labels patch anchors are re-validated separately (the gateway runs fine without them).
RUN ( python3 openclaw-patches/apply_patches.py || echo "[build] WARN: apply_patches.py failed — re-validate anchors on 2026.6.10" ) \
 && chmod +x docker/entrypoint.sh scripts/*.sh 2>/dev/null || true

# The build-time plugin install writes a PARTIAL ~/.openclaw/openclaw.json (plugin
# entries only, no gateway.mode), which would pre-empt the entrypoint's first-run
# config generation and block the gateway (exit 78). Remove it — the plugin files in
# ~/.openclaw/extensions persist, and the entrypoint regenerates the full config.
RUN rm -f /home/it-admin/.openclaw/openclaw.json

# 8088 = admin dashboard. The gateway's control port (18789) stays internal.
EXPOSE 8088

ENTRYPOINT ["/usr/bin/tini", "--", "/home/it-admin/wa-lead-gen/docker/entrypoint.sh"]
