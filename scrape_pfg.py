"""
PFG CustomerFirst price scraper — pure API mode, no browser needed.

Uses the Performance Food Group CustomerFirst portal REST API directly:
  1. Refresh MSAL/B2C Bearer token (self-refreshing chain)
  2. POST CreateOrderEntryHeader  → temp order ID
  3. POST ProductListOrderEntrySearch  → product list (ProductKey, description, UOM)
  4. POST GetOrderEntryCustomerProductPrice  → case/split prices per ProductKey
  5. DELETE the temp order (clean up)
  6. Match products to Supabase item master, write prices

Config sources (in priority order):
  - GitHub Actions: PFG_REFRESH_TOKEN + PFG_CONFIG env vars (repo secrets)
    After each run, PFG_REFRESH_TOKEN is updated via `gh secret set` so the
    token chain never breaks.
  - Local: ~/.FoodOrderAgent/pfg_api_config.json  (created by intercept_pfg7.py)

Supabase credentials:
  - SUPABASE_URL / SUPABASE_KEY env vars (or defaults below)
"""
import json, os, sys, re, subprocess, urllib.request, urllib.error, urllib.parse, datetime

# ── Config ─────────────────────────────────────────────────
SB_URL     = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY     = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SEASON     = os.getenv("PRICE_SEASON", "Spring 2026")
VENDOR_ID  = 2   # PFG

CONFIG_FILE = os.path.expanduser("~/.FoodOrderAgent/pfg_api_config.json")

# Azure B2C MSAL token endpoint
B2C_TOKEN_URL = (
    "https://pfgcustomerfirst.b2clogin.com"
    "/pfgcustomerfirst.onmicrosoft.com"
    "/b2c_1a_signup_signin"
    "/oauth2/v2.0/token"
)
B2C_SCOPE = (
    "https://pfgcustomerfirst.onmicrosoft.com/api/customer-first-site-api "
    "openid profile offline_access"
)

SB_HDRS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

# ── Supabase helpers ───────────────────────────────────────

def sb_get(path):
    req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", headers=SB_HDRS)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def sb_post(path, payload):
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/{path}", method="POST",
        data=json.dumps(payload).encode(), headers=SB_HDRS)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "duplicate" in body.lower() or "23505" in body:
            return None
        print(f"  SB ERROR {path}: {body[:200]}")
        return None

def sb_upsert(path, payload, on_conflict):
    hdrs = {**SB_HDRS, "Prefer": "resolution=merge-duplicates,return=representation"}
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/{path}?on_conflict={on_conflict}",
        method="POST", data=json.dumps(payload).encode(), headers=hdrs)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  UPSERT ERROR: {e.read().decode()[:200]}")
        return None

def load_item_map():
    rows  = sb_get("items?select=id,name")
    by_name = {r["name"].lower().strip(): r["id"] for r in rows}
    by_apn  = {}
    rows2 = sb_get(
        f"pricing?select=item_id,apn&vendor_id=eq.{VENDOR_ID}&apn=not.is.null")
    for r in rows2:
        if r.get("apn"):
            by_apn[str(r["apn"]).upper()] = r["item_id"]
    return {"by_name": by_name, "by_apn": by_apn}

def _word_overlap(a, b):
    stop = {'', 'the', 'a', 'an', 'and', 'of', 'in', 'ss', 'w'}
    wa = set(re.split(r'\W+', a.lower())) - stop
    wb = set(re.split(r'\W+', b.lower())) - stop
    if not wa or not wb:
        return 0.0
    shorter = wa if len(wa) <= len(wb) else wb
    return len(shorter & (wa | wb)) / len(shorter)

def match_item(name, apn, item_map):
    """Match a PFG product to an item_id via APN (product number) or name."""
    if apn and apn.upper() in item_map["by_apn"]:
        return item_map["by_apn"][apn.upper()]
    n = (name or "").lower().strip()
    if n in item_map["by_name"]:
        return item_map["by_name"][n]
    for k, v in item_map["by_name"].items():
        if k in n or n in k:
            return v
    best_score, best_id = 0.0, None
    for k, v in item_map["by_name"].items():
        score = _word_overlap(n, k)
        if score > best_score:
            best_score, best_id = score, v
    return best_id if best_score >= 0.7 else None

# ── Config load / save ─────────────────────────────────────

def load_config():
    """Load API config from GitHub Actions env vars or local file."""
    if os.getenv("GITHUB_ACTIONS") == "true":
        config = json.loads(os.environ["PFG_CONFIG"])
        config["refresh_token"] = os.environ["PFG_REFRESH_TOKEN"]
        print("  Config loaded from GitHub Actions secrets")
        return config
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ No config at {CONFIG_FILE}")
        print("   Run  python3 intercept_pfg7.py  first to capture tokens.")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_config(config):
    """Persist updated refresh token — GitHub secret in CI, local file otherwise."""
    if os.getenv("GITHUB_ACTIONS") == "true":
        repo   = os.environ.get("GITHUB_REPOSITORY", "")
        result = subprocess.run(
            ["gh", "secret", "set", "PFG_REFRESH_TOKEN",
             "-b", config["refresh_token"], "-R", repo],
            capture_output=True, text=True,
            env={**os.environ, "GH_TOKEN": os.environ.get("GH_PAT", "")},
        )
        if result.returncode == 0:
            print("  ✅ PFG_REFRESH_TOKEN secret rotated")
        else:
            print(f"  ⚠️  Secret rotation failed: {result.stderr[:200]}")
    else:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

# ── PFG API helpers ────────────────────────────────────────

def pfg_request(method, endpoint, bearer, payload=None, params=None):
    """Make a call to the PFG CustomerFirst REST API."""
    api_base = "https://apps-zz-cusfst-mw-p-eus01.azurewebsites.net/api"
    url = f"{api_base}/{endpoint}"
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
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ❌ PFG API {method} {endpoint} ({e.code}): {body[:300]}")
        raise

def refresh_token(config):
    """Exchange MSAL B2C refresh token for new access + refresh token."""
    print("→ Refreshing PFG Bearer token (MSAL B2C)...")
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
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ❌ Token refresh failed ({e.code}): {body[:300]}")
        raise

    access = resp.get("access_token") or resp.get("id_token")
    bearer = f"Bearer {access}"
    config["access_token"]  = access
    config["refresh_token"] = resp["refresh_token"]   # chain refreshes
    save_config(config)
    expires = resp.get("expires_in") or resp.get("id_token_expires_in", "?")
    print(f"  ✅ Bearer token refreshed (expires in {expires}s)")
    return bearer

def delete_order(bearer, order_id, customer_id):
    """Best-effort deletion of a temporary order entry header."""
    print(f"→ Deleting temp order {order_id[:8]}...")
    # Try POST with body first (most common pattern in this API)
    for ep, meth, body in [
        ("OrderEntryHeader/V1/DeleteOrderEntryHeader", "POST",
         {"OrderEntryHeaderId": order_id, "CustomerId": customer_id}),
        ("OrderEntryHeader/V1/DeleteOrderEntryHeader", "DELETE",
         {"OrderEntryHeaderId": order_id, "CustomerId": customer_id}),
        (f"OrderEntryHeader/V1/DeleteOrderEntryHeader/{order_id}", "DELETE", None),
    ]:
        try:
            pfg_request(meth, ep, bearer, payload=body)
            print(f"  ✅ Order deleted via {meth} {ep.split('/')[-1]}")
            return True
        except urllib.error.HTTPError as e:
            if e.code in (404, 405):
                continue   # try next variant
            print(f"  ⚠️  Delete order returned {e.code} — continuing anyway")
            return False
    print("  ⚠️  Could not delete order (all variants failed) — it will expire naturally")
    return False

def create_order(bearer, customer_id, biz_unit_key=3):
    """Create a temporary order entry header for the price-lookup context.
    Returns (order_id, delivery_date).
    """
    print("→ Creating temporary order entry header...")
    resp = pfg_request("POST", "OrderEntryHeader/V1/CreateOrderEntryHeader", bearer,
                       payload={"CustomerId": customer_id, "BusinessUnitKey": biz_unit_key})
    ro = resp.get("ResultObject", {})
    order_id      = ro.get("OrderEntryHeaderId")
    delivery_date = ro.get("DeliveryDate", "")
    print(f"  ✅ Temp order created: {order_id}  delivery: {delivery_date}")
    return order_id, delivery_date

def get_products(bearer, customer_id, list_id):
    """
    Fetch all products from the Fall 2025 list via SearchProductList.
    Returns list of dicts: {ProductKey, ProductNumber, ProductDescription, UOMs}

    Note: Uses SearchProductList (not ProductListOrderEntrySearch) because
    the latter requires a specific browser-side session state that isn't
    reproducible via direct API calls.
    """
    print("→ Fetching product list (SearchProductList)...")
    products = []
    skip = 0
    while True:
        resp = pfg_request("POST", "ProductListSearch/V1/SearchProductList", bearer,
                           payload={
                               "CustomerId":          customer_id,
                               "ProductListHeaderId": list_id,
                               "Query":               "",
                               "Skip":                skip,
                               "Take":                500,
                               "SortValue":           5,
                               "FacetFilter":         [],
                           })
        ro   = resp.get("ResultObject", {})
        cats = ro.get("ProductListCategories", [])
        page_prods = []
        for cat in cats:
            for pw in cat.get("Products", []):
                p = pw.get("Product", pw)   # SearchProductList nests under "Product" key
                uoms = p.get("UnitOfMeasureOrderQuantities", [])
                page_prods.append({
                    "ProductKey":         p.get("ProductKey", ""),
                    "ProductNumber":      p.get("ProductNumber", ""),
                    "ProductDescription": p.get("ProductDescription", ""),
                    "ProductBrand":       p.get("ProductBrand", ""),
                    "UOMs":               [
                        {
                            "UnitOfMeasure":        u.get("UnitOfMeasure"),
                            "UnitOfMeasureAbbr":    u.get("UnitOfMeasureAbbreviation", "CS"),
                            "UnitOfMeasureName":    u.get("UnitOfMeasureName", "case"),
                            "PackSize":             u.get("PackSize", ""),
                            "ProductNumberDisplay": u.get("ProductNumberDisplay", ""),
                        }
                        for u in uoms
                    ],
                })
        products.extend(page_prods)
        has_more = ro.get("HasLoadMore", False)
        total    = ro.get("TotalCount", 0)
        new_skip = ro.get("Skip", skip + len(page_prods))
        print(f"  Page skip={skip}: {len(page_prods)} products "
              f"(total so far: {len(products)}/{total})")
        if not has_more or not page_prods or new_skip <= skip:
            break
        skip = new_skip
    print(f"  ✅ Got {len(products)} products")
    return products

def get_prices(bearer, customer_id, order_id, opco_number, biz_unit, delivery_date, products):
    """
    Fetch prices for all ProductKey+UOM combos.
    Uses the exact request body format the browser sends.
    Returns dict: {(ProductKey.upper(), uom_type): price}
    """
    print("→ Fetching prices (GetOrderEntryCustomerProductPrice)...")
    combos = []
    for p in products:
        for uom in p["UOMs"]:
            combos.append({
                "ProductKey":        p["ProductKey"],
                "UnitOfMeasureType": uom["UnitOfMeasure"],
                "OrderEntryDetailId": None,
                "LastViewedPrice":    None,
            })
    print(f"  Sending {len(combos)} ProductKey+UOM combos...")

    resp = pfg_request(
        "POST", "CustomerProductPrice/V1/GetOrderEntryCustomerProductPrice", bearer,
        payload={
            "BusinessUnitKey":              biz_unit,
            "OperationCompanyNumber":       opco_number,
            "CustomerId":                   customer_id,
            "DeliveryDate":                 delivery_date,
            "OrderEntryHeaderId":           order_id,
            "CustomerProductPriceRequests": combos,  # ← correct field name
            "IgnoreRetry":                  False,
        })
    ro = resp.get("ResultObject", resp)
    price_list = ro.get("CustomerProductPrices", [])

    price_map = {}
    for entry in price_list:
        key   = (entry["ProductKey"].upper(), entry["UnitOfMeasureType"])
        price = entry.get("Price")
        if price and price > 0:
            price_map[key] = price

    non_zero = len(price_map)
    print(f"  ✅ Got {len(price_list)} price entries ({non_zero} non-zero)")
    return price_map

# ── Main ───────────────────────────────────────────────────

def main():
    config      = load_config()
    customer_id = config.get("customer_id", "ccbddeae-bc43-4287-a4e0-8d5bee2b913c")
    list_id     = config.get("fall_list_id", "13e8ce85-8f4e-4cfe-a6dd-cac49a88dc60")
    biz_unit    = int(config.get("biz_unit_key", 3))

    # Supabase item master
    item_map = load_item_map()
    print(f"Loaded {len(item_map['by_name'])} items "
          f"({len(item_map['by_apn'])} PFG APNs already on record)")

    # Refresh token
    bearer = refresh_token(config)

    # Clean up the accidentally created order from exploration (one-time)
    stale_order = "b9c31091-9956-421c-af96-1baa913cbe04"
    if config.get("delete_stale_order", True):
        delete_order(bearer, stale_order, customer_id)
        config["delete_stale_order"] = False
        save_config(config)

    opco_number = config.get("opco_number", "795")

    # Create fresh temp order
    order_id, delivery_date = create_order(bearer, customer_id, biz_unit)

    try:
        # Get product list (uses SearchProductList, always works)
        products = get_products(bearer, customer_id, list_id)

        # Get prices (uses exact browser request format)
        price_map = get_prices(
            bearer, customer_id, order_id, opco_number, biz_unit,
            delivery_date, products)

    finally:
        # Always clean up the temp order
        delete_order(bearer, order_id, customer_id)

    # ── Build flat product+price list ─────────────────────
    rows = []
    for p in products:
        key = p["ProductKey"].upper()
        for uom in p["UOMs"]:
            uom_type = uom["UnitOfMeasure"]
            price    = price_map.get((key, uom_type))
            if price:
                rows.append({
                    "product_key":   key,
                    "product_number": p["ProductNumber"],
                    "name":          p["ProductDescription"],
                    "brand":         p["ProductBrand"],
                    "price":         price,
                    "uom_type":      uom_type,
                    "uom_abbr":      uom["UnitOfMeasureAbbr"],
                    "pack_size":     uom["PackSize"],
                })

    print(f"\nProducts with prices: {len(rows)} / {len(products)} products")
    for r in rows[:8]:
        print(f"  {r['product_number']:8}  {r['name'][:45]:45}  "
              f"${r['price']:7.2f}  {r['uom_abbr']}  {r['pack_size']}")

    # ── Write to Supabase ──────────────────────────────────
    pl = sb_post("price_lists", {
        "vendor_id": VENDOR_ID,
        "season":    SEASON,
        "notes":     "Auto-scraped via PFG CustomerFirst API",
    })
    pl_id = pl[0]["id"] if pl and isinstance(pl, list) else None
    if not pl_id:
        # Already exists — fetch it
        existing = sb_get(
            f"price_lists?vendor_id=eq.{VENDOR_ID}&season=eq.{SEASON}&select=id&order=id.desc&limit=1")
        pl_id = existing[0]["id"] if existing else None
    print(f"Price list ID: {pl_id}")

    matched, unmatched = 0, []
    for r in rows:
        item_id = match_item(r["name"], r["product_number"], item_map)
        if item_id:
            sb_upsert("pricing", {
                "item_id":       item_id,
                "vendor_id":     VENDOR_ID,
                "price_list_id": pl_id,
                "apn":           r["product_number"],
                "price":         r["price"],
            }, "item_id,vendor_id,price_list_id")
            matched += 1
        else:
            unmatched.append(f"{r['name'][:45]}  #{r['product_number']}")

    print(f"\n✅ Matched and saved: {matched} prices")
    if unmatched:
        print(f"⚠️  Unmatched ({len(unmatched)}):")
        for u in unmatched[:25]:
            print(f"   {u}")

if __name__ == "__main__":
    main()
