"""
On Par Item Master — Cross-Vendor Coverage Report
===================================================
Queries Supabase items + latest pricing APNs for all 4 vendors,
deduplicates the items table (each item appears twice — use lower id),
assigns On Par Product IDs (OP-XXX), and writes item_master.html.

Usage:
    python3 generate_item_master.py
    # Opens item_master.html in your browser automatically.

Outputs:
    item_master.html  — full table, grouped by category, color-coded by coverage
"""

import json, os, urllib.request, urllib.parse, webbrowser, datetime

SB_URL = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_HDRS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Accept":        "application/json",
}

VENDOR_IDS   = [1, 2, 3, 4]
VENDOR_NAMES = {1: "US Foods", 2: "PFG", 3: "Sysco", 4: "GFS"}
OUTPUT_FILE  = os.path.join(os.path.dirname(__file__), "item_master.html")

# Category display order + short code for OP IDs
CATEGORIES = [
    (1, "Paper Goods",    "PP"),
    (2, "Spice Shelf",    "SP"),
    (3, "Tortilla Shelf", "TR"),
    (4, "Dry Stock",      "DS"),
    (5, "Disposables",    "DI"),
    (6, "Walk-In Cooler", "WC"),
    (7, "Freezer",        "FZ"),
    (8, "Chemical Room",  "CR"),
    (9, "Beverage Dock",  "BV"),
]
CAT_CODE = {cat_id: code for cat_id, _, code in CATEGORIES}
CAT_NAME = {cat_id: name for cat_id, name, _ in CATEGORIES}

# ── Supabase helpers ──────────────────────────────────────────────────────────

def sb_get(path):
    url = f"{SB_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=SB_HDRS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

# ── Data loading ──────────────────────────────────────────────────────────────

def load_data():
    """
    Returns:
      canonical_items  – list of dicts {canonical_id, name, category_id, all_ids}
                         sorted by category_id, then name.
      vendor_apns      – dict {canonical_id: {vendor_id: apn_string}}
    """

    # 1. Load all items, ordered by id asc (so lower id = canonical)
    print("→ Loading items from Supabase...")
    raw_items = sb_get("items?select=id,name,category_id&order=id.asc")
    print(f"  {len(raw_items)} rows in items table")

    # Group by normalised name → pick lowest id as canonical
    from collections import defaultdict
    name_groups = defaultdict(list)  # lower_name → list of item_ids
    id_to_item  = {}
    for row in raw_items:
        key = row["name"].lower().strip()
        name_groups[key].append(row["id"])
        id_to_item[row["id"]] = row

    canonical_items = []
    for lower_name, ids in name_groups.items():
        ids.sort()
        canonical_id = ids[0]
        item = id_to_item[canonical_id]
        canonical_items.append({
            "canonical_id": canonical_id,
            "name":         item["name"],
            "category_id":  item["category_id"],
            "all_ids":      ids,
        })

    # Sort by category, then name
    canonical_items.sort(key=lambda x: (x["category_id"] or 99, x["name"].lower()))
    print(f"  {len(canonical_items)} unique items after deduplication")

    # Build reverse map: any item_id → canonical_id
    id_to_canonical = {}
    for ci in canonical_items:
        for iid in ci["all_ids"]:
            id_to_canonical[iid] = ci["canonical_id"]

    # 2. Load all price_lists to get season info
    print("→ Loading price lists...")
    pls = sb_get("price_lists?select=id,vendor_id,season&order=id.desc")
    latest_season_per_vendor = {}  # vendor_id → season string of highest pl id
    for pl in pls:
        vid = pl["vendor_id"]
        if vid not in latest_season_per_vendor:
            latest_season_per_vendor[vid] = pl["season"]
    for vid in VENDOR_IDS:
        vn = VENDOR_NAMES.get(vid, str(vid))
        print(f"  {vn}: latest season = {latest_season_per_vendor.get(vid, 'none')}")

    # 3. Load ALL pricing rows for our 4 vendors.
    #    For each (canonical_item_id, vendor_id) pair, keep the APN from the
    #    highest (most recent) price_list_id so CI's old runs don't overwrite fresh ones.
    print("→ Loading pricing APNs (all seasons, best per item×vendor)...")
    all_pricing = sb_get("pricing?select=item_id,vendor_id,apn,price_list_id&order=price_list_id.asc")
    print(f"  {len(all_pricing)} total pricing rows")

    # vendor_apns[canonical_id][vendor_id] = apn   (last write wins = highest pl_id)
    vendor_apns = defaultdict(dict)
    for row in all_pricing:
        vid = row["vendor_id"]
        if vid not in VENDOR_IDS:
            continue
        apn = row.get("apn") or ""
        if not apn:
            continue
        iid   = row["item_id"]
        can_id = id_to_canonical.get(iid, iid)
        vendor_apns[can_id][vid] = apn   # higher pl_id overwrites older

    matched_counts = {vid: 0 for vid in VENDOR_IDS}
    for can_id, vendors in vendor_apns.items():
        for vid in vendors:
            matched_counts[vid] += 1
    for vid in VENDOR_IDS:
        print(f"  {VENDOR_NAMES[vid]}: {matched_counts[vid]} items with APNs")

    return canonical_items, dict(vendor_apns)


def assign_op_ids(canonical_items):
    """
    Assign On Par IDs: OP-{CAT_CODE}{NNN}  e.g. OP-PP001, OP-WC012
    Sequential within each category, alphabetical order (already sorted).
    """
    cat_counter = {}
    for item in canonical_items:
        cat_id = item["category_id"] or 0
        code   = CAT_CODE.get(cat_id, "XX")
        cat_counter[cat_id] = cat_counter.get(cat_id, 0) + 1
        item["op_id"] = f"OP-{code}{cat_counter[cat_id]:03d}"
    return canonical_items


# ── HTML generation ───────────────────────────────────────────────────────────

# Coverage count → row CSS class
def coverage_class(n):
    if n == 4:  return "cov4"
    if n == 3:  return "cov3"
    if n == 2:  return "cov2"
    if n == 1:  return "cov1"
    return "cov0"


HTML_HEAD = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>On Par — Item Master · Cross-Vendor Coverage</title>
<style>
  :root {
    --usf:  #1d4e89;   /* US Foods  — dark blue   */
    --pfg:  #b5451b;   /* PFG       — burnt orange */
    --syc:  #1a6b3c;   /* Sysco     — dark green  */
    --gfs:  #7a5c00;   /* GFS       — gold/brown  */
    --bg:   #f4f5f7;
    --card: #ffffff;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         background: var(--bg); color: #1a1a2e; }
  header { background: #1a1a2e; color: #fff; padding: 18px 32px;
           display: flex; justify-content: space-between; align-items: center; }
  header h1 { font-size: 1.4rem; font-weight: 700; letter-spacing: .03em; }
  header .subtitle { font-size: .85rem; opacity: .65; margin-top: 3px; }
  .legend { display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
  .legend-item { display: flex; align-items: center; gap: 6px; font-size: .78rem; }
  .swatch { width: 14px; height: 14px; border-radius: 3px; flex-shrink: 0; }
  .sw4 { background: #d4edda; border: 1px solid #28a745; }
  .sw3 { background: #d1ecf1; border: 1px solid #17a2b8; }
  .sw2 { background: #fff3cd; border: 1px solid #ffc107; }
  .sw1 { background: #f8d7da; border: 1px solid #dc3545; }
  .sw0 { background: #e9ecef; border: 1px solid #adb5bd; }

  .summary-bar { background: #fff; padding: 10px 32px; border-bottom: 1px solid #dee2e6;
                 display: flex; gap: 24px; flex-wrap: wrap; font-size: .82rem; }
  .summary-bar span { color: #6c757d; }
  .summary-bar strong { color: #1a1a2e; }

  .table-wrap { overflow-x: auto; padding: 20px 24px; }
  table { width: 100%; border-collapse: collapse; background: var(--card);
          box-shadow: 0 1px 3px rgba(0,0,0,.12); border-radius: 8px;
          overflow: hidden; font-size: .82rem; }
  thead tr { background: #1a1a2e; color: #fff; text-transform: uppercase;
             font-size: .72rem; letter-spacing: .07em; }
  thead th { padding: 11px 12px; text-align: left; white-space: nowrap; }
  thead th.op-col  { min-width: 90px; }
  thead th.nm-col  { min-width: 200px; }
  thead th.vnd-col { min-width: 110px; text-align: center; }
  thead th.usf  { color: #9ec8ff; }
  thead th.pfg  { color: #ffc9a8; }
  thead th.syc  { color: #a8f0c8; }
  thead th.gfs  { color: #ffe08a; }

  .cat-row { background: #1a1a2e; color: #e0e0ff; font-weight: 700;
             font-size: .75rem; letter-spacing: .1em; text-transform: uppercase; }
  .cat-row td { padding: 7px 12px; }

  tbody tr:not(.cat-row) { border-bottom: 1px solid #e9ecef; }
  tbody tr:not(.cat-row):hover { filter: brightness(0.97); }
  td { padding: 8px 12px; vertical-align: middle; }
  td.apn { text-align: center; font-family: 'SF Mono','Fira Code',monospace;
           font-size: .78rem; }
  td.apn a { text-decoration: none; color: inherit; }
  td.blank { text-align: center; color: #ced4da; font-size: 1rem; }

  /* Coverage row tinting */
  .cov4 { background: #f0faf3; }
  .cov3 { background: #f0f8fb; }
  .cov2 { background: #fffdf0; }
  .cov1 { background: #fff7f7; }
  .cov0 { background: #f5f5f5; }

  /* Vendor pill in APN cells */
  .pill { display: inline-block; padding: 2px 7px; border-radius: 12px;
          font-size: .72rem; font-weight: 600; letter-spacing: .03em; }
  .pill-usf { background: #dce8f8; color: var(--usf); }
  .pill-pfg { background: #fce8e0; color: var(--pfg); }
  .pill-syc { background: #ddf3e8; color: var(--syc); }
  .pill-gfs { background: #fdf3d0; color: var(--gfs); }

  .op-id { font-family: 'SF Mono','Fira Code',monospace; font-size: .75rem;
           color: #6c757d; font-weight: 600; }
  .item-name { font-weight: 500; }

  @media print {
    header .legend { display: none; }
    .summary-bar   { display: none; }
    body { background: #fff; }
    table { box-shadow: none; }
  }
</style>
</head>
<body>
"""

HTML_FOOT = """\
</body>
</html>
"""


def pill(apn, vendor_id):
    cls = {1: "pill-usf", 2: "pill-pfg", 3: "pill-syc", 4: "pill-gfs"}.get(vendor_id, "")
    return f'<span class="pill {cls}">{apn}</span>'


def build_html(canonical_items, vendor_apns):
    now = datetime.datetime.now().strftime("%B %d, %Y at %I:%M %p")
    total = len(canonical_items)
    cov4 = sum(1 for ci in canonical_items if len(vendor_apns.get(ci["canonical_id"], {})) == 4)
    cov3 = sum(1 for ci in canonical_items if len(vendor_apns.get(ci["canonical_id"], {})) == 3)
    cov2 = sum(1 for ci in canonical_items if len(vendor_apns.get(ci["canonical_id"], {})) == 2)
    cov1 = sum(1 for ci in canonical_items if len(vendor_apns.get(ci["canonical_id"], {})) == 1)
    cov0 = sum(1 for ci in canonical_items if len(vendor_apns.get(ci["canonical_id"], {})) == 0)

    parts = [HTML_HEAD]

    # Header
    parts.append(f"""
<header>
  <div>
    <h1>On Par — Item Master</h1>
    <div class="subtitle">Cross-Vendor Coverage · Generated {now}</div>
  </div>
  <div class="legend">
    <div class="legend-item"><div class="swatch sw4"></div>All 4 vendors ({cov4})</div>
    <div class="legend-item"><div class="swatch sw3"></div>3 vendors ({cov3})</div>
    <div class="legend-item"><div class="swatch sw2"></div>2 vendors ({cov2})</div>
    <div class="legend-item"><div class="swatch sw1"></div>1 vendor ({cov1})</div>
    <div class="legend-item"><div class="swatch sw0"></div>No match ({cov0})</div>
  </div>
</header>
<div class="summary-bar">
  <div><span>Total items: </span><strong>{total}</strong></div>
  <div><span>US Foods: </span><strong>{sum(1 for ci in canonical_items if 1 in vendor_apns.get(ci["canonical_id"], {}))}</strong></div>
  <div><span>PFG: </span><strong>{sum(1 for ci in canonical_items if 2 in vendor_apns.get(ci["canonical_id"], {}))}</strong></div>
  <div><span>Sysco: </span><strong>{sum(1 for ci in canonical_items if 3 in vendor_apns.get(ci["canonical_id"], {}))}</strong></div>
  <div><span>GFS: </span><strong>{sum(1 for ci in canonical_items if 4 in vendor_apns.get(ci["canonical_id"], {}))}</strong></div>
</div>
""")

    # Table
    parts.append('<div class="table-wrap"><table>')
    parts.append("""<thead><tr>
  <th class="op-col">On Par ID</th>
  <th class="nm-col">Item Description</th>
  <th class="vnd-col usf">US Foods</th>
  <th class="vnd-col pfg">PFG</th>
  <th class="vnd-col syc">Sysco</th>
  <th class="vnd-col gfs">GFS</th>
</tr></thead>
<tbody>""")

    current_cat = None
    for item in canonical_items:
        cat_id = item["category_id"]
        if cat_id != current_cat:
            current_cat = cat_id
            cat_name = CAT_NAME.get(cat_id, f"Category {cat_id}")
            parts.append(f'<tr class="cat-row"><td colspan="6">{cat_name}</td></tr>\n')

        can_id  = item["canonical_id"]
        apns    = vendor_apns.get(can_id, {})
        n_vend  = len(apns)
        cls     = coverage_class(n_vend)
        op_id   = item["op_id"]
        name    = item["name"]

        cells = []
        for vid in [1, 2, 3, 4]:
            apn = apns.get(vid, "")
            if apn:
                cells.append(f'<td class="apn">{pill(apn, vid)}</td>')
            else:
                cells.append('<td class="blank">—</td>')

        parts.append(
            f'<tr class="{cls}">'
            f'<td class="op-id">{op_id}</td>'
            f'<td class="item-name">{name}</td>'
            + "".join(cells) +
            "</tr>\n"
        )

    parts.append("</tbody></table></div>")
    parts.append(HTML_FOOT)
    return "".join(parts)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("═══════════════════════════════════════════════════")
    print("  On Par Item Master — Cross-Vendor Coverage")
    print("═══════════════════════════════════════════════════")

    canonical_items, vendor_apns = load_data()
    canonical_items = assign_op_ids(canonical_items)

    print("\n→ Building HTML...")
    html = build_html(canonical_items, vendor_apns)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ Saved → {OUTPUT_FILE}  ({len(html):,} bytes)")

    # Print summary table to console
    print("\n── Coverage Summary ───────────────────────────────")
    from collections import defaultdict
    cat_stats = defaultdict(lambda: [0,0,0,0,0])  # cat_id → [cov4,cov3,cov2,cov1,cov0]
    for item in canonical_items:
        n = len(vendor_apns.get(item["canonical_id"], {}))
        idx = 4 - n if n <= 4 else 0
        cat_stats[item["category_id"]][4-n if n<=4 else 4] += 1

    print(f"{'Category':<20} {'Total':>5} {'4-vend':>6} {'3-vend':>6} {'2-vend':>6} {'1-vend':>6} {'none':>5}")
    print("─" * 60)
    totals = [0,0,0,0,0]
    for cat_id, cat_name, _ in CATEGORIES:
        items_in_cat = [ci for ci in canonical_items if ci["category_id"] == cat_id]
        counts = [0,0,0,0,0]
        for item in items_in_cat:
            n = len(vendor_apns.get(item["canonical_id"], {}))
            counts[4-n if 0<=4-n<5 else 4] += 1
        for i in range(5): totals[i] += counts[i]
        row = f"{cat_name:<20} {len(items_in_cat):>5}"
        for c in counts:
            row += f" {c:>6}" if c else f"{'':>6}"
        print(row)
    print("─" * 60)
    total_items = sum(totals)
    print(f"{'TOTAL':<20} {total_items:>5}  {'  '.join(str(t) for t in totals)}")

    # Open in browser
    try:
        webbrowser.open(f"file://{OUTPUT_FILE}")
        print("\n→ Opened in browser.")
    except Exception:
        pass


if __name__ == "__main__":
    main()
