"""Discover the US Foods ordering portal URL using the saved session."""
import asyncio, json, os
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/usfoods_session.json")

CANDIDATE_URLS = [
    "https://www.usfoods.com/usfdce/",
    "https://www.usfoods.com/usfdce/home",
    "https://www.usfoods.com/usfdce/ordering",
    "https://www.usfoods.com/usfdce/dashboard",
    "https://moxe.usfoods.com/",
    "https://ordering.usfoods.com/",
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=300,
            args=["--disable-blink-features=AutomationControlled"])
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            storage_state=SESSION_FILE,
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
        )
        page = await ctx.new_page()

        # Print saved cookies so we can see what domains are covered
        print("Saved session cookies (domains):")
        with open(SESSION_FILE) as f:
            session = json.load(f)
        domains = sorted({c["domain"] for c in session.get("cookies", [])})
        for d in domains:
            print(f"  {d}")

        # Try each candidate URL
        print("\nTrying candidate URLs...")
        for url in CANDIDATE_URLS:
            try:
                resp = await page.goto(url, timeout=10000)
                await asyncio.sleep(2)
                final = page.url
                title = await page.title()
                status = resp.status if resp else "?"
                not_found = "not found" in title.lower() or "404" in title.lower() or status == 404
                marker = "❌" if not_found else "✅"
                print(f"  {marker} [{status}] {url}")
                print(f"       → {final!r}  title={title!r}")
                if not not_found:
                    await page.screenshot(path=f"/tmp/uf_portal_{url.split('/')[-2] or 'root'}.png")
            except Exception as e:
                print(f"  ⚠️  {url}: {e}")

        # Now look at the logged-in homepage for order-related links
        print("\nNavigating to usfoods.com to find ordering links...")
        await page.goto("https://www.usfoods.com/", timeout=15000)
        await asyncio.sleep(3)
        await page.screenshot(path="/tmp/uf_home_loggedin.png")
        print(f"  URL: {page.url}  title: {await page.title()!r}")

        links = await page.eval_on_selector_all(
            "a",
            "els => els.map(e=>({t:e.innerText.trim().slice(0,60),h:e.href})).filter(l=>l.t&&l.h)"
        )
        print("\n  All links on logged-in homepage:")
        for l in links:
            if any(kw in l["h"].lower() or kw in l["t"].lower()
                   for kw in ["order","catalog","guide","moxe","usfdce","product","pricing"]):
                print(f"    *** {l['t']!r} → {l['h']}")
        print("\n  All nav links:")
        for l in links[:60]:
            print(f"    {l['t']!r} → {l['h']}")

        # Wait so you can look at the browser
        print("\n\nLeaving browser open for 60s — check it manually too...")
        await asyncio.sleep(60)
        await browser.close()

asyncio.run(main())
