"""
POST /api/place_order_usfoods
==============================
Places a US Foods order via the panamax REST API.

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

import json, os, uuid, time, urllib.request, urllib.error, datetime
from http.server import BaseHTTPRequestHandler

# ── Config ────────────────────────────────────────────────────────────────────

SB_URL   = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY   = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_SKEY  = os.getenv("SUPABASE_SERVICE_KEY", SB_KEY)   # service role key for vendor_auth

API_BASE = "https://panamax-api.ama.usfoods.com"

SB_HDRS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type":  "application/json",
}

# ── Credential loading / saving ───────────────────────────────────────────────

def _sb_svc_headers():
    """Headers using service role key (for vendor_auth table)."""
    return {
        "apikey":        SB_SKEY,
        "Authorization": f"Bearer {SB_SKEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def load_usf_credentials():
    """
    Load USF credentials from Supabase vendor_auth (vendor_id=1).
    Falls back to USF_REFRESH_TOKEN + USF_CONFIG env vars.
    """
    # 1. Try Supabase vendor_auth table
    try:
        req = urllib.request.Request(
            f"{SB_URL}/rest/v1/vendor_auth?vendor_id=eq.1&select=credentials",
            headers=_sb_svc_headers()
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        if rows:
            return rows[0]["credentials"]
    except Exception:
        pass

    # 2. Fall back to env vars (CI pattern)
    if os.getenv("USF_CONFIG"):
        creds = json.loads(os.environ["USF_CONFIG"])
        creds["refresh_token"] = os.environ.get("USF_REFRESH_TOKEN", "")
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


# ── Token refresh ─────────────────────────────────────────────────────────────

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
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read())

    bearer = f"{resp['tokenType']} {resp['accessToken']}"
    save_usf_refresh_token(resp["refreshToken"], config)
    return bearer


# ── USF API helper ────────────────────────────────────────────────────────────

def usf_call(method, path, bearer, payload=None, params=None):
    url = f"{API_BASE}/{path}"
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
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
    data = json.dumps(payload).encode() if payload else None
    req  = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


# ── Order placement ───────────────────────────────────────────────────────────

def get_delivery_date(bearer):
    """Return next available delivery date as ISO string (YYYY-MM-DDT00:00:00.000Z)."""
    try:
        resp = usf_call("GET", "order-request-reply-domain-api/v1/nextDeliveryDate", bearer)
        return resp.get("deliveryDate", "")
    except Exception:
        # Fallback: next Thursday (USF typically delivers Thu/Fri)
        today = datetime.date.today()
        days_ahead = (3 - today.weekday()) % 7 or 7   # next Thursday
        d = today + datetime.timedelta(days=days_ahead)
        return f"{d.isoformat()}T00:00:00.000Z"


def place_order(bearer, config, items):
    """
    Create and submit a US Foods order in a single POST.
    items: [{"productNumber": 1085770, "qty": 3}, ...]
    Returns: {"orderId": str, "tandemOrderNumber": int, "deliveryDate": str}
    """
    delivery_date = get_delivery_date(bearer)
    auth_ctx = config.get("auth_context", {})

    order_items = [
        {
            "productNumber": item["productNumber"],
            "unitsOrdered":  item["qty"],
            "sequence":      (i + 1) * 10,
        }
        for i, item in enumerate(items)
    ]

    body = {
        "divisionNumber":        auth_ctx.get("divisionNumber", 1103),
        "customerNumber":        auth_ctx.get("customerNumber", 31586241),
        "departmentNumber":      auth_ctx.get("departmentNumber", 0),
        "orderType":             "RT",
        "requestedDeliveryDate": delivery_date,
        "addOrderSource":        "MO",
        "orderItems":            order_items,
    }

    resp = usf_call("POST", "order-domain-api/v1/orders", bearer, body)

    # Response may be the order object or wrapped in a list
    if isinstance(resp, list):
        resp = resp[0] if resp else {}

    return {
        "orderId":           resp.get("orderId", ""),
        "tandemOrderNumber": resp.get("tandemOrderNumber"),
        "deliveryDate":      delivery_date[:10],
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
            bearer = refresh_bearer(config)
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
