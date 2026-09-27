#!/usr/bin/env python3
"""Drive openai-python against each isolated SSE variant and report the outcome.

Usage: variants.py PORT
"""
import json
import sys
import traceback

import urllib.request

from openai import OpenAI

PORT = int(sys.argv[1])
VARIANTS = ["agw_verbatim", "type_omitted", "type_repeated", "type_null_only"]


def set_variant(name):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/__variant",
        data=json.dumps({"variant": name}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req).read()


def main():
    client = OpenAI(base_url=f"http://127.0.0.1:{PORT}/v1", api_key="k")
    body = {
        "model": "captured-model",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{
            "type": "function",
            "function": {"name": "get_weather", "description": "w",
                         "parameters": {"type": "object",
                                        "properties": {"city": {"type": "string"}}}},
        }],
    }
    print(f"{'variant':<18} {'client.chat.completions.stream()':<44} detail")
    print("-" * 110)
    for name in VARIANTS:
        set_variant(name)
        try:
            with client.chat.completions.stream(**body) as s:
                final = s.get_final_completion()
            u = final.usage
            detail = (f"OK  prompt={u.prompt_tokens} total={u.total_tokens} "
                      f"tool={[t.function.name for t in final.choices[0].message.tool_calls]}")
            outcome = "OK"
        except BaseException as e:
            f = traceback.extract_tb(e.__traceback__)[-1]
            outcome = "RAISED"
            detail = f"{type(e).__name__} at {f.filename.split('site-packages/')[-1]}:{f.lineno}"
        print(f"{name:<18} {outcome:<44} {detail}")


if __name__ == "__main__":
    main()
