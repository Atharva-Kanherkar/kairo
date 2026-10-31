import asyncio
import http.server
import json
import os
import sys
import threading
import time
import uuid
import httpx

sys.path.insert(0, "/Users/atharva/litellm-main")

os.environ["DATABASE_URL"] = os.environ.get(
    "DATABASE_URL", "postgresql://atharva@localhost:5432/litellm_kairo"
)
os.environ["LITELLM_MASTER_KEY"] = "sk-master-key-115"

import litellm
import litellm.proxy.proxy_server as ps
from litellm.proxy.proxy_server import app, ProxyStartupEvent

MOCK_PORT = 19999
WIRE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "wire")
)
os.makedirs(WIRE_DIR, exist_ok=True)


class MockOpenAIHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        req_data = json.loads(body.decode("utf-8")) if body else {}
        model = req_data.get("model", "unknown-model")

        resp = {
            "id": f"chatcmpl-mock-115-{uuid.uuid4().hex[:6]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"Mock response successfully served for {model}",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 8,
                "completion_tokens": 8,
                "total_tokens": 16,
            },
        }
        resp_bytes = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_bytes)))
        self.end_headers()
        self.wfile.write(resp_bytes)

    def log_message(self, format, *args):
        pass


def start_mock_upstream():
    server = http.server.HTTPServer(("127.0.0.1", MOCK_PORT), MockOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


async def run_reproduction(iterations: int = 5):
    mock_server = start_mock_upstream()

    # 1. Initialize Prisma client with local PostgreSQL
    client_obj = await ProxyStartupEvent.setup_prisma_client(
        database_url=os.environ["DATABASE_URL"],
        proxy_logging_obj=ps.proxy_logging_obj,
        user_api_key_cache=ps.user_api_key_cache,
    )
    ps.prisma_client = client_obj
    ps.master_key = "sk-master-key-115"
    ps.general_settings["master_key"] = "sk-master-key-115"

    # Configure Router with two models: allowed-model and forbidden-model
    router_config = [
        {
            "model_name": "allowed-model",
            "litellm_params": {
                "model": "openai/allowed-model",
                "api_key": "fake-key",
                "api_base": f"http://127.0.0.1:{MOCK_PORT}/v1",
            },
        },
        {
            "model_name": "forbidden-model",
            "litellm_params": {
                "model": "openai/forbidden-model",
                "api_key": "fake-key",
                "api_base": f"http://127.0.0.1:{MOCK_PORT}/v1",
            },
        },
    ]
    ps.llm_router = litellm.Router(model_list=router_config)

    admin_headers = {"Authorization": "Bearer sk-master-key-115"}

    results_matrix = []

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for run_idx in range(1, iterations + 1):
            run_uid = f"run{run_idx}-{uuid.uuid4().hex[:6]}"

            # Step 1: Create Access Group allowing only 'allowed-model'
            ag_payload = {
                "access_group_name": f"ag-restricted-{run_uid}",
                "access_model_names": ["allowed-model"],
            }
            resp_ag = await client.post(
                "/v1/access_group", headers=admin_headers, json=ag_payload
            )
            assert resp_ag.status_code == 201, f"Failed to create access group: {resp_ag.text}"
            ag_data = resp_ag.json()
            ag_id = ag_data["access_group_id"]

            if run_idx == 1:
                with open(
                    os.path.join(WIRE_DIR, "access-group-create-request.json"), "w"
                ) as f:
                    json.dump(ag_payload, f, indent=2)
                with open(
                    os.path.join(WIRE_DIR, "access-group-create-response.json"), "w"
                ) as f:
                    json.dump(ag_data, f, indent=2)

            # Step 2: Generate Trigger Key with models: [] and access_group_ids: [ag_id]
            key_payload = {
                "key_alias": f"trigger-key-{run_uid}",
                "models": [],
                "access_group_ids": [ag_id],
            }
            resp_key = await client.post(
                "/key/generate", headers=admin_headers, json=key_payload
            )
            assert resp_key.status_code == 200, f"Failed to generate key: {resp_key.text}"
            key_data = resp_key.json()
            trigger_key = key_data["key"]

            if run_idx == 1:
                with open(
                    os.path.join(WIRE_DIR, "key-generate-request.json"), "w"
                ) as f:
                    json.dump(key_payload, f, indent=2)
                with open(
                    os.path.join(WIRE_DIR, "key-generate-response.json"), "w"
                ) as f:
                    # Sanitize token
                    sanitized_key_data = dict(key_data)
                    sanitized_key_data["key"] = "sk-litellm-trigger-key-redacted"
                    json.dump(sanitized_key_data, f, indent=2)

            # Step 3: Generate Control Key with native models: ["allowed-model"]
            ctrl_payload = {
                "key_alias": f"control-key-{run_uid}",
                "models": ["allowed-model"],
            }
            resp_ctrl = await client.post(
                "/key/generate", headers=admin_headers, json=ctrl_payload
            )
            assert resp_ctrl.status_code == 200
            ctrl_key = resp_ctrl.json()["key"]

            # Step 4: Control Key calls forbidden-model (Expected 403 Forbidden)
            forbidden_chat_payload = {
                "model": "forbidden-model",
                "messages": [{"role": "user", "content": "hello from control"}],
            }
            ctrl_resp = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {ctrl_key}"},
                json=forbidden_chat_payload,
            )
            ctrl_status = ctrl_resp.status_code

            if run_idx == 1:
                with open(
                    os.path.join(
                        WIRE_DIR, "control-key-forbidden-response.json"
                    ),
                    "w",
                ) as f:
                    json.dump(ctrl_resp.json(), f, indent=2)

            # Step 5: Trigger Key calls forbidden-model (Expected 403 by spec, Observed 200 OK bypass)
            trigger_forbidden_resp = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {trigger_key}"},
                json=forbidden_chat_payload,
            )
            trigger_forbidden_status = trigger_forbidden_resp.status_code

            if run_idx == 1:
                with open(
                    os.path.join(WIRE_DIR, "trigger-key-forbidden-request.json"),
                    "w",
                ) as f:
                    json.dump(forbidden_chat_payload, f, indent=2)
                with open(
                    os.path.join(WIRE_DIR, "trigger-key-forbidden-response.json"),
                    "w",
                ) as f:
                    json.dump(trigger_forbidden_resp.json(), f, indent=2)

            # Step 6: Trigger Key calls allowed-model (Expected 200 OK)
            allowed_chat_payload = {
                "model": "allowed-model",
                "messages": [{"role": "user", "content": "hello from trigger"}],
            }
            trigger_allowed_resp = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {trigger_key}"},
                json=allowed_chat_payload,
            )
            trigger_allowed_status = trigger_allowed_resp.status_code

            if run_idx == 1:
                with open(
                    os.path.join(WIRE_DIR, "trigger-key-allowed-request.json"),
                    "w",
                ) as f:
                    json.dump(allowed_chat_payload, f, indent=2)
                with open(
                    os.path.join(WIRE_DIR, "trigger-key-allowed-response.json"),
                    "w",
                ) as f:
                    json.dump(trigger_allowed_resp.json(), f, indent=2)

            results_matrix.append(
                {
                    "run": run_idx,
                    "control_key_forbidden_model_status": ctrl_status,
                    "trigger_key_forbidden_model_status": trigger_forbidden_status,
                    "trigger_key_allowed_model_status": trigger_allowed_status,
                }
            )

    if ps.prisma_client:
        await ps.prisma_client.disconnect()
    mock_server.shutdown()

    summary = {
        "runs_completed": iterations,
        "results": results_matrix,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(run_reproduction())
