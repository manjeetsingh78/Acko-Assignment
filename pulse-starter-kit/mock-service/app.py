"""Mock Acko service for the Pulse starter kit.

GET  /health                    health check (behaviour depends on the current mode)
GET  /                          a small sample response, like a real service
GET  /control/state             current mode
POST /control/down              health returns 503 until reset
POST /control/slow?ms=3000      health answers successfully, but only after the delay
POST /control/flaky?rate=0.3    health fails at random with the given probability (0 to 1)
POST /control/reset             back to normal

Do not change this file: the evaluators test against the original kit.
"""
import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SERVICE = os.environ.get("SERVICE_NAME", "service")
PORT = int(os.environ.get("PORT", "8080"))
# A realistic internal error message. It must never reach a public status page.
INTERNAL_ERROR = os.environ.get("INTERNAL_ERROR", f"upstream {SERVICE}-db timeout at 10.12.4.7:5432")

state = {"mode": "normal", "ms": 0, "rate": 0.0, "since": time.time()}
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # one short line per request
        print(f"{SERVICE} {self.command} {self.path} {args[1] if len(args) > 1 else ''}", flush=True)

    def do_GET(self):
        path = urlparse(self.path).path
        with lock:
            s = dict(state)
        if path == "/health":
            if s["mode"] == "down":
                return self._send(503, {"status": "error", "service": SERVICE, "error": INTERNAL_ERROR})
            if s["mode"] == "slow":
                time.sleep(s["ms"] / 1000)
            if s["mode"] == "flaky" and random.random() < s["rate"]:
                return self._send(500, {"status": "error", "service": SERVICE, "error": INTERNAL_ERROR})
            return self._send(200, {"status": "ok", "service": SERVICE})
        if path == "/":
            return self._send(200, {"service": SERVICE, "message": f"Hello from {SERVICE}"})
        if path == "/control/state":
            return self._send(200, {"service": SERVICE, **s})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        with lock:
            if u.path == "/control/down":
                state.update(mode="down", ms=0, rate=0.0)
            elif u.path == "/control/slow":
                ms = int(q.get("ms", ["3000"])[0])
                if not 0 <= ms <= 60000:
                    return self._send(400, {"error": "ms must be between 0 and 60000"})
                state.update(mode="slow", ms=ms, rate=0.0)
            elif u.path == "/control/flaky":
                rate = float(q.get("rate", ["0.3"])[0])
                if not 0 <= rate <= 1:
                    return self._send(400, {"error": "rate must be between 0 and 1"})
                state.update(mode="flaky", ms=0, rate=rate)
            elif u.path == "/control/reset":
                state.update(mode="normal", ms=0, rate=0.0)
            else:
                return self._send(404, {"error": "not found"})
            state["since"] = time.time()
            s = dict(state)
        print(f"{SERVICE} mode -> {s['mode']}", flush=True)
        return self._send(200, {"service": SERVICE, **s})


if __name__ == "__main__":
    print(f"{SERVICE} listening on port {PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
