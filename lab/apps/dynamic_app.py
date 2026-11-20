from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Lock

_count = 0
_lock = Lock()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        global _count
        with _lock:
            _count += 1
            count = _count
        body = json.dumps({
            "backend": "dynamic-31337",
            "count": count,
            "method": self.command,
            "host": self.headers.get("Host"),
            "path": self.path.split("?", 1)[0],
            "query": self.path.split("?", 1)[1] if "?" in self.path else "",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 31337), Handler).serve_forever()
