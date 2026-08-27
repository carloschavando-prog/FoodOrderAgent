"""Preview, test-send, or explicitly live-send saved pricing feedback."""

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler

from order_feedback import (
    FeedbackConfig,
    FeedbackError,
    FeedbackService,
    SUPPLIERS,
    SupabaseFeedbackStore,
)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        try:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            order_id = (query.get("order_id") or [""])[0]
            supplier_id = int((query.get("supplier_id") or ["0"])[0])
            result = self._service().preview_supplier(order_id, supplier_id)
            self._json(200, {"success": True, "preview": result})
        except (ValueError, FeedbackError) as error:
            self._json(400, {"success": False, "error": str(error)})
        except Exception:
            self._json(
                500,
                {"success": False, "error": "Feedback preview could not be loaded"},
            )

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) if length else b"{}")
            action = str(payload.get("action") or "preview")
            order_id = str(payload.get("order_id") or "")
            supplier_value = payload.get("supplier_id")
            supplier_ids = (
                [int(supplier_value)] if supplier_value is not None else sorted(SUPPLIERS)
            )
            service = self._service()
            if action == "preview":
                results = [
                    service.preview_supplier(order_id, supplier_id)
                    for supplier_id in supplier_ids
                ]
            elif action in {"test-send", "live-send"}:
                # Merely hitting this endpoint is not enough to send.  The
                # caller must provide one of these explicit action strings;
                # live-send also requires the environment gate.
                results = [
                    service.send(order_id, supplier_id, action)
                    for supplier_id in supplier_ids
                ]
            else:
                raise FeedbackError(
                    "action must be preview, test-send, or live-send"
                )
            self._json(200, {"success": True, "action": action, "results": results})
        except (ValueError, FeedbackError) as error:
            self._json(400, {"success": False, "error": str(error)})
        except Exception:
            self._json(
                500,
                {"success": False, "error": "Feedback action could not be completed"},
            )

    @staticmethod
    def _service():
        store = SupabaseFeedbackStore.from_env()
        config = FeedbackConfig.from_env(require_representatives=True)
        return FeedbackService(store, config)

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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, fmt, *args):
        pass
