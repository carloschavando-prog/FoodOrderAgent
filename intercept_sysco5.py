"""
Sysco pass 5 — navigate directly to Order Guide 8.5.25 and capture the
GetListItems + pricing GraphQL query.

Known from pass 4:
  - Order Guide 8.5.25  listId = 66a83a1e-8c6f-4e83-820e-f485012da85f
  - shopAccountId = usbl-019-700932 (siteId=019, customerId=700932)
  - sellerId = USBL
  - Bearer JWT from auth.shop.sysco.com/api/v1/auth/validate → gatewayCredentials
  - All API via POST gateway-api.shop.sysco.com/graphql with operationName
"""
import asyncio, json, os
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/sysco_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
os.makedirs(API_DIR, exist_ok=True)

PORTAL_URL = "https://shop.sysco.com/auth/login"
EMAIL      = "carlos@onparbar.com"
PASSWORD   = "!Compass1066"

ORDER_GUIDE_LIST_ID = "66a83a1e-8c6f-4e83-820e-f485012da85f"
SHOP_ACCOUNT_ID     = "usbl-019-700932"

all_requests  = []
api_responses = {}
bearer_token  = None

SKIP_EXTS    = (".css", ".js", ".png", ".jpg", ".svg", ".woff", ".ico",
                ".gif", ".ttf", ".woff2", ".webp", ".map")
SKIP_DOMAINS = ("google.com", "doubleclick.net", "googleads", "bing.com",
                "facebook.com", "hotjar.com", "zendesk.com",
                "newrelic", "nr-data", "launchdarkly", "akamai", "cloudfront",
                "browser-intake-datadoghq", "datadoghq", "intercom",
                "contentsquare", "connect.facebook", "mediacdn")

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
    auth = hdrs.get("authorization", "")
    if auth.startswith("Bearer ") and len(auth) > 20:
        bearer_token = auth
    if "graphql" in request.url and body:
        try:
            b = json.loads(body)
            op = b.get("operationName", "?")
            vars_ = json.dumps(b.get("variables", {}))[:200]
            print(f"  GQL  {op}")
            print(f"       vars: {vars_}")
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
        if "graphql" in url:
            data = body.get("data", {})
            keys = list(data.keys())[:4] if isinstance(data, dict) else []
            print(f"  RSP graphql ({size:.1f}KB)  data={keys}")
        else:
            print(f"  RSP {url[:90]} ({size:.1f}KB)")
    except Exception:
        pass

async def login(page):
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

    return "shop.sysco.com" in page.url and "auth/login" not in page.url

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=80)
        ctx  = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        ok = await login(page)
        if not ok:
            print("⚠️  Login failed")
            await browser.close()
            return
        print(f"✅ Logged in! URL: {page.url}")
        await ctx.storage_state(path=SESSION_FILE)

        # ── Navigate directly to Order Guide ──────────────
        await asyncio.sleep(3)
        list_url = f"https://shop.sysco.com/app/lists/{ORDER_GUIDE_LIST_ID}"
        print(f"\n→ Navigating to Order Guide: {list_url}")
        await page.goto(list_url, timeout=30000)
        await asyncio.sleep(8)
        print(f"URL: {page.url}")
        await page.screenshot(path=f"{API_DIR}/sysco5_01_list.png")

        text = await page.evaluate("() => document.body.innerText.slice(0,2000)")
        print(f"\nPage text:\n{text[:1500]}")

        # Scroll to load all items
        print("\n→ Scrolling to load all items ...")
        prev_height = 0
        for i in range(15):
            h = await page.evaluate("document.body.scrollHeight")
            if h == prev_height:
                break
            prev_height = h
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
        print(f"  Done scrolling")

        await page.screenshot(path=f"{API_DIR}/sysco5_02_scrolled.png")

        # ── Show all GraphQL ops ───────────────────────────
        print(f"\n\n=== ALL GRAPHQL OPS (this pass) ===")
        gql_ops = {}
        for req in all_requests:
            if "graphql" not in req["url"]: continue
            if not req.get("body"): continue
            try:
                b = json.loads(req["body"])
                op = b.get("operationName", "?")
                vars_ = b.get("variables", {})
                q = b.get("query", "")
                if op not in gql_ops:
                    gql_ops[op] = {"count": 0, "vars": vars_, "query": q[:300]}
                gql_ops[op]["count"] += 1
            except Exception: pass

        for op, info in sorted(gql_ops.items()):
            print(f"\n  {op}  (×{info['count']})")
            print(f"    vars: {json.dumps(info['vars'])[:250]}")

        # ── Show large GraphQL responses ───────────────────
        print(f"\n\n=== LARGE GRAPHQL RESPONSES (>2KB) ===")
        for url, bodies in api_responses.items():
            if "graphql" not in url: continue
            for body in bodies:
                size = len(json.dumps(body)) / 1024
                if size < 2: continue
                data = body.get("data", {})
                print(f"\n  {size:.1f}KB  keys={list(data.keys())[:5]}")
                for k, v in data.items():
                    if isinstance(v, list):
                        print(f"    {k}: [{len(v)} items]")
                        if v and isinstance(v[0], dict):
                            print(f"      item[0] keys: {list(v[0].keys())[:10]}")
                            print(f"      item[0]: {json.dumps(v[0])[:400]}")
                    elif isinstance(v, dict):
                        sub_keys = list(v.keys())[:8]
                        print(f"    {k}: {{{sub_keys}}}")
                        for sk, sv in list(v.items())[:4]:
                            if isinstance(sv, list):
                                print(f"      {sk}: [{len(sv)} items]")
                                if sv and isinstance(sv[0], dict):
                                    print(f"        item[0] keys: {list(sv[0].keys())[:10]}")
                                    print(f"        item[0]: {json.dumps(sv[0])[:400]}")
                            elif isinstance(sv, dict):
                                print(f"      {sk}: {json.dumps(sv)[:200]}")
                            else:
                                print(f"      {sk}: {str(sv)[:100]}")

        # Save
        out = {
            "bearer_token":      bearer_token,
            "shop_account_id":   SHOP_ACCOUNT_ID,
            "order_guide_list_id": ORDER_GUIDE_LIST_ID,
            "requests":  all_requests[:300],
            "responses": api_responses,
            "gql_ops":   gql_ops,
        }
        with open(f"{API_DIR}/sysco_capture5.json", "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\n✅ Capture saved → {API_DIR}/sysco_capture5.json")
        print(f"Bearer token: {bearer_token[:80] if bearer_token else 'NONE'}...")

        await asyncio.sleep(8)
        await browser.close()

asyncio.run(main())
