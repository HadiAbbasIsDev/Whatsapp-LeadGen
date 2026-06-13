#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  wa-lead-gen  —  OpenClaw WhatsApp Lead Generation Bot Setup
# ─────────────────────────────────────────────────────────────
set -e

echo ""
echo "=== WhatsApp Lead Gen Bot — Setup ==="
echo ""

# ── 1. Check Node.js version ─────────────────────────────────
NODE_MAJOR=$(node -e "process.stdout.write(process.version.split('.')[0].replace('v',''))" 2>/dev/null || echo "0")
if [ "$NODE_MAJOR" -lt 22 ]; then
  echo "ERROR: Node.js 22 or higher is required."
  echo "       Current version: $(node --version 2>/dev/null || echo 'not found')"
  echo "       Install from: https://nodejs.org"
  exit 1
fi
echo "✓ Node.js $(node --version) detected"

# ── 2. Install openclaw CLI globally if not already installed ─
if ! command -v openclaw &>/dev/null; then
  echo "→ Installing openclaw CLI globally..."
  npm install -g clawdbot
  echo "✓ openclaw installed"
else
  echo "✓ openclaw already installed ($(openclaw --version 2>/dev/null || echo 'version unknown'))"
fi

# ── 3. Prompt for OpenRouter API key ─────────────────────────
CONFIG_FILE="$(dirname "$0")/openclaw.json"

if grep -q "YOUR_OPENROUTER_API_KEY_HERE" "$CONFIG_FILE"; then
  echo ""
  echo "Enter your OpenRouter API key (from https://openrouter.ai/keys):"
  read -r -p "  OPENROUTER_API_KEY: " OR_KEY
  if [ -n "$OR_KEY" ]; then
    # Replace placeholder in openclaw.json
    sed -i "s|YOUR_OPENROUTER_API_KEY_HERE|$OR_KEY|g" "$CONFIG_FILE"
    echo "✓ API key saved to openclaw.json"
  else
    echo "  (skipped — remember to add your key to openclaw.json before starting)"
  fi
else
  echo "✓ OpenRouter API key already set in openclaw.json"
fi

# ── 4. Install WhatsApp channel plugin ───────────────────────
echo ""
echo "→ Installing WhatsApp channel plugin..."
openclaw plugins install @openclaw/whatsapp 2>/dev/null || echo "  (already installed or skipping)"
echo "✓ WhatsApp plugin ready"

# ── 5. Apply openclaw runtime patches (labels + reliable images) ──
echo ""
echo "→ Applying openclaw runtime patches (labels + image sending)..."
python3 "$(dirname "$0")/openclaw-patches/apply_patches.py" || \
  echo "  (patch step skipped/failed — see openclaw-patches/README.md)"

# ── 6. Ensure workspace data files exist ─────────────────────
LEADS_FILE="$(dirname "$0")/workspace/data/leads.json"
if [ ! -f "$LEADS_FILE" ]; then
  echo '{"leads":[]}' > "$LEADS_FILE"
  echo "✓ Created empty leads.json"
fi

# ── Done ─────────────────────────────────────────────────────
echo ""
echo "══════════════════════════════════════════════════"
echo "  Setup complete!  Next steps:"
echo ""
echo "  1. Edit workspace/USER.md with your business name,"
echo "     escalation email, and timezone."
echo ""
echo "  2. Edit workspace/data/products.json to reflect"
echo "     your actual products and pricing."
echo ""
echo "  3. Link your WhatsApp number:"
echo "       cd wa-lead-gen"
echo "       openclaw channels login --channel whatsapp"
echo "     Scan the QR code with WhatsApp on your phone."
echo ""
echo "  4. Start the bot:"
echo "       openclaw gateway"
echo ""
echo "  5. Send a WhatsApp message to your linked number"
echo "     and Aria will respond!"
echo "══════════════════════════════════════════════════"
echo ""
