"""
POST /api/place_order_sysco
============================
Places a Sysco order via GraphQL (Okta SAML2 auth + gateway-api).

Body JSON:
  {"items": [{"productId": "0534567", "qty": 3}, ...]}

Returns JSON:
  {"success": true, "orderId": "...", "orderNumber": "...",
   "deliveryDate": "2026-06-03", "totalItems": N, "error": null}

Auth:
  Okta SAML2 programmatic flow (same as price scraper).
  SYSCO_EMAIL + SYSCO_PASSWORD env vars required.
  No token rotation needed — re-authenticates on every call (~15s).

Order flow:
  1. Authenticate → Bearer token
  2. getDeliveryV2 → next delivery date
  3. addToCart mutation → add all items
  4. submitOrderV2 / placeOrder mutation → submit
  5. Return order ID / number
"""

import base64, json, os, re, sys, time, urllib.request, urllib.error, urllib.parse
import http.cookiejar, html.parser
from http.server import BaseHTTPRequestHandler

# ── Config ────────────────────────────────────────────────────────────────────

GQL_URL   = "https://gateway-api.shop.sysco.com/graphql"
AUTH_BASE = "https://auth.shop.sysco.com"
OKTA_BASE = "https://secure.sysco.com"

SELLER_ID       = "USBL"
SITE_ID         = "019"
SHOP_ACCOUNT_ID = "usbl-019-700932"

EMAIL    = os.getenv("SYSCO_EMAIL",    "carlos@onparbar.com")
PASSWORD = os.getenv("SYSCO_PASSWORD", "")

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# ── Auth (copied from scrape_sysco.py) ────────────────────────────────────────
# Keeping self-contained so Vercel can import this without the scraper.

class _FormParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self.action = None
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("method", "").upper() == "POST":
            self.action = attrs.get("action", "")
        if tag == "input" and attrs.get("type", "").lower() == "hidden":
            name = attrs.get("name", "")
            if name:
                self.fields[name] = attrs.get("value", "")


def _extract_state_token(html_text):
    for pat in [
        r'"stateToken"\s*:\s*"([^"]{10,})"',
        r"'stateToken'\s*:\s*'([^']{10,})'",
        r'stateToken[\s:="\']+([0-9A-Za-z_\-]{20,})',
    ]:
        m = re.search(pat, html_text)
        if m:
            tok = m.group(1)
            tok = re.sub(r'\\x([0-9A-Fa-f]{2})', lambda h: chr(int(h.group(1), 16)), tok)
            tok = re.sub(r'\\u([0-9A-Fa-f]{4})', lambda h: chr(int(h.group(1), 16)), tok)
            return tok
    return None


def _build_syy_auth(shop_account_id, seller_id, site_id):
    SYY_HASH = "bc038006687544baa90fb5021c9432ee"
    payload = {
        "data": {
            "shopAccountId": shop_account_id,
            "sellers": {
                seller_id: {"siteId": site_id, "sellerAccountId": "700932"}
            },
            "shopUserType": "multi_buyer",
            "country": "US",
        },
        "_hash": SYY_HASH,
    }
    return base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")


def get_bearer(email, password):
    """Full Okta SAML2 auth flow → (bearer, shop_account_id, csrf_token, vid)."""
    jar    = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # Step 1
    sso_req = urllib.request.Request(
        f"{AUTH_BASE}/api/v1/auth/sso",
        data=json.dumps({"email": email}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": _UA, "Origin": "https://shop.sysco.com",
                 "Referer": "https://shop.sysco.com/"}
    )
    with opener.open(sso_req, timeout=20) as r:
        sso = json.loads(r.read())
    redirect_to = (sso.get("data") or {}).get("redirectTo", "")
    if not redirect_to:
        raise RuntimeError(f"No redirectTo: {sso}")

    # Step 2
    with opener.open(urllib.request.Request(
        redirect_to,
        headers={"User-Agent": _UA, "Accept": "text/html,*/*",
                 "Referer": "https://shop.sysco.com/"}
    ), timeout=20) as r:
        page_html = r.read().decode("utf-8", errors="replace")
    state_token = _extract_state_token(page_html)
    if not state_token:
        raise RuntimeError("Could not extract stateToken from Okta page")

    # Step 3
    authn_req = urllib.request.Request(
        f"{OKTA_BASE}/api/v1/authn",
        data=json.dumps({"password": password, "username": email,
                         "options": {"warnBeforePasswordExpired": True,
                                     "multiOptionalFactorEnroll": False},
                         "stateToken": state_token}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": _UA, "Origin": OKTA_BASE, "Referer": f"{OKTA_BASE}/"}
    )
    with opener.open(authn_req, timeout=20) as r:
        authn = json.loads(r.read())
    if authn.get("status") != "SUCCESS":
        raise RuntimeError(f"Okta authn failed: {authn.get('status')}")

    # Step 4
    step_url = (f"{OKTA_BASE}/login/step-up/redirect"
                f"?stateToken={urllib.parse.quote(state_token)}")
    with opener.open(urllib.request.Request(
        step_url,
        headers={"User-Agent": _UA, "Accept": "text/html,*/*",
                 "Referer": f"{OKTA_BASE}/"}
    ), timeout=20) as r:
        step_html = r.read().decode("utf-8", errors="replace")

    fp = _FormParser()
    fp.feed(step_html)
    saml_response = fp.fields.get("SAMLResponse", "")
    relay_state   = fp.fields.get("RelayState", "")
    form_action   = fp.action or f"{AUTH_BASE}/api/v1/auth/sso/assert"
    if not saml_response:
        raise RuntimeError("No SAMLResponse in step-up HTML")

    # Step 5
    try:
        with opener.open(urllib.request.Request(
            form_action,
            data=urllib.parse.urlencode({"SAMLResponse": saml_response,
                                          "RelayState": relay_state}).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "User-Agent": _UA, "Accept": "text/html,*/*",
                     "Origin": OKTA_BASE, "Referer": f"{OKTA_BASE}/"}
        ), timeout=20) as r:
            r.read()
    except urllib.error.HTTPError as e:
        if e.code not in (302,):
            raise

    # Step 6
    with opener.open(urllib.request.Request(
        f"{AUTH_BASE}/api/v1/auth/validate",
        headers={"User-Agent": _UA, "Accept": "application/json",
                 "Origin": "https://shop.sysco.com",
                 "Referer": "https://shop.sysco.com/"}
    ), timeout=20) as r:
        val = json.loads(r.read())

    if val.get("role") != "CUSTOMER":
        raise RuntimeError(f"Expected CUSTOMER role, got {val.get('role')}")

    creds     = val.get("gatewayCredentials", "")
    shop_acct = val.get("shopAccountId", SHOP_ACCOUNT_ID)
    try:
        pl_b64   = creds.split(".")[1]
        pl_b64  += "=" * (4 - len(pl_b64) % 4)
        jwt_pl   = json.loads(base64.b64decode(pl_b64))
        csrf_tok = jwt_pl.get("csrf_token", "")
        vid      = jwt_pl.get("vid", "")
    except Exception:
        csrf_tok = vid = ""

    return f"Bearer {creds}", shop_acct, csrf_tok, vid


# ── GraphQL helper ────────────────────────────────────────────────────────────

def gql(bearer, op_name, query, variables, ctx=None):
    ctx      = ctx or {}
    csrf_tok = ctx.get("csrf_token", "")
    vid      = ctx.get("vid", "")
    shop_id  = ctx.get("shop_account_id", SHOP_ACCOUNT_ID)
    syy_auth = _build_syy_auth(shop_id, SELLER_ID, SITE_ID)
    corr_id  = hex(int(time.time() * 1000))[2:]

    req = urllib.request.Request(
        GQL_URL,
        data=json.dumps({"operationName": op_name, "variables": variables,
                         "query": query}).encode(),
        headers={
            "Authorization":                bearer,
            "Content-Type":                 "application/json",
            "Accept":                       "application/json",
            "apollographql-client-name":    "SYSCO_SHOP_WEB",
            "apollographql-client-version": "1",
            "Origin":                       "https://shop.sysco.com",
            "Referer":                      "https://shop.sysco.com/",
            "User-Agent":                   _UA,
            "syy-authorization":            syy_auth,
            "syy-experience":               "exp-usbl",
            "syy-pricing-version":          "2",
            "syy-request-tier":             "priority",
            "syy-request-type":             "write",
            "syy-site":                     SITE_ID,
            "syy-source":                   "web",
            "syy-visitor-id":               vid,
            "syy-requested-by":             csrf_tok,
            "syy-correlation-id":           corr_id,
        }
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


# ── GraphQL queries / mutations ───────────────────────────────────────────────

_GQL_DELIVERY = """
query getDeliveryV2($sellerId: String, $siteId: String) {
  getDeliveryV2(sellerId: $sellerId, siteId: $siteId) {
    deliveryDates {
      date
      cutOffDate
      isDefault
    }
  }
}
"""

_GQL_ADD_TO_CART_TMPL = """
mutation AddToCart {{
  addToCart(
    items: [{items}],
    sellerId: "{seller}",
    siteId: "{site}"
  ) {{
    cartId
    totalQuantity
    cartHash
  }}
}}
"""

# Sysco uses several possible submit mutations — try each until one works
_SUBMIT_MUTATIONS = [
    ("submitOrderV2",
     'mutation SubmitOrder{submitOrderV2(sellerId:"%s",siteId:"%s"){orderId orderNumber status deliveryDate}}'),
    ("submitOrder",
     'mutation SubmitOrder{submitOrder(sellerId:"%s",siteId:"%s"){orderId orderNumber status}}'),
    ("placeOrderV2",
     'mutation PlaceOrder{placeOrderV2(sellerId:"%s",siteId:"%s"){orderId}}'),
    ("placeOrder",
     'mutation PlaceOrder{placeOrder(sellerId:"%s",siteId:"%s"){orderId}}'),
    ("checkoutV2",
     'mutation Checkout{checkoutV2(sellerId:"%s",siteId:"%s"){orderId orderNumber}}'),
]


# ── Order placement ───────────────────────────────────────────────────────────

def get_delivery_date(bearer, ctx):
    """Return the default (next) delivery date string."""
    try:
        resp = gql(bearer, "getDeliveryV2", _GQL_DELIVERY,
                   {"sellerId": SELLER_ID, "siteId": SITE_ID}, ctx=ctx)
        dates = ((resp.get("data") or {})
                 .get("getDeliveryV2", {})
                 .get("deliveryDates", []))
        for d in dates:
            if d.get("isDefault"):
                return d["date"]
        if dates:
            return dates[0]["date"]
    except Exception:
        pass
    return ""


def add_items_to_cart(bearer, ctx, items):
    """
    Add items to cart using inline mutation (avoids CartItemInput type issue).
    items: [{"productId": "...", "qty": N}, ...]
    """
    # Build inline item objects
    item_strs = []
    for item in items:
        pid = item["productId"]
        qty = item["qty"]
        item_strs.append(
            f'{{productId:"{pid}",quantity:{qty},splitCode:"CASE",'
            f'sellerId:"{SELLER_ID}",siteId:"{SITE_ID}"}}'
        )

    # Try a few field combinations — Sysco may expect slightly different fields
    for items_fragment in [
        ", ".join(item_strs),                                     # with seller/site in item
        ", ".join(                                                 # without seller/site in item
            f'{{productId:"{i["productId"]}",quantity:{i["qty"]},splitCode:"CASE"}}'
            for i in items
        ),
        ", ".join(                                                 # minimal
            f'{{productId:"{i["productId"]}",quantity:{i["qty"]}}}'
            for i in items
        ),
    ]:
        mutation = (
            f'mutation AddToCart{{addToCart('
            f'items:[{items_fragment}],'
            f'sellerId:"{SELLER_ID}",siteId:"{SITE_ID}")'
            f'{{cartId totalQuantity cartHash}}}}'
        )
        try:
            resp = gql(bearer, "AddToCart", mutation, {}, ctx=ctx)
            errs = resp.get("errors", [])
            if not errs:
                data = (resp.get("data") or {}).get("addToCart") or {}
                return data.get("cartId", "")
            # Check if it's a "field doesn't exist" error vs "wrong args"
            msg = errs[0].get("message", "")
            if "Cannot query field" in msg or "Unknown field" in msg:
                raise RuntimeError(f"addToCart mutation not found: {msg}")
            # Wrong args but mutation exists — try next variant
            continue
        except RuntimeError:
            raise
        except Exception:
            continue

    raise RuntimeError("Could not add items to Sysco cart (all item format variants failed)")


def submit_sysco_order(bearer, ctx):
    """Try each submit mutation until one succeeds."""
    for op_name, mutation_tmpl in _SUBMIT_MUTATIONS:
        mutation = mutation_tmpl % (SELLER_ID, SITE_ID)
        try:
            resp = gql(bearer, op_name, mutation, {}, ctx=ctx)
            errs = resp.get("errors", [])
            if not errs:
                data = (resp.get("data") or {}).get(op_name) or {}
                return {
                    "orderId":     data.get("orderId", ""),
                    "orderNumber": data.get("orderNumber", ""),
                    "status":      data.get("status", ""),
                    "deliveryDate": data.get("deliveryDate", ""),
                }
            msg = errs[0].get("message", "")
            if "Cannot query field" in msg or "Unknown field" in msg:
                continue  # mutation doesn't exist, try next
            # Mutation exists but returned an error
            raise RuntimeError(f"{op_name} error: {msg}")
        except RuntimeError:
            raise
        except Exception:
            continue

    raise RuntimeError("No Sysco submit mutation found (tried 5 variants). "
                       "Manual order placement required for Sysco.")


def place_sysco_order(items):
    """Full Sysco order placement flow."""
    if not PASSWORD:
        raise RuntimeError("SYSCO_PASSWORD env var not set")

    bearer, shop_acct, csrf_tok, vid = get_bearer(EMAIL, PASSWORD)
    ctx = {"csrf_token": csrf_tok, "vid": vid,
           "shop_account_id": shop_acct, "site_id": SITE_ID}

    delivery_date = get_delivery_date(bearer, ctx)
    add_items_to_cart(bearer, ctx, items)
    result        = submit_sysco_order(bearer, ctx)

    return {
        "orderId":      result["orderId"],
        "orderNumber":  result["orderNumber"],
        "deliveryDate": result.get("deliveryDate") or delivery_date,
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
            result  = place_sysco_order(items)
            payload = json.dumps({
                "success":      True,
                "vendor":       "Sysco",
                "orderId":      result["orderId"],
                "orderNumber":  result["orderNumber"],
                "deliveryDate": result["deliveryDate"],
                "totalItems":   len(items),
                "error":        None,
            }).encode()

        except Exception as ex:
            import traceback
            payload = json.dumps({
                "success": False,
                "vendor":  "Sysco",
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
