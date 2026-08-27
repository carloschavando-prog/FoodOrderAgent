"""Read-only vendor sign-on checks run before an order is staged or submitted."""

import concurrent.futures
import json
from http.server import BaseHTTPRequestHandler

from api import place_order_pfg as pfg
from api import place_order_sysco as sysco
from api import place_order_usfoods as usfoods


VENDOR_NAMES = {1: "US Foods", 2: "PFG", 3: "Sysco"}


def check_vendor(vendor_id):
    """Authenticate and make, at most, a read-only availability request."""
    try:
        if vendor_id == 1:
            bearer, _ = usfoods.authenticate_usfoods()
            usfoods.get_delivery_date(bearer)
        elif vendor_id == 2:
            # A successful B2C exchange proves the server-held refresh chain.
            pfg.authenticate_pfg()
        elif vendor_id == 3:
            bearer, shop_account_id, csrf_token, visitor_id = (
                sysco.get_bearer_token(sysco.EMAIL, sysco.PASSWORD)
            )
            sysco.get_delivery_date(bearer, {
                "shop_account_id": shop_account_id,
                "csrf_token": csrf_token,
                "vid": visitor_id,
            })
        else:
            raise ValueError("Unsupported vendor")
        return {
            "vendorId": vendor_id,
            "vendor": VENDOR_NAMES[vendor_id],
            "ready": True,
            "error": None,
        }
    except Exception as ex:
        return {
            "vendorId": vendor_id,
            "vendor": VENDOR_NAMES.get(vendor_id, f"Vendor {vendor_id}"),
            "ready": False,
            "error": str(ex),
        }


def check_vendors(vendor_ids):
    unique_ids = sorted({int(value) for value in vendor_ids})
    if not unique_ids or any(value not in VENDOR_NAMES for value in unique_ids):
        raise ValueError("Select at least one supported vendor")
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(unique_ids)
    ) as executor:
        results = list(executor.map(check_vendor, unique_ids))
    return results


class handler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) if length else b"{}")
            results = check_vendors(body.get("vendorIds") or [])
            success = all(result["ready"] for result in results)
            payload = {
                "success": success,
                "results": results,
                "error": None if success else (
                    "One or more vendor sign-ons need attention. "
                    "No order was saved or submitted."
                ),
            }
        except Exception as ex:
            payload = {"success": False, "results": [], "error": str(ex)}

        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self._cors()
        self.end_headers()
        self.wfile.write(encoded)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        pass
