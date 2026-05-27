"""
One-time setup: populate Supabase vendor_auth table with current credentials.
Run this locally after any token refresh or cookie capture.

Usage:
    python3 setup_vendor_auth.py          # loads all 4 vendors
    python3 setup_vendor_auth.py gfs      # reload just GFS
    python3 setup_vendor_auth.py usf pfg  # reload USF and PFG

Creates (if missing) and populates:
    vendor_auth(vendor_id int PK, credentials jsonb, updated_at timestamptz)

Vendor IDs:
    1 = US Foods   (refresh_token + auth_context)
    2 = PFG        (refresh_token + customer_id + list_id + biz_unit + opco)
    3 = Sysco      (password stored as SYSCO_PASSWORD env var — NOT in DB)
    4 = GFS        (session cookies from gfs_session.json)
"""

import json, os, sys, urllib.request, urllib.error

SB_URL  = os.getenv("SUPABASE_URL", "https://gnkwdoohzspomvdshzge.supabase.co")
SB_SKEY = os.getenv("SUPABASE_SERVICE_KEY")

# ── Ensure vendor_auth table exists via SQL API ───────────────────────────────

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS vendor_auth (
    vendor_id   INTEGER PRIMARY KEY,
    credentials JSONB   NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

def ensure_table():
    if not SB_SKEY:
        print("⚠️  SUPABASE_SERVICE_KEY not set — using publishable key (may fail on vendor_auth)")
        return
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/rpc/exec_sql",
        data=json.dumps({"query": CREATE_TABLE_SQL}).encode(),
        headers={
            "apikey":        SB_SKEY,
            "Authorization": f"Bearer {SB_SKEY}",
            "Content-Type":  "application/json",
        }, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        print("✅ vendor_auth table ready")
    except Exception as ex:
        # Table may already exist or rpc/exec_sql not available
        print(f"  Table creation: {ex} (may already exist — continuing)")


# ── Supabase upsert ───────────────────────────────────────────────────────────

def upsert(vendor_id, credentials):
    key = SB_SKEY or os.getenv("SUPABASE_KEY", "sb_publishable_BZ9rpzEITSHCo2BVGHA1iA_7nsCVnMc")
    hdrs = {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates,return=representation",
    }
    req = urllib.request.Request(
        f"{SB_URL}/rest/v1/vendor_auth?on_conflict=vendor_id",
        data=json.dumps({"vendor_id": vendor_id, "credentials": credentials}).encode(),
        headers=hdrs, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
        return True
    except urllib.error.HTTPError as e:
        print(f"  Upsert error: {e.read().decode()[:200]}")
        return False


# ── Per-vendor loaders ────────────────────────────────────────────────────────

def setup_usf():
    print("\n── US Foods (vendor_id=1) ──────────────────────────────────────────")
    config_file = os.path.expanduser("~/.FoodOrderAgent/usf_api_config.json")
    if not os.path.exists(config_file):
        print(f"  ✗ Not found: {config_file}")
        print("    Run python3 intercept_api.py to capture USF tokens first.")
        return False
    with open(config_file) as f:
        config = json.load(f)
    if not config.get("refresh_token"):
        print("  ✗ No refresh_token in config")
        return False
    ok = upsert(1, config)
    if ok:
        print(f"  ✅ USF credentials saved (refresh_token: {config['refresh_token'][:20]}...)")
    return ok


def setup_pfg():
    print("\n── PFG (vendor_id=2) ───────────────────────────────────────────────")
    config_file = os.path.expanduser("~/.FoodOrderAgent/pfg_api_config.json")
    if not os.path.exists(config_file):
        print(f"  ✗ Not found: {config_file}")
        print("    Run python3 intercept_pfg7.py to capture PFG tokens first.")
        return False
    with open(config_file) as f:
        config = json.load(f)
    if not config.get("refresh_token"):
        print("  ✗ No refresh_token in config")
        return False
    ok = upsert(2, config)
    if ok:
        print(f"  ✅ PFG credentials saved (refresh_token: {config['refresh_token'][:20]}...)")
    return ok


def setup_sysco():
    print("\n── Sysco (vendor_id=3) — password only, stored as env var ─────────")
    # Sysco uses Okta auth each time — no token storage needed
    # Just verify the env var is set
    pwd = os.getenv("SYSCO_PASSWORD", "")
    if pwd:
        print(f"  ✅ SYSCO_PASSWORD is set ({len(pwd)} chars) — no DB storage needed")
    else:
        print("  ⚠️  SYSCO_PASSWORD not set as env var")
        print("     Set it in Vercel: Settings → Environment Variables → SYSCO_PASSWORD")
    return True


def setup_gfs():
    print("\n── GFS (vendor_id=4) ───────────────────────────────────────────────")
    session_file = os.path.expanduser("~/.FoodOrderAgent/gfs_session.json")
    if not os.path.exists(session_file):
        print(f"  ✗ Not found: {session_file}")
        print("    Run python3 intercept_gfs.py to capture a fresh GFS session.")
        return False
    with open(session_file) as f:
        sess = json.load(f)
    want    = {"GOR", "GCLB", "XSRF-TOKEN", "__Secure-GORDONORDERING2"}
    cookies = {c["name"]: c["value"] for c in sess.get("cookies", []) if c["name"] in want}
    missing = want - set(cookies)
    if missing:
        print(f"  ⚠️  Missing cookies: {missing}")
    creds = {
        "gor":     cookies.get("GOR", "us-central1"),
        "gclb":    cookies.get("GCLB", ""),
        "xsrf":    cookies.get("XSRF-TOKEN", ""),
        "session": cookies.get("__Secure-GORDONORDERING2", ""),
    }
    if not creds["session"]:
        print("  ✗ No session cookie found")
        return False
    ok = upsert(4, creds)
    if ok:
        print(f"  ✅ GFS cookies saved (session: {creds['session'][:20]}...)")
    return ok


# ── Main ──────────────────────────────────────────────────────────────────────

VENDOR_MAP = {"usf": setup_usf, "usfoods": setup_usf,
              "pfg": setup_pfg,
              "sysco": setup_sysco,
              "gfs": setup_gfs}

def main():
    args = [a.lower() for a in sys.argv[1:]]

    if not SB_SKEY:
        print("⚠️  SUPABASE_SERVICE_KEY not set.")
        print("   Get it from: Supabase Dashboard → Settings → API → service_role key")
        print("   Then: export SUPABASE_SERVICE_KEY='eyJ...'")
        print("   Continuing with publishable key (may have permission errors on vendor_auth)\n")

    ensure_table()

    if args:
        for arg in args:
            fn = VENDOR_MAP.get(arg)
            if fn:
                fn()
            else:
                print(f"Unknown vendor: {arg}  (valid: usf, pfg, sysco, gfs)")
    else:
        setup_usf()
        setup_pfg()
        setup_sysco()
        setup_gfs()

    print("\n── Next steps ──────────────────────────────────────────────────────")
    print("  1. Add SUPABASE_SERVICE_KEY to Vercel env vars")
    print("     (Settings → Environment Variables)")
    print("  2. The 4 place_order endpoints will read credentials from vendor_auth")
    print("  3. USF and PFG tokens auto-rotate after each use")
    print("  4. GFS cookies expire in ~30 days — re-run intercept_gfs.py + this script")


if __name__ == "__main__":
    main()
