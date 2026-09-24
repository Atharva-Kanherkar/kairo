"""Build and run the GoModel gateway from /work/repo against a local upstream.

GoModel reads config.yaml from its working directory, so each Gateway runs in a
fresh temporary directory holding one.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Optional

from kairo_verify import Service, free_port, request, run, wait_http

BINARY = "/tmp/kairo-gomodel"
MASTER_KEY = "kairo-gm-master"
MODEL = "captured-model"
_built = False


def build() -> None:
    global _built
    if not _built:
        code, out = run(["gomodel-build", BINARY], timeout=1800)
        if code != 0:
            raise RuntimeError("gomodel build failed:\n" + out[-4000:])
        _built = True


def config_yaml(port: int, upstream_base: str, model: str = MODEL) -> str:
    return (f'server:\n  port: "{port}"\n  master_key: "{MASTER_KEY}"\n'
            f'providers:\n  mock:\n    type: openai\n    base_url: "{upstream_base}"\n    api_key: "sk-kairo-provider"\n'
            f'    models:\n      - id: {model}\n')


class Gateway:
    def __init__(self, upstream_base: str, log_path: str = "/tmp/gomodel.log") -> None:
        self.upstream_base = upstream_base
        self.log_path = log_path
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> "Gateway":
        build()
        self.dir = tempfile.mkdtemp(prefix="kairo-gomodel-")
        with open(os.path.join(self.dir, "config.yaml"), "w") as fh:
            fh.write(config_yaml(self.port, self.upstream_base))
        env = {k: v for k, v in os.environ.items() if not any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET"))}
        self.service = Service([BINARY], env=env, cwd=self.dir, log_path=self.log_path).__enter__()
        if not wait_http(f"{self.url}/health", timeout=90, proc=self.service.proc, ok=lambda s: s == 200):
            tail = self.service.log_tail(60)
            self.service.__exit__(None, None, None)
            raise RuntimeError("GoModel did not become ready:\n" + tail)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.service.__exit__(*exc)

    def post_json(self, path: str, body: Any, headers: Optional[dict] = None, timeout: float = 60):
        hdrs = {"Authorization": f"Bearer {MASTER_KEY}", **(headers or {})}
        if path.startswith("/v1/messages"):
            hdrs.setdefault("anthropic-version", "2023-06-01")
        status, _, raw = request("POST", self.url + path, body, hdrs, timeout)
        try:
            parsed = json.loads(raw) if raw else None
        except (json.JSONDecodeError, ValueError):
            parsed = None
        return status, parsed, raw
