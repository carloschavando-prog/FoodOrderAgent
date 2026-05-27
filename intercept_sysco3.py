"""
Sysco pass 3 — fix submit button issue.

Problem from pass 2: fill() bypasses keyboard events, leaving submit button disabled.
Fix: click() into password field first, then type() char-by-char to fire real key events,
     then press Enter (submit button becomes live after first keystroke).
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
        "sysco.com", "syy1.com", "auth.shop", "gateway-api", "secure.sysco",
    ])

async def handle_request(request):
    if not _is_interesting(request.url): return
    try: body = request.post_data
    except Exception: body = None
    try: hdrs = dict(await request.all_headers())
    except Exception: hdrs = {}
    all_requests.append({"method": request.method, "url": request.url,
                         "headers": hdrs, "body": body})
    print(f"  REQ {request.method:6} {request.url[:120]}")
    if body and len(body) < 500:
        print(f"       body: {body[:300]}")
    for h in ("authorization", "x-api-key", "x-customer-account", "x-site-id",
              "x-auth-token", "x-customer-id", "x-account-id"):
        if h in hdrs:
            print(f"       {h}: {hdrs[h][:120]}")

async def handle_response(response):
    if not _is_interesting(response.url): return
    try:
        body = await response.json()
        api_responses[response.url] = body
        size = len(json.dumps(body)) / 1024
        print(f"  RSP {response.url[:100]} ({size:.1f}KB) status={response.status}")
        if isinstance(body, dict):
            print(f"       keys: {list(body.keys())[:8]}")
        elif isinstance(body, list) and body:
            n = len(body)
            fk = list(body[0].keys())[:6] if isinstance(body[0], dict) else "?"
            print(f"       [{n} items]  first keys: {fk}")
    except Exception:
        pass

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=80)
        ctx  = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        # ── Step 1: Load login page ────────────────────────
        print(f"→ Loading {PORTAL_URL} ...")
        await page.goto(PORTAL_URL, timeout=45000)
        await asyncio.sleep(4)
        print(f"URL: {page.url}")

        # ── Step 2: Enter email → click Next ──────────────
        print("\n→ Entering email ...")
        for sel in ['input[type="email"]', 'input[name="email"]',
                    'input[name="username"]', 'input[type="text"]']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    await el.fill(EMAIL)        # fill() fine for email (no submit guard)
                    print(f"  Filled email via {sel}")
                    break
            except Exception:
                pass

        await asyncio.sleep(1)
        for sel in ['button:has-text("Next")', 'button[type="submit"]']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    print(f"  Clicked Next via {sel}")
                    break
            except Exception:
                pass

        # Wait for secure.sysco.com Okta page
        await asyncio.sleep(5)
        print(f"URL after email: {page.url}")
        await page.screenshot(path=f"{API_DIR}/sysco3_01_okta.png")

        # ── Step 3: Password with real key events ──────────
        if "secure.sysco.com" in page.url or "signin" in page.url:
            print("\n→ Okta page — entering password with real keystroke events ...")

            # Wait for password field
            pw_sel = None
            for sel in ['input[type="password"]', 'input[name="credentials.passcode"]',
                        '#okta-signin-password', 'input[autocomplete="current-password"]']:
                try:
                    el = page.locator(sel).first
                    await el.wait_for(state="visible", timeout=8000)
                    pw_sel = sel
                    print(f"  Password field found: {sel}")
                    break
                except Exception:
                    pass

            if pw_sel:
                pw_el = page.locator(pw_sel).first
                # Click to focus (this is what the user does manually)
                await pw_el.click()
                await asyncio.sleep(0.5)
                # type() fires real keydown/keypress/keyup events — activates submit button
                await pw_el.type(PASSWORD, delay=40)
                await asyncio.sleep(0.8)
                print(f"  Typed password (real key events)")

                # Press Enter — always works even if submit button selector varies
                await page.keyboard.press("Enter")
                print("  Pressed Enter to submit")
            else:
                print("  ⚠️  Password field not found — taking screenshot for inspection")
                await page.screenshot(path=f"{API_DIR}/sysco3_no_pw.png")

            print("→ Waiting for redirect to shop.sysco.com ...")
            try:
                await page.wait_for_url("**/shop.sysco.com/**", timeout=25000)
            except Exception:
                pass
            await asyncio.sleep(6)
            print(f"URL after login: {page.url}")

        await page.screenshot(path=f"{API_DIR}/sysco3_02_portal.png")
        text = await page.evaluate("() => document.body.innerText.slice(0,2000)")
        print(f"\nPage text:\n{text[:1500]}")

        logged_in = ("shop.sysco.com" in page.url and
                     "login" not in page.url and "auth" not in page.url)
        print(f"\n{'✅ Logged in!' if logged_in else '⚠️  Still at ' + page.url}")

        if not logged_in:
            print("Login failed — saving screenshot and exiting")
            await page.screenshot(path=f"{API_DIR}/sysco3_fail.png")
            await browser.close()
            return

        # Save session cookies
        await ctx.storage_state(path=SESSION_FILE)
        print(f"✅ Session saved → {SESSION_FILE}")

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

        # ── Step 4: Navigate to Order Guide ───────────────
        await asyncio.sleep(5)
        print("\n\n→ Looking for Order Guide ...")

        # Click nav links
        for label in ["Order Guide", "My Order Guide", "Catalog", "Browse", "Products", "Shop"]:
            try:
                el = page.locator(f"text=/{label}/i").first
                if await el.is_visible(timeout=2000):
                    print(f"  Found '{label}', clicking ...")
                    await el.click()
                    await asyncio.sleep(7)
                    print(f"  URL: {page.url}")
                    break
            except Exception:
                pass

        # If nav didn't work, try direct URL
        if "order-guide" not in page.url and "catalog" not in page.url:
            for url in [
                "https://shop.sysco.com/app/order-guide",
                "https://shop.sysco.com/app/catalog",
            ]:
                print(f"  Direct nav: {url}")
                await page.goto(url, timeout=20000)
                await asyncio.sleep(7)
                print(f"  URL: {page.url}")
                if "login" not in page.url and "auth" not in page.url:
                    break

        await asyncio.sleep(5)
        await page.screenshot(path=f"{API_DIR}/sysco3_03_guide.png")
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
                          "x-syy-locale", "x-customer-id", "x-account-id"):
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
                n = len(body)
                print(f"    [{n} items]")
                if body and isinstance(body[0], dict):
                    print(f"    first keys: {list(body[0].keys())[:8]}")
                    print(f"    {json.dumps(body[0])[:300]}")

        out = {"requests": all_requests[:250], "responses": api_responses,
               "storage": storage}
        with open(f"{API_DIR}/sysco_capture3.json", "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\n✅ Capture saved → {API_DIR}/sysco_capture3.json")

        await asyncio.sleep(10)
        await browser.close()

asyncio.run(main())
