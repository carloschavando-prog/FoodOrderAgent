"""
On Par — Weekly Food Order Generator
======================================
Reads par levels + live pricing from Supabase.
Assigns each item to the cheapest vendor that carries it,
then enforces vendor order minimums.

Vendor minimums:
  US Foods (1) : 20 cases
  PFG      (2) : 20 cases
  Sysco    (3) : 15 cases
  GFS      (4) : $750 purchase

Algorithm:
  1. Greedy assignment — cheapest vendor per item
  2. Iterative consolidation — if a vendor misses its minimum,
     drop it from the active set and re-assign its items to
     the next-cheapest option.  Repeat until stable.
  3. Any item that has no active-vendor price → "Manual / Other Vendors"

Outputs:
  weekly_order.html  — printable order sheet

Usage:
    python3 weekly_order.py
"""

import json, math, os, re, sys, webbrowser, datetime, urllib.request, argparse
from collections import defaultdict

# ── Config ────────────────────────────────────────────────────────────────────
SB_URL = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_HDRS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Accept":        "application/json",
}
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "weekly_order.html")

# Vendor IDs for the 4 broadliners
BROADLINER_IDS = [1, 2, 3, 4]
VENDOR_NAMES   = {1: "US Foods", 2: "PFG", 3: "Sysco", 4: "GFS"}
VENDOR_ABBR    = {1: "USF",      2: "PFG", 3: "SYC",   4: "GFS"}
VENDOR_COLOR   = {
    1: ("#1d4e89", "#dce8f8"),   # dark blue / light blue
    2: ("#b5451b", "#fce8e0"),   # burnt orange / light orange
    3: ("#1a6b3c", "#ddf3e8"),   # dark green / light green
    4: ("#7a5c00", "#fdf3d0"),   # gold / light gold
}

# Minimum order requirements
# (type, value)  type = "cases" or "dollars"
MINIMUMS = {
    1: ("cases",   20),   # US Foods: 20 cases
    2: ("cases",   20),   # PFG: 20 cases
    3: ("cases",   15),   # Sysco: 15 cases
    4: ("dollars", 750),  # GFS: $750
}

CAT_ORDER = [
    (1, "Paper Goods"),
    (2, "Spice Shelf"),
    (3, "Tortilla Shelf"),
    (4, "Dry Stock"),
    (5, "Disposables"),
    (6, "Walk-In Cooler"),
    (7, "Freezer"),
    (8, "Chemical Room"),
    (9, "Beverage Dock"),
]

# ── Supabase helpers ──────────────────────────────────────────────────────────

def sb_get(path):
    req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", headers=SB_HDRS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def sb_get_all(path, page_size=1000):
    rows = []
    offset = 0
    while True:
        hdrs = {**SB_HDRS, "Range": f"{offset}-{offset + page_size - 1}"}
        req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", headers=hdrs)
        with urllib.request.urlopen(req, timeout=30) as r:
            page = json.loads(r.read())
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size

# ── Data loading ──────────────────────────────────────────────────────────────

def load_data(on_hand=None):
    """
    on_hand: optional dict {item_name_lower: on_hand_float}
    When provided, order_qty = max(0, ceil(par_level - on_hand))
    When None, order_qty = par_level  (standard par-cycle run)
    """
    print("→ Loading items...")
    raw_items = sb_get_all("items?select=id,name,category_id,pack_size,par_level,"
                           "preferred_vendor_id&order=id.asc")

    # Deduplicate: group by lower-cased name, keep lowest id as canonical
    name_groups = defaultdict(list)
    id_to_item  = {}
    for row in raw_items:
        key = row["name"].lower().strip()
        name_groups[key].append(row["id"])
        id_to_item[row["id"]] = row

    canonical_items = []
    for lower_name, ids in name_groups.items():
        ids.sort()
        can_id = ids[0]
        item   = id_to_item[can_id]
        par = float(item.get("par_level") or 0)
        if on_hand is not None:
            oh  = on_hand.get(item["name"].lower().strip())
            if oh is not None:
                qty = max(0.0, math.ceil(par - float(oh)))
            else:
                qty = par   # not counted → fall back to full par
        else:
            qty = par

        canonical_items.append({
            "id":            can_id,
            "all_ids":       ids,
            "name":          item["name"],
            "category_id":   item["category_id"],
            "pack_size":     item.get("pack_size") or "",
            "par_level":     par,
            "order_qty":     qty,
            "preferred_vid": item.get("preferred_vendor_id"),
        })
    # Sort by category, then name
    canonical_items.sort(key=lambda x: (x["category_id"] or 99, x["name"].lower()))

    # Reverse map: any item_id → canonical_id
    id_to_canonical = {}
    for ci in canonical_items:
        for iid in ci["all_ids"]:
            id_to_canonical[iid] = ci["id"]

    print(f"  {len(canonical_items)} unique items")

    # Load all pricing (broadliners only), newest price wins per (canonical_id, vendor_id)
    print("→ Loading pricing...")
    all_pricing = sb_get_all(
        "pricing?select=item_id,vendor_id,apn,price,price_list_id"
        "&order=price_list_id.asc"
    )
    print(f"  {len(all_pricing)} total rows")

    # best_prices[canonical_id][vendor_id] = {price, apn}
    best_prices = defaultdict(dict)
    for row in all_pricing:
        vid = row["vendor_id"]
        if vid not in BROADLINER_IDS:
            continue
        apn   = row.get("apn") or ""
        price = row.get("price")
        if price is None:
            continue
        iid    = row["item_id"]
        can_id = id_to_canonical.get(iid, iid)
        best_prices[can_id][vid] = {"price": float(price), "apn": apn}

    # Summary
    for vid in BROADLINER_IDS:
        cnt = sum(1 for p in best_prices.values() if vid in p)
        print(f"  {VENDOR_NAMES[vid]}: {cnt} items with prices")

    return canonical_items, dict(best_prices)

# ── Basket optimizer ──────────────────────────────────────────────────────────

def meets_minimum(vid, cases, spend):
    min_type, min_val = MINIMUMS[vid]
    if min_type == "cases":
        return cases >= min_val
    else:
        return spend >= min_val

def minimum_label(vid):
    min_type, min_val = MINIMUMS[vid]
    if min_type == "cases":
        return f"{min_val} cases"
    else:
        return f"${min_val:,.0f}"

def calc_totals(assignment, items_by_id, best_prices):
    """
    Returns:
      vendor_items  – {vendor_id: [(canonical_item, price, apn)]}
      vendor_cases  – {vendor_id: int total_cases}
      vendor_spend  – {vendor_id: float total_$}
    """
    vendor_items = defaultdict(list)
    vendor_cases = defaultdict(int)
    vendor_spend = defaultdict(float)

    for can_id, vid in assignment.items():
        item  = items_by_id[can_id]
        cases = int(item["order_qty"]) if item["order_qty"] > 0 else 1
        pdata = best_prices[can_id][vid]
        price = pdata["price"]
        apn   = pdata.get("apn", "")
        vendor_items[vid].append({
            "item":     item,
            "cases":    cases,
            "price":    price,
            "apn":      apn,
            "subtotal": round(cases * price, 2),
        })
        vendor_cases[vid] += cases
        vendor_spend[vid] += cases * price

    return dict(vendor_items), dict(vendor_cases), dict(vendor_spend)


def optimize_basket(canonical_items, best_prices):
    """
    Returns:
      assignment    – {canonical_id: vendor_id}
      dropped_vids  – set of vendor_ids that were dropped (failed minimum)
      unassigned    – list of canonical_items with no active-vendor price
      consolidation_notes – list of strings describing what was moved
    """
    items_by_id = {ci["id"]: ci for ci in canonical_items}
    active      = set(BROADLINER_IDS)
    notes       = []
    dropped     = set()

    for iteration in range(10):
        # Greedy: cheapest active vendor per item
        assignment = {}
        for ci in canonical_items:
            if ci["order_qty"] <= 0:
                continue
            opts = {
                v: best_prices[ci["id"]][v]
                for v in active
                if ci["id"] in best_prices and v in best_prices[ci["id"]]
            }
            if not opts:
                continue
            best_v = min(opts, key=lambda v: opts[v]["price"])
            assignment[ci["id"]] = best_v

        # Calculate totals
        _, vendor_cases, vendor_spend = calc_totals(assignment, items_by_id, best_prices)

        # Check minimums for active vendors
        failing = set()
        for vid in active:
            cases = vendor_cases.get(vid, 0)
            spend = vendor_spend.get(vid, 0.0)
            if cases == 0:
                continue   # vendor has no items; don't penalise
            if not meets_minimum(vid, cases, spend):
                failing.add(vid)

        if not failing:
            break  # ✅ all minimums satisfied

        # Drop failing vendors (smallest case/spend first for stability)
        to_drop = sorted(
            failing,
            key=lambda v: vendor_spend.get(v, 0),
        )
        for vid in to_drop:
            cases = vendor_cases.get(vid, 0)
            spend = vendor_spend.get(vid, 0.0)
            min_type, min_val = MINIMUMS[vid]
            if min_type == "cases":
                shortfall = f"{cases}/{min_val} cases"
            else:
                shortfall = f"${spend:,.0f}/${min_val:,.0f}"
            notes.append(
                f"{VENDOR_NAMES[vid]} dropped — minimum not met "
                f"({shortfall}).  Items reassigned to next cheapest vendor."
            )
            active.discard(vid)
            dropped.add(vid)

    # Items that couldn't be assigned to any broadliner
    unassigned = []
    for ci in canonical_items:
        if ci["par_level"] <= 0:
            continue
        if ci["id"] not in assignment:
            unassigned.append(ci)

    return assignment, dropped, unassigned, notes


# ── Savings calculation ────────────────────────────────────────────────────────

def compute_savings(assignment, canonical_items, best_prices):
    """
    For each assigned item, compare price paid vs:
      - highest available price (worst case)
    Returns:
      rows – list of dicts per item
      total_saved – float
    """
    items_by_id = {ci["id"]: ci for ci in canonical_items}
    rows        = []
    total_saved = 0.0

    for can_id, vid in assignment.items():
        item   = items_by_id[can_id]
        cases  = int(item["par_level"]) if item["par_level"] > 0 else 1
        prices = best_prices.get(can_id, {})
        if not prices:
            continue

        paid_price = prices[vid]["price"]
        all_prices = {v: d["price"] for v, d in prices.items()}
        max_price  = max(all_prices.values())
        min_price  = min(all_prices.values())
        max_vendor = max(all_prices, key=all_prices.get)

        saved_vs_worst = round((max_price - paid_price) * cases, 2)
        total_saved   += saved_vs_worst

        rows.append({
            "item":         item,
            "cases":        cases,
            "chosen_vid":   vid,
            "paid_price":   paid_price,
            "max_price":    max_price,
            "max_vendor":   max_vendor,
            "saved":        saved_vs_worst,
            "all_prices":   all_prices,
            "competing":    len(all_prices) > 1,
        })

    # Sort by savings descending
    rows.sort(key=lambda r: r["saved"], reverse=True)
    return rows, round(total_saved, 2)


# ── HTML builder ──────────────────────────────────────────────────────────────

CSS = """
:root {
  --bg: #f0f2f5; --card: #fff;
  --text: #1a1a2e; --muted: #6c757d;
  --border: #dee2e6;
  --green:  #198754; --red: #dc3545;
  --yellow: #ffc107; --blue: #0d6efd;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg); color: var(--text); font-size: 13px;
}
/* ── Page header ──────────────────────── */
.page-header {
  background: #1a1a2e; color: #fff;
  padding: 16px 32px;
  display: flex; justify-content: space-between; align-items: flex-end;
}
.page-header h1 { font-size: 1.5rem; font-weight: 700; letter-spacing: .04em; }
.page-header .meta { font-size: .78rem; opacity: .6; margin-top: 4px; }
/* ── Summary bar ──────────────────────── */
.summary-bar {
  background: #fff; border-bottom: 1px solid var(--border);
  padding: 10px 32px; display: flex; gap: 28px; flex-wrap: wrap;
}
.summary-bar .stat { display: flex; flex-direction: column; }
.summary-bar .stat span { font-size: .72rem; color: var(--muted); text-transform: uppercase;
  letter-spacing: .05em; }
.summary-bar .stat strong { font-size: 1rem; font-weight: 700; color: var(--text); }
/* ── Content ──────────────────────────── */
.content { padding: 20px 24px; display: flex; flex-direction: column; gap: 20px; }
/* ── Vendor card ──────────────────────── */
.vendor-card {
  background: var(--card);
  border-radius: 10px;
  box-shadow: 0 1px 4px rgba(0,0,0,.1);
  overflow: hidden;
}
.vendor-header {
  padding: 12px 18px;
  display: flex; justify-content: space-between; align-items: center;
}
.vendor-header h2 { font-size: 1rem; font-weight: 700; letter-spacing: .03em; }
.vendor-header .v-stats { display: flex; gap: 20px; font-size: .8rem; }
.vendor-header .v-stat span { opacity: .75; }
.badge {
  display: inline-block; padding: 3px 10px; border-radius: 20px;
  font-size: .7rem; font-weight: 700; letter-spacing: .04em;
}
.badge-ok  { background: #d4edda; color: #155724; }
.badge-warn { background: #fff3cd; color: #856404; }
.badge-err  { background: #f8d7da; color: #721c24; }
/* ── Items table ──────────────────────── */
table { width: 100%; border-collapse: collapse; }
thead tr { background: rgba(0,0,0,.04); }
thead th {
  padding: 7px 12px; text-align: left; font-size: .7rem;
  text-transform: uppercase; letter-spacing: .07em; color: var(--muted);
  white-space: nowrap;
}
th.r, td.r { text-align: right; }
th.c, td.c { text-align: center; }
tbody td { padding: 7px 12px; border-top: 1px solid var(--border); }
tbody tr:hover { background: rgba(0,0,0,.02); }
td.item-name { font-weight: 500; }
td.pack  { color: var(--muted); font-size: .78rem; white-space: nowrap; }
td.apn   { font-family: 'SF Mono','Fira Code',monospace; font-size: .75rem; color: var(--muted); }
td.price { font-variant-numeric: tabular-nums; }
td.sub   { font-weight: 600; font-variant-numeric: tabular-nums; }
.vendor-footer {
  padding: 10px 18px; background: rgba(0,0,0,.03);
  display: flex; justify-content: flex-end; gap: 24px; font-size: .82rem;
}
.vendor-footer .total-label { color: var(--muted); }
.vendor-footer .total-val   { font-weight: 700; font-size: 1rem; }
/* cat group header inside table */
tr.cat-header td {
  background: #f8f9fa; font-weight: 700; font-size: .72rem;
  text-transform: uppercase; letter-spacing: .07em;
  color: #495057; padding: 5px 12px; border-top: 2px solid var(--border);
}
/* ── Min warning banner ───────────────── */
.consol-box {
  background: #fff3cd; border: 1px solid #ffc107; border-radius: 8px;
  padding: 12px 18px; font-size: .82rem; color: #664d03;
}
.consol-box strong { display: block; margin-bottom: 4px; font-size: .85rem; }
/* ── Manual section ───────────────────── */
.manual-card {
  background: var(--card); border-radius: 10px;
  box-shadow: 0 1px 4px rgba(0,0,0,.1); overflow: hidden;
}
.manual-header {
  background: #495057; color: #fff;
  padding: 12px 18px; font-size: 1rem; font-weight: 700;
}
/* ── Savings section ──────────────────── */
.savings-card {
  background: var(--card); border-radius: 10px;
  box-shadow: 0 1px 4px rgba(0,0,0,.1); overflow: hidden;
}
.savings-header {
  background: #1a1a2e; color: #fff;
  padding: 12px 18px; display: flex; justify-content: space-between;
  align-items: center;
}
.savings-header h2 { font-size: 1rem; font-weight: 700; }
.savings-total {
  font-size: 1.1rem; font-weight: 700; color: #4ade80;
}
td.save-pos { color: #198754; font-weight: 600; }
td.save-zero { color: var(--muted); }
.price-chip {
  display: inline-block; padding: 1px 6px; border-radius: 10px;
  font-size: .7rem; font-weight: 600; margin: 1px;
}
@media print {
  body { background: #fff; }
  .summary-bar { border: 1px solid #ccc; }
  .vendor-card, .manual-card, .savings-card { box-shadow: none; border: 1px solid #ddd; }
  .content { padding: 8px; gap: 12px; }
}
"""


def fmt_money(v):
    return f"${v:,.2f}"

def fmt_cases(n):
    return f"{n}"


def _pill(text, bg, fg):
    return (f'<span class="price-chip" '
            f'style="background:{bg};color:{fg}">{text}</span>')


def vendor_pill(vid):
    dark, light = VENDOR_COLOR.get(vid, ("#333", "#eee"))
    return _pill(VENDOR_ABBR.get(vid, str(vid)), light, dark)


def build_html(
    assignment, dropped, unassigned, notes,
    canonical_items, best_prices,
    savings_rows, total_saved,
    from_count=False,
):
    items_by_id   = {ci["id"]: ci for ci in canonical_items}
    vendor_items, vendor_cases, vendor_spend = calc_totals(
        assignment, items_by_id, best_prices
    )

    now         = datetime.datetime.now()
    date_str    = now.strftime("%A, %B %d, %Y")
    time_str    = now.strftime("%I:%M %p")
    mode_label  = "From Inventory Count" if from_count else "Full Par-Level Order"
    grand_total = sum(vendor_spend.values())
    total_cases = sum(vendor_cases.values())

    # Organise assignment by category for per-vendor tables
    def sorted_by_cat(items_list):
        cat_map = defaultdict(list)
        for entry in items_list:
            cat_map[entry["item"]["category_id"]].append(entry)
        result = []
        for cat_id, cat_name in CAT_ORDER:
            grp = cat_map.get(cat_id, [])
            if grp:
                grp.sort(key=lambda e: e["item"]["name"].lower())
                result.append((cat_name, grp))
        return result

    html = [f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weekly Food Order — {date_str}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page-header">
  <div>
    <h1>📋  Weekly Food Order</h1>
    <div class="meta">On Par Bar &amp; Grill &nbsp;·&nbsp; {date_str} &nbsp;·&nbsp; Generated {time_str} &nbsp;·&nbsp; {mode_label}</div>
  </div>
  <div style="text-align:right;font-size:.85rem;opacity:.8">
    {len(assignment)} items ordered &nbsp;|&nbsp;
    {total_cases} cases &nbsp;|&nbsp;
    <strong style="font-size:1.1rem;opacity:1">{fmt_money(grand_total)}</strong>
  </div>
</div>

<div class="summary-bar">"""]

    for vid in BROADLINER_IDS:
        cases = vendor_cases.get(vid, 0)
        spend = vendor_spend.get(vid, 0.0)
        if vid in dropped:
            status_html = '<span class="badge badge-err">DROPPED</span>'
        elif cases == 0:
            status_html = '<span class="badge badge-warn">NO ITEMS</span>'
        elif meets_minimum(vid, cases, spend):
            status_html = '<span class="badge badge-ok">MIN MET ✓</span>'
        else:
            status_html = '<span class="badge badge-warn">BELOW MIN</span>'

        dark, light = VENDOR_COLOR[vid]
        html.append(f"""
  <div class="stat">
    <span style="color:{dark};font-weight:700">{VENDOR_NAMES[vid]}</span>
    <strong>{fmt_money(spend)}</strong>
    <span>{cases} cases &nbsp; {status_html}</span>
  </div>""")

    if total_saved > 0:
        html.append(f"""
  <div class="stat" style="margin-left:auto;text-align:right">
    <span>Savings vs Worst</span>
    <strong style="color:#198754">{fmt_money(total_saved)}</strong>
    <span>vs always buying highest price</span>
  </div>""")

    html.append("</div>\n\n<div class=\"content\">\n")

    # ── Consolidation notes ──────────────────────────────────────────────────
    if notes:
        html.append('<div class="consol-box"><strong>⚠️  Basket Consolidation Notes</strong>')
        for n in notes:
            html.append(f"<div>• {n}</div>")
        html.append("</div>\n")

    # ── Per-vendor cards ─────────────────────────────────────────────────────
    for vid in BROADLINER_IDS:
        entries = vendor_items.get(vid, [])
        if not entries:
            continue

        cases = vendor_cases.get(vid, 0)
        spend = vendor_spend.get(vid, 0.0)
        dark, light = VENDOR_COLOR[vid]

        if meets_minimum(vid, cases, spend):
            badge = '<span class="badge badge-ok">Minimum Met ✓</span>'
        else:
            min_type, min_val = MINIMUMS[vid]
            if min_type == "cases":
                badge = f'<span class="badge badge-warn">⚠️ Need {min_val} cases (have {cases})</span>'
            else:
                badge = f'<span class="badge badge-warn">⚠️ Need {fmt_money(min_val)} (have {fmt_money(spend)})</span>'

        html.append(f"""
<div class="vendor-card">
  <div class="vendor-header" style="background:{dark};color:#fff">
    <h2>{VENDOR_NAMES[vid]}</h2>
    <div class="v-stats">
      <div><span>Cases: </span><strong>{cases}</strong></div>
      <div><span>Total: </span><strong>{fmt_money(spend)}</strong></div>
      <div>{badge}</div>
    </div>
  </div>
  <table>
    <thead><tr>
      <th class="nm-col">Item</th>
      <th>Pack</th>
      <th class="c">Vendor #</th>
      <th class="c">Cases</th>
      <th class="r">Unit Price</th>
      <th class="r">Subtotal</th>
    </tr></thead>
    <tbody>""")

        for cat_name, grp in sorted_by_cat(entries):
            html.append(
                f'<tr class="cat-header"><td colspan="6">{cat_name}</td></tr>'
            )
            for e in grp:
                item = e["item"]
                html.append(f"""
      <tr>
        <td class="item-name">{item["name"]}</td>
        <td class="pack">{item["pack_size"]}</td>
        <td class="apn c">{e["apn"] or "—"}</td>
        <td class="c">{e["cases"]}</td>
        <td class="price r">{fmt_money(e["price"])}</td>
        <td class="sub r">{fmt_money(e["subtotal"])}</td>
      </tr>""")

        html.append(f"""
    </tbody>
  </table>
  <div class="vendor-footer">
    <span class="total-label">Order Total:</span>
    <span class="total-val">{fmt_money(spend)}</span>
  </div>
</div>""")

    # ── Manual / Other Vendors section ───────────────────────────────────────
    if unassigned:
        other_vendors = {
            5: "I Supply", 6: "Markets Depot", 7: "Meat Church",
        }
        html.append("""
<div class="manual-card">
  <div class="manual-header">🛒  Manual / Other Vendors</div>
  <table>
    <thead><tr>
      <th>Item</th>
      <th>Pack</th>
      <th class="c">Cases</th>
      <th>Preferred Vendor</th>
      <th class="c">Note</th>
    </tr></thead>
    <tbody>""")

        last_cat = None
        for ci in sorted(unassigned, key=lambda x: (x["category_id"], x["name"].lower())):
            cat_id = ci["category_id"]
            if cat_id != last_cat:
                last_cat = cat_id
                cat_name = dict(CAT_ORDER).get(cat_id, f"Cat {cat_id}")
                html.append(f'<tr class="cat-header"><td colspan="5">{cat_name}</td></tr>')

            pref_vid  = ci["preferred_vid"]
            pref_name = (VENDOR_NAMES.get(pref_vid)
                         or other_vendors.get(pref_vid, f"Vendor {pref_vid}"))
            cases = int(ci["order_qty"]) if ci["order_qty"] > 0 else "?"
            note  = "No broadliner price on file"
            html.append(f"""
      <tr>
        <td class="item-name">{ci["name"]}</td>
        <td class="pack">{ci["pack_size"]}</td>
        <td class="c">{cases}</td>
        <td>{pref_name}</td>
        <td class="c" style="color:#856404;font-size:.75rem">{note}</td>
      </tr>""")

        html.append("  </tbody></table>\n</div>\n")

    # ── Savings Summary ──────────────────────────────────────────────────────
    if savings_rows:
        competing = [r for r in savings_rows if r["competing"] and r["saved"] >= 0.01]
        html.append(f"""
<div class="savings-card">
  <div class="savings-header">
    <h2>💰  Savings Summary — Cheapest Vendor vs Most Expensive Available</h2>
    <div class="savings-total">Total Saved: {fmt_money(total_saved)}</div>
  </div>
  <table>
    <thead><tr>
      <th>Item</th>
      <th class="c">Cases</th>
      <th>Ordered From</th>
      <th class="r">Price Paid</th>
      <th>All Available Prices</th>
      <th class="r">Highest Avail.</th>
      <th class="r">Saved</th>
    </tr></thead>
    <tbody>""")

        for r in competing:
            item = r["item"]
            dark, light = VENDOR_COLOR.get(r["chosen_vid"], ("#333", "#eee"))
            vendor_name = VENDOR_NAMES.get(r["chosen_vid"], str(r["chosen_vid"]))

            # Build price chips for all vendors
            price_chips = ""
            for v, p in sorted(r["all_prices"].items(), key=lambda x: x[1]):
                d2, l2 = VENDOR_COLOR.get(v, ("#333", "#eee"))
                chip_label = f"{VENDOR_ABBR[v]} {fmt_money(p)}"
                price_chips += _pill(chip_label, l2, d2)

            saved_td = (
                f'<td class="save-pos r">{fmt_money(r["saved"])}</td>'
                if r["saved"] > 0 else
                f'<td class="save-zero r">—</td>'
            )

            html.append(f"""
      <tr>
        <td class="item-name">{item["name"]}</td>
        <td class="c">{r["cases"]}</td>
        <td>{_pill(vendor_name, light, dark)}</td>
        <td class="r">{fmt_money(r["paid_price"])}</td>
        <td style="white-space:nowrap">{price_chips}</td>
        <td class="r" style="color:#dc3545">{fmt_money(r["max_price"])}</td>
        {saved_td}
      </tr>""")

        # Totals row
        total_paid    = sum(r["cases"] * r["paid_price"] for r in competing)
        total_max     = sum(r["cases"] * r["max_price"]  for r in competing)
        html.append(f"""
      <tr style="background:#f8f9fa;font-weight:700;border-top:2px solid #dee2e6">
        <td colspan="3">TOTAL  ({len(competing)} items with competing prices)</td>
        <td class="r">{fmt_money(total_paid)}</td>
        <td></td>
        <td class="r" style="color:#dc3545">{fmt_money(total_max)}</td>
        <td class="save-pos r">{fmt_money(total_saved)}</td>
      </tr>""")

        html.append("  </tbody></table>\n</div>\n")

    html.append("</div>\n</body>\n</html>")
    return "".join(html)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="On Par Weekly Food Order Generator")
    parser.add_argument(
        "--from-count", metavar="FILE",
        help="JSON file with {item_name: on_hand_qty} from the inventory count sheet. "
             "When provided, order quantities = max(0, PAR - on_hand). "
             "Without this flag, order quantities = full PAR level."
    )
    args = parser.parse_args()

    on_hand = None
    if args.from_count:
        with open(args.from_count) as f:
            raw = json.load(f)
        # Normalise keys to lowercase
        on_hand = {k.lower().strip(): float(v) for k, v in raw.items() if v != "" and v is not None}
        print(f"  📋 Using on-hand counts for {len(on_hand)} items from {args.from_count}")

    print("═══════════════════════════════════════════════════")
    print("  On Par — Weekly Food Order Generator")
    print("═══════════════════════════════════════════════════")

    canonical_items, best_prices = load_data(on_hand)

    print("\n→ Running basket optimizer...")
    assignment, dropped, unassigned, notes = optimize_basket(canonical_items, best_prices)

    items_by_id = {ci["id"]: ci for ci in canonical_items}
    _, vendor_cases, vendor_spend = calc_totals(assignment, items_by_id, best_prices)

    print(f"\n── Basket Summary ─────────────────────────────────")
    for vid in BROADLINER_IDS:
        cases = vendor_cases.get(vid, 0)
        spend = vendor_spend.get(vid, 0.0)
        ok    = "✅" if meets_minimum(vid, cases, spend) else "⚠️ "
        print(f"  {ok} {VENDOR_NAMES[vid]:<10} {cases:3d} cases   {fmt_money(spend):>10}")

    grand = sum(vendor_spend.values())
    total = sum(vendor_cases.values())
    print(f"\n  Grand Total: {fmt_money(grand)}  ({total} cases)")

    if notes:
        print("\n── Consolidation Notes ────────────────────────────")
        for n in notes:
            print(f"  • {n}")

    if unassigned:
        print(f"\n  No broadliner price for {len(unassigned)} items "
              f"(Manual/Other Vendors section)")

    print("\n→ Computing savings...")
    savings_rows, total_saved = compute_savings(assignment, canonical_items, best_prices)
    print(f"  Total saved vs most expensive available: {fmt_money(total_saved)}")

    print("\n→ Building HTML...")
    html = build_html(
        assignment, dropped, unassigned, notes,
        canonical_items, best_prices,
        savings_rows, total_saved,
        from_count=(on_hand is not None),
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Saved → {OUTPUT_FILE}  ({len(html):,} bytes)")

    try:
        webbrowser.open(f"file://{OUTPUT_FILE}")
        print("→ Opened in browser.")
    except Exception:
        pass


if __name__ == "__main__":
    main()
