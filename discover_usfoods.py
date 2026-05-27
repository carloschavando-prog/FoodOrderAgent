"""Quick discovery: map US Foods login and order guide structure."""
import asyncio, os
from playwright.async_api import async_playwright

USER = "onparbarngrill"
PASS = "Onpar4464"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=800)
        ctx = await browser.new_context(viewport={"width":1280,"height":900})
        page = await ctx.new_page()

        print("→ Navigating to US Foods...")
        await page.goto("https://www.usfoods.com/", timeout=30000)
        await page.screenshot(path="/tmp/uf_01_home.png")
        print(f"  Title: {await page.title()}")
        print(f"  URL: {page.url}")

        # Look for sign in button
        print("→ Looking for sign-in...")
        for sel in ["text=Sign In", "text=Log In", "text=Login", "[href*='login']", "[href*='signin']"]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    print(f"  Found: {sel}")
                    await el.click()
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    break
            except: pass

        await page.screenshot(path="/tmp/uf_02_login.png")
        print(f"  After click URL: {page.url}")
        print(f"  Title: {await page.title()}")

        # Try to fill credentials
        print("→ Filling credentials...")
        for user_sel in ["input[name='username']","input[name='email']","input[type='email']","#username","#email","#userId"]:
            try:
                el = page.locator(user_sel).first
                if await el.is_visible(timeout=2000):
                    await el.fill(USER)
                    print(f"  Username field: {user_sel}")
                    break
            except: pass

        for pass_sel in ["input[name='password']","input[type='password']","#password"]:
            try:
                el = page.locator(pass_sel).first
                if await el.is_visible(timeout=2000):
                    await el.fill(PASS)
                    print(f"  Password field: {pass_sel}")
                    break
            except: pass

        await page.screenshot(path="/tmp/uf_03_filled.png")

        # Submit
        for submit_sel in ["button[type='submit']","input[type='submit']","text=Sign In","text=Log In","text=Login"]:
            try:
                el = page.locator(submit_sel).first
                if await el.is_visible(timeout=2000):
                    print(f"  Submit: {submit_sel}")
                    await el.click()
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    break
            except: pass

        await page.screenshot(path="/tmp/uf_04_post_login.png")
        print(f"  Post-login URL: {page.url}")
        print(f"  Title: {await page.title()}")

        # Look for order guide / ordering links
        print("→ Scanning for order guide links...")
        links = await page.eval_on_selector_all("a", "els => els.map(e => ({text: e.innerText.trim(), href: e.href}))")
        for l in links:
            if any(kw in l['text'].lower() for kw in ['order guide','ordering','fall','season','price','catalog']):
                print(f"  LINK: {l['text']!r} → {l['href']}")

        print("\n✅ Screenshots saved to /tmp/uf_0*.png")
        await browser.close()

asyncio.run(main())
