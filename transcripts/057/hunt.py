#!/usr/bin/env python3
"""057 any-llm SDK hunt: Messages bridge -> OpenAI-compatible mock.

Requires Python 3.11+ and any-llm-sdk[openai]. Run with the venv interpreter:

  /tmp/kairo-venv/bin/python3 transcripts/057/hunt.py

The rig spawns its own mock on MOCK (9996). Do not start a second listener on
that port. No API keys required.
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

VIOLATION_CASES = (
    "thinking_history",
    "output_format_wrong_shape",
    "parallel",
    "is_error",
    "tool_result_image",
    "tool_result_document",
)


def require_any_llm() -> None:
    try:
        import any_llm  # noqa: F401
    except ImportError as e:
        sys.stderr.write(
            "any-llm-sdk is required (Python 3.11+). Example:\n"
            "  python3 -m venv /tmp/kairo-venv\n"
            "  /tmp/kairo-venv/bin/pip install 'any-llm-sdk[openai]'\n"
            "  /tmp/kairo-venv/bin/python3 transcripts/057/hunt.py\n"
        )
        raise SystemExit(1) from e


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


def cap_line_count() -> int:
    path = OUT / "cap.jsonl"
    if not path.exists():
        return 0
    return sum(1 for ln in path.read_text().splitlines() if ln.strip())


def last_cap() -> dict | None:
    path = OUT / "cap.jsonl"
    if not path.exists():
        return None
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def capture_call(label: str, **kwargs) -> dict:
    before_count = cap_line_count()
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
    after_count = cap_line_count()
    reached = after_count > before_count
    cap = last_cap() if reached else None
    body = (cap or {}).get("body") if isinstance(cap, dict) else None
    blob = json.dumps(body, ensure_ascii=False) if body is not None else ""
    schema_props = (
        ((body or {}).get("response_format") or {})
        .get("json_schema", {})
        .get("schema", {})
        .get("properties")
        if isinstance(body, dict)
        else None
    )
    return {
        "label": label,
        "status": status,
        "error": err,
        "reached_upstream": reached,
        "upstream_jsonl": json.dumps(cap, ensure_ascii=False) if cap else "",
        "upstream_keys": list(body.keys()) if isinstance(body, dict) else [],
        "has_thinkprobe": THINK in blob,
        "has_is_error": '"is_error"' in blob,
        "has_png": PNG in blob,
        "has_doc": DOC in blob,
        "has_image_url": "image_url" in blob,
        "has_schema_city": isinstance(schema_props, dict) and "city" in schema_props,
        "has_parallel_false": '"parallel_tool_calls": false' in blob
        or '"parallel_tool_calls":false' in blob,
        "has_stop_probe": STOP in blob,
    }


def capture_completion(label: str, **kwargs) -> dict:
    before_count = cap_line_count()
    from any_llm import completion

    try:
        completion(
            model=MODEL,
            provider="openai",
            api_base=f"http://127.0.0.1:{MOCK}/v1",
            api_key="sk-mock",
            max_tokens=16,
            **kwargs,
        )
        status = 200
        err = ""
    except Exception as e:
        status = 0
        err = f"{type(e).__name__}: {e}"
    time.sleep(0.08)
    after_count = cap_line_count()
    reached = after_count > before_count
    cap = last_cap() if reached else None
    body = (cap or {}).get("body") if isinstance(cap, dict) else None
    blob = json.dumps(body, ensure_ascii=False) if body is not None else ""
    return {
        "label": label,
        "status": status,
        "error": err,
        "reached_upstream": reached,
        "upstream_jsonl": json.dumps(cap, ensure_ascii=False) if cap else "",
        "has_parallel_false": '"parallel_tool_calls": false' in blob
        or '"parallel_tool_calls":false' in blob,
        "has_stop_probe": STOP in blob,
    }


def save_jsonl(name: str, rows: list[dict]) -> None:
    lines = [
        r["upstream_jsonl"]
        for r in rows
        if r.get("reached_upstream") and r.get("upstream_jsonl")
    ]
    if not lines:
        raise RuntimeError(f"no upstream captures for {name}")
    (OUT / name).write_text("\n".join(lines) + "\n")


def main() -> None:
    require_any_llm()
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
        # Natural mistake: top-level json_schema shape (not Anthropic output_config).
        "output_format_wrong_shape": {
            "messages": [{"role": "user", "content": "hi"}],
            "output_format": {"type": "json_schema", "schema": SCHEMA},
        },
        # Documented Anthropic output_config dict form.
        "output_format_control": {
            "messages": [{"role": "user", "content": "hi"}],
            "output_format": {
                "format": {
                    "type": "json_schema",
                    "schema": {"title": "CityOut", **SCHEMA},
                }
            },
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
        "user_image": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what is this"},
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
        rows = [capture_call(f"{name} {i+1}", **kwargs) for i in range(N)]
        results[name] = rows

    results["ctrl_completion"] = [
        capture_completion(
            f"ctrl completion {i+1}",
            messages=[{"role": "user", "content": "hi"}],
            parallel_tool_calls=False,
            stop=[STOP],
        )
        for i in range(N)
    ]

    kill_all()

    mapping = {
        "thinking_history": "al-thinking-history-upstream.jsonl",
        "output_format_wrong_shape": "al-output-format-empty-schema-upstream.jsonl",
        "output_format_control": "al-output-format-control-upstream.jsonl",
        "parallel": "al-parallel-upstream.jsonl",
        "stop": "al-stop-upstream.jsonl",
        "is_error": "al-is-error-upstream.jsonl",
        "tool_result_image": "al-toolresult-image-upstream.jsonl",
        "tool_result_document": "al-toolresult-document-upstream.jsonl",
        "user_document": "al-user-document-upstream.jsonl",
        "user_image": "al-user-image-upstream.jsonl",
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
            "reached_upstream": sum(1 for r in rows if r.get("reached_upstream")),
            "status_ok": sum(1 for r in rows if r.get("status") == 200),
            "think": sum(1 for r in rows if r.get("has_thinkprobe")),
            "is_error": sum(1 for r in rows if r.get("has_is_error")),
            "png": sum(1 for r in rows if r.get("has_png")),
            "doc": sum(1 for r in rows if r.get("has_doc")),
            "image_url": sum(1 for r in rows if r.get("has_image_url")),
            "schema_city": sum(1 for r in rows if r.get("has_schema_city")),
            "parallel_false": sum(1 for r in rows if r.get("has_parallel_false")),
            "stop_probe": sum(1 for r in rows if r.get("has_stop_probe")),
        }

    for name in VIOLATION_CASES:
        sb = scoreboard.get(name, {})
        if sb.get("reached_upstream") != N:
            raise RuntimeError(f"{name}: expected {N}/{N} upstream captures, got {sb}")

    (OUT / "scoreboard.json").write_text(json.dumps(scoreboard, indent=2) + "\n")
    (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(scoreboard, indent=2))


if __name__ == "__main__":
    main()
