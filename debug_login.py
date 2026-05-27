"""Debug the US Foods login modal — inspect iframes and network."""
import asyncio
from playwright.async_api import async_playwright

USER = "onparbarngrill"
PASS = "Onpar4464"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            slow_mo=300,
            args=["--disable-blink-features=AutomationControlled"]
        )
        ctx = await browser.new_context(
            viewport={"width":1280,"height":900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await ctx.new_page()

        # Go directly to the B2C login page (skip the modal)
        b2c_url = (
            "https://usfoodsb2cprod.b2clogin.com/usfoodsb2cprod.onmicrosoft.com"
            "/oauth2/v2.0/authorize?p=b2c_1a_signin_sellersandcustomers"
            "&client_id=bb101b81-7868-40b5-85d9-dbc155ba41d9"
            "&response_type=id_token"
            "&redirect_uri=https://www.usfoods.com/usfdce/login/validation/callback/j_security_check"
            "&scope=openid%20offline_access%20bb101b81-7868-40b5-85d9-dbc155ba41d9"
            "&response_mode=query&state=%252F"
        )
        print("→ Going directly to B2C login...")
        await page.goto(b2c_url, timeout=30000)
        await asyncio.sleep(4)
        await page.screenshot(path="/tmp/uf_b2c.png")
        print(f"  URL: {page.url}")
        print(f"  Title: {await page.title()}")

        # Check for iframes
        frames = page.frames
        print(f"  Frames: {len(frames)}")
        for i, frame in enumerate(frames):
            print(f"    Frame {i}: {frame.url}")

        # Find inputs across all frames
        for i, frame in enumerate(frames):
            try:
                inputs = await frame.eval_on_selector_all(
                    "input",
                    "els => els.map(e => ({type:e.type, name:e.name, id:e.id, placeholder:e.placeholder, visible: e.offsetParent !== null}))"
                )
                if inputs:
                    print(f"  Frame {i} inputs: {inputs}")
            except: pass

        # Try filling User ID in the right frame
        for frame in frames:
            for sel in ["input[placeholder='User ID']", "input[id*='user']", "input[name*='user']", "#signInName"]:
                try:
                    el = frame.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        print(f"  Found UID field in frame: {frame.url[:60]} → {sel}")
                        await el.fill(USER)
                        await page.screenshot(path="/tmp/uf_b2c_filled.png")
                        # Submit
                        await frame.evaluate("""
                            () => {
                                const btns = Array.from(document.querySelectorAll('button'));
                                const btn = btns.find(b => b.offsetParent !== null);
                                if (btn) { console.log('clicking', btn.id, btn.innerText); btn.click(); }
                            }
                        """)
                        await asyncio.sleep(5)
                        await page.screenshot(path="/tmp/uf_b2c_after_uid.png")
                        break
                except: pass

        # Check for password field
        for frame in frames:
            try:
                pw = frame.locator("input[type='password']").first
                if await pw.is_visible(timeout=3000):
                    print("  Found password field!")
                    await pw.fill(PASS)
                    await page.screenshot(path="/tmp/uf_b2c_pw.png")
                    await frame.evaluate("() => { const btn = document.querySelector('button[type=submit]'); if(btn) btn.click(); }")
                    await asyncio.sleep(6)
                    break
            except: pass

        await page.screenshot(path="/tmp/uf_b2c_final.png")
        print(f"  Final URL: {page.url}")
        await asyncio.sleep(3)
        await browser.close()

asyncio.run(main())
