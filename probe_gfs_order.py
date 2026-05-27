"""
Probe GFS order.gfs.com API for cart/order submission endpoints.
Uses same session cookies as scrape_gfs.py.

Known working:
  - v6/lists/order-guide  (GET)
  - v1/materials/info     (POST)
  - v5/prices             (POST)

Probing:
  - v6/cart               (GET/POST)
  - v1/cart               (GET/POST)
  - v1/orders             (GET/POST)
  - v6/orders             (GET/POST)
  - v1/checkout           (POST)
"""
import json, os, sys, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(__file__))
from scrape_gfs import load_cookies, gfs_get, gfs_post

def try_gfs_get(path, cookies, label):
    print(f"\n── GET {path} ({label}) ────────────────────────────────────────────")
    try:
        resp = gfs_get(path, cookies)
        print(json.dumps(resp, indent=2)[:600])
        return resp
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        print(f"  HTTP {e.code}: {body}")
        return None
    except Exception as ex:
        print(f"  Error: {ex}")
        return None

def try_gfs_post(path, body, cookies, label):
    print(f"\n── POST {path} ({label}) ───────────────────────────────────────────")
    try:
        resp = gfs_post(path, body, cookies)
        print(json.dumps(resp, indent=2)[:600])
        return resp
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode()[:300]
        print(f"  HTTP {e.code}: {body_txt}")
        return None
    except Exception as ex:
        print(f"  Error: {ex}")
        return None

def main():
    cookies = load_cookies()

    # Cart endpoints
    try_gfs_get("v6/cart",              cookies, "cart v6")
    try_gfs_get("v1/cart",              cookies, "cart v1")
    try_gfs_get("v1/orders",            cookies, "orders v1")
    try_gfs_get("v6/orders",            cookies, "orders v6")
    try_gfs_get("v1/orders/pending",    cookies, "orders/pending")
    try_gfs_get("v1/orders/history",    cookies, "orders/history")
    try_gfs_get("v6/orders/history",    cookies, "orders/history v6")

    # Try posting minimal cart item (probe only — won't have valid material numbers)
    try_gfs_post("v6/cart/items",      {"materialNumber": "TEST", "quantity": 0},
                 cookies, "cart items v6")
    try_gfs_post("v1/cart/items",      {"materialNumber": "TEST", "quantity": 0},
                 cookies, "cart items v1")
    try_gfs_post("v1/cart",            {"items": []}, cookies, "cart v1")

if __name__ == "__main__":
    main()
