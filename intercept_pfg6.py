"""
PFG pass 6 — click FALL 2025 list, capture product+price API calls.
Also reads the CustomerSpecialOrders file (PDF/Excel).
"""
import asyncio, json, os, base64, urllib.request, urllib.error
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/pfg_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
CONFIG_FILE  = os.path.expanduser("~/.FoodOrderAgent/pfg_api_config.json")

API_BASE     = "https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api"
CUSTOMER_ID  = "ccbddeae-bc43-4287-a4e0-8d5bee2b913c"
CUSTOMER_NUM = "03510"
OPCO_NUM     = "795"

api_bodies = {}

def _is_our_api(url):
    return "azurewebsites.net/api" in url

async def handle_request(request):
    if not _is_our_api(request.url): return
    try: post = request.post_data
    except Exception: post = None
    short = request.url.split("/api/")[-1][:80]
    if post: print(f"  REQ {request.method:6} {short}  body={post[:120]}")
    else:    print(f"  REQ {request.method:6} {short}")

async def handle_response(response):
    if not _is_our_api(response.url): return
    try:
        body = await response.json()
        api_bodies[response.url] = body
        size = len(json.dumps(body))/1024
        print(f"  RSP {response.url.split('/api/')[-1][:80]} ({size:.1f}KB)")
    except Exception: pass

def call_api(bearer, method, endpoint, payload=None, params=None):
    url = f"{API_BASE}/{endpoint}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    hdrs = {"Authorization": bearer, "Accept": "application/json", "Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode()[:300]}

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
    return f"Bearer {d['secret']}" if d else None

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=150)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900}, storage_state=SESSION_FILE)
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        print("→ Loading portal & navigating to List Management...")
        await page.goto("https://www.customerfirstsolutions.com/", timeout=30000)
        await asyncio.sleep(4)

        bearer = await get_token(page)
        print(f"Bearer: {'✅' if bearer else '❌'}")

        # Navigate to list-management via JS click on Lists span
        await page.evaluate("""
            () => {
                const span = Array.from(document.querySelectorAll('span'))
                    .find(s => s.textContent.trim() === 'Lists');
                if (span) span.click();
            }
        """)
        await asyncio.sleep(5)
        print(f"URL: {page.url}")

        # ── Look at the ProductListHeaders response ─────────
        print("\n→ Examining ProductListHeaders response...")
        list_headers_url = next(
            (u for u in api_bodies if "ProductListHeaders" in u), None)
        if list_headers_url:
            headers_data = api_bodies[list_headers_url]
            print(f"  Raw: {json.dumps(headers_data)[:500]}")
            items = (headers_data.get("ResultObject") or
                     headers_data if isinstance(headers_data, list) else [])
            print(f"  {len(items)} list headers")
            for item in items[:10]:
                print(f"    {json.dumps(item)[:200]}")

        # ── Click FALL 2025 ─────────────────────────────────
        print("\n→ Clicking FALL 2025...")
        try:
            fall_el = page.get_by_text("FALL 2025", exact=True).first
            await fall_el.click(timeout=5000)
            await asyncio.sleep(6)
            print(f"URL: {page.url}")
            text = await page.evaluate("() => document.body.innerText.slice(0,3000)")
            print(f"Page text:\n{text[:2000]}")
            await page.screenshot(path=f"{API_DIR}/pfg_fall2025.png")
        except Exception as e:
            print(f"  Could not click FALL 2025: {e}")
            # Try "Fall 2025" (mixed case)
            try:
                fall_el = page.get_by_text("Fall 2025").first
                await fall_el.click(timeout=5000)
                await asyncio.sleep(6)
                print(f"URL: {page.url}")
            except Exception as e2:
                print(f"  Also failed with mixed case: {e2}")

        await asyncio.sleep(6)

        # ── Examine all new API calls ───────────────────────
        print("\n\n=== RESPONSES AFTER FALL 2025 CLICK ===")
        for url, body in api_bodies.items():
            ep = url.split("/api/")[-1].split("?")[0]
            if "ProductList" in ep or "Special" in ep or "Product" in ep:
                size = len(json.dumps(body))/1024
                print(f"\n[{size:.1f}KB] {ep}")
                result = (body.get("ResultObject") or body
                          if isinstance(body, dict) else body)
                if isinstance(result, list):
                    print(f"  [{len(result)} items]")
                    if result:
                        print(f"  Keys: {list(result[0].keys()) if isinstance(result[0], dict) else '?'}")
                        print(f"  First: {json.dumps(result[0])[:300]}")
                        # Save large lists
                        if len(result) > 5:
                            fname = f"pfg_{ep.replace('/', '_')}.json"
                            with open(f"{API_DIR}/{fname}", "w") as f:
                                json.dump(result, f, indent=2)
                            print(f"  Saved → {fname}")
                elif isinstance(result, dict):
                    print(f"  Keys: {list(result.keys())[:10]}")
                    print(f"  {json.dumps(result)[:300]}")

        # ── Try direct API calls for list items ────────────
        print("\n\n=== Direct API calls for list items ===")

        # Get the Fall 2025 list ID from ProductListHeaders
        fall_list_id = None
        if list_headers_url:
            items = api_bodies.get(list_headers_url, {})
            items = items.get("ResultObject") or items if isinstance(items, dict) else items
            if isinstance(items, list):
                for item in items:
                    name = (item.get("ProductListName") or item.get("Name") or
                            item.get("ListName") or "").lower()
                    if "fall" in name or "2025" in name:
                        fall_list_id = (item.get("ProductListHeaderId") or
                                        item.get("Id") or item.get("ListId"))
                        print(f"  Found Fall 2025 list: {json.dumps(item)[:200]}")
                        break

        if fall_list_id:
            print(f"\n→ Getting Fall 2025 items (list_id={fall_list_id})...")
            for ep, payload in [
                ("ProductListDetail/V1/GetProductListDetails",
                 {"ProductListHeaderId": fall_list_id, "CustomerId": CUSTOMER_ID}),
                ("ProductListItem/V1/GetProductListItems",
                 {"ProductListHeaderId": fall_list_id, "CustomerId": CUSTOMER_ID}),
                ("ProductList/V1/GetProductListItems",
                 {"ProductListHeaderId": fall_list_id, "CustomerId": CUSTOMER_ID}),
            ]:
                r = call_api(bearer, "POST", ep, payload)
                size = len(json.dumps(r))/1024
                print(f"  {ep.split('/')[-1]}: {size:.1f}KB  err={r.get('_error','ok')}")
                if not r.get("_error"):
                    result = r.get("ResultObject") or r if isinstance(r, dict) else r
                    if isinstance(result, list) and result:
                        print(f"  [{len(result)} items] first: {json.dumps(result[0])[:300]}")
                        fname = f"pfg_fall2025_items_{ep.split('/')[-1]}.json"
                        with open(f"{API_DIR}/{fname}", "w") as f:
                            json.dump(result, f, indent=2)

        # Save final config
        config = {
            "api_base":       API_BASE,
            "customer_id":    CUSTOMER_ID,
            "customer_number": CUSTOMER_NUM,
            "opco_number":    OPCO_NUM,
            "biz_unit_key":   "3",
            "fall_list_id":   fall_list_id,
            "portal_url":     "https://www.customerfirstsolutions.com",
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        print(f"\n✅ Config saved → {CONFIG_FILE}")

        await asyncio.sleep(8)
        await browser.close()

asyncio.run(main())
