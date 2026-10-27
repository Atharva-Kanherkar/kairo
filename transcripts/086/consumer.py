#!/usr/bin/env python3
"""Consumer-boundary check for kairo issue 086.

Drives a running Bifrost gateway with the official OpenAI Python SDK and the
OpenAI Agents SDK, the way two services sharing one cache partition would.
Run it with a Python that has `openai` and `openai-agents` installed; the
parent runner (`reproduce.py --consumer-python`) starts the gateway and
passes its URL. Every SDK HTTP exchange is frozen under OUTPUT_DIR.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

try:
    import httpx2 as httpx  # openai>=3 ships its HTTP layer as httpx2
except ImportError:  # pragma: no cover - older openai releases use httpx
    import httpx
import openai
from openai import AsyncOpenAI, OpenAI

import agents
from agents import Agent, OpenAIResponsesModel, Runner, set_tracing_disabled

MODEL = "openai/gpt-4o-mini"
CLIENT_KEY = "sk-kairo-086-unused-client-value"
PROMPT = "KAIRO_086 cell=%s run=%s Reply with the capital of France."
CELLS = [
    "consumer_agents_after_chat",
    "consumer_chat_after_agents",
    "consumer_responses_stream_after_chat_stream",
    "consumer_chat_stream_after_responses_stream",
    "consumer_control_agents_alone",
    "consumer_control_agents_after_chat_no_cache_key",
]


class Recorder:
    def __init__(self, directory):
        self.directory = directory
        self.stem = "unset"
        self.count = 0

    def begin(self, stem):
        self.stem = stem
        self.count = 0

    def _path(self, side):
        return self.directory / ("%s-%02d-sdk-%s.http" % (self.stem, self.count, side))

    @staticmethod
    def _headers(headers):
        lines = []
        for name, value in headers.items():
            if name.lower() == "authorization":
                value = "Bearer <UNUSED_CLIENT_KEY>"
            lines.append("%s: %s" % (name, value))
        return lines

    def request(self, request):
        self.count += 1
        lines = ["%s %s HTTP/1.1" % (request.method, request.url.raw_path.decode())]
        lines.extend(self._headers(request.headers))
        body = request.content.decode("utf-8", "replace")
        self._path("request").write_text("\r\n".join(lines) + "\r\n\r\n" + body)

    def response(self, response, body):
        lines = ["HTTP/1.1 %d %s" % (response.status_code, response.reason_phrase)]
        lines.extend(self._headers(response.headers))
        self._path("response").write_text("\r\n".join(lines) + "\r\n\r\n" + body.decode("utf-8", "replace"))


def sync_client(gateway, recorder, cache_key):
    def on_request(request):
        recorder.request(request)

    def on_response(response):
        recorder.response(response, response.read())

    headers = {"x-bf-cache-key": cache_key} if cache_key else {}
    http = httpx.Client(event_hooks={"request": [on_request], "response": [on_response]})
    return OpenAI(base_url=gateway, api_key=CLIENT_KEY, default_headers=headers, max_retries=0, http_client=http)


def async_client(gateway, recorder, cache_key):
    async def on_request(request):
        recorder.request(request)

    async def on_response(response):
        recorder.response(response, await response.aread())

    headers = {"x-bf-cache-key": cache_key} if cache_key else {}
    http = httpx.AsyncClient(event_hooks={"request": [on_request], "response": [on_response]})
    return AsyncOpenAI(base_url=gateway, api_key=CLIENT_KEY, default_headers=headers, max_retries=0, http_client=http)


def outcome_of(callable_):
    try:
        value = callable_()
        return {"ok": True, "value": value}
    except Exception as error:  # noqa: BLE001 - the exception is the evidence
        return {"ok": False, "error": type(error).__name__, "message": str(error)[:300]}


async def async_outcome_of(awaitable):
    try:
        value = await awaitable
        return {"ok": True, "value": value}
    except Exception as error:  # noqa: BLE001 - the exception is the evidence
        return {"ok": False, "error": type(error).__name__, "message": str(error)[:300]}


def chat_text(client, prompt):
    completion = client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": prompt}])
    return completion.choices[0].message.content


def chat_stream_text(client, prompt):
    text, chunks = "", 0
    for chunk in client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": prompt}], stream=True):
        chunks += 1
        for choice in chunk.choices or []:
            text += choice.delta.content or ""
    return {"chunks": chunks, "text": text}


def responses_stream_text(client, prompt):
    with client.responses.stream(model=MODEL, input=prompt) as stream:
        return stream.get_final_response().output_text


async def agent_output(gateway, recorder, cache_key, prompt):
    client = async_client(gateway, recorder, cache_key)
    agent = Agent(name="kairo-086-agent", model=OpenAIResponsesModel(model=MODEL, openai_client=client))
    result = await Runner.run(agent, prompt)
    return result.final_output


async def run_cell(cell, run_id, gateway, recorder):
    prompt = PROMPT % (cell, run_id)
    key = "kairo-086-%s-%s" % (cell, run_id)
    steps = {}
    if cell == "consumer_agents_after_chat":
        recorder.begin("%s-%s-first" % (cell, run_id))
        steps["first_chat"] = outcome_of(lambda: chat_text(sync_client(gateway, recorder, key), prompt))
        await asyncio.sleep(1.0)
        recorder.begin("%s-%s-second" % (cell, run_id))
        steps["second_agent"] = await async_outcome_of(agent_output(gateway, recorder, key, prompt))
    elif cell == "consumer_chat_after_agents":
        recorder.begin("%s-%s-first" % (cell, run_id))
        steps["first_agent"] = await async_outcome_of(agent_output(gateway, recorder, key, prompt))
        await asyncio.sleep(1.0)
        recorder.begin("%s-%s-second" % (cell, run_id))
        steps["second_chat"] = outcome_of(lambda: chat_text(sync_client(gateway, recorder, key), prompt))
    elif cell == "consumer_responses_stream_after_chat_stream":
        recorder.begin("%s-%s-first" % (cell, run_id))
        steps["first_chat_stream"] = outcome_of(lambda: chat_stream_text(sync_client(gateway, recorder, key), prompt))
        await asyncio.sleep(1.0)
        recorder.begin("%s-%s-second" % (cell, run_id))
        steps["second_responses_stream"] = outcome_of(lambda: responses_stream_text(sync_client(gateway, recorder, key), prompt))
    elif cell == "consumer_chat_stream_after_responses_stream":
        recorder.begin("%s-%s-first" % (cell, run_id))
        steps["first_responses_stream"] = outcome_of(lambda: responses_stream_text(sync_client(gateway, recorder, key), prompt))
        await asyncio.sleep(1.0)
        recorder.begin("%s-%s-second" % (cell, run_id))
        steps["second_chat_stream"] = outcome_of(lambda: chat_stream_text(sync_client(gateway, recorder, key), prompt))
    elif cell == "consumer_control_agents_alone":
        recorder.begin("%s-%s-first" % (cell, run_id))
        steps["first_agent"] = await async_outcome_of(agent_output(gateway, recorder, key, prompt))
    elif cell == "consumer_control_agents_after_chat_no_cache_key":
        recorder.begin("%s-%s-first" % (cell, run_id))
        steps["first_chat"] = outcome_of(lambda: chat_text(sync_client(gateway, recorder, None), prompt))
        await asyncio.sleep(1.0)
        recorder.begin("%s-%s-second" % (cell, run_id))
        steps["second_agent"] = await async_outcome_of(agent_output(gateway, recorder, None, prompt))
    return steps


def final_step(steps):
    name = sorted(steps, key=lambda step_name: step_name.startswith("second"))[-1]
    return name, steps[name]


def healthy(step_name, step):
    """True when the final step returned the upstream answer for its own API."""
    if not step["ok"]:
        return False
    value = step["value"]
    if isinstance(value, dict):
        value = value.get("text", "")
    if not isinstance(value, str):
        return False
    wants_responses = "agent" in step_name or "responses" in step_name
    marker = "KAIRO_086_UPSTREAM_RESPONSES" if wants_responses else "KAIRO_086_UPSTREAM_CHAT"
    return marker in value


async def main(args):
    set_tracing_disabled(True)
    output = Path(args.output)
    sdk_dir = output / "sdk"
    sdk_dir.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(sdk_dir)
    results = {
        "sdk": {"openai": openai.__version__, "openai-agents": agents.__version__, "python": sys.version.split()[0]},
        "cells": {},
    }
    for cell in CELLS:
        cell_runs = []
        for iteration in range(1, args.runs + 1):
            run_id = "%02d" % iteration
            steps = await run_cell(cell, run_id, args.gateway, recorder)
            final_name, final = final_step(steps)
            cell_runs.append(
                {"final_step": final_name, "final_healthy": healthy(final_name, final), "run": run_id, "steps": steps}
            )
        results["cells"][cell] = {
            "final_healthy": sum(1 for item in cell_runs if item["final_healthy"]),
            "runs": cell_runs,
        }
    (output / "consumer-results.json").write_text(json.dumps(results, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps({cell: value["final_healthy"] for cell, value in results["cells"].items()}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runs", type=int, default=5)
    asyncio.run(main(parser.parse_args()))
