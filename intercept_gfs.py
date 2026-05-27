"""
GFS (Gordon Food Service) pass 1 — login and full API capture.

Goals:
  1. Log in to order.gfs.com with saved session or fresh credentials
  2. Intercept ALL network calls (domain, method, body, response)
  3. Navigate to the order guide / catalog to trigger product+price API calls
  4. Identify: auth endpoint, token storage location, product list endpoint, price endpoint
  5. Save session + config for use by scrape_gfs.py

Login: cchavando@onparbar.com / !Onpar4464
Portal: https://order.gfs.com/home
"""
import asyncio, json, os, base64
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/gfs_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
os.makedirs(API_DIR, exist_ok=True)

PORTAL_URL = "https://order.gfs.com/home"
EMAIL      = "cchavando@onparbar.com"
PASSWORD   = "!Onpar4464"

all_requests  = []   # list of {method, url, headers, body}
api_responses = {}   # url -> response body

INTERESTING_DOMAINS = [
    "gfs.com", "gordonfs.com", "gordonfoodservice.com",
    "azurewebsites.net", "azure.com", "microsoftonline.com",
    "b2clogin.com", "api.", "cdn.",
]

def _is_interesting(url):
    return any(d in url for d in INTERESTING_DOMAINS) and not any(
        ext in url for ext in [".css", ".js", ".png", ".jpg", ".svg", ".woff", ".ico"]
    )

async def handle_request(request):
    if not _is_interesting(request.url): return
    try: body = request.post_data
    except Exception: body = None
    try: hdrs = dict(await request.all_headers())
    except Exception: hdrs = {}
    all_requests.append({
        "method":  request.method,
        "url":     request.url,
        "headers": {k: v for k, v in hdrs.items() if not k.startswith(":")},
        "body":    body,
    })
    short = request.url[:100]
    if body and len(body) < 400:
        print(f"  REQ {request.method:6} {short}")
        print(f"       body: {body[:200]}")
    else:
        print(f"  REQ {request.method:6} {short}")

async def handle_response(response):
    if not _is_interesting(response.url): return
    try:
        body = await response.json()
        api_responses[response.url] = body
        size = len(json.dumps(body)) / 1024
        print(f"  RSP {response.url[:90]} ({size:.1f}KB)")
    except Exception:
        pass

async def dump_all_storage(page):
    """Dump localStorage, sessionStorage, and cookies."""
    storage = await page.evaluate("""
        () => {
            const ls = {}, ss = {};
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                ls[k] = localStorage.getItem(k);
            }
            for (let i = 0; i < sessionStorage.length; i++) {
                const k = sessionStorage.key(i);
                ss[k] = sessionStorage.getItem(k);
            }
            return {localStorage: ls, sessionStorage: ss};
        }
    """)
    return storage

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=150)

        # Try loading saved session first
        ctx_kwargs = {"viewport": {"width": 1440, "height": 900}}
        if os.path.exists(SESSION_FILE):
            print(f"→ Loading saved session from {SESSION_FILE}")
            ctx_kwargs["storage_state"] = SESSION_FILE
        ctx  = await browser.new_context(**ctx_kwargs)
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        print(f"→ Navigating to {PORTAL_URL}...")
        await page.goto(PORTAL_URL, timeout=45000)
        await asyncio.sleep(5)
        print(f"URL: {page.url}")

        # Check if we need to log in
        current_url = page.url
        if "login" in current_url.lower() or "signin" in current_url.lower() or \
           await page.locator('input[type="email"], input[name="email"], #email').count() > 0:
            print("\n→ Login required — filling credentials...")
            await asyncio.sleep(2)
            # Try email field
            for sel in ['input[type="email"]', 'input[name="email"]', '#email',
                        'input[placeholder*="email" i]', 'input[autocomplete="email"]']:
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
            for sel in ['button:has-text("Next")', 'button:has-text("Continue")',
                        'button[type="submit"]:has-text("Next")', '[id*="next"]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        await asyncio.sleep(2)
                        print(f"  Clicked next: {sel}")
                        break
                except Exception:
                    pass

            # Fill password
            for sel in ['input[type="password"]', 'input[name="password"]', '#password',
                        'input[placeholder*="password" i]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=3000):
                        await el.fill(PASSWORD)
                        print(f"  Filled password via {sel}")
                        break
                except Exception:
                    pass

            await asyncio.sleep(1)
            # Submit
            for sel in ['button[type="submit"]', 'button:has-text("Sign in")',
                        'button:has-text("Log in")', 'button:has-text("Login")',
                        'input[type="submit"]']:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click()
                        print(f"  Clicked submit: {sel}")
                        break
                except Exception:
                    pass

            await asyncio.sleep(8)
            print(f"URL after login: {page.url}")

        # Screenshot
        await page.screenshot(path=f"{API_DIR}/gfs_home.png")
        text = await page.evaluate("() => document.body.innerText.slice(0,2000)")
        print(f"\nPage text:\n{text[:1500]}")

        # Save session
        await ctx.storage_state(path=SESSION_FILE)
        print(f"\n✅ Session saved → {SESSION_FILE}")

        # Dump storage to find tokens
        print("\n→ Dumping storage for tokens...")
        storage = await dump_all_storage(page)
        ls = storage.get("localStorage", {})
        ss = storage.get("sessionStorage", {})
        print(f"  localStorage: {len(ls)} keys")
        print(f"  sessionStorage: {len(ss)} keys")

        # Look for token-like values
        token_data = {}
        for store_name, store in [("localStorage", ls), ("sessionStorage", ss)]:
            for k, v in store.items():
                if any(word in k.lower() for word in
                       ["token", "auth", "access", "refresh", "bearer", "jwt", "user"]):
                    print(f"  [{store_name}] {k[:80]}")
                    try:
                        parsed = json.loads(v)
                        if isinstance(parsed, dict):
                            print(f"    = {json.dumps(parsed)[:200]}")
                        else:
                            print(f"    = {str(v)[:100]}")
                    except Exception:
                        print(f"    = {str(v)[:100]}")
                    token_data[k] = v

        # Also look for JWT-like values (base64 with dots)
        for store_name, store in [("localStorage", ls), ("sessionStorage", ss)]:
            for k, v in store.items():
                if isinstance(v, str) and v.count(".") >= 2 and len(v) > 100 and " " not in v:
                    if k not in token_data:
                        print(f"  [{store_name}] JWT-like: {k[:60]} = {v[:60]}...")
                        token_data[k] = v

        # ── Navigate to Order Guide / Catalog ─────────────
        print("\n\n→ Looking for Order Guide / Catalog link...")
        for label in ["Order Guide", "Products", "Browse", "Catalog", "Shop", "Items"]:
            for sel in [f"text={label}", f"a:has-text('{label}')",
                        f"button:has-text('{label}')", f"[href*='{label.lower()}']"]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        print(f"  Found '{label}' via {sel}, clicking...")
                        await el.click()
                        await asyncio.sleep(6)
                        print(f"  URL: {page.url}")
                        text = await page.evaluate(
                            "() => document.body.innerText.slice(0,2000)")
                        print(f"  Page text:\n{text[:1000]}")
                        await page.screenshot(path=f"{API_DIR}/gfs_catalog.png")
                        break
                except Exception:
                    pass
            else:
                continue
            break

        await asyncio.sleep(6)

        # ── Summary of all captured calls ─────────────────
        print(f"\n\n=== TOTAL REQUESTS: {len(all_requests)} ===")
        seen_eps = {}
        for req in all_requests:
            url = req["url"]
            ep = url.split("?")[0][-80:]
            if ep not in seen_eps:
                seen_eps[ep] = req["method"]
                print(f"  {req['method']:6} {ep}")

        print(f"\n=== RESPONSES: {len(api_responses)} ===")
        for url, body in api_responses.items():
            size = len(json.dumps(body)) / 1024
            print(f"  {url[:80]} ({size:.1f}KB)")
            if isinstance(body, dict):
                print(f"    keys: {list(body.keys())[:8]}")
            elif isinstance(body, list) and body:
                print(f"    [{len(body)} items] first keys: "
                      f"{list(body[0].keys())[:6] if isinstance(body[0], dict) else '?'}")

        # Save full capture
        out = {
            "requests": all_requests[:100],
            "responses": {k: v for k, v in list(api_responses.items())[:20]},
            "token_storage": token_data,
            "localStorage":  ls,
            "sessionStorage": ss,
        }
        with open(f"{API_DIR}/gfs_capture.json", "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\n✅ Full capture saved → {API_DIR}/gfs_capture.json")

        await asyncio.sleep(10)
        await browser.close()

asyncio.run(main())
