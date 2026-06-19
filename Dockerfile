# wa-lead-gen — WhatsApp furniture sales bot (openclaw + DeepSeek)
#
# The openclaw runtime patches (labels + media-queue + auto-image) are BAKED into
# this image at build time, so they can never be silently wiped by an update.
# openclaw is pinned to the version the patches target — do not bump without
# re-testing openclaw-patches/.
FROM node:22-bookworm-slim

# System deps: python3 + flask (dashboard), supervisor (process manager),
# procps (ps/pgrep used by the dashboard), tini (clean PID 1), ca-certificates.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-flask supervisor procps tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Pinned openclaw — MUST match openclaw-patches/. Never auto-update.
RUN npm install -g openclaw@2026.4.9

# Match the project's expected absolute paths so the hardcoded script paths in the
# skills/instructions work unchanged inside the container.
ENV HOME=/home/it-admin \
    OPENCLAW_AUTO_UPDATE=0 \
    PYTHONUNBUFFERED=1
WORKDIR /home/it-admin/wa-lead-gen

# App code
COPY . /home/it-admin/wa-lead-gen

# Register the bundled WhatsApp channel, then BAKE the runtime patches into the image.
RUN openclaw plugins install @openclaw/whatsapp 2>/dev/null || true \
 && python3 openclaw-patches/apply_patches.py \
 && chmod +x docker/entrypoint.sh scripts/*.sh 2>/dev/null || true

# 8088 = admin dashboard. The gateway's control port (18789) stays internal.
EXPOSE 8088

ENTRYPOINT ["/usr/bin/tini", "--", "/home/it-admin/wa-lead-gen/docker/entrypoint.sh"]
