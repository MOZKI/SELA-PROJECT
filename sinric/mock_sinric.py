#!/usr/bin/env python3
"""Sinric palsu untuk uji kering sinric_probe.py tanpa akun dan tanpa ESP32.

  python3 mock_sinric.py &
  export SINRIC_API_BASE=http://localhost:8790 SINRIC_SSE_URL=http://localhost:8790/sse/stream
  export SINRIC_API_KEY=x SINRIC_DEVICE_A_ID=devA
  python3 sinric_probe.py latency A -n 3 --gap 1
"""
import json
import queue
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

listeners = []


def broadcast(ev: dict) -> None:
    for q in list(listeners):
        q.put(json.dumps(ev))


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/sse/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            q = queue.Queue()
            listeners.append(q)
            try:
                while True:
                    self.wfile.write((q.get() + "\n").encode())
                    self.wfile.flush()
            except OSError:
                listeners.remove(q)
        else:
            body = json.dumps({"success": True, "devices": [{"id": "devA", "name": "Lampu 10 W"}]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n))
        dev = self.path.split("/")[4]
        state = json.loads(req["value"])["state"]
        threading.Timer(random.uniform(0.2, 0.7), broadcast, [{"event": "deviceMessageArrived",
                        "device": {"name": "mock", "id": dev, "powerState": state}}]).start()
        body = b'{"success":true,"message":"OK. Your message has been queued for processing."}'
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


if __name__ == "__main__":
    print("Mock Sinric di http://localhost:8790")
    ThreadingHTTPServer(("127.0.0.1", 8790), H).serve_forever()
