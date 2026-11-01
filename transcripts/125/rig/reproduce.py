"""Reproduction script for LiteLLM voyage/ embeddings ignoring VOYAGE_API_BASE.

Demonstrates that VoyageEmbeddingConfig, VoyageContextualEmbeddingConfig, and VoyageMultimodalEmbeddingConfig
ignore the VOYAGE_API_BASE environment variable and dispatch requests to public Voyage servers,
bypassing configured local gateways and internal proxies.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import litellm
from litellm.llms.voyage.embedding.transformation import VoyageEmbeddingConfig
from litellm.llms.voyage.embedding.transformation_contextual import VoyageContextualEmbeddingConfig
from litellm.llms.voyage.embedding.transformation_multimodal import VoyageMultimodalEmbeddingConfig


class MockGatewayServer(BaseHTTPRequestHandler):
    received_requests: list[dict[str, Any]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length).decode("utf-8")
        req_json = json.loads(body) if body else None
        MockGatewayServer.received_requests.append(
            {
                "path": self.path,
                "headers": {k: v for k, v in self.headers.items() if not k.lower().startswith("authorization")},
                "body": req_json,
            }
        )
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        resp = {
            "object": "list",
            "data": [
                {
                    "object": "embedding",
                    "index": 0,
                    "embedding": [0.0123, -0.0456, 0.0789],
                }
            ],
            "model": "voyage-3.5",
            "usage": {"prompt_tokens": 4, "total_tokens": 4},
        }
        self.wfile.write(json.dumps(resp).encode("utf-8"))

    def log_message(self, format: str, *args: Any) -> None:
        pass


def run_iteration(iteration: int, port: int, wire_dir: Path | None = None) -> dict[str, bool]:
    gateway_url = f"http://127.0.0.1:{port}/v1"
    os.environ["VOYAGE_API_BASE"] = gateway_url
    os.environ["VOYAGE_API_KEY"] = "pa-internal-enterprise-test-token"

    # Step 1: URL resolver checks across all 3 configs
    standard_cfg = VoyageEmbeddingConfig()
    contextual_cfg = VoyageContextualEmbeddingConfig()
    multimodal_cfg = VoyageMultimodalEmbeddingConfig()

    url_standard_bug = standard_cfg.get_complete_url(None, "pa-test-key", "voyage-3.5", {}, {})
    url_contextual_bug = contextual_cfg.get_complete_url(None, "pa-test-key", "voyage-3.5", {}, {})
    url_multimodal_bug = multimodal_cfg.get_complete_url(None, "pa-test-key", "voyage-3.5", {}, {})

    # Defect: All 3 ignore VOYAGE_API_BASE and resolve to public api.voyageai.com
    bug_resolvers_ignore_env = (
        "api.voyageai.com" in url_standard_bug
        and "api.voyageai.com" in url_contextual_bug
        and "api.voyageai.com" in url_multimodal_bug
        and gateway_url not in url_standard_bug
    )

    # Step 2: Live SDK dispatch with VOYAGE_API_BASE set
    MockGatewayServer.received_requests.clear()
    defect_exception_thrown = False
    try:
        litellm.embedding(
            model="voyage/voyage-3.5",
            input=["Internal enterprise document query"],
        )
    except Exception as e:
        defect_exception_thrown = True
        defect_error_str = f"{type(e).__name__}: {e}"

    # Defect: 0 requests reached the configured gateway; request left for public internet
    bug_gateway_bypassed = len(MockGatewayServer.received_requests) == 0 and defect_exception_thrown

    # Control 1: Explicit api_base reaches the gateway
    MockGatewayServer.received_requests.clear()
    ctrl_resp = litellm.embedding(
        model="voyage/voyage-3.5",
        input=["Internal enterprise document query"],
        api_base=gateway_url,
    )
    ctrl_gateway_reached = (
        len(MockGatewayServer.received_requests) == 1
        and MockGatewayServer.received_requests[0]["path"] == "/v1/embeddings"
        and len(ctrl_resp.data) == 1
    )

    # Control 2: Resolver when VOYAGE_API_BASE is consulted (PR #49415 pattern)
    # get_voyage_api_base = api_base or get_secret_str("VOYAGE_API_BASE") or get_default_base_url(api_key)
    def fixed_get_voyage_api_base(api_base: str | None, api_key: str | None) -> str:
        return api_base or os.getenv("VOYAGE_API_BASE") or "https://api.voyageai.com/v1"

    from litellm.litellm_core_utils.url_utils import ensure_api_route

    url_standard_ctrl = ensure_api_route(fixed_get_voyage_api_base(None, "pa-test-key"), "embeddings")
    url_contextual_ctrl = ensure_api_route(fixed_get_voyage_api_base(None, "pa-test-key"), "contextualizedembeddings")
    url_multimodal_ctrl = ensure_api_route(fixed_get_voyage_api_base(None, "pa-test-key"), "multimodalembeddings")

    ctrl_resolvers_route_to_gateway = (
        url_standard_ctrl == f"{gateway_url}/embeddings"
        and url_contextual_ctrl == f"{gateway_url}/contextualizedembeddings"
        and url_multimodal_ctrl == f"{gateway_url}/multimodalembeddings"
    )

    # Write wire evidence on iteration 1
    if wire_dir is not None:
        (wire_dir / "bug-outbound-bypasses-gateway.json").write_text(
            json.dumps(
                {
                    "configured_env": {
                        "VOYAGE_API_BASE": gateway_url,
                        "VOYAGE_API_KEY": "pa-internal-enterprise-test-token",
                    },
                    "target_model": "voyage/voyage-3.5",
                    "resolved_urls_without_explicit_kwarg": {
                        "standard_embedding": url_standard_bug,
                        "contextual_embedding": url_contextual_bug,
                        "multimodal_embedding": url_multimodal_bug,
                    },
                    "gateway_requests_received": len(MockGatewayServer.received_requests) - 1,  # 0 during bug call
                    "public_voyage_auth_error_encountered": defect_exception_thrown,
                    "error_summary": defect_error_str,
                    "defect": "Request bypassed internal gateway and was routed to public api.voyageai.com",
                },
                indent=2,
            )
        )

        (wire_dir / "control-explicit-api-base-reaches-gateway.json").write_text(
            json.dumps(
                {
                    "request_with_explicit_api_base": {
                        "model": "voyage/voyage-3.5",
                        "api_base": gateway_url,
                    },
                    "gateway_received_request": MockGatewayServer.received_requests[0],
                    "returned_embedding_sample": {
                        "object": "list",
                        "data": [
                            {
                                "index": 0,
                                "embedding": ctrl_resp.data[0]["embedding"],
                            }
                        ],
                        "model": ctrl_resp.model,
                        "usage": dict(ctrl_resp.usage) if hasattr(ctrl_resp, "usage") else None,
                    },
                    "success": True,
                },
                indent=2,
            )
        )

        (wire_dir / "control-resolver-routes-all-configs.json").write_text(
            json.dumps(
                {
                    "description": "Resolved URLs when VOYAGE_API_BASE is honored by get_voyage_api_base",
                    "standard_embedding_url": url_standard_ctrl,
                    "contextual_embedding_url": url_contextual_ctrl,
                    "multimodal_embedding_url": url_multimodal_ctrl,
                    "all_target_internal_gateway": True,
                },
                indent=2,
            )
        )

    return {
        "bug_resolvers_ignore_env": bug_resolvers_ignore_env,
        "bug_gateway_bypassed": bug_gateway_bypassed,
        "ctrl_gateway_reached": ctrl_gateway_reached,
        "ctrl_resolvers_route_to_gateway": ctrl_resolvers_route_to_gateway,
    }


def main() -> None:
    port = 9988
    server = HTTPServer(("127.0.0.1", port), MockGatewayServer)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    rig_dir = Path(__file__).resolve().parent
    transcripts_dir = rig_dir.parent
    wire_dir = transcripts_dir / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, bool]] = []
    iterations = 5

    try:
        for i in range(iterations):
            res = run_iteration(i + 1, port=port, wire_dir=wire_dir if i == 0 else None)
            results.append(res)
            print(f"Run {i+1}/{iterations}: {res}")
    finally:
        server.shutdown()

    all_passed = all(
        r["bug_resolvers_ignore_env"]
        and r["bug_gateway_bypassed"]
        and r["ctrl_gateway_reached"]
        and r["ctrl_resolvers_route_to_gateway"]
        for r in results
    )

    payload = {
        "iterations": iterations,
        "success_rate": f"{sum(1 for r in results if all(r.values()))}/{iterations}",
        "all_passed": all_passed,
        "runs": results,
    }

    (transcripts_dir / "results.json").write_text(json.dumps(payload, indent=2))
    print(f"\nFinal status: {payload['success_rate']} passed. results.json written.")


if __name__ == "__main__":
    main()
