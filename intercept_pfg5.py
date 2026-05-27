"""
PFG pass 5 — direct API calls to get order lines, product catalog, and order guide.
Also navigates inside the React SPA to trigger list/order-guide API calls.
"""
import asyncio, json, os, urllib.request, urllib.error
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/pfg_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")

API_BASE     = "https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api"
CUSTOMER_ID  = "ccbddeae-bc43-4287-a4e0-8d5bee2b913c"
CUSTOMER_NUM = "03510"
OPCO_NUM     = "795"
BIZ_UNIT_KEY = "3"
ORDER_ID     = "99875cd1-c770-456a-a902-6c796b6291b0"  # active order with 31 lines

api_calls  = []
api_bodies = {}

def _is_our_api(url):
    return "azurewebsites.net/api" in url

async def handle_request(request):
    if not _is_our_api(request.url):
        return
    try: post = request.post_data
    except Exception: post = None
    api_calls.append({"method": request.method, "url": request.url, "body": post})
    short = request.url.split("/api/")[-1][:70]
    print(f"  REQ {request.method:6} {short}" + (f"  body={post[:80]}" if post else ""))

async def handle_response(response):
    if not _is_our_api(response.url):
        return
    try:
        body = await response.json()
        api_bodies[response.url] = body
        size = len(json.dumps(body))/1024
        print(f"  RSP {response.url.split('/api/')[-1][:70]} ({size:.1f}KB)")
    except Exception:
        pass

def call(bearer, method, endpoint, payload=None, params=None):
    url = f"{API_BASE}/{endpoint}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    hdrs = {"Authorization": bearer, "Accept": "application/json", "Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode()[:300]}

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=100)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900}, storage_state=SESSION_FILE)
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        print("→ Loading portal...")
        await page.goto("https://www.customerfirstsolutions.com/", timeout=30000)
        await asyncio.sleep(4)

        # Get token from session storage
        token_data = await page.evaluate("""
            () => {
                for (let i = 0; i < sessionStorage.length; i++) {
                    const k = sessionStorage.key(i);
                    if (k.includes('accesstoken') && k.includes('pfgcustomerfirst')) {
                        try { const v = JSON.parse(sessionStorage.getItem(k)); if (v.secret) return v; }
                        catch {}
                    }
                }
                return null;
            }
        """)
        bearer = f"Bearer {token_data['secret']}" if token_data else None
        print(f"Bearer token: {'✅ ' + str(len(bearer)) + ' chars' if bearer else '❌ not found'}")

        # ── Direct API exploration ──────────────────────────
        print("\n=== Direct API calls ===")

        # 1. Get active order lines
        print("\n[1] Active order lines...")
        r = call(bearer, "GET", "OrderEntryDetail/V1/GetOrderEntryDetails",
            params={"OrderEntryHeaderId": ORDER_ID})
        size = len(json.dumps(r))/1024
        print(f"  GetOrderEntryDetails: {size:.1f}KB  err={r.get('_error','ok')}")
        if not r.get("_error"):
            items = r if isinstance(r, list) else r.get("ResultObject", r)
            if isinstance(items, list):
                print(f"  → {len(items)} order lines")
                if items:
                    print(f"  First line keys: {list(items[0].keys())[:10]}")
                    print(f"  First line: {json.dumps(items[0])[:300]}")
                    with open(f"{API_DIR}/pfg_order_lines.json", "w") as f:
                        json.dump(items, f, indent=2)
                    print(f"  Saved order lines → {API_DIR}/pfg_order_lines.json")

        # 2. Try product list / order guide endpoints
        print("\n[2] Product list / order guide endpoints...")
        endpoints = [
            ("GET",  "ProductList/V1/GetProductLists",        None,
             {"CustomerId": CUSTOMER_ID}),
            ("POST", "ProductList/V1/GetProductListDetails",  {"ProductListId": "all", "CustomerId": CUSTOMER_ID},
             None),
            ("POST", "OrderGuide/V1/GetOrderGuideItems",      {"CustomerId": CUSTOMER_ID, "BusinessUnitKey": BIZ_UNIT_KEY},
             None),
            ("GET",  "OrderGuide/V1/GetOrderGuide",           None,
             {"CustomerId": CUSTOMER_ID}),
            ("POST", "Product/V1/GetProducts",                {"CustomerId": CUSTOMER_ID, "BusinessUnitKey": BIZ_UNIT_KEY},
             None),
            ("POST", "Product/V1/SearchProducts",             {"SearchTerm": "", "CustomerId": CUSTOMER_ID, "PageSize": 20},
             None),
            ("GET",  "ProductList/V1/GetCustomerProductLists", None,
             {"CustomerId": CUSTOMER_ID, "BusinessUnitKey": BIZ_UNIT_KEY}),
            ("POST", "CustomerSpecialOrder/V1/GetCustomerSpecialOrders",
             {"CustomerNumber": CUSTOMER_NUM, "OperationCompanyNumber": OPCO_NUM,
              "CustomerId": CUSTOMER_ID}, None),
        ]
        for method, ep, payload, params in endpoints:
            r = call(bearer, method, ep, payload, params)
            size = len(json.dumps(r))/1024
            err = r.get("_error", "ok")
            print(f"  {method} {ep.split('/')[-1]}: {size:.1f}KB  err={err}")
            if err == "ok":
                result = r.get("ResultObject") or r.get("result") or r
                if isinstance(result, list) and result:
                    print(f"    → [{len(result)} items] first keys: {list(result[0].keys())[:6] if isinstance(result[0], dict) else result[0]}")
                    print(f"    First: {json.dumps(result[0])[:200]}")
                    with open(f"{API_DIR}/pfg_{ep.split('/')[-1]}.json", "w") as f:
                        json.dump(r, f, indent=2)
                elif isinstance(result, dict) and result:
                    print(f"    → keys: {list(result.keys())[:8]}")

        # ── Browser navigation into Lists ──────────────────
        print("\n=== Browser navigation: clicking Lists nav item ===")
        # The nav items are MUI spans inside a nav/header - find by text
        try:
            # Try clicking via JavaScript to bypass Playwright locator issues
            await page.evaluate("""
                () => {
                    const spans = Array.from(document.querySelectorAll('span'));
                    const listsSpan = spans.find(s => s.textContent.trim() === 'Lists');
                    if (listsSpan) listsSpan.click();
                }
            """)
            await asyncio.sleep(4)
            print(f"  URL after Lists click: {page.url}")
            text = await page.evaluate("() => document.body.innerText.slice(0,3000)")
            print(f"  Page text:\n{text[:2000]}")

            # Wait for List-related API calls
            await asyncio.sleep(4)

            # Take screenshot
            await page.screenshot(path=f"{API_DIR}/pfg_lists_nav.png")
        except Exception as e:
            print(f"  Error: {e}")

        # ── Summary ────────────────────────────────────────
        print(f"\n\n=== NEW API CALLS SEEN (browser navigation) ===")
        seen_eps = {c["url"].split("/api/")[-1].split("?")[0] for c in api_calls}
        for ep in sorted(seen_eps):
            if "List" in ep or "Guide" in ep or "Product" in ep:
                print(f"  {ep}")

        await asyncio.sleep(8)
        await browser.close()

asyncio.run(main())
