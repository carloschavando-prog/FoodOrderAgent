"""
PFG pass 3 — extract MSAL token, then call the API directly to find the price list.

We know:
  API base: https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api/
  CustomerId: ccbddeae-bc43-4287-a4e0-8d5bee2b913c
  CustomerNumber: 03510
  OperationCompanyNumber: 795
"""
import asyncio, json, os, urllib.request, urllib.error
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/pfg_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
CONFIG_FILE  = os.path.expanduser("~/.FoodOrderAgent/pfg_api_config.json")

API_BASE    = "https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api"
CUSTOMER_ID = "ccbddeae-bc43-4287-a4e0-8d5bee2b913c"
CUSTOMER_NUM = "03510"
OPCO_NUM    = "795"

def api_get(path, bearer, params=None):
    url = f"{API_BASE}/{path}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={
        "Authorization": bearer,
        "Accept": "application/json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()[:200]}

def api_post(path, bearer, payload):
    url = f"{API_BASE}/{path}"
    req = urllib.request.Request(url, method="POST",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": bearer,
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": e.code, "body": e.read().decode()[:200]}

async def get_fresh_token(page):
    """Extract the MSAL access token from sessionStorage."""
    token_data = await page.evaluate("""
        () => {
            for (let i = 0; i < sessionStorage.length; i++) {
                const k = sessionStorage.key(i);
                if (k.includes('accesstoken') && k.includes('pfgcustomerfirst')) {
                    try {
                        const v = JSON.parse(sessionStorage.getItem(k));
                        if (v.secret) return v;
                    } catch {}
                }
            }
            return null;
        }
    """)
    if token_data:
        return f"Bearer {token_data['secret']}"

    # Fallback: look for any Bearer token in cookies/headers by making a request
    return None

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome", headless=False, slow_mo=100)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=SESSION_FILE)
        page = await ctx.new_page()

        # Load portal to activate MSAL token refresh
        print("→ Loading portal to activate session...")
        await page.goto("https://www.customerfirstsolutions.com/", timeout=30000)
        await asyncio.sleep(5)

        # Extract token
        bearer = await get_fresh_token(page)
        if not bearer:
            print("❌ Could not get token from sessionStorage")
            await browser.close()
            return
        print(f"✅ Got Bearer token ({len(bearer)} chars)")

        # ── Explore API endpoints ───────────────────────────
        results = {}

        # 1. Get all lists
        print("\n→ GET List endpoints...")
        for ep, payload in [
            ("List/V1/GetLists",             {"CustomerId": CUSTOMER_ID}),
            ("List/V1/GetCustomerLists",     {"CustomerId": CUSTOMER_ID}),
            ("ProductList/V1/GetProductLists", {"CustomerId": CUSTOMER_ID}),
            ("SpecialOrder/V1/GetSpecialOrders", {"CustomerId": CUSTOMER_ID}),
            ("CustomerSpecialOrder/V1/GetCustomerSpecialOrders",
             {"CustomerNumber": CUSTOMER_NUM, "OperationCompanyNumber": OPCO_NUM, "CustomerId": CUSTOMER_ID}),
        ]:
            r = api_post(ep, bearer, payload)
            size = len(json.dumps(r))/1024
            print(f"  {ep}: {size:.1f}KB  err={r.get('error','ok')}")
            if not r.get("error"):
                results[ep] = r
                if isinstance(r, list) and r:
                    print(f"    [{len(r)} items] first: {json.dumps(r[0])[:200]}")
                elif isinstance(r, dict):
                    print(f"    keys: {list(r.keys())[:8]}")

        # 2. Navigate to the Lists page in the browser and capture what fires
        print("\n→ Navigating to Lists page via URL patterns...")
        for url_path in [
            "/lists",
            "/order-guide",
            "/product-list",
            "/special-orders",
        ]:
            try:
                await page.goto(f"https://www.customerfirstsolutions.com{url_path}", timeout=10000)
                await asyncio.sleep(3)
                current = page.url
                if "customerfirstsolutions.com" in current and url_path in current:
                    print(f"  ✅ {url_path} exists → {current}")
                    text = await page.evaluate("() => document.body.innerText.slice(0,1000)")
                    print(f"     {text[:300]}")
                    break
                else:
                    print(f"  ↩ {url_path} → redirected to {current}")
            except Exception as e:
                print(f"  ❌ {url_path}: {e}")

        # 3. Try capturing what the Lists link actually does
        print("\n→ Going back to home and finding nav structure...")
        await page.goto("https://www.customerfirstsolutions.com/", timeout=30000)
        await asyncio.sleep(3)

        nav_items = await page.evaluate("""
            () => {
                const items = [];
                // Check all nav/header links
                document.querySelectorAll('a, [role="menuitem"], nav *, header *').forEach(el => {
                    const text = el.innerText?.trim();
                    const href = el.href || el.getAttribute('data-href') || '';
                    const onclick = el.getAttribute('onclick') || '';
                    if (text && text.length < 30 && text.length > 1) {
                        items.push({text, tag: el.tagName, href, class: el.className?.slice(0,60)});
                    }
                });
                return [...new Map(items.map(i=>[i.text,i])).values()].slice(0,40);
            }
        """)
        print("Nav items:")
        for item in nav_items:
            print(f"  [{item['tag']}] {item['text']!r}  href={item['href'][:50]}  class={item['class'][:40]}")

        # Click Lists by text
        print("\n→ Clicking nav item 'Lists'...")
        clicked = False
        for sel in [
            "text=Lists",
            "[href*='list']",
            "a:has-text('Lists')",
            "a:has-text('List')",
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    href = await el.get_attribute("href")
                    print(f"  Found Lists link via {sel!r}: href={href}")
                    await el.click()
                    await asyncio.sleep(4)
                    print(f"  URL: {page.url}")
                    text = await page.evaluate("() => document.body.innerText.slice(0,2000)")
                    print(f"  Page text:\n{text[:800]}")
                    clicked = True
                    break
            except Exception:
                pass

        if not clicked:
            print("  ⚠️  Could not find Lists nav item")

        # ── Try the network capture after clicking Lists ───
        await asyncio.sleep(5)

        # 4. Check for list/product endpoints in network log
        print("\n→ Checking captured network requests via page intercept...")
        network_reqs = await page.evaluate("""
            () => window.__pfgApiCalls || []
        """)

        # Save config
        config = {
            "api_base":          API_BASE,
            "customer_id":       CUSTOMER_ID,
            "customer_number":   CUSTOMER_NUM,
            "opco_number":       OPCO_NUM,
            "portal_url":        "https://www.customerfirstsolutions.com",
            "token_session_key": "MSAL_accesstoken_pfgcustomerfirst",
            "api_results":       {k: (list(v)[:3] if isinstance(v, list) else v)
                                  for k, v in results.items()},
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2, default=str)
        print(f"\n✅ Config saved → {CONFIG_FILE}")

        # Save all results
        with open(os.path.join(API_DIR, "pfg_api_results.json"), "w") as f:
            json.dump(results, f, indent=2, default=str)

        await asyncio.sleep(8)
        await browser.close()

asyncio.run(main())
