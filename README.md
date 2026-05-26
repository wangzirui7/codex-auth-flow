# Codex CLI OAuth Device Auth — Automated Chrome Login

> Automate Codex CLI OAuth login using Chrome DevTools Protocol (CDP) via computer use,
> no manual browser interaction needed after the OAuth device flow starts.

## TL;DR

```bash
# 1. Install deps
pip3 install websockets

# 2. Setup Chrome + Codex
bash scripts/setup.sh

# 3. Run automation (in a new terminal)
python3 scripts/login_via_chrome_cdp.py --page-id "$(curl -s http://127.0.0.1:9222/json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')"

# 4. Approve MFA on your phone

# 5. Done
codex login status  # → "Logged in using ChatGPT" ✅
```

## Full Walkthrough

### Step 1 — Install Python dependency

```bash
pip3 install websockets
```

### Step 2 — Run the setup script

```bash
bash scripts/setup.sh
```

This script:
- Verifies Chrome + Codex CLI are installed
- Kills any Chrome already on port 9222
- Launches a **fresh Chrome** at `http://127.0.0.1:9222` with remote debugging enabled
- Opens one `about:blank` tab (which the automation will control)
- Prints your `PAGE_ID` (you'll need it for step 4)
- Shows the `codex login --device-auth` command to run in another terminal

**Sample output:**
```
============================================
  Codex CLI OAuth — Automated Setup
============================================

[1/5] Checking prerequisites...
  ✅ Chrome found
  ✅ Codex CLI found
  ✅ Python websockets available

[2/5] Cleaning up existing Chrome instances...
  ✅ Port 9222 is free

[3/5] Launching fresh Chrome with remote debugging...
  Chrome launching (waiting 10s for full startup)...
  ✅ Chrome running on port 9222
  📄 Page ID: 0FAF5FBAF6F1FE7B7D50F27B0365F58D
  🔗 Page URL: about:blank

[4/5] Starting Codex device auth flow...
  To complete auth manually (optional):
  → Visit: https://auth.openai.com/codex/device
  → Enter the code shown by: codex login --device-auth

  Alternatively, run the automation script:
  python3 scripts/login_via_chrome_cdp.py --page-id 0FAF5FBAF6F1FE7B7D50F27B0365F58D
```

### Step 3 — Start Codex device auth

In a **second terminal**:

```bash
codex login --device-auth
```

You'll see output like:
```
To authenticate, visit:
  https://auth.openai.com/codex/device
and enter the code: QRST-ABCD
Waiting for authentication...
```

Keep this terminal running — Codex is listening for the OAuth callback.

### Step 4 — Run the automation script

In a **third terminal** (or reuse the setup terminal after step 2):

```bash
# Get the PAGE_ID from the Chrome we just started
PAGE_ID=$(curl -s http://127.0.0.1:9222/json | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')
echo "Page ID: $PAGE_ID"

# Run the automation
python3 scripts/login_via_chrome_cdp.py --page-id "$PAGE_ID"
```

The script will print its progress as it goes:

```
Connecting to Chrome CDP: ws://127.0.0.1:9222/devtools/page/0FAF5FBAF6F1FE7B7D50F27B0365F58D
Connected to Chrome.
STEP 1 — Navigate to device auth page...
  URL: https://auth.openai.com/codex/device
STEP 2 — Email page: type email and continue...
  Email input at (600, 224)
  Clicking '继续' at (600, 300)
  Waiting for password page...
  Password page detected: https://auth.openai.com/log-in/password
STEP 3 — Password page: type password and continue...
  Password input at (586, 288)
  Waiting for MFA approval page...
  MFA page detected: https://auth.openai.com/push-auth-verification/...
```

### Step 5 — Approve MFA

When you see this message from the script:

```
============================================================
  MFA REQUIRED — Please approve the push notification
  on your ChatGPT iOS/Android app or device.
  (Waiting up to 90 seconds...)
============================================================
```

Open the **ChatGPT app** on your iPhone/iPad and tap **Approve** (or tap the notification).

Once approved, the script automatically continues to the workspace selection page and clicks through.

### Step 6 — Verify

```bash
codex login status
# → Logged in using ChatGPT  ✅
```

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Terminal 1: scripts/setup.sh                               │
│  Fresh Chrome, CDP at ws://127.0.0.1:9222                     │
│  (One blank tab waiting)                                      │
└──────────────────────────────────────────────────────────────┘
                              ↕ CDP (WebSocket)
┌──────────────────────────────────────────────────────────────┐
│  Terminal 2: python3 scripts/login_via_chrome_cdp.py         │
│  Controls Chrome via CDP:                                    │
│    • Navigates to OAuth URL                                   │
│    • Types credentials (CDP Input.dispatchKeyEvent)          │
│    • Clicks buttons (JS el.click())                          │
│    • Detects page transitions (window.location.href poll)     │
└──────────────────────────────────────────────────────────────┘
                              ↕
┌──────────────────────────────────────────────────────────────┐
│  Terminal 3: codex login --device-auth                       │
│  Codex CLI listens at localhost:1455/success                 │
└──────────────────────────────────────────────────────────────┘
                              ↕ OAuth Device Flow
┌──────────────────────────────────────────────────────────────┐
│  auth.openai.com                                             │
│    Step 1: Email → Continue                                   │
│    Step 2: Password → Continue                               │
│    Step 3: MFA push → (human approves on phone)              │
│    Step 4: Workspace selection                               │
│    Step 5: Redirects to localhost:1455/success?id_token=...   │
└──────────────────────────────────────────────────────────────┘
```

## Why CDP instead of AX / osascript?

The Chrome profile used by OpenAI's web OAuth is **sandboxed from macOS
Accessibility APIs**. When you inspect such a tab with `get_window_state` (AX),
the tree only shows empty MenuBar items — no web content, no inputs, no buttons.

```
AX accessibility tree (sandboxed web content):
  - AXApplication "Chrome"
      - [0] AXMenuBar ...
      - [1] AXMenuBar ...
      ... (only MenuBar items, no web content)
```

**CDP (Chrome DevTools Protocol)** connects directly to the browser's built-in
debugger interface and bypasses the AX sandbox entirely. It gives you full DOM access
even to cross-origin web content.

## Key Technical Details

### 1. Isolated Chrome Profile

```bash
rm -rf /tmp/chrome_codex_fresh
mkdir -p /tmp/chrome_codex_fresh

open -na "Google Chrome" --args \
  --user-data-dir="/tmp/chrome_codex_fresh" \
  --remote-debugging-port=9222 \
  --remote-allow-origins="*" \
  --no-first-run \
  --new-window \
  "about:blank"
```

The `--user-data-dir=/tmp/...` flag gives us a clean, isolated Chrome profile
with no existing session cookies. This avoids conflicts with any existing
OpenAI/ChatGPT sessions and prevents the OAuth flow from accidentally logging
you into the wrong account.

### 2. CDP WebSocket Connection

```python
from websockets.asyncio.client import connect

ws_url = f"ws://127.0.0.1:9222/devtools/page/{PAGE_ID}"
async with connect(ws_url, max_size=15 * 1024 * 1024) as ws:
    # Send CDP commands and read responses
    await ws.send(json.dumps({"id": 1, "method": "Page.navigate",
                               "params": {"url": "https://..."}}))
    resp = await ws.recv()
```

### 3. DOM Manipulation via `Runtime.evaluate`

Clicking buttons via JS avoids the AX sandbox entirely:

```python
async def click_button(ws, text: str):
    expr = f"""
    (function() {{
        var btn = [...document.querySelectorAll('button')]
            .find(e => e.innerText.trim() === '{text}' && !e.disabled);
        if (!btn) return 'NOT FOUND';
        btn.scrollIntoView({{behavior: 'instant', block: 'center'}});
        btn.click();
        return 'clicked';
    }})()
    """
    await eval_js(ws, expr)
```

### 4. Character-by-Character Typing

Typing credentials character-by-character with small delays mimics human typing
and avoids triggering anti-bot rate limits that can appear with instant bulk inserts:

```python
for c in text:
    await ws.send(json.dumps({
        "id": 2, "method": "Input.dispatchKeyEvent",
        "params": {"type": "keyDown", "text": c, "key": c}
    }))
    await asyncio.sleep(0.04)
    await ws.send(json.dumps({
        "id": 3, "method": "Input.dispatchKeyEvent",
        "params": {"type": "keyUp", "key": c}
    }))
    await asyncio.sleep(0.04)
    await ws.recv()  # drain response
```

### 5. Page Transition Detection

After clicking a form's submit button, the CDP connection may close because
the page unloads. We handle this by polling the URL via `curl` after a brief wait:

```python
for _ in range(20):
    await asyncio.sleep(2)
    url = await eval_js(ws, 'window.location.href')
    if "password" in url:
        print("Password page detected!")
        break
else:
    print(f"Current URL: {url}")
```

## Troubleshooting

### "Chrome didn't start with remote debugging on port 9222"

```bash
# Check what's on port 9222
lsof -i :9222

# Kill it and restart
pkill -f "remote-debugging-port=9222" 2>/dev/null
sleep 3
# Then re-run setup.sh
```

### "osascript timed out" when running CDP JS

This happens when Chrome shows a "Allow JavaScript from Apple Events"
permission dialog. A fresh `--user-data-dir` avoids this. Restart Chrome
per Step 2 of the setup.

### Script hangs at MFA page

The push notification can take 10-30 seconds to arrive. The script waits
up to 90 seconds. If it doesn't arrive:
- On the MFA page, click **"重新发送提示"** (Resend)
- Or click **"试试电子邮件"** (Try email) to use an email code instead
- The script will detect the page transition automatically after you proceed

### CDP WebSocket disconnects

Some CDP calls (like `btn.click()` that triggers navigation) can cause the
WebSocket to close because the page unloads. The script handles this by:
1. Checking URL immediately after click via `curl`
2. Reconnecting if needed

### Wrong workspace selected

If your account has multiple workspaces and the script picks the wrong one,
after the script completes you can switch workspaces manually:
```bash
# Log out and re-run the flow
codex logout
codex login --device-auth
# Then re-run the automation script
```

Or modify the script to target a specific workspace index (see comments in
`scripts/login_via_chrome_cdp.py` around line "STEP 4 — Workspace selection").

## Project Structure

```
codex-auth-flow/
├── README.md              # This file
├── requirements.txt       # Python deps (websockets)
├── .gitignore
└── scripts/
    ├── setup.sh           # Automated Chrome + Codex setup (run once)
    └── login_via_chrome_cdp.py   # Main automation script
```

## Requirements

- **macOS** (Linux compatible with minor path adjustments)
- **Google Chrome** installed at `/Applications/Google Chrome.app`
- **Codex CLI** installed (`brew install openai-codex` on macOS)
- **Python 3.9+** with `websockets` package

## License

MIT — do whatever you want with it.
