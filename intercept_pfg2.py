"""
Second-pass PFG intercept — captures ALL API calls (any domain).
Uses saved session so no login needed.
Navigates to Lists → finds seasonal/price list → captures full API traffic.
"""
import asyncio, json, os, re
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/pfg_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")
CONFIG_FILE  = os.path.expanduser("~/.FoodOrderAgent/pfg_api_config.json")

all_requests  = []
all_responses = {}

def _is_api(url):
    skip = ["google-analytics", "googletagmanager", "fonts.googleapis",
            "doubleclick", "facebook", ".css", ".woff", ".png", ".jpg",
            ".gif", ".ico", "chunk.js", "main.js", "browser-check",
            "google.com/g/collect"]
    return not any(s in url for s in skip)

async def handle_request(request):
    url = request.url
    if not _is_api(url):
        return
    try:
        post = request.post_data
    except Exception:
        post = None
    hdrs = dict(await request.all_headers())
    rec = {
        "method": request.method,
        "url":    url,
        "auth":   hdrs.get("authorization", "")[:60],
        "body":   post[:200] if post else None,
    }
    all_requests.append(rec)
    # Only print non-asset calls
    if not any(x in url for x in [".js", ".css", "b2clogin", "favicon"]):
        print(f"  REQ {request.method:6} {url[:100]}")
        if post:
            print(f"            body: {post[:120]}")

async def handle_response(response):
    url = response.url
    if not _is_api(url):
        return
    if any(x in url for x in [".js", ".css", "b2clogin", "favicon"]):
        return
    ct = response.headers.get("content-type", "")
    if "json" not in ct and "text" not in ct:
        return
    try:
        body = await response.json()
        all_responses[url] = body
        size = len(json.dumps(body)) / 1024
        print(f"  RSP {url[:100]} ({size:.1f} KB)")
    except Exception:
        pass

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            channel="chrome", headless=False, slow_mo=200)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=SESSION_FILE)
        page = await ctx.new_page()
        page.on("request",  lambda r: asyncio.ensure_future(handle_request(r)))
        page.on("response", lambda r: asyncio.ensure_future(handle_response(r)))

        # ── Dashboard ──────────────────────────────────────
        print("→ Loading dashboard...")
        await page.goto("https://www.customerfirstsolutions.com/", timeout=30000)
        await asyncio.sleep(4)
        print(f"  URL: {page.url}")

        # ── Navigate to Lists ──────────────────────────────
        print("\n→ Clicking Lists...")
        lists_link = page.get_by_role("link", name=re.compile(r"^Lists$", re.I)).first
        await lists_link.click()
        await page.wait_for_load_state("networkidle", timeout=15000)
        await asyncio.sleep(4)
        print(f"  URL: {page.url}")

        # Dump visible list names
        page_text = await page.evaluate("() => document.body.innerText.slice(0,3000)")
        print("\n--- Lists page text ---")
        print(page_text)

        # Screenshot
        shot = os.path.join(API_DIR, "pfg_lists_screenshot.png")
        await page.screenshot(path=shot)
        print(f"\n  Screenshot: {shot}")

        # Look for seasonal/special order list
        print("\n→ Looking for seasonal / special order list...")
        all_text = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a, button, td, li, div[class*="list"], div[class*="item"]'))
                .map(el => ({text: el.innerText.trim(), tag: el.tagName, href: el.href||''}))
                .filter(el => el.text.length > 2 && el.text.length < 100)
                .slice(0, 60)
        """)
        for el in all_text:
            if any(k in el['text'].lower() for k in ['season', 'special', 'fall', 'spring', 'summer', 'winter', 'price', 'promo']):
                print(f"  [{el['tag']}] {el['text']!r}  href={el['href'][:60]}")

        # Try clicking first list item to see what loads
        print("\n→ Clicking first list item...")
        try:
            first_row = page.locator("table tbody tr, .list-item, [class*='list-row']").first
            await first_row.click(timeout=5000)
            await asyncio.sleep(4)
            print(f"  URL after click: {page.url}")
        except Exception as e:
            print(f"  Could not click first row: {e}")

        await asyncio.sleep(6)

        # ── Summary ────────────────────────────────────────
        print(f"\n\n=== CAPTURED {len(all_responses)} JSON responses ===")
        for url, body in list(all_responses.items())[:20]:
            size = len(json.dumps(body))/1024
            print(f"  {url[:90]} ({size:.1f} KB)")
            # Peek at structure
            if isinstance(body, dict):
                print(f"    keys: {list(body.keys())[:6]}")
            elif isinstance(body, list) and body:
                print(f"    [{len(body)} items] first: {json.dumps(body[0])[:100]}")

        print(f"\n=== ALL UNIQUE API DOMAINS ===")
        domains = set()
        for r in all_requests:
            from urllib.parse import urlparse
            domains.add(urlparse(r["url"]).netloc)
        for d in sorted(domains):
            print(f"  {d}")

        # Save full capture
        out = {
            "requests":  all_requests,
            "responses": {k: v for k, v in all_responses.items()},
        }
        with open(os.path.join(API_DIR, "pfg_capture2.json"), "w") as f:
            json.dump(out, f, indent=2, default=str)
        print(f"\nSaved → {API_DIR}/pfg_capture2.json")

        await asyncio.sleep(10)
        await browser.close()

asyncio.run(main())
