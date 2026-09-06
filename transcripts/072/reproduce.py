#!/usr/bin/env python3
"""Record issue 072 against real Bifrost; --live uses OpenAI, never a mock error.

Raw UTF-8 HTTP bodies are retained verbatim in *_raw JSON strings. Authentication
headers are never captured. Live credentials stay in the relay process only.
"""

import argparse
import copy
import hashlib
import http.client
import http.server
import importlib.metadata
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone

REVISION = "e4a30d6041c0446603aea615bc5da340dac001b1"
RELEASE = "transports/v2.0.0"
MODEL = "gpt-4o"
RUNS = 5
DEFAULT_BINARY = os.path.expanduser(
    "~/Library/Caches/bifrost/v2.0.0/bin/bifrost-http-0"
)
SAFE_HEADERS = {"content-type", "date", "x-request-id"}
TOOL = {
    "name": "get_weather",
    "description": "Get current weather for a location.",
    "input_schema": {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
    },
}


class ReproductionError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReproductionError(message)


def safe_raw(raw, secret=""):
    text = raw.decode("utf-8")
    require(
        not secret or secret not in text, "credential appeared in body; capture refused"
    )
    require(
        not re.search(r"sk-[A-Za-z0-9_-]{8,}", text),
        "possible credential in body; capture refused",
    )
    return text


def safe_headers(headers):
    return {k.lower(): v for k, v in headers if k.lower() in SAFE_HEADERS}


def ensure_port_available(host, port):
    with socket.socket() as sock:
        try:
            sock.bind((host, port))
        except OSError:
            raise ReproductionError(f"port {port} is unavailable") from None


def verify_binary(binary):
    text = subprocess.check_output(["go", "version", "-m", binary], text=True)
    require(
        f"vcs.revision={REVISION}" in text,
        "binary does not match the pinned release revision",
    )
    require("vcs.modified=false" in text, "binary was built from modified sources")
    require(
        "github.com/maximhq/bifrost/core\tv1.8.3\t" in text,
        "unexpected Bifrost core version",
    )
    digest = hashlib.sha256()
    with open(binary, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "release": RELEASE,
        "revision": REVISION,
        "sha256": digest.hexdigest(),
        "go_build_info": text,
    }


def load_key(env_file):
    from dotenv import dotenv_values

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        key = dotenv_values(env_file, interpolate=False).get("OPENAI_API_KEY")
    require(bool(key), "OPENAI_API_KEY is required for --live")
    return key


def upstream(path, raw, live, secret):
    require(
        path in ("/v1/responses", "/v1/chat/completions"), "unexpected upstream path"
    )
    safe_raw(raw, secret)
    if live:
        # The only authenticated destination is fixed here. No redirects/proxies.
        conn = http.client.HTTPSConnection("api.openai.com", timeout=60)
        try:
            conn.request(
                "POST",
                path,
                raw,
                {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + secret,
                },
            )
            response = conn.getresponse()
            return {
                "status": response.status,
                "headers": safe_headers(response.getheaders()),
                "body_raw": safe_raw(response.read(), secret),
            }
        finally:
            conn.close()
    if path == "/v1/responses":
        reply = {
            "id": "resp_072",
            "object": "response",
            "model": MODEL,
            "status": "completed",
            "output": [
                {
                    "type": "function_call",
                    "id": "fc_072",
                    "call_id": "call_072",
                    "name": "get_weather",
                    "arguments": '{"location":"Tokyo"}',
                    "status": "completed",
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }
    else:
        reply = {
            "id": "chatcmpl-072",
            "object": "chat.completion",
            "model": MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_072",
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
    return {
        "status": 200,
        "headers": {"content-type": "application/json"},
        "body_raw": json.dumps(reply),
    }


class Relay(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, raw):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.reply(
            200,
            json.dumps(
                {"object": "list", "data": [{"id": MODEL, "object": "model"}]}
            ).encode(),
        )

    def do_POST(self):
        try:
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            request = {
                "method": "POST",
                "path": self.path,
                "headers": safe_headers(self.headers.items()),
                "body_raw": safe_raw(raw, self.server.secret),
            }
            response = upstream(self.path, raw, self.server.live, self.server.secret)
            self.server.captures.append({"request": request, "response": response})
            self.reply(response["status"], response["body_raw"].encode())
        except Exception as exc:
            # Never log exception values: provider errors can contain credentials.
            self.server.errors.append(type(exc).__name__)
            self.reply(502, b'{"error":{"message":"capture relay failed"}}')


def wait_for_ready(url, proc, timeout=30):
    import httpx

    deadline = time.monotonic() + timeout
    with httpx.Client(trust_env=False, timeout=1) as client:
        while time.monotonic() < deadline:
            require(
                proc.poll() is None,
                f"bifrost process exited early with code {proc.returncode}",
            )
            try:
                if client.get(url + "/v1/models").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
    raise ReproductionError("Bifrost did not become ready")


def get_weather(location):
    """A deterministic local tool; no model-supplied code is executed."""
    require(isinstance(location, str), "tool location must be a string")
    return {"location": location, "temperature_c": 20}


def validate_record(record, live):
    case = record["case"]
    body = json.loads(record["forwarded_request"]["body_raw"])
    require(body == record["body"], "parsed and raw forwarded bodies disagree")
    require(body["model"] == MODEL, "unexpected wire model")
    require(record["path"] == record["forwarded_request"]["path"], "path mismatch")
    require(
        record["path"]
        == (
            "/v1/responses"
            if record["profile"] == "responses"
            else "/v1/chat/completions"
        ),
        "wrong upstream route",
    )
    request = json.loads(record["client_request"]["body_raw"])
    client = record["client_response"]
    provider = record["upstream_response"]
    expected_status = 400 if live and case == "any" else 200
    require(
        client["status"] == provider["status"] == expected_status,
        "unexpected client/provider status",
    )
    choice = body.get("tool_choice")
    if case == "any":
        require(
            request["tool_choice"] == {"type": "any"} and choice == "any",
            "any trigger did not reproduce",
        )
        if live:
            error = json.loads(provider["body_raw"])["error"]
            require(
                error.get("param") == "tool_choice" and "any" in error["message"],
                "provider rejected something other than tool_choice=any",
            )
            require(
                record["consumer"]["outcome"] == "BadRequestError"
                and record["consumer"]["tool_dispatches"] == 0,
                "SDK did not stop before tool dispatch",
            )
    elif case in ("required", "direct-required"):
        require(
            request["tool_choice"] == choice == "required", "required control changed"
        )
    elif case == "named":
        require(
            request["tool_choice"] == {"type": "tool", "name": "get_weather"},
            "named control input changed",
        )
        require(
            choice
            == (
                {"type": "function", "name": "get_weather"}
                if record["profile"] == "responses"
                else {"type": "function", "function": {"name": "get_weather"}}
            ),
            "named tool was not preserved",
        )
        require(
            record["consumer"]["tool_dispatches"] == 1,
            "named control did not dispatch exactly one tool",
        )
    elif case == "auto":
        require(choice == "auto", "auto control changed")
    else:
        raise ReproductionError("unknown case")
    if case in ("required", "direct-required"):
        result = json.loads(provider["body_raw"])
        calls = (
            [
                item
                for item in result.get("output", [])
                if item.get("type") == "function_call"
            ]
            if record["profile"] == "responses"
            else result["choices"][0]["message"].get("tool_calls", [])
        )
        require(bool(calls), "forced-tool control returned no function call")


def validate_records(records, live):
    require(len(records) == RUNS, "expected exactly five raw exchanges")
    require(
        [r["run"] for r in records] == list(range(1, RUNS + 1)),
        "missing/duplicated run",
    )
    for record in records:
        validate_record(record, live)


def run(args):
    import anthropic
    import httpx

    target = verify_binary(args.bifrost_bin)
    secret = load_key(args.env_file) if args.live else ""
    ensure_port_available("127.0.0.1", args.port)
    ensure_port_available("127.0.0.1", args.upstream_port)
    output = Path(args.output_dir)
    require(
        not output.exists() or not any(output.iterdir()),
        "output directory contains evidence; use a fresh directory",
    )
    output.mkdir(parents=True, exist_ok=True)
    metadata = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live-openai" if args.live else "keyless-capture",
        "model": MODEL,
        "runs_per_case": RUNS,
        "target": target,
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("anthropic", "httpx", "python-dotenv")
        },
        "credential_variable": "OPENAI_API_KEY" if args.live else None,
        "upstream_origin": (
            "https://api.openai.com"
            if args.live
            else "local synthetic success responder"
        ),
        "complete": False,
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.upstream_port), Relay)
    server.secret, server.live, server.captures, server.errors = (
        secret,
        args.live,
        [],
        [],
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for profile in ("responses", "chat"):
            provider_name = "openai" if profile == "responses" else "chatonly"
            provider = {
                "keys": [
                    {
                        "name": "local-placeholder",
                        "value": "local-placeholder",
                        "weight": 1,
                        "models": ["*"],
                    }
                ],
                "network_config": {
                    "base_url": f"http://127.0.0.1:{args.upstream_port}",
                    "max_retries": 0,
                },
            }
            if profile == "chat":
                provider["custom_provider_config"] = {
                    "base_provider_type": "openai",
                    "allowed_requests": {
                        "list_models": True,
                        "chat_completion": True,
                        "chat_completion_stream": True,
                    },
                }
            config = {
                "config_store": {"enabled": False},
                "client": {"enable_logging": False, "initial_pool_size": 10},
                "providers": {provider_name: provider},
            }
            (output / f"{profile}-config.json").write_text(
                json.dumps(config, indent=2) + "\n"
            )
            with tempfile.TemporaryDirectory(prefix="kairo-072-") as app:
                Path(app, "config.json").write_text(json.dumps(config))
                child_env = {
                    k: v
                    for k, v in os.environ.items()
                    if not any(
                        word in k.upper()
                        for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")
                    )
                }
                proc = subprocess.Popen(
                    [
                        args.bifrost_bin,
                        "-host",
                        "127.0.0.1",
                        "-app-dir",
                        app,
                        "-port",
                        str(args.port),
                    ],
                    env=child_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                try:
                    url = f"http://127.0.0.1:{args.port}"
                    wait_for_ready(url, proc)
                    exchanges = []

                    def request_hook(request):
                        exchanges.append(
                            {
                                "request": {
                                    "method": request.method,
                                    "path": request.url.path,
                                    "headers": safe_headers(request.headers.items()),
                                    "body_raw": safe_raw(request.content, secret),
                                }
                            }
                        )

                    def response_hook(response):
                        exchanges[-1]["response"] = {
                            "status": response.status_code,
                            "headers": safe_headers(response.headers.items()),
                            "body_raw": safe_raw(response.read(), secret),
                        }

                    with httpx.Client(
                        trust_env=False,
                        timeout=90,
                        event_hooks={
                            "request": [request_hook],
                            "response": [response_hook],
                        },
                    ) as client:
                        sdk = anthropic.Anthropic(
                            api_key="local-placeholder",
                            base_url=url + "/anthropic",
                            max_retries=0,
                            http_client=client,
                        )
                        violating_body = None
                        cases = ("any", "named", "required", "direct-required") + (
                            () if args.live else ("auto",)
                        )
                        for case in cases:
                            records = []
                            for trial in range(1, RUNS + 1):
                                exchanges.clear()
                                server.captures.clear()
                                consumer = {
                                    "outcome": "http-control",
                                    "tool_dispatches": 0,
                                }
                                if case == "direct-required":
                                    body = copy.deepcopy(violating_body)
                                    body["tool_choice"] = "required"
                                    raw = json.dumps(body).encode()
                                    path = (
                                        "/v1/responses"
                                        if profile == "responses"
                                        else "/v1/chat/completions"
                                    )
                                    request = {
                                        "method": "POST",
                                        "path": path,
                                        "headers": {"content-type": "application/json"},
                                        "body_raw": safe_raw(raw, secret),
                                    }
                                    response = upstream(path, raw, args.live, secret)
                                    capture = {"request": request, "response": response}
                                    exchange = capture
                                else:
                                    if case in ("any", "named", "auto"):
                                        choice = (
                                            {"type": "tool", "name": "get_weather"}
                                            if case == "named"
                                            else {"type": case}
                                        )
                                        try:
                                            message = sdk.messages.create(
                                                model=f"{provider_name}/{MODEL}",
                                                max_tokens=100,
                                                tools=[TOOL],
                                                tool_choice=choice,
                                                messages=[
                                                    {
                                                        "role": "user",
                                                        "content": "weather in Tokyo?",
                                                    }
                                                ],
                                            )
                                            consumer["outcome"] = "tool_executed"
                                            for block in message.content:
                                                if block.type == "tool_use":
                                                    require(
                                                        block.name == "get_weather",
                                                        "unexpected tool",
                                                    )
                                                    get_weather(**block.input)
                                                    consumer["tool_dispatches"] += 1
                                        except anthropic.APIStatusError as exc:
                                            consumer["outcome"] = type(exc).__name__
                                    else:
                                        body = {
                                            "model": f"{provider_name}/{MODEL}",
                                            "tool_choice": "required",
                                        }
                                        function = {
                                            "name": TOOL["name"],
                                            "description": TOOL["description"],
                                            "parameters": TOOL["input_schema"],
                                        }
                                        if profile == "responses":
                                            body.update(
                                                max_output_tokens=100,
                                                input=[
                                                    {
                                                        "role": "user",
                                                        "content": "weather in Tokyo?",
                                                    }
                                                ],
                                                tools=[
                                                    {"type": "function", **function}
                                                ],
                                            )
                                            path = "/v1/responses"
                                        else:
                                            body.update(
                                                max_tokens=100,
                                                messages=[
                                                    {
                                                        "role": "user",
                                                        "content": "weather in Tokyo?",
                                                    }
                                                ],
                                                tools=[
                                                    {
                                                        "type": "function",
                                                        "function": function,
                                                    }
                                                ],
                                            )
                                            path = "/v1/chat/completions"
                                        client.post(url + path, json=body)
                                    require(
                                        not server.errors,
                                        "relay failed; no safe provider evidence available",
                                    )
                                    require(
                                        len(exchanges) == len(server.captures) == 1,
                                        "expected one client and one upstream request, without retries",
                                    )
                                    capture, exchange = server.captures[0], exchanges[0]
                                forwarded = json.loads(capture["request"]["body_raw"])
                                if case == "any":
                                    violating_body = copy.deepcopy(forwarded)
                                record = {
                                    "profile": profile,
                                    "case": case,
                                    "run": trial,
                                    "mode": metadata["mode"],
                                    "path": capture["request"]["path"],
                                    "body": forwarded,
                                    "client_request": exchange["request"],
                                    "forwarded_request": capture["request"],
                                    "upstream_response": capture["response"],
                                    "client_response": exchange["response"],
                                    "consumer": consumer,
                                }
                                # Keep even a failing exchange for diagnosis, but never claim it passed.
                                with (output / f"{profile}-{case}.jsonl").open(
                                    "a"
                                ) as stream:
                                    stream.write(
                                        safe_raw(
                                            json.dumps(
                                                record, ensure_ascii=True
                                            ).encode(),
                                            secret,
                                        )
                                        + "\n"
                                    )
                                records.append(record)
                                validate_record(record, args.live)
                            validate_records(records, args.live)
                            print(
                                f"{metadata['mode']} {profile}/{case}: {RUNS}/{RUNS} passed",
                                flush=True,
                            )
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
        metadata["complete"] = True
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    finally:
        server.shutdown()
        server.server_close()
        server.secret = ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Call real OpenAI using OPENAI_API_KEY"
    )
    parser.add_argument(
        "--env-file", default=str(Path(__file__).resolve().parents[2] / ".env")
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="New directory; existing evidence is never overwritten",
    )
    parser.add_argument(
        "--bifrost-bin", default=os.environ.get("BIFROST_BIN", DEFAULT_BINARY)
    )
    parser.add_argument("--port", type=int, default=18072)
    parser.add_argument("--upstream-port", type=int, default=19072)
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:
        # Exception values from HTTP clients can contain provider data or credentials.
        detail = str(exc) if isinstance(exc, ReproductionError) else type(exc).__name__
        print(
            f"Reproduction failed: {detail}. Inspect sanitized captures, not credentials."
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
