"""
GET /api/item_master
====================
Returns the live cross-vendor item master as text/html.
Queries Supabase on every request — always shows current data.

Columns: On Par ID | Item Description | US Foods # | PFG # | Sysco # | GFS #
Grouped by category, color-coded by vendor coverage.
"""

import json
import os
import datetime
import urllib.request
from collections import defaultdict
from http.server import BaseHTTPRequestHandler

# ── Config ────────────────────────────────────────────────────────────────────

SB_URL = os.environ.get("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_HDRS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Accept":        "application/json",
}

VENDOR_IDS   = [1, 2, 3, 4]
VENDOR_NAMES = {1: "US Foods", 2: "PFG", 3: "Sysco", 4: "GFS"}
VENDOR_ABBR  = {1: "USF",      2: "PFG", 3: "SYC",   4: "GFS"}
VENDOR_COLOR = {
    1: ("#1d4e89", "#dce8f8"),
    2: ("#b5451b", "#fce8e0"),
    3: ("#1a6b3c", "#ddf3e8"),
    4: ("#7a5c00", "#fdf3d0"),
}
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
CAT_CODE = {cid: code for cid, _, code in CATEGORIES}
CAT_NAME = {cid: name for cid, name, _ in CATEGORIES}

# ── Supabase ──────────────────────────────────────────────────────────────────

def sb_get(path):
    req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", headers=SB_HDRS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

# ── Data ──────────────────────────────────────────────────────────────────────

def load_data():
    raw_items = sb_get("items?select=id,name,category_id&order=id.asc")

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
        canonical_items.append({
            "id":          can_id,
            "all_ids":     ids,
            "name":        item["name"],
            "category_id": item["category_id"],
        })

    canonical_items.sort(key=lambda x: (x["category_id"] or 99, x["name"].lower()))

    id_to_canonical = {}
    for ci in canonical_items:
        for iid in ci["all_ids"]:
            id_to_canonical[iid] = ci["id"]

    # Best APN per (canonical_id, vendor_id) — highest price_list_id wins
    all_pricing = sb_get(
        "pricing?select=item_id,vendor_id,apn,price_list_id"
        "&order=price_list_id.asc"
    )
    vendor_apns = defaultdict(dict)
    for row in all_pricing:
        vid = row["vendor_id"]
        if vid not in VENDOR_IDS:
            continue
        apn = row.get("apn") or ""
        if not apn:
            continue
        can_id = id_to_canonical.get(row["item_id"], row["item_id"])
        vendor_apns[can_id][vid] = apn

    return canonical_items, dict(vendor_apns)

def assign_op_ids(items):
    cat_counter = {}
    for item in items:
        cid  = item["category_id"] or 0
        code = CAT_CODE.get(cid, "XX")
        cat_counter[cid] = cat_counter.get(cid, 0) + 1
        item["op_id"] = f"OP-{code}{cat_counter[cid]:03d}"
    return items

# ── HTML ──────────────────────────────────────────────────────────────────────

CSS = """
:root{--usf:#1d4e89;--pfg:#b5451b;--syc:#1a6b3c;--gfs:#7a5c00;--bg:#f4f5f7;--card:#fff}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:#1a1a2e}
header{background:#1a1a2e;color:#fff;padding:18px 32px;display:flex;justify-content:space-between;align-items:center}
header h1{font-size:1.4rem;font-weight:700;letter-spacing:.03em}
header .subtitle{font-size:.85rem;opacity:.65;margin-top:3px}
.legend{display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.legend-item{display:flex;align-items:center;gap:6px;font-size:.78rem}
.swatch{width:14px;height:14px;border-radius:3px;flex-shrink:0}
.sw4{background:#d4edda;border:1px solid #28a745}
.sw3{background:#d1ecf1;border:1px solid #17a2b8}
.sw2{background:#fff3cd;border:1px solid #ffc107}
.sw1{background:#f8d7da;border:1px solid #dc3545}
.sw0{background:#e9ecef;border:1px solid #adb5bd}
.summary-bar{background:#fff;padding:10px 32px;border-bottom:1px solid #dee2e6;display:flex;gap:24px;flex-wrap:wrap;font-size:.82rem}
.summary-bar span{color:#6c757d}
.summary-bar strong{color:#1a1a2e}
.table-wrap{overflow-x:auto;padding:20px 24px}
table{width:100%;border-collapse:collapse;background:var(--card);box-shadow:0 1px 3px rgba(0,0,0,.12);border-radius:8px;overflow:hidden;font-size:.82rem}
thead tr{background:#1a1a2e;color:#fff;text-transform:uppercase;font-size:.72rem;letter-spacing:.07em}
thead th{padding:11px 12px;text-align:left;white-space:nowrap}
th.vnd{min-width:110px;text-align:center}
th.usf{color:#9ec8ff} th.pfg{color:#ffc9a8} th.syc{color:#a8f0c8} th.gfs{color:#ffe08a}
.cat-row{background:#1a1a2e;color:#e0e0ff;font-weight:700;font-size:.75rem;letter-spacing:.1em;text-transform:uppercase}
.cat-row td{padding:7px 12px}
tbody tr:not(.cat-row){border-bottom:1px solid #e9ecef}
tbody tr:not(.cat-row):hover{filter:brightness(.97)}
td{padding:8px 12px;vertical-align:middle}
td.apn{text-align:center;font-family:'SF Mono','Fira Code',monospace;font-size:.78rem}
td.blank{text-align:center;color:#ced4da}
.cov4{background:#f0faf3} .cov3{background:#f0f8fb} .cov2{background:#fffdf0} .cov1{background:#fff7f7} .cov0{background:#f5f5f5}
.pill{display:inline-block;padding:2px 7px;border-radius:12px;font-size:.72rem;font-weight:600}
.op-id{font-family:'SF Mono','Fira Code',monospace;font-size:.75rem;color:#6c757d;font-weight:600}
.item-name{font-weight:500}
"""

def pill(apn, vid):
    dark, light = VENDOR_COLOR.get(vid, ("#333","#eee"))
    return f'<span class="pill" style="background:{light};color:{dark}">{apn}</span>'

def cov_class(n):
    return f"cov{min(n, 4)}"

def build_html(canonical_items, vendor_apns):
    now     = datetime.datetime.now().strftime("%B %d, %Y at %I:%M %p")
    total   = len(canonical_items)
    counts  = [sum(1 for ci in canonical_items
                   if len(vendor_apns.get(ci["id"], {})) == n)
               for n in range(5)]

    def vendor_count(vid):
        return sum(1 for ci in canonical_items if vid in vendor_apns.get(ci["id"], {}))

    h = [f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>On Par — Item Master</title>
<style>{CSS}</style></head><body>
<header>
  <div><h1>On Par — Item Master</h1>
    <div class="subtitle">Cross-Vendor Coverage &nbsp;·&nbsp; Live data &nbsp;·&nbsp; {now}</div>
  </div>
  <div class="legend">
    <div class="legend-item"><div class="swatch sw4"></div>All 4 vendors ({counts[4]})</div>
    <div class="legend-item"><div class="swatch sw3"></div>3 vendors ({counts[3]})</div>
    <div class="legend-item"><div class="swatch sw2"></div>2 vendors ({counts[2]})</div>
    <div class="legend-item"><div class="swatch sw1"></div>1 vendor ({counts[1]})</div>
    <div class="legend-item"><div class="swatch sw0"></div>No match ({counts[0]})</div>
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
</tr></thead><tbody>"""]

    current_cat = None
    for item in canonical_items:
        cat_id = item["category_id"]
        if cat_id != current_cat:
            current_cat = cat_id
            h.append(f'<tr class="cat-row"><td colspan="6">{CAT_NAME.get(cat_id, "")}</td></tr>')
        apns  = vendor_apns.get(item["id"], {})
        n     = len(apns)
        cells = "".join(
            f'<td class="apn">{pill(apns[v], v)}</td>' if v in apns else '<td class="blank">—</td>'
            for v in [1, 2, 3, 4]
        )
        h.append(f'<tr class="{cov_class(n)}">'
                 f'<td class="op-id">{item["op_id"]}</td>'
                 f'<td class="item-name">{item["name"]}</td>'
                 f'{cells}</tr>')

    h.append("</tbody></table></div></body></html>")
    return "".join(h)

# ── Vercel handler ────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        try:
            canonical_items, vendor_apns = load_data()
            canonical_items = assign_op_ids(canonical_items)
            html = build_html(canonical_items, vendor_apns)
        except Exception as e:
            import traceback
            payload = traceback.format_exc().encode()
            self.send_response(500)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass
