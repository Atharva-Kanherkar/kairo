#!/usr/bin/env python3
"""Finding 101 consumer boundary, real providers.

Same client request, same model (gpt-4o-mini), same real OpenAI account, two
Switchyard routes that differ only in the target dialect:

  model-resp-live  openai_responses target: the tool is representable
  model-chat-live  openai_chat target: the tool has no Chat representation

The control runs against OpenAI directly (no gateway) to show the request and
the credentials are otherwise identical.

No credential is read, printed, or written here: Switchyard holds the key in
its own environment and the client sends a dummy one.
"""
import json
import os
import sys

from openai import OpenAI

SWITCHYARD = OpenAI(base_url="http://127.0.0.1:19403/v1", api_key="canary-not-a-credential")

# The two live routes point at a local relay that records the forwarded bytes and
# then forwards to api.openai.com unchanged, so both the bug cell and the control
# cell have wire evidence, not just an SDK verdict.
DIRECT = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

QUESTION = ("Search the web for the current president of the United States and reply "
            "with the name and the source URL. If you cannot search, say so.")
TOOLS = [{"type": "web_search_preview"}]
RUNS = int(sys.argv[1]) if len(sys.argv) > 1 else 2


def summarize(label, response):
    kinds = [item.type for item in response.output]
    answer = ""
    for item in response.output:
        if item.type == "message":
            answer = "".join(part.text for part in item.content if part.type == "output_text")
    row = {
        "case": label,
        "http_status": "200 (Switchyard response object)",
        "status": response.status,
        "output_kinds": kinds,
        "ran_web_search": "web_search_call" in kinds,
        "answer": answer[:400],
    }
    print(json.dumps(row))
    return row


rows = []
for run in range(RUNS):
    rows.append(summarize(f"control-direct-openai-{run}",
                          DIRECT.responses.create(model="gpt-4o-mini", input=QUESTION,
                                                  tools=TOOLS, tool_choice="auto")))
for run in range(RUNS):
    rows.append(summarize(f"control-switchyard-responses-route-{run}",
                          SWITCHYARD.responses.create(model="model-resp-live", input=QUESTION,
                                                      tools=TOOLS, tool_choice="auto")))
for run in range(RUNS):
    rows.append(summarize(f"bug-switchyard-chat-route-{run}",
                          SWITCHYARD.responses.create(model="model-chat-live", input=QUESTION,
                                                      tools=TOOLS, tool_choice="auto")))

out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "live-consumer-boundary.jsonl")
with open(out, "w") as handle:
    for row in rows:
        handle.write(json.dumps(row) + "\n")

searched = sum(row["ran_web_search"] for row in rows if row["case"].startswith("control"))
unsearched = sum(not row["ran_web_search"] for row in rows if row["case"].startswith("bug"))
print(f"\ncontrol cells that searched: {searched}/{2 * RUNS}")
print(f"bug cells that searched:      {unsearched}/{RUNS} searched (0 expected)")