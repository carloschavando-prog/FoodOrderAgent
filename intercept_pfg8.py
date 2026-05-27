"""
PFG pass 8 — navigate to New Order entry to capture the pricing endpoint.
The order entry view shows prices; we capture those API calls.
"""
import asyncio, json, os
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/pfg_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
CONFIG_FILE  = os.path.expanduser("~/.FoodOrderAgent/pfg_api_config.json")

CUSTOMER_ID  = "ccbddeae-bc43-4287-a4e0-8d5bee2b913c"
FALL_LIST_ID = "13e8ce85-8f4e-4cfe-a6dd-cac49a88dc60"

api_bodies = {}

def _is_our_api(url):
    return "azurewebsites.net/api" in url

async def handle_request(request):
    if not _is_our_api(request.url): return
    try: post = request.post_data
    except Exception: post = None
    short = request.url.split("/api/")[-1][:80]
    if post and len(post) < 300: print(f"  REQ {request.method:6} {short}  b={post[:150]}")
    else: print(f"  REQ {request.method:6} {short}")

async def handle_response(response):
    if not _is_our_api(response.url): return
    try:
        body = await response.json()
        api_bodies[response.url] = body
        size = len(json.dumps(body))/1024
        print(f"  RSP {response.url.split('/api/')[-1][:80]} ({size:.1f}KB)")
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
    return f"Bearer {d['secret']}" if d else None

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=200)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900}, storage_state=SESSION_FILE)
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        print("→ Loading portal...")
        await page.goto("https://www.customerfirstsolutions.com/", timeout=30000)
        await asyncio.sleep(4)

        # Click "New order" button on dashboard
        print("\n→ Clicking 'New order'...")
        try:
            new_order = page.get_by_text("New order", exact=True).first
            await new_order.click(timeout=5000)
            await asyncio.sleep(5)
            print(f"URL: {page.url}")
        except Exception as e:
            print(f"  Could not click: {e}")
            # Try via JS
            await page.evaluate("""
                () => {
                    const els = Array.from(document.querySelectorAll('a, button, span'));
                    const el = els.find(e => e.textContent.trim().toLowerCase().includes('new order'));
                    if (el) el.click();
                }
            """)
            await asyncio.sleep(5)
            print(f"URL: {page.url}")

        # Screenshot
        await page.screenshot(path=f"{API_DIR}/pfg_new_order.png")
        text = await page.evaluate("() => document.body.innerText.slice(0,2000)")
        print(f"Page text:\n{text[:1500]}")

        await asyncio.sleep(4)

        # Navigate to Fall 2025 list in order context
        print("\n→ Looking for Fall 2025 in order context...")
        fall_el = None
        for sel in ["text=FALL 2025", "text=Fall 2025", "[class*='list']:has-text('2025')"]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    fall_el = el
                    break
            except Exception:
                pass

        if fall_el:
            print(f"  Found FALL 2025 element, clicking...")
            await fall_el.click()
            await asyncio.sleep(6)
            print(f"URL: {page.url}")
            text = await page.evaluate("() => document.body.innerText.slice(0,3000)")
            print(f"Page text:\n{text[:2000]}")
        else:
            print("  FALL 2025 not visible in current view")

        await asyncio.sleep(6)

        # ── Analyze price-related responses ────────────────
        print("\n\n=== PRICE-RELATED RESPONSES ===")
        for url, body in api_bodies.items():
            ep = url.split("/api/")[-1].split("?")[0]
            if any(w in ep.lower() for w in ["search", "product", "order", "price", "detail"]):
                size = len(json.dumps(body))/1024
                if size > 1:
                    result = body.get("ResultObject") or body
                    cats = result.get("ProductListCategories", []) if isinstance(result, dict) else []
                    prods = [p for cat in cats for p in cat.get("Products", [])]
                    print(f"\n[{size:.1f}KB] {ep} → {len(prods)} products")
                    if prods:
                        uom = prods[0]["Product"]["UnitOfMeasureOrderQuantities"][0]
                        print(f"  First product price: {uom.get('Price')} | ViewedPrice: {uom.get('ViewedPrice')}")
                        # Check ALL price-related fields
                        price_fields = {k: v for k, v in uom.items()
                                        if any(w in k.lower() for w in ["price", "cost", "amount"])}
                        print(f"  Price fields: {price_fields}")
                    with open(f"{API_DIR}/pfg_order_search_{ep.replace('/', '_')}.json", "w") as f:
                        json.dump(body, f, indent=2)

        # Look for any endpoint that returned data with "Price" != 0
        print("\n=== SCANNING ALL RESPONSES FOR PRICES ===")
        for url, body in api_bodies.items():
            body_str = json.dumps(body)
            if '"Price":' in body_str and '"Price": 0' not in body_str.replace('"Price": 0.0', 'z'):
                ep = url.split("/api/")[-1][:60]
                # Count non-zero prices
                import re
                prices = [float(x) for x in re.findall(r'"Price":\s*([\d.]+)', body_str) if float(x) > 0]
                if prices:
                    print(f"  {ep}: {len(prices)} non-zero prices, e.g. {prices[:3]}")
                    with open(f"{API_DIR}/pfg_with_prices_{ep.replace('/', '_')}.json", "w") as f:
                        json.dump(body, f, indent=2)

        # Save all API response keys for analysis
        all_eps = sorted(url.split("/api/")[-1].split("?")[0] for url in api_bodies)
        print(f"\n=== ALL ENDPOINTS ({len(all_eps)}) ===")
        for ep in all_eps:
            print(f"  {ep}")

        await asyncio.sleep(8)
        await browser.close()

asyncio.run(main())
