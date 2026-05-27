"""
Extended DOM inspection — finds the price element class name and
dumps a full item to understand the scraping structure.
"""
import asyncio, json, os
from playwright.async_api import async_playwright

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/usfoods_session.json")

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=False, slow_mo=100)
        ctx = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            storage_state=SESSION_FILE,
        )
        page = await ctx.new_page()

        await page.goto("https://order.usfoods.com/desktop/lists", timeout=20000)
        await asyncio.sleep(3)
        fall = page.get_by_text("Fall 2025", exact=True).first
        await fall.click()
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        await asyncio.sleep(5)

        print(f"URL: {page.url}")

        # ── Find price elements (non-zero, non-substitute) ─
        print("\n--- All APP-PRICE-DISPLAY elements ---")
        price_els = await page.evaluate(r"""
            () => {
                const els = document.querySelectorAll('app-price-display');
                return Array.from(els).slice(0,20).map(el => ({
                    class: el.className,
                    text: el.innerText.trim().slice(0,40),
                    parentClass: (el.parentElement||{}).className||'',
                    grandClass: ((el.parentElement||{}).parentElement||{}).className||''
                }));
            }
        """)
        for el in price_els:
            print(f"  [{el['class']!r}] {el['text']!r}  parent={el['parentClass']!r}")

        # ── Dump one full product card ──────────────────────
        print("\n--- First description-row card ---")
        card = await page.evaluate(r"""
            () => {
                const desc = document.querySelector('.description-row');
                if (!desc) return null;
                // Walk up until we find a card-like container
                let el = desc.parentElement;
                for (let i = 0; i < 8 && el; i++) {
                    if (el.innerText.length > 100 && el.querySelectorAll('.brand-row').length) {
                        return {
                            tag: el.tagName,
                            class: el.className,
                            text: el.innerText.slice(0, 600),
                            html: el.innerHTML.slice(0, 1000)
                        };
                    }
                    el = el.parentElement;
                }
                return {
                    tag: desc.tagName, class: desc.className,
                    text: desc.innerText.slice(0,300), html: desc.innerHTML.slice(0,300)
                };
            }
        """)
        if card:
            print(f"Tag: {card['tag']}, Class: {card['class']!r}")
            print(f"Text:\n{card['text']}")

        # ── Check the pricing-info element structure ────────
        print("\n--- pricing-info contents ---")
        pricing = await page.evaluate(r"""
            () => {
                const els = document.querySelectorAll('.pricing-info');
                return Array.from(els).slice(0,5).map(el => ({
                    text: el.innerText.trim().slice(0,200),
                    html: el.innerHTML.slice(0,400)
                }));
            }
        """)
        for p_el in pricing:
            print(f"  Text: {p_el['text']!r}")

        # ── Check case-price and similar elements ──────────
        print("\n--- case-price / price elements ---")
        case_prices = await page.evaluate(r"""
            () => {
                const sels = [
                    '.case-price', '.item-price', '.price', '.current-price',
                    '[class*="case-price"]', '[class*="current-price"]',
                    '[class*="item-price"]', '[class*="week-price"]',
                    'app-price-display:not(.substitute-price)'
                ];
                const results = [];
                for (const sel of sels) {
                    const els = document.querySelectorAll(sel);
                    if (els.length) results.push({
                        sel, count: els.length,
                        samples: Array.from(els).slice(0,3).map(e=>e.innerText.trim().slice(0,30))
                    });
                }
                return results;
            }
        """)
        for cp in case_prices:
            print(f"  {cp['sel']!r} → {cp['count']}x  samples={cp['samples']}")

        await asyncio.sleep(20)
        await browser.close()

asyncio.run(main())
