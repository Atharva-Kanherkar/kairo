#!/usr/bin/env python3
"""Consumer-boundary evidence for issue 085: the real Codex CLI through a real
LiteLLM proxy with a `fallbacks` configuration.

Codex -> capture relay -> LiteLLM proxy -> deterministic upstream (and, with
--live-fallback-model, the live OpenAI API for the fallback deployment).

The deterministic primary streams one completed `exec_command` call that
appends a line to `ledger.txt`, then fails with a retriable in-band error. The
number of lines in the ledger after `codex exec` exits is the number of times
Codex ran the side effect. Codex runs with a fresh temporary HOME and
CODEX_HOME, no approval prompts, a workspace-write sandbox, and its own request
and stream retries disabled, so any repeated execution comes from the stream
LiteLLM returned rather than from Codex retrying.
"""

import argparse
import contextlib
import hashlib
import http.client
import http.server
import itertools
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

HOST = "127.0.0.1"
MASTER_KEY = "sk-kairo-085-cx-master"
UPSTREAM_KEY = "sk-kairo-085-cx-upstream"
CLIENT_KEY = "sk-kairo-085-cx-client"
LEDGER = "ledger.txt"
PROMPT = "Append the line run to ledger.txt using the shell, once."
EXTERNALIZED_KEYS = ("instructions", "tools")
TMP_MARKER = "[REDACTED:tmpdir]"
ENCRYPTED_MARKER = "[REDACTED:encrypted_content]"
CREDENTIAL_PATTERN = re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{24,}")

# case -> (primary deployment, fallback deployment). Cases double as the public
# model names Codex requests through its custom provider.
CASES = {
    "codex-trigger-duplicate-tool": ("codex-primary-fault", "codex-fallback"),
    "codex-control-no-fault": ("codex-primary-ok", "codex-fallback"),
    "codex-control-fault-before-output": ("codex-primary-prefail", "codex-fallback"),
}
LIVE_CASE = "codex-trigger-live-fallback"
LIVE_FALLBACK = "codex-fallback-live"
EXPECTED_EXECUTIONS = {
    "codex-trigger-duplicate-tool": 2,
    "codex-control-no-fault": 1,
    "codex-control-fault-before-output": 1,
    LIVE_CASE: 2,
}


class RunError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise RunError(message)


# ---- deterministic upstream -------------------------------------------------

def sse(event_type, **fields):
    payload = {"type": event_type, **fields}
    return f"event: {event_type}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


def response_obj(rid, model, status, output=None):
    usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
             "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}
    return {"id": rid, "object": "response", "created_at": 0, "status": status, "model": model,
            "output": output or [], "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
            "error": None, "usage": usage if status == "completed" else None}


def shell_tool_call(request):
    """Pick Codex's shell tool from the request and build its arguments."""
    names = [tool.get("name") for tool in request.get("tools", []) if isinstance(tool, dict)]
    command = f"echo run >> {LEDGER}"
    if "exec_command" in names:
        return "exec_command", json.dumps({"cmd": command})
    if "shell_command" in names:
        return "shell_command", json.dumps({"command": command})
    if "shell" in names:
        return "shell", json.dumps({"command": ["bash", "-lc", command]})
    raise ValueError(f"no shell-like function tool in {names}")


def has_tool_output(request):
    items = request.get("input")
    return isinstance(items, list) and any(
        isinstance(item, dict) and item.get("type") == "function_call_output" for item in items
    )


def function_call_events(seq, name, arguments, call_id):
    item = {"id": f"fc_{call_id}", "type": "function_call", "status": "in_progress",
            "name": name, "call_id": call_id, "arguments": ""}
    done = dict(item, status="completed", arguments=arguments)
    return [
        sse("response.output_item.added", sequence_number=next(seq), output_index=0, item=item),
        sse("response.function_call_arguments.delta", sequence_number=next(seq), output_index=0,
            item_id=item["id"], delta=arguments),
        sse("response.function_call_arguments.done", sequence_number=next(seq), output_index=0,
            item_id=item["id"], arguments=arguments),
        sse("response.output_item.done", sequence_number=next(seq), output_index=0, item=done),
    ], done


def final_message_events(seq, rid, model):
    msg = {"id": f"msg_{rid}", "type": "message", "status": "in_progress", "role": "assistant", "content": []}
    done = dict(msg, status="completed", content=[{"type": "output_text", "text": "done", "annotations": []}])
    return [
        sse("response.output_item.added", sequence_number=next(seq), output_index=0, item=msg),
        sse("response.content_part.added", sequence_number=next(seq), output_index=0, content_index=0,
            item_id=msg["id"], part={"type": "output_text", "text": "", "annotations": []}),
        sse("response.output_text.delta", sequence_number=next(seq), output_index=0, content_index=0,
            item_id=msg["id"], delta="done", logprobs=[]),
        sse("response.output_text.done", sequence_number=next(seq), output_index=0, content_index=0,
            item_id=msg["id"], text="done", logprobs=[]),
        sse("response.output_item.done", sequence_number=next(seq), output_index=0, item=done),
        sse("response.completed", sequence_number=next(seq), response=response_obj(rid, model, "completed", [done])),
    ]


def error_event(seq):
    return sse("error", sequence_number=next(seq),
               error={"type": "server_error", "code": "server_error", "message": "upstream error"})


def provider_stream(request, rid):
    """Exact SSE bytes for one upstream call. A request that already carries a
    tool result gets a final assistant message, which ends the Codex turn."""
    model = request.get("model", "")
    seq = itertools.count()
    out = [sse("response.created", sequence_number=next(seq), response=response_obj(rid, model, "in_progress"))]
    if has_tool_output(request):
        return b"".join(out + final_message_events(seq, rid, model))
    if model == "codex-primary-fault":
        name, arguments = shell_tool_call(request)
        events, _ = function_call_events(seq, name, arguments, "call_primary")
        return b"".join(out + events + [error_event(seq)])
    if model == "codex-primary-prefail":
        return b"".join(out + [error_event(seq)])
    if model in ("codex-primary-ok", "codex-fallback"):
        name, arguments = shell_tool_call(request)
        call_id = "call_primary" if model == "codex-primary-ok" else "call_fallback"
        events, done = function_call_events(seq, name, arguments, call_id)
        completed = sse("response.completed", sequence_number=next(seq),
                        response=response_obj(rid, model, "completed", [done]))
        return b"".join(out + events + [completed])
    raise ValueError(f"unknown deterministic scenario model: {model!r}")


# ---- lossless externalization of repeated request fields ------------------

def top_level_value_span(text, key):
    """(start, end) of the raw JSON value of top-level `key` in `text`, or None."""
    decoder = json.JSONDecoder()
    depth, index, length = 0, 0, len(text)
    while index < length:
        char = text[index]
        if char == '"':
            name, end = decoder.raw_decode(text, index)
            if depth == 1:
                cursor = end
                while cursor < length and text[cursor] in " \t\r\n":
                    cursor += 1
                if cursor < length and text[cursor] == ":" and name == key:
                    cursor += 1
                    while text[cursor] in " \t\r\n":
                        cursor += 1
                    _, value_end = decoder.raw_decode(text, cursor)
                    return cursor, value_end
            index = end
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        index += 1
    return None


def externalize(text, shared_dir):
    """Replace the raw value of each large repeated top-level field with a
    reference marker and store the exact raw bytes once under `shared_dir`."""
    for key in EXTERNALIZED_KEYS:
        span = top_level_value_span(text, key)
        if span is None:
            continue
        start, end = span
        raw_value = text[start:end]
        digest = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()
        name = f"{key}-{digest[:16]}.json"
        path = shared_dir / name
        if not path.exists():
            path.write_text(raw_value, encoding="utf-8")
        marker = json.dumps(f"[kairo-ref sha256:{digest} file:shared/{name}]")
        text = text[:start] + marker + text[end:]
    return text


REF_PATTERN = re.compile(r'"\[kairo-ref sha256:([0-9a-f]{64}) file:shared/([A-Za-z0-9_.-]+)\]"')


def restore(text, shared_dir):
    """Inverse of externalize. Raises if a referenced file does not match its digest."""
    def replace(match):
        raw_value = (shared_dir / match.group(2)).read_text(encoding="utf-8")
        if hashlib.sha256(raw_value.encode("utf-8")).hexdigest() != match.group(1):
            raise RunError(f"shared/{match.group(2)} does not match its sha256")
        return raw_value
    return REF_PATTERN.sub(replace, text)


ENCRYPTED_PATTERN = re.compile(r'("encrypted_content":\s*)"(?:[^"\\]|\\.)*"')


def sanitize(text, temp_roots):
    for root in temp_roots:
        text = text.replace(root, TMP_MARKER)
    return ENCRYPTED_PATTERN.sub(lambda m: m.group(1) + json.dumps(ENCRYPTED_MARKER), text)


def check_clean(text, secret_values):
    for fixed in (MASTER_KEY, UPSTREAM_KEY):
        require(fixed not in text, "fixed local key appeared in a capture")
    # Anchored at a token boundary: LiteLLM's random base64url response ids can
    # contain "sk-" mid-string, which is not a credential.
    require(not CREDENTIAL_PATTERN.search(text), "possible credential appeared in a capture")
    for value in secret_values:
        require(value not in text, "a credential from the environment appeared in a capture")


# ---- capture servers ------------------------------------------------------

class CaptureServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler_cls):
        super().__init__(address, handler_cls)
        self.records = []
        self.lock = threading.Lock()
        self.counter = itertools.count(1)

    def add(self, record):
        with self.lock:
            self.records.append(record)

    def snapshot(self):
        with self.lock:
            return list(self.records)

    def handle_error(self, request, client_address):
        # Codex closes kept-alive connections when it exits; that reset is not evidence.
        if not isinstance(sys.exc_info()[1], (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)


class UpstreamHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", "0")))
        request = json.loads(raw)
        body = provider_stream(request, f"resp_{request.get('model')}_{next(self.server.counter)}")
        self.server.add({"started": time.time(), "path": self.path, "model": request.get("model"),
                         "request_body_raw": raw.decode("utf-8"), "response_body_raw": body.decode("utf-8")})
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class RelayHandler(http.server.BaseHTTPRequestHandler):
    """Streams each exchange between Codex and LiteLLM through unchanged,
    except that Codex's placeholder bearer is replaced by the proxy key."""
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def relay(self, method):
        started = time.time()
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length) if length else None
        headers = {name: value for name, value in self.headers.items()
                   if name.lower() not in ("host", "content-length", "authorization", "connection", "accept-encoding")}
        headers["authorization"] = f"Bearer {MASTER_KEY}"
        connection = http.client.HTTPConnection(HOST, self.server.proxy_port, timeout=120)
        connection.request(method, self.path, body=body, headers=headers)
        response = connection.getresponse()
        content_type = response.getheader("content-type", "application/json")
        self.send_response(response.status)
        self.send_header("content-type", content_type)
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        captured = bytearray()
        while True:
            chunk = response.read1(65536)
            if not chunk:
                break
            captured += chunk
            self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()
        connection.close()
        self.server.add({
            "started": started, "method": method, "path": self.path, "status": response.status,
            "request_headers": {name.lower(): value for name, value in self.headers.items()
                                if name.lower() in ("content-type", "accept", "originator")},
            "request_body_raw": body.decode("utf-8") if body else None,
            "response_content_type": content_type,
            "response_body_raw": captured.decode("utf-8", errors="replace"),
        })

    def do_POST(self):
        self.relay("POST")

    def do_GET(self):
        self.relay("GET")


@contextlib.contextmanager
def serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def free_port():
    with socket.socket() as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def wait_ready(port, process, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        require(process.poll() is None, f"LiteLLM exited early with status {process.returncode}")
        try:
            connection = http.client.HTTPConnection(HOST, port, timeout=1)
            connection.request("GET", "/health/liveliness")
            if connection.getresponse().status == 200:
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise RunError("LiteLLM proxy did not become ready")


def write_config(path, upstream_base, cases, live_model):
    lines = ["model_list:"]
    for case in cases:
        primary = CASES[case][0] if case != LIVE_CASE else "codex-primary-fault"
        lines += [f"  - model_name: {case}", "    litellm_params:", f"      model: openai/{primary}",
                  f"      api_base: {upstream_base}", f"      api_key: {UPSTREAM_KEY}"]
    lines += ["  - model_name: codex-fallback", "    litellm_params:", "      model: openai/codex-fallback",
              f"      api_base: {upstream_base}", f"      api_key: {UPSTREAM_KEY}"]
    if live_model:
        lines += [f"  - model_name: {LIVE_FALLBACK}", "    litellm_params:", f"      model: openai/{live_model}",
                  "      api_key: os.environ/OPENAI_API_KEY"]
    pairs = ", ".join(
        f'{{"{case}": ["{LIVE_FALLBACK if case == LIVE_CASE else CASES[case][1]}"]}}' for case in cases
    )
    lines += ["router_settings:", f"  fallbacks: [{pairs}]", "  num_retries: 0",
              "general_settings:", f"  master_key: {MASTER_KEY}", "litellm_settings:", "  telemetry: false", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_codex_home(home, relay_port):
    home.mkdir()
    (home / "config.toml").write_text("\n".join([
        'model_provider = "kairo_litellm"',
        'approval_policy = "never"',
        'sandbox_mode = "workspace-write"',
        'web_search = "disabled"',
        "[model_providers.kairo_litellm]",
        'name = "kairo-litellm"',
        f'base_url = "http://{HOST}:{relay_port}/v1"',
        'env_key = "KAIRO_LITELLM_KEY"',
        'wire_api = "responses"',
        "request_max_retries = 0",
        "stream_max_retries = 0",
        "",
    ]), encoding="utf-8")


def package_metadata(python, codex):
    script = "import importlib.metadata,json;print(json.dumps({n:importlib.metadata.version(n) for n in ['litellm','openai']}))"
    versions = json.loads(subprocess.check_output([python, "-c", script], text=True))
    source = subprocess.check_output(
        [python, "-c", "import litellm.router as m;print(m.__file__)"], text=True).strip()
    codex_version = subprocess.check_output([codex, "--version"], text=True, stdin=subprocess.DEVNULL).strip()
    return {
        "packages": versions,
        "router_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
        "codex": codex_version,
        "python": subprocess.check_output([python, "--version"], text=True).strip(),
    }


def command_executions(events):
    return [event["item"] for event in events
            if event.get("type") == "item.completed" and (event.get("item") or {}).get("type") == "command_execution"]


def run(args):
    output = Path(args.output_dir).resolve()
    require(not output.exists(), "output directory already exists")
    python, codex = os.path.abspath(args.python), os.path.abspath(args.codex)
    metadata = package_metadata(python, codex)
    require(metadata["packages"]["litellm"] == args.expect_litellm, f"expected LiteLLM {args.expect_litellm}")
    require(metadata["codex"] == f"codex-cli {args.expect_codex}", f"expected Codex {args.expect_codex}")
    cases = list(CASES) if not args.live_fallback_model else [LIVE_CASE]
    secrets = [os.environ["OPENAI_API_KEY"]] if args.live_fallback_model and os.environ.get("OPENAI_API_KEY") else []
    require(not args.live_fallback_model or secrets, "--live-fallback-model needs OPENAI_API_KEY in the environment")
    output.mkdir(parents=True)
    shared = Path(args.shared_dir).resolve() if args.shared_dir else output / "shared"
    shared.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="kairo-085-codex-") as temp_name:
        temp = Path(temp_name)
        temp_roots = sorted({str(temp), str(temp.resolve()), "/private" + str(temp)}, key=len, reverse=True)
        upstream = CaptureServer((HOST, free_port()), UpstreamHandler)
        relay = CaptureServer((HOST, free_port()), RelayHandler)
        proxy_port = free_port()
        relay.proxy_port = proxy_port
        config = temp / "litellm.yaml"
        write_config(config, f"http://{HOST}:{upstream.server_address[1]}/v1", cases, args.live_fallback_model)
        proxy_env = dict(os.environ) if args.live_fallback_model else {
            name: value for name, value in os.environ.items() if not re.search(r"KEY|TOKEN|SECRET", name)
        }
        codex_env = {name: value for name, value in os.environ.items() if not re.search(r"KEY|TOKEN|SECRET", name)}
        records = []
        log = (temp / "litellm.log").open("w", encoding="utf-8")
        process = subprocess.Popen([str(Path(python).with_name("litellm")), "--config", str(config),
                                    "--port", str(proxy_port)], stdout=log, stderr=subprocess.STDOUT, env=proxy_env)
        try:
            with serving(upstream), serving(relay):
                wait_ready(proxy_port, process)
                for case in cases:
                    for trial in range(1, args.runs + 1):
                        run_dir = temp / f"{case}-{trial}"
                        work, home, codex_home = run_dir / "work", run_dir / "home", run_dir / "codex-home"
                        work.mkdir(parents=True)
                        home.mkdir()
                        write_codex_home(codex_home, relay.server_address[1])
                        env = dict(codex_env, HOME=str(home), CODEX_HOME=str(codex_home), KAIRO_LITELLM_KEY=CLIENT_KEY)
                        relay_before, upstream_before = len(relay.snapshot()), len(upstream.snapshot())
                        completed = subprocess.run(
                            [codex, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-rules", "--json",
                             "-m", case, "-C", str(work), PROMPT],
                            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180,
                        )
                        time.sleep(0.3)
                        ledger = (work / LEDGER).read_text() if (work / LEDGER).exists() else ""
                        events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
                        client = sorted(relay.snapshot()[relay_before:], key=lambda r: r["started"])
                        forwarded = sorted(upstream.snapshot()[upstream_before:], key=lambda r: r["started"])
                        for exchange in client + forwarded:
                            raw = exchange.pop("request_body_raw")
                            exchange.pop("started")
                            if raw is None:
                                exchange["request_body_raw"] = None
                                continue
                            clean = sanitize(raw, temp_roots)
                            exchange["request_body_sha256"] = hashlib.sha256(clean.encode("utf-8")).hexdigest()
                            exchange["request_body_raw"] = externalize(clean, shared)
                            exchange["response_body_raw"] = sanitize(exchange["response_body_raw"], temp_roots)
                        record = {
                            "target": {"project": "BerriAI/litellm", "version": args.expect_litellm},
                            "consumer": {"name": "codex-cli", "version": args.expect_codex},
                            "case": case,
                            "trial": trial,
                            "live_fallback_model": args.live_fallback_model,
                            "codex_exit": completed.returncode,
                            "ledger_lines": ledger.count("\n"),
                            "command_executions": len(command_executions(events)),
                            "codex_events": json.loads(sanitize(json.dumps(events), temp_roots)),
                            "codex_stderr_tail": sanitize(completed.stderr[-1500:], temp_roots),
                            "client_exchanges": client,
                            "upstream_exchanges": forwarded,
                        }
                        check_clean(json.dumps(record), secrets)
                        require(record["codex_exit"] == 0, f"{case} trial {trial}: codex exited {completed.returncode}")
                        require(record["ledger_lines"] == EXPECTED_EXECUTIONS[case],
                                f"{case} trial {trial}: expected {EXPECTED_EXECUTIONS[case]} ledger lines, "
                                f"got {record['ledger_lines']}")
                        records.append(record)
                        print(f"{case} {trial}: ledger lines {record['ledger_lines']}", flush=True)
        finally:
            process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                process.wait(timeout=10)
            if process.poll() is None:
                process.kill()
            log.close()

    for case in cases:
        rows = [record for record in records if record["case"] == case]
        (output / f"{case}.jsonl").write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
                                              encoding="utf-8")
    metadata.update({"captured_at": args.captured_at, "runs_per_case": args.runs, "cases": cases, "note": args.note,
                     "live_fallback_model": args.live_fallback_model,
                     "live_fallback_hop": "LiteLLM to api.openai.com, not captured" if args.live_fallback_model else None,
                     "complete": True})
    for path in shared.iterdir():
        check_clean(path.read_text(encoding="utf-8"), secrets)
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"LiteLLM {args.expect_litellm}, Codex {args.expect_codex}: every case matched its expected "
          f"execution count {args.runs}/{args.runs}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="Python executable in the pinned LiteLLM environment")
    parser.add_argument("--codex", required=True, help="path to the pinned codex binary")
    parser.add_argument("--expect-litellm", required=True)
    parser.add_argument("--expect-codex", required=True)
    parser.add_argument("--output-dir", required=True, help="fresh directory for sanitized captures")
    parser.add_argument("--shared-dir", help="directory for externalized request fields (default OUTPUT/shared)")
    parser.add_argument("--captured-at", required=True, help="UTC date of this capture")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--note", help="free-text provenance recorded in metadata.json, e.g. the source commit")
    parser.add_argument("--live-fallback-model",
                        help="run only the live case, with this OpenAI model as the fallback; needs OPENAI_API_KEY")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, RunError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
        print(f"codex matrix failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
