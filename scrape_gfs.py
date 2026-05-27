"""
GFS (Gordon Food Service) price scraper — cookie-based session auth.

GFS uses Okta SAML2 SSO.  There is no programmatic refresh token; auth is
maintained by a server-side session cookie (__Secure-GORDONORDERING2).

Flow:
  1. Load session cookies from GFS_COOKIES (CI) or ~/.FoodOrderAgent/gfs_session.json (local)
  2. GET /us-central1/api/v6/lists/order-guide    → all material numbers (144 items)
  3. POST /us-central1/api/v1/materials/info       → product names/brands (batches of 50)
  4. POST /us-central1/api/v5/prices               → case prices (batches of 50)
  5. Create price_list entry, match to item master, upsert to Supabase

Session refresh (when cookies expire):
  1. Run locally:  python3 intercept_gfs.py  (opens Chrome, logs in)
  2. Update GitHub secret:
       python3 - <<'EOF'
       import json, os
       s = json.load(open(os.path.expanduser('~/.FoodOrderAgent/gfs_session.json')))
       cks = {c['name']: c['value'] for c in s['cookies']}
       print(json.dumps({
           'gor':     cks.get('GOR',     'us-central1'),
           'gclb':    cks.get('GCLB',    ''),
           'xsrf':    cks.get('XSRF-TOKEN', ''),
           'session': cks.get('__Secure-GORDONORDERING2', ''),
       }))
       EOF
       | gh secret set GFS_COOKIES -R <repo>

Supabase:  vendor_id=4, season="Fall 2025"
"""

import json, os, re, sys, urllib.request, urllib.error, urllib.parse

# ── Config ──────────────────────────────────────────────────────────────────

API_BASE   = "https://order.gfs.com/us-central1/api"
SB_URL     = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_KEY     = os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
SEASON     = os.getenv("PRICE_SEASON", "Spring 2026")
VENDOR_ID  = 4   # GFS
BATCH      = 50

SESSION_FILE = os.path.expanduser("~/.FoodOrderAgent/gfs_session.json")

SB_HDRS = {
    "apikey":        SB_KEY,
    "Authorization": f"Bearer {SB_KEY}",
    "Content-Type":  "application/json",
    "Prefer":        "return=representation",
}

# ── Cookie loading ───────────────────────────────────────────────────────────

def load_cookies():
    """
    Return dict of GFS session cookies.
    CI: read from GFS_COOKIES env var (JSON: {gor, gclb, xsrf, session})
    Local: parse from ~/.FoodOrderAgent/gfs_session.json (Playwright storage state)
    """
    raw = os.environ.get("GFS_COOKIES")
    if raw:
        c = json.loads(raw)
        cookies = {
            "GOR":                      c.get("gor", "us-central1"),
            "GCLB":                     c.get("gclb", ""),
            "XSRF-TOKEN":               c.get("xsrf", ""),
            "__Secure-GORDONORDERING2": c.get("session", ""),
        }
        print("  Cookies loaded from GFS_COOKIES env var")
        return cookies

    if not os.path.exists(SESSION_FILE):
        print("❌ No GFS_COOKIES env var and no gfs_session.json found.")
        print("   Run  python3 intercept_gfs.py  to capture a fresh session.")
        sys.exit(1)

    with open(SESSION_FILE) as f:
        sess = json.load(f)

    want = {"GOR", "GCLB", "XSRF-TOKEN", "__Secure-GORDONORDERING2"}
    cookies = {}
    for c in sess.get("cookies", []):
        if c["name"] in want:
            cookies[c["name"]] = c["value"]

    missing = want - set(cookies)
    if missing:
        print(f"⚠️  Missing cookies in session file: {missing}")

    print(f"  Cookies loaded from {SESSION_FILE}  ({len(cookies)} found)")
    return cookies


def cookie_header(cookies):
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)


# ── GFS HTTP helpers ─────────────────────────────────────────────────────────

def _gfs_headers(cookies, extra=None):
    h = {
        "Cookie":       cookie_header(cookies),
        "X-XSRF-TOKEN": cookies.get("XSRF-TOKEN", ""),
        "Accept":       "application/json, text/plain, */*",
        "Origin":       "https://order.gfs.com",
        "Referer":      "https://order.gfs.com/",
        "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36",
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
            print("❌ GFS session expired (HTTP {}).".format(e.code))
            print("   Re-login: python3 intercept_gfs.py  then update GFS_COOKIES secret.")
            sys.exit(2)
        body = e.read().decode()[:200]
        raise RuntimeError(f"GFS GET {path} → {e.code}: {body}")


def gfs_post(path, body, cookies):
    url = f"{API_BASE}/{path}"
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data,
        headers=_gfs_headers(cookies, {"Content-Type": "application/json"}),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print("❌ GFS session expired (HTTP {}).".format(e.code))
            print("   Re-login: python3 intercept_gfs.py  then update GFS_COOKIES secret.")
            sys.exit(2)
        body_txt = e.read().decode()[:300]
        raise RuntimeError(f"GFS POST {path} → {e.code}: {body_txt}")


# ── Supabase helpers ─────────────────────────────────────────────────────────

def sb_get(path):
    req = urllib.request.Request(f"{SB_URL}/rest/v1/{path}", headers=SB_HDRS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def sb_post(path, payload):
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/{path}", method="POST",
        data=json.dumps(payload).encode(), headers=SB_HDRS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
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
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  UPSERT ERROR: {e.read().decode()[:200]}")
        return None


# ── Item-master matching ─────────────────────────────────────────────────────

def load_item_map():
    rows    = sb_get("items?select=id,name")
    by_name = {r["name"].lower().strip(): r["id"] for r in rows}
    by_apn  = {}
    rows2   = sb_get(f"pricing?select=item_id,apn&vendor_id=eq.{VENDOR_ID}&apn=not.is.null")
    for r in rows2:
        if r.get("apn"):
            by_apn[str(r["apn"]).upper()] = r["item_id"]
    return {"by_name": by_name, "by_apn": by_apn}


def _word_overlap(a, b):
    stop = {"", "the", "a", "an", "and", "of", "in", "ss", "w"}
    wa = set(re.split(r"\W+", a.lower())) - stop
    wb = set(re.split(r"\W+", b.lower())) - stop
    if not wa or not wb:
        return 0.0
    shorter = wa if len(wa) <= len(wb) else wb
    return len(shorter & (wa | wb)) / len(shorter)


def match_item(name, apn, item_map):
    """Match a GFS material to a Supabase item_id by APN then by name."""
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


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("── GFS Price Scraper ──────────────────────────────────")

    # 1. Load cookies
    cookies = load_cookies()

    # 2. Fetch order guide
    print("\n→ Fetching order guide...")
    guide = gfs_get("v6/lists/order-guide", cookies)
    categories = guide.get("guideCategories", [])
    all_materials = []
    seen = set()
    for cat in categories:
        for m in cat.get("materialNumbers", []):
            if m not in seen:
                seen.add(m)
                all_materials.append(m)
    print(f"  {len(categories)} categories, {len(all_materials)} unique materials")
    for cat in categories:
        name = cat.get("categoryName", {}).get("en", "?")
        print(f"    {name}: {len(cat.get('materialNumbers', []))} items")

    # 3. Fetch material info (descriptions + brands)
    print("\n→ Fetching material info...")
    info_map = {}  # materialNumber → {name, brand, description}
    for i in range(0, len(all_materials), BATCH):
        batch = all_materials[i : i + BATCH]
        resp  = gfs_post("v1/materials/info", batch, cookies)
        for m in resp.get("materialInfos", []):
            num   = m["materialNumber"]
            desc  = (m.get("description") or {}).get("en") or ""
            brand = (m.get("brand")       or {}).get("en") or ""
            # Combine "Brand Description" so matching picks it up correctly
            name  = f"{brand} {desc}".strip() if brand else desc
            info_map[num] = {"name": name, "brand": brand, "description": desc}
        print(f"  {min(i + BATCH, len(all_materials))}/{len(all_materials)} materials fetched")
    print(f"  Got info for {len(info_map)} materials")

    # 4. Fetch prices
    print("\n→ Fetching prices...")
    price_map = {}  # materialNumber → case price (float)
    for i in range(0, len(all_materials), BATCH):
        batch = all_materials[i : i + BATCH]
        resp  = gfs_post("v5/prices", {"materialNumbers": batch}, cookies)
        for mp in resp.get("materialPrices", []):
            num = mp["materialNumber"]
            # Prefer CS (case) price; fall back to first non-null price
            cs_price = None
            first_price = None
            for up in mp.get("unitPrices", []):
                p = up.get("price")
                if p is not None:
                    if first_price is None:
                        first_price = (float(p), up.get("salesUom", "CS"))
                    if up.get("salesUom") == "CS":
                        cs_price = float(p)
                        break
            if cs_price is not None:
                price_map[num] = {"price": cs_price, "uom": "CS"}
            elif first_price:
                price_map[num] = {"price": first_price[0], "uom": first_price[1]}
        print(f"  {min(i + BATCH, len(all_materials))}/{len(all_materials)} priced")
    print(f"  Got prices for {len(price_map)} materials")

    # Preview sample
    sample_nums = list(price_map)[:8]
    for n in sample_nums:
        info = info_map.get(n, {})
        pr   = price_map[n]
        print(f"    {n}  {info.get('name','?')[:45]:45}  ${pr['price']:7.2f}  {pr['uom']}")

    # 5. Load item master
    print("\n→ Loading item master from Supabase...")
    item_map = load_item_map()
    print(f"  {len(item_map['by_name'])} items  |  {len(item_map['by_apn'])} existing GFS APNs")

    # 6. Create or fetch price_list entry
    pl = sb_post("price_lists", {
        "vendor_id": VENDOR_ID,
        "season":    SEASON,
        "notes":     "Auto-scraped via GFS order.gfs.com API",
    })
    pl_id = pl[0]["id"] if pl and isinstance(pl, list) else None
    if not pl_id:
        existing = sb_get(
            f"price_lists?vendor_id=eq.{VENDOR_ID}&season=eq.{SEASON}"
            "&select=id&order=id.desc&limit=1"
        )
        pl_id = existing[0]["id"] if existing else None
    print(f"\nPrice list ID: {pl_id}")

    # 7. Match and upsert
    print("\n→ Matching materials to item master and upserting...")
    matched, unmatched = 0, []
    for mat_num, pr in price_map.items():
        info    = info_map.get(mat_num, {})
        name    = info.get("name", mat_num)
        item_id = match_item(name, mat_num, item_map)
        if item_id:
            sb_upsert("pricing", {
                "item_id":       item_id,
                "vendor_id":     VENDOR_ID,
                "price_list_id": pl_id,
                "apn":           mat_num,
                "price":         pr["price"],
            }, "item_id,vendor_id,price_list_id")
            matched += 1
        else:
            unmatched.append(f"{mat_num}  {name[:45]}  ${pr['price']:.2f}")

    print(f"\n✅ Matched and saved: {matched} GFS prices  (vendor_id={VENDOR_ID})")
    if unmatched:
        print(f"⚠️  Unmatched ({len(unmatched)}):")
        for u in unmatched[:30]:
            print(f"   {u}")


if __name__ == "__main__":
    main()
