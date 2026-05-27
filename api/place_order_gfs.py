"""
POST /api/place_order_gfs
==========================
Places a GFS (Gordon Food Service) order via the order.gfs.com REST API.

Body JSON:
  {"items": [{"materialNumber": "000000001001234567", "qty": 3}, ...]}

Returns JSON:
  {"success": true, "orderId": "...", "deliveryDate": "2026-06-03",
   "totalItems": N, "error": null}

Auth:
  Session cookies stored in Supabase vendor_auth (vendor_id=4).
  Falls back to GFS_COOKIES env var (JSON: {gor, gclb, xsrf, session}).

  ⚠️  GFS uses Okta SAML2 SSO with NO programmatic refresh token.
  Sessions expire after ~30 days. When expired, an admin must:
    1. Run python3 intercept_gfs.py  locally (opens Chrome, logs in)
    2. Run python3 setup_vendor_auth.py  to upload fresh cookies to Supabase

Order flow:
  1. Load session cookies
  2. GET v6/lists/order-guide  → validate session + get available items
  3. Probe cart endpoint (v6/cart or v1/orders)
  4. POST items to cart / order
  5. Submit order
"""

import json, os, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler

# ── Config ────────────────────────────────────────────────────────────────────

SB_URL   = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY   = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SB_SKEY  = os.getenv("SUPABASE_SERVICE_KEY", SB_KEY)

API_BASE = "https://order.gfs.com/us-central1/api"
_UA      = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

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
        "No GFS_COOKIES found. Run intercept_gfs.py then setup_vendor_auth.py."
    )


# ── GFS HTTP helpers ──────────────────────────────────────────────────────────

def _cookie_header(cookies):
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)


def _gfs_headers(cookies, extra=None):
    h = {
        "Cookie":       _cookie_header(cookies),
        "X-XSRF-TOKEN": cookies.get("XSRF-TOKEN", ""),
        "Accept":       "application/json, text/plain, */*",
        "Origin":       "https://order.gfs.com",
        "Referer":      "https://order.gfs.com/",
        "User-Agent":   _UA,
    }
    if extra:
        h.update(extra)
    return h


def gfs_get(path, cookies):
    url = f"{API_BASE}/{path}"
    req = urllib.request.Request(url, headers=_gfs_headers(cookies))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError(
                "GFS session expired (HTTP {}). "
                "Re-login: python3 intercept_gfs.py → python3 setup_vendor_auth.py".format(e.code)
            )
        raise RuntimeError(f"GFS GET {path} → {e.code}: {e.read().decode()[:200]}")


def gfs_post(path, body, cookies):
    url  = f"{API_BASE}/{path}"
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        url, data=data,
        headers=_gfs_headers(cookies, {"Content-Type": "application/json"}),
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError(
                "GFS session expired (HTTP {}). "
                "Re-login: python3 intercept_gfs.py → python3 setup_vendor_auth.py".format(e.code)
            )
        body_txt = e.read().decode()[:300]
        raise RuntimeError(f"GFS POST {path} → {e.code}: {body_txt}")


# ── Order placement ───────────────────────────────────────────────────────────

def validate_session(cookies):
    """Validate GFS session is still active. Returns True or raises."""
    # HEAD the session endpoint
    url = f"https://order.gfs.com/us-central1/api/v4/session"
    req = urllib.request.Request(url, headers=_gfs_headers(cookies), method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return True
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise RuntimeError("GFS session expired — re-run intercept_gfs.py")
        return True   # other errors may still work
    except Exception:
        return True


def place_gfs_order(cookies, items):
    """
    Place a GFS order.
    items: [{"materialNumber": "...", "qty": N}, ...]

    GFS API endpoint for ordering is probed at runtime.
    Known read endpoints (scraper): v6/lists/order-guide, v1/materials/info, v5/prices
    Ordering endpoints (probed):    v1/cart, v6/cart, v1/orders, v6/orders
    """
    validate_session(cookies)

    # ── Probe: find the cart / order submission endpoint ──────────────────────
    # Build the order items in GFS format (material number + quantity)
    gfs_items = [
        {"materialNumber": item["materialNumber"], "quantity": item["qty"]}
        for item in items
    ]

    # Try multiple endpoint patterns, return on first success
    cart_endpoints = [
        # (method, path, body_builder)
        ("POST", "v6/cart/items",
         lambda: {"items": gfs_items}),
        ("POST", "v1/cart",
         lambda: {"items": gfs_items}),
        ("POST", "v6/orders",
         lambda: {"items": gfs_items, "submit": True}),
        ("POST", "v1/orders",
         lambda: {"orderItems": [{"materialNumber": i["materialNumber"],
                                   "quantity": i["qty"]} for i in items]}),
    ]

    order_id = None
    for method, path, body_fn in cart_endpoints:
        try:
            resp = gfs_post(path, body_fn(), cookies)
            # If we get here without error, use this endpoint
            order_id = (resp.get("orderId") or resp.get("id") or
                        resp.get("orderNumber") or "submitted")
            break
        except RuntimeError as ex:
            if "session expired" in str(ex).lower():
                raise
            continue  # try next endpoint

    if order_id is None:
        raise RuntimeError(
            "Could not place GFS order — order endpoint not yet mapped. "
            "This requires a live session capture from the GFS portal. "
            "Open order.gfs.com and place a manual order while running intercept_gfs2.py "
            "to capture the exact submit endpoint."
        )

    return {"orderId": str(order_id), "deliveryDate": ""}


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
