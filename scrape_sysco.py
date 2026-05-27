"""
Sysco price scraper — programmatic Okta SAML2 auth, GraphQL queries.
No browser required in CI.

Auth flow:
  1. POST auth.shop.sysco.com/api/v1/auth/sso {email} → redirectTo (Okta SAML URL)
  2. GET redirectTo (CookieJar) → extract stateToken from page HTML
  3. POST secure.sysco.com/api/v1/authn {username, password, stateToken} → SUCCESS
  4. GET secure.sysco.com/login/step-up/redirect?stateToken=... → HTML form (SAMLResponse)
  5. POST auth.shop.sysco.com/api/v1/auth/sso/assert form → sets MSS_STATEFUL cookie
  6. GET auth.shop.sysco.com/api/v1/auth/validate → {gatewayCredentials: JWT}

GraphQL (gateway-api.shop.sysco.com/graphql):
  - GetListItemsV2  → Order Guide 8.5.25 (58 items, listType MY_LIST)
  - Prices          → priceInfoV2.case.netPrice for each product

CI secrets required:
  SYSCO_EMAIL      carlos@onparbar.com
  SYSCO_PASSWORD   !Compass1066
  SUPABASE_URL     https://gnkwdoohzspomvdshzge.supabase.co
  SUPABASE_KEY     sb_publishable_…

Supabase: vendor_id=3 (SYSCO), season from PRICE_SEASON env var.

If re-auth fails (Okta MFA or policy change), debug HTML is saved to /tmp/sysco_*.html.
"""

import base64, json, os, re, sys, time, urllib.request, urllib.error, urllib.parse
import http.cookiejar, html.parser

# ── Config ───────────────────────────────────────────────────────────────────

GQL_URL   = "https://gateway-api.shop.sysco.com/graphql"
AUTH_BASE = "https://auth.shop.sysco.com"
OKTA_BASE = "https://secure.sysco.com"

SELLER_ID             = "USBL"
SITE_ID               = "019"
SHOP_ACCOUNT_ID       = "usbl-019-700932"
ORDER_GUIDE_LIST_ID   = "66a83a1e-8c6f-4e83-820e-f485012da85f"
ORDER_GUIDE_LIST_TYPE = "MY_LIST"
PAGE_SIZE             = 60   # max page size for GetListItemsV2

SB_URL    = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY    = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SEASON    = os.getenv("PRICE_SEASON", "Spring 2026")
VENDOR_ID = 3   # SYSCO

EMAIL    = os.getenv("SYSCO_EMAIL",    "carlos@onparbar.com")
PASSWORD = os.getenv("SYSCO_PASSWORD", "")

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

SB_HDRS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

# ── HTML helpers ─────────────────────────────────────────────────────────────

class _FormParser(html.parser.HTMLParser):
    """Collects hidden form fields and the form action URL."""
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
            value = attrs.get("value", "")
            if name:
                self.fields[name] = value


def _extract_state_token(html_text):
    """Extract Okta stateToken from page HTML (embedded in JS by Okta widget)."""
    for pat in [
        r'"stateToken"\s*:\s*"([^"]{10,})"',
        r"'stateToken'\s*:\s*'([^']{10,})'",
        r'stateToken[\s:="\']+([0-9A-Za-z_\-]{20,})',
    ]:
        m = re.search(pat, html_text)
        if m:
            tok = m.group(1)
            # Decode JS hex escapes (e.g. \x2D → '-') that Okta embeds in JS strings
            tok = re.sub(r'\\x([0-9A-Fa-f]{2})',
                         lambda h: chr(int(h.group(1), 16)), tok)
            # Decode JS unicode escapes (e.g. - → '-')
            tok = re.sub(r'\\u([0-9A-Fa-f]{4})',
                         lambda h: chr(int(h.group(1), 16)), tok)
            return tok
    return None


# ── Auth flow ─────────────────────────────────────────────────────────────────

def get_bearer_token(email, password):
    """
    Authenticate via Okta SAML2 step-up flow and return
    (bearer_header, shop_account_id).
    """
    jar    = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # ── Step 1: POST auth/sso to get Okta SAML redirect URL ──────────────────
    print("  [1] POST auth/sso ...")
    sso_req = urllib.request.Request(
        f"{AUTH_BASE}/api/v1/auth/sso",
        data=json.dumps({"email": email}).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   _UA,
            "Origin":       "https://shop.sysco.com",
            "Referer":      "https://shop.sysco.com/",
        },
    )
    with opener.open(sso_req, timeout=20) as r:
        sso_resp = json.loads(r.read())

    redirect_to = (sso_resp.get("data") or {}).get("redirectTo", "")
    if not redirect_to:
        raise RuntimeError(f"No redirectTo in auth/sso response: {sso_resp}")
    print(f"  [1] redirectTo: {redirect_to[:80]}...")

    # ── Step 2: GET Okta SAML page, extract stateToken ───────────────────────
    print("  [2] GET Okta SAML page ...")
    saml_req = urllib.request.Request(
        redirect_to,
        headers={
            "User-Agent": _UA,
            "Accept":     "text/html,application/xhtml+xml,*/*",
            "Referer":    "https://shop.sysco.com/",
        },
    )
    with opener.open(saml_req, timeout=20) as r:
        page_html = r.read().decode("utf-8", errors="replace")

    state_token = _extract_state_token(page_html)
    if not state_token:
        with open("/tmp/sysco_okta_debug.html", "w") as f:
            f.write(page_html[:8000])
        raise RuntimeError(
            "Could not extract stateToken from Okta page. "
            "Debug HTML saved to /tmp/sysco_okta_debug.html"
        )
    print(f"  [2] stateToken: {state_token[:30]}...")

    # ── Step 3: POST authn with username + password ───────────────────────────
    print("  [3] POST authn (password) ...")
    authn_req = urllib.request.Request(
        f"{OKTA_BASE}/api/v1/authn",
        data=json.dumps({
            "password":   password,
            "username":   email,
            "options":    {"warnBeforePasswordExpired": True,
                           "multiOptionalFactorEnroll": False},
            "stateToken": state_token,
        }).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept":       "application/json",
            "User-Agent":   _UA,
            "Origin":       OKTA_BASE,
            "Referer":      f"{OKTA_BASE}/",
        },
    )
    try:
        with opener.open(authn_req, timeout=20) as r:
            authn_resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:400]
        raise RuntimeError(f"Okta /api/v1/authn → {e.code}: {body}")

    status = authn_resp.get("status", "")
    if status != "SUCCESS":
        raise RuntimeError(
            f"Okta authn failed: status={status}. "
            f"Response: {json.dumps(authn_resp)[:300]}"
        )
    print(f"  [3] authn status: {status}")

    # ── Step 4: GET step-up redirect → HTML form with SAMLResponse ────────────
    print("  [4] GET step-up/redirect ...")
    step_url = (f"{OKTA_BASE}/login/step-up/redirect"
                f"?stateToken={urllib.parse.quote(state_token)}")
    step_req = urllib.request.Request(
        step_url,
        headers={
            "User-Agent": _UA,
            "Accept":     "text/html,application/xhtml+xml,*/*",
            "Referer":    f"{OKTA_BASE}/",
        },
    )
    with opener.open(step_req, timeout=20) as r:
        step_html = r.read().decode("utf-8", errors="replace")

    parser = _FormParser()
    parser.feed(step_html)
    saml_response = parser.fields.get("SAMLResponse", "")
    relay_state   = parser.fields.get("RelayState",   "")
    form_action   = parser.action or f"{AUTH_BASE}/api/v1/auth/sso/assert"

    if not saml_response:
        with open("/tmp/sysco_stepup_debug.html", "w") as f:
            f.write(step_html[:8000])
        raise RuntimeError(
            "No SAMLResponse in step-up HTML. "
            "Debug HTML saved to /tmp/sysco_stepup_debug.html"
        )
    print(f"  [4] SAMLResponse len={len(saml_response)}  RelayState: {relay_state[:40]}...")

    # ── Step 5: POST SAML assertion → sets MSS_STATEFUL cookie ───────────────
    print("  [5] POST sso/assert ...")
    assert_body = urllib.parse.urlencode({
        "SAMLResponse": saml_response,
        "RelayState":   relay_state,
    }).encode()
    assert_req = urllib.request.Request(
        form_action,
        data=assert_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent":   _UA,
            "Accept":       "text/html,application/xhtml+xml,*/*",
            "Origin":       OKTA_BASE,
            "Referer":      f"{OKTA_BASE}/",
        },
    )
    try:
        with opener.open(assert_req, timeout=20) as r:
            r.read()  # consume; cookies set by CookieProcessor
    except urllib.error.HTTPError as e:
        # A 302 is normal — urllib follows it, but 4xx/5xx means SAML rejected
        if e.code not in (302,):
            body = e.read().decode()[:300]
            raise RuntimeError(f"sso/assert → {e.code}: {body}")

    # ── Step 6: GET auth/validate → gatewayCredentials JWT ───────────────────
    print("  [6] GET auth/validate ...")
    validate_req = urllib.request.Request(
        f"{AUTH_BASE}/api/v1/auth/validate",
        headers={
            "User-Agent": _UA,
            "Accept":     "application/json",
            "Origin":     "https://shop.sysco.com",
            "Referer":    "https://shop.sysco.com/",
        },
    )
    with opener.open(validate_req, timeout=20) as r:
        validate_resp = json.loads(r.read())

    role = validate_resp.get("role", "")
    if role != "CUSTOMER":
        raise RuntimeError(
            f"Expected CUSTOMER role after auth, got role={role!r}. "
            f"Keys: {list(validate_resp.keys())}"
        )

    creds = validate_resp.get("gatewayCredentials", "")
    if not creds:
        raise RuntimeError(f"No gatewayCredentials in validate response: {validate_resp}")

    shop_account_id = validate_resp.get("shopAccountId", SHOP_ACCOUNT_ID)

    # Extract csrf_token from JWT payload (used in syy-requested-by header)
    try:
        payload_b64 = creds.split(".")[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)
        jwt_payload = json.loads(base64.b64decode(payload_b64))
        csrf_token = jwt_payload.get("csrf_token", "")
        vid = jwt_payload.get("vid", "")
    except Exception:
        csrf_token = ""
        vid = ""

    print(f"  [6] ✅ Authenticated as {validate_resp.get('nameId')}  (role={role})")
    return f"Bearer {creds}", shop_account_id, csrf_token, vid


# ── GraphQL helper ────────────────────────────────────────────────────────────

# syy-authorization is a base64-encoded JSON carrying the shop account context.
# The _hash is deterministic for this account (constant across sessions).
# csrf_token and vid are extracted from the Bearer JWT payload at auth time.

def _build_syy_auth(shop_account_id, seller_id, site_id, seller_account_id):
    """Build the syy-authorization header value (base64-encoded JSON)."""
    # _hash is a constant derived from the account config by the Sysco frontend JS.
    # It stays stable across sessions for the same account.
    SYY_HASH = "bc038006687544baa90fb5021c9432ee"
    payload = {
        "data": {
            "shopAccountId": shop_account_id,
            "sellers": {
                seller_id: {
                    "siteId":           site_id,
                    "sellerAccountId":  seller_account_id,
                }
            },
            "shopUserType": "multi_buyer",
            "country":      "US",
        },
        "_hash": SYY_HASH,
    }
    return base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")


def gql(bearer, operation_name, query, variables, ctx=None):
    """
    POST a single GraphQL operation to the Sysco gateway API.
    ctx: dict with csrf_token, vid, shop_account_id, site_id (from auth).
    """
    body = json.dumps({
        "operationName": operation_name,
        "variables":     variables,
        "query":         query,
    }).encode()

    ctx = ctx or {}
    csrf_token     = ctx.get("csrf_token", "")
    vid            = ctx.get("vid", "")
    shop_acct_id   = ctx.get("shop_account_id", SHOP_ACCOUNT_ID)
    site           = ctx.get("site_id", SITE_ID)
    seller_acct_id = site.lstrip("0") if site else "700932"   # "019" → "700932"
    # seller_account_id is the numeric customer number
    seller_acct_id = "700932"   # hardcoded for this account

    syy_auth = _build_syy_auth(shop_acct_id, SELLER_ID, site, seller_acct_id)
    corr_id  = hex(int(time.time() * 1000))[2:]   # cheap unique ID per request

    req = urllib.request.Request(
        GQL_URL,
        data=body,
        headers={
            "Authorization":                bearer,
            "Content-Type":                 "application/json",
            "Accept":                       "application/json",
            "apollographql-client-name":    "SYSCO_SHOP_WEB",
            "apollographql-client-version": "1",
            "Origin":                       "https://shop.sysco.com",
            "Referer":                      "https://shop.sysco.com/",
            "User-Agent":                   _UA,
            # Required syy-* context headers (account + CSRF)
            "syy-authorization":            syy_auth,
            "syy-experience":               "exp-usbl",
            "syy-pricing-version":          "2",
            "syy-request-tier":             "priority",
            "syy-request-type":             "read",
            "syy-site":                     site,
            "syy-source":                   "web",
            "syy-visitor-id":               vid,
            "syy-requested-by":             csrf_token,
            "syy-correlation-id":           corr_id,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print(f"❌ Sysco GraphQL auth error (HTTP {e.code}). Bearer token expired.")
            sys.exit(2)
        body_txt = e.read().decode()[:300]
        raise RuntimeError(f"GraphQL {operation_name} → {e.code}: {body_txt}")


# ── GraphQL queries (verbatim from browser capture) ──────────────────────────

_GQL_LIST_ITEMS = """
query GetListItemsV2($sellerId: String, $siteId: String, $listType: ListType!,
    $listId: String!, $itemStatus: ItemStatus, $filters: ListItemFiltersInputV2,
    $pageNumber: Int, $pageSize: Int, $sortBy: String, $sortOrder: String,
    $groupBy: String, $searchTerm: String) {
  getListItemsV2(
    sellerId: $sellerId
    siteId: $siteId
    listType: $listType
    listId: $listId
    filters: $filters
    pageNumber: $pageNumber
    pageSize: $pageSize
    sortBy: $sortBy
    sortOrder: $sortOrder
    groupBy: $groupBy
    searchTerm: $searchTerm
    itemStatus: $itemStatus
  ) {
    items {
      lineNumber
      product {
        siteId
        sellerId
        productId
        productInfo {
          name
          description
          brand { name }
          packSize { pack size uom }
        }
      }
    }
    meta {
      totalProductCount
      totalPages
      pageNumber
    }
  }
}
"""

_GQL_PRICES = """
query Prices($products: ProductQuery!, $priceOptions: PriceOptions) {
  getProducts(products: $products, priceOptions: $priceOptions) {
    productId
    sellerId
    priceInfoV2 {
      case(products: $products, newAttributeGroupDiscounts: true) {
        netPrice
        price
        grossPrice
        unitPrice
      }
    }
  }
}
"""


# ── Fetch Order Guide ─────────────────────────────────────────────────────────

def fetch_order_guide(bearer, ctx=None):
    """
    Returns list of dicts: {productId, sellerId, siteId, name}
    for all ACTIVE items in the Order Guide list.
    """
    all_items = []
    page = 1
    while True:
        resp = gql(bearer, "GetListItemsV2", _GQL_LIST_ITEMS, {
            "sellerId":    SELLER_ID,
            "siteId":      SITE_ID,
            "listType":    ORDER_GUIDE_LIST_TYPE,
            "listId":      ORDER_GUIDE_LIST_ID,
            "pageNumber":  page,
            "pageSize":    PAGE_SIZE,
            "itemStatus":  "ACTIVE",
        }, ctx=ctx)

        errors = resp.get("errors")
        if errors:
            raise RuntimeError(f"GetListItemsV2 errors: {errors}")

        data  = (resp.get("data") or {}).get("getListItemsV2", {})
        items = data.get("items", [])
        meta  = data.get("meta", {})

        for item in items:
            prod = item.get("product") or {}
            pi   = prod.get("productInfo") or {}
            name = pi.get("name") or pi.get("description") or ""
            brand = (pi.get("brand") or {}).get("name", "")
            full_name = f"{brand} {name}".strip() if brand else name
            all_items.append({
                "productId": prod.get("productId", ""),
                "sellerId":  prod.get("sellerId",  SELLER_ID),
                "siteId":    prod.get("siteId",    SITE_ID),
                "name":      full_name,
            })

        total_pages = meta.get("totalPages", 1)
        total_count = meta.get("totalProductCount", "?")
        print(f"  Page {page}/{total_pages}: {len(items)} items  "
              f"(running total: {len(all_items)} / {total_count})")

        if page >= total_pages:
            break
        page += 1

    return all_items


# ── Fetch Prices ──────────────────────────────────────────────────────────────

def fetch_prices(bearer, products, ctx=None):
    """
    products: list of {productId, sellerId, siteId}
    Returns: dict productId → case netPrice (float)
    """
    price_map = {}
    BATCH = 58   # server handles up to 60; 58 for headroom

    for i in range(0, len(products), BATCH):
        batch  = products[i:i + BATCH]
        params = [
            {
                "productId": p["productId"],
                "sellerId":  p["sellerId"],
                "siteId":    p["siteId"],
                "quantity":  {"case": 0, "each": 0},
                "splitCode": "CASE",
            }
            for p in batch
        ]
        resp = gql(bearer, "Prices", _GQL_PRICES, {
            "products": {"params": params},
        }, ctx=ctx)

        errors = resp.get("errors")
        if errors:
            print(f"  ⚠️  Prices errors: {errors[:2]}")

        prods = (resp.get("data") or {}).get("getProducts") or []
        for p in prods:
            pid      = p.get("productId", "")
            piv2     = p.get("priceInfoV2") or {}
            case_inf = piv2.get("case") or {}
            net      = case_inf.get("netPrice")
            if net is not None:
                price_map[pid] = float(net)

        fetched = min(i + BATCH, len(products))
        print(f"  {fetched}/{len(products)} queried  ({len(price_map)} have prices)")

    return price_map


# ── Supabase helpers ──────────────────────────────────────────────────────────

def sb_get(path):
    hdrs = {**SB_HDRS, "Prefer": ""}
    req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", headers=hdrs)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def sb_post(path, payload):
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/{path}", method="POST",
        data=json.dumps(payload).encode(), headers=SB_HDRS,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "duplicate" in body.lower() or "23505" in body:
            return None
        print(f"  SB POST {path}: {body[:200]}")
        return None


def sb_upsert(path, payload, on_conflict):
    hdrs = {**SB_HDRS, "Prefer": "resolution=merge-duplicates,return=representation"}
    req  = urllib.request.Request(
        f"{SB_URL}/rest/v1/{path}?on_conflict={on_conflict}",
        method="POST", data=json.dumps(payload).encode(), headers=hdrs,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  UPSERT ERROR: {e.read().decode()[:200]}")
        return None


# ── Item master matching ──────────────────────────────────────────────────────

def load_item_map():
    rows  = sb_get("items?select=id,name&order=id.asc")
    by_name = {r["name"].lower().strip(): r["id"] for r in rows}
    # existing Sysco APNs (productIds) already matched in pricing table
    rows2 = sb_get(
        f"pricing?select=item_id,apn&vendor_id=eq.{VENDOR_ID}&apn=not.is.null"
    )
    by_apn = {str(r["apn"]).upper(): r["item_id"] for r in rows2 if r.get("apn")}
    return {"by_name": by_name, "by_apn": by_apn}


_BRAND_PREFIXES = re.compile(
    r"^("
    # Sysco branded items
    r"sysco\s+(classic|imperial|supreme|natural|reliance|essentials?|premium)\s+"
    r"|sysco\s+(imperial/mccormick|imperial/bacardi|imperial/tabasco|freshpoint)\s+"
    r"|sysco\s+imperial\s+"
    r"|sysco/freshpoint\s+(natural|classic|imperial)\s+"
    # Other common Sysco order-guide brand prefixes
    r"|imperial\s+fresh\s+"
    r"|house\s+recipe\s+(classic|imperial|premium)\s+"
    r"|block\s+&\s+barrel\s+(classic|imperial)\s+"
    r"|arrezzio\s+(classic|imperial)\s+"
    r"|casa\s+solana\s+(classic|imperial)\s+"
    r"|reliance\s+fresh\s+"
    r"|tyson\s+red\s+label\s+"
    r"|tyson\s+(imperial|classic|premium|select|natural)\s+"
    r"|fire\s+river\s+farms\s+reliance\s+"
    r"|packer\s+"
    r")",
    re.IGNORECASE,
)

# Synonym map — both directions for common abbreviations / alternate names
_SYNONYMS = {
    "mayo":        "mayonnaise",
    "mayonnaise":  "mayo",
    "parm":        "parmesan",
    "parmesan":    "parm",
    "mara":        "maraschino",
    "maraschino":  "mara",
    "beef":        "burger",
    "burger":      "beef",
    "hamburger":   "burger",
    "chdr":        "cheddar",
    "cheddar":     "chdr",
    "mozz":        "mozzarella",
    "mozzarella":  "mozz",
    "foam":        "styrofoam",
    "styrofoam":   "foam",
    "amer":        "american",
    "american":    "amer",
    "bnls":        "boneless",
    "boneless":    "bnls",
    "film":        "wrap",
    "wrap":        "paper",    # one-way: wrap products match "paper" items, not vice versa
    "tshrt":       "shirt",
    "shirt":       "tshrt",
    "layflat":     "slice",
    "sliced":      "layflat",
}


def _strip_vendor_prefix(name):
    """Remove brand prefixes that add noise to matching."""
    prev = None
    result = name
    # Apply repeatedly until stable (handles double-prefixes like "SYSCO CLASSIC ARREZZIO")
    while result != prev:
        prev = result
        result = _BRAND_PREFIXES.sub("", result).strip()
    return result


def _stem(word):
    """Minimal plural normalization: fries→fry, tomatoes→tomato, wings→wing."""
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"   # fries→fry, cherries→cherry
    if len(w) > 4 and w.endswith("oes"):
        return w[:-2]          # tomatoes→tomato, potatoes→potato
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]          # wings→wing, kegs→keg, packets→packet
    return w


def _preprocess(text):
    """Combine number+unit tokens so sizes aren't stripped: '2 oz' → '2oz', '120 ct' → '120ct'."""
    text = re.sub(r"(\d+)\s*(oz|ct|lb)\b", r"\1\2", text.lower(), flags=re.IGNORECASE)
    return text


def _tokenize(text):
    """Split text into stemmed, synonym-expanded tokens, stripping noise."""
    stop = {"", "the", "a", "an", "and", "of", "in", "to", "go", "ss",
            "w", "oz", "s", "bev", "c", "pf",
            "fresh", "pla", "rl", "bk", "wt", "pp", "mw", "hw",
            "fc", "gf", "iqf", "pld", "brd", "cvp", "jb", "jt",
            "grde", "grade", "usda", "sel", "select", "choice", "premium",
            "classic", "imperial", "natural", "reliance", "supreme"}
    tokens = set()
    for raw in re.split(r"\W+", _preprocess(text)):
        if re.fullmatch(r"\d+", raw):  # pure digits (no units attached)
            continue
        t = _stem(raw)
        if t in stop or len(t) <= 1:
            continue
        tokens.add(t)
        # expand synonym
        if t in _SYNONYMS:
            tokens.add(_stem(_SYNONYMS[t]))
    return tokens


def _word_overlap(a, b):
    wa = _tokenize(a)
    wb = _tokenize(b)
    if not wa or not wb:
        return 0.0
    shorter = wa if len(wa) <= len(wb) else wb
    longer  = wb if len(wa) <= len(wb) else wa
    return len(shorter & longer) / len(shorter)


def match_item(name, apn, item_map):
    """Match a Sysco product to a Supabase item_id by APN then by name."""
    # 1. Exact APN match (previous run stored productId in apn column)
    if apn and apn.upper() in item_map["by_apn"]:
        return item_map["by_apn"][apn.upper()]
    # 2. Exact name match (raw, then prefix-stripped)
    n = (name or "").lower().strip()
    if n in item_map["by_name"]:
        return item_map["by_name"][n]
    n_stripped = _strip_vendor_prefix(n)
    if n_stripped in item_map["by_name"]:
        return item_map["by_name"][n_stripped]
    # 3. Substring match (use stripped name to avoid brand false hits)
    for k, v in item_map["by_name"].items():
        if k in n_stripped or n_stripped in k:
            return v
    # 4. Word-overlap with synonym expansion (threshold 0.65)
    best_score, best_id = 0.0, None
    for k, v in item_map["by_name"].items():
        score = _word_overlap(n_stripped, k)
        if score > best_score:
            best_score, best_id = score, v
    return best_id if best_score >= 0.65 else None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("── Sysco Price Scraper ────────────────────────────────")

    password = os.getenv("SYSCO_PASSWORD", "")
    if not password:
        print("❌ SYSCO_PASSWORD env var not set.")
        print("   Set it in CI secrets or locally:  export SYSCO_PASSWORD='...'")
        sys.exit(1)

    # 1. Authenticate
    print("\n→ Authenticating via Okta SAML2 ...")
    bearer, shop_account_id, csrf_token, vid = get_bearer_token(EMAIL, password)
    print(f"  shopAccountId: {shop_account_id}")

    ctx = {
        "csrf_token":      csrf_token,
        "vid":             vid,
        "shop_account_id": shop_account_id,
        "site_id":         SITE_ID,
    }

    # 2. Fetch Order Guide
    print(f"\n→ Fetching Order Guide (listId={ORDER_GUIDE_LIST_ID}) ...")
    products = fetch_order_guide(bearer, ctx)
    if not products:
        print("❌ No products in Order Guide — exiting")
        sys.exit(1)
    print(f"  Total: {len(products)} products")
    for p in products[:5]:
        print(f"    {p['productId']}  {p['name'][:55]}")

    # 3. Fetch prices
    print("\n→ Fetching prices ...")
    price_map = fetch_prices(bearer, products, ctx)
    print(f"  Got prices for {len(price_map)}/{len(products)} products")

    # 4. Load Supabase item master
    print("\n→ Loading item master from Supabase ...")
    item_map = load_item_map()
    print(f"  {len(item_map['by_name'])} items  |  {len(item_map['by_apn'])} existing Sysco APNs")

    # 5. Create / fetch price_list entry
    pl = sb_post("price_lists", {
        "vendor_id": VENDOR_ID,
        "season":    SEASON,
        "notes":     "Auto-scraped via Sysco GraphQL API (Order Guide 8.5.25)",
    })
    pl_id = pl[0]["id"] if pl and isinstance(pl, list) else None
    if not pl_id:
        existing = sb_get(
            f"price_lists?vendor_id=eq.{VENDOR_ID}"
            f"&season=eq.{urllib.parse.quote(SEASON)}"
            "&select=id&order=id.desc&limit=1"
        )
        pl_id = existing[0]["id"] if existing else None
    print(f"\nPrice list ID: {pl_id}")

    # 6. Match and upsert
    print("\n→ Matching products to item master and upserting ...")
    matched, unmatched = 0, []
    for prod in products:
        pid   = prod["productId"]
        name  = prod["name"]
        price = price_map.get(pid)
        if price is None:
            unmatched.append(f"{pid}  {name[:45]}  (no price)")
            continue
        item_id = match_item(name, pid, item_map)
        if item_id:
            sb_upsert("pricing", {
                "item_id":       item_id,
                "vendor_id":     VENDOR_ID,
                "price_list_id": pl_id,
                "apn":           pid,
                "price":         price,
            }, "item_id,vendor_id,price_list_id")
            matched += 1
        else:
            unmatched.append(f"{pid}  {name[:45]}  ${price:.2f}")

    print(f"\n✅ Matched and saved: {matched} Sysco prices  (vendor_id={VENDOR_ID})")
    if unmatched:
        print(f"⚠️  Unmatched ({len(unmatched)}):")
        for u in unmatched[:30]:
            print(f"   {u}")


if __name__ == "__main__":
    main()
