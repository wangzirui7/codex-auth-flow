# Codex CLI OAuth Device Auth — Automated Chrome Login

> Automate Codex CLI OAuth login using Chrome DevTools Protocol (CDP) via computer use,
> no manual browser interaction required after the OAuth device flow starts.

## Overview

When you run `codex login --device-auth`, the Codex CLI opens a device auth URL and waits
for OAuth completion. Traditionally, you must manually open that URL in a browser, enter
credentials, approve MFA, and pick a workspace — the browser is controlled by a human.

This project automates that entire browser flow using CDP (Chrome DevTools Protocol) from
a separate Chrome instance. The CDP approach works even when the browser profile is sandboxed
from AX accessibility APIs.

## Architecture

```
codex login --device-auth
        │
        ▼
┌─────────────────────────┐     ┌──────────────────────────────┐
│  Codex CLI              │     │  Fresh Chrome (isolated      │
│  • fetches device code  │     │  --user-data-dir=/tmp/...)   │
│  • listens on localhost  │     │                              │
│    :1455/success        │     │  CDP WS at :9222             │
└─────────────────────────┘     │  • navigates OAuth URL        │
        │                       │  • types credentials via CDP   │
        │  auth.openai.com/    │  • clicks buttons via CDP      │
        │    codex/device      │                              │
        │ (Device Auth Flow)   │  [MFA must be approved by     │
        │                      │   human on their device]       │
        │◄─── redirect ────────┤                              │
        │  localhost:1455       │                              │
        │  /success?id_token=..│                              │
        ▼                       └──────────────────────────────┘
Codex CLI receives token → "Logged in using ChatGPT" ✅
```

## Prerequisites

- **macOS** (Linux should work with minor path adjustments)
- **Google Chrome** installed
- **Codex CLI** installed (`brew install openai-codex` or from OpenAI)
- **Python 3.9+** with `websockets` package (`pip install websockets`)

## Quick Start

### Step 1 — Launch Chrome with CDP

```bash
# Kill any existing Chrome on port 9222 to avoid conflicts
pkill -f "Google Chrome" 2>/dev/null || true
sleep 2

# Launch fresh Chrome with remote debugging
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

Wait ~10 seconds for Chrome to fully start, then verify:

```bash
curl -s http://127.0.0.1:9222/json | python3 -c "
import json,sys
pages = [p for p in json.load(sys.stdin) if p['type']=='page']
for p in pages:
    print(p['id'], p['url'])
"
```

You should see a page with `"url": "about:blank"`.

### Step 2 — Start Codex Device Auth

In a **separate terminal**, run:

```bash
codex login --device-auth
```

Codex will output something like:

```
To authenticate, visit:
  https://auth.openai.com/codex/device
and enter the code: XXXX-XXXX
Waiting for authentication...
```

### Step 3 — Run the Automation Script

In yet another terminal (or the same one after Codex is waiting),
get the page ID for the `about:blank` tab:

```bash
PAGE_ID=$(curl -s http://127.0.0.1:9222/json | python3 -c "
import json,sys
pages = [p for p in json.load(sys.stdin) if p['type']=='page']
print(pages[0]['id'] if pages else '')
")
echo "Page ID: $PAGE_ID"
```

Then run the automation script (from this repo):

```bash
python3 scripts/login_via_chrome_cdp.py --page-id "$PAGE_ID"
```

The script will:
1. Navigate to `auth.openai.com/codex/device`
2. Wait for the email input field
3. Type the email address
4. Click **Continue**
5. Type the password
6. Click **Continue**
6. Wait for the MFA approval push (you must approve on your phone/device)
7. After MFA, it detects the workspace selection page
8. Clicks the desired workspace radio button
9. Clicks **Continue**

### Step 4 — Verify

Once done, check Codex:

```bash
codex login status
# → "Logged in using ChatGPT" ✅
```

## The Script (`scripts/login_via_chrome_cdp.py`)

The script is self-contained. Key techniques:

### Why CDP over AX / Computer Use?

The OX Chrome profile used by Codex/ChatGPT web is **sandboxed from macOS
Accessibility APIs**. When you inspect such a Chrome tab with `get_window_state`,
the AX tree only shows MenuBar items — no web content, no inputs, no buttons.

**CDP (Chrome DevTools Protocol)** connects directly to the browser's internal
debugger interface, giving full DOM access regardless of AX sandboxing.

### DOM Injection via `Runtime.evaluate`

```python
# Focus an element
await ws.send(json.dumps({
    'id': n, 'method': 'Runtime.evaluate',
    'params': {'expression': "document.querySelector('#email-input-id').focus()"}
}))

# Click a button
await ws.send(json.dumps({
    'id': n, 'method': 'Runtime.evaluate',
    'params': {'expression': """
        (function(){
            var btn = [...document.querySelectorAll('button')]
                .find(e => e.innerText.trim() === 'Continue');
            btn.scrollIntoView({behavior:'instant',block:'center'});
            btn.click();
        })()
    """}
}))
```

### Human-Like Keystroke Timing

Character-by-character typing with ~50ms delays avoids triggering anti-bot
rate limits on credential fields:

```python
for c in email:
    await ws.send(json.dumps({
        'id': 2, 'method': 'Input.dispatchKeyEvent',
        'params': {'type': 'keyDown', 'text': c, 'key': c}
    }))
    await asyncio.sleep(0.05)
    await ws.send(json.dumps({
        'id': 3, 'method': 'Input.dispatchKeyEvent',
        'params': {'type': 'keyUp', 'key': c}
    }))
    await asyncio.sleep(0.05)
    await ws.recv()
```

### Detecting Page Transitions

After each form submission, poll the URL to detect navigation:

```python
await ws.send(json.dumps({
    'id': n, 'method': 'Runtime.evaluate',
    'params': {'expression': 'window.location.href'}
}))
resp = await asyncio.wait_for(ws.recv(), timeout=15)
url = json.loads(resp)['result']['result']['value']
# e.g. ".../password" after email submit
```

## MFA Note

The OAuth flow sends a push notification to all devices logged into your
ChatGPT account (iOS/Android app). **You must manually approve** the MFA request
on your phone or tablet. The script cannot bypass this — it's a security measure
by OpenAI.

The script will print a message and wait when it detects the MFA page. Once you
approve, it automatically continues.

## Troubleshooting

### "Chrome didn't start with remote debugging"

Make sure no Chrome instance is already listening on port 9222, or use a different
port and update the script's `CDP_URL`.

### "osascript timed out" when running CDP JS

This usually means Chrome is showing a permission dialog ("Allow JavaScript from
Apple Events"). A fresh `--user-data-dir` avoids this. Restart Chrome as shown
in Step 1.

### Script hangs at MFA page

The MFA push may take 10-30 seconds to arrive on your device. The script waits
up to 60 seconds. If you miss it, click **"重新发送提示"** (Resend) or use
**"试试电子邮件"** (Try email) and enter the code manually.

### CDP WebSocket disconnects after navigation

`Runtime.evaluate` calls that trigger navigation (like `btn.click()`) can cause
the CDP connection to close because the page unloads. The script handles this
by waiting a few seconds and re-connecting to check the new URL via `curl`.

## License

MIT
