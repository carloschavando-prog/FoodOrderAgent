"""
POST /api/place_order_pfg
==========================
Places a PFG CustomerFirst order via the Azure REST API.

Body JSON:
  {"items": [{"productKey": "35795bd7-...", "uomType": "CS", "qty": 3}, ...]}

Returns JSON:
  {"success": true, "orderHeaderId": "...", "confirmationNumber": "...",
   "deliveryDate": "2026-06-03", "totalItems": N, "error": null}

Auth:
  MSAL B2C refresh token stored in Supabase vendor_auth (vendor_id=2).
  Falls back to PFG_REFRESH_TOKEN + PFG_CONFIG env vars.
  Token is rotated and saved after each successful refresh.

Order flow:
  1. Load creds, refresh B2C token
  2. GET OrderEntryHeader/V1/GetActiveOrder  → use existing draft, or
     POST OrderEntryHeader/V1/CreateOrderEntryHeader → create new draft
  3. POST OrderEntryDetail/V1/AddOrderEntryDetails (bulk add)
  4. POST OrderEntryHeader/V1/SubmitOrderEntryHeader → submit
  5. Return ConfirmationOrderNumber
"""

import json, os, urllib.request, urllib.error, urllib.parse
from http.server import BaseHTTPRequestHandler

# ── Config ────────────────────────────────────────────────────────────────────

SB_URL  = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY  = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_SKEY = os.getenv("SUPABASE_SERVICE_KEY", SB_KEY)

PFG_API_BASE   = "https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api"
B2C_TOKEN_URL  = (
    "https://pfgcustomerfirst.b2clogin.com"
    "/pfgcustomerfirst.onmicrosoft.com"
    "/b2c_1a_signup_signin/oauth2/v2.0/token"
)
B2C_SCOPE = (
    "https://pfgcustomerfirst.onmicrosoft.com/api/customer-first-site-api "
    "openid profile offline_access"
)

# ── Credential loading / saving ───────────────────────────────────────────────

def _sb_svc_headers():
    return {
        "apikey":        SB_SKEY,
        "Authorization": f"Bearer {SB_SKEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def load_pfg_credentials():
    """Load PFG credentials from Supabase or env vars."""
    try:
        req = urllib.request.Request(
            f"{SB_URL}/rest/v1/vendor_auth?vendor_id=eq.2&select=credentials",
            headers=_sb_svc_headers()
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        if rows:
            return rows[0]["credentials"]
    except Exception:
        pass

    if os.getenv("PFG_CONFIG"):
        creds = json.loads(os.environ["PFG_CONFIG"])
        creds["refresh_token"] = os.environ.get("PFG_REFRESH_TOKEN", "")
        return creds

    raise RuntimeError(
        "No PFG credentials. Run setup_vendor_auth.py or set PFG_CONFIG + PFG_REFRESH_TOKEN."
    )


def save_pfg_refresh_token(new_token, config):
    config["refresh_token"] = new_token
    try:
        hdrs = {**_sb_svc_headers(), "Prefer": "resolution=merge-duplicates,return=representation"}
        req = urllib.request.Request(
            f"{SB_URL}/rest/v1/vendor_auth?on_conflict=vendor_id",
            data=json.dumps({"vendor_id": 2, "credentials": config}).encode(),
            headers=hdrs, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as ex:
        print(f"  ⚠️  Could not save PFG refresh token: {ex}")


# ── Token refresh ─────────────────────────────────────────────────────────────

def refresh_bearer(config):
    """Exchange MSAL B2C refresh token for new Bearer + refresh token."""
    payload = urllib.parse.urlencode({
        "grant_type":    "refresh_token",
        "refresh_token": config["refresh_token"],
        "client_id":     config.get("b2c_client_id", "c68e7fae-80a1-42db-bd89-3fb37d1224a2"),
        "scope":         B2C_SCOPE,
        "client_info":   "1",
    }).encode()
    req = urllib.request.Request(
        B2C_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read())

    access = resp.get("access_token") or resp.get("id_token")
    save_pfg_refresh_token(resp["refresh_token"], config)
    return f"Bearer {access}"


# ── PFG API helper ────────────────────────────────────────────────────────────

def pfg_call(method, endpoint, bearer, payload=None, params=None):
    url = f"{PFG_API_BASE}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    hdrs = {
        "Authorization": bearer,
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }
    data = json.dumps(payload).encode() if payload is not None else None
    req  = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


# ── Order placement ───────────────────────────────────────────────────────────

def get_or_create_order_header(bearer, customer_id, biz_unit=3):
    """
    Get the current active/draft order header, or create a new one.
    Returns (order_entry_header_id, delivery_date).
    """
    # Try to get the active order first (avoids creating duplicates)
    try:
        resp = pfg_call("GET", "OrderEntryHeader/V1/GetActiveOrder",
                        bearer, params={"CustomerId": customer_id})
        ro = resp.get("ResultObject") or {}
        if ro.get("OrderEntryHeaderId"):
            return ro["OrderEntryHeaderId"], ro.get("DeliveryDate", "")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    # Create new draft order
    resp = pfg_call("POST", "OrderEntryHeader/V1/CreateOrderEntryHeader",
                    bearer, {"CustomerId": customer_id, "BusinessUnitKey": biz_unit})
    ro = resp.get("ResultObject", {})
    return ro["OrderEntryHeaderId"], ro.get("DeliveryDate", "")


def _is_uuid(s):
    """Return True if s looks like a UUID (8-4-4-4-12 hex)."""
    return bool(s and len(str(s)) == 36 and str(s).count("-") == 4)


def _resolve_product_keys(bearer, config):
    """
    Fetch SearchProductList and return {product_number_str: product_key_uuid}.
    Used when generate_order.py passes numeric APNs for PFG items.
    """
    list_id     = (config.get("fall_list_id") or config.get("list_id") or
                   "13e8ce85-8f4e-4cfe-a6dd-cac49a88dc60")
    customer_id = config.get("customer_id", "ccbddeae-bc43-4287-a4e0-8d5bee2b913c")
    apn_to_key  = {}
    skip = 0
    while True:
        try:
            resp = pfg_call("POST", "ProductListSearch/V1/SearchProductList", bearer, {
                "CustomerId":          customer_id,
                "ProductListHeaderId": list_id,
                "Query":               "",
                "Skip":                skip,
                "Take":                500,
                "SortValue":           5,
                "FacetFilter":         [],
            })
        except Exception:
            break
        ro   = resp.get("ResultObject", {})
        cats = ro.get("ProductListCategories", [])
        count = 0
        for cat in cats:
            for pw in cat.get("Products", []):
                p  = pw.get("Product", pw)
                pn = str(p.get("ProductNumber", "")).strip()
                pk = p.get("ProductKey", "")
                if pn and pk:
                    apn_to_key[pn] = pk
                count += 1
        if not ro.get("HasLoadMore") or count == 0:
            break
        skip = ro.get("Skip", skip + count)
    return apn_to_key


def add_order_items(bearer, order_id, customer_id, items, config=None):
    """
    Add items to the draft order.
    Tries bulk endpoint first, falls back to per-item calls.
    items can use:
      {"productKey": "UUID", "uomType": "CS", "qty": N}   ← UUID (preferred)
      {"apn": "product_number",  "uomType": "CS", "qty": N}  ← numeric APN (resolved here)
    """
    # Resolve numeric APNs → ProductKey UUIDs via SearchProductList
    needs_resolution = [i for i in items
                        if not _is_uuid(i.get("productKey") or i.get("apn", ""))]
    if needs_resolution and config:
        apn_to_key = _resolve_product_keys(bearer, config)
        resolved = []
        for i in items:
            if _is_uuid(i.get("productKey") or ""):
                resolved.append(i)
            else:
                raw = str(i.get("apn") or i.get("productKey", "")).strip()
                pk  = apn_to_key.get(raw, raw)   # fallback: send raw (will likely 400)
                resolved.append({**i, "productKey": pk})
        items = resolved

    # Build item list
    item_list = [
        {
            "OrderEntryHeaderId": order_id,
            "CustomerId":         customer_id,
            "ProductKey":         item.get("productKey") or item.get("apn", ""),
            "UnitOfMeasureType":  item.get("uomType", "CS"),
            "Quantity":           item["qty"],
        }
        for item in items
    ]

    for endpoint in [
        "OrderEntryDetail/V1/AddOrderEntryDetails",  # plural (bulk)
        "OrderEntryDetail/V1/AddOrderEntryDetail",   # singular (single item)
    ]:
        try:
            if endpoint.endswith("Details"):
                resp = pfg_call("POST", endpoint, bearer,
                                {"OrderEntryDetails": item_list})
            else:
                # Singular: loop
                for item_body in item_list:
                    pfg_call("POST", endpoint, bearer, item_body)
                return True
            ro = resp.get("ResultObject") or resp
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise

    raise RuntimeError("Could not find AddOrderEntryDetail endpoint")


def submit_order(bearer, order_id, customer_id, biz_unit=3):
    """
    Submit the draft order. Returns confirmation number.
    Tries multiple likely endpoint names.
    """
    submit_body = {
        "OrderEntryHeaderId": order_id,
        "CustomerId":         customer_id,
        "BusinessUnitKey":    biz_unit,
    }

    for endpoint in [
        "OrderEntryHeader/V1/SubmitOrderEntryHeader",
        "SubmittedOrder/V1/SubmitOrder",
        "OrderEntryHeader/V1/FinalizeOrderEntryHeader",
        "OrderEntryHeader/V1/ConfirmOrderEntryHeader",
    ]:
        try:
            resp = pfg_call("POST", endpoint, bearer, submit_body)
            ro   = resp.get("ResultObject") or resp
            # Success — extract confirmation number
            if isinstance(ro, dict):
                conf = (ro.get("ConfirmationOrderNumber") or
                        ro.get("OrderNumber") or
                        ro.get("OrderEntryHeaderId") or "")
                return conf
            return ""
        except urllib.error.HTTPError as e:
            if e.code in (404, 405):
                continue
            # 400 might mean already submitted or other business error
            body = e.read().decode()[:300]
            raise RuntimeError(f"{endpoint} → {e.code}: {body}")

    raise RuntimeError("Could not find PFG submit endpoint (tried 4 variants)")


def place_pfg_order(bearer, config, items):
    """Full PFG order placement flow."""
    customer_id = config.get("customer_id", "ccbddeae-bc43-4287-a4e0-8d5bee2b913c")
    biz_unit    = int(config.get("biz_unit_key", 3))

    order_id, delivery_date = get_or_create_order_header(bearer, customer_id, biz_unit)
    add_order_items(bearer, order_id, customer_id, items, config=config)
    confirmation = submit_order(bearer, order_id, customer_id, biz_unit)

    return {
        "orderHeaderId":    order_id,
        "confirmationNumber": confirmation,
        "deliveryDate":     delivery_date[:10] if delivery_date else "",
    }


# ── Vercel handler ────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length) if length else b"{}")
        items  = body.get("items", [])

        try:
            if not items:
                raise ValueError("No items in request body")

            config = load_pfg_credentials()
            bearer = refresh_bearer(config)
            result = place_pfg_order(bearer, config, items)

            payload = json.dumps({
                "success":            True,
                "vendor":             "PFG",
                "orderHeaderId":      result["orderHeaderId"],
                "confirmationNumber": result["confirmationNumber"],
                "deliveryDate":       result["deliveryDate"],
                "totalItems":         len(items),
                "error":              None,
            }).encode()

        except Exception as ex:
            import traceback
            payload = json.dumps({
                "success": False,
                "vendor":  "PFG",
                "error":   str(ex),
                "trace":   traceback.format_exc()[-500:],
            }).encode()

        self.send_response(200)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        pass
