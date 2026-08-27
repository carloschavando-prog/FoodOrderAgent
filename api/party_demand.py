"""GET /api/party_demand — refresh and return one shared party window."""

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler

from party_demand import load_party_snapshot, refresh_party_demand


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        cycle = str(query.get("cycle", ["tuesday"])[0]).lower()
        delivery_date = query.get("delivery_date", [None])[0]
        should_refresh = query.get("refresh", ["1"])[0] != "0"
        print(json.dumps({
            "component": "api.party_demand",
            "event": "request_started",
            "cycle": cycle,
            "delivery_date": delivery_date,
            "refresh": should_refresh,
        }, separators=(",", ":")), flush=True)
        try:
            snapshot = (
                refresh_party_demand(cycle, delivery_date=delivery_date)
                if should_refresh
                else load_party_snapshot(cycle=cycle, delivery_date=delivery_date)
            )
            if not snapshot:
                self._json(404, {"error": "No party-demand snapshot is available."})
                return
            print(json.dumps({
                "component": "api.party_demand",
                "event": "request_completed",
                "snapshot_id": snapshot.get("id"),
                "source_status": snapshot.get("source_status"),
                "party_count": snapshot.get("party_count", 0),
            }, separators=(",", ":")), flush=True)
            self._json(200, {"snapshot": snapshot})
        except ValueError as exc:
            print(json.dumps({
                "component": "api.party_demand",
                "event": "request_rejected",
                "error": str(exc),
            }, separators=(",", ":")), flush=True)
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            print(json.dumps({
                "component": "api.party_demand",
                "event": "request_failed",
                "error": str(exc),
            }, separators=(",", ":")), flush=True)
            self._json(503, {"error": str(exc)})

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code, payload):
        data = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass
