# OGX capture upstream for kairo 081: OpenAI-compatible mock that serves
# canned /v1/models and per-scenario chat responses, and records every
# forwarded request body unmutated. Scenario is selected by a marker in the
# first message text: "|scenario:NAME|". No credentials are recorded.
import json, os, re, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT, OUT, CANNED_DIR = int(sys.argv[1]), sys.argv[2], sys.argv[3]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, body, ctype):
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        data = open(os.path.join(CANNED_DIR, "models.json"), "rb").read()
        self._reply(data, "application/json")

    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(n)
        scenario, stream = "default", False
        try:
            body = json.loads(raw)
            if isinstance(body, dict):
                first = body.get("messages", [{}])[0]
                content = first.get("content") if isinstance(first, dict) else None
                text = content if isinstance(content, str) else json.dumps(content)
                m = re.search(r"\|scenario:([a-z0-9_-]+)\|", text or "")
                if m:
                    scenario = m.group(1)
                stream = bool(body.get("stream"))
        except Exception:
            body = raw
        with open(OUT, "a") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "path": self.path,
                "scenario": scenario,
                "body": body,
            }) + "\n")
        f_json = os.path.join(CANNED_DIR, f"{scenario}.json")
        f_sse = os.path.join(CANNED_DIR, f"{scenario}.sse")
        if stream and os.path.exists(f_sse):
            self._reply(open(f_sse, "rb").read(), "text/event-stream")
        elif not stream and os.path.exists(f_json):
            self._reply(open(f_json, "rb").read(), "application/json")
        else:
            self.send_response(500)
            self.end_headers()

HTTPServer(("127.0.0.1", PORT), H).serve_forever()
