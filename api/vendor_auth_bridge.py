"""Restricted server-to-server bridge for vendor credential rotation in CI."""

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler

from api.vendor_auth import VendorAuthClient


ALLOWED_VENDOR_IDS = {1, 2}


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        try:
            expected = os.getenv("VENDOR_AUTH_BRIDGE_SECRET", "").strip()
            supplied = self.headers.get("Authorization", "")
            if supplied.startswith("Bearer "):
                supplied = supplied[7:]
            if not expected or not hmac.compare_digest(expected, supplied):
                self._send(401, {"success": False, "error": "Unauthorized"})
                return

            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 262_144:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            action = str(body.get("action", ""))
            vendor_id = int(body.get("vendorId", 0))
            if vendor_id not in ALLOWED_VENDOR_IDS:
                raise ValueError("Unsupported vendor")

            store = VendorAuthClient.from_env(direct=True)
            if action == "claim":
                owner = str(body.get("owner", ""))
                if not owner:
                    raise ValueError("Missing claim owner")
                lease = store.claim(
                    vendor_id,
                    owner=owner,
                    lease_seconds=int(body.get("leaseSeconds", 45)),
                    wait_seconds=0,
                )
                self._send(200, {
                    "success": True,
                    "credentials": lease.credentials,
                })
                return
            if action == "commit":
                store.commit(
                    vendor_id,
                    str(body.get("owner", "")),
                    body.get("credentials"),
                    verified=bool(body.get("verified", True)),
                )
            elif action == "fail":
                store.fail(
                    vendor_id,
                    str(body.get("owner", "")),
                    str(body.get("error", "")),
                )
            elif action == "replace":
                store.replace(vendor_id, body.get("credentials"))
            else:
                raise ValueError("Unsupported action")
            self._send(200, {"success": True})
        except Exception as ex:
            self._send(400, {"success": False, "error": str(ex)})

    def _send(self, status, value):
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass
