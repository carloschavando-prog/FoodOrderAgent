"""
GET /api/item_master
====================
Returns the live cross-vendor item master as text/html.
Queries Supabase on every request so it shows current data.

Columns: On Par ID | Item Description | vendor product ID, case price, unit price, broadliner item name
Grouped by category, color-coded by vendor coverage.
"""

import json
import os
import datetime
import urllib.request
import urllib.parse
import html
from collections import defaultdict
from http.server import BaseHTTPRequestHandler

# ── Config ────────────────────────────────────────────────────────────────────

SB_URL = os.environ.get("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_HDRS = {
    "apikey": SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Accept": "application/json",
}

VENDOR_IDS = [1, 2, 3, 4]
VENDOR_NAMES = {1: "US Foods", 2: "PFG", 3: "Sysco", 4: "GFS"}
VENDOR_COLOR = {
    1: ("#0f8f4f", "#dff5e9"),
    2: ("#111111", "#e9ecef"),
    3: ("#1f6feb", "#dceaff"),
    4: ("#d71920", "#fde2e4"),
}
CATEGORIES = [
    (1, "Paper Goods", "PP"),
    (2, "Spice Shelf", "SP"),
    (3, "Tortilla Shelf", "TR"),
    (4, "Dry Stock", "DS"),
    (5, "Disposables", "DI"),
    (6, "Walk-In Cooler", "WC"),
    (7, "Freezer", "FZ"),
    (8, "Chemical Room", "CR"),
    (9, "Beverage Dock", "BV"),
]
CAT_CODE = {cid: code for cid, _, code in CATEGORIES}
CAT_NAME = {cid: name for cid, name, _ in CATEGORIES}
SORT_NAME_OVERRIDES = {
    # Preserve the established OP-DS011 position after the display rename.
    25: "Golden Sauce",
}

# Catalog matches that are verified but do not have a usable current price.
# These display in the item master only; generated orders still require priced rows.
CATALOG_ONLY_MATCHES = {
    (5, 1): {
        "apn": "8690061",
        "price": None,
        "vendor_item_name": "US Foods Monogram Bag, Shopping 13x7x17 Paper Kraft Brown Carry-out",
        "unit_basis": None,
        "unit_quantity": 250,
        "unit_price": None,
        "unit_note": "US Foods Monogram Bag, Shopping 13x7x17 Paper Kraft Brown Carry-out, 250 EA; base catalog match verified, price not visible in current US Foods session.",
        "pulled_at": None,
    },
    (7, 1): {
        "apn": "6645220",
        "price": None,
        "vendor_item_name": "US Foods Handgards Bag, Food Storage 7x7 Sandwich Clear Plastic",
        "unit_basis": None,
        "unit_quantity": 2000,
        "unit_price": None,
        "unit_note": "US Foods Handgards Bag, Food Storage 7x7 Sandwich Clear Plastic, 2000 EA; catalog match verified, price not visible in current US Foods session.",
        "pulled_at": None,
    },
}


def sb_get_all(path, page_size=1000):
    rows = []
    offset = 0
    while True:
        hdrs = {**SB_HDRS, "Range": f"{offset}-{offset + page_size - 1}"}
        req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", headers=hdrs)
        with urllib.request.urlopen(req, timeout=20) as r:
            page = json.loads(r.read())
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += page_size


def load_data():
    raw_items = sb_get_all("items?select=id,name,category_id&order=id.asc")

    name_groups = defaultdict(list)
    id_to_item = {}
    for row in raw_items:
        key = row["name"].lower().strip()
        name_groups[key].append(row["id"])
        id_to_item[row["id"]] = row

    canonical_items = []
    for _, ids in name_groups.items():
        ids.sort()
        can_id = ids[0]
        item = id_to_item[can_id]
        canonical_items.append({
            "id": can_id,
            "all_ids": ids,
            "name": item["name"],
            "category_id": item["category_id"],
        })

    canonical_items.sort(
        key=lambda x: (
            x["category_id"] or 99,
            SORT_NAME_OVERRIDES.get(x["id"], x["name"]).lower(),
        )
    )

    id_to_canonical = {}
    for ci in canonical_items:
        for iid in ci["all_ids"]:
            id_to_canonical[iid] = ci["id"]

    price_lists = sb_get_all("price_lists?select=id,pulled_at")
    price_list_pulled_at = {row["id"]: row.get("pulled_at") for row in price_lists}

    all_pricing = sb_get_all(
        "pricing?select=item_id,vendor_id,apn,price,price_list_id,pulled_at,"
        "unit_basis,unit_quantity,unit_price,unit_note,vendor_item_name"
        "&order=price_list_id.asc"
    )
    vendor_prices = defaultdict(dict)
    for row in all_pricing:
        vid = row["vendor_id"]
        if vid not in VENDOR_IDS:
            continue
        apn = row.get("apn") or ""
        price = row.get("price")
        if not apn and price is None:
            continue
        can_id = id_to_canonical.get(row["item_id"], row["item_id"])
        pulled_at = price_list_pulled_at.get(row.get("price_list_id")) or row.get("pulled_at")
        vendor_prices[can_id][vid] = {
            "apn": apn,
            "price": price,
            "unit_basis": row.get("unit_basis"),
            "unit_quantity": row.get("unit_quantity"),
            "unit_price": row.get("unit_price"),
            "unit_note": row.get("unit_note"),
            "vendor_item_name": row.get("vendor_item_name"),
            "pulled_at": pulled_at,
        }

    for (can_id, vid), row in CATALOG_ONLY_MATCHES.items():
        vendor_prices.setdefault(can_id, {}).setdefault(vid, row)

    return canonical_items, dict(vendor_prices)


def assign_op_ids(items):
    cat_counter = {}
    for item in items:
        cid = item["category_id"] or 0
        code = CAT_CODE.get(cid, "XX")
        cat_counter[cid] = cat_counter.get(cid, 0) + 1
        item["op_id"] = f"OP-{code}{cat_counter[cid]:03d}"
    return items


CSS = """
:root{--usf:#0f8f4f;--pfg:#111111;--syc:#1f6feb;--gfs:#d71920;--bg:#f4f5f7;--card:#fff}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:#1a1a2e;overflow-x:auto}
header{background:#1a1a2e;color:#fff;padding:18px 32px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}
header h1{font-size:1.4rem;font-weight:700;letter-spacing:.03em}
header .subtitle{font-size:.85rem;opacity:.65;margin-top:3px}
.sheets-btn{display:inline-flex;align-items:center;gap:7px;padding:8px 16px;background:#0f9d58;color:#fff;border:none;border-radius:6px;font-size:.82rem;font-weight:600;cursor:pointer;text-decoration:none;white-space:nowrap}
.sheets-btn:hover{background:#0b8043}
.sheets-btn svg{width:16px;height:16px;flex-shrink:0}
.legend{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.legend-item{display:flex;align-items:center;gap:6px;font-size:.78rem}
.swatch{width:14px;height:14px;border-radius:3px;flex-shrink:0}
.sw4{background:#d4edda;border:1px solid #28a745}.sw3{background:#d1ecf1;border:1px solid #17a2b8}.sw2{background:#fff3cd;border:1px solid #ffc107}.sw1{background:#f8d7da;border:1px solid #dc3545}.sw0{background:#e9ecef;border:1px solid #adb5bd}
.summary-bar{background:#fff;padding:10px 32px;border-bottom:1px solid #dee2e6;display:flex;gap:24px;flex-wrap:wrap;font-size:.82rem}
.summary-bar span{color:#6c757d}
.summary-bar strong{color:#1a1a2e}
.table-wrap{overflow:visible;padding:20px 24px}
table{width:100%;min-width:860px;border-collapse:separate;border-spacing:0;background:var(--card);box-shadow:0 1px 3px rgba(0,0,0,.12);border-radius:8px;overflow:visible;font-size:.82rem}
thead{position:sticky;top:0;z-index:20}
thead tr{background:#1a1a2e;color:#fff;text-transform:uppercase;font-size:.72rem;letter-spacing:.07em}
thead th{padding:11px 12px;text-align:left;white-space:nowrap;background:#1a1a2e}
th.vnd{min-width:170px;text-align:center}
th.usf{color:#8ee6b0} th.pfg{color:#f8f9fa} th.syc{color:#9ec8ff} th.gfs{color:#ffb3b6}
.cat-row{background:#1a1a2e;color:#e0e0ff;font-weight:700;font-size:.75rem;letter-spacing:.1em;text-transform:uppercase}
.cat-row td{padding:7px 12px}
tbody tr:not(.cat-row){border-bottom:1px solid #e9ecef}
tbody tr:not(.cat-row):hover{filter:brightness(.97)}
td{padding:8px 12px;vertical-align:middle}
td.apn{text-align:center;font-family:'SF Mono','Fira Code',monospace;font-size:.78rem}
td.blank{text-align:center;color:#ced4da}
.cov4{background:#f0faf3}.cov3{background:#f0f8fb}.cov2{background:#fffdf0}.cov1{background:#fff7f7}.cov0{background:#f5f5f5}
.vendor-cell{display:flex;flex-direction:column;align-items:center;gap:3px;line-height:1.2}
.pill{display:inline-block;padding:2px 7px;border-radius:12px;font-size:.72rem;font-weight:600}
.price{font-weight:700;color:#1a1a2e}
.unit-price{font-size:.7rem;color:#495057;font-weight:650}
.vendor-name{max-width:180px;color:#495057;font-size:.7rem;line-height:1.25;text-align:center}
.op-id{font-family:'SF Mono','Fira Code',monospace;font-size:.75rem;color:#6c757d;font-weight:600}
.item-name{font-weight:500}
"""


def fmt_money(value):
    if value is None:
        return ""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return ""

def fmt_unit_price(data):
    value = data.get("unit_price")
    if value is None:
        return ""
    basis = data.get("unit_basis") or "unit"
    try:
        price = float(value)
    except (TypeError, ValueError):
        return ""
    return f"${price:,.4f}/{basis}"



def vendor_cell(data, vid):
    dark, light = VENDOR_COLOR.get(vid, ("#333", "#eee"))
    apn = html.escape(str(data.get("apn") or ""))
    price = fmt_money(data.get("price"))
    vendor_item_name = html.escape(str(data.get("vendor_item_name") or ""))
    parts = ['<div class="vendor-cell">']
    if apn:
        parts.append(f'<span class="pill" style="background:{light};color:{dark}">{apn}</span>')
    if price:
        parts.append(f'<div class="price">{price}</div>')
    unit_price = fmt_unit_price(data)
    if unit_price:
        parts.append(f'<div class="unit-price">{html.escape(unit_price)}</div>')
    if vendor_item_name:
        parts.append(f'<div class="vendor-name">{vendor_item_name}</div>')
    parts.append("</div>")
    return "".join(parts)


def cov_class(n):
    return f"cov{min(n, 4)}"


def build_tsv(canonical_items, vendor_prices):
    headers = ["Category", "On Par ID", "Item Description"]
    for vid in VENDOR_IDS:
        vendor = VENDOR_NAMES[vid]
        headers.extend([
            f"{vendor} Product ID",
            f"{vendor} Case Price",
            f"{vendor} Unit Price",
            f"{vendor} Item Name",
        ])
    rows = ["\t".join(headers)]
    for item in canonical_items:
        cat_id = item["category_id"]
        row = [CAT_NAME.get(cat_id, ""), item["op_id"], item["name"]]
        prices = vendor_prices.get(item["id"], {})
        for vid in VENDOR_IDS:
            data = prices.get(vid, {})
            row.extend([
                str(data.get("apn") or ""),
                fmt_money(data.get("price")),
                fmt_unit_price(data),
                str(data.get("vendor_item_name") or ""),
            ])
        rows.append("\t".join(row))
    return "\n".join(rows)


def build_html(canonical_items, vendor_prices):
    now = datetime.datetime.now().strftime("%B %d, %Y at %I:%M %p")
    total = len(canonical_items)
    counts = [sum(1 for ci in canonical_items if len(vendor_prices.get(ci["id"], {})) == n) for n in range(5)]

    def vendor_count(vid):
        return sum(1 for ci in canonical_items if vid in vendor_prices.get(ci["id"], {}))

    h = [f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>On Par - Item Master</title>
<style>{CSS}</style></head><body>
<header>
  <div><h1>On Par - Item Master</h1>
    <div class="subtitle">Cross-Vendor Coverage &nbsp;.&nbsp; Live data &nbsp;.&nbsp; {now}</div>
  </div>
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
    <div class="legend">
      <div class="legend-item"><div class="swatch sw4"></div>All 4 vendors ({counts[4]})</div>
      <div class="legend-item"><div class="swatch sw3"></div>3 vendors ({counts[3]})</div>
      <div class="legend-item"><div class="swatch sw2"></div>2 vendors ({counts[2]})</div>
      <div class="legend-item"><div class="swatch sw1"></div>1 vendor ({counts[1]})</div>
      <div class="legend-item"><div class="swatch sw0"></div>No match ({counts[0]})</div>
    </div>
    <a class="sheets-btn" href="?format=tsv" download="item_master.tsv">
      <svg viewBox="0 0 24 24" fill="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm-1 7V3.5L18.5 9H13zM8 13h8v1H8v-1zm0 3h8v1H8v-1zm0-6h3v1H8v-1z"/></svg>
      Copy for Google Sheets
    </a>
  </div>
</header>
<div class="summary-bar">
  <div><span>Total items: </span><strong>{total}</strong></div>
  <div><span>US Foods: </span><strong>{vendor_count(1)}</strong></div>
  <div><span>PFG: </span><strong>{vendor_count(2)}</strong></div>
  <div><span>Sysco: </span><strong>{vendor_count(3)}</strong></div>
  <div><span>GFS: </span><strong>{vendor_count(4)}</strong></div>
</div>
<div class="table-wrap"><table>
<thead><tr>
  <th style="min-width:90px">On Par ID</th>
  <th style="min-width:200px">Item Description</th>
  <th class="vnd usf">US Foods</th>
  <th class="vnd pfg">PFG</th>
  <th class="vnd syc">Sysco</th>
  <th class="vnd gfs">GFS</th>
</tr></thead><tbody>''']

    current_cat = None
    for item in canonical_items:
        cat_id = item["category_id"]
        if cat_id != current_cat:
            current_cat = cat_id
            h.append(f'<tr class="cat-row"><td colspan="6">{html.escape(CAT_NAME.get(cat_id, ""))}</td></tr>')
        prices = vendor_prices.get(item["id"], {})
        n = len(prices)
        cells = "".join(
            f'<td class="apn">{vendor_cell(prices[v], v)}</td>' if v in prices else '<td class="blank">-</td>'
            for v in VENDOR_IDS
        )
        h.append(
            f'<tr class="{cov_class(n)}">'
            f'<td class="op-id">{html.escape(item["op_id"])}</td>'
            f'<td class="item-name">{html.escape(item["name"])}</td>'
            f'{cells}</tr>'
        )

    h.append("</tbody></table></div></body></html>")
    return "".join(h)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(qs)
        fmt = params.get("format", ["html"])[0].lower()

        try:
            canonical_items, vendor_prices = load_data()
            canonical_items = assign_op_ids(canonical_items)
        except Exception:
            import traceback
            payload = traceback.format_exc().encode()
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if fmt == "tsv":
            body = build_tsv(canonical_items, vendor_prices)
            payload = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/tab-separated-values; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="item_master.tsv"')
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
        else:
            body = build_html(canonical_items, vendor_prices)
            payload = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()

        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass
