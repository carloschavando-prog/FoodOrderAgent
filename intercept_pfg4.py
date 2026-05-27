"""
PFG pass 4 — load /lists and /lists/{id} with full API interception.
Finds the right endpoint for seasonal price list items + pricing.
"""
import asyncio, json, os, urllib.request, urllib.error
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/pfg_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")

API_BASE     = "https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api"
CUSTOMER_ID  = "ccbddeae-bc43-4287-a4e0-8d5bee2b913c"
CUSTOMER_NUM = "03510"
OPCO_NUM     = "795"

api_calls  = []   # all azurewebsites API calls seen
api_bodies = {}   # url -> response body

def _is_our_api(url):
    return "azurewebsites.net/api" in url

async def handle_request(request):
    if not _is_our_api(request.url):
        return
    try:
        post = request.post_data
    except Exception:
        post = None
    hdrs = dict(await request.all_headers())
    api_calls.append({
        "method": request.method,
        "url":    request.url,
        "auth":   hdrs.get("authorization","")[:80],
        "body":   post,
    })
    short = request.url.split("/api/")[-1][:70]
    body_preview = f"  body={post[:100]}" if post else ""
    print(f"  REQ {request.method:6} {short}{body_preview}")

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

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome", headless=False, slow_mo=150)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=SESSION_FILE)
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        # ── 1. Load /lists page ─────────────────────────────
        print("→ Loading /lists page...")
        await page.goto("https://www.customerfirstsolutions.com/lists", timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=20000)
        await asyncio.sleep(4)

        print("\n--- Page text ---")
        text = await page.evaluate("() => document.body.innerText")
        print(text[:3000])

        # Screenshot
        await page.screenshot(path=f"{API_DIR}/pfg_lists.png")

        # ── 2. Find list items on page ──────────────────────
        print("\n→ Finding list items on the page...")
        list_items = await page.evaluate("""
            () => {
                const items = [];
                // Look for any row/card that has text
                const sels = ['tr', 'li', '[class*="row"]', '[class*="card"]', '[class*="list-item"]', '[class*="ListItem"]'];
                for (const sel of sels) {
                    const els = document.querySelectorAll(sel);
                    if (els.length > 0 && els.length < 50) {
                        Array.from(els).forEach(el => {
                            const t = el.innerText?.trim();
                            if (t && t.length > 3 && t.length < 200) {
                                items.push({sel, text: t, class: el.className?.slice(0,50)});
                            }
                        });
                        break;
                    }
                }
                return items.slice(0, 20);
            }
        """)
        for item in list_items:
            print(f"  {item['text']!r:.80}")

        # ── 3. Click the first list item ────────────────────
        print("\n→ Clicking first list/row item...")
        clicked_url = None
        for sel in [
            "table tbody tr:first-child",
            "[class*='list-item']:first-child",
            "[class*='ListItem']:first-child",
            "tr:has(td)",
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=3000):
                    await el.click(timeout=5000)
                    await asyncio.sleep(5)
                    clicked_url = page.url
                    print(f"  ✅ Clicked → {clicked_url}")
                    text2 = await page.evaluate("() => document.body.innerText.slice(0,2000)")
                    print(f"  Page text:\n{text2[:1000]}")
                    break
            except Exception:
                pass

        await asyncio.sleep(6)

        # ── 4. Summary of all API calls ─────────────────────
        print(f"\n\n=== TOTAL API CALLS: {len(api_calls)} ===")
        seen = set()
        for call in api_calls:
            ep = call["url"].split("/api/")[-1].split("?")[0]
            if ep not in seen:
                seen.add(ep)
                print(f"\n  {call['method']} {ep}")
                if call["body"]:
                    try:
                        print(f"    body: {json.dumps(json.loads(call['body']), indent=0)[:200]}")
                    except Exception:
                        print(f"    body: {call['body'][:200]}")

        # ── 5. Analyze responses ────────────────────────────
        print(f"\n=== RESPONSE CONTENTS ===")
        for url, body in api_bodies.items():
            ep = url.split("/api/")[-1].split("?")[0]
            size = len(json.dumps(body))/1024
            print(f"\n[{size:.1f}KB] {ep}")
            if isinstance(body, dict):
                result = body.get("ResultObject") or body.get("result") or body.get("data") or body
                if isinstance(result, list):
                    print(f"  → List of {len(result)} items")
                    if result:
                        print(f"  First item keys: {list(result[0].keys()) if isinstance(result[0], dict) else result[0]}")
                        print(f"  First item: {json.dumps(result[0])[:300]}")
                elif isinstance(result, dict):
                    print(f"  Keys: {list(result.keys())[:10]}")
                    print(f"  Sample: {json.dumps(result)[:300]}")
            elif isinstance(body, list):
                print(f"  → {len(body)} items")
                if body: print(f"  First: {json.dumps(body[0])[:200]}")

        # Save
        out = {"api_calls": api_calls, "responses": api_bodies}
        with open(f"{API_DIR}/pfg_lists_capture.json", "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\nSaved → {API_DIR}/pfg_lists_capture.json")

        await asyncio.sleep(8)
        await browser.close()

asyncio.run(main())
