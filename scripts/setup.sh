#!/bin/bash
#
# Setup script for Codex CLI OAuth automation
#
# What this does:
#   1. Checks prerequisites (Chrome, Codex CLI, Python + websockets)
#   2. Kills any existing Chrome on port 9222
#   3. Launches a fresh Chrome with remote debugging on :9222
#   4. Starts the Codex device-auth flow in the background
#   5. Prints the device code URL so the user can visit it manually
#      (or the automation script connects to the existing tab)
#
# After this script, run:
#   python3 scripts/login_via_chrome_cdp.py --page-id "$(curl -s http://127.0.0.1:9222/json | python3 -c "import json,sys; p=json.load(sys.stdin); print(p[0]['id'])")"
#

set -e

echo "============================================"
echo " Codex CLI OAuth — Automated Setup"
echo "============================================"
echo

# ─── 1. Prerequisites ─────────────────────────────────────────────────────────

echo "[1/5] Checking prerequisites..."

if ! command -v google-chrome &>/dev/null && ! ls /Applications/Google\ Chrome*.app &>/dev/null; then
    echo "ERROR: Google Chrome is not installed."
    exit 1
fi

if ! command -v codex &>/dev/null; then
    echo "ERROR: Codex CLI is not installed."
    echo "  Install: brew install openai-codex  (macOS)"
    exit 1
fi

PYLIBS=$(python3 -c "import websockets" 2>&1)
if [ $? -ne 0 ]; then
    echo "Installing websockets..."
    pip3 install websockets
fi

echo "  ✅ Chrome found"
echo "  ✅ Codex CLI found"
echo "  ✅ Python websockets available"
echo

# ─── 2. Clean up existing Chrome on port 9222 ────────────────────────────────

echo "[2/5] Cleaning up existing Chrome instances..."

pkill -f "Google Chrome.*remote-debugging-port=9222" 2>/dev/null || true
sleep 2
echo "  ✅ Port 9222 is free"
echo

# ─── 3. Launch fresh Chrome with CDP ─────────────────────────────────────────

echo "[3/5] Launching fresh Chrome with remote debugging..."

rm -rf /tmp/chrome_codex_fresh
mkdir -p /tmp/chrome_codex_fresh

open -na "Google Chrome" --args \
    --user-data-dir="/tmp/chrome_codex_fresh" \
    --remote-debugging-port=9222 \
    --remote-allow-origins="*" \
    --no-first-run \
    --new-window \
    "about:blank" 2>/dev/null

echo "  Chrome launching (waiting 10s for full startup)..."
sleep 10

# Verify Chrome started
PAGE_COUNT=$(curl -s http://127.0.0.1:9222/json | python3 -c "import json,sys; print(len([p for p in json.load(sys.stdin) if p['type']=='page']))" 2>/dev/null || echo "0")
if [ "$PAGE_COUNT" -eq "0" ]; then
    echo "  ⚠️  Chrome may not have started correctly. Trying again..."
    sleep 5
    PAGE_COUNT=$(curl -s http://127.0.0.1:9222/json | python3 -c "import json,sys; print(len([p for p in json.load(sys.stdin) if p['type']=='page']))" 2>/dev/null || echo "0")
fi

PAGE_ID=$(curl -s http://127.0.0.1:9222/json | python3 -c "import json,sys; p=json.load(sys.stdin); print(p[0]['id'])" 2>/dev/null)
PAGE_URL=$(curl -s http://127.0.0.1:9222/json | python3 -c "import json,sys; p=json.load(sys.stdin); print(p[0]['url'])" 2>/dev/null)

echo "  ✅ Chrome running on port 9222"
echo "  📄 Page ID: $PAGE_ID"
echo "  🔗 Page URL: $PAGE_URL"
echo

# ─── 4. Start Codex device auth ──────────────────────────────────────────────

echo "[4/5] Starting Codex device auth flow..."

# Give user the device code URL directly
echo "  To complete auth manually (optional):"
echo "  → Visit: https://auth.openai.com/codex/device"
echo "  → Enter the code shown by: codex login --device-auth"
echo
echo "  Alternatively, run the automation script:"
echo "  python3 scripts/login_via_chrome_cdp.py --page-id $PAGE_ID"
echo

# Start codex login in background and capture output
CODEX_OUTPUT="/tmp/codex_device_code.txt"
timeout 5 codex login --device-auth 2>&1 | tee "$CODEX_OUTPUT" &

echo "  ✅ Codex device auth started (background)"
echo

# ─── 5. Print connection info ────────────────────────────────────────────────

echo "[5/5] Connection info saved to /tmp/codex_setup_info.sh"

cat > /tmp/codex_setup_info.sh << EOF
export CDP_PAGE_ID="$PAGE_ID"
export CDP_URL="ws://127.0.0.1:9222/devtools/page/$PAGE_ID"
export CODEX_OUTPUT="$CODEX_OUTPUT"
EOF

echo "============================================"
echo " Next steps:"
echo "============================================"
echo
echo "  1. Start Codex device auth:"
echo "     codex login --device-auth"
echo
echo "  2. In another terminal, run the automation:"
echo "     CDP_PAGE_ID=\$(curl -s http://127.0.0.1:9222/json | python3 -c \"import json,sys; print(json.load(sys.stdin)[0]['id'])\")"
echo "     python3 scripts/login_via_chrome_cdp.py --page-id \$CDP_PAGE_ID"
echo
echo "  3. When MFA page appears, approve on your phone."
echo
echo "  4. Verify:"
echo "     codex login status"
echo "============================================"
