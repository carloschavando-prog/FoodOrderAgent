"""
Sysco pass 2 — complete Okta login on secure.sysco.com, reach the portal,
capture order guide and price API calls.

From pass 1:
  - shop.sysco.com/auth/login → POST auth/sso → redirect to secure.sysco.com (Okta)
  - secure.sysco.com uses Okta IDX with stateToken
  - webfinger routes carlos@onparbar.com through native Okta (not federation)
  - gateway-api.shop.sysco.com is the main API; auth via gatewayCredentials JWT

Goals:
  1. Navigate to shop.sysco.com → Okta login flow → complete on secure.sysco.com
  2. Land on shop.sysco.com as authenticated user
  3. Navigate to Order Guide / Catalog
  4. Capture all API calls: auth tokens, customer IDs, order-guide endpoint, prices endpoint
"""
import asyncio, json, os
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/sysco_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
os.makedirs(API_DIR, exist_ok=True)

PORTAL_URL = "https://shop.sysco.com/auth/login"
EMAIL      = "carlos@onparbar.com"
PASSWORD   = "!Compass1066"

all_requests  = []
api_responses = {}

SKIP_EXTS    = (".css", ".js", ".png", ".jpg", ".svg", ".woff", ".ico",
                ".gif", ".ttf", ".woff2", ".webp", ".map")
SKIP_DOMAINS = ("google.com", "doubleclick.net", "googleads", "bing.com",
                "facebook.com", "hotjar.com", "zendesk.com",
                "newrelic", "nr-data", "launchdarkly", "akamai", "cloudfront",
                "browser-intake-datadoghq", "datadoghq", "intercom")

def _is_interesting(url):
    if any(url.split("?")[0].endswith(e) for e in SKIP_EXTS):
        return False
    if any(d in url for d in SKIP_DOMAINS):
        return False
    return any(d in url for d in [
        "sysco.com", "syy1.com", "auth.shop", "gateway-api",
        "okta", "secure.sysco",
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
    all_requests.append({"method": request.method, "url": request.url,
                         "headers": hdrs, "body": body})
    short = request.url[:120]
    print(f"  REQ {request.method:6} {short}")
    if body and len(body) < 500:
        print(f"       body: {body[:300]}")
    for h in ("authorization", "x-api-key", "x-customer-account",
              "x-site-id", "x-auth-token"):
        if h in hdrs:
            print(f"       {h}: {hdrs[h][:120]}")

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

async def fill_visible(page, selectors, value, label):
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=3000):
                await el.fill(value)
                print(f"  ✓ Filled {label} via {sel}")
                return True
        except Exception:
            pass
    return False

async def click_visible(page, selectors, label):
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.click()
                print(f"  ✓ Clicked {label} via {sel}")
                return True
        except Exception:
            pass
    return False

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=150)
        ctx  = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        # ── Step 1: Load login page ────────────────────────
        print(f"→ Loading {PORTAL_URL} ...")
        await page.goto(PORTAL_URL, timeout=45000)
        await asyncio.sleep(4)
        print(f"URL: {page.url}")
        await page.screenshot(path=f"{API_DIR}/sysco2_01_start.png")

        # ── Step 2: Enter email ────────────────────────────
        print("\n→ Entering email ...")
        await fill_visible(page, [
            'input[type="email"]', 'input[name="email"]',
            'input[name="username"]', 'input[type="text"]',
            '#username', '#email',
        ], EMAIL, "email")

        await asyncio.sleep(1)
        await click_visible(page, [
            'button:has-text("Next")',
            'button[type="submit"]:has-text("Next")',
            '[data-testid="next-button"]',
            'button[type="submit"]',
        ], "Next")

        # Wait for either: Okta on secure.sysco.com OR direct password field
        await asyncio.sleep(4)
        print(f"URL after email: {page.url}")
        await page.screenshot(path=f"{API_DIR}/sysco2_02_after_email.png")

        # ── Step 3: Handle Okta on secure.sysco.com ────────
        if "secure.sysco.com" in page.url or "okta" in page.url.lower():
            print(f"\n→ Okta/SSO page detected: {page.url}")

            # Take a moment to let the page fully render
            await asyncio.sleep(3)
            text_check = await page.evaluate("() => document.body.innerText.slice(0,500)")
            print(f"  Page text: {text_check[:300]}")

            # Fill password — try multiple selectors
            await fill_visible(page, [
                'input[type="password"]',
                'input[name="credentials.passcode"]',
                'input[name="password"]',
                '#okta-signin-password',
                'input[autocomplete="current-password"]',
            ], PASSWORD, "password")

            await asyncio.sleep(1)

            # Submit
            await click_visible(page, [
                'input[value="Verify"]',
                'input[value="Sign In"]',
                'button:has-text("Sign In")',
                'button:has-text("Verify")',
                'button[type="submit"]',
                '.button-primary',
                '[data-se="o-form-button-bar"] input',
                '[data-testid="login-button"]',
            ], "Submit")

            print("→ Waiting for redirect back to shop.sysco.com ...")
            try:
                await page.wait_for_url("**/shop.sysco.com/**", timeout=25000)
            except Exception:
                pass
            await asyncio.sleep(6)
            print(f"URL after Okta: {page.url}")

        elif await page.locator('input[type="password"]').count() > 0:
            # Password field directly on login page
            print("\n→ Password field on current page")
            await fill_visible(page, ['input[type="password"]'], PASSWORD, "password")
            await asyncio.sleep(1)
            await click_visible(page, [
                'button[type="submit"]',
                'button:has-text("Sign In")',
                'button:has-text("Login")',
            ], "Submit")
            await asyncio.sleep(8)
            print(f"URL after login: {page.url}")

        await page.screenshot(path=f"{API_DIR}/sysco2_03_portal.png")
        text = await page.evaluate("() => document.body.innerText.slice(0,3000)")
        print(f"\nPage text:\n{text[:2000]}")

        # Check if still at login
        if "shop.sysco.com" in page.url and "login" not in page.url and "auth" not in page.url:
            print("\n✅ Logged in to shop.sysco.com!")
        else:
            print(f"\n⚠️  Still at {page.url} — may need manual intervention")

        # Save session
        await ctx.storage_state(path=SESSION_FILE)
        print(f"✅ Session saved → {SESSION_FILE}")

        # Dump storage
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

        # ── Step 4: Navigate to Order Guide ───────────────
        await asyncio.sleep(4)
        print("\n\n→ Navigating to Order Guide ...")

        # Try clicking nav links first
        for label in ["Order Guide", "My Order Guide", "Catalog", "Browse", "Products"]:
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

        # Try direct URL navigation
        if "order-guide" not in page.url and "catalog" not in page.url:
            for url in [
                "https://shop.sysco.com/app/order-guide",
                "https://shop.sysco.com/app/catalog",
                "https://shop.sysco.com/app/product-catalog",
            ]:
                print(f"  Trying direct nav to {url} ...")
                try:
                    await page.goto(url, timeout=20000)
                    await asyncio.sleep(6)
                    print(f"  URL: {page.url}")
                    if "login" not in page.url and "auth" not in page.url:
                        break
                except Exception:
                    pass

        await asyncio.sleep(5)
        await page.screenshot(path=f"{API_DIR}/sysco2_04_orderguide.png")
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
                if req.get("body") and len(str(req["body"])) < 500:
                    print(f"    body: {str(req['body'])[:400]}")
                auth = req.get("headers", {}).get("authorization", "")
                if auth:
                    print(f"    auth: {auth[:120]}")
                for h in ("x-api-key", "x-customer-account", "x-site-id",
                          "x-syy-locale", "client_id", "x-auth-token",
                          "x-customer-id", "x-account-id"):
                    v = req.get("headers", {}).get(h, "")
                    if v:
                        print(f"    {h}: {v[:120]}")

        print(f"\n=== RESPONSES ({len(api_responses)}) ===")
        for url, body in api_responses.items():
            size = len(json.dumps(body)) / 1024
            print(f"\n  {url[:100]} ({size:.1f}KB)")
            if isinstance(body, dict):
                print(f"    keys: {list(body.keys())[:10]}")
                print(f"    {json.dumps(body)[:500]}")
            elif isinstance(body, list):
                print(f"    [{len(body)} items]")
                if body and isinstance(body[0], dict):
                    print(f"    first keys: {list(body[0].keys())[:8]}")

        out = {"requests": all_requests[:200], "responses": api_responses,
               "storage": storage}
        with open(f"{API_DIR}/sysco_capture2.json", "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\n✅ Capture saved → {API_DIR}/sysco_capture2.json")

        await asyncio.sleep(10)
        await browser.close()

asyncio.run(main())
