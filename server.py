"""
On Par Order Server — local HTTP server
  GET  /*                → serves static files from project root
  POST /generate-order   → runs weekly_order.py with actual on-hand counts,
                           returns {"ok": true} when done
  OPTIONS /*             → CORS preflight (needed when index.html is on file://)

Start:  python3 server.py
Auto-start: configured via ~/Library/LaunchAgents/com.onpar.orderserver.plist
Access: http://localhost:3457/index.html
"""

import http.server
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 3457

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    # ── CORS preflight ────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    # ── POST /generate-order ──────────────────────────────
    def do_POST(self):
        if self.path != "/generate-order":
            self.send_error(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except Exception as e:
            self._json(400, {"ok": False, "error": f"Bad JSON: {e}"})
            return

        # Write on-hand counts to a temp file (weekly_order.py reads it)
        counts_path = os.path.join(ROOT, ".order_counts.json")
        with open(counts_path, "w") as f:
            json.dump(data, f, indent=2)

        print(f"  [server] Received counts for {len(data)} items, running weekly_order.py…")

        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "weekly_order.py"),
             "--from-count", counts_path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode == 0:
            print("  [server] weekly_order.py succeeded")
            self._json(200, {"ok": True})
        else:
            print(f"  [server] weekly_order.py FAILED:\n{result.stderr[:400]}")
            self._json(500, {"ok": False, "error": result.stderr[-300:]})

    # ── helpers ───────────────────────────────────────────
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        print(f"  [server] {fmt % args}")


if __name__ == "__main__":
    os.chdir(ROOT)
    print(f"On Par Order Server listening on http://127.0.0.1:{PORT}")
    print(f"  Serving files from: {ROOT}")
    print(f"  Open: http://localhost:{PORT}/index.html")
    print(f"  Stop: Ctrl-C")
    try:
        http.server.test(HandlerClass=Handler, port=PORT, bind="127.0.0.1")
    except KeyboardInterrupt:
        print("\nServer stopped.")
