"""Persist the generated order and item lines before vendor submission."""

import json
import uuid
from http.server import BaseHTTPRequestHandler

from order_feedback import FeedbackError, SupabaseFeedbackStore, stage_order


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) if length else b"{}")
            order = payload.get("order") or {}
            self._validate_order(order)
            saved = stage_order(SupabaseFeedbackStore.from_env(), order)
            self._json(
                200,
                {
                    "success": True,
                    "orderId": saved["id"],
                    "orderStatus": saved["status"],
                    "itemCount": int(saved.get("item_total") or 0),
                    "caseCount": float(saved.get("case_total") or 0),
                },
            )
        except (ValueError, FeedbackError) as error:
            self._json(400, {"success": False, "error": str(error)})
        except Exception:
            self._json(
                500,
                {"success": False, "error": "Generated order could not be saved"},
            )

    @staticmethod
    def _validate_order(order):
        try:
            uuid.UUID(str(order.get("order_id") or ""))
        except ValueError as error:
            raise ValueError("order.order_id must be a valid UUID") from error
        if not str(order.get("order_date") or ""):
            raise ValueError("order.order_date is required")
        for field in ("expected_supplier_ids", "order_lines", "decisions"):
            if not isinstance(order.get(field), list):
                raise ValueError(f"order.{field} must be a list")
        if not order["order_lines"]:
            raise ValueError("order.order_lines must contain the submitted items")

    def _json(self, status, body):
        response = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(response)))
        self._cors()
        self.end_headers()
        self.wfile.write(response)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        pass
