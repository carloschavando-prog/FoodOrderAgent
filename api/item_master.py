"""
GET /api/item_master
====================
Returns the live cross-vendor item master as text/html.
Queries Supabase on every request so it shows current data.

Columns: On Par ID | Item Description | vendor product ID, case cost, unit cost,
one-line description, and last scraped/updated timestamp
Grouped by category, color-coded by vendor coverage.
"""

import json
import os
import datetime
import urllib.request
import urllib.parse
import html
import re
from collections import defaultdict
from http.server import BaseHTTPRequestHandler
from zoneinfo import ZoneInfo

from order_normalization import (
    count_unit_for_item,
    pricing_matches_item_requirements,
    units_per_case,
)
from delivery_pars import REMOVED_ITEM_NAMES
from vendor_restrictions import vendor_allowed_for_item

# ── Config ────────────────────────────────────────────────────────────────────

SB_URL = os.environ.get("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_HDRS = {
    "apikey": SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Accept": "application/json",
}

# GFS (vendor 4) is retained in Supabase as archived history, but is excluded
# from every active Item Master format (HTML, TSV, and inventory JSON).
VENDOR_IDS = [1, 2, 3]
VENDOR_NAMES = {1: "US Foods", 2: "PFG", 3: "Sysco"}
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
SORT_NAME_OVERRIDES_BY_NAME = {
    # Keep this new inventory row directly after Straws and before Styrofoam.
    "16 oz to-go cold cups": "strax 16 oz to-go cold cups",
    # Keep the beverage addition immediately after Daily's Sweet & Sour Mix.
    "vanilla monin": "daily's sweet & sour mix z vanilla monin",
}
ITEM_MASTER_EXCLUDED_NAMES = REMOVED_ITEM_NAMES
SKU_ONLY_VENDOR_RECORDS = {
    # User-confirmed vendor SKUs whose account price has not yet been captured.
    "aluminum 1/3 pans": {
        "vendor_id": 1,
        "apn": "7737075",
        "pack_size": "100 EA",
        "unit_basis": "each",
        "unit_quantity": 100,
        "vendor_item_name": (
            'Monogram Pan, Steamtable Foil 5 LB 1/3 Size 3.31" Deep Aluminum'
        ),
        "pulled_at": "2026-08-25T16:25:00+00:00",
    },
    "solid dish detergent": {
        "vendor_id": 1,
        "apn": "4000885",
        "pack_size": "2/9 LB",
        "unit_basis": "lb",
        "unit_quantity": 18,
        "vendor_item_name": "Detergent, Dishwasher D39 Solid Capsule",
        "pulled_at": "2026-08-25T17:00:13+00:00",
    },
    "ranch dressing": {
        "vendor_id": 3,
        "apn": "1344033",
        "pack_size": "4/1 GAL",
        "unit_basis": "fl oz",
        "unit_quantity": 512,
        "vendor_item_name": "Sysco Classic Dressing Ranch Buttermilk Banquet",
        "pulled_at": "2026-08-26T17:24:00+00:00",
    },
    "mozzarella sticks": {
        "vendor_id": 1,
        "apn": "7332687",
        "pack_size": None,
        "unit_basis": None,
        "unit_quantity": None,
        "vendor_item_name": "Mozzarella Sticks",
        "pulled_at": "2026-08-27T14:30:00+00:00",
    },
    "vanilla monin": {
        "vendor_id": 1,
        "apn": "8231367",
        "pack_size": None,
        "unit_basis": None,
        "unit_quantity": None,
        "vendor_item_name": "Vanilla Monin",
        "pulled_at": "2026-08-27T14:30:00+00:00",
    },
}
STATUS_LABELS = {
    "product_mismatch": "Product mismatch",
    "identity_review": "Identity review needed",
    "pending_approval": "Pending approval",
    "special_order": "Special order",
    "not_found": "Not found",
    "verified": "Verified",
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
        if key in ITEM_MASTER_EXCLUDED_NAMES:
            continue
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
            SORT_NAME_OVERRIDES.get(
                x["id"],
                SORT_NAME_OVERRIDES_BY_NAME.get(
                    x["name"].lower().strip(),
                    x["name"],
                ),
            ).lower(),
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
        "pack_size,unit_basis,unit_quantity,unit_price,unit_note,vendor_item_name"
        "&order=price_list_id.asc"
    )
    vendor_prices = defaultdict(dict)
    price_history = defaultdict(lambda: defaultdict(list))
    for row in all_pricing:
        vid = row["vendor_id"]
        if vid not in VENDOR_IDS:
            continue
        apn = row.get("apn") or ""
        price = row.get("price")
        can_id = id_to_canonical.get(row["item_id"], row["item_id"])
        item = id_to_item.get(can_id)
        if item is None:
            continue
        pulled_at = price_list_pulled_at.get(row.get("price_list_id")) or row.get("pulled_at")
        if apn and price is not None:
            price_history[can_id][vid].append({
                "apn": apn,
                "price": price,
                "pack_size": row.get("pack_size"),
                "vendor_item_name": row.get("vendor_item_name"),
                "pulled_at": pulled_at,
                "price_list_id": row.get("price_list_id"),
            })

        # Only complete, orderable rows count as confirmed vendor matches.
        if (
            not apn
            or price is None
            or not vendor_allowed_for_item(item["name"], vid, apn)
        ):
            continue
        vendor_prices[can_id][vid] = {
            "apn": apn,
            "price": price,
            "pack_size": row.get("pack_size"),
            "unit_basis": row.get("unit_basis"),
            "unit_quantity": row.get("unit_quantity"),
            "unit_price": row.get("unit_price"),
            "unit_note": row.get("unit_note"),
            "vendor_item_name": row.get("vendor_item_name"),
            "pulled_at": pulled_at,
            "history": price_history[can_id][vid],
        }

    for item in canonical_items:
        sku_record = SKU_ONLY_VENDOR_RECORDS.get(item["name"].lower().strip())
        if not sku_record:
            continue
        vid = sku_record["vendor_id"]
        existing = vendor_prices[item["id"]].get(vid)
        if existing and existing.get("apn") == sku_record["apn"]:
            continue
        vendor_prices[item["id"]][vid] = {
            "apn": sku_record["apn"],
            "price": None,
            "pack_size": sku_record.get("pack_size"),
            "unit_basis": sku_record.get("unit_basis"),
            "unit_quantity": sku_record.get("unit_quantity"),
            "unit_price": None,
            "unit_note": "Vendor SKU verified; current numeric account price pending.",
            "vendor_item_name": sku_record.get("vendor_item_name"),
            "pulled_at": sku_record.get("pulled_at"),
            "history": price_history[item["id"]][vid],
        }

    status_rows = sb_get_all(
        "item_vendor_status?select=item_id,vendor_id,apn,status,note,"
        "vendor_item_name,pack_size,price_available,blocks_ordering,"
        "verified_on,source"
    )
    for status_row in status_rows:
        vid = status_row.get("vendor_id")
        if vid not in VENDOR_IDS:
            continue
        can_id = id_to_canonical.get(
            status_row.get("item_id"), status_row.get("item_id")
        )
        if can_id not in id_to_item:
            continue
        apn = str(status_row.get("apn") or "").strip()
        history = price_history[can_id][vid]
        latest = next(
            (
                observation
                for observation in reversed(history)
                if not apn or observation.get("apn") == apn
            ),
            None,
        )
        record = dict(latest or {})
        captured_price = record.get("price")
        record.update({
            "apn": apn,
            "price": (
                captured_price
                if status_row.get("price_available")
                and not status_row.get("blocks_ordering")
                else None
            ),
            "captured_price": captured_price,
            "pack_size": status_row.get("pack_size") or record.get("pack_size"),
            "unit_basis": None,
            "unit_quantity": None,
            "unit_price": None,
            "unit_note": status_row.get("note"),
            "vendor_item_name": (
                status_row.get("vendor_item_name")
                or record.get("vendor_item_name")
            ),
            "pulled_at": record.get("pulled_at"),
            "verified_on": status_row.get("verified_on"),
            "availability": status_row.get("status"),
            "status_note": status_row.get("note"),
            "blocks_ordering": bool(status_row.get("blocks_ordering")),
            "history": history,
            "source": status_row.get("source"),
        })
        vendor_prices[can_id][vid] = record

    # A later observation may have replaced the record object before all of its
    # history was accumulated. Reattach the complete timeline once loading ends.
    for can_id, prices in vendor_prices.items():
        for vid, record in prices.items():
            record["history"] = price_history[can_id][vid]

    return canonical_items, dict(vendor_prices)


def normalized_basis(value):
    basis = str(value or "").lower().strip()
    aliases = {
        "ea": "each",
        "unit": "each",
        "units": "each",
        "count": "each",
        "ounce": "oz",
        "ounces": "oz",
        "fl oz": "oz",
        "fluid ounce": "oz",
        "fluid ounces": "oz",
        "pound": "lb",
        "pounds": "lb",
        "litre": "liter",
        "litres": "liter",
        "liters": "liter",
        "gal": "gallon",
        "gallons": "gallon",
    }
    return aliases.get(basis, basis)


def normalized_unit_price(data):
    basis = normalized_basis(data.get("unit_basis"))
    if not basis:
        return None
    try:
        price = float(data.get("unit_price"))
    except (TypeError, ValueError):
        try:
            case_price = float(data.get("price"))
            quantity = float(data.get("unit_quantity"))
        except (TypeError, ValueError):
            return None
        if quantity <= 0:
            return None
        price = case_price / quantity
    if price <= 0:
        return None
    return price, basis


def cheapest_comparable_quote(prices):
    """Return the lowest normalized price only when at least two quotes compare."""
    by_basis = defaultdict(list)
    for vendor_id, data in prices.items():
        normalized = normalized_unit_price(data)
        if normalized is None or not data.get("pulled_at"):
            continue
        unit_price, basis = normalized
        by_basis[basis].append((vendor_id, unit_price, data))

    comparable_groups = [rows for rows in by_basis.values() if len(rows) >= 2]
    if not comparable_groups:
        return None
    comparable_groups.sort(key=lambda rows: len(rows), reverse=True)
    if len(comparable_groups) > 1 and len(comparable_groups[0]) == len(comparable_groups[1]):
        return None

    winner = min(comparable_groups[0], key=lambda row: row[1])
    return {
        **winner[2],
        "vendor_id": winner[0],
        "unit_price": winner[1],
        "unit_basis": normalized_basis(winner[2].get("unit_basis")),
        "comparable_quotes": len(comparable_groups[0]),
    }


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
.method-note{margin:14px 24px 0;padding:10px 12px;border:1px solid #b9d9c5;border-radius:7px;background:#f5fbf7;color:#385547;font-size:.76rem;line-height:1.4}
.table-wrap{overflow:visible;padding:20px 24px}
table{width:100%;min-width:1510px;border-collapse:separate;border-spacing:0;background:var(--card);box-shadow:0 1px 3px rgba(0,0,0,.12);border-radius:8px;overflow:visible;font-size:.82rem}
thead{position:sticky;top:0;z-index:20}
thead tr{background:#1a1a2e;color:#fff;text-transform:uppercase;font-size:.72rem;letter-spacing:.07em}
thead th{padding:11px 12px;text-align:left;white-space:nowrap;background:#1a1a2e}
th.vnd{min-width:225px;text-align:center}
th.usf{color:#8ee6b0} th.pfg{color:#f8f9fa} th.syc{color:#9ec8ff} th.gfs{color:#ffb3b6}
.cat-row{background:#1a1a2e;color:#e0e0ff;font-weight:700;font-size:.75rem;letter-spacing:.1em;text-transform:uppercase}
.cat-row td{padding:7px 12px}
tbody tr:not(.cat-row){border-bottom:1px solid #e9ecef}
tbody tr:not(.cat-row):hover{filter:brightness(.97)}
td{padding:8px 12px;vertical-align:middle}
td.apn{text-align:left;font-size:.78rem}
td.blank{text-align:center;color:#ced4da}
.cov4{background:#f0faf3}.cov3{background:#f0f8fb}.cov2{background:#fffdf0}.cov1{background:#fff7f7}.cov0{background:#f5f5f5}
.vendor-cell{display:flex;flex-direction:column;align-items:stretch;gap:4px;line-height:1.25;text-align:left}
.field-line{display:grid;grid-template-columns:70px minmax(0,1fr);gap:6px;align-items:start}
.field-label{color:#6c757d;font-size:.65rem;font-weight:700;text-transform:uppercase}
.field-value{min-width:0;overflow-wrap:anywhere}
.pill{display:inline-block;padding:2px 7px;border-radius:12px;font-family:'SF Mono','Fira Code',monospace;font-size:.72rem;font-weight:600}
.price{font-weight:700;color:#1a1a2e}
.unit-price{font-size:.7rem;color:#495057;font-weight:650}
.vendor-name{color:#495057;font-size:.7rem;line-height:1.25}
.updated-at{color:#6c757d;font-size:.67rem;white-space:nowrap}
.status-banner{padding:6px 8px;border-radius:6px;font-size:.7rem;font-weight:750;line-height:1.35}
.status-product_mismatch{background:#ffe7e7;color:#8d1d24;border:1px solid #efb9bd}
.status-identity_review{background:#fff4d6;color:#72540b;border:1px solid #ead188}
.status-pending_approval{background:#fff4d6;color:#72540b;border:1px solid #ead188}
.status-special_order{background:#e8f0ff;color:#254b87;border:1px solid #bed0ee}
.status-not_found{background:#eceff2;color:#4f5962;border:1px solid #cfd5da}
.captured-price{font-size:.7rem;color:#6c757d;font-weight:650}
.price-history{margin-top:3px;border-top:1px solid rgba(0,0,0,.08);padding-top:5px}
.price-history summary{cursor:pointer;color:#3e596f;font-size:.68rem;font-weight:700;list-style-position:outside;margin-left:12px}
.history-table{width:100%;min-width:0;margin-top:6px;border-collapse:collapse;box-shadow:none;border-radius:0;background:transparent;font-size:.66rem}
.history-table thead{position:static}.history-table thead tr{background:transparent}
.history-table th,.history-table td{padding:4px 5px;border-bottom:1px solid rgba(0,0,0,.07);text-align:left;white-space:nowrap;background:transparent;color:#495057;text-transform:none;letter-spacing:0}
.history-table th{font-weight:750;color:#68737d}
.delta-up{color:#a8232c;font-weight:700}.delta-down{color:#187447;font-weight:700}.delta-flat{color:#6c757d}
.best-cell{min-width:230px;background:#f5fbf7}
.best-card{display:flex;flex-direction:column;gap:5px;line-height:1.25}
.best-vendor{font-size:.84rem;font-weight:800;color:#155724}
.best-price{font-size:.92rem;font-weight:800;color:#1a1a2e}
.best-meta{font-size:.68rem;color:#5f6f65}
.best-unavailable{color:#8a9299;font-size:.74rem;line-height:1.35}
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
        return "Not available"
    basis = data.get("unit_basis") or "unit"
    try:
        price = float(value)
    except (TypeError, ValueError):
        return "Not available"
    return f"${price:,.4f}/{basis}"


def fmt_timestamp(value, human=True):
    if not value:
        return "Not available"
    try:
        timestamp = str(value).replace("Z", "+00:00")
        # Python 3.9 only accepts 3 or 6 fractional-second digits, while
        # Postgres may emit any precision from 1 through 6.
        match = re.search(r"\.(\d+)([+-]\d{2}:\d{2})$", timestamp)
        if match:
            micros = match.group(1)[:6].ljust(6, "0")
            timestamp = (
                timestamp[:match.start()]
                + f".{micros}"
                + match.group(2)
            )
        parsed = datetime.datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        eastern = parsed.astimezone(ZoneInfo("America/New_York"))
        if human:
            return eastern.strftime("%b %d, %Y %I:%M %p %Z").replace(" 0", " ")
        return eastern.strftime("%Y-%m-%d %H:%M:%S %Z")
    except (TypeError, ValueError):
        return str(value)


def fmt_date(value):
    if not value:
        return "Not available"
    try:
        parsed = datetime.date.fromisoformat(str(value)[:10])
        return parsed.strftime("%b %d, %Y").replace(" 0", " ")
    except (TypeError, ValueError):
        return str(value)


def field_line(label, value, value_class=""):
    class_name = f"field-value {value_class}".strip()
    return (
        '<div class="field-line">'
        f'<span class="field-label">{html.escape(label)}</span>'
        f'<span class="{class_name}">{value}</span>'
        "</div>"
    )


def description_for(data):
    description = str(data.get("vendor_item_name") or "").strip()
    if description:
        return " ".join(description.split())
    note = str(data.get("unit_note") or "").strip()
    if note:
        return " ".join(note.split(";", 1)[0].split())
    return "Description not available"


def _price_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def price_history_summary(history):
    """Return chronological price-change events and observation counts."""
    observations = [row for row in (history or []) if _price_number(row.get("price")) is not None]
    events = []
    price_changes = 0
    previous = None
    for observation in observations:
        current_price = _price_number(observation.get("price"))
        previous_price = _price_number(previous.get("price")) if previous else None
        price_changed = previous_price is not None and current_price != previous_price
        identity_changed = previous is not None and (
            str(observation.get("apn") or "") != str(previous.get("apn") or "")
            or str(observation.get("pack_size") or "")
            != str(previous.get("pack_size") or "")
        )
        if previous is None or price_changed or identity_changed:
            event = dict(observation)
            event["delta"] = (
                current_price - previous_price if price_changed else None
            )
            events.append(event)
        if price_changed:
            price_changes += 1
        previous = observation
    return {
        "observations": len(observations),
        "price_changes": price_changes,
        "events": events,
    }


def price_history_details(data):
    summary = price_history_summary(data.get("history"))
    observations = summary["observations"]
    if not observations:
        return ""
    price_changes = summary["price_changes"]
    change_label = "price change" if price_changes == 1 else "price changes"
    rows = []
    for event in reversed(summary["events"][-10:]):
        delta = event.get("delta")
        if delta is None:
            delta_text = "—"
            delta_class = "delta-flat"
        elif delta > 0:
            delta_text = f"+${delta:,.2f}"
            delta_class = "delta-up"
        else:
            delta_text = f"-${abs(delta):,.2f}"
            delta_class = "delta-down"
        rows.append(
            "<tr>"
            f'<td>{html.escape(fmt_timestamp(event.get("pulled_at"), human=False))}</td>'
            f'<td>{html.escape(fmt_money(event.get("price")) or "—")}</td>'
            f'<td class="{delta_class}">{delta_text}</td>'
            f'<td>{html.escape(str(event.get("apn") or "—"))}</td>'
            "</tr>"
        )
    return (
        '<details class="price-history">'
        f"<summary>{observations} checks · {price_changes} {change_label}</summary>"
        '<table class="history-table"><thead><tr>'
        '<th>Observed</th><th>Case</th><th>Change</th><th>Product ID</th>'
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></details>"
    )


def is_orderable_quote(data):
    return (
        _price_number(data.get("price")) is not None
        and not data.get("blocks_ordering")
        and data.get("availability") not in {
            "product_mismatch",
            "identity_review",
            "pending_approval",
            "special_order",
            "not_found",
        }
    )



def vendor_cell(data, vid):
    dark, light = VENDOR_COLOR.get(vid, ("#333", "#eee"))
    apn = html.escape(str(data.get("apn") or ""))
    price = fmt_money(data.get("price"))
    description = html.escape(description_for(data))
    updated_at = html.escape(fmt_timestamp(data.get("pulled_at")))
    parts = ['<div class="vendor-cell">']
    status = str(data.get("availability") or "").strip()
    if status and status != "verified":
        status_label = STATUS_LABELS.get(status, status.replace("_", " ").title())
        note = str(data.get("status_note") or data.get("unit_note") or "").strip()
        parts.append(
            f'<div class="status-banner status-{html.escape(status)}">'
            f"{html.escape(status_label)}"
            + (f" — {html.escape(note)}" if note else "")
            + "</div>"
        )
    product_id = (
        f'<span class="pill" style="background:{light};color:{dark}">{apn}</span>'
        if apn else "Not available"
    )
    parts.append(field_line("Product ID", product_id))
    case_display = (
        "Call for price"
        if status == "special_order"
        else "Pending approval"
        if status == "pending_approval"
        else "Blocked"
        if status == "product_mismatch"
        else "Review before use"
        if status == "identity_review"
        else "Not found"
        if status == "not_found"
        else (price or "Not available")
    )
    parts.append(field_line("Case", html.escape(case_display), "price"))
    captured_price = fmt_money(data.get("captured_price"))
    if captured_price and status in {"product_mismatch", "identity_review"}:
        parts.append(
            field_line(
                "Last captured",
                html.escape(f"{captured_price} (not approved for ordering)"),
                "captured-price",
            )
        )
    unit_price = fmt_unit_price(data)
    parts.append(field_line("Unit", html.escape(unit_price), "unit-price"))
    parts.append(field_line("Description", description, "vendor-name"))
    if data.get("verified_on"):
        verified_on = html.escape(fmt_date(data.get("verified_on")))
        parts.append(field_line("Audited", verified_on, "updated-at"))
    parts.append(field_line("Price checked", updated_at, "updated-at"))
    parts.append(price_history_details(data))
    parts.append("</div>")
    return "".join(parts)


def cheapest_cell(prices):
    winner = cheapest_comparable_quote(prices)
    if winner is None:
        return (
            '<div class="best-unavailable">No defensible comparison yet. '
            'At least two verified quotes with the same normalized unit are required.</div>'
        )
    vendor = html.escape(VENDOR_NAMES[winner["vendor_id"]])
    case_price = html.escape(fmt_money(winner.get("price")) or "Not available")
    pack_size = html.escape(str(winner.get("pack_size") or "pack not recorded"))
    unit_price = html.escape(
        f'${winner["unit_price"]:,.4f}/{winner["unit_basis"]}'
    )
    verified = html.escape(fmt_timestamp(winner.get("pulled_at")))
    count = winner["comparable_quotes"]
    return (
        '<div class="best-card">'
        f'<span class="best-vendor">{vendor}</span>'
        f'<span class="best-price">{case_price} · {pack_size}</span>'
        f'<span class="best-meta">Compared at {unit_price} across {count} vendors</span>'
        f'<span class="best-meta">Verified {verified}</span>'
        '</div>'
    )


def cov_class(n):
    return f"cov{min(n, len(VENDOR_IDS))}"


def build_tsv(canonical_items, vendor_prices):
    headers = ["Category", "On Par ID", "Item Description"]
    for vid in VENDOR_IDS:
        vendor = VENDOR_NAMES[vid]
        headers.extend([
            f"{vendor} Product ID",
            f"{vendor} Case Cost",
            f"{vendor} Unit Cost",
            f"{vendor} Description",
            f"{vendor} Verified At",
        ])
    rows = ["\t".join(headers)]
    for item in canonical_items:
        cat_id = item["category_id"]
        row = [CAT_NAME.get(cat_id, ""), item["op_id"], item["name"]]
        prices = vendor_prices.get(item["id"], {})
        for vid in VENDOR_IDS:
            data = prices.get(vid)
            if not data:
                row.extend(["Item not available", "", "", "", ""])
                continue
            row.extend([
                str(data.get("apn") or ""),
                (
                    "Call for price"
                    if data.get("availability") == "special_order"
                    else fmt_money(data.get("price"))
                ),
                fmt_unit_price(data),
                description_for(data),
                fmt_timestamp(data.get("pulled_at"), human=False),
            ])
        rows.append("\t".join(row))
    return "\n".join(rows)


def build_inventory_pricing(canonical_items, vendor_prices):
    """Build the current approved price per kitchen inventory count unit."""
    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    latest_price_checked_at = None
    rows = []

    for item in canonical_items:
        count_unit = count_unit_for_item(item)
        inventory_item = {**item, "count_unit": count_unit}
        quotes = {}

        for vendor_id, data in vendor_prices.get(item["id"], {}).items():
            if vendor_id not in VENDOR_IDS:
                continue
            case_price = _price_number(data.get("price"))
            if (
                case_price is None
                or not is_orderable_quote(data)
                or not pricing_matches_item_requirements(inventory_item, data)
            ):
                continue

            conversion = units_per_case(inventory_item, data)
            if conversion is None or conversion <= 0:
                continue

            checked_at = data.get("pulled_at")
            if checked_at and (
                latest_price_checked_at is None
                or str(checked_at) > str(latest_price_checked_at)
            ):
                latest_price_checked_at = checked_at

            quotes[str(vendor_id)] = {
                "vendor_id": vendor_id,
                "vendor": VENDOR_NAMES[vendor_id],
                "case_price": case_price,
                "units_per_case": conversion,
                "price_per_count_unit": case_price / conversion,
                "count_unit": count_unit,
                "pack_size": data.get("pack_size") or "",
                "product_id": str(data.get("apn") or ""),
                "price_checked_at": checked_at,
            }

        rows.append({
            "item_id": item["id"],
            "name": item["name"],
            "count_unit": count_unit,
            "quotes": quotes,
        })

    return {
        "generated_at": generated_at,
        "latest_price_checked_at": latest_price_checked_at,
        "items": rows,
    }


def build_html(canonical_items, vendor_prices):
    now = datetime.datetime.now(ZoneInfo("America/New_York")).strftime(
        "%B %d, %Y at %I:%M %p %Z"
    )
    vendor_prices = {
        item_id: {
            vendor_id: data
            for vendor_id, data in prices.items()
            if vendor_id in VENDOR_IDS
        }
        for item_id, prices in vendor_prices.items()
    }
    total = len(canonical_items)
    coverage_by_item = {
        item["id"]: sum(
            1 for data in vendor_prices.get(item["id"], {}).values()
            if is_orderable_quote(data)
        )
        for item in canonical_items
    }
    max_coverage = len(VENDOR_IDS)
    counts = [
        sum(1 for item in canonical_items if coverage_by_item[item["id"]] == n)
        for n in range(max_coverage + 1)
    ]
    comparable_count = sum(
        1 for item in canonical_items
        if cheapest_comparable_quote(vendor_prices.get(item["id"], {})) is not None
    )
    audit_flag_count = sum(
        1
        for item in canonical_items
        for data in vendor_prices.get(item["id"], {}).values()
        if data.get("availability")
        in {"product_mismatch", "identity_review", "special_order", "not_found"}
    )

    def vendor_count(vid):
        return sum(
            1
            for item in canonical_items
            if is_orderable_quote(vendor_prices.get(item["id"], {}).get(vid, {}))
        )

    h = [f'''<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>On Par - Item Master</title>
<style>{CSS}</style></head><body>
<header>
  <div><h1>On Par - Item Master</h1>
    <div class="subtitle">Cross-Vendor Coverage &nbsp;.&nbsp; Latest recorded supplier prices &nbsp;.&nbsp; Page generated {now}</div>
  </div>
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
    <div class="legend">
      <div class="legend-item"><div class="swatch sw3"></div>All 3 active vendors ({counts[3]})</div>
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
  <div><span>Cheapest comparisons: </span><strong>{comparable_count}</strong></div>
  <div><span>Audit flags: </span><strong>{audit_flag_count}</strong></div>
</div>
<div class="method-note"><strong>Live Supabase history:</strong> expand any supplier's history to see its recorded checks and price changes over time. Cheapest prices are compared only when at least two approved suppliers have the same normalized unit. “Price checked” is the supplier observation time; “Audited” is the most recent manual verification.</div>
<div class="table-wrap"><table>
<thead><tr>
  <th style="min-width:90px">On Par ID</th>
  <th style="min-width:200px">Item Description</th>
  <th style="min-width:230px">Cheapest Comparable Price</th>
  <th class="vnd usf">US Foods</th>
  <th class="vnd pfg">PFG</th>
  <th class="vnd syc">Sysco</th>
</tr></thead><tbody>''']

    current_cat = None
    for item in canonical_items:
        cat_id = item["category_id"]
        if cat_id != current_cat:
            current_cat = cat_id
            h.append(f'<tr class="cat-row"><td colspan="{3 + len(VENDOR_IDS)}">{html.escape(CAT_NAME.get(cat_id, ""))}</td></tr>')
        prices = vendor_prices.get(item["id"], {})
        n = coverage_by_item[item["id"]]
        cells = "".join(
            f'<td class="apn">{vendor_cell(prices[v], v)}</td>'
            if v in prices
            else '<td class="blank">Item not available</td>'
            for v in VENDOR_IDS
        )
        h.append(
            f'<tr class="{cov_class(n)}">'
            f'<td class="op-id">{html.escape(item["op_id"])}</td>'
            f'<td class="item-name">{html.escape(item["name"])}</td>'
            f'<td class="best-cell">{cheapest_cell(prices)}</td>'
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

        if fmt in {"inventory", "json"}:
            body = json.dumps(
                build_inventory_pricing(canonical_items, vendor_prices),
                separators=(",", ":"),
            )
            payload = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
        elif fmt == "tsv":
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
