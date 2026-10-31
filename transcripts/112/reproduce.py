#!/usr/bin/env python3
"""Reproduce finding 112 and rebuild the evidence matrix.

Prerequisites:
  * `npx wrangler dev --port 8799` running in transcripts/112/rig on an account
    with Workers AI access (`wrangler login`). The AI binding always calls the
    real hosted service, even in local dev.
  * OPENROUTER_API_KEY in the environment for the direct-provider rung. The key
    is read from the environment and never written to disk.

Usage:
  python3 reproduce.py            # run every cell, then summarize
  python3 reproduce.py --summary  # rebuild matrix.md from the saved cells only

Each run is saved as cells/<cell>/run-NN/{request.json|request.http,
response.http, meta.json}. Only the OpenRouter Authorization header is redacted,
inline, as [REDACTED:OPENROUTER_API_KEY].
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CELLS = HERE / "cells"
RIG = "http://127.0.0.1:8799"
MODEL = "@cf/google/gemma-4-26b-a4b-it"
OPENROUTER_MODEL = "google/gemma-4-26b-a4b-it"

PARALLEL_PROMPT = (
    "Get the weather for Paris and for Tokyo. Call the get_weather tool once per "
    "city, in parallel, in this single turn."
)
SINGLE_PROMPT = "What's the weather in Paris? Use the get_weather tool."
AGENT_PROMPT = PARALLEL_PROMPT + " Then tell me which city is warmer."

TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def post(url: str, body: dict, headers: dict | None = None, timeout: int = 300):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers.items()), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()), e.read().decode()


def http_text(status: int, headers: dict, body: str) -> str:
    head = "".join(f"{k}: {v}\n" for k, v in sorted(headers.items()))
    return f"HTTP/1.1 {status}\n{head}\n{body}"


def chat_inputs(prompt: str, stream: bool) -> dict:
    inputs = {
        "messages": [{"role": "user", "content": prompt}],
        "tools": [TOOL],
        "max_tokens": 400,
        "temperature": 0,
    }
    if stream:
        inputs["stream"] = True
    return inputs


# ---------------------------------------------------------------- analysis


def sse_payloads(body: str) -> list[dict]:
    out = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            out.append(json.loads(data))
        except json.JSONDecodeError:
            pass
    return out


def single_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except (json.JSONDecodeError, TypeError):
        return False


def analyze_body(body: str, stream: bool) -> dict:
    """Served model, tool calls as a client sees them, and whether each call's
    arguments are exactly one JSON object."""
    served = None
    calls: list[dict] = []
    if stream:
        by_index: dict[int, dict] = {}
        ids_on_index: dict[int, list[str]] = {}
        for chunk in sse_payloads(body):
            served = served or chunk.get("model")
            choice = (chunk.get("choices") or [{}])[0]
            for tc in (choice.get("delta") or {}).get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = by_index.setdefault(idx, {"name": "", "arguments": ""})
                if tc.get("id"):
                    ids_on_index.setdefault(idx, []).append(tc["id"])
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = slot["name"] or fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
        calls = [by_index[k] for k in sorted(by_index)]
        reused = {k: len(v) for k, v in ids_on_index.items() if len(set(v)) > 1}
    else:
        try:
            j = json.loads(body)
        except json.JSONDecodeError:
            return {"served_model": None, "verdict": "unparseable"}
        served = j.get("model")
        msg = (j.get("choices") or [{}])[0].get("message") or {}
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            calls.append({"name": fn.get("name"), "arguments": fn.get("arguments")})
        reused = {}
    valid = [single_json_object(c["arguments"]) for c in calls]
    if not calls:
        verdict = "no-tool-call"
    elif reused or not all(valid):
        verdict = "broken"
    else:
        verdict = "ok"
    return {
        "served_model": served,
        "tool_calls_seen_by_client": len(calls),
        "arguments": [c["arguments"] for c in calls],
        "all_arguments_single_json_object": all(valid) if calls else None,
        "index_reused_by_distinct_ids": reused,
        "verdict": verdict,
    }


# ---------------------------------------------------------------- cells


def run_binding_cell(name: str, prompt: str, stream: bool, n: int) -> None:
    for i in range(1, n + 1):
        d = CELLS / name / f"run-{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        request = {
            "entry_point": "env.AI.run (Workers binding, no gateway)",
            "model": MODEL,
            "inputs": chat_inputs(prompt, stream),
            "options": {"returnRawResponse": True},
        }
        start = now()
        status, _, text = post(f"{RIG}/raw", {"model": MODEL, "inputs": request["inputs"]})
        end = now()
        (d / "request.json").write_text(json.dumps(request, indent=2) + "\n")
        if status != 200:
            (d / "rig-error.txt").write_text(text)
            meta = {"utc_start": start, "utc_end": end, "verdict": "rig-error"}
        else:
            env = json.loads(text)
            (d / "response.http").write_text(http_text(env["status"], env["headers"], env["body"]))
            meta = {"utc_start": start, "utc_end": end, "status": env["status"]}
            meta.update(analyze_body(env["body"], stream))
        (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        print(f"{name} run-{i:02d} {meta.get('served_model')} {meta.get('verdict')}", flush=True)


def run_openrouter_cell(name: str, stream: bool, n: int) -> None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print(f"skip {name}: OPENROUTER_API_KEY not set", file=sys.stderr)
        return
    url = "https://openrouter.ai/api/v1/chat/completions"
    for i in range(1, n + 1):
        d = CELLS / name / f"run-{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        body = {"model": OPENROUTER_MODEL, **chat_inputs(PARALLEL_PROMPT, stream)}
        start = now()
        status, headers, text = post(url, body, {"authorization": f"Bearer {key}"})
        end = now()
        (d / "request.http").write_text(
            f"POST {url}\nauthorization: Bearer [REDACTED:OPENROUTER_API_KEY]\n"
            f"content-type: application/json\n\n{json.dumps(body, indent=2)}\n"
        )
        headers = {
            k: ("[REDACTED:set-cookie]" if k.lower() == "set-cookie" else v)
            for k, v in headers.items()
        }
        (d / "response.http").write_text(http_text(status, headers, text))
        meta = {"utc_start": start, "utc_end": end, "status": status}
        meta.update(analyze_body(text, stream))
        provider = None
        for chunk in sse_payloads(text) if stream else [json.loads(text)] if status == 200 else []:
            provider = provider or chunk.get("provider")
        meta["openrouter_provider"] = provider
        (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        print(f"{name} run-{i:02d} {provider} {meta.get('verdict')}", flush=True)


TENANT_HEADERS = ("cf-aig-log-id", "cf-aig-event-id", "cf-aig-trace-id", "cf-aig-request-id")


def redact_tenant_ids(out: dict) -> dict:
    """AI Gateway log, event, trace and request ids are scoped to the account's
    gateway, so they are replaced inline. Everything else is kept verbatim."""
    for call in out.get("calls", []):
        headers = call.get("headers") or {}
        for h in TENANT_HEADERS:
            if h in headers:
                headers[h] = f"[REDACTED:{h}]"
    return out


def run_agent_cell(name: str, n: int) -> None:
    for i in range(1, n + 1):
        d = CELLS / name / f"run-{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        request = {"model": MODEL, "messages": [{"role": "user", "content": AGENT_PROMPT}], "maxSteps": 5}
        start = now()
        status, _, text = post(f"{RIG}/agent", request, timeout=600)
        end = now()
        (d / "request.json").write_text(json.dumps(request, indent=2) + "\n")
        out = redact_tenant_ids(json.loads(text)) if status == 200 else {"rig_error": text}
        (d / "agent.json").write_text(json.dumps(out, indent=2) + "\n")
        served = []
        for call in out.get("calls", []):
            served.append(analyze_body(call.get("body") or "", False).get("served_model"))
        res = out.get("result", {})
        meta = {
            "utc_start": start,
            "utc_end": end,
            "served_model_per_step": served,
            "steps": len(res.get("steps", [])),
            "invalid_tool_calls": sum(
                1 for s in res.get("steps", []) for c in s["toolCalls"] if c["invalid"]
            ),
            "tool_executions": out.get("executions"),
            "final_text_empty": not (res.get("text") or "").strip(),
            "final_text": res.get("text"),
        }
        (d / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        print(f"{name} run-{i:02d} {served} invalid={meta['invalid_tool_calls']} empty={meta['final_text_empty']}", flush=True)


# ---------------------------------------------------------------- summary


def backend(served: str | None) -> str:
    if not served:
        return "unknown"
    return "external" if served.endswith("-external") else "native"


def summarize() -> str:
    rows = []
    window = []
    for cell in sorted(p for p in CELLS.iterdir() if p.is_dir()):
        metas = [json.loads((r / "meta.json").read_text()) for r in sorted(cell.iterdir())]
        for m in metas:
            window += [m.get("utc_start"), m.get("utc_end")]
        if cell.name.startswith("consumer"):
            ext_first = [m for m in metas if m["served_model_per_step"] and backend(m["served_model_per_step"][0]) == "external"]
            nat_first = [m for m in metas if m["served_model_per_step"] and backend(m["served_model_per_step"][0]) == "native"]
            rows.append(
                f"| {cell.name} | first step external | {len(ext_first)} | "
                f"{sum(1 for m in ext_first if m['invalid_tool_calls'] > 0)}/{len(ext_first)} had invalid calls; "
                f"{sum(1 for m in ext_first if m['final_text_empty'])}/{len(ext_first)} ended with an empty answer at the step limit; "
                f"{sum(1 for m in ext_first if set(m['tool_executions'] or []) != {'Paris', 'Tokyo'})}/{len(ext_first)} never ran both tools; "
                f"steps used {sorted(m['steps'] for m in ext_first)} |"
            )
            rows.append(
                f"| {cell.name} | first step native | {len(nat_first)} | "
                f"{sum(1 for m in nat_first if m['invalid_tool_calls'] > 0)}/{len(nat_first)} had invalid calls; "
                f"{sum(1 for m in nat_first if m['final_text_empty'])}/{len(nat_first)} ended with an empty answer; "
                f"steps used {sorted(m['steps'] for m in nat_first)} |"
            )
            continue
        groups: dict[str, list[dict]] = {}
        for m in metas:
            key = m.get("openrouter_provider") or backend(m.get("served_model"))
            groups.setdefault(key, []).append(m)
        for key, ms in sorted(groups.items()):
            broken = sum(1 for m in ms if m.get("verdict") == "broken")
            ok = sum(1 for m in ms if m.get("verdict") == "ok")
            other = len(ms) - broken - ok
            rows.append(
                f"| {cell.name} | {key} | {len(ms)} | broken {broken}/{len(ms)}, ok {ok}/{len(ms)}"
                + (f", other {other}" if other else "")
                + " |"
            )
    window = sorted(w for w in window if w)
    text = (
        "# Finding 112 matrix\n\n"
        f"UTC window: {window[0]} to {window[-1]}\n\n"
        "| Cell | Served by | Runs | Result |\n|---|---|---|---|\n" + "\n".join(rows) + "\n"
    )
    (HERE / "matrix.md").write_text(text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    if not args.summary:
        run_binding_cell("plain-parallel-nonstream", PARALLEL_PROMPT, False, 10)
        run_binding_cell("plain-parallel-stream", PARALLEL_PROMPT, True, 10)
        run_binding_cell("trigger-removed-single-nonstream", SINGLE_PROMPT, False, 5)
        run_binding_cell("trigger-removed-single-stream", SINGLE_PROMPT, True, 5)
        run_openrouter_cell("direct-openrouter-parallel-nonstream", False, 5)
        run_openrouter_cell("direct-openrouter-parallel-stream", True, 5)
        run_agent_cell("consumer-createai-agent", 6)
    print(summarize())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
