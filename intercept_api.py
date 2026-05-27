"""
Capture all three API calls needed for the Fall 2025 price scrape:
  1. list-domain-api/v1/listItems  → product numbers
  2. price-domain-api/v1/pricing   → prices per product number
  3. product-domain-api/v2/products → names per product number

Also captures exact request details (method, headers, body) so we can
replicate the calls in scrape_usfoods.py without a browser.
"""
import asyncio, json, os
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/usfoods_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
os.makedirs(API_DIR, exist_ok=True)

FALL_2025_LIST_ID = 1000643297

# We want to intercept these specific paths
TARGET_PATHS = {
    "list-domain-api/v1/listItems",
    "price-domain-api/v1/pricing",
    "product-domain-api/v2/products",
    "auth-api/v1/oauth/token",
    "user-domain-api/v1/identity",
    "customer-domain-api/v3/customers",
}

req_details  = {}   # path -> {method, url, headers, post_data}
resp_data    = {}   # path -> body (last one wins if multiple calls)
bearer_token = [None]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=200)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=SESSION_FILE,
        )
        page = await ctx.new_page()

        # Intercept REQUESTS for headers / body
        async def handle_request(request):
            url = request.url
            if "panamax-api.ama.usfoods" not in url:
                return
            path = url.split("panamax-api.ama.usfoods.com/")[-1].split("?")[0]

            if bearer_token[0] is None:
                hdrs = await request.all_headers()
                auth = hdrs.get("authorization", "")
                if auth.startswith("Bearer "):
                    bearer_token[0] = auth
                    print(f"  🔑 Bearer token: {auth[:30]}...")

            if any(t in path for t in TARGET_PATHS):
                try:
                    post_data = request.post_data
                except Exception:
                    post_data = None
                hdrs = dict(await request.all_headers())
                req_details[path] = {
                    "method":    request.method,
                    "url":       url,
                    "headers":   {k: v for k, v in hdrs.items()
                                  if not k.startswith(":")},  # capture all non-pseudo headers
                    "post_data": post_data,
                }
                print(f"  REQ {request.method} {path}")
                if post_data:
                    print(f"       body: {post_data[:200]}")

        # Intercept RESPONSES for data
        async def handle_response(response):
            url = response.url
            if "panamax-api.ama.usfoods" not in url:
                return
            path = url.split("panamax-api.ama.usfoods.com/")[-1].split("?")[0]
            if not any(t in path for t in TARGET_PATHS):
                return
            try:
                body = await response.json()
                resp_data[path] = body
                size_kb = len(json.dumps(body)) / 1024
                print(f"  RSP {path} ({size_kb:.1f} KB)")
            except Exception:
                pass

        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        print("→ Loading portal...")
        await page.goto("https://order.usfoods.com/desktop/lists", timeout=30000)
        await asyncio.sleep(5)

        print("→ Opening Fall 2025 list...")
        fall = page.get_by_text("Fall 2025", exact=True).first
        await fall.click()
        await page.wait_for_load_state("networkidle", timeout=20000)
        await asyncio.sleep(8)

        print(f"\n\nCaptured {len(resp_data)} target responses")

        # Save everything
        all_data = {
            "bearer_token": bearer_token[0],
            "request_details": req_details,
            "responses": {k: v for k, v in resp_data.items()},
        }
        with open(os.path.join(API_DIR, "_capture_summary.json"), "w") as f:
            json.dump(all_data, f, indent=2, default=str)

        # Show key info for replication
        print("\n--- Request details for API replication ---")
        for path, details in req_details.items():
            print(f"\nPATH: {path}")
            print(f"  Method: {details['method']}")
            print(f"  URL: {details['url'][:120]}")
            print(f"  Headers: {json.dumps(details['headers'])[:200]}")
            if details.get('post_data'):
                print(f"  Body: {details['post_data'][:300]}")

        # Analyze listItems for Fall 2025
        list_items_resp = resp_data.get("list-domain-api/v1/listItems")
        if list_items_resp:
            all_items = list_items_resp if isinstance(list_items_resp, list) \
                        else list_items_resp.get("items", [])
            fall_items = [i for i in all_items
                          if i.get("listKey", {}).get("listId") == FALL_2025_LIST_ID]
            fall_pnums = [str(i["productNumber"]) for i in fall_items]
            print(f"\n✅ Fall 2025 has {len(fall_items)} items")
            print(f"   Product numbers: {fall_pnums[:10]}...")

        # Analyze pricing
        pricing_resp = resp_data.get("price-domain-api/v1/pricing", {})
        detail = pricing_resp.get("messageDetail", {})
        prod_list = detail.get("productList", []) if isinstance(detail, dict) else []
        print(f"\nPricing records captured: {len(prod_list)}")
        if prod_list:
            print(f"Sample: {json.dumps(prod_list[0])}")

        if bearer_token[0]:
            with open(os.path.expanduser("~/.FoodOrderAgent/bearer_token.txt"), "w") as f:
                f.write(bearer_token[0])
            print(f"\n✅ Bearer token saved")

        # Extract token refresh details and write config for scrape_usfoods.py
        tok_req = req_details.get("auth-api/v1/oauth/token", {})
        tok_hdrs = tok_req.get("headers", {})
        consumer_id = tok_hdrs.get("consumer-id", "")
        tok_body = json.loads(tok_req.get("post_data") or "{}")

        tok_resp = resp_data.get("auth-api/v1/oauth/token", {})
        new_refresh_token = tok_resp.get("refreshToken", "")

        config_path = os.path.expanduser("~/.FoodOrderAgent/usf_api_config.json")
        existing = {}
        if os.path.exists(config_path):
            with open(config_path) as f:
                existing = json.load(f)

        config = {
            **existing,
            "refresh_token": new_refresh_token or tok_body.get("refreshToken", existing.get("refresh_token", "")),
            "auth_context":  tok_body.get("authContext", existing.get("auth_context", {})),
            "scopes":        tok_body.get("scopes", existing.get("scopes", "")),
            "platform":      tok_body.get("platform", existing.get("platform", "DESKTOP")),
            "consumer_id":   consumer_id or existing.get("consumer_id", ""),
            "fall_2025_list_id": FALL_2025_LIST_ID,
        }
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        print(f"\n✅ Config saved → {config_path}")
        print(f"   consumer-id: {consumer_id!r}")
        print(f"   refresh_token: {config['refresh_token'][:20]}...")

        await asyncio.sleep(10)
        await browser.close()

asyncio.run(main())
