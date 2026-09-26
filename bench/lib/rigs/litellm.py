"""Run the real LiteLLM proxy CLI from /work/repo against a local upstream.

    with Upstream(responder) as up, litellm.Proxy(config=..., env=...) as proxy:
        status, headers, raw = proxy.post("/v1/messages", body)

``config`` is a dict written as YAML (or None to start without a config file).
The proxy always runs with a master key (the supported setup; current LiteLLM
refuses to start without one), and ``post``/``get`` send it unless ``headers``
already carry an ``Authorization`` value.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Optional

from kairo_verify import Service, free_port, request, wait_http

MASTER_KEY = "sk-kairo-master-6d1f0c"


class Proxy:
    def __init__(self, config: Optional[dict] = None, env: Optional[dict] = None, port: Optional[int] = None,
                 log_path: str = "/tmp/litellm-proxy.log", ready_timeout: float = 240) -> None:
        self.config = config
        self.env = env or {}
        self.port = port or free_port()
        self.log_path = log_path
        self.ready_timeout = ready_timeout
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "Proxy":
        argv = ["litellm", "--host", "127.0.0.1", "--port", str(self.port), "--num_workers", "1"]
        if self.config is not None:
            import yaml

            fd, path = tempfile.mkstemp(prefix="litellm-", suffix=".yaml")
            with os.fdopen(fd, "w") as fh:
                yaml.safe_dump(self.config, fh, sort_keys=False)
            argv += ["--config", path]
        env = {"LITELLM_LOG": "ERROR", "LITELLM_MODE": "PRODUCTION", "NO_DOCS": "True", "DISABLE_ADMIN_UI": "True",
               "LITELLM_MASTER_KEY": MASTER_KEY, **self.env}
        self.service = Service(argv, env=env, cwd="/tmp", log_path=self.log_path).__enter__()
        if not wait_http(f"{self.url}/health/liveliness", timeout=self.ready_timeout, proc=self.service.proc,
                         ok=lambda s: s == 200):
            tail = self.service.log_tail(60)
            self.service.__exit__(None, None, None)
            raise RuntimeError("LiteLLM proxy did not become ready:\n" + tail)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.service.__exit__(*exc)

    @staticmethod
    def _auth(headers: Optional[dict]) -> dict:
        hdrs = dict(headers or {})
        if not any(k.lower() == "authorization" for k in hdrs):
            hdrs["Authorization"] = f"Bearer {MASTER_KEY}"
        return hdrs

    def post(self, path: str, body: Any, headers: Optional[dict] = None, timeout: float = 120):
        return request("POST", self.url + path, body, self._auth(headers), timeout)

    def get(self, path: str, headers: Optional[dict] = None, timeout: float = 60):
        return request("GET", self.url + path, None, self._auth(headers), timeout)

    def post_json(self, path: str, body: Any, headers: Optional[dict] = None, timeout: float = 120):
        status, hdrs, raw = self.post(path, body, headers, timeout)
        try:
            parsed = json.loads(raw) if raw else None
        except (json.JSONDecodeError, ValueError):
            parsed = None
        return status, parsed, raw
