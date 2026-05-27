"""
Probe Sysco GraphQL API for cart/order mutations.
Uses same auth as scrape_sysco.py — run with SYSCO_PASSWORD set.

Tries:
  1. GraphQL introspection → list all mutation names
  2. GetCart query
  3. Check DeliveryDates query
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from scrape_sysco import get_bearer_token, gql, EMAIL

def main():
    password = os.getenv("SYSCO_PASSWORD", "")
    if not password:
        print("Set SYSCO_PASSWORD")
        sys.exit(1)

    print("Authenticating...")
    bearer, shop_account_id, csrf_token, vid = get_bearer_token(EMAIL, password)
    ctx = {"csrf_token": csrf_token, "vid": vid,
           "shop_account_id": shop_account_id, "site_id": "019"}
    print(f"✅ Bearer obtained (shop_account_id={shop_account_id})")

    # ── 1. Probe specific mutations by name ───────────────────────────────────
    # Introspection is disabled; try mutation names directly with minimal vars.
    print("\n── Probing mutation names ───────────────────────────────────────────")
    # Probe using inline args (avoids type name issues), dummy product
    DUMMY_PRODUCT = "0001234567"  # won't exist but gives us error structure
    mutation_candidates = [
        ("addToCart_v1",
         'mutation AddToCart{addToCart(items:[{productId:"'+DUMMY_PRODUCT+'",quantity:1,splitCode:"CASE"}],sellerId:"USBL",siteId:"019"){cartId totalQuantity}}'),
        ("addToCart_v2",
         'mutation AddToCart{addToCart(items:[{productId:"'+DUMMY_PRODUCT+'",quantity:1}],sellerId:"USBL",siteId:"019"){cartId}}'),
        ("addToCart_v3",
         'mutation AddToCart{addToCart(item:{productId:"'+DUMMY_PRODUCT+'",quantity:1,splitCode:"CASE"},sellerId:"USBL",siteId:"019"){cartId}}'),
        ("submitOrder",
         'mutation SubmitOrder{submitOrder(sellerId:"USBL",siteId:"019"){orderId orderNumber status}}'),
        ("placeOrder",
         'mutation PlaceOrder{placeOrder(sellerId:"USBL",siteId:"019"){orderId}}'),
        ("checkoutCart",
         'mutation CheckoutCart{checkoutCart(sellerId:"USBL",siteId:"019"){orderId}}'),
    ]
    for op_name, query in mutation_candidates:
        try:
            resp = gql(bearer, op_name.split("_")[0].title(), query, {}, ctx=ctx)
            errors = resp.get("errors", [])
            if errors:
                msg = errors[0].get("message", "")
                if "Cannot query field" in msg:
                    print(f"  ✗ {op_name} — field doesn't exist")
                else:
                    print(f"  ✅ {op_name} — EXISTS → {msg[:100]}")
            else:
                print(f"  ✅ {op_name} — SUCCESS: {json.dumps(resp.get('data'))[:100]}")
        except Exception as ex:
            s = str(ex)
            if "Cannot query field" in s:
                print(f"  ✗ {op_name} — field doesn't exist")
            else:
                print(f"  ✅ {op_name} — EXISTS (exception): {s[:100]}")

    # ── 2. Try GetCart query ──────────────────────────────────────────────────
    print("\n── Trying GetCart query ────────────────────────────────────────────")
    resp2 = gql(bearer, "GetCart", """
    query GetCart($sellerId: String, $siteId: String) {
      getCart(sellerId: $sellerId, siteId: $siteId) {
        cartId
        totalQuantity
        items {
          productId
          quantity
          unitOfMeasure
        }
      }
    }
    """, {"sellerId": "USBL", "siteId": "019"}, ctx=ctx)
    print(json.dumps(resp2, indent=2)[:1000])

    # ── 3. Try getDeliveryDates ───────────────────────────────────────────────
    print("\n── Trying getDeliveryDates ──────────────────────────────────────────")
    resp3 = gql(bearer, "GetDeliveryDates", """
    query GetDeliveryDates($sellerId: String, $siteId: String) {
      getDeliveryDates(sellerId: $sellerId, siteId: $siteId) {
        deliveryDate
        cutOffDate
        isDefault
      }
    }
    """, {"sellerId": "USBL", "siteId": "019"}, ctx=ctx)
    print(json.dumps(resp3, indent=2)[:1000])

    # ── 4. Try getOrderSummary ────────────────────────────────────────────────
    print("\n── Trying getOrderHistory ───────────────────────────────────────────")
    resp4 = gql(bearer, "GetOrderHistory", """
    query GetOrderHistory($sellerId: String, $siteId: String, $pageSize: Int) {
      getOrderHistory(sellerId: $sellerId, siteId: $siteId, pageSize: $pageSize) {
        orders {
          orderId
          orderDate
          totalAmount
          status
        }
      }
    }
    """, {"sellerId": "USBL", "siteId": "019", "pageSize": 5}, ctx=ctx)
    print(json.dumps(resp4, indent=2)[:1000])

    # ── 5. Try introspect query type ─────────────────────────────────────────
    print("\n── Introspecting query type for order-related fields ───────────────")
    resp5 = gql(bearer, "IntrospectQueries", """
    query IntrospectQueries {
      __schema {
        queryType {
          fields {
            name
          }
        }
      }
    }
    """, {}, ctx=ctx)
    fields5 = ((resp5.get("data") or {})
               .get("__schema", {})
               .get("queryType") or {}).get("fields", [])
    order_related = [f["name"] for f in fields5
                     if any(kw in f["name"].lower()
                            for kw in ["order", "cart", "delivery", "checkout", "submit"])]
    print(f"Order-related query fields: {order_related}")

if __name__ == "__main__":
    main()
