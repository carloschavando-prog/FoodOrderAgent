"""
US Foods price scraper — pure API mode, no browser needed.

Calls the panamax-api.ama.usfoods.com REST API directly:
  1. Refresh Bearer token (self-refreshing chain)
  2. GET listItems → Fall 2025 product numbers
  3. POST products → product names/descriptions
  4. POST pricing → case prices
  5. Write matched prices to Supabase

Config sources (in priority order):
  - GitHub Actions: USF_REFRESH_TOKEN + USF_CONFIG env vars (set as repo secrets)
    After each run, USF_REFRESH_TOKEN is updated via `gh secret set` so the
    token chain never breaks.
  - Local: ~/.FoodOrderAgent/usf_api_config.json (created by intercept_api.py)

Supabase credentials:
  - SUPABASE_URL / SUPABASE_KEY env vars (or defaults below)
"""
import json, os, sys, uuid, time, subprocess, urllib.request, urllib.error, datetime

# ── Config ────────────────────────────────────────────────
SB_URL    = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY    = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SEASON    = os.getenv("PRICE_SEASON", "Spring 2026")
VENDOR_ID = 1  # US FOODS

API_BASE   = "https://panamax-api.ama.usfoods.com"
CONFIG_FILE = os.path.expanduser("~/.FoodOrderAgent/usf_api_config.json")

SB_HDRS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

# ── Supabase helpers ──────────────────────────────────────

def sb_get(path):
    req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", headers=SB_HDRS)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def sb_post(path, payload):
    req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", method="POST",
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
    rows = sb_get("items?select=id,name")
    by_name = {r["name"].lower().strip(): r["id"] for r in rows}
    by_apn  = {}
    rows2 = sb_get(f"pricing?select=item_id,apn&vendor_id=eq.{VENDOR_ID}&apn=not.is.null")
    for r in rows2:
        if r.get("apn"):
            by_apn[str(r["apn"])] = r["item_id"]
    return {"by_name": by_name, "by_apn": by_apn}

import re
def _word_overlap(a, b):
    wa = set(re.split(r'\W+', a.lower())) - {'', 'the', 'a', 'an', 'and', 'of', 'in', 'ss'}
    wb = set(re.split(r'\W+', b.lower())) - {'', 'the', 'a', 'an', 'and', 'of', 'in', 'ss'}
    if not wa or not wb:
        return 0.0
    shorter = wa if len(wa) <= len(wb) else wb
    return len(shorter & (wa | wb)) / len(shorter)

def match_item(name, apn, item_map):
    if apn and str(apn) in item_map["by_apn"]:
        return item_map["by_apn"][str(apn)]
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

# ── US Foods API helpers ──────────────────────────────────

def usf_request(method, path, bearer, payload=None, params=None):
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
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ❌ API ERROR {method} {path} ({e.code}): {body[:300]}")
        raise

def refresh_token(config):
    """Exchange refresh token for new Bearer + refresh token. Updates config in place."""
    print("→ Refreshing Bearer token...")
    url = f"{API_BASE}/auth-api/v1/oauth/token"
    # Token exchange — NO Authorization header
    hdrs = {
        "Accept":          "application/json, text/plain, */*",
        "Content-Type":    "application/json",
        "consumer-id":     config.get("consumer_id", "ecom"),
        "correlation-id":  f"ecomr4-{uuid.uuid4()}",
        "transaction-id":  str(int(time.time() * 1000)),
        "trace-context":   "login",
        "Origin":          "https://order.usfoods.com",
    }
    payload = {
        "grantType":    "refreshToken",
        "scopes":       config["scopes"],
        "platform":     config["platform"],
        "authContext":  config["auth_context"],
        "refreshToken": config["refresh_token"],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=hdrs, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  ❌ Token refresh failed ({e.code}): {body[:200]}")
        raise

    bearer = f"{resp['tokenType']} {resp['accessToken']}"
    config["refresh_token"] = resp["refreshToken"]   # chain refreshes
    config["bearer"]        = bearer
    save_config(config)
    print(f"  ✅ Bearer token refreshed (expires in {resp.get('expiresIn', '?')}s)")
    return bearer

def get_list_items(bearer, list_id):
    """Fetch all items for the given list ID."""
    print("→ Fetching list items...")
    resp = usf_request("GET", "list-domain-api/v1/listItems",
        bearer=bearer,
        params={"watermark": "1995-08-26T15:28:17.854Z"})
    all_items = resp if isinstance(resp, list) else resp.get("items", resp)
    fall = [i for i in all_items if i.get("listKey", {}).get("listId") == list_id]
    pnums = [i["productNumber"] for i in fall]
    print(f"  Fall 2025 list: {len(fall)} items")
    return pnums

def get_product_names(bearer, product_numbers):
    """Fetch product descriptions for a list of product numbers."""
    print(f"→ Fetching product names ({len(product_numbers)} products)...")
    # Batch in chunks of 100
    names = {}
    for i in range(0, len(product_numbers), 100):
        batch = product_numbers[i:i+100]
        delivery = (datetime.date.today() + datetime.timedelta(days=2)).isoformat() + "T00:00:00.000Z"
        resp = usf_request("POST", "product-domain-api/v2/products",
            bearer=bearer,
            payload={
                "productNumbers":       batch,
                "deliveryDate":         delivery,
                "includeHiddenProduct": "",
                "autoSubProducts":      False,
                "subsAndOrderability":  True,
            })
        items = resp.get("items", resp) if isinstance(resp, dict) else resp
        for item in items:
            pnum = item.get("productNumber")
            summary = item.get("summary", {})
            desc = summary.get("productDescLong") or summary.get("description") or ""
            brand = summary.get("brand", "")
            names[pnum] = {"name": desc, "brand": brand}
    print(f"  Got names for {len(names)} products")
    return names

def get_prices(bearer, product_numbers, list_id):
    """Fetch case prices for a list of product numbers."""
    print(f"→ Fetching prices ({len(product_numbers)} products)...")
    prices = {}
    for i in range(0, len(product_numbers), 100):
        batch = product_numbers[i:i+100]
        resp = usf_request("POST", "price-domain-api/v1/pricing",
            bearer=bearer,
            payload={
                "productNumbers": batch,
                "feature":        f"/desktop/lists/view/SL-{list_id}",
            })
        detail = resp.get("messageDetail", {})
        prod_list = detail.get("productList", []) if isinstance(detail, dict) else []
        for p in prod_list:
            pnum = int(p.get("productNumber", 0))
            unit_price = p.get("unitPrice")
            if unit_price and p.get("errorNumber", "1") == "0":
                prices[pnum] = {
                    "price": float(unit_price),
                    "uom":   p.get("priceUom", "CS"),
                }
    print(f"  Got prices for {len(prices)} products")
    return prices

# ── Config load / save ────────────────────────────────────

def load_config():
    """Load API config from GitHub Actions env vars or local file."""
    if os.getenv("GITHUB_ACTIONS") == "true":
        config = json.loads(os.environ["USF_CONFIG"])
        config["refresh_token"] = os.environ["USF_REFRESH_TOKEN"]
        print("  Config loaded from GitHub Actions secrets")
        return config
    if not os.path.exists(CONFIG_FILE):
        print(f"❌ No config at {CONFIG_FILE}")
        print("   Run  python3 intercept_api.py  first (opens browser once to capture API tokens).")
        sys.exit(1)
    with open(CONFIG_FILE) as f:
        return json.load(f)

def save_config(config):
    """Persist updated refresh token — GitHub secret in CI, local file otherwise."""
    if os.getenv("GITHUB_ACTIONS") == "true":
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        result = subprocess.run(
            ["gh", "secret", "set", "USF_REFRESH_TOKEN",
             "-b", config["refresh_token"], "-R", repo],
            capture_output=True, text=True,
            env={**os.environ, "GH_TOKEN": os.environ.get("GH_PAT", "")}
        )
        if result.returncode == 0:
            print(f"  ✅ USF_REFRESH_TOKEN secret rotated")
        else:
            print(f"  ⚠️  Secret rotation failed: {result.stderr[:200]}")
    else:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)

# ── Main ──────────────────────────────────────────────────

def main():
    config = load_config()

    # Load Supabase item master
    item_map = load_item_map()
    print(f"Loaded {len(item_map['by_name'])} items ({len(item_map['by_apn'])} with APNs)")

    # Refresh Bearer token
    bearer = refresh_token(config)

    # Get Fall 2025 product numbers
    list_id  = config.get("fall_2025_list_id", 1000643297)
    pnums    = get_list_items(bearer, list_id)

    # Get names and prices
    names    = get_product_names(bearer, pnums)
    prices   = get_prices(bearer, pnums, list_id)

    # Build combined product list
    products = []
    for pnum in pnums:
        price_info = prices.get(pnum)
        name_info  = names.get(pnum, {})
        if price_info:
            products.append({
                "product_number": pnum,
                "name":  name_info.get("name", ""),
                "brand": name_info.get("brand", ""),
                "price": price_info["price"],
                "uom":   price_info["uom"],
            })

    print(f"\nProducts with prices: {len(products)} / {len(pnums)}")
    for p in products[:5]:
        print(f"  #{p['product_number']}  {p['name'][:50]}  ${p['price']:.2f} {p['uom']}")

    # Write to Supabase
    pl = sb_post("price_lists", {
        "vendor_id": VENDOR_ID,
        "season":    SEASON,
        "notes":     "Auto-scraped via US Foods panamax API"
    })
    pl_id = pl[0]["id"] if pl and isinstance(pl, list) else None
    print(f"Price list ID: {pl_id}")

    matched, unmatched = 0, []
    for p in products:
        item_id = match_item(p["name"], str(p["product_number"]), item_map)
        if item_id:
            sb_upsert("pricing", {
                "item_id":       item_id,
                "vendor_id":     VENDOR_ID,
                "price_list_id": pl_id,
                "apn":           str(p["product_number"]),
                "price":         p["price"],
            }, "item_id,vendor_id,price_list_id")
            matched += 1
        else:
            unmatched.append(f"{p['name'][:45]}  #{p['product_number']}")

    print(f"\n✅ Matched and saved: {matched} prices")
    if unmatched:
        print(f"⚠️  Unmatched ({len(unmatched)}):")
        for u in unmatched[:25]:
            print(f"   {u}")

if __name__ == "__main__":
    main()
