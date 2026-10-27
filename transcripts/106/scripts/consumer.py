#!/usr/bin/env python3
"""Consumer-boundary check: one agent tool-call turn through the official SDKs.

The agent asks the model to call write_file, then executes the call. A worker
is SIGKILLed after --kill-secs. Outcome is either the executed tool call or the
exception the SDK raised. Run with --kill-secs 0 for the no-failure control.
"""
import argparse, json, os, signal, sys, threading, time
sys.path.insert(0, "/work")
from probe import kill_serving, TOOL_PROMPT, TOOLS, MODEL


def run_openai():
    from openai import OpenAI
    c = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="x", max_retries=0)
    stream = c.chat.completions.create(model=MODEL, stream=True, temperature=0, max_tokens=1500,
                                       tools=TOOLS, messages=[{"role": "user", "content": TOOL_PROMPT}])
    calls = {}
    for ch in stream:
        for choice in ch.choices:
            for tc in choice.delta.tool_calls or []:
                d = calls.setdefault(tc.index, {"name": "", "args": ""})
                if tc.function and tc.function.name: d["name"] += tc.function.name
                if tc.function and tc.function.arguments: d["args"] += tc.function.arguments
    return list(calls.values())


def run_anthropic():
    import anthropic
    c = anthropic.Anthropic(base_url="http://127.0.0.1:8000", api_key="x", max_retries=0)
    tools = [{"name": "write_file", "description": "Write text content to a file.",
              "input_schema": TOOLS[0]["function"]["parameters"]}]
    with c.messages.stream(model=MODEL, max_tokens=1500, extra_body={"temperature": 0}, tools=tools,
                           messages=[{"role": "user", "content": TOOL_PROMPT}]) as s:
        msg = s.get_final_message()
    return [{"name": b.name, "args": json.dumps(b.input)} for b in msg.content if b.type == "tool_use"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sdk", choices=["openai", "anthropic"]); ap.add_argument("--kill-secs", type=float, default=0)
    a = ap.parse_args()
    killed = {}
    if a.kill_secs > 0:
        def k():
            time.sleep(a.kill_secs); killed["w"] = kill_serving(signal.SIGKILL)
        threading.Thread(target=k, daemon=True).start()
    t0 = time.time()
    try:
        calls = run_openai() if a.sdk == "openai" else run_anthropic()
        executed = []
        for cl in calls:
            args = json.loads(cl["args"])
            nums = args["content"].split()
            executed.append({"tool": cl["name"], "path": args["path"], "n": len(nums),
                             "ok": nums == [str(i) for i in range(1, 201)]})
        out = {"outcome": "tool_executed" if executed else "no_tool_call", "executed": executed}
    except Exception as e:
        out = {"outcome": "sdk_exception", "type": type(e).__module__ + "." + type(e).__name__,
               "message": str(e)[:300]}
    out.update({"sdk": a.sdk, "kill_secs": a.kill_secs, "killed": killed.get("w"),
                "secs": round(time.time() - t0, 2)})
    print(json.dumps(out))


if __name__ == "__main__":
    main()
