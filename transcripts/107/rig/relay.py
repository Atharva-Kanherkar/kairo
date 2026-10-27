"""Recording relay: http://127.0.0.1:PORT/... -> https://generativelanguage.googleapis.com/...
Writes each exchange as raw HTTP-ish bytes with x-goog-api-key redacted."""
import http.server, os, sys, urllib.request, itertools, threading
PORT = int(sys.argv[1]); OUT = sys.argv[2]; os.makedirs(OUT, exist_ok=True)
UP = "https://generativelanguage.googleapis.com"; ctr = itertools.count(1); lock = threading.Lock()
SECRET_HDRS = {"x-goog-api-key", "authorization"}
def hdrs(items): return "".join(f"{k}: {'[REDACTED]' if k.lower() in SECRET_HDRS else v}\r\n" for k, v in items)
class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def do_POST(self):
        with lock: n = next(ctr)
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        path = self.path.replace("key=", "key=[REDACTED]") if "key=" in self.path else self.path
        open(f"{OUT}/fwd-{n:03d}-request.http", "wb").write(f"POST {path} HTTP/1.1\r\n{hdrs(self.headers.items())}\r\n".encode() + body)
        fh = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length", "accept-encoding", "connection")}
        req = urllib.request.Request(UP + self.path, data=body, headers=fh, method="POST")
        try: r = urllib.request.urlopen(req, timeout=300); code = r.status; rh = r.headers.items(); rb = r.read()
        except urllib.error.HTTPError as e: code = e.code; rh = e.headers.items(); rb = e.read()
        rh = [(k, v) for k, v in rh if k.lower() not in ("transfer-encoding", "content-length", "content-encoding", "connection")]
        open(f"{OUT}/fwd-{n:03d}-response.http", "wb").write(f"HTTP/1.1 {code}\r\n{hdrs(rh)}\r\n".encode() + rb)
        self.send_response(code)
        for k, v in rh: self.send_header(k, v)
        self.send_header("content-length", str(len(rb))); self.end_headers(); self.wfile.write(rb)
    def log_message(self, *a): pass
http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
