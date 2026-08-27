"""Save a completed multi-vendor order, then prepare pricing-feedback previews."""

import json
import uuid
from http.server import BaseHTTPRequestHandler

from order_feedback import (
    FeedbackConfig,
    FeedbackError,
    FeedbackService,
    SupabaseFeedbackStore,
    save_final_order,
)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(body)
            order = payload.get("order") or {}
            submissions = payload.get("submissions") or {}
            self._validate_order(order)

            store = SupabaseFeedbackStore.from_env()
            saved = save_final_order(store, order, submissions)
            feedback_error = None
            try:
                config = FeedbackConfig.from_env(require_representatives=True)
                previews = FeedbackService(store, config).prepare_order(saved["id"])
                summary = [
                    {
                        "supplierId": row["supplier_id"],
                        "status": row["status"],
                        "intendedRecipient": row["intended_recipient"],
                        "subject": row["subject"],
                        "itemCount": row["item_total"],
                        "caseCount": row["case_total"],
                    }
                    for row in previews
                ]
            except FeedbackError as error:
                # The food order is already durable and finalized. Feedback is
                # independently retryable and must never make the saved order
                # look lost or failed.
                summary = []
                feedback_error = str(error)
            self._json(
                200,
                {
                    "success": True,
                    "orderId": saved["id"],
                    "orderStatus": saved["status"],
                    "deliveryMode": "dry-run",
                    "feedback": summary,
                    "feedbackPrepared": feedback_error is None,
                    "feedbackError": feedback_error,
                },
            )
        except (ValueError, FeedbackError) as error:
            self._json(400, {"success": False, "error": str(error)})
        except Exception:
            self._json(
                500,
                {
                    "success": False,
                    "error": "Order was not finalized for feedback preparation",
                },
            )

    @staticmethod
    def _validate_order(order):
        order_id = str(order.get("order_id") or "")
        try:
            uuid.UUID(order_id)
        except ValueError as error:
            raise ValueError("order.order_id must be a valid UUID") from error
        if not str(order.get("order_date") or ""):
            raise ValueError("order.order_date is required")
        if not isinstance(order.get("decisions"), list):
            raise ValueError("order.decisions must be a list")
        if not isinstance(order.get("expected_supplier_ids"), list):
            raise ValueError("order.expected_supplier_ids must be a list")

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
