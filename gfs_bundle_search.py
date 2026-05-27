"""
Fetch GFS main JavaScript bundle(s) and grep for order submission endpoint patterns.
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

        print("→ Loading app to discover JS bundle URLs…")
        await page.goto("https://order.gfs.com/guides/order-guide", timeout=45000)
        await asyncio.sleep(4)

        # Get all script URLs
        script_urls = await page.evaluate("""
            () => Array.from(document.querySelectorAll('script[src]'))
                .map(s => s.src)
                .filter(s => s.includes('order.gfs.com'))
        """)
        print(f"Found {len(script_urls)} GFS scripts:")
        for s in script_urls[:10]:
            print(f"  {s[-60:]}")

        results = []

        # Patterns to search for
        patterns = [
            # Cart-level submit endpoints
            (r'v\d+/cart[^"\'`\s]{0,60}(?:submit|place|confirm|checkout)[^"\'`\s]{0,30}', 'cart-submit-path'),
            (r'(?:submit|place|confirm|checkout)[^"\'`\s]{0,30}cart[^"\'`\s]{0,30}', 'cart-submit-path2'),
            # Order endpoints
            (r'["\'/]v\d+/order(?:s)?(?:/[^"\'`\s]{0,40})?["\'\s]', 'order-endpoint'),
            # submitOrder, placeOrder etc. function/method names
            (r'submitOrder[^(]{0,60}', 'submitOrder-fn'),
            (r'placeOrder[^(]{0,60}', 'placeOrder-fn'),
            (r'submitCart[^(]{0,60}', 'submitCart-fn'),
            (r'checkout[A-Z][^(]{0,40}', 'checkout-fn'),
            # PUT/POST patterns near "submit"
            (r'"submit":\s*(?:true|function|["\'])', 'submit-prop'),
            # Angular HTTP calls with submit-related paths
            (r'(?:post|put)\(["\'][^"\']*submit[^"\']*["\']', 'http-submit'),
            (r'(?:post|put)\(["\'][^"\']*order[^"\']*["\']', 'http-order'),
            # v7 or v8 calls other than what we know
            (r'v[789]/(?!cart\b)[^"\'`\s]{1,40}', 'v7-v9-other'),
        ]

        for script_url in script_urls:
            try:
                print(f"\n→ Fetching {script_url[-50:]} …")
                resp = await page.evaluate(f"""
                    async () => {{
                        const r = await fetch({json.dumps(script_url)});
                        if (!r.ok) return null;
                        return await r.text();
                    }}
                """)
                if not resp:
                    print("  (empty/failed)")
                    continue

                size_kb = len(resp) // 1024
                print(f"  Size: {size_kb} KB")

                found_any = False
                for pat_str, label in patterns:
                    matches = re.findall(pat_str, resp, re.IGNORECASE)
                    unique  = list(dict.fromkeys(m.strip() for m in matches))  # dedupe preserving order
                    if unique:
                        found_any = True
                        print(f"\n  [{label}] ({len(unique)} matches):")
                        for m in unique[:15]:
                            print(f"    {m[:120]}")
                        results.append({
                            "script": script_url[-50:],
                            "label":  label,
                            "matches": unique[:15],
                        })

                if not found_any:
                    print("  (no submit-related patterns found)")

            except Exception as e:
                print(f"  Error: {e}")

        # Save
        out = f"{API_DIR}/gfs_bundle_search.json"
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n✅ Saved → {out}")

        await browser.close()

asyncio.run(main())
