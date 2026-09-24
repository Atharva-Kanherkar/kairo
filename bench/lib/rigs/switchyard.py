"""Build and run switchyard-server from /work/repo against a local upstream.

    with switchyard.Gateway(switchyard.passthrough(up.url + "/v1", "openai_chat")) as gw:
        status, headers, raw = gw.post("/v1/messages", body)

``passthrough`` writes the smallest config: one llm_client, one target, and one
passthrough route whose model id is ``captured-model``.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Optional

from kairo_verify import Service, free_port, request, run, wait_http

_binary: Optional[str] = None
MODEL = "captured-model"


def build() -> str:
    global _binary
    if _binary is None:
        code, out = run(["switchyard-build"], timeout=3000)
        if code != 0:
            raise RuntimeError("switchyard build failed:\n" + out[-4000:])
        _binary = out.strip().splitlines()[-1]
    return _binary


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return json.dumps(str(v))


def passthrough(base_url: str, fmt: str = "openai_chat", *, extra_headers: Optional[dict] = None,
                client: Optional[dict] = None, model: str = MODEL) -> str:
    """Config TOML text for one passthrough route. ``fmt``: openai_chat, anthropic_messages, openai_responses."""
    lines = ["schema_version = 1", "[llm_clients.local]", f"format = {_toml_value(fmt)}",
             f"base_url = {_toml_value(base_url)}"]
    for k, v in (client or {}).items():
        lines.append(f"{k} = {_toml_value(v)}")
    if extra_headers:
        lines.append("[llm_clients.local.extra_headers]")
        lines += [f"{json.dumps(k)} = {_toml_value(v)}" for k, v in extra_headers.items()]
    lines += ["[targets.cap]", f"id = {_toml_value(model)}", 'llm_client = "local"',
              "[routes.primary]", f"id = {_toml_value(model)}", 'type = "passthrough"', 'target = "cap"']
    return "\n".join(lines) + "\n"


class Gateway:
    def __init__(self, config_toml: str, env: Optional[dict] = None, log_path: str = "/tmp/switchyard.log") -> None:
        self.config_toml = config_toml
        self.env = env or {}
        self.log_path = log_path
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "Gateway":
        binary = build()
        fd, path = tempfile.mkstemp(prefix="switchyard-", suffix=".toml")
        with os.fdopen(fd, "w") as fh:
            fh.write(self.config_toml)
        self.service = Service([binary, "--config", path, "--host", "127.0.0.1", "--port", str(self.port)],
                               env={"RUST_LOG": "warn", **self.env}, cwd="/tmp", log_path=self.log_path).__enter__()
        if not wait_http(f"{self.url}/health", timeout=60, proc=self.service.proc, ok=lambda s: s == 200):
            tail = self.service.log_tail(60)
            self.service.__exit__(None, None, None)
            raise RuntimeError("switchyard-server did not become ready:\n" + tail)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.service.__exit__(*exc)

    def post(self, path: str, body: Any, headers: Optional[dict] = None, timeout: float = 60):
        hdrs = {"anthropic-version": "2023-06-01", **(headers or {})} if path.startswith("/v1/messages") else headers
        return request("POST", self.url + path, body, hdrs, timeout)

    def post_json(self, path: str, body: Any, headers: Optional[dict] = None, timeout: float = 60):
        status, hdrs, raw = self.post(path, body, headers, timeout)
        try:
            parsed = json.loads(raw) if raw else None
        except (json.JSONDecodeError, ValueError):
            parsed = None
        return status, parsed, raw
