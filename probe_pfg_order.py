"""
Probe PFG CustomerFirst API for order entry endpoints.
Uses same auth as scrape_pfg.py.

Known working:
  - CreateOrderEntryHeader  → order_id, delivery_date
  - GetOrderEntryCustomerProductPrice

Probing:
  - AddOrderEntryDetail  (add line items)
  - GetOrderEntryDetails (list line items on an order)
  - SubmitOrderEntryHeader / FinalizeOrderEntryHeader / ConfirmOrderEntry
  - GetDeliveryDates
"""
import json, os, sys, urllib.request, urllib.error, urllib.parse
sys.path.insert(0, os.path.dirname(__file__))
from scrape_pfg import load_config, refresh_token, pfg_request, create_order, delete_order

def try_endpoint(method, ep, bearer, payload, label):
    print(f"\n── {label} ─────────────────────────────────────────────────────")
    try:
        resp = pfg_request(method, ep, bearer, payload)
        print(json.dumps(resp, indent=2)[:800])
        return resp
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        print(f"  HTTP {e.code}: {body}")
        return None
    except Exception as ex:
        print(f"  Error: {ex}")
        return None

def main():
    config      = load_config()
    customer_id = config.get("customer_id", "ccbddeae-bc43-4287-a4e0-8d5bee2b913c")
    list_id     = config.get("fall_list_id", "13e8ce85-8f4e-4cfe-a6dd-cac49a88dc60")
    biz_unit    = int(config.get("biz_unit_key", 3))
    opco_number = config.get("opco_number", "795")

    bearer = refresh_token(config)

    # Create a fresh temp order
    print("\nCreating temp order...")
    order_id, delivery_date = create_order(bearer, customer_id, biz_unit)
    print(f"  order_id: {order_id}")
    print(f"  delivery_date: {delivery_date}")

    try:
        # ── GetDeliveryDates ─────────────────────────────────────────────────
        try_endpoint("POST", "Delivery/V1/GetDeliveryDates", bearer,
                     {"CustomerId": customer_id, "BusinessUnitKey": biz_unit},
                     "GetDeliveryDates")

        # ── GetOrderEntryDetails (list existing line items) ──────────────────
        try_endpoint("POST", "OrderEntryDetail/V1/GetOrderEntryDetails", bearer,
                     {"OrderEntryHeaderId": order_id, "CustomerId": customer_id},
                     "GetOrderEntryDetails")

        # ── Try adding one item (a real item from our list) ──────────────────
        # ProductKey from a known PFG product (we'll use something from the last scrape)
        # First, get one valid ProductKey from the product list
        print("\n── Fetching first product from list (to get a valid ProductKey) ──")
        try:
            from scrape_pfg import get_products
            prods = get_products(bearer, customer_id, list_id)
            if prods:
                first = prods[0]
                pk    = first["ProductKey"]
                pn    = first["ProductNumber"]
                desc  = first["ProductDescription"]
                uom   = first["UOMs"][0]["UnitOfMeasure"] if first["UOMs"] else "CS"
                print(f"  First product: {pk} | {pn} | {desc[:50]} | UOM={uom}")

                # ── AddOrderEntryDetail ──────────────────────────────────────
                try_endpoint("POST", "OrderEntryDetail/V1/AddOrderEntryDetail", bearer,
                             {
                                 "OrderEntryHeaderId": order_id,
                                 "CustomerId":         customer_id,
                                 "ProductKey":         pk,
                                 "UnitOfMeasureType":  uom,
                                 "Quantity":           1,
                             },
                             "AddOrderEntryDetail (qty=1)")

                # ── GetOrderEntryDetails again (to see if item was added) ────
                try_endpoint("POST", "OrderEntryDetail/V1/GetOrderEntryDetails", bearer,
                             {"OrderEntryHeaderId": order_id, "CustomerId": customer_id},
                             "GetOrderEntryDetails (after add)")

                # ── Try SubmitOrderEntryHeader ───────────────────────────────
                print("\n── NOT submitting — just probing submit endpoint signature ──")
                for ep_name in ["SubmitOrderEntryHeader", "FinalizeOrderEntryHeader",
                                 "ConfirmOrderEntry", "PlaceOrder"]:
                    # Call with obviously wrong payload to see error type
                    try:
                        resp = pfg_request("POST",
                                           f"OrderEntryHeader/V1/{ep_name}", bearer,
                                           {"test": True})
                        print(f"  {ep_name}: EXISTS → {json.dumps(resp)[:200]}")
                    except urllib.error.HTTPError as e:
                        body = e.read().decode()[:200]
                        print(f"  {ep_name}: HTTP {e.code} → {body[:120]}")
                    except Exception as ex:
                        print(f"  {ep_name}: Error → {ex}")

        except Exception as ex:
            print(f"  Product fetch failed: {ex}")

    finally:
        # Always clean up
        print("\nCleaning up temp order...")
        delete_order(bearer, order_id, customer_id)

if __name__ == "__main__":
    main()
