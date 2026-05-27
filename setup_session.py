"""
One-time session setup for US Foods ordering portal (order.usfoods.com).

Run this script once. A browser window will open on the MOXe ordering
portal. Click "Log In" and complete the login with your US Foods credentials.
Once you land on your dashboard, the script saves your session automatically.

All future scraper runs are fully automatic until the session expires
(usually several weeks).

Usage:
    python3 setup_session.py
"""
import asyncio, json, os
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/usfoods_session.json")

# Selectors that only appear when logged in to the ordering portal
LOGGED_IN_INDICATORS = [
    "[class*='account']",
    "[class*='Account']",
    "text=My Lists",
    "text=My Orders",
    "text=Order Guide",
    "[href*='/desktop/lists']",
    "[href*='/desktop/profile']",
    "[href*='/desktop/home']",
]

async def is_logged_in(page):
    """Return True if any logged-in-only element is visible."""
    url = page.url
    # If we're at a specific account page, definitely logged in
    if any(p in url for p in ["/desktop/lists", "/desktop/profile",
                                "/desktop/my-orders", "/desktop/home",
                                "/desktop/order"]):
        return True
    # Check for nav elements only present when authenticated
    for sel in LOGGED_IN_INDICATORS:
        try:
            if await page.locator(sel).first.is_visible(timeout=500):
                return True
        except Exception:
            pass
    # If header shows "Log In" link, definitely NOT logged in
    try:
        if await page.locator("text=Log In, text=LOG IN").first.is_visible(timeout=500):
            return False
    except Exception:
        pass
    return False

async def main():
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)

    async with async_playwright() as p:
        # Use the real Chrome installation — avoids bot detection fingerprinting
        browser = await p.chromium.launch(
            channel="chrome",          # launches /Applications/Google Chrome.app
            headless=False,
            slow_mo=0,
        )
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 900},
        )
        page = await ctx.new_page()

        print("\n" + "="*60)
        print("Opening the US Foods ordering portal (order.usfoods.com).")
        print()
        print("ACTION REQUIRED:")
        print("  1. Click the 'Log In' button in the browser window")
        print("  2. Enter your US Foods credentials")
        print("  3. Complete login — the script saves your session automatically")
        print("="*60 + "\n")

        await page.goto("https://order.usfoods.com/", timeout=30000)
        await asyncio.sleep(3)

        # Poll until logged in
        print("Waiting for you to log in", end="", flush=True)
        for tick in range(300):  # up to 10 minutes
            await asyncio.sleep(2)
            if await is_logged_in(page):
                print(f"\n  ✅ Authenticated! URL: {page.url}")
                break
            if tick % 5 == 4:
                print(f"\n  ({tick+1}s — URL: {page.url[:70]})", end="", flush=True)
            else:
                print(".", end="", flush=True)
        else:
            print("\n  ⚠️  Timed out (10 min). Try running setup_session.py again.")
            await browser.close()
            return

        # Give the portal a moment to finish setting cookies / localStorage
        await asyncio.sleep(3)

        # Navigate to the order lists page so those specific cookies are set
        print("  Navigating to order guide lists page...")
        await page.goto("https://order.usfoods.com/desktop/lists", timeout=15000)
        await asyncio.sleep(3)
        print(f"  Lists URL: {page.url}")

        # Save the full storage state
        state = await ctx.storage_state(path=SESSION_FILE)
        n_cookies = len(state.get("cookies", []))
        domains = sorted({c["domain"] for c in state.get("cookies", [])})
        ls_keys  = sum(len(o.get("origins", [])) for o in [state])

        print(f"\n✅ Session saved → {SESSION_FILE}")
        print(f"   Cookies: {n_cookies} across {len(domains)} domains")
        print(f"   Domains: {', '.join(domains)}")
        print("\nThe scraper will now run automatically using this session.")
        print("Re-run this script if you see login errors (session expired).")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
