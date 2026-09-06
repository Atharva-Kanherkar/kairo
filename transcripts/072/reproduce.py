#!/usr/bin/env python3
"""Reproduction for issue 072: Bifrost forwards Anthropic tool_choice 'any'
to OpenAI backends as bare 'any' instead of mapping to 'required'.

In the Anthropic Messages API, forcing tool use is specified as:
    tool_choice: {"type": "any"}

In the OpenAI API (Responses API and Chat Completions), forcing tool use requires:
    tool_choice: "required"

OpenAI rejects tool_choice="any" with HTTP 400 Bad Request. Bifrost fails to
translate "any" to "required" when driving OpenAI backends, leaking the Anthropic
dialect string verbatim.

Usage:
    python3 transcripts/072/reproduce.py
    python3 transcripts/072/reproduce.py --output-dir transcripts/072
"""

import argparse
import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

DEFAULT_BIFROST_BIN = os.environ.get(
    "BIFROST_BIN",
    os.path.expanduser("~/Library/Caches/bifrost/v2.0.0/bin/bifrost-http-0"),
)
DEFAULT_GW_PORT = 8082
DEFAULT_UPSTREAM_PORT = 9912
MODEL = "mockoai/mimo-v2.5"
REQUIRED_RUNS = 5
READY_TIMEOUT = 30.0

ANT_HEADERS = {
    "content-type": "application/json",
    "anthropic-version": "2023-06-01",
}
TOOL_ANT = [
    {
        "name": "get_weather",
        "description": "Get current weather for a location.",
        "input_schema": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    }
]
TOOL_OAI = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a location.",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"],
            },
        },
    }
]
TOOL_RESPONSES = [
    {
        "type": "function",
        "name": "get_weather",
        "description": "Get current weather for a location.",
        "parameters": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    }
]


class ReproductionError(Exception):
    pass


def ensure_port_available(host: str, port: int) -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
    except OSError as e:
        raise ReproductionError(
            f"port {port} on {host} is in use ({e}); stop running processes first"
        ) from None
    finally:
        s.close()


class CaptureHandler(http.server.BaseHTTPRequestHandler):
    captured = []

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            resp = json.dumps(
                {"object": "list", "data": [{"id": "mimo-v2.5", "object": "model"}]}
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
        try:
            body = json.loads(raw)
        except Exception:
            body = raw

        CaptureHandler.captured.append(
            {
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": body,
            }
        )

        is_responses = self.path.rstrip("/").endswith("/responses")
        if is_responses:
            reply = {
                "id": "resp_072",
                "object": "response",
                "status": "completed",
                "model": "mimo-v2.5",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "Fetching weather."}],
                    },
                    {
                        "type": "function_call",
                        "id": "fc_1",
                        "call_id": "call_1",
                        "name": "get_weather",
                        "arguments": '{"location":"Tokyo"}',
                        "status": "completed",
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
        else:
            reply = {
                "id": "chatcmpl-072",
                "object": "chat.completion",
                "model": "mimo-v2.5",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Fetching weather.",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"location":"Tokyo"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

        data = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def post_json(url: str, payload: dict, headers: dict = None):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers or {"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8", "replace"))


def wait_for_ready(gw_url: str, proc: subprocess.Popen, timeout: float = READY_TIMEOUT):
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            raise ReproductionError(f"bifrost process exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(gw_url + "/v1/models", timeout=1) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(0.3)
    raise ReproductionError(f"bifrost at {gw_url} did not become ready within {timeout}s")


def run_reproduction(bifrost_bin: str, gw_port: int, upstream_port: int, output_dir: str):
    ensure_port_available("127.0.0.1", gw_port)
    ensure_port_available("127.0.0.1", upstream_port)

    if not os.path.isfile(bifrost_bin):
        raise ReproductionError(
            f"bifrost executable not found at {bifrost_bin}; set BIFROST_BIN"
        )

    temp_app_dir = tempfile.mkdtemp(prefix="bifrost_072_")
    upstream_server = http.server.ThreadingHTTPServer(("127.0.0.1", upstream_port), CaptureHandler)
    upstream_thread = threading.Thread(target=upstream_server.serve_forever, daemon=True)
    upstream_thread.start()

    bifrost_proc = None
    try:
        config_path = os.path.join(temp_app_dir, "config.json")
        config = {
            "$schema": "https://www.getbifrost.ai/schema",
            "config_store": {"enabled": False},
            "client": {"enable_logging": False, "initial_pool_size": 10},
            "providers": {
                "mockoai": {
                    "keys": [
                        {
                            "name": "k1",
                            "value": "sk-CANARY-MOCK-KEY-072",
                            "weight": 1,
                            "models": ["*"],
                        }
                    ],
                    "network_config": {"base_url": f"http://127.0.0.1:{upstream_port}"},
                    "custom_provider_config": {"base_provider_type": "openai"},
                }
            },
        }
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        gw_url = f"http://127.0.0.1:{gw_port}"
        bifrost_proc = subprocess.Popen(
            [bifrost_bin, "-app-dir", temp_app_dir, "-port", str(gw_port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        wait_for_ready(gw_url, bifrost_proc)

        results = {
            "runs": REQUIRED_RUNS,
            "violation": [],
            "control_responses": [],
            "control_chat": [],
            "control_auto": [],
        }

        last_violation_capture = None
        last_ctrl_responses_capture = None
        last_ctrl_chat_capture = None
        last_ctrl_auto_capture = None

        # 1. Violation: Anthropic tool_choice: {"type": "any"}
        for i in range(REQUIRED_RUNS):
            CaptureHandler.captured.clear()
            status, resp = post_json(
                f"{gw_url}/anthropic/v1/messages",
                {
                    "model": MODEL,
                    "max_tokens": 100,
                    "tools": TOOL_ANT,
                    "tool_choice": {"type": "any"},
                    "messages": [{"role": "user", "content": "weather in Tokyo?"}],
                },
                headers=ANT_HEADERS,
            )
            if status != 200:
                raise ReproductionError(f"Anthropic violation request failed: {status} {resp}")
            if not CaptureHandler.captured:
                raise ReproductionError("no upstream request was captured for violation")
            cap = CaptureHandler.captured[-1]
            last_violation_capture = cap
            fwd_body = cap["body"]
            tool_choice = fwd_body.get("tool_choice")
            results["violation"].append(
                {
                    "run": i + 1,
                    "path": cap["path"],
                    "tool_choice": tool_choice,
                    "is_leak": tool_choice == "any",
                }
            )

        # 2. Control 1: OpenAI Responses API tool_choice: "required"
        for i in range(REQUIRED_RUNS):
            CaptureHandler.captured.clear()
            status, resp = post_json(
                f"{gw_url}/v1/responses",
                {
                    "model": MODEL,
                    "max_output_tokens": 100,
                    "tools": TOOL_RESPONSES,
                    "tool_choice": "required",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "weather in Tokyo?"}],
                        }
                    ],
                },
            )
            if status != 200:
                raise ReproductionError(f"OpenAI responses control failed: {status} {resp}")
            if not CaptureHandler.captured:
                raise ReproductionError("no upstream request captured for control 1")
            cap = CaptureHandler.captured[-1]
            last_ctrl_responses_capture = cap
            tool_choice = cap["body"].get("tool_choice")
            results["control_responses"].append(
                {
                    "run": i + 1,
                    "path": cap["path"],
                    "tool_choice": tool_choice,
                    "conformant": tool_choice == "required",
                }
            )

        # 3. Control 2: OpenAI Chat Completions tool_choice: "required"
        for i in range(REQUIRED_RUNS):
            CaptureHandler.captured.clear()
            status, resp = post_json(
                f"{gw_url}/v1/chat/completions",
                {
                    "model": MODEL,
                    "max_tokens": 100,
                    "tools": TOOL_OAI,
                    "tool_choice": "required",
                    "messages": [{"role": "user", "content": "weather in Tokyo?"}],
                },
            )
            if status != 200:
                raise ReproductionError(f"OpenAI chat control failed: {status} {resp}")
            if not CaptureHandler.captured:
                raise ReproductionError("no upstream request captured for control 2")
            cap = CaptureHandler.captured[-1]
            last_ctrl_chat_capture = cap
            tool_choice = cap["body"].get("tool_choice")
            results["control_chat"].append(
                {
                    "run": i + 1,
                    "path": cap["path"],
                    "tool_choice": tool_choice,
                    "conformant": tool_choice == "required",
                }
            )

        # 4. Control 3: Anthropic tool_choice: {"type": "auto"}
        for i in range(REQUIRED_RUNS):
            CaptureHandler.captured.clear()
            status, resp = post_json(
                f"{gw_url}/anthropic/v1/messages",
                {
                    "model": MODEL,
                    "max_tokens": 100,
                    "tools": TOOL_ANT,
                    "tool_choice": {"type": "auto"},
                    "messages": [{"role": "user", "content": "weather in Tokyo?"}],
                },
                headers=ANT_HEADERS,
            )
            if status != 200:
                raise ReproductionError(f"Anthropic auto control failed: {status} {resp}")
            if not CaptureHandler.captured:
                raise ReproductionError("no upstream request captured for control 3")
            cap = CaptureHandler.captured[-1]
            last_ctrl_auto_capture = cap
            tool_choice = cap["body"].get("tool_choice")
            results["control_auto"].append(
                {
                    "run": i + 1,
                    "path": cap["path"],
                    "tool_choice": tool_choice,
                    "conformant": tool_choice == "auto",
                }
            )

        # Strict 5/5 validation
        violation_leaks = sum(1 for r in results["violation"] if r["is_leak"])
        if violation_leaks != REQUIRED_RUNS:
            raise ReproductionError(
                f"violation non-deterministic: {violation_leaks}/{REQUIRED_RUNS} runs leaked 'any'"
            )

        ctrl_responses_ok = sum(1 for r in results["control_responses"] if r["conformant"])
        if ctrl_responses_ok != REQUIRED_RUNS:
            raise ReproductionError(
                f"control responses non-deterministic: {ctrl_responses_ok}/{REQUIRED_RUNS} ok"
            )

        ctrl_chat_ok = sum(1 for r in results["control_chat"] if r["conformant"])
        if ctrl_chat_ok != REQUIRED_RUNS:
            raise ReproductionError(
                f"control chat non-deterministic: {ctrl_chat_ok}/{REQUIRED_RUNS} ok"
            )

        ctrl_auto_ok = sum(1 for r in results["control_auto"] if r["conformant"])
        if ctrl_auto_ok != REQUIRED_RUNS:
            raise ReproductionError(
                f"control auto non-deterministic: {ctrl_auto_ok}/{REQUIRED_RUNS} ok"
            )

        # Write fixtures atomically to output_dir
        os.makedirs(output_dir, exist_ok=True)

        def write_fixture(filename: str, content: str):
            tmp = os.path.join(output_dir, f".{filename}.tmp")
            dest = os.path.join(output_dir, filename)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, dest)

        write_fixture(
            "upstream-request.jsonl",
            json.dumps({"path": last_violation_capture["path"], "body": last_violation_capture["body"]})
            + "\n",
        )
        write_fixture(
            "control-responses-upstream.jsonl",
            json.dumps(
                {"path": last_ctrl_responses_capture["path"], "body": last_ctrl_responses_capture["body"]}
            )
            + "\n",
        )
        write_fixture(
            "control-chat-upstream.jsonl",
            json.dumps({"path": last_ctrl_chat_capture["path"], "body": last_ctrl_chat_capture["body"]})
            + "\n",
        )
        write_fixture(
            "control-anthropic-auto-upstream.jsonl",
            json.dumps({"path": last_ctrl_auto_capture["path"], "body": last_ctrl_auto_capture["body"]})
            + "\n",
        )
        write_fixture("client-results.json", json.dumps(results, indent=2) + "\n")

        print("Reproduction successful! 5/5 runs verified.")
        print(f"Violation: 'tool_choice': 'any' forwarded 5/5 to {last_violation_capture['path']}")
        print(f"Control 1 (Responses): 'tool_choice': 'required' forwarded 5/5")
        print(f"Control 2 (Chat): 'tool_choice': 'required' forwarded 5/5")
        print(f"Control 3 (Anthropic auto): 'tool_choice': 'auto' forwarded 5/5")
        print(f"Fixtures written to {output_dir}")

    finally:
        if bifrost_proc is not None:
            try:
                bifrost_proc.terminate()
                bifrost_proc.wait(timeout=5)
            except Exception:
                bifrost_proc.kill()
        try:
            upstream_server.shutdown()
            upstream_server.server_close()
        except Exception:
            pass
        shutil.rmtree(temp_app_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Reproduction for issue 072")
    parser.add_argument(
        "--output-dir",
        default=os.path.abspath(os.path.join(os.path.dirname(__file__))),
        help="Directory to write fixtures into",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_GW_PORT, help="Bifrost port")
    parser.add_argument(
        "--upstream-port", type=int, default=DEFAULT_UPSTREAM_PORT, help="Mock upstream port"
    )
    parser.add_argument(
        "--bifrost-bin", default=DEFAULT_BIFROST_BIN, help="Path to bifrost binary"
    )
    args = parser.parse_args()

    run_reproduction(args.bifrost_bin, args.port, args.upstream_port, args.output_dir)


if __name__ == "__main__":
    main()
