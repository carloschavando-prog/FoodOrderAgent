"""
PFG pass 9 — capture EXACT request bodies for GetOrderEntryCustomerProductPrice.

Flow:
  1. Load portal
  2. Create order via New Order button (or API)
  3. Navigate directly to order-entry/{id}?listId=...
  4. Wait for the price call to fire
  5. Capture ALL request bodies for ALL API calls
  6. Save the price request body and any intermediate calls
"""
import asyncio, json, os
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/pfg_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")

CUSTOMER_ID  = "ccbddeae-bc43-4287-a4e0-8d5bee2b913c"
FALL_LIST_ID = "13e8ce85-8f4e-4cfe-a6dd-cac49a88dc60"

api_requests  = {}   # url -> {method, body}
api_responses = {}   # url -> response body

def _is_api(url):
    return "azurewebsites.net/api" in url

async def handle_request(request):
    if not _is_api(request.url): return
    try: body = request.post_data
    except Exception: body = None
    ep = request.url.split("/api/")[-1].split("?")[0]
    api_requests[ep] = {"method": request.method, "url": request.url, "body": body}
    if body:
        print(f"  REQ {request.method:6} {ep[:70]}")
        print(f"       body ({len(body)}B): {body[:200]}")
    else:
        print(f"  REQ {request.method:6} {ep[:70]}")

async def handle_response(response):
    if not _is_api(response.url): return
    try:
        body = await response.json()
        ep = response.url.split("/api/")[-1].split("?")[0]
        api_responses[ep] = body
        size = len(json.dumps(body)) / 1024
        print(f"  RSP {ep[:70]} ({size:.1f}KB)")
    except Exception: pass

async def get_token(page):
    d = await page.evaluate("""
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
    return (f"Bearer {d['secret']}", d) if d else (None, None)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=150)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=SESSION_FILE)
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        print("→ Loading portal...")
        await page.goto("https://www.customerfirstsolutions.com/", timeout=30000)
        await asyncio.sleep(5)
        bearer, token_data = await get_token(page)
        print(f"Bearer: {'✅' if bearer else '❌'}")

        # Create a fresh order via API
        import urllib.request, urllib.error
        def call(method, ep, payload=None):
            api_base = "https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api"
            url = f"{api_base}/{ep}"
            hdrs = {"Authorization": bearer, "Accept": "application/json", "Content-Type": "application/json"}
            data = json.dumps(payload).encode() if payload else None
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())

        print("\n→ Creating order...")
        r = call("POST", "OrderEntryHeader/V1/CreateOrderEntryHeader",
                 {"CustomerId": CUSTOMER_ID, "BusinessUnitKey": 3})
        order_id = r["ResultObject"]["OrderEntryHeaderId"]
        print(f"  Order: {order_id}")

        # Navigate directly to order-entry page with listId
        order_url = (
            f"https://www.customerfirstsolutions.com/order-entry/{order_id}"
            f"?listId={FALL_LIST_ID}&sort=5"
        )
        print(f"\n→ Navigating to order entry with list...")
        print(f"  URL: {order_url}")
        await page.goto(order_url, timeout=30000)
        await asyncio.sleep(8)   # wait for all API calls to fire

        print(f"\nPage URL: {page.url}")

        # Screenshot
        await page.screenshot(path=f"{API_DIR}/pfg_order_entry_list.png")

        # Wait more for lazy-loaded price calls
        await asyncio.sleep(6)

        # ── Analyze captured requests ──────────────────────
        print("\n\n=== ALL API REQUESTS CAPTURED ===")
        for ep, info in api_requests.items():
            print(f"\n{info['method']:6} {ep}")
            if info['body']:
                try:
                    body_json = json.loads(info['body'])
                    print(f"  body: {json.dumps(body_json, indent=2)[:400]}")
                except Exception:
                    print(f"  body: {info['body'][:300]}")

        # ── Save price request body specifically ──────────
        price_ep = "CustomerProductPrice/V1/GetOrderEntryCustomerProductPrice"
        if price_ep in api_requests:
            req_info = api_requests[price_ep]
            print(f"\n\n=== PRICE REQUEST BODY ===")
            print(req_info['body'])
            with open(f"{API_DIR}/pfg_price_request_body.json", "w") as f:
                try:
                    json.dump(json.loads(req_info['body']), f, indent=2)
                except Exception:
                    f.write(req_info['body'])
            print(f"Saved → {API_DIR}/pfg_price_request_body.json")

        if price_ep in api_responses:
            prices = api_responses[price_ep].get("ResultObject", {}).get("CustomerProductPrices", [])
            nz = [p for p in prices if p.get("Price", 0) > 0]
            print(f"\n\n=== PRICE RESPONSE: {len(prices)} total, {len(nz)} non-zero ===")
            if nz:
                print("First 3:", json.dumps(nz[:3], indent=2))

        # Clean up order
        print("\n→ Deleting order...")
        try:
            call("POST", "OrderEntryHeader/V1/DeleteOrderEntryHeader",
                 {"OrderEntryHeaderId": order_id, "CustomerId": CUSTOMER_ID})
            print("  ✅ Order deleted")
        except Exception as e:
            print(f"  ⚠️  Could not delete: {e}")

        await asyncio.sleep(3)
        await browser.close()

asyncio.run(main())
