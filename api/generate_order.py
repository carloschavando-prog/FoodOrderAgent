"""
POST /api/generate_order
========================
Accepts JSON: { "item_name": on_hand_qty, ... }
Returns:      text/html  — the complete Weekly Food Order page

Vercel env vars required:
  SUPABASE_URL   https://gnkwdoohzspomvdshzge.supabase.co
  SUPABASE_KEY   sb_publishable_...
"""

import json
import math
import os
import re
import datetime
import urllib.request
from collections import defaultdict
from http.server import BaseHTTPRequestHandler

# ── Config ────────────────────────────────────────────────────────────────────

SB_URL  = os.environ.get("SUPABASE_URL",         "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY  = os.environ.get("SUPABASE_KEY",         "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_SKEY = os.environ.get("SUPABASE_SERVICE_KEY", SB_KEY)
SB_HDRS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Accept":        "application/json",
}

BROADLINER_IDS = [1, 2, 3, 4]
VENDOR_NAMES   = {1: "US Foods", 2: "PFG", 3: "Sysco", 4: "GFS"}
VENDOR_ABBR    = {1: "USF",      2: "PFG", 3: "SYC",   4: "GFS"}
VENDOR_COLOR   = {
    1: ("#1d4e89", "#dce8f8"),
    2: ("#b5451b", "#fce8e0"),
    3: ("#1a6b3c", "#ddf3e8"),
    4: ("#7a5c00", "#fdf3d0"),
}
MINIMUMS = {
    1: ("cases",   20),
    2: ("cases",   20),
    3: ("cases",   15),
    4: ("dollars", 750),
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

# ── Supabase ──────────────────────────────────────────────────────────────────

def sb_get(path):
    req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", headers=SB_HDRS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())

def save_inventory_snapshot(on_hand, canonical_items):
    """
    Persist the inventory count to Supabase for food cost tracking.
    Creates one inventory_snapshots row + one inventory_snapshot_items row per item.
    Non-fatal — errors are swallowed so a DB issue never blocks order generation.
    Returns snapshot_id or None.
    """
    try:
        hdrs = {
            "apikey":        SB_SKEY,
            "Authorization": f"Bearer {SB_SKEY}",
            "Content-Type":  "application/json",
            "Prefer":        "return=representation",
        }
        # 1. Create snapshot header row
        req = urllib.request.Request(
            f"{SB_URL}/rest/v1/inventory_snapshots",
            data=json.dumps({"notes": "Auto-saved from weekly order generation"}).encode(),
            headers=hdrs, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            snap = json.loads(r.read())
        snapshot_id = snap[0]["id"]

        # 2. Build name → canonical item_id map
        name_to_id = {ci["name"].lower().strip(): ci["id"] for ci in canonical_items}

        # 3. Build item rows
        snap_items = [
            {
                "snapshot_id": snapshot_id,
                "item_id":     name_to_id.get(name),
                "item_name":   name,
                "on_hand_qty": qty,
            }
            for name, qty in on_hand.items()
        ]

        if snap_items:
            req2 = urllib.request.Request(
                f"{SB_URL}/rest/v1/inventory_snapshot_items",
                data=json.dumps(snap_items).encode(),
                headers=hdrs, method="POST"
            )
            with urllib.request.urlopen(req2, timeout=15) as r:
                r.read()

        return snapshot_id
    except Exception:
        return None   # non-fatal

# ── Data loading ──────────────────────────────────────────────────────────────

def load_data(on_hand):
    """
    on_hand: {item_name_lower: float}  — on-hand count from inventory sheet
    Returns canonical_items list + best_prices dict
    """
    raw_items = sb_get(
        "items?select=id,name,category_id,pack_size,par_level,"
        "preferred_vendor_id&order=id.asc"
    )

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
        par    = float(item.get("par_level") or 0)

        oh = on_hand.get(lower_name)
        if oh is not None:
            qty = max(0.0, math.ceil(par - float(oh)))
        else:
            qty = par  # uncounted → full par

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

    canonical_items.sort(key=lambda x: (x["category_id"] or 99, x["name"].lower()))

    id_to_canonical = {}
    for ci in canonical_items:
        for iid in ci["all_ids"]:
            id_to_canonical[iid] = ci["id"]

    # Load pricing — newest write per (canonical_id, vendor_id) wins
    all_pricing = sb_get(
        "pricing?select=item_id,vendor_id,apn,price,price_list_id"
        "&order=price_list_id.asc"
    )

    best_prices = defaultdict(dict)
    for row in all_pricing:
        vid = row["vendor_id"]
        if vid not in BROADLINER_IDS:
            continue
        price = row.get("price")
        if price is None:
            continue
        apn    = row.get("apn") or ""
        can_id = id_to_canonical.get(row["item_id"], row["item_id"])
        best_prices[can_id][vid] = {"price": float(price), "apn": apn}

    return canonical_items, dict(best_prices)

# ── Optimizer ─────────────────────────────────────────────────────────────────

def meets_minimum(vid, cases, spend):
    min_type, min_val = MINIMUMS[vid]
    return spend >= min_val if min_type == "dollars" else cases >= min_val

def calc_totals(assignment, items_by_id, best_prices):
    vendor_items = defaultdict(list)
    vendor_cases = defaultdict(int)
    vendor_spend = defaultdict(float)
    for can_id, vid in assignment.items():
        item  = items_by_id[can_id]
        cases = max(1, int(item["order_qty"]))
        pdata = best_prices[can_id][vid]
        price = pdata["price"]
        apn   = pdata.get("apn", "")
        vendor_items[vid].append({
            "item": item, "cases": cases, "price": price,
            "apn": apn, "subtotal": round(cases * price, 2),
        })
        vendor_cases[vid] += cases
        vendor_spend[vid] += cases * price
    return dict(vendor_items), dict(vendor_cases), dict(vendor_spend)

def optimize_basket(canonical_items, best_prices):
    items_by_id = {ci["id"]: ci for ci in canonical_items}
    active = set(BROADLINER_IDS)
    notes  = []
    dropped = set()

    for _ in range(10):
        assignment = {}
        for ci in canonical_items:
            if ci["order_qty"] <= 0:
                continue
            opts = {
                v: best_prices[ci["id"]][v]
                for v in active
                if ci["id"] in best_prices and v in best_prices[ci["id"]]
            }
            if opts:
                assignment[ci["id"]] = min(opts, key=lambda v: opts[v]["price"])

        _, vendor_cases, vendor_spend = calc_totals(assignment, items_by_id, best_prices)

        failing = {
            v for v in active
            if vendor_cases.get(v, 0) > 0
            and not meets_minimum(v, vendor_cases.get(v, 0), vendor_spend.get(v, 0.0))
        }
        if not failing:
            break

        for vid in sorted(failing, key=lambda v: vendor_spend.get(v, 0)):
            cases = vendor_cases.get(vid, 0)
            spend = vendor_spend.get(vid, 0.0)
            min_type, min_val = MINIMUMS[vid]
            shortfall = (f"${spend:,.0f}/${min_val:,.0f}"
                         if min_type == "dollars"
                         else f"{cases}/{min_val} cases")
            notes.append(
                f"{VENDOR_NAMES[vid]} dropped — minimum not met ({shortfall}). "
                "Items reassigned to next cheapest vendor."
            )
            active.discard(vid)
            dropped.add(vid)

    unassigned = [ci for ci in canonical_items
                  if ci["order_qty"] > 0 and ci["id"] not in assignment]

    return assignment, dropped, unassigned, notes

def compute_savings(assignment, canonical_items, best_prices):
    items_by_id = {ci["id"]: ci for ci in canonical_items}
    rows        = []
    total_saved = 0.0
    for can_id, vid in assignment.items():
        item   = items_by_id[can_id]
        cases  = max(1, int(item["order_qty"]))
        prices = best_prices.get(can_id, {})
        if not prices:
            continue
        paid_price = prices[vid]["price"]
        all_prices = {v: d["price"] for v, d in prices.items()}
        max_price  = max(all_prices.values())
        max_vendor = max(all_prices, key=all_prices.get)
        saved = round((max_price - paid_price) * cases, 2)
        total_saved += saved
        rows.append({
            "item": item, "cases": cases,
            "chosen_vid": vid, "paid_price": paid_price,
            "max_price": max_price, "max_vendor": max_vendor,
            "saved": saved, "all_prices": all_prices,
            "competing": len(all_prices) > 1,
        })
    rows.sort(key=lambda r: r["saved"], reverse=True)
    return rows, round(total_saved, 2)

# ── HTML ──────────────────────────────────────────────────────────────────────

CSS = """
:root{--bg:#f0f2f5;--card:#fff;--text:#1a1a2e;--muted:#6c757d;--border:#dee2e6;--green:#198754;--red:#dc3545}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);font-size:13px}
.page-header{background:#1a1a2e;color:#fff;padding:16px 32px;display:flex;justify-content:space-between;align-items:flex-end}
.page-header h1{font-size:1.5rem;font-weight:700;letter-spacing:.04em}
.page-header .meta{font-size:.78rem;opacity:.6;margin-top:4px}
.summary-bar{background:#fff;border-bottom:1px solid var(--border);padding:10px 32px;display:flex;gap:28px;flex-wrap:wrap}
.summary-bar .stat{display:flex;flex-direction:column}
.summary-bar .stat span{font-size:.72rem;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.summary-bar .stat strong{font-size:1rem;font-weight:700}
.content{padding:20px 24px;display:flex;flex-direction:column;gap:20px}
.vendor-card{background:var(--card);border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.1);overflow:hidden}
.vendor-header{padding:12px 18px;display:flex;justify-content:space-between;align-items:center}
.vendor-header h2{font-size:1rem;font-weight:700}
.v-stats{display:flex;gap:20px;font-size:.8rem}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:.7rem;font-weight:700;letter-spacing:.04em}
.badge-ok{background:#d4edda;color:#155724}
.badge-warn{background:#fff3cd;color:#856404}
table{width:100%;border-collapse:collapse}
thead tr{background:rgba(0,0,0,.04)}
thead th{padding:7px 12px;text-align:left;font-size:.7rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);white-space:nowrap}
th.r,td.r{text-align:right}
th.c,td.c{text-align:center}
tbody td{padding:7px 12px;border-top:1px solid var(--border)}
tbody tr:hover{background:rgba(0,0,0,.02)}
td.item-name{font-weight:500}
td.pack,td.apn{color:var(--muted);font-size:.78rem}
td.apn{font-family:'SF Mono','Fira Code',monospace}
td.sub{font-weight:600}
.vendor-footer{padding:10px 18px;background:rgba(0,0,0,.03);display:flex;justify-content:flex-end;gap:24px;font-size:.82rem}
.vendor-footer .total-val{font-weight:700;font-size:1rem}
tr.cat-header td{background:#f8f9fa;font-weight:700;font-size:.72rem;text-transform:uppercase;letter-spacing:.07em;color:#495057;padding:5px 12px;border-top:2px solid var(--border)}
.consol-box{background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:12px 18px;font-size:.82rem;color:#664d03}
.consol-box strong{display:block;margin-bottom:4px}
.manual-card{background:var(--card);border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.1);overflow:hidden}
.manual-header{background:#495057;color:#fff;padding:12px 18px;font-size:1rem;font-weight:700}
.savings-card{background:var(--card);border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.1);overflow:hidden}
.savings-header{background:#1a1a2e;color:#fff;padding:12px 18px;display:flex;justify-content:space-between;align-items:center}
.savings-header h2{font-size:1rem;font-weight:700}
.savings-total{font-size:1.1rem;font-weight:700;color:#4ade80}
td.save-pos{color:#198754;font-weight:600}
td.save-zero{color:var(--muted)}
.pill{display:inline-block;padding:1px 6px;border-radius:10px;font-size:.7rem;font-weight:600;margin:1px}
@media print{body{background:#fff}.vendor-card,.manual-card,.savings-card{box-shadow:none;border:1px solid #ddd}.content{padding:8px;gap:12px}}
.order-btn{background:#198754;color:#fff;border:none;padding:9px 22px;border-radius:8px;font-size:.88rem;font-weight:700;cursor:pointer;letter-spacing:.02em;white-space:nowrap}
.order-btn:hover{background:#146c43}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;align-items:center;justify-content:center}
.modal-box{background:#fff;border-radius:12px;padding:28px;max-width:540px;width:92%;box-shadow:0 12px 48px rgba(0,0,0,.3);max-height:90vh;overflow-y:auto}
.modal-title{font-size:1.15rem;font-weight:700;margin-bottom:14px;color:#1a1a2e}
.vendor-summary{display:flex;flex-direction:column;gap:8px;margin:12px 0}
.vs-row{display:flex;justify-content:space-between;align-items:center;padding:10px 14px;border-radius:7px}
.vs-name{font-weight:700;font-size:.9rem}
.vs-detail{font-size:.8rem;color:#555}
.order-warning{background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:10px 14px;font-size:.82rem;color:#664d03;margin:12px 0}
.modal-actions{display:flex;gap:12px;margin-top:16px;justify-content:flex-end}
.btn-cancel{background:#e9ecef;color:#333;border:none;padding:9px 20px;border-radius:7px;cursor:pointer;font-weight:600;font-size:.88rem}
.btn-cancel:hover{background:#ced4da}
.btn-submit{background:#198754;color:#fff;border:none;padding:9px 22px;border-radius:7px;cursor:pointer;font-weight:700;font-size:.88rem}
.btn-submit:hover{background:#146c43}
.btn-submit:disabled{background:#6c757d;cursor:not-allowed}
.status-row{display:flex;align-items:flex-start;gap:10px;padding:11px 14px;border-radius:7px;margin-bottom:8px;background:#f8f9fa;font-size:.88rem;line-height:1.5}
.status-ok{background:#d4edda!important;color:#155724}
.status-err{background:#f8d7da!important;color:#721c24}
.spin{animation:_spin 1s linear infinite;display:inline-block}
@keyframes _spin{to{transform:rotate(360deg)}}
"""

def fmt_money(v): return f"${v:,.2f}"
def _pill(text, bg, fg): return f'<span class="pill" style="background:{bg};color:{fg}">{text}</span>'
def vendor_pill(vid):
    d, l = VENDOR_COLOR.get(vid, ("#333","#eee"))
    return _pill(VENDOR_ABBR.get(vid, str(vid)), l, d)

def build_html(assignment, dropped, unassigned, notes,
               canonical_items, best_prices, savings_rows, total_saved):
    items_by_id = {ci["id"]: ci for ci in canonical_items}
    vendor_items, vendor_cases, vendor_spend = calc_totals(assignment, items_by_id, best_prices)

    # ── Build per-vendor order payload for "Place Orders" button ─────────────
    _order_data = {}
    for _vid in BROADLINER_IDS:
        _entries = vendor_items.get(_vid, [])
        _vi = []
        for _e in _entries:
            _apn = (_e.get("apn") or "").strip()
            if not _apn:
                continue
            _qty = _e["cases"]
            if _vid == 1:    # US Foods — productNumber (numeric)
                try:    _vi.append({"productNumber": int(_apn), "qty": _qty})
                except: _vi.append({"productNumber": _apn,     "qty": _qty})
            elif _vid == 2:  # PFG — apn (numeric product number; resolved to UUID server-side)
                _vi.append({"apn": _apn, "qty": _qty, "uomType": "CS"})
            elif _vid == 3:  # Sysco — productId
                _vi.append({"productId": _apn, "qty": _qty})
            elif _vid == 4:  # GFS — materialNumber
                _vi.append({"materialNumber": _apn, "qty": _qty})
        if _vi:
            _order_data[str(_vid)] = _vi
    _order_data_json = json.dumps(_order_data, separators=(",", ":"))
    _vnames_json     = json.dumps({str(k): v for k, v in VENDOR_NAMES.items()})

    now        = datetime.datetime.now()
    date_str   = now.strftime("%A, %B %d, %Y")
    time_str   = now.strftime("%I:%M %p")
    grand      = sum(vendor_spend.values())
    total_cases = sum(vendor_cases.values())

    def sorted_by_cat(entries):
        cat_map = defaultdict(list)
        for e in entries: cat_map[e["item"]["category_id"]].append(e)
        result = []
        for cat_id, cat_name in CAT_ORDER:
            grp = cat_map.get(cat_id, [])
            if grp:
                grp.sort(key=lambda e: e["item"]["name"].lower())
                result.append((cat_name, grp))
        return result

    h = [f"""<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Weekly Food Order — {date_str}</title>
<style>{CSS}</style></head><body>
<div class="page-header">
  <div><h1>📋 Weekly Food Order</h1>
    <div class="meta">On Par Bar &amp; Grill &nbsp;·&nbsp; {date_str} &nbsp;·&nbsp; {time_str} &nbsp;·&nbsp; From Inventory Count</div>
  </div>
  <div style="display:flex;align-items:center;gap:18px">
    <div style="text-align:right;font-size:.85rem;opacity:.8">
      {len(assignment)} items &nbsp;|&nbsp; {total_cases} cases &nbsp;|&nbsp;
      <strong style="font-size:1.1rem;opacity:1">{fmt_money(grand)}</strong>
    </div>
    <button class="order-btn" onclick="openOrderModal()">📦 Place Orders</button>
  </div>
</div>
<div class="summary-bar">"""]

    for vid in BROADLINER_IDS:
        cases = vendor_cases.get(vid, 0)
        spend = vendor_spend.get(vid, 0.0)
        if vid in dropped:
            status = '<span class="badge badge-warn">DROPPED</span>'
        elif cases == 0:
            status = '<span class="badge" style="background:#e9ecef;color:#6c757d">NO ITEMS</span>'
        elif meets_minimum(vid, cases, spend):
            status = '<span class="badge badge-ok">MIN MET ✓</span>'
        else:
            status = '<span class="badge badge-warn">BELOW MIN</span>'
        dark, light = VENDOR_COLOR[vid]
        h.append(f'<div class="stat"><span style="color:{dark};font-weight:700">{VENDOR_NAMES[vid]}</span>'
                 f'<strong>{fmt_money(spend)}</strong><span>{cases} cases &nbsp; {status}</span></div>')

    if total_saved > 0:
        h.append(f'<div class="stat" style="margin-left:auto;text-align:right">'
                 f'<span>Savings vs Worst</span>'
                 f'<strong style="color:#198754">{fmt_money(total_saved)}</strong>'
                 f'<span>vs always buying highest price</span></div>')

    h.append('</div>\n<div class="content">\n')

    if notes:
        h.append('<div class="consol-box"><strong>⚠️ Basket Consolidation Notes</strong>')
        for n in notes: h.append(f"<div>• {n}</div>")
        h.append("</div>\n")

    for vid in BROADLINER_IDS:
        entries = vendor_items.get(vid, [])
        if not entries: continue
        cases = vendor_cases.get(vid, 0)
        spend = vendor_spend.get(vid, 0.0)
        dark, light = VENDOR_COLOR[vid]
        if meets_minimum(vid, cases, spend):
            badge = '<span class="badge badge-ok">Minimum Met ✓</span>'
        else:
            min_type, min_val = MINIMUMS[vid]
            need = fmt_money(min_val) if min_type == "dollars" else f"{min_val} cases"
            badge = f'<span class="badge badge-warn">⚠️ Need {need}</span>'

        h.append(f'<div class="vendor-card">'
                 f'<div class="vendor-header" style="background:{dark};color:#fff">'
                 f'<h2>{VENDOR_NAMES[vid]}</h2>'
                 f'<div class="v-stats"><div><span>Cases: </span><strong>{cases}</strong></div>'
                 f'<div><span>Total: </span><strong>{fmt_money(spend)}</strong></div>'
                 f'<div>{badge}</div></div></div>'
                 f'<table><thead><tr>'
                 f'<th>Item</th><th>Pack</th><th class="c">Vendor #</th>'
                 f'<th class="c">Cases</th><th class="r">Unit Price</th><th class="r">Subtotal</th>'
                 f'</tr></thead><tbody>')

        for cat_name, grp in sorted_by_cat(entries):
            h.append(f'<tr class="cat-header"><td colspan="6">{cat_name}</td></tr>')
            for e in grp:
                item = e["item"]
                h.append(f'<tr><td class="item-name">{item["name"]}</td>'
                         f'<td class="pack">{item["pack_size"]}</td>'
                         f'<td class="apn c">{e["apn"] or "—"}</td>'
                         f'<td class="c">{e["cases"]}</td>'
                         f'<td class="r">{fmt_money(e["price"])}</td>'
                         f'<td class="sub r">{fmt_money(e["subtotal"])}</td></tr>')

        h.append(f'</tbody></table>'
                 f'<div class="vendor-footer">'
                 f'<span style="color:var(--muted)">Order Total:</span>'
                 f'<span class="total-val">{fmt_money(spend)}</span>'
                 f'</div></div>')

    if unassigned:
        other_vendors = {5: "I Supply", 6: "Markets Depot", 7: "Meat Church"}
        h.append('<div class="manual-card"><div class="manual-header">🛒 Manual / Other Vendors</div>'
                 '<table><thead><tr><th>Item</th><th>Pack</th><th class="c">Cases</th>'
                 '<th>Preferred Vendor</th></tr></thead><tbody>')
        last_cat = None
        for ci in sorted(unassigned, key=lambda x: (x["category_id"], x["name"].lower())):
            if ci["category_id"] != last_cat:
                last_cat = ci["category_id"]
                cat_name = dict(CAT_ORDER).get(ci["category_id"], "")
                h.append(f'<tr class="cat-header"><td colspan="4">{cat_name}</td></tr>')
            pref_vid  = ci["preferred_vid"]
            pref_name = VENDOR_NAMES.get(pref_vid) or other_vendors.get(pref_vid, f"Vendor {pref_vid}")
            cases = int(ci["order_qty"]) if ci["order_qty"] > 0 else "?"
            h.append(f'<tr><td class="item-name">{ci["name"]}</td>'
                     f'<td class="pack">{ci["pack_size"]}</td>'
                     f'<td class="c">{cases}</td><td>{pref_name}</td></tr>')
        h.append('</tbody></table></div>')

    competing = [r for r in savings_rows if r["competing"] and r["saved"] >= 0.01]
    if competing:
        total_paid = sum(r["cases"] * r["paid_price"] for r in competing)
        total_max  = sum(r["cases"] * r["max_price"]  for r in competing)
        h.append(f'<div class="savings-card">'
                 f'<div class="savings-header">'
                 f'<h2>💰 Savings — Cheapest vs Most Expensive Available</h2>'
                 f'<div class="savings-total">Total Saved: {fmt_money(total_saved)}</div>'
                 f'</div>'
                 f'<table><thead><tr>'
                 f'<th>Item</th><th class="c">Cases</th><th>Ordered From</th>'
                 f'<th class="r">Price Paid</th><th>All Prices</th>'
                 f'<th class="r">Highest</th><th class="r">Saved</th>'
                 f'</tr></thead><tbody>')
        for r in competing:
            dark, light = VENDOR_COLOR.get(r["chosen_vid"], ("#333","#eee"))
            chips = "".join(
                _pill(f'{VENDOR_ABBR[v]} {fmt_money(p)}',
                      VENDOR_COLOR.get(v, ("#333","#eee"))[1],
                      VENDOR_COLOR.get(v, ("#333","#eee"))[0])
                for v, p in sorted(r["all_prices"].items(), key=lambda x: x[1])
            )
            saved_td = (f'<td class="save-pos r">{fmt_money(r["saved"])}</td>'
                        if r["saved"] > 0 else '<td class="save-zero r">—</td>')
            h.append(f'<tr><td class="item-name">{r["item"]["name"]}</td>'
                     f'<td class="c">{r["cases"]}</td>'
                     f'<td>{_pill(VENDOR_NAMES.get(r["chosen_vid"],""), light, dark)}</td>'
                     f'<td class="r">{fmt_money(r["paid_price"])}</td>'
                     f'<td style="white-space:nowrap">{chips}</td>'
                     f'<td class="r" style="color:#dc3545">{fmt_money(r["max_price"])}</td>'
                     f'{saved_td}</tr>')
        h.append(f'<tr style="background:#f8f9fa;font-weight:700;border-top:2px solid #dee2e6">'
                 f'<td colspan="3">TOTAL ({len(competing)} items with competing prices)</td>'
                 f'<td class="r">{fmt_money(total_paid)}</td><td></td>'
                 f'<td class="r" style="color:#dc3545">{fmt_money(total_max)}</td>'
                 f'<td class="save-pos r">{fmt_money(total_saved)}</td></tr>')
        h.append('</tbody></table></div>')

    # ── Confirmation modal + Place Orders JavaScript ──────────────────────────
    _modal_rows = []
    for _vid in BROADLINER_IDS:
        _vd = _order_data.get(str(_vid))
        if not _vd:
            continue
        _dark, _light = VENDOR_COLOR[_vid]
        _n     = len(_vd)
        _spend = fmt_money(vendor_spend.get(_vid, 0.0))
        _cases = vendor_cases.get(_vid, 0)
        _modal_rows.append(
            f'<div class="vs-row" style="background:{_light}">'
            f'<span class="vs-name" style="color:{_dark}">{VENDOR_NAMES[_vid]}</span>'
            f'<span class="vs-detail">{_n} items &nbsp;·&nbsp; {_cases} cases &nbsp;·&nbsp; {_spend}</span>'
            f'</div>'
        )
    _modal_vendor_html = "\n".join(_modal_rows) if _modal_rows else \
        '<p style="color:#6c757d">No vendor items with APNs — cannot auto-place orders.</p>'

    _js = (
        "const ORDER_DATA=" + _order_data_json + ";\n"
        "const VENDOR_NAMES_MAP=" + _vnames_json + ";\n"
        """const ORDER_ENDPOINTS={
  "1":"/api/place_order_usfoods",
  "2":"/api/place_order_pfg",
  "3":"/api/place_order_sysco",
  "4":"/api/place_order_gfs"
};
function openOrderModal(){
  document.getElementById('order-modal').style.display='flex';
}
function closeOrderModal(){
  document.getElementById('order-modal').style.display='none';
  document.getElementById('confirm-section').style.display='block';
  document.getElementById('progress-section').style.display='none';
  document.getElementById('done-actions').style.display='none';
  var sb=document.getElementById('submit-btn');
  if(sb) sb.disabled=false;
}
async function submitOrders(){
  var btn=document.getElementById('submit-btn');
  if(btn) btn.disabled=true;
  document.getElementById('confirm-section').style.display='none';
  document.getElementById('progress-section').style.display='block';
  var vendors=Object.keys(ORDER_DATA);
  var container=document.getElementById('status-rows');
  container.innerHTML='';
  vendors.forEach(function(vid){
    var row=document.createElement('div');
    row.id='vs-'+vid;
    row.className='status-row';
    row.innerHTML='<span class="spin">⏳</span>&nbsp;<strong>'+VENDOR_NAMES_MAP[vid]+'</strong>: Placing order…';
    container.appendChild(row);
  });
  var promises=vendors.map(async function(vid){
    var items=ORDER_DATA[vid];
    var row=document.getElementById('vs-'+vid);
    try{
      var resp=await fetch(ORDER_ENDPOINTS[vid],{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({items:items})
      });
      var data=await resp.json();
      if(data.success){
        var id=data.orderId||data.confirmationNumber||data.orderHeaderId||data.tandemOrderNumber||'';
        var deliv=data.deliveryDate?' · Delivery: '+data.deliveryDate:'';
        row.className='status-row status-ok';
        row.innerHTML='✅ <strong>'+VENDOR_NAMES_MAP[vid]+'</strong>: Order placed!'
          +(id?' (ID '+id+')':'')+deliv;
      }else{
        row.className='status-row status-err';
        row.innerHTML='❌ <strong>'+VENDOR_NAMES_MAP[vid]+'</strong>: '+(data.error||'Unknown error');
      }
    }catch(e){
      row.className='status-row status-err';
      row.innerHTML='❌ <strong>'+VENDOR_NAMES_MAP[vid]+'</strong>: Network error — '+e.message;
    }
  });
  await Promise.all(promises);
  document.getElementById('done-actions').style.display='flex';
}"""
    )

    h.append(
        '<div id="order-modal" class="modal-overlay">'
        '<div class="modal-box">'
        '<div class="modal-title">🛒 Confirm &amp; Place All Orders</div>'
        '<div id="confirm-section">'
        '<p style="color:#555;font-size:.88rem;margin-bottom:8px">'
        'Purchase orders will be submitted to the following vendors:</p>'
        '<div class="vendor-summary">' + _modal_vendor_html + '</div>'
        '<div class="order-warning">'
        '⚠️ Orders are <strong>final</strong> once submitted. '
        'Verify quantities in the table above before confirming.'
        '</div>'
        '<div class="modal-actions">'
        '<button class="btn-cancel" onclick="closeOrderModal()">Cancel</button>'
        '<button id="submit-btn" class="btn-submit" onclick="submitOrders()">'
        'Confirm &amp; Place All Orders</button>'
        '</div></div>'
        '<div id="progress-section" style="display:none">'
        '<p style="color:#555;font-size:.88rem;margin-bottom:12px">'
        'Submitting orders in parallel&hellip;</p>'
        '<div id="status-rows"></div>'
        '<div class="modal-actions" id="done-actions" style="display:none">'
        '<button class="btn-submit" onclick="closeOrderModal()">Done</button>'
        '</div></div>'
        '</div></div>'
        '<script>' + _js + '</script>'
    )

    h.append("</div></body></html>")
    return "".join(h)

# ── Vercel handler ────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b"{}"
        try:
            raw = json.loads(body)
        except Exception as e:
            self._err(400, f"Bad JSON: {e}")
            return

        # Normalise keys to lowercase
        on_hand = {
            k.lower().strip(): float(v)
            for k, v in raw.items()
            if v != "" and v is not None
        }

        try:
            canonical_items, best_prices = load_data(on_hand)
            save_inventory_snapshot(on_hand, canonical_items)   # persist count for food cost
            assignment, dropped, unassigned, notes = optimize_basket(canonical_items, best_prices)
            savings_rows, total_saved = compute_savings(assignment, canonical_items, best_prices)
            html = build_html(
                assignment, dropped, unassigned, notes,
                canonical_items, best_prices,
                savings_rows, total_saved,
            )
        except Exception as e:
            import traceback
            self._err(500, traceback.format_exc())
            return

        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _err(self, code, msg):
        payload = json.dumps({"error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass  # suppress Vercel log noise
