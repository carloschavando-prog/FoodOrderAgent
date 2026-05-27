"""
Probe US Foods panamax API for cart/order endpoints.
Uses same auth as scrape_usfoods.py.

Known working:
  - list-domain-api/v1/listItems
  - product-domain-api/v2/products
  - price-domain-api/v1/pricing

Probing cart/order:
  - cart-domain-api/v1/cart
  - order-domain-api/v1/orders
  - order-domain-api/v1/order/create
"""
import json, os, sys, uuid, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(__file__))
from scrape_usfoods import load_config, refresh_token, usf_request

def try_usf(method, path, bearer, payload, label):
    print(f"\n── {label} ─────────────────────────────────────────────────────")
    try:
        resp = usf_request(method, path, bearer, payload)
        txt = json.dumps(resp, indent=2)[:800]
        print(txt)
        return resp
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        print(f"  HTTP {e.code}: {body}")
        return None
    except Exception as ex:
        print(f"  Error: {ex}")
        return None

def main():
    config = load_config()
    bearer = refresh_token(config)
    acctnum = config["auth_context"]["customerNumber"]  # 31586241
    div     = config["auth_context"]["divisionNumber"]  # 1103

    # ── GET cart ──────────────────────────────────────────────────────────────
    try_usf("GET", "cart-domain-api/v1/cart", bearer, None, "GET cart")

    # ── GET cart/current ──────────────────────────────────────────────────────
    try_usf("GET", "cart-domain-api/v1/cart/current", bearer, None, "GET cart/current")

    # ── GET orders (recent order history) ─────────────────────────────────────
    try_usf("GET", "order-domain-api/v1/orders", bearer, None, "GET orders")

    # ── GET orders with params ────────────────────────────────────────────────
    try_usf("GET", f"order-domain-api/v1/orders?customerNumber={acctnum}&limit=3",
            bearer, None, "GET orders?customer")

    # ── GET delivery dates ────────────────────────────────────────────────────
    try_usf("GET", f"delivery-domain-api/v1/deliveryDates?customerNumber={acctnum}",
            bearer, None, "GET deliveryDates")

    # ── GET delivery dates v2 ─────────────────────────────────────────────────
    try_usf("GET", f"order-domain-api/v1/deliveryDates",
            bearer, None, "GET order/deliveryDates")

    # ── POST create order ────────────────────────────────────────────────────
    # Use obviously minimal/wrong payload to probe the endpoint signature
    try_usf("POST", "order-domain-api/v1/orders", bearer,
            {"test": True},
            "POST orders (probe endpoint)")

    # ── GET order-management-api ──────────────────────────────────────────────
    try_usf("GET", "order-management-api/v1/orders", bearer, None,
            "GET order-management")

if __name__ == "__main__":
    main()
