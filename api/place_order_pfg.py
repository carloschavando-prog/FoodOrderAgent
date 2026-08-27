"""
POST /api/place_order_pfg
==========================
Places a PFG CustomerFirst order via the Azure REST API.

Body JSON:
  {"orderId": "generated-order-uuid",
   "items": [{"productKey": "35795bd7-...", "uomType": "CS", "qty": 3}, ...]}

Returns JSON:
  {"success": true, "orderHeaderId": "...", "confirmationNumber": "...",
   "deliveryDate": "2026-06-03", "totalItems": N, "error": null}

Auth:
  MSAL B2C refresh token stored in Supabase vendor_auth (vendor_id=2).
  Falls back to PFG_REFRESH_TOKEN + PFG_CONFIG env vars.
  Token is rotated and saved after each successful refresh.

Order flow:
  1. Load creds, refresh B2C token
  2. Resolve products from the saved guide, then the global PFG catalog
  3. POST OrderEntryHeader/V1/CreateOrderEntryHeader → create a clean draft
  4. POST OrderEntryDetail/V1/UpdateOrderEntryDetail for each item
  5. POST OrderEntryHeader/V1/SubmitOrderEntryHeader → submit
  6. Return ConfirmationOrderNumber
"""

import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler

from api.vendor_auth import VendorAuthClient

# ── Config ────────────────────────────────────────────────────────────────────

SB_URL  = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY  = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_SKEY = os.getenv("SUPABASE_SERVICE_KEY", "")

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

ZERO_UUID = "00000000-0000-0000-0000-000000000000"
DEFAULT_CATALOG_FILTER = {
    "IsLocallyStockedItem": None,
    "IsCriticalItem": None,
    "IsNewInStock": None,
    "HasOrderedInLastNumberOfDays": None,
    "HasPreviousPurchase": None,
    "Badges": [],
    "CategoryIds": [],
    "Brand": None,
    "Brands": [],
    "PackSize": None,
    "StorageTypes": [],
    "StateOfOriginAbbreviation": None,
    "StateOfOriginAbbreviations": [],
    "DeliveryOptions": {},
    "Nutritional": {},
    "Manufacturers": [],
}

# ── Credential loading / saving ───────────────────────────────────────────────

def _sb_svc_headers():
    if not SB_SKEY:
        raise RuntimeError("SUPABASE_SERVICE_KEY is required for vendor credentials")
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
            data=json.dumps({
                "vendor_id": 2,
                "credentials": config,
                "updated_at": datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat(),
            }).encode(),
            headers=hdrs, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as ex:
        print(f"  ⚠️  Could not save PFG refresh token: {ex}")


# ── Token refresh ─────────────────────────────────────────────────────────────

def refresh_bearer(config, *, persist=True):
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
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(
            f"PFG authentication failed (HTTP {ex.code}): {body or ex.reason}"
        ) from ex

    access = resp.get("access_token") or resp.get("id_token")
    if not access:
        raise RuntimeError("PFG authentication returned no access token")
    if resp.get("refresh_token"):
        config["refresh_token"] = resp["refresh_token"]
        if persist:
            save_pfg_refresh_token(resp["refresh_token"], config)
    return f"Bearer {access}"


def authenticate_pfg():
    """Refresh from the shared credential chain and durably save the rotation."""
    lease = VendorAuthClient.from_env(direct=True).claim(2)
    config = lease.credentials
    try:
        bearer = refresh_bearer(config, persist=False)
        lease.commit(config, verified=True)
        return bearer, config
    except Exception as ex:
        lease.fail(ex)
        raise


# ── PFG API helper ────────────────────────────────────────────────────────────

def pfg_call(method, endpoint, bearer, payload=None, params=None, stage="API request"):
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
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            result = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as ex:
        body = ex.read().decode("utf-8", errors="replace")[:800]
        raise RuntimeError(
            f"PFG {stage} failed (HTTP {ex.code}): {body or ex.reason}"
        ) from ex
    if isinstance(result, dict) and result.get("IsSuccess") is False:
        errors = result.get("ErrorMessages") or result.get("Message") or result
        raise RuntimeError(f"PFG {stage} failed: {str(errors)[:800]}")
    return result


# ── Order placement ───────────────────────────────────────────────────────────

def create_order_header(bearer, customer_id):
    """
    Create a clean PFG draft owned by this submission.

    CustomerFirst can contain unrelated manual drafts. Reusing its generic
    active order risks submitting somebody else's quantities, so automated
    orders always start from a fresh header after all products resolve.
    Returns (order_entry_header_id, delivery_date).
    """
    resp = pfg_call(
        "POST",
        "OrderEntryHeader/V1/CreateOrderEntryHeader",
        bearer,
        {"CustomerId": customer_id, "PurchaseOrderNumber": ""},
        stage="order creation",
    )
    ro = resp.get("ResultObject", {})
    if not ro.get("OrderEntryHeaderId"):
        raise RuntimeError("PFG order creation returned no order header ID")
    return ro["OrderEntryHeaderId"], ro.get("DeliveryDate", "")


def _is_uuid(s):
    """Return True if s looks like a UUID (8-4-4-4-12 hex)."""
    return bool(s and len(str(s)) == 36 and str(s).count("-") == 4)


def _load_order_guide_products(bearer, config):
    """
    Fetch the current PFG guide and index products by number and UUID.
    """
    list_id     = (config.get("fall_list_id") or config.get("list_id") or
                   "13e8ce85-8f4e-4cfe-a6dd-cac49a88dc60")
    customer_id = config.get("customer_id", "ccbddeae-bc43-4287-a4e0-8d5bee2b913c")
    products = {}
    skip = 0
    while True:
        resp = pfg_call(
            "POST",
            "ProductListSearch/V1/SearchProductList",
            bearer,
            {
                "CustomerId":          customer_id,
                "ProductListHeaderId": list_id,
                "Query":               "",
                "Skip":                skip,
                "Take":                500,
                "SortValue":           5,
                "FacetFilter":         [],
            },
            stage="order-guide lookup",
        )
        ro   = resp.get("ResultObject", {})
        cats = ro.get("ProductListCategories", [])
        count = 0
        for cat in cats:
            for pw in cat.get("Products", []):
                p = pw.get("Product", pw)
                pn = str(p.get("ProductNumber", "")).strip()
                pk = str(p.get("ProductKey", "")).strip()
                if pn:
                    products[pn] = p
                if pk:
                    products[pk] = p
                count += 1
        if not ro.get("HasLoadMore") or count == 0:
            break
        skip += count
    return products


def _catalog_search_context(bearer, config):
    """Build the read-only context used by CustomerFirst's global search."""
    customer_id = config.get(
        "customer_id", "ccbddeae-bc43-4287-a4e0-8d5bee2b913c"
    )
    active = {}
    try:
        response = pfg_call(
            "GET",
            "OrderEntryHeader/V1/GetActiveOrder",
            bearer,
            params={"CustomerId": customer_id},
            stage="catalog context lookup",
        )
        active = response.get("ResultObject") or {}
    except RuntimeError:
        # Global search supports the zero header outside order entry. A future
        # date is sufficient because pricing is deliberately not loaded here.
        active = {}

    delivery_date = active.get("DeliveryDate")
    if not delivery_date:
        delivery_date = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=1)
        ).strftime("%Y-%m-%dT00:00:00Z")
    return {
        "BusinessUnitKey": int(config.get("biz_unit_key") or 3),
        "OperationCompanyNumber": str(config.get("opco_number") or "795"),
        "CustomerId": customer_id,
        "DeliveryDate": delivery_date,
        "OrderEntryHeaderId": active.get("OrderEntryHeaderId") or ZERO_UUID,
    }


def _product_matches_identifier(product, identifier):
    expected = str(identifier).strip().upper()
    values = {
        str(product.get("ProductKey") or "").strip().upper(),
        str(product.get("ProductNumber") or "").strip().upper(),
        str(product.get("DisplayProductNumber") or "").strip().upper(),
    }
    for uom in product.get("UnitOfMeasureOrderQuantities") or []:
        values.add(str(uom.get("ProductNumberDisplay") or "").strip().upper())
        values.add(str(uom.get("OrignialProductNumber") or "").strip().upper())
    return expected in values


def _search_global_catalog(bearer, config, identifier, context):
    """Return an exact orderable match from CustomerFirst's global catalog."""
    response = pfg_call(
        "POST",
        "ProductCatalog/V1/SearchProductCatalog",
        bearer,
        {
            **context,
            "CurrentPageNumber": 0,
            "PageSize": 25,
            "QueryText": str(identifier),
            "Skip": 0,
            "LoadPricing": False,
            "AdvanceFilter": DEFAULT_CATALOG_FILTER,
        },
        stage=f"global catalog lookup ({identifier})",
    )
    result = response.get("ResultObject") or {}
    for value in result.get("CatalogProducts") or []:
        product = value.get("Product", value)
        if (
            _product_matches_identifier(product, identifier)
            and product.get("CanOrder") is not False
            and not product.get("IsRemoved")
        ):
            return product
    return None


def resolve_order_items(bearer, config, items):
    """Resolve generated APNs to the full product/UOM records PFG requires."""
    products = _load_order_guide_products(bearer, config)
    identifiers = [
        str(item.get("productKey") or item.get("apn") or "").strip()
        for item in items
    ]
    missing_from_guide = [
        identifier for identifier in identifiers
        if identifier and identifier not in products
    ]
    if missing_from_guide:
        context = _catalog_search_context(bearer, config)
        for identifier in dict.fromkeys(missing_from_guide):
            product = _search_global_catalog(
                bearer, config, identifier, context
            )
            if product:
                products[identifier] = product

    resolved = []
    missing = []
    for item in items:
        identifier = str(item.get("productKey") or item.get("apn") or "").strip()
        product = products.get(identifier)
        if not product:
            missing.append(identifier or "<blank>")
            continue
        requested_uom = str(item.get("uomType", "CS")).upper()
        uoms = product.get("UnitOfMeasureOrderQuantities") or []
        uom = next(
            (
                candidate
                for candidate in uoms
                if str(candidate.get("UnitOfMeasure", "")).upper() == requested_uom
            ),
            None,
        )
        if not uom:
            uom = next((candidate for candidate in uoms if candidate.get("CanOrderUom")), None)
        if not uom:
            missing.append(f"{identifier} ({requested_uom})")
            continue
        resolved.append({"item": item, "product": product, "uom": uom})
    if missing:
        raise RuntimeError(
            "PFG could not resolve current catalog products: " + ", ".join(missing)
        )
    return resolved


def add_order_items(bearer, order_id, customer_id, resolved_items):
    """Set each requested quantity using PFG's current cart endpoint."""
    for resolved in resolved_items:
        item = resolved["item"]
        product = resolved["product"]
        uom = resolved["uom"]
        payload = {
            "OrderEntryHeaderId": order_id,
            "BusinessUnitKey": product.get("BusinessUnitKey"),
            "BusinessUnitERPKey": product.get("BusinessUnitERPKey"),
            "CustomerId": customer_id,
            "ProductKey": product.get("ProductKey"),
            "UnitOfMeasureType": uom.get("UnitOfMeasure", item.get("uomType", "CS")),
            "Quantity": int(item["qty"]),
            "Price": uom.get("Price") or 0,
            "ProductNumber": product.get("ProductNumber", ""),
            "ProductDescription": product.get("ProductDescription", ""),
            "ProductBrand": product.get("ProductBrand", ""),
            "ProductPackSize": uom.get("PackSize", ""),
            "ProductIsCatchWeight": uom.get(
                "ProductIsCatchWeight", product.get("ProductIsCatchWeight", False)
            ),
            "ProductAverageWeight": uom.get(
                "ProductAverageWeight", product.get("ProductAverageWeight", 0)
            ),
            "ShipLaterMaxEstimatedDays": product.get("ShipLaterMaxEstimatedDays", 0),
            "CutoffDateTime": product.get("CutoffDateTime"),
            "UOMOrderQuantityAlertThresholdMin": uom.get(
                "UOMOrderQuantityAlertThresholdMin", 0
            ),
            "UOMOrderQuantityAlertThresholdMax": uom.get(
                "UOMOrderQuantityAlertThresholdMax", 0
            ),
        }
        pfg_call(
            "POST",
            "OrderEntryDetail/V1/UpdateOrderEntryDetail",
            bearer,
            payload,
            stage=f"item update ({product.get('ProductNumber', 'unknown')})",
        )
    return True


def submit_order(bearer, order_id):
    """Submit the draft order through PFG's current query-parameter endpoint."""
    submit_params = {
        "OrderEntryHeaderId": order_id,
        "TimeZone": "America/New_York",
    }
    resp = pfg_call(
        "POST",
        "OrderEntryHeader/V1/SubmitOrderEntryHeader",
        bearer,
        params=submit_params,
        stage="order submission",
    )
    ro = resp.get("ResultObject") or resp
    if isinstance(ro, dict):
        return (
            ro.get("ConfirmationOrderNumber")
            or ro.get("OrderNumber")
            or ro.get("OrderEntryHeaderId")
            or ""
        )
    return ""


def place_pfg_order(bearer, config, items):
    """Full PFG order placement flow."""
    customer_id = config.get("customer_id", "ccbddeae-bc43-4287-a4e0-8d5bee2b913c")
    resolved_items = resolve_order_items(bearer, config, items)
    order_id, delivery_date = create_order_header(bearer, customer_id)
    add_order_items(bearer, order_id, customer_id, resolved_items)
    confirmation = submit_order(bearer, order_id)

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

            bearer, config = authenticate_pfg()
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
