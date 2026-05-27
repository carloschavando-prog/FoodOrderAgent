"""
Sysco pass 1 — login and full API capture.

Portal: https://shop.sysco.com
Login:  carlos@onparbar.com / !Compass1066

Goals:
  1. Log in to shop.sysco.com
  2. Intercept ALL network calls (domain, method, body, auth headers, response)
  3. Navigate to the order guide / catalog to trigger product + price API calls
  4. Identify: auth endpoint, token storage, product list endpoint, price endpoint
  5. Save session + capture for scraper implementation
"""
import asyncio, json, os
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/sysco_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
os.makedirs(API_DIR, exist_ok=True)

PORTAL_URL = "https://shop.sysco.com/app/account-manager"
EMAIL      = "carlos@onparbar.com"
PASSWORD   = "!Compass1066"

all_requests  = []
api_responses = {}

SKIP_EXTS    = (".css", ".js", ".png", ".jpg", ".svg", ".woff", ".ico",
                ".gif", ".ttf", ".woff2", ".webp", ".map")
SKIP_DOMAINS = ("google.com", "doubleclick.net", "analytics", "googleads",
                "bing.com", "facebook.com", "hotjar.com", "segment.io",
                "zendesk.com", "newrelic", "nr-data", "launchdarkly",
                "quantum", "akamai", "cloudfront")

def _is_interesting(url):
    if any(url.split("?")[0].endswith(e) for e in SKIP_EXTS):
        return False
    if any(d in url for d in SKIP_DOMAINS):
        return False
    # Keep anything that looks like API / auth / Sysco domains
    return any(d in url for d in [
        "sysco.com", "syy1.com", "syscofoods", "okta",
        "auth", "login", "api", "token", "oauth",
    ])

async def handle_request(request):
    if not _is_interesting(request.url):
        return
    try:
        body = request.post_data
    except Exception:
        body = None
    try:
        hdrs = dict(await request.all_headers())
    except Exception:
        hdrs = {}
    all_requests.append({
        "method":  request.method,
        "url":     request.url,
        "headers": hdrs,
        "body":    body,
    })
    short = request.url[:120]
    print(f"  REQ {request.method:6} {short}")
    if body and len(body) < 600:
        print(f"       body: {body[:300]}")
    # Print key headers
    for h in ("authorization", "x-api-key", "x-customer-account", "x-site-id"):
        if h in hdrs:
            print(f"       {h}: {hdrs[h][:100]}")

async def handle_response(response):
    if not _is_interesting(response.url):
        return
    try:
        body = await response.json()
        api_responses[response.url] = body
        size = len(json.dumps(body)) / 1024
        print(f"  RSP {response.url[:100]} ({size:.1f}KB) status={response.status}")
        if isinstance(body, dict):
            print(f"       keys: {list(body.keys())[:8]}")
        elif isinstance(body, list) and body:
            print(f"       [{len(body)} items]  first keys: {list(body[0].keys())[:6] if isinstance(body[0], dict) else '?'}")
    except Exception:
        pass

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=100)
        ctx  = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        print(f"→ Navigating to {PORTAL_URL} ...")
        await page.goto(PORTAL_URL, timeout=45000)
        await asyncio.sleep(5)
        print(f"URL: {page.url}")
        await page.screenshot(path=f"{API_DIR}/sysco_start.png")

        # ── Handle login if redirected ────────────────────
        current = page.url
        if "login" in current.lower() or "signin" in current.lower() or "auth" in current.lower() or \
           await page.locator('input[type="email"], input[type="text"], input[name="username"]').count() > 0:

            print("\n→ Login page detected")

            # Try to fill username/email
            for sel in [
                'input[type="email"]',
                'input[name="email"]',
                'input[name="username"]',
                'input[type="text"]',
                'input[placeholder*="email" i]',
                'input[placeholder*="user" i]',
                '#username', '#email',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.fill(EMAIL)
                        print(f"  Filled email via {sel}")
                        break
                except Exception:
                    pass

            await asyncio.sleep(1)

            # Click Next/Continue if needed
            for sel in [
                'button:has-text("Next")',
                'button:has-text("Continue")',
                'input[value="Next"]',
                '[data-se="o-form-button-bar"] input',
                'button[type="submit"]',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        await asyncio.sleep(3)
                        print(f"  Clicked Next: {sel}")
                        break
                except Exception:
                    pass

            await page.screenshot(path=f"{API_DIR}/sysco_after_email.png")
            print(f"URL after email: {page.url}")

            # Fill password
            for sel in [
                'input[type="password"]',
                'input[name="password"]',
                'input[name="credentials.passcode"]',
                '#password', '#okta-signin-password',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=4000):
                        await el.fill(PASSWORD)
                        print(f"  Filled password via {sel}")
                        break
                except Exception:
                    pass

            await asyncio.sleep(1)

            # Submit
            for sel in [
                'button:has-text("Sign In")',
                'button:has-text("Sign in")',
                'button:has-text("Log In")',
                'button:has-text("Login")',
                'button:has-text("Verify")',
                'input[value="Sign In"]',
                'input[value="Verify"]',
                'button[type="submit"]',
                '.button-primary',
                '[data-se="o-form-button-bar"] input',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        print(f"  Clicked Submit: {sel}")
                        break
                except Exception:
                    pass

            print("→ Waiting for redirect after login ...")
            try:
                await page.wait_for_url("**/shop.sysco.com/**", timeout=25000)
            except Exception:
                pass
            await asyncio.sleep(6)
            print(f"URL after login: {page.url}")

        await page.screenshot(path=f"{API_DIR}/sysco_portal.png")
        text = await page.evaluate("() => document.body.innerText.slice(0,3000)")
        print(f"\nPage text:\n{text[:2000]}")

        # Save session
        await ctx.storage_state(path=SESSION_FILE)
        print(f"\n✅ Session saved → {SESSION_FILE}")

        # Dump storage for tokens
        storage = await page.evaluate("""
            () => {
                const ls = {}, ss = {};
                for (let i=0; i<localStorage.length; i++) {
                    const k = localStorage.key(i);
                    ls[k] = localStorage.getItem(k);
                }
                for (let i=0; i<sessionStorage.length; i++) {
                    const k = sessionStorage.key(i);
                    ss[k] = sessionStorage.getItem(k);
                }
                return {ls, ss};
            }
        """)
        print(f"\nlocalStorage ({len(storage['ls'])} keys):")
        for k, v in list(storage["ls"].items())[:30]:
            print(f"  {k[:80]}: {str(v)[:120]}")
        print(f"\nsessionStorage ({len(storage['ss'])} keys):")
        for k, v in list(storage["ss"].items())[:30]:
            print(f"  {k[:80]}: {str(v)[:120]}")

        # ── Navigate to order guide ───────────────────────
        await asyncio.sleep(4)
        print("\n\n→ Looking for Order Guide / Catalog ...")

        for label in ["Order Guide", "My Order Guide", "Catalog", "Products",
                      "Browse", "Shop", "Items", "My List", "Lists"]:
            try:
                el = page.locator(f"text=/{label}/i").first
                if await el.is_visible(timeout=2000):
                    print(f"  Found '{label}', clicking ...")
                    await el.click()
                    await asyncio.sleep(6)
                    print(f"  URL: {page.url}")
                    break
            except Exception:
                pass

        # Also try navigating directly to common Sysco order guide URLs
        for url in [
            "https://shop.sysco.com/app/catalog",
            "https://shop.sysco.com/app/order-guide",
        ]:
            if "shop.sysco.com/app/" not in page.url or page.url == PORTAL_URL:
                try:
                    await page.goto(url, timeout=20000)
                    await asyncio.sleep(5)
                    print(f"  Direct nav URL: {page.url}")
                    break
                except Exception:
                    pass

        await asyncio.sleep(5)
        await page.screenshot(path=f"{API_DIR}/sysco_catalog.png")
        text2 = await page.evaluate("() => document.body.innerText.slice(0,3000)")
        print(f"\nPage text after nav:\n{text2[:2000]}")

        # ── Summary ───────────────────────────────────────
        print(f"\n\n=== API REQUESTS ({len(all_requests)}) ===")
        seen = set()
        for req in all_requests:
            ep = req["url"].split("?")[0]
            if ep not in seen:
                seen.add(ep)
                print(f"\n  {req['method']:6} {ep}")
                if req.get("body") and len(str(req["body"])) < 400:
                    print(f"    body: {str(req['body'])[:300]}")
                auth = req.get("headers", {}).get("authorization", "")
                if auth:
                    print(f"    auth: {auth[:100]}")
                for h in ("x-api-key", "x-customer-account", "x-site-id",
                          "x-syy-locale", "client_id", "x-auth-token"):
                    v = req.get("headers", {}).get(h, "")
                    if v:
                        print(f"    {h}: {v[:100]}")

        print(f"\n=== RESPONSES ({len(api_responses)}) ===")
        for url, body in api_responses.items():
            size = len(json.dumps(body)) / 1024
            print(f"\n  {url[:100]} ({size:.1f}KB)")
            if isinstance(body, dict):
                print(f"    keys: {list(body.keys())[:10]}")
                print(f"    {json.dumps(body)[:400]}")
            elif isinstance(body, list):
                print(f"    [{len(body)} items]")
                if body and isinstance(body[0], dict):
                    print(f"    first keys: {list(body[0].keys())[:8]}")
                    print(f"    {json.dumps(body[0])[:300]}")

        # Save capture
        out = {
            "requests":  all_requests[:200],
            "responses": api_responses,
            "storage":   storage,
        }
        with open(f"{API_DIR}/sysco_capture1.json", "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\n✅ Capture saved → {API_DIR}/sysco_capture1.json")

        await asyncio.sleep(10)
        await browser.close()

asyncio.run(main())
