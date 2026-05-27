"""
Sysco pass 4 — logged in, now capture order guide + price GraphQL queries.

From pass 3 we know:
  - Login works: type() password → press Enter
  - API: GraphQL at https://gateway-api.shop.sysco.com/graphql
  - Auth: Bearer JWT in Authorization header (from auth.shop.sysco.com/api/v1/auth/validate)
  - Customer: OpCo=019, CustomerNumber=700932, OktaUserId=00uksk91yvpVzXEjr5d7
  - Lists visible: "Order Guide 8.5.25"
  - Auth token is in gatewayCredentials from auth/validate response

Goals this pass:
  1. Log in (reuse pass 3 flow)
  2. Navigate directly to Order Guide list "Order Guide 8.5.25"
  3. Scroll to load all items
  4. Capture the exact GraphQL operationName + variables for order guide and prices
  5. Save Bearer token + customer IDs for scraper implementation
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
api_responses = {}  # url -> list of bodies (multiple calls to same endpoint)
bearer_token  = None

SKIP_EXTS    = (".css", ".js", ".png", ".jpg", ".svg", ".woff", ".ico",
                ".gif", ".ttf", ".woff2", ".webp", ".map")
SKIP_DOMAINS = ("google.com", "doubleclick.net", "googleads", "bing.com",
                "facebook.com", "hotjar.com", "zendesk.com",
                "newrelic", "nr-data", "launchdarkly", "akamai", "cloudfront",
                "browser-intake-datadoghq", "datadoghq", "intercom",
                "contentsquare", "connect.facebook")

def _is_interesting(url):
    if any(url.split("?")[0].endswith(e) for e in SKIP_EXTS):
        return False
    if any(d in url for d in SKIP_DOMAINS):
        return False
    return any(d in url for d in [
        "sysco.com", "syy1.com", "auth.shop", "gateway-api",
        "web-bff", "secure.sysco",
    ])

async def handle_request(request):
    global bearer_token
    if not _is_interesting(request.url): return
    try: body = request.post_data
    except Exception: body = None
    try: hdrs = dict(await request.all_headers())
    except Exception: hdrs = {}
    all_requests.append({"method": request.method, "url": request.url,
                         "headers": hdrs, "body": body})
    # Capture bearer token
    auth = hdrs.get("authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 20:
        if bearer_token != auth:
            bearer_token = auth
    # Print GraphQL specifically
    if "graphql" in request.url and body:
        try:
            b = json.loads(body)
            op = b.get("operationName", "?")
            vars_ = json.dumps(b.get("variables", {}))[:120]
            print(f"  GQL  {op}  vars={vars_}")
        except Exception:
            pass

async def handle_response(response):
    if not _is_interesting(response.url): return
    try:
        body = await response.json()
        url = response.url
        if url not in api_responses:
            api_responses[url] = []
        api_responses[url].append(body)
        size = len(json.dumps(body)) / 1024
        # For GraphQL, decode what operation this was
        if "graphql" in url:
            data = body.get("data", {})
            keys = list(data.keys())[:4] if isinstance(data, dict) else []
            print(f"  RSP graphql ({size:.1f}KB)  data keys={keys}")
        else:
            print(f"  RSP {url[:90]} ({size:.1f}KB)")
            if isinstance(body, dict):
                print(f"       keys={list(body.keys())[:8]}")
    except Exception:
        pass

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=80)
        ctx  = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        # ── Login ──────────────────────────────────────────
        print(f"→ Loading {PORTAL_URL} ...")
        await page.goto(PORTAL_URL, timeout=45000)
        await asyncio.sleep(4)

        for sel in ['input[type="email"]', 'input[name="username"]']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    await el.fill(EMAIL)
                    break
            except Exception: pass

        for sel in ['button:has-text("Next")', 'button[type="submit"]']:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.click()
                    break
            except Exception: pass

        await asyncio.sleep(5)

        if "secure.sysco.com" in page.url or "signin" in page.url:
            for sel in ['input[type="password"]', 'input[name="credentials.passcode"]']:
                try:
                    el = page.locator(sel).first
                    await el.wait_for(state="visible", timeout=8000)
                    await el.click()
                    await el.type(PASSWORD, delay=40)
                    break
                except Exception: pass
            await page.keyboard.press("Enter")
            try:
                await page.wait_for_url("**/shop.sysco.com/**", timeout=25000)
            except Exception: pass
            await asyncio.sleep(6)

        print(f"URL after login: {page.url}")
        logged_in = "shop.sysco.com" in page.url and "auth/login" not in page.url
        if not logged_in:
            print("⚠️  Login failed — check credentials")
            await page.screenshot(path=f"{API_DIR}/sysco4_fail.png")
            await browser.close()
            return

        print("✅ Logged in!")
        await page.screenshot(path=f"{API_DIR}/sysco4_01_portal.png")

        # Save session immediately
        await ctx.storage_state(path=SESSION_FILE)

        # ── Navigate to Order Guide ────────────────────────
        await asyncio.sleep(3)
        print("\n→ Navigating to Order Guide ...")

        # Look for the Lists section in nav / sidebar
        for label in ["Order Guide", "Order Guide 8.5.25", "Lists"]:
            try:
                el = page.locator(f"text=/{label}/i").first
                if await el.is_visible(timeout=3000):
                    print(f"  Clicking '{label}' ...")
                    await el.click()
                    await asyncio.sleep(5)
                    print(f"  URL: {page.url}")
                    break
            except Exception: pass

        # Also try the order-guide URL directly
        if "order-guide" not in page.url and "list" not in page.url.lower():
            print("  Trying direct URL ...")
            for url in [
                "https://shop.sysco.com/app/order-guide",
                "https://shop.sysco.com/app/lists",
                "https://shop.sysco.com/app/catalog?listType=orderGuide",
            ]:
                await page.goto(url, timeout=20000)
                await asyncio.sleep(5)
                print(f"  URL: {page.url}")
                if "login" not in page.url:
                    break

        await page.screenshot(path=f"{API_DIR}/sysco4_02_orderguide.png")
        text = await page.evaluate("() => document.body.innerText.slice(0,3000)")
        print(f"\nPage text:\n{text[:2000]}")

        # Scroll to load all items
        print("\n→ Scrolling to load all order guide items ...")
        for i in range(8):
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)

        await page.screenshot(path=f"{API_DIR}/sysco4_03_scrolled.png")
        await asyncio.sleep(3)

        # ── Dump key data ──────────────────────────────────
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

        print(f"\nBearer token captured: {bearer_token[:80] if bearer_token else 'NONE'}...")

        # ── Summary ───────────────────────────────────────
        print(f"\n\n=== ALL GRAPHQL OPERATIONS ===")
        gql_ops = {}
        for req in all_requests:
            if "graphql" not in req["url"]: continue
            if not req.get("body"): continue
            try:
                b = json.loads(req["body"])
                op = b.get("operationName", "?")
                vars_ = b.get("variables", {})
                query = b.get("query", "")[:200]
                if op not in gql_ops:
                    gql_ops[op] = {"count": 0, "vars_sample": vars_, "query": query}
                gql_ops[op]["count"] += 1
            except Exception:
                pass
        for op, info in gql_ops.items():
            print(f"\n  {op}  (×{info['count']})")
            print(f"    vars: {json.dumps(info['vars_sample'])[:150]}")
            if info["query"]:
                print(f"    query: {info['query'][:150]}")

        print(f"\n\n=== GRAPHQL RESPONSES (large ones) ===")
        for url, bodies in api_responses.items():
            if "graphql" not in url: continue
            for body in bodies:
                size = len(json.dumps(body)) / 1024
                if size < 5: continue  # skip small responses
                data = body.get("data", {})
                print(f"\n  {size:.1f}KB  data keys={list(data.keys())[:6]}")
                # Show structure of each key
                for k, v in data.items():
                    if isinstance(v, list):
                        print(f"    {k}: [{len(v)} items]")
                        if v and isinstance(v[0], dict):
                            print(f"      item keys: {list(v[0].keys())[:8]}")
                            print(f"      item[0]: {json.dumps(v[0])[:300]}")
                    elif isinstance(v, dict):
                        print(f"    {k}: {{{list(v.keys())[:8]}}}")
                        print(f"      {json.dumps(v)[:300]}")

        print(f"\n\n=== NON-GRAPHQL RESPONSES ===")
        for url, bodies in api_responses.items():
            if "graphql" in url: continue
            for body in bodies:
                size = len(json.dumps(body)) / 1024
                print(f"\n  {url[:90]} ({size:.1f}KB)")
                if isinstance(body, dict):
                    print(f"    keys: {list(body.keys())[:10]}")
                    print(f"    {json.dumps(body)[:400]}")

        # Save everything
        out = {
            "bearer_token": bearer_token,
            "requests": all_requests[:300],
            "responses": {k: v for k, v in api_responses.items()},
            "storage": storage,
            "gql_ops": gql_ops,
        }
        with open(f"{API_DIR}/sysco_capture4.json", "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\n✅ Capture saved → {API_DIR}/sysco_capture4.json")

        await asyncio.sleep(10)
        await browser.close()

asyncio.run(main())
