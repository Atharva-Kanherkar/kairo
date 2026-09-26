"""Build and run the Bifrost HTTP gateway from /work/repo against a local upstream.

    with bifrost.Gateway(config) as gw:
        status, headers, raw = gw.post("/anthropic/v1/messages", body)

The gateway is rebuilt from the checkout on first use in a process (the image
ships a warm Go build cache, so this takes seconds), then started with a fresh
app dir holding ``config.json``.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Optional

from kairo_verify import Service, free_port, request, run, wait_http

BINARY = "/tmp/kairo-bifrost-http"
_built = False


def build() -> None:
    global _built
    if _built:
        return
    code, out = run(["bifrost-build", BINARY], timeout=1800)
    if code != 0:
        raise RuntimeError("bifrost build failed:\n" + out[-4000:])
    _built = True


def openai_provider(base_url: str, **extra: Any) -> dict:
    """Built-in `openai` provider pointed at a local upstream (Responses API by default)."""
    return {"keys": [{"name": "kairo-key", "value": "sk-kairo-provider", "weight": 1, "models": ["*"]}],
            "network_config": {"base_url": base_url, "max_retries": 0, **extra.pop("network_config", {})}, **extra}


def chat_only_provider(base_url: str, **extra: Any) -> dict:
    """Custom OpenAI-compatible provider that only allows Chat Completions."""
    p = openai_provider(base_url, **extra)
    p["custom_provider_config"] = {"base_provider_type": "openai", "allowed_requests": {
        "list_models": True, "chat_completion": True, "chat_completion_stream": True}}
    return p


DATASHEETS = "/work/repo/framework/modelcatalog/datasheet/testdata"


def config(providers: dict, **extra: Any) -> dict:
    """A config.json for an offline gateway: the pricing, model-parameter, and MCP-library
    catalogs are read from files (Bifrost's documented air-gapped mode), not getbifrost.ai."""
    return {"config_store": extra.pop("config_store", {"enabled": False}),
            "client": {"enable_logging": False, "initial_pool_size": 8, **extra.pop("client", {})},
            "framework": {"pricing": {"pricing_url": f"file://{DATASHEETS}/pricing.json",
                                      "model_parameters_url": f"file://{DATASHEETS}/model-parameters.json",
                                      "mcp_library_url": "file://__APP_DIR__/mcp-library.json"}},
            "providers": providers, **extra}


class Gateway:
    def __init__(self, cfg: dict, log_path: str = "/tmp/bifrost.log", ready_timeout: float = 120) -> None:
        self.cfg = cfg
        self.log_path = log_path
        self.ready_timeout = ready_timeout
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "Gateway":
        build()
        self.app_dir = tempfile.mkdtemp(prefix="kairo-bifrost-")
        cfg = json.loads(json.dumps(self.cfg).replace("__APP_DIR__", self.app_dir))
        with open(os.path.join(self.app_dir, "config.json"), "w") as fh:
            json.dump(cfg, fh, indent=2)
        with open(os.path.join(self.app_dir, "mcp-library.json"), "w") as fh:
            json.dump({"servers": []}, fh)
        env = {k: v for k, v in os.environ.items() if not any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET"))}
        self.service = Service([BINARY, "-app-dir", self.app_dir, "-host", "127.0.0.1", "-port", str(self.port),
                                "-log-level", "error"], env=env, cwd=self.app_dir, log_path=self.log_path).__enter__()
        if not wait_http(f"{self.url}/health", timeout=self.ready_timeout, proc=self.service.proc,
                         ok=lambda s: s < 500):
            tail = self.service.log_tail(60)
            self.service.__exit__(None, None, None)
            raise RuntimeError("Bifrost did not become ready:\n" + tail)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.service.__exit__(*exc)

    def post(self, path: str, body: Any, headers: Optional[dict] = None, timeout: float = 120):
        return request("POST", self.url + path, body, headers, timeout)

    def get(self, path: str, headers: Optional[dict] = None, timeout: float = 60):
        return request("GET", self.url + path, None, headers, timeout)

    def post_json(self, path: str, body: Any, headers: Optional[dict] = None, timeout: float = 120):
        status, hdrs, raw = self.post(path, body, headers, timeout)
        try:
            parsed = json.loads(raw) if raw else None
        except (json.JSONDecodeError, ValueError):
            parsed = None
        return status, parsed, raw
