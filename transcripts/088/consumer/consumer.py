#!/usr/bin/env python3
"""Consumer leg for the agentgateway OpenAI-ingress findings.

Drives the real openai-python SDK against the running rig and reports what the
SDK actually surfaces, not what the wire appears to contain.

Routes (same rig, one egress provider each):
  :4003  OpenAI ingress -> Anthropic provider   (the conversion under test)
  :4002  OpenAI ingress -> OpenAI provider      (passthrough control)

The caller stages the upstream capture with `canned.pointer`, so a `.sse`
fixture pairs with stream=True and a `.json` fixture with stream=False.

Usage: consumer.py conv|ctrl
"""
import json
import os
import sys
import traceback

from openai import OpenAI

CONV = int(os.environ.get("BASE_CONV", "4003"))
CTRL = int(os.environ.get("BASE_CTRL", "4002"))

BODY = {
    "model": "captured-model",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "hi"}],
}

TOOLS = {
    "model": "captured-model",
    "max_tokens": 64,
    "messages": [{"role": "user", "content": "hi"}],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "w",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            },
        }
    ],
}


def usage_repr(usage):
    if usage is None:
        return "None"
    d = usage.prompt_tokens_details
    return json.dumps(
        {
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "prompt_tokens_details": None
            if d is None
            else {
                "cached_tokens": d.cached_tokens,
                "cache_write_tokens": getattr(d, "cache_write_tokens", None),
            },
        },
        sort_keys=True,
    )


def report_raised(e):
    tb = traceback.extract_tb(e.__traceback__)
    f = tb[-1]
    print(f"  {'SDK RAISED':<32} {type(e).__name__}: {e}".rstrip())
    print(f"  {'raised at':<32} {f.filename.split('site-packages/')[-1]}:{f.lineno}")
    print(f"  {'source line':<32} {(f.line or '').strip()}")
    return type(e).__name__


def run_stream(client, label, body):
    print(f"[{label}] stream=True, raw chunk iteration")
    n = usage_chunks = 0
    tools = {}
    for raw in client.chat.completions.create(**body, stream=True):
        n += 1
        if raw.usage is not None:
            usage_chunks += 1
            print(f"  raw chunk[{n}].usage{'':<13} {usage_repr(raw.usage)}")
        if not raw.choices:
            continue
        for tc in raw.choices[0].delta.tool_calls or []:
            slot = tools.setdefault(tc.index, {"id": None, "name": "", "args": ""})
            if tc.id:
                slot["id"] = tc.id
            if tc.function and tc.function.name:
                slot["name"] = tc.function.name
            if tc.function and tc.function.arguments:
                slot["args"] += tc.function.arguments
    print(f"  {'chunks received':<32} {n}")
    print(f"  {'chunks carrying usage':<32} {usage_chunks}")
    if tools:
        print(f"  {'reassembled tool calls':<32} {json.dumps(tools, sort_keys=True)}")


def run_accumulated(client, label, body):
    print(f"[{label}] stream=True, client.chat.completions.stream()")
    try:
        with client.chat.completions.stream(**body) as stream:
            final = stream.get_final_completion()
    except BaseException as e:
        report_raised(e)
        return None
    print(f"  {'final.usage':<32} {usage_repr(final.usage)}")
    tc = final.choices[0].message.tool_calls
    if tc:
        print(f"  {'final tool_calls':<32} {[t.function.name for t in tc]}")
        print(f"  {'final arguments':<32} {[t.function.arguments for t in tc]}")
    return final


def run_nonstream(client, label, body):
    print(f"[{label}] stream=False")
    try:
        r = client.chat.completions.create(**body, stream=False)
    except BaseException as e:
        report_raised(e)
        return None
    print(f"  {'response.usage':<32} {usage_repr(r.usage)}")
    m = r.choices[0].message
    if m.tool_calls:
        print(f"  {'tool_calls':<32} {[t.function.name for t in m.tool_calls]}")
    print(f"  {'finish_reason':<32} {r.choices[0].finish_reason}")
    return r


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "conv"
    port = CONV if which == "conv" else CTRL
    label = f"{which} :{port}"
    client = OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="test-key")
    body = TOOLS if os.environ.get("WITH_TOOLS") == "1" else BODY
    print("=" * 74)
    print(label, " body keys:", sorted(body))
    print("=" * 74)
    run_stream(client, label, body)
    run_accumulated(client, label, body)
    if os.environ.get("ALSO_NONSTREAM") == "1":
        run_nonstream(client, label, body)
    print()
