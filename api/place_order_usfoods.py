"""
POST /api/place_order_usfoods
==============================
Places a US Foods order via the Panamax REST APIs.

Body JSON:
  {"items": [{"productNumber": 1085770, "qty": 3}, ...]}

Returns JSON:
  {"success": true, "orderId": "...", "tandemOrderNumber": 12345,
   "deliveryDate": "2026-06-03", "totalItems": N, "error": null}

Auth:
  Refresh token stored in Supabase vendor_auth table (vendor_id=1).
  Falls back to USF_REFRESH_TOKEN + USF_CONFIG env vars (same as CI).
  After refresh, new token is stored back to Supabase.

Credentials table (Supabase):
  vendor_auth(vendor_id int PK, credentials jsonb, updated_at timestamptz)
"""

import json, os, uuid, time, urllib.request, urllib.error, urllib.parse
from http.server import BaseHTTPRequestHandler

# ── Config ────────────────────────────────────────────────────────────────────

SB_URL   = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY   = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_SKEY  = os.getenv("SUPABASE_SERVICE_KEY", "")

API_BASE = "https://panamax-api.ama.usfoods.com"

SB_HDRS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type":  "application/json",
}

# ── Credential loading / saving ───────────────────────────────────────────────

def _sb_svc_headers():
    """Headers using service role key (for vendor_auth table)."""
    if not SB_SKEY:
        raise RuntimeError("SUPABASE_SERVICE_KEY is required for vendor credentials")
    return {
        "apikey":        SB_SKEY,
        "Authorization": f"Bearer {SB_SKEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def load_usf_credentials():
    """
    Load USF credentials from Supabase vendor_auth (vendor_id=1).
    Falls back to USF_CONFIG + USF_REFRESH_TOKEN when no Supabase row exists.

    The environment refresh token is intentionally not allowed to override a
    Supabase token here. US Foods rotates refresh tokens, so an env token is a
    one-time bootstrap credential rather than a durable source of truth.
    """
    env_refresh_token = os.getenv("USF_REFRESH_TOKEN", "").strip()

    # 1. Try Supabase vendor_auth table
    try:
        req = urllib.request.Request(
            f"{SB_URL}/rest/v1/vendor_auth?vendor_id=eq.1&select=credentials",
            headers=_sb_svc_headers()
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        if rows:
            return dict(rows[0]["credentials"])
    except Exception:
        pass

    # 2. Fall back to env vars (CI pattern)
    if os.getenv("USF_CONFIG"):
        creds = json.loads(os.environ["USF_CONFIG"])
        creds["refresh_token"] = env_refresh_token
        return creds

    raise RuntimeError(
        "No USF credentials found. Run setup_vendor_auth.py to populate Supabase "
        "or set USF_CONFIG + USF_REFRESH_TOKEN env vars."
    )


def save_usf_refresh_token(new_refresh_token, config):
    """Persist updated refresh token to Supabase vendor_auth."""
    config["refresh_token"] = new_refresh_token
    try:
        hdrs = {**_sb_svc_headers(), "Prefer": "resolution=merge-duplicates,return=representation"}
        req = urllib.request.Request(
            f"{SB_URL}/rest/v1/vendor_auth?on_conflict=vendor_id",
            data=json.dumps({"vendor_id": 1, "credentials": config}).encode(),
            headers=hdrs, method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
    except Exception as ex:
        # Non-fatal: token will just expire sooner
        print(f"  ⚠️  Could not save USF refresh token: {ex}")


def refresh_bearer_with_fallback(config):
    """Refresh from Supabase, using the env token once if that token expired."""
    try:
        return refresh_bearer(config), config
    except RuntimeError as ex:
        env_refresh_token = os.getenv("USF_REFRESH_TOKEN", "").strip()
        stored_refresh_token = str(config.get("refresh_token", "")).strip()
        if (
            "invalid refresh token" not in str(ex).lower()
            or not env_refresh_token
            or env_refresh_token == stored_refresh_token
        ):
            raise

        fallback_config = dict(config)
        fallback_config["refresh_token"] = env_refresh_token
        return refresh_bearer(fallback_config), fallback_config


# ── Token refresh ─────────────────────────────────────────────────────────────

def _http_error_message(stage, error):
    """Return an actionable vendor error without exposing auth headers."""
    try:
        body = error.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    detail = body[:800] if body else (error.reason or "request rejected")
    return f"US Foods {stage} failed (HTTP {error.code}): {detail}"


def refresh_bearer(config):
    """Exchange refresh token for new Bearer + refresh token. Updates config."""
    hdrs = {
        "Accept":         "application/json, text/plain, */*",
        "Content-Type":   "application/json",
        "consumer-id":    config.get("consumer_id", "ecom"),
        "correlation-id": f"ecomr4-{uuid.uuid4()}",
        "transaction-id": str(int(time.time() * 1000)),
        "trace-context":  "login",
        "Origin":         "https://order.usfoods.com",
    }
    payload = {
        "grantType":   "refreshToken",
        "scopes":      config["scopes"],
        "platform":    config.get("platform", "DESKTOP"),
        "authContext": config["auth_context"],
        "refreshToken": config["refresh_token"],
    }
    req = urllib.request.Request(
        f"{API_BASE}/auth-api/v1/oauth/token",
        data=json.dumps(payload).encode(), headers=hdrs, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as ex:
        raise RuntimeError(_http_error_message("authentication", ex)) from ex

    access_token = resp.get("accessToken")
    if not access_token:
        raise RuntimeError("US Foods authentication returned no access token")
    bearer = f"{resp.get('tokenType', 'Bearer')} {access_token}"
    if resp.get("refreshToken"):
        save_usf_refresh_token(resp["refreshToken"], config)
    return bearer


# ── USF API helper ────────────────────────────────────────────────────────────

def usf_call(method, path, bearer, payload=None, params=None, stage="API request"):
    url = f"{API_BASE}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    hdrs = {
        "Accept":          "application/json, text/plain, */*",
        "Authorization":   bearer,
        "Content-Type":    "application/json",
        "consumer-id":     "ecom",
        "correlation-id":  f"ecomr4-{uuid.uuid4()}",
        "transaction-id":  str(int(time.time() * 1000)),
        "Origin":          "https://order.usfoods.com",
        "usflang":         "en",
    }
    data = json.dumps(payload).encode() if payload is not None else None
    req  = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as ex:
        raise RuntimeError(_http_error_message(stage, ex)) from ex


# ── Order placement ───────────────────────────────────────────────────────────

def get_delivery_date(bearer):
    """Return next available delivery date as ISO string (YYYY-MM-DDT00:00:00.000Z)."""
    resp = usf_call(
        "GET",
        "order-request-reply-domain-api/v1/nextDeliveryDate",
        bearer,
        stage="delivery-date lookup",
    )
    delivery_date = resp.get("deliveryDate", "") if isinstance(resp, dict) else ""
    if not delivery_date:
        raise RuntimeError("US Foods returned no available delivery date")
    return delivery_date


def _first_order(response, stage):
    """Panamax order endpoints return a one-element list in the current API."""
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict) or not response:
        raise RuntimeError(f"US Foods {stage} returned no order")
    return response


def place_order(bearer, config, items):
    """
    Create, update, and submit a US Foods in-progress order.
    items: [{"productNumber": 1085770, "qty": 3}, ...]
    Returns: {"orderId": str, "tandemOrderNumber": int, "deliveryDate": str}
    """
    delivery_date = get_delivery_date(bearer)
    auth_ctx = config.get("auth_context", {})

    order_items = [
        {
            "productNumber": item["productNumber"],
            "unitsOrdered":  item["qty"],
            "eachesOrdered": 0,
            "sequence":      (i + 1) * 10,
        }
        for i, item in enumerate(items)
    ]

    context = {
        "divisionNumber":        auth_ctx.get("divisionNumber", 1103),
        "customerNumber":        auth_ctx.get("customerNumber", 31586241),
        "departmentNumber":      auth_ctx.get("departmentNumber", 0),
        "orderType":             "RT",
        "requestedDeliveryDate": delivery_date,
        "confirmedDeliveryDate": delivery_date,
        "addOrderSource":        "MO",
        "orderItems":            [],
        "decomposeFlag":         True,
    }

    # The current web client first obtains the server-side in-progress order
    # context, then updates it with items, and finally submits that full order.
    order = _first_order(
        usf_call(
            "PUT",
            "order-domain-api/v1/orders",
            bearer,
            context,
            stage="order creation",
        ),
        "order creation",
    )

    order.update({
        "requestedDeliveryDate": delivery_date,
        "confirmedDeliveryDate": delivery_date,
        "orderItems": order_items,
        "totalUnits": sum(int(item["qty"]) for item in items),
        "totalEaches": 0,
        "decomposeFlag": True,
    })

    order = _first_order(
        usf_call(
            "PUT",
            "order-domain-api/v1/orders",
            bearer,
            order,
            stage="order update",
        ),
        "order update",
    )

    submitted = _first_order(
        usf_call(
            "POST",
            "order-submission-domain-api/v1/submitIpOrder",
            bearer,
            order,
            stage="order submission",
        ),
        "order submission",
    )

    order_id = submitted.get("orderId") or order.get("orderId") or ""
    tandem_number = (submitted.get("tandemOrderNumber")
                     or order.get("tandemOrderNumber"))

    return {
        "orderId":           order_id,
        "tandemOrderNumber": tandem_number,
        "deliveryDate":      (submitted.get("requestedDeliveryDate")
                              or delivery_date)[:10],
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

            config = load_usf_credentials()
            bearer, config = refresh_bearer_with_fallback(config)
            result = place_order(bearer, config, items)

            payload = json.dumps({
                "success":           True,
                "vendor":            "US Foods",
                "orderId":           result["orderId"],
                "tandemOrderNumber": result["tandemOrderNumber"],
                "deliveryDate":      result["deliveryDate"],
                "totalItems":        len(items),
                "error":             None,
            }).encode()

        except Exception as ex:
            import traceback
            payload = json.dumps({
                "success": False,
                "vendor":  "US Foods",
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
