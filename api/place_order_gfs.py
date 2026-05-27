"""
POST /api/place_order_gfs
==========================
Places a GFS (Gordon Food Service) order via the order.gfs.com REST API.

Body JSON:
  {"items": [{"materialNumber": "000000001001234567", "qty": 3}, ...]}

Returns JSON:
  {"success": true, "orderId": "...", "deliveryDate": "2026-05-29",
   "totalItems": N, "error": null}

Auth:
  Session cookies stored in Supabase vendor_auth (vendor_id=4).
  Falls back to GFS_COOKIES env var (JSON: {gor, gclb, xsrf, session}).

  ⚠️  GFS uses Okta SAML2 SSO with NO programmatic refresh token.
  Sessions expire after ~30 days. When expired, an admin must:
    1. Run python3 intercept_gfs2.py  locally (opens Chrome, logs in)
    2. Run python3 setup_vendor_auth.py  to upload fresh cookies to Supabase

Order flow (confirmed via network capture 2026-05-27):
  1. Load session cookies from Supabase / env var
  2. POST v8/cart             → get current active cart ID
  3. GET  v3/delivery-schedules → find next available delivery date
  4. PUT  v7/cart/{cartId}    → add items + set fulfillmentType=TRUCK + routeDate
  5. POST v6/cart/{cartId}/submit  → submit order, returns {cartOrderIds: [...]}

Required headers on every mutating call:
  Content-Type: application/json
  X-Requested-With: XMLHttpRequest
  X-XSRF-TOKEN: {value of XSRF-TOKEN cookie}
"""

import json, os, datetime, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler

# ── Config ────────────────────────────────────────────────────────────────────

SB_URL   = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY   = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_SKEY  = os.getenv("SUPABASE_SERVICE_KEY", SB_KEY)

API_BASE = "https://order.gfs.com/us-central1/api"
_UA      = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# Minimum hours before cutoff to still accept the delivery date
MIN_HOURS_BEFORE_CUTOFF = 2


# ── Credential loading ────────────────────────────────────────────────────────

def _sb_svc_headers():
    return {
        "apikey":        SB_SKEY,
        "Authorization": f"Bearer {SB_SKEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def load_gfs_cookies():
    """Load GFS session cookies from Supabase or GFS_COOKIES env var."""
    # 1. Try Supabase
    try:
        req = urllib.request.Request(
            f"{SB_URL}/rest/v1/vendor_auth?vendor_id=eq.4&select=credentials",
            headers=_sb_svc_headers()
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = json.loads(r.read())
        if rows:
            c = rows[0]["credentials"]
            return {
                "GOR":                      c.get("gor", "us-central1"),
                "GCLB":                     c.get("gclb", ""),
                "XSRF-TOKEN":               c.get("xsrf", ""),
                "__Secure-GORDONORDERING2": c.get("session", ""),
            }
    except Exception:
        pass

    # 2. Env var
    raw = os.environ.get("GFS_COOKIES")
    if raw:
        c = json.loads(raw)
        return {
            "GOR":                      c.get("gor", "us-central1"),
            "GCLB":                     c.get("gclb", ""),
            "XSRF-TOKEN":               c.get("xsrf", ""),
            "__Secure-GORDONORDERING2": c.get("session", ""),
        }

    raise RuntimeError(
        "No GFS_COOKIES found. Run intercept_gfs2.py then setup_vendor_auth.py."
    )


# ── GFS HTTP helpers ──────────────────────────────────────────────────────────

def _cookie_header(cookies):
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)


def _gfs_headers(cookies, extra=None):
    """Base headers for all GFS API requests."""
    h = {
        "Cookie":              _cookie_header(cookies),
        "X-XSRF-TOKEN":        cookies.get("XSRF-TOKEN", ""),
        "X-Requested-With":    "XMLHttpRequest",   # required — without this GFS returns 218
        "Accept":              "application/json, text/plain, */*",
        "Content-Type":        "application/json",
        "Origin":              "https://order.gfs.com",
        "Referer":             "https://order.gfs.com/cart",
        "User-Agent":          _UA,
        "sec-fetch-site":      "same-origin",
        "sec-fetch-mode":      "cors",
    }
    if extra:
        h.update(extra)
    return h


def _gfs_request(method, path, body, cookies):
    """Execute a GFS API request, return parsed JSON."""
    url  = f"{API_BASE}/{path}"
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(
        url, data=data,
        headers=_gfs_headers(cookies),
        method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            if not raw:
                return {}
            result = json.loads(raw)
            # GFS returns HTTP 200 but with {"error": {"code": "default.error"}} for bad calls
            if isinstance(result, dict) and "error" in result:
                raise RuntimeError(
                    f"GFS {method} {path} → API error: {result['error']}"
                )
            return result
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError(
                f"GFS session expired (HTTP {e.code}). "
                "Re-login: python3 intercept_gfs2.py → python3 setup_vendor_auth.py"
            )
        body_txt = e.read().decode()[:300]
        raise RuntimeError(f"GFS {method} {path} → HTTP {e.code}: {body_txt}")


def gfs_get(path, cookies):
    return _gfs_request("GET", path, None, cookies)

def gfs_post(path, body, cookies):
    return _gfs_request("POST", path, body, cookies)

def gfs_put(path, body, cookies):
    return _gfs_request("PUT", path, body, cookies)


# ── Order placement helpers ───────────────────────────────────────────────────

def validate_session(cookies):
    """Validate GFS session is still active. Returns True or raises."""
    url = f"{API_BASE}/v4/session"
    req = urllib.request.Request(url, headers=_gfs_headers(cookies), method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("GFS session expired — re-run intercept_gfs2.py")
        return True
    except Exception:
        return True


def get_next_route_date(cookies):
    """
    Fetch delivery schedules and return the soonest route date whose cutoff
    hasn't passed (with MIN_HOURS_BEFORE_CUTOFF buffer).

    Returns ISO date string: "2026-05-29"
    """
    schedules = gfs_get("v3/delivery-schedules", cookies)
    entries   = schedules.get("deliverySchedules", [])
    if not entries:
        raise RuntimeError("GFS delivery-schedules returned empty list")

    now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
    buffer = datetime.timedelta(hours=MIN_HOURS_BEFORE_CUTOFF)

    for entry in entries:
        cutoff_str = entry.get("cutoffDateTime", "")
        if not cutoff_str:
            return entry["routeDate"]
        # Parse "2026-05-28T21:00:00+0000"
        cutoff_str_normalized = cutoff_str.replace("+0000", "+00:00")
        try:
            cutoff = datetime.datetime.fromisoformat(cutoff_str_normalized)
        except ValueError:
            # fallback: skip timezone parsing
            cutoff = datetime.datetime.strptime(cutoff_str[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=datetime.timezone.utc
            )
        if now + buffer < cutoff:
            return entry["routeDate"]

    raise RuntimeError(
        "No GFS delivery date available — all upcoming cutoffs have passed."
    )


def place_gfs_order(cookies, items):
    """
    Place a GFS truck delivery order.

    items: [{"materialNumber": "282537", "qty": 2}, ...]

    Returns: {"orderId": "...", "deliveryDate": "2026-05-29",
              "cartOrderIds": [...], "cartId": "..."}
    """
    validate_session(cookies)

    # ── Step 1: Get current active cart ──────────────────────────────────────
    cart    = gfs_post("v8/cart", {}, cookies)
    cart_id = cart.get("id")
    if not cart_id:
        raise RuntimeError(f"Could not retrieve GFS cart ID. Response: {cart}")

    # ── Step 2: Get next available delivery date ──────────────────────────────
    route_date = get_next_route_date(cookies)

    # ── Step 3: PUT v7/cart/{id} — add items + set delivery method/date ───────
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"

    materials = [
        {
            "materialNumber":   str(item["materialNumber"]),
            "lines":            [{"uom": "CS", "quantity": int(item["qty"])}],
            "restored":         False,
            "originTrackingId": None,
        }
        for item in items
        if int(item.get("qty", 0)) > 0
    ]

    if not materials:
        raise ValueError("All item quantities are 0 — nothing to order")

    put_body = {
        "userLastUpdatedTimestamp": ts,
        "fulfillmentType":          "TRUCK",
        "truckFulfillment": {
            "routeDate":            route_date,
            "customerArrivalDate":  route_date,
        },
        "materials": materials,
    }

    gfs_put(f"v7/cart/{cart_id}", put_body, cookies)

    # ── Step 4: Submit order ───────────────────────────────────────────────────
    submit_resp = gfs_post(
        f"v6/cart/{cart_id}/submit",
        {"splitOrders": []},
        cookies
    )

    cart_order_ids = submit_resp.get("cartOrderIds", [])
    order_id       = cart_order_ids[0] if cart_order_ids else "submitted"

    return {
        "orderId":      str(order_id),
        "deliveryDate": route_date,
        "cartOrderIds": cart_order_ids,
        "cartId":       cart_id,
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
            cookies = load_gfs_cookies()
            result  = place_gfs_order(cookies, items)
            payload = json.dumps({
                "success":      True,
                "vendor":       "GFS",
                "orderId":      result["orderId"],
                "deliveryDate": result["deliveryDate"],
                "totalItems":   len(items),
                "cartOrderIds": result.get("cartOrderIds", []),
                "error":        None,
            }).encode()

        except Exception as ex:
            import traceback
            payload = json.dumps({
                "success": False,
                "vendor":  "GFS",
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
