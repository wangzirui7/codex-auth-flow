#!/usr/bin/env python3
"""
Codex CLI OAuth — Automated Login via Chrome CDP

This script automates the Codex CLI device-auth OAuth flow by controlling
Chrome via Chrome DevTools Protocol (CDP).

Usage:
    python3 scripts/login_via_chrome_cdp.py --page-id <PAGE_ID>

PAGE_ID is the CDP page ID for the Chrome tab to control.
Get it with: curl -s http://127.0.0.1:9222/json | python3 -c "import json,sys; [print(p['id'], p['url']) for p in json.load(sys.stdin) if p['type']=='page']"

Prerequisites:
    1. Launch Chrome: open -na "Google Chrome" --args --user-data-dir=/tmp/chrome_codex_fresh --remote-debugging-port=9222 ...
    2. Run: codex login --device-auth
    3. This script connects to the Chrome tab and completes the OAuth flow.
"""

import argparse
import asyncio
import json
import sys
import time
from websockets.asyncio.client import connect

# ─── Configuration ────────────────────────────────────────────────────────────

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
OAUTH_URL = "https://auth.openai.com/codex/device"
TIMEOUT = 20  # seconds per CDP operation

# ─── CDP WebSocket helpers ────────────────────────────────────────────────────

async def ws_send(ws, msg_id: int, method: str, params: dict) -> None:
    """Send a CDP message."""
    await ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))


async def ws_call(ws, msg_id: int, method: str, params: dict) -> dict:
    """Send a CDP message and wait for the response."""
    await ws_send(ws, msg_id, method, params)
    resp = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT)
    return json.loads(resp)


async def eval_js(ws, expr: str) -> str:
    """Evaluate arbitrary JS and return the string result."""
    resp = await ws_call(ws, 1, "Runtime.evaluate", {"expression": expr})
    result = resp.get("result", {}).get("result", {})
    return result.get("value", "")


async def nav_to(ws, url: str) -> None:
    """Navigate to a URL."""
    await ws_call(ws, 1, "Page.navigate", {"url": url})
    # Wait for network to settle
    await asyncio.sleep(3)


async def click_element_via_js(ws, selector: str) -> None:
    """Click an element using JS (safe for cross-origin iframes)."""
    expr = f"""
    (function() {{
        var el = document.querySelector('{selector}');
        if (!el) return 'NOT FOUND: {selector}';
        el.scrollIntoView({{behavior: 'instant', block: 'center'}});
        el.click();
        return 'clicked';
    }})()
    """
    await eval_js(ws, expr)


async def type_text(ws, selector: str, text: str) -> None:
    """Type text into an input by focusing it and dispatching key events."""
    # Focus and clear
    await eval_js(ws, f"""
        (function() {{
            var el = document.querySelector('{selector}');
            el.focus();
            el.value = '';
        }})()
    """)
    await asyncio.sleep(0.3)

    # Character-by-character typing (anti-bot rate limit evasion)
    for c in text:
        await ws_call(ws, 2, "Input.dispatchKeyEvent",
                      {"type": "keyDown", "text": c, "key": c})
        await asyncio.sleep(0.04)
        await ws_call(ws, 3, "Input.dispatchKeyEvent",
                      {"type": "keyUp", "key": c})
        await asyncio.sleep(0.04)
        try:
            await ws.recv()  # drain response
        except Exception:
            pass


def get_page_url() -> str:
    """Poll the CDP page list to get current URL (outside WS context)."""
    import urllib.request
    try:
        resp = urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5)
        pages = json.loads(resp.read())
        return pages[0].get("url", "") if pages else ""
    except Exception:
        return ""


# ─── Page element helpers ────────────────────────────────────────────────────

async def get_element_info(ws, selector: str) -> dict:
    """Get bounding rect + text for a CSS selector."""
    expr = f"""
    (function() {{
        var el = document.querySelector('{selector}');
        if (!el) return null;
        var r = el.getBoundingClientRect();
        return {{
            x: Math.round(r.x),
            y: Math.round(r.y),
            w: Math.round(r.width),
            h: Math.round(r.height),
            px: Math.round(r.x + r.width / 2),
            py: Math.round(r.y + r.height / 2),
            text: el.innerText?.trim() || ''
        }};
    }})()
    """
    val = await eval_js(ws, expr)
    if val:
        return json.loads(val)
    return {}


async def get_all_inputs(ws) -> list:
    """Return all <input> elements as dicts."""
    val = await eval_js(ws, """
        JSON.stringify(
            [...document.querySelectorAll('input')].map(e => ({
                type: e.type,
                name: e.name,
                id: e.id,
                placeholder: e.placeholder
            }))
        )
    """)
    return json.loads(val) if val else []


async def get_all_buttons(ws) -> list:
    """Return all <button> elements as dicts."""
    val = await eval_js(ws, """
        JSON.stringify(
            [...document.querySelectorAll('button')].map(e => ({
                text: e.innerText.trim(),
                disabled: e.disabled
            }))
        )
    """)
    return json.loads(val) if val else []


async def find_button(ws, text: str) -> dict:
    """Find a button by innerText and return its bounding rect."""
    expr = f"""
    (function() {{
        var btn = [...document.querySelectorAll('button')]
            .find(e => e.innerText.trim() === '{text}' && !e.disabled);
        if (!btn) return 'NOT FOUND';
        var r = btn.getBoundingClientRect();
        return JSON.stringify({{
            px: Math.round(r.x + r.width / 2),
            py: Math.round(r.y + r.height / 2)
        }});
    }})()
    """
    val = await eval_js(ws, expr)
    if val and val != "NOT FOUND":
        return json.loads(val)
    return {}


# ─── Main automation ─────────────────────────────────────────────────────────

async def login_flow(ws, page_id: str):
    """Execute the full OAuth login flow."""

    print("STEP 1 — Navigate to device auth page...")
    await nav_to(ws, OAUTH_URL)
    await asyncio.sleep(4)  # Let page fully load
    print(f"  URL: {await eval_js(ws, 'window.location.href')}")

    # ── Email page ──────────────────────────────────────────────────────────

    print("STEP 2 — Email page: type email and continue...")

    # Find email input
    email_input = await get_element_info(ws, 'input[type="email"]')
    if not email_input:
        # Try placeholder match
        email_input = await get_element_info(ws, '[placeholder*="电子"]')
    if not email_input:
        # Try by partial ID
        email_input = await get_element_info(ws, 'input[name="email"]')
    if not email_input:
        # Last resort: enumerate inputs
        inputs = await get_all_inputs(ws)
        print(f"  Available inputs: {inputs}")
        raise RuntimeError("Could not find email input")

    print(f"  Email input at ({email_input['px']}, {email_input['py']})")
    await type_text(ws, 'input[type="email"]', "YOUR_EMAIL@example.com")
    await asyncio.sleep(0.5)

    btn = await find_button(ws, "继续")
    if btn:
        print(f"  Clicking '继续' at ({btn['px']}, {btn['py']})")
        await eval_js(ws, """
            (function() {
                var btn = [...document.querySelectorAll('button')]
                    .find(e => e.innerText.trim() === '继续' && !e.disabled);
                btn.scrollIntoView({behavior: 'instant', block: 'center'});
                btn.click();
            })()
        """)
    else:
        print("  Continue button not found, trying form submit...")
        await eval_js(ws, """
            document.querySelector('form')?.requestSubmit()
        """)

    # Wait for password page
    print("  Waiting for password page...")
    for _ in range(20):
        await asyncio.sleep(2)
        url = await eval_js(ws, 'window.location.href')
        if "password" in url:
            print(f"  Password page detected: {url}")
            break
    else:
        print(f"  Current URL: {url}")

    await asyncio.sleep(2)

    # ── Password page ───────────────────────────────────────────────────────

    print("STEP 3 — Password page: type password and continue...")

    pw_input = await get_element_info(ws, 'input[type="password"]')
    if not pw_input:
        inputs = await get_all_inputs(ws)
        print(f"  Available inputs: {inputs}")
        raise RuntimeError("Could not find password input")

    print(f"  Password input at ({pw_input['px']}, {pw_input['py']})")
    await eval_js(ws, 'document.querySelector(\'input[type="password"]\').focus()')
    await asyncio.sleep(0.3)
    await type_text(ws, 'input[type="password"]', "YOUR_PASSWORD")
    await asyncio.sleep(0.5)

    btn = await find_button(ws, "继续")
    if btn:
        await eval_js(ws, """
            (function() {
                var btn = [...document.querySelectorAll('button')]
                    .find(e => e.innerText.trim() === '继续' && !e.disabled);
                btn.scrollIntoView({behavior: 'instant', block: 'center'});
                btn.click();
            })()
        """)

    # Wait for MFA page
    print("  Waiting for MFA approval page...")
    for _ in range(30):
        await asyncio.sleep(2)
        url = await eval_js(ws, 'window.location.href')
        if "push-auth" in url or "verification" in url:
            print(f"  MFA page detected: {url}")
            break
        if "consent" in url or "workspace" in url:
            print(f"  Workspace page detected: {url}")
            break
    else:
        print(f"  Current URL: {url}")

    await asyncio.sleep(2)

    # ── MFA page (human must approve) ─────────────────────────────────────

    page_text = await eval_js(ws, 'document.body.innerText.substring(0, 300)')
    if "批准" in page_text or "发送" in page_text:
        print()
        print("=" * 60)
        print("  MFA REQUIRED — Please approve the push notification")
        print("  on your ChatGPT iOS/Android app or device.")
        print("  (Waiting up to 90 seconds...)")
        print("=" * 60)
        print()

        # Wait for MFA to resolve (either success or human gives up)
        for _ in range(45):
            await asyncio.sleep(2)
            url = await eval_js(ws, 'window.location.href')
            if "consent" in url or "workspace" in url or "codex" in url:
                print(f"  MFA resolved: {url}")
                break
        else:
            print("  MFA page still showing — you may need to approve or use email fallback")

    await asyncio.sleep(2)

    # ── Workspace selection page ───────────────────────────────────────────

    url = await eval_js(ws, 'window.location.href')
    if "consent" in url or "workspace" in url or "选择" in (await eval_js(ws, 'document.body.innerText')):
        print("STEP 4 — Workspace selection page...")

        buttons = await get_all_buttons(ws)
        inputs = await get_all_inputs(ws)
        print(f"  Buttons: {buttons}")
        print(f"  Inputs: {inputs}")

        # Try to find and click "W" workspace (index 1 — adjust as needed)
        # The page structure: radio[0] = first workspace, radio[1] = second workspace
        workspace_clicked = False
        for idx in range(5):
            expr = f"""
            (function() {{
                var radios = [...document.querySelectorAll('input[type="radio"]')];
                if (!radios[{idx}]) return 'NO RADIO';
                var r = radios[{idx}].getBoundingClientRect();
                return JSON.stringify({{
                    px: Math.round(r.x + r.width / 2),
                    py: Math.round(r.y + r.height / 2)
                }});
            }})()
            """
            val = await eval_js(ws, expr)
            if val and val != "NO RADIO":
                coords = json.loads(val)
                await ws_call(ws, 10, "Input.dispatchMouseEvent",
                              {"type": "mousePressed", "x": coords["px"], "y": coords["py"],
                               "button": "left", "clickCount": 1})
                await asyncio.sleep(0.1)
                await ws_call(ws, 11, "Input.dispatchMouseEvent",
                              {"type": "mouseReleased", "x": coords["px"], "y": coords["py"],
                               "button": "left", "clickCount": 1})
                await asyncio.sleep(0.5)
                workspace_clicked = True
                print(f"  Clicked workspace radio[{idx}] at ({coords['px']}, {coords['py']})")
                break

        if not workspace_clicked:
            print("  Could not find workspace radios — attempting by label...")

        # Click "继续" to confirm workspace
        btn = await find_button(ws, "继续")
        if btn:
            await eval_js(ws, """
                (function() {
                    var btn = [...document.querySelectorAll('button')]
                        .find(e => e.innerText.trim() === '继续' && !e.disabled);
                    btn.scrollIntoView({behavior: 'instant', block: 'center'});
                    btn.click();
                })()
            """)
            print("  Clicked '继续'")

        # Wait for redirect
        await asyncio.sleep(5)

    # ── Done ───────────────────────────────────────────────────────────────

    final_url = await eval_js(ws, 'window.location.href')
    print()
    print("=" * 60)
    print(f"  Final URL: {final_url}")
    if "localhost:1455" in final_url or "success" in final_url:
        print("  ✅ OAuth flow completed successfully!")
        print("  Run 'codex login status' to verify.")
    elif "error" in final_url.lower():
        print("  ❌ OAuth flow hit an error.")
    else:
        print("  ℹ️  Check the URL above — you may need manual action.")
    print("=" * 60)


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Automate Codex OAuth login via Chrome CDP")
    parser.add_argument("--page-id", required=True,
                        help="CDP page ID (get from curl http://127.0.0.1:9222/json)")
    args = parser.parse_args()

    ws_url = f"ws://{CDP_HOST}:{CDP_PORT}/devtools/page/{args.page_id}"
    print(f"Connecting to Chrome CDP: {ws_url}")

    async def run():
        try:
            async with connect(ws_url, max_size=15 * 1024 * 1024) as ws:
                print("Connected to Chrome.")
                await login_flow(ws, args.page_id)
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    asyncio.run(run())


if __name__ == "__main__":
    main()
