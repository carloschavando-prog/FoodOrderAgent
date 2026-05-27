"""
Basket Report — cheapest vendor per item across all active price lists.

Reads the `cheapest_prices` Supabase view (pre-ranked by price per item),
then prints a Markdown summary to stdout.

In CI, output is piped to $GITHUB_STEP_SUMMARY so it appears on the
GitHub Actions run page under the "Basket Report" job.

Run locally:
  python3 basket_report.py
"""

import json, os, sys, urllib.request
from datetime import datetime, timezone

SB_URL = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")

SB_HDRS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Accept":        "application/json",
}


def sb_get(path):
    req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", headers=SB_HDRS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    # Pull all rows from cheapest_prices, sorted by category + item + rank
    rows = sb_get(
        "cheapest_prices"
        "?select=item_id,item_name,category,vendor,price,price_rank,season"
        "&order=category.asc,item_name.asc,price_rank.asc"
    )

    if not rows:
        print("## Basket Report\n\n_No pricing data available._")
        return

    # Filter to best-price row per item (rank=1) for the cheapest-vendor table
    seen_items = {}
    all_by_item = {}
    for r in rows:
        iid = r["item_id"]
        if iid not in all_by_item:
            all_by_item[iid] = []
        all_by_item[iid].append(r)
        if r["price_rank"] == 1 and iid not in seen_items:
            seen_items[iid] = r

    best_rows = sorted(seen_items.values(),
                       key=lambda r: (r.get("category") or "", r.get("item_name") or ""))

    # Compute savings: best price vs most expensive available
    def savings(item_id):
        opts = all_by_item.get(item_id, [])
        prices = [o["price"] for o in opts if o.get("price") is not None]
        if len(prices) < 2:
            return None, None
        return max(prices) - min(prices), max(prices)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    season = best_rows[0].get("season", "?") if best_rows else "?"

    # ── Header ────────────────────────────────────────────────────────────────
    print(f"## 🛒 Basket Report — {season}")
    print(f"_Generated {now}_\n")

    # ── Summary stats ─────────────────────────────────────────────────────────
    vendors_seen = sorted({r["vendor"] for r in rows if r.get("vendor")})
    total_best   = sum(r["price"] for r in best_rows if r.get("price"))
    items_multi  = sum(1 for iid, opts in all_by_item.items() if len(opts) >= 2)

    print(f"| Stat | Value |")
    print(f"|------|-------|")
    print(f"| Vendors with current prices | {', '.join(vendors_seen)} |")
    print(f"| Items priced | {len(best_rows)} |")
    print(f"| Items priced by ≥2 vendors | {items_multi} |")
    print(f"| Estimated basket (cheapest vendor each) | **${total_best:,.2f}** |")
    print()

    # ── Savings table (items with multiple vendor options) ────────────────────
    saveable = []
    for r in best_rows:
        save_amt, worst_price = savings(r["item_id"])
        if save_amt and save_amt > 0.01:
            saveable.append((r, save_amt, worst_price))

    saveable.sort(key=lambda x: -x[1])  # largest savings first

    if saveable:
        basket_savings = sum(s for _, s, _ in saveable)
        print(f"### 💰 Savings vs most expensive option — save **${basket_savings:,.2f}** total\n")
        print("| Item | Best Vendor | Best Price | Worst Price | Save |")
        print("|------|-------------|-----------|------------|------|")
        for r, save_amt, worst_price in saveable:
            name   = r["item_name"] or "?"
            vendor = r["vendor"]   or "?"
            best   = r["price"]
            print(f"| {name} | {vendor} | ${best:.2f} | ${worst_price:.2f} | **${save_amt:.2f}** |")
        print()

    # ── Full cheapest-vendor table by category ────────────────────────────────
    print("### 📋 Cheapest vendor per item\n")

    current_cat = None
    for r in best_rows:
        cat = r.get("category") or "Uncategorized"
        if cat != current_cat:
            if current_cat is not None:
                print()
            print(f"**{cat}**\n")
            print("| Item | Vendor | Price |")
            print("|------|--------|-------|")
            current_cat = cat
        name   = r.get("item_name") or "?"
        vendor = r.get("vendor")    or "?"
        price  = r.get("price")
        price_str = f"${price:.2f}" if price is not None else "—"
        print(f"| {name} | {vendor} | {price_str} |")


if __name__ == "__main__":
    main()
