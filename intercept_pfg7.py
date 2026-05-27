"""
PFG pass 7 — directly call SearchProductList, save full 292KB response,
analyze price fields, and build the final config for scrape_pfg.py.
"""
import asyncio, json, os, urllib.request, urllib.error
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/pfg_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
CONFIG_FILE  = os.path.expanduser("~/.FoodOrderAgent/pfg_api_config.json")

API_BASE      = "https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api"
CUSTOMER_ID   = "ccbddeae-bc43-4287-a4e0-8d5bee2b913c"
FALL_LIST_ID  = "13e8ce85-8f4e-4cfe-a6dd-cac49a88dc60"
CUSTOMER_NUM  = "03510"
OPCO_NUM      = "795"

def call_api(bearer, method, endpoint, payload=None, params=None):
    url = f"{API_BASE}/{endpoint}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    hdrs = {"Authorization": bearer, "Accept": "application/json", "Content-Type": "application/json"}
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
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
    return (f"Bearer {d['secret']}", d) if d else (None, None)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=100)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900}, storage_state=SESSION_FILE)
        page = await ctx.new_page()

        print("→ Loading portal to get token...")
        await page.goto("https://www.customerfirstsolutions.com/", timeout=30000)
        await asyncio.sleep(5)
        bearer, token_data = await get_token(page)
        print(f"Bearer: {'✅' if bearer else '❌'}")

        # ── Call SearchProductList ─────────────────────────
        print("\n→ Calling SearchProductList for FALL 2025...")
        r = call_api(bearer, "POST", "ProductListSearch/V1/SearchProductList",
            payload={
                "CustomerId":          CUSTOMER_ID,
                "ProductListHeaderId": FALL_LIST_ID,
                "Query":               "",
                "Skip":                0,
                "Take":                500,   # get all items at once
                "SortValue":           5,     # custom category sort
                "FacetFilter":         [],
            })
        size = len(json.dumps(r))/1024
        print(f"  Response: {size:.1f}KB  err={r.get('_error','ok')}")

        if r.get("_error"):
            print(f"  Error: {r}")
            await browser.close()
            return

        # Save raw response
        with open(f"{API_DIR}/pfg_fall2025_raw.json", "w") as f:
            json.dump(r, f, indent=2)
        print(f"  Saved → {API_DIR}/pfg_fall2025_raw.json")

        # ── Analyze structure ──────────────────────────────
        print(f"\n  Top-level keys: {list(r.keys())}")
        print(f"  TotalCount: {r.get('TotalCount')}")
        print(f"  HasLoadMore: {r.get('HasLoadMore')}")
        categories = r.get("ProductListCategories", [])
        print(f"  Categories: {len(categories)}")

        all_products = []
        for cat in categories:
            cat_name = cat.get("CategoryTitle", "Unknown")
            products = cat.get("Products", [])
            print(f"\n  Category: {cat_name!r} ({len(products)} products)")
            for prod in products[:2]:
                print(f"    {json.dumps(prod)[:300]}")
            all_products.extend(products)

        print(f"\n  Total products: {len(all_products)}")

        # Find price fields
        if all_products:
            p0 = all_products[0]
            print(f"\n  Product keys: {list(p0.keys())}")
            print(f"\n  Full first product:\n{json.dumps(p0, indent=2)}")

            # Look for price-related keys
            price_keys = [k for k in p0.keys()
                          if any(w in k.lower() for w in ["price", "cost", "amount", "unit", "case"])]
            print(f"\n  Price-related keys: {price_keys}")

        # ── If Take=500 didn't get all, paginate ─────────
        total = r.get("TotalCount", 0)
        skip = r.get("Skip", 0)
        print(f"\n  skip={skip}, total={total}, got={len(all_products)}")

        if total > len(all_products):
            print(f"  → Need to paginate (got {len(all_products)}/{total})")
            while len(all_products) < total:
                next_skip = r.get("Skip", len(all_products))
                r2 = call_api(bearer, "POST", "ProductListSearch/V1/SearchProductList",
                    payload={
                        "CustomerId":          CUSTOMER_ID,
                        "ProductListHeaderId": FALL_LIST_ID,
                        "Query":               "",
                        "Skip":                next_skip,
                        "Take":                500,
                        "SortValue":           5,
                        "FacetFilter":         [],
                    })
                more = []
                for cat in r2.get("ProductListCategories", []):
                    more.extend(cat.get("Products", []))
                all_products.extend(more)
                print(f"  Page: got {len(more)} more, total so far: {len(all_products)}")
                if not more or not r2.get("HasLoadMore"):
                    break
                r = r2

        print(f"\n✅ Total products collected: {len(all_products)}")
        with open(f"{API_DIR}/pfg_fall2025_all_products.json", "w") as f:
            json.dump(all_products, f, indent=2)
        print(f"  Saved → {API_DIR}/pfg_fall2025_all_products.json")

        # ── Get/save refresh token from MSAL storage ───────
        refresh_token_data = await page.evaluate("""
            () => {
                for (let i = 0; i < sessionStorage.length; i++) {
                    const k = sessionStorage.key(i);
                    if (k.includes('refreshtoken') && k.includes('pfgcustomerfirst')) {
                        try { return JSON.parse(sessionStorage.getItem(k)); }
                        catch {}
                    }
                }
                return null;
            }
        """)

        # Build final config
        config = {
            "api_base":       API_BASE,
            "customer_id":    CUSTOMER_ID,
            "customer_number": CUSTOMER_NUM,
            "opco_number":    OPCO_NUM,
            "biz_unit_key":   "3",
            "fall_list_id":   FALL_LIST_ID,
            "portal_url":     "https://www.customerfirstsolutions.com",
            "b2c_tenant":     "pfgcustomerfirst.b2clogin.com",
            "b2c_policy":     "b2c_1a_signup_signin",
            "b2c_client_id":  "c68e7fae-80a1-42db-bd89-3fb37d1224a2",
            "access_token":   token_data.get("secret", "") if token_data else "",
            "refresh_token":  refresh_token_data.get("secret", "") if refresh_token_data else "",
            "refresh_token_home_account_id": (
                refresh_token_data.get("homeAccountId", "") if refresh_token_data else ""),
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        print(f"\n✅ Config saved → {CONFIG_FILE}")

        await asyncio.sleep(5)
        await browser.close()

asyncio.run(main())
