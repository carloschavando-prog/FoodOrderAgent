"""
Capture PFG CustomerFirst API credentials and price list structure.

Strategy (mirrors US Foods approach):
  1. Open real Chrome via Playwright (bypasses Azure B2C bot detection)
  2. Auto-login with provided credentials at pfgcustomerfirst.b2clogin.com
  3. Intercept all XHR/fetch requests to find the internal REST API
  4. Navigate to the seasonal/special price list page
  5. Save bearer token, refresh token, and request details

Output:
  ~/.FoodOrderAgent/pfg_api_config.json  — used by scrape_pfg.py
  ~/.FoodOrderAgent/api_captures/pfg_*   — raw captured responses
"""
import asyncio, json, os, re
from playwright.async_api import async_playwright

EMAIL    = "cchavando@onparbar.com"
PASSWORD = "!Onpar4464"

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/pfg_session.json")
CONFIG_FILE  = os.path.expanduser("~/.FoodOrderAgent/pfg_api_config.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
os.makedirs(API_DIR, exist_ok=True)

PORTAL_URL = "https://www.customerfirstsolutions.com"
B2C_HOST   = "pfgcustomerfirst.b2clogin.com"

# Capture all XHR/fetch to these domains
TARGET_DOMAINS = {
    "customerfirstsolutions.com",
    "pfg.com",
    "pfgc.com",
    "api.customerfirstsolutions.com",
    "pfgcustomerfirst",
}

req_log   = {}   # url_key -> {method, url, headers, post_data}
resp_log  = {}   # url_key -> body
tokens    = {}   # access_token, refresh_token, id_token

def _key(url):
    # Strip query string for grouping
    return re.sub(r'\?.*', '', url.split("://")[-1])

async def handle_request(request):
    url = request.url
    if not any(d in url for d in TARGET_DOMAINS):
        return
    try:
        post_data = request.post_data
    except Exception:
        post_data = None
    hdrs = dict(await request.all_headers())
    # Capture the bearer token from any authenticated request
    auth = hdrs.get("authorization", "")
    if auth.startswith("Bearer ") and "access_token" not in tokens:
        tokens["access_token"] = auth.replace("Bearer ", "")
        tokens["bearer"] = auth
        print(f"  🔑 Bearer token captured ({len(tokens['access_token'])} chars)")
    k = _key(url)
    req_log[k] = {
        "method":    request.method,
        "url":       url,
        "headers":   {h: v for h, v in hdrs.items() if not h.startswith(":")},
        "post_data": post_data,
    }
    if request.method != "GET" and post_data:
        print(f"  REQ {request.method} {url.split('/')[-1][:60]}  body={post_data[:120]}")
    else:
        print(f"  REQ {request.method} {url.split('customerfirstsolutions.com/')[-1][:70]}")

async def handle_response(response):
    url = response.url
    if not any(d in url for d in TARGET_DOMAINS):
        return
    # Don't capture binary/image responses
    ct = response.headers.get("content-type", "")
    if not any(t in ct for t in ["json", "text", "javascript"]):
        return
    try:
        body = await response.json()
        k = _key(url)
        resp_log[k] = body
        size = len(json.dumps(body)) / 1024
        print(f"  RSP {url.split('customerfirstsolutions.com/')[-1][:60]} ({size:.1f} KB)")
    except Exception:
        pass

async def do_login(page):
    """Attempt automated login on the B2C page."""
    print("→ Attempting automated login...")
    # Wait for email field
    try:
        await page.wait_for_selector("input[type=email], input[name=email], #signInName", timeout=10000)
    except Exception:
        print("  ⚠️  No email field found — may already be logged in")
        return

    email_sel = "input[type=email], input[name=email], #signInName"
    pw_sel    = "input[type=password], input[name=password], #password"

    await page.fill(email_sel, EMAIL)
    await asyncio.sleep(0.5)
    await page.fill(pw_sel, PASSWORD)
    await asyncio.sleep(0.5)

    # Click the sign-in button
    btn = page.locator("button[type=submit], input[type=submit], button:has-text('Sign in'), button:has-text('Log in')").first
    await btn.click()
    print("  ⏳ Credentials submitted — waiting for redirect...")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome",
            headless=False,
            slow_mo=150,
        )

        # Try to reuse existing session
        ctx_kwargs = {"viewport": {"width": 1440, "height": 900}}
        if os.path.exists(SESSION_FILE):
            print(f"→ Loading existing session from {SESSION_FILE}")
            ctx_kwargs["storage_state"] = SESSION_FILE

        ctx  = await browser.new_context(**ctx_kwargs)
        page = await ctx.new_page()

        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        # ── Navigate to portal ──────────────────────────────
        print(f"→ Opening {PORTAL_URL} ...")
        await page.goto(PORTAL_URL, timeout=30000)
        await asyncio.sleep(3)

        # If redirected to B2C login, do automated login
        if B2C_HOST in page.url:
            await do_login(page)
            # Wait up to 30s for redirect back to portal
            try:
                await page.wait_for_url(f"**customerfirstsolutions.com**", timeout=30000)
                print(f"  ✅ Redirected to: {page.url}")
            except Exception:
                print(f"  ⚠️  Still at: {page.url} — may need manual intervention")
                await asyncio.sleep(15)

        await asyncio.sleep(4)
        print(f"  Portal URL: {page.url}")

        # Save session now (so it can be reused even if we fail below)
        await ctx.storage_state(path=SESSION_FILE)
        print(f"  ✅ Session saved → {SESSION_FILE}")

        # ── Explore the portal ──────────────────────────────
        print("→ Looking for price list / order guide / seasonal page...")
        await asyncio.sleep(3)

        # Dump top-level nav links to understand portal structure
        nav_links = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href], nav a, header a'))
                .map(a => ({text: a.innerText.trim(), href: a.href}))
                .filter(a => a.text.length > 0 && a.href.startsWith('http'))
                .slice(0, 30)
        """)
        print("\n--- Navigation links ---")
        for lnk in nav_links:
            print(f"  {lnk['text'][:40]!r:42} → {lnk['href'][:80]}")

        # Look for price list / seasonal keywords
        keywords = ["price", "seasonal", "special", "order guide", "catalog", "product"]
        price_links = [l for l in nav_links if any(k in l["text"].lower() for k in keywords)]
        if price_links:
            print(f"\n→ Clicking price/seasonal link: {price_links[0]['text']!r}")
            await page.goto(price_links[0]["href"], timeout=20000)
            await asyncio.sleep(5)

        # Take a screenshot to see what we're dealing with
        shot = os.path.join(API_DIR, "pfg_portal_screenshot.png")
        await page.screenshot(path=shot, full_page=False)
        print(f"\n→ Screenshot saved: {shot}")

        # Dump page text summary
        body_text = await page.evaluate("() => document.body.innerText.slice(0, 2000)")
        print("\n--- Page text (first 2000 chars) ---")
        print(body_text)

        # ── Wait for more network activity ──────────────────
        print("\n→ Waiting 10s for additional API calls to complete...")
        await asyncio.sleep(10)

        # ── Capture token from B2C response if available ────
        # Check localStorage / sessionStorage for tokens
        storage_tokens = await page.evaluate("""
            () => {
                const out = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    const v = localStorage.getItem(k);
                    if (v && (v.includes('access_token') || v.includes('refresh_token') || k.includes('token'))) {
                        try { out[k] = JSON.parse(v); } catch { out[k] = v; }
                    }
                }
                // Also check sessionStorage
                for (let i = 0; i < sessionStorage.length; i++) {
                    const k = sessionStorage.key(i);
                    const v = sessionStorage.getItem(k);
                    if (v && (v.includes('access_token') || v.includes('refresh_token') || k.includes('token'))) {
                        try { out['ss_' + k] = JSON.parse(v); } catch { out['ss_' + k] = v; }
                    }
                }
                return out;
            }
        """)
        if storage_tokens:
            print(f"\n--- localStorage/sessionStorage tokens ({len(storage_tokens)} keys) ---")
            for k, v in list(storage_tokens.items())[:10]:
                v_str = json.dumps(v)[:200] if not isinstance(v, str) else v[:200]
                print(f"  {k!r}: {v_str}")

        # ── Save everything ─────────────────────────────────
        print(f"\n\nCaptured {len(resp_log)} API responses, {len(req_log)} requests")

        summary = {
            "tokens":          tokens,
            "storage_tokens":  storage_tokens,
            "request_details": req_log,
            "responses":       resp_log,
        }
        with open(os.path.join(API_DIR, "pfg_capture_summary.json"), "w") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"  Saved → {API_DIR}/pfg_capture_summary.json")

        # Build config for scrape_pfg.py
        config = {
            "portal_url": PORTAL_URL,
            "tokens": tokens,
            "storage_tokens_sample": dict(list(storage_tokens.items())[:5]),
            "api_endpoints": list(req_log.keys())[:30],
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2, default=str)
        print(f"  Config saved → {CONFIG_FILE}")

        await asyncio.sleep(15)
        await browser.close()

asyncio.run(main())
