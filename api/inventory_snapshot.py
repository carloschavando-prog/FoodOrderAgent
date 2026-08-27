"""
GET/POST /api/inventory_snapshot
================================
Stores and retrieves shared inventory counts for the kitchen count sheet.

The browser keeps a local draft, but this endpoint saves the count in Supabase
so other people and the Integrator app can use the same inventory snapshot.
"""

import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler

from party_demand import load_party_snapshot


SB_URL = os.environ.get("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY = os.environ.get("SUPABASE_KEY", "")
SB_SKEY = os.environ.get("SUPABASE_SERVICE_KEY", SB_KEY)


def _headers(use_service=False, prefer=None):
    key = SB_SKEY if use_service else SB_KEY
    hdrs = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if prefer:
        hdrs["Prefer"] = prefer
    return hdrs


def _sb_request(path, method="GET", payload=None, use_service=False, prefer=None, timeout=20):
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/{path}",
        data=data,
        headers=_headers(use_service=use_service, prefer=prefer),
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else None


def _norm(name):
    return " ".join(str(name or "").lower().strip().split())


def _number(value):
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_order_overrides(value):
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise ValueError("Order overrides must be an object.")
    if len(value) > 200:
        raise ValueError("Too many order overrides were provided.")

    normalized = {}
    for raw_name, raw_override in value.items():
        name = str(raw_name or "").strip()
        if not name or not isinstance(raw_override, dict):
            raise ValueError("Each order override must name an item and provide settings.")
        quantity = _number(raw_override.get("quantity"))
        mode = str(raw_override.get("mode") or "").strip().lower()
        if quantity is None or quantity < 0:
            raise ValueError(f"Order override for {name} has an invalid quantity.")
        if mode not in ("cases", "minimum_cases", "count_unit"):
            raise ValueError(f"Order override for {name} has an invalid mode.")
        if mode in ("cases", "minimum_cases") and not quantity.is_integer():
            raise ValueError(f"Order override for {name} must use whole cases.")

        override = {
            "quantity": int(quantity) if quantity.is_integer() else quantity,
            "mode": mode,
        }
        vendor_id = raw_override.get("vendorId")
        if vendor_id not in (None, ""):
            try:
                vendor_id = int(vendor_id)
            except (TypeError, ValueError):
                raise ValueError(f"Order override for {name} has an invalid vendor.")
            if vendor_id not in (1, 2, 3):
                raise ValueError(f"Order override for {name} has an invalid vendor.")
            override["vendorId"] = vendor_id
        required_pack = str(raw_override.get("required_pack") or "").strip()
        if required_pack:
            override["required_pack"] = required_pack[:80]
        normalized[name] = override
    return normalized


def _load_item_lookup():
    rows = _sb_request("items?select=id,name,pack_size&order=id.asc", use_service=True)
    by_name = {}
    for row in rows or []:
        key = _norm(row.get("name"))
        if key and key not in by_name:
            by_name[key] = row
    return by_name


def _latest_snapshot():
    snapshots = _sb_request(
        "inventory_snapshots?select=*&order=taken_at.desc,id.desc&limit=1",
        use_service=True,
    )
    if not snapshots:
        return None, [], None

    snapshot = snapshots[0]
    snapshot_id = snapshot["id"]
    items = _sb_request(
        f"inventory_snapshot_items?select=*&snapshot_id=eq.{snapshot_id}&order=id.asc",
        use_service=True,
    )
    party_snapshot = None
    if snapshot.get("party_demand_snapshot_id") is not None:
        party_snapshot = load_party_snapshot(
            snapshot_id=snapshot["party_demand_snapshot_id"]
        )
    return snapshot, items or [], party_snapshot


def _save_snapshot(payload):
    incoming = payload.get("items") if isinstance(payload, dict) else None
    if incoming is None and isinstance(payload, dict):
        counts = payload.get("counts", payload)
        incoming = [
            {"name": name, "on_hand": qty}
            for name, qty in counts.items()
        ]
    if not isinstance(incoming, list):
        raise ValueError("Expected an items list or counts object.")

    item_lookup = _load_item_lookup()
    rows = []
    for item in incoming:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("item_name")
        key = _norm(name)
        qty = _number(item.get("on_hand", item.get("on_hand_qty")))
        if not key or qty is None:
            continue

        master = item_lookup.get(key, {})
        rows.append({
            "item_id": master.get("id"),
            "item_name": key,
            "on_hand_qty": qty,
            "unit": item.get("unit") or master.get("pack_size") or "case",
        })

    if not rows:
        raise ValueError("No inventory counts were provided.")

    header_payload = {
        "taken_by": payload.get("taken_by") or None,
        "notes": payload.get("notes") or "Saved from Kitchen Order Sheet",
        "party_demand_snapshot_id": payload.get("party_demand_snapshot_id") or None,
        "order_overrides": _normalize_order_overrides(
            payload.get("order_overrides")
        ),
    }
    snapshot = _sb_request(
        "inventory_snapshots",
        method="POST",
        payload=header_payload,
        use_service=True,
        prefer="return=representation",
    )
    snapshot_id = snapshot[0]["id"]

    for row in rows:
        row["snapshot_id"] = snapshot_id

    _sb_request(
        "inventory_snapshot_items",
        method="POST",
        payload=rows,
        use_service=True,
        prefer="return=minimal",
        timeout=30,
    )

    return snapshot[0], len(rows)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            snapshot, items, party_snapshot = _latest_snapshot()
            counts = {
                row["item_name"]: row.get("on_hand_qty")
                for row in items
                if row.get("item_name")
            }
            self._json(200, {
                "snapshot": snapshot,
                "counts": counts,
                "items": items,
                "party_demand": party_snapshot,
            })
        except Exception as exc:
            self._json(500, {"error": str(exc)})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(body)
            snapshot, saved_count = _save_snapshot(payload)
            self._json(200, {"snapshot": snapshot, "saved_count": saved_count})
        except Exception as exc:
            self._json(400, {"error": str(exc)})

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code, payload):
        data = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass
