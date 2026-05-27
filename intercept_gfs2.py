"""
GFS pass 2 — complete Okta SSO login and capture the order portal API.

GFS uses Okta SAML2 SSO:
  order.gfs.com → sso.gfs.com (Okta) → SAML assertion → order.gfs.com

Goals:
  1. Complete the Okta login form
  2. Land on the order portal
  3. Navigate to the order guide / seasonal list
  4. Intercept ALL API calls: endpoint patterns, auth headers, response shape
  5. Find the product list and pricing endpoints
"""
import asyncio, json, os
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/gfs_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
os.makedirs(API_DIR, exist_ok=True)

PORTAL_URL = "https://order.gfs.com/home"
EMAIL      = "carlos@onparbar.com"
PASSWORD   = "Onpar24!"

all_requests  = []
api_responses = {}

def _is_interesting(url):
    skip_exts = (".css", ".js", ".png", ".jpg", ".svg", ".woff", ".ico", ".gif", ".ttf", ".woff2")
    skip_domains = ("google.com", "doubleclick.net", "analytics", "googleads", "bing.com",
                    "facebook.com", "hotjar.com", "segment.io", "zendesk.com")
    if any(url.endswith(e) or f"{e}?" in url for e in skip_exts):
        return False
    if any(d in url for d in skip_domains):
        return False
    return ("gfs.com" in url or "gordonfs" in url or "okta" in url or
            "azure" in url or "api" in url.lower()[:50])

async def handle_request(request):
    if not _is_interesting(request.url): return
    try: body = request.post_data
    except Exception: body = None
    try: hdrs = dict(await request.all_headers())
    except Exception: hdrs = {}
    all_requests.append({"method": request.method, "url": request.url,
                         "headers": hdrs, "body": body})
    short = request.url[:100]
    if body and len(body) < 500:
        print(f"  REQ {request.method:6} {short}")
        print(f"       body: {body[:250]}")
    else:
        print(f"  REQ {request.method:6} {short}")

async def handle_response(response):
    if not _is_interesting(response.url): return
    try:
        body = await response.json()
        api_responses[response.url] = body
        size = len(json.dumps(body)) / 1024
        print(f"  RSP {response.url[:90]} ({size:.1f}KB) status={response.status}")
    except Exception:
        pass

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=100)
        ctx  = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        print("→ Loading GFS portal (fresh, no saved session)...")
        await page.goto(PORTAL_URL, timeout=45000)
        await asyncio.sleep(4)
        print(f"URL: {page.url}")

        # ── Handle Okta login form ─────────────────────────
        if "sso.gfs.com" in page.url or "okta" in page.url:
            print("\n→ Okta login page detected")
            await page.screenshot(path=f"{API_DIR}/gfs_login.png")

            # Okta uses "identifier" field (not "email")
            await asyncio.sleep(2)
            for sel in ['input[name="identifier"]', '#okta-signin-username',
                        'input[type="email"]', 'input[placeholder*="username" i]',
                        'input[placeholder*="email" i]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.fill(EMAIL)
                        print(f"  Filled username via {sel}")
                        break
                except Exception:
                    pass

            await asyncio.sleep(1)
            # Click Next
            for sel in ['input[value="Next"]', 'button[data-type="save"]',
                        'button:has-text("Next")', 'button[type="submit"]',
                        '.button-primary', '[data-se="o-form-button-bar"] input']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        await asyncio.sleep(3)
                        print(f"  Clicked Next: {sel}")
                        break
                except Exception:
                    pass

            print(f"URL after username: {page.url}")
            await page.screenshot(path=f"{API_DIR}/gfs_after_username.png")

            # Fill password
            for sel in ['input[name="credentials.passcode"]', '#okta-signin-password',
                        'input[type="password"]', 'input[name="password"]']:
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
            for sel in ['input[value="Verify"]', 'input[value="Sign In"]',
                        'button:has-text("Sign In")', 'button:has-text("Verify")',
                        'button[type="submit"]', '.button-primary',
                        '[data-se="o-form-button-bar"] input']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        print(f"  Clicked Submit: {sel}")
                        break
                except Exception:
                    pass

            # Wait for redirect back to order.gfs.com
            print("→ Waiting for SSO redirect...")
            try:
                await page.wait_for_url("**/order.gfs.com/**", timeout=20000)
            except Exception:
                pass
            await asyncio.sleep(6)
            print(f"URL after login: {page.url}")

        await page.screenshot(path=f"{API_DIR}/gfs_portal.png")
        text = await page.evaluate("() => document.body.innerText.slice(0,3000)")
        print(f"\nPage text:\n{text[:2000]}")

        # Save session
        await ctx.storage_state(path=SESSION_FILE)
        print(f"\n✅ Session saved → {SESSION_FILE}")

        # Dump all storage to find tokens
        storage = await page.evaluate("""
            () => {
                const result = {ls: {}, ss: {}};
                for (let i=0; i<localStorage.length; i++) {
                    const k = localStorage.key(i);
                    result.ls[k] = localStorage.getItem(k);
                }
                for (let i=0; i<sessionStorage.length; i++) {
                    const k = sessionStorage.key(i);
                    result.ss[k] = sessionStorage.getItem(k);
                }
                return result;
            }
        """)
        print(f"\nlocalStorage ({len(storage['ls'])} keys):")
        for k, v in list(storage['ls'].items())[:20]:
            print(f"  {k[:80]}: {str(v)[:100]}")
        print(f"\nsessionStorage ({len(storage['ss'])} keys):")
        for k, v in list(storage['ss'].items())[:20]:
            print(f"  {k[:80]}: {str(v)[:100]}")

        # ── Navigate to order guide ───────────────────────
        await asyncio.sleep(4)
        print("\n\n→ Looking for order guide/seasonal list...")

        # Try nav items
        for label in ["Order Guide", "Seasonal", "Products", "Browse", "Shop", "Catalog",
                      "Items", "My Lists", "Lists"]:
            try:
                el = page.locator(f"text=/{label}/i").first
                if await el.is_visible(timeout=2000):
                    print(f"  Found '{label}', clicking...")
                    await el.click()
                    await asyncio.sleep(5)
                    print(f"  URL: {page.url}")
                    break
            except Exception:
                pass

        await asyncio.sleep(4)
        await page.screenshot(path=f"{API_DIR}/gfs_order_guide.png")
        text2 = await page.evaluate("() => document.body.innerText.slice(0,3000)")
        print(f"\nPage text after nav:\n{text2[:2000]}")

        # ── Add ONE item to cart to capture the add-to-cart endpoint ─────────
        print("\n\n→ Attempting to add one item to cart (to capture endpoint)...")
        cart_captured = False
        # Try quantity inputs first (type "1" and press Enter)
        for sel in [
            'input[aria-label*="quantity" i]',
            'input[placeholder*="qty" i]',
            'input[type="number"]',
            '[class*="quantity"] input',
            '[class*="qty"] input',
        ]:
            try:
                els = page.locator(sel)
                cnt = await els.count()
                if cnt > 0:
                    first = els.first
                    if await first.is_visible(timeout=2000):
                        await first.triple_click()
                        await first.type("1")
                        await first.press("Tab")
                        await asyncio.sleep(4)   # wait for cart POST to fire
                        print(f"  Entered qty via: {sel}")
                        cart_captured = True
                        break
            except Exception:
                pass

        # If no qty input, try "Add to Cart" button
        if not cart_captured:
            for sel in [
                'button:has-text("Add to Cart")',
                'button:has-text("Add to Order")',
                '[class*="add-to-cart"]',
                '[data-testid*="add"]',
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        await asyncio.sleep(4)
                        print(f"  Clicked add via: {sel}")
                        cart_captured = True
                        break
                except Exception:
                    pass

        if cart_captured:
            print("  ✅ Cart interaction done — check captured requests for cart endpoint")
        else:
            print("  ⚠️  No add-to-cart element found — inspect page manually if needed")

        await page.screenshot(path=f"{API_DIR}/gfs_after_cart.png")
        await asyncio.sleep(3)

        # ── Print summary ──────────────────────────────────
        print(f"\n\n=== API REQUESTS ({len(all_requests)}) ===")
        seen = set()
        for req in all_requests:
            ep = req["url"].split("?")[0]
            if ep not in seen:
                seen.add(ep)
                print(f"\n  {req['method']:6} {ep}")
                if req.get("body") and len(str(req["body"])) < 300:
                    print(f"    body: {str(req['body'])[:200]}")
                # Show Authorization header if present
                auth = req.get("headers", {}).get("authorization", "")
                if auth and not auth.startswith("Basic"):
                    print(f"    auth: {auth[:80]}")

        print(f"\n=== RESPONSES ({len(api_responses)}) ===")
        for url, body in api_responses.items():
            size = len(json.dumps(body)) / 1024
            print(f"\n  {url[:90]} ({size:.1f}KB)")
            if isinstance(body, dict):
                print(f"    keys: {list(body.keys())[:10]}")
                sample = json.dumps(body)[:300]
                print(f"    {sample}")
            elif isinstance(body, list):
                print(f"    [{len(body)} items]")
                if body:
                    print(f"    first keys: {list(body[0].keys())[:8] if isinstance(body[0], dict) else body[0]}")

        # Save capture
        out = {"requests": all_requests[:150], "responses": api_responses,
               "storage": storage}
        with open(f"{API_DIR}/gfs_capture2.json", "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\n✅ Capture saved → {API_DIR}/gfs_capture2.json")

        await asyncio.sleep(8)
        await browser.close()

asyncio.run(main())
