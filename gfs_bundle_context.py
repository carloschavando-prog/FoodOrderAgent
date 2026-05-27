"""
Extract surrounding context around the submit/checkout endpoints in the main.js bundle.
"""
import asyncio, json, os, re
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/gfs_session.json")
API_DIR      = os.path.expanduser("~/.FoodOrderAgent/api_captures")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        ctx = await browser.new_context(storage_state=SESSION_FILE)
        page = await ctx.new_page()

        print("→ Fetching main.js bundle…")
        await page.goto("https://order.gfs.com/guides/order-guide", timeout=45000)
        await asyncio.sleep(3)

        main_js_url = await page.evaluate("""
            () => {
                const scripts = Array.from(document.querySelectorAll('script[src]'));
                return scripts.map(s=>s.src).find(s => s.includes('main.') && s.includes('.js'));
            }
        """)
        print(f"main.js: {main_js_url}")

        bundle = await page.evaluate(f"""
            async () => {{
                const r = await fetch({json.dumps(main_js_url)});
                return await r.text();
            }}
        """)
        print(f"Bundle size: {len(bundle)//1024} KB")

        # Search for submit/checkout patterns with context
        search_terms = [
            'v6/cart',
            'v2/cart',
            '/submit',
            '/checkout',
            '/presubmit',
            'creditCheckout',
            'checkoutId',
            'paymentOption',
            'submitOrder',
            'placeOrder',
        ]

        found_contexts = {}
        for term in search_terms:
            positions = []
            start = 0
            while True:
                idx = bundle.find(term, start)
                if idx == -1: break
                positions.append(idx)
                start = idx + 1
                if len(positions) >= 10: break

            if positions:
                contexts = []
                for pos in positions[:5]:
                    lo = max(0, pos - 120)
                    hi = min(len(bundle), pos + 200)
                    snippet = bundle[lo:hi].replace('\n', '↵')
                    contexts.append(snippet)
                found_contexts[term] = contexts
                print(f"\n=== '{term}' ({len(positions)} occurrences) ===")
                for i, ctx in enumerate(contexts):
                    print(f"  [{i+1}] …{ctx}…")

        # Also look for the function that wraps v6/cart submit
        idx = bundle.find('v6/cart/')
        if idx >= 0:
            lo = max(0, idx - 500)
            hi = min(len(bundle), idx + 500)
            print(f"\n=== Full context around v6/cart (±500 chars) ===")
            print(bundle[lo:hi])

        idx2 = bundle.find('v2/cart/')
        if idx2 >= 0:
            lo = max(0, idx2 - 500)
            hi = min(len(bundle), idx2 + 500)
            print(f"\n=== Full context around v2/cart (±500 chars) ===")
            print(bundle[lo:hi])

        # Specifically find how the checkout endpoint is called
        idx3 = bundle.find('creditCheckoutId')
        if idx3 >= 0:
            lo = max(0, idx3 - 300)
            hi = min(len(bundle), idx3 + 400)
            print(f"\n=== Full context around creditCheckoutId (±400 chars) ===")
            print(bundle[lo:hi])

        out = f"{API_DIR}/gfs_bundle_context.json"
        with open(out, "w") as f:
            json.dump(found_contexts, f, indent=2)
        print(f"\n✅ Saved → {out}")

        await browser.close()

asyncio.run(main())
