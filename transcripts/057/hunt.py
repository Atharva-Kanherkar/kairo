#!/usr/bin/env python3
"""057 any-llm SDK first-pass hunt: Messages bridge -> OpenAI-compatible mock.

any-llm-sdk 1.26.0 via create_openai_compatible(api_base=mock). Records every
upstream POST body in cap.jsonl, then writes per-check jsonl fixtures for the
Rust harness. No API keys required.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "transcripts/057"
N = 5
MOCK = 9996
MODEL = "captured-model"
THINK = "THINKPROBE"
STOP = "STOPPROBE"
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
DOC = "DOCBODY"
SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "ok": {"type": "boolean"}},
    "required": ["city", "ok"],
    "additionalProperties": False,
}
TOOL = {
    "name": "lookup_city",
    "description": "Look up a city code",
    "input_schema": {
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
}
PROCS: list[subprocess.Popen] = []


def wait_port(port: int, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = socket.socket()
        s.settimeout(0.25)
        ok = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        if ok:
            return
        time.sleep(0.1)
    raise RuntimeError(f"port {port} did not come up")


def spawn(args: list[str], log_name: str) -> subprocess.Popen:
    log = open(OUT / log_name, "ab")
    proc = subprocess.Popen(
        args,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    proc._kairo_log = log  # type: ignore[attr-defined]
    PROCS.append(proc)
    return proc


def kill_all() -> None:
    for proc in PROCS:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        log = getattr(proc, "_kairo_log", None)
        if log:
            log.close()
    PROCS.clear()


def last_caps(n: int = 1) -> list[dict]:
    path = OUT / "cap.jsonl"
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def capture_call(label: str, **kwargs) -> dict:
    before = last_caps(1)
    # Import here so venv can be injected via PYTHONPATH.
    from any_llm import messages

    try:
        messages(
            model=MODEL,
            provider="openai",
            api_base=f"http://127.0.0.1:{MOCK}/v1",
            api_key="sk-mock",
            max_tokens=64,
            **kwargs,
        )
        status = 200
        err = ""
    except Exception as e:
        status = 0
        err = f"{type(e).__name__}: {e}"
    time.sleep(0.08)
    after = last_caps(1)
    cap = after[0] if after and after != before else (after[0] if after else None)
    body = (cap or {}).get("body") if isinstance(cap, dict) else None
    blob = json.dumps(body, ensure_ascii=False) if body is not None else ""
    return {
        "label": label,
        "status": status,
        "error": err,
        "upstream_jsonl": json.dumps(cap, ensure_ascii=False) if cap else "",
        "upstream_keys": list(body.keys()) if isinstance(body, dict) else [],
        "has_thinkprobe": THINK in blob,
        "has_is_error": '"is_error"' in blob,
        "has_png": PNG in blob,
        "has_doc": DOC in blob,
        "has_image_url": "image_url" in blob or "image/" in blob,
        "has_schema": "json_schema" in blob or "response_format" in blob,
        "has_parallel": "parallel_tool_calls" in blob,
        "has_disable_parallel": "disable_parallel_tool_use" in blob,
        "has_stop": STOP in blob or '"stop"' in blob,
    }


def save_jsonl(name: str, rows: list[dict]) -> None:
    lines = [r["upstream_jsonl"] for r in rows if r.get("upstream_jsonl")]
    if lines:
        (OUT / name).write_text("\n".join(lines) + "\n")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cap_path = OUT / "cap.jsonl"
    if cap_path.exists():
        cap_path.unlink()

    canned = OUT / "canned-ok.json"
    if not canned.exists():
        canned.write_text(
            '{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'
        )

    spawn(
        [sys.executable, str(ROOT / "tools/mock_upstream.py"), str(MOCK), str(cap_path), str(canned)],
        "mock.log",
    )
    wait_port(MOCK)

    thinking_msgs = [
        {"role": "user", "content": "2+2?"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": THINK, "signature": "SIGNATURE_ABC123"},
                {"type": "text", "text": "4"},
            ],
        },
        {"role": "user", "content": "now 3+3"},
    ]
    tool_use = {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "lookup_city",
        "input": {"city": "x"},
    }

    cases = {
        "thinking_history": {"messages": thinking_msgs},
        "output_format": {
            "messages": [{"role": "user", "content": "hi"}],
            "output_format": {"type": "json_schema", "schema": SCHEMA},
        },
        "parallel": {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [TOOL],
            "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
        },
        "stop": {
            "messages": [{"role": "user", "content": "hi"}],
            "stop_sequences": [STOP],
        },
        "is_error": {
            "messages": [
                {"role": "user", "content": "run ls"},
                {"role": "assistant", "content": [tool_use]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "is_error": True,
                            "content": "permission denied",
                        }
                    ],
                },
            ],
            "tools": [TOOL],
        },
        "tool_result_image": {
            "messages": [
                {"role": "user", "content": "screenshot"},
                {"role": "assistant", "content": [tool_use]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": [
                                {"type": "text", "text": "here it is:"},
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": PNG,
                                    },
                                },
                            ],
                        }
                    ],
                },
            ],
            "tools": [TOOL],
        },
        "tool_result_document": {
            "messages": [
                {"role": "user", "content": "read doc"},
                {"role": "assistant", "content": [tool_use]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": [
                                {"type": "text", "text": "result text"},
                                {
                                    "type": "document",
                                    "source": {
                                        "type": "text",
                                        "media_type": "text/plain",
                                        "data": DOC,
                                    },
                                },
                            ],
                        }
                    ],
                },
            ],
            "tools": [TOOL],
        },
        "user_document": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "summarize"},
                        {
                            "type": "document",
                            "source": {
                                "type": "text",
                                "media_type": "text/plain",
                                "data": DOC,
                            },
                        },
                    ],
                }
            ],
        },
        "cache_control": {
            "system": [
                {"type": "text", "text": "sys1"},
                {"type": "text", "text": "sys2", "cache_control": {"type": "ephemeral"}},
            ],
            "messages": [{"role": "user", "content": "hi"}],
        },
    }

    results: dict[str, list[dict]] = {}
    for name, kwargs in cases.items():
        rows = []
        for i in range(N):
            rows.append(capture_call(f"{name} {i+1}", **kwargs))
        results[name] = rows

    # OpenAI completion control: parallel_tool_calls and stop survive on same bridge.
    from any_llm import completion

    for i in range(N):
        before = last_caps(1)
        try:
            completion(
                model=MODEL,
                provider="openai",
                api_base=f"http://127.0.0.1:{MOCK}/v1",
                api_key="sk-mock",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=16,
                parallel_tool_calls=False,
                stop=[STOP],
            )
            st = 200
        except Exception as e:
            st = 0
        time.sleep(0.08)
        after = last_caps(1)
        cap = after[0] if after and after != before else (after[0] if after else None)
        body = (cap or {}).get("body") if isinstance(cap, dict) else None
        blob = json.dumps(body, ensure_ascii=False) if body is not None else ""
        results.setdefault("ctrl_completion", []).append(
            {
                "label": f"ctrl completion {i+1}",
                "status": st,
                "upstream_jsonl": json.dumps(cap, ensure_ascii=False) if cap else "",
                "has_parallel": "parallel_tool_calls" in blob,
                "has_stop": STOP in blob,
            }
        )

    kill_all()

    mapping = {
        "thinking_history": "al-thinking-history-upstream.jsonl",
        "output_format": "al-output-format-upstream.jsonl",
        "parallel": "al-parallel-upstream.jsonl",
        "stop": "al-stop-upstream.jsonl",
        "is_error": "al-is-error-upstream.jsonl",
        "tool_result_image": "al-toolresult-image-upstream.jsonl",
        "tool_result_document": "al-toolresult-document-upstream.jsonl",
        "user_document": "al-user-document-upstream.jsonl",
        "cache_control": "al-cache-control-upstream.jsonl",
        "ctrl_completion": "al-completion-control-upstream.jsonl",
    }
    for key, fname in mapping.items():
        save_jsonl(fname, results.get(key, []))

    scoreboard = {}
    for name, rows in results.items():
        if not rows:
            continue
        scoreboard[name] = {
            "n": len(rows),
            "think": sum(1 for r in rows if r.get("has_thinkprobe")),
            "is_error": sum(1 for r in rows if r.get("has_is_error")),
            "png": sum(1 for r in rows if r.get("has_png")),
            "doc": sum(1 for r in rows if r.get("has_doc")),
            "image_url": sum(1 for r in rows if r.get("has_image_url")),
            "schema": sum(1 for r in rows if r.get("has_schema")),
            "parallel": sum(1 for r in rows if r.get("has_parallel")),
            "disable_parallel": sum(1 for r in rows if r.get("has_disable_parallel")),
            "stop": sum(1 for r in rows if r.get("has_stop")),
        }
    (OUT / "scoreboard.json").write_text(json.dumps(scoreboard, indent=2) + "\n")
    (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(scoreboard, indent=2))


if __name__ == "__main__":
    # Prefer the project venv when present.
    venv_site = Path("/tmp/kairo-venv/lib/python3.12/site-packages")
    if venv_site.exists():
        sys.path.insert(0, str(venv_site))
    main()
