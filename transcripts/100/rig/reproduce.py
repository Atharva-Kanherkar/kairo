"""Reproduce finding 100 against a locally built switchyard-server.

Runs three case groups and writes every exchange under --output:

  direct/    the provider control: Anthropic called with no gateway, once with the
             real server tool and once with the exact tool Switchyard forwards
  minimal/   one Messages request with only a web_search server tool, sent to the
             plan_execute route and to the passthrough control route
  claude/    (with --claude) Claude Code print mode asked to use WebSearch, on
             each route

The Anthropic key is read from ANTHROPIC_API_KEY or from --env-file. It is passed
to switchyard-server and to the direct calls, and never printed or written: the
relays redact it, and the script scans every output file for it before exiting.

Exit status is 0 only when every case matches the claim.
"""
import argparse
import http.client
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time

RIG = pathlib.Path(__file__).resolve().parent
SERVER_PORT, CLIENT_RELAY_PORT, UPSTREAM_RELAY_PORT = 9000, 9100, 9101
SERVER_TOOL = {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}
# The tool definition switchyard-server forwards for SERVER_TOOL on plan_execute.
REENCODED_TOOL = {"name": "web_search", "description": "", "input_schema": {}}
PROMPT = "Search the web for the latest stable Rust version. One sentence."
CLAUDE_PROMPT = (
    "Use the WebSearch tool to look up the latest stable Rust release version, "
    "then answer in one sentence citing what the search returned."
)
WEBSEARCH_MARKER = "Perform a web search for the query"


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--switchyard-bin", required=True)
    parser.add_argument("--switchyard-src", required=True, help="checkout the binary was built from")
    parser.add_argument("--output", required=True)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--claude", action="store_true", help="also run the Claude Code cases")
    parser.add_argument("--env-file", help="KEY=VALUE file that holds ANTHROPIC_API_KEY")
    return parser.parse_args()


def load_key(env_file):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key and env_file:
        for line in pathlib.Path(env_file).read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit("ANTHROPIC_API_KEY is not set and --env-file did not provide it")
    return key


def port_free(port):
    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def wait_port(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not port_free(port):
            return
        time.sleep(0.2)
    sys.exit(f"port {port} did not open")


class Relays:
    """Starts the client-side and upstream-side relays into one case directory."""

    def __init__(self, case_dir, client_hop=True):
        self.procs = []
        upstream = ["https://api.anthropic.com", str(case_dir / "upstream")]
        self.procs.append(self.start(UPSTREAM_RELAY_PORT, upstream))
        if client_hop:
            client = [f"http://127.0.0.1:{SERVER_PORT}", str(case_dir / "client")]
            self.procs.append(self.start(CLIENT_RELAY_PORT, client))

    @staticmethod
    def start(port, args):
        proc = subprocess.Popen(
            [sys.executable, str(RIG / "relay.py"), str(port), *args],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_port(port)
        return proc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        for proc in self.procs:
            proc.terminate()
            proc.wait()
        for port in (UPSTREAM_RELAY_PORT, CLIENT_RELAY_PORT):
            while not port_free(port):
                time.sleep(0.1)


def post(port, body, headers):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=300)
    conn.request("POST", "/v1/messages", body=json.dumps(body), headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    return resp.status, data


def http_body(path):
    return json.loads(path.read_bytes().split(b"\r\n\r\n", 1)[1])


def http_status(path):
    return int(path.read_bytes().split(b" ", 2)[1])


def exchanges(directory):
    """Yields (request_path, response_path, body) for every JSON request in a hop."""
    for request in sorted(directory.glob("*-request.http")):
        try:
            body = http_body(request)
        except (ValueError, IndexError):
            continue
        yield request, request.with_name(request.name.replace("-request", "-response")), body


def web_search_tools(body):
    return [tool for tool in body.get("tools") or [] if tool.get("name") == "web_search"]


def is_server_tool(tool):
    return tool.get("type") == SERVER_TOOL["type"] and "input_schema" not in tool


def run_direct(out, key, runs):
    results = []
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01", "x-api-key": key}
    for shape, tool in (("server-tool", SERVER_TOOL), ("reencoded-tool", REENCODED_TOOL)):
        for run in range(1, runs + 1):
            case = out / "direct" / f"{shape}-{run:02d}"
            body = {
                "model": "claude-sonnet-5-5",
                "max_tokens": 400,
                "tools": [tool],
                "messages": [{"role": "user", "content": PROMPT}],
            }
            with Relays(case, client_hop=False):
                status, data = post(UPSTREAM_RELAY_PORT, body, headers)
            searched = b"web_search_tool_result" in data
            results.append({"case": f"direct/{shape}", "run": run, "status": status, "searched": searched})
    return results


def run_minimal(out, runs):
    results = []
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    for route in ("sy-plan", "sy-pass"):
        for run in range(1, runs + 1):
            case = out / "minimal" / f"{route}-{run:02d}"
            body = {
                "model": route,
                "max_tokens": 400,
                "tools": [SERVER_TOOL],
                "messages": [{"role": "user", "content": PROMPT}],
            }
            with Relays(case):
                status, _ = post(CLIENT_RELAY_PORT, body, headers)
            (_, response, forwarded), = list(exchanges(case / "upstream"))
            tools = web_search_tools(forwarded)
            results.append({
                "case": f"minimal/{route}",
                "run": run,
                "client_status": status,
                "upstream_status": http_status(response),
                "forwarded_web_search": tools,
                "forwarded_as_server_tool": bool(tools) and all(map(is_server_tool, tools)),
            })
    return results


def run_claude(out, runs):
    results = []
    for route in ("sy-plan", "sy-pass"):
        for run in range(1, runs + 1):
            case = out / "claude" / f"{route}-{run:02d}"
            work = pathlib.Path(tempfile.mkdtemp(prefix="kairo-100-cc-"))
            env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")}
            env.update({
                "CLAUDE_CONFIG_DIR": str(work / "config"),
                "ANTHROPIC_API_KEY": "sk-ant-placeholder-not-a-key",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{CLIENT_RELAY_PORT}",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": route,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": route,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": route,
                "ANTHROPIC_SMALL_FAST_MODEL": route,
            })
            (work / "cwd").mkdir()
            with Relays(case):
                proc = subprocess.run(
                    ["claude", "--model", route, "-p", CLAUDE_PROMPT,
                     "--allowedTools", "WebSearch", "--output-format", "json"],
                    cwd=work / "cwd", env=env, capture_output=True, text=True, timeout=600,
                )
            shutil.rmtree(work)
            (case / "claude-stdout.json").write_text(proc.stdout)
            lines = [line for line in proc.stdout.splitlines() if '"type":"result"' in line]
            final = json.loads(lines[-1]) if lines else {}
            searches = [
                (request, response, body)
                for request, response, body in exchanges(case / "upstream")
                if WEBSEARCH_MARKER in json.dumps(body.get("messages"))
            ]
            record = {
                "case": f"claude/{route}",
                "run": run,
                "claude_exit": proc.returncode,
                "claude_is_error": final.get("is_error"),
                "result": final.get("result", ""),
                "websearch_subrequests": len(searches),
            }
            if searches:
                _, response, forwarded = searches[0]
                record.update({
                    "upstream_status": http_status(response),
                    "forwarded_web_search": web_search_tools(forwarded),
                    "forwarded_as_server_tool": all(map(is_server_tool, web_search_tools(forwarded))),
                    "search_results_returned": b"web_search_tool_result" in response.read_bytes(),
                })
            results.append(record)
    return results


def scan_for_key(out, key):
    needle = key.encode()
    return [str(path) for path in out.rglob("*") if path.is_file() and needle in path.read_bytes()]


def main():
    args = parse_args()
    key = load_key(args.env_file)
    out = pathlib.Path(args.output).resolve()
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    for port in (SERVER_PORT, CLIENT_RELAY_PORT, UPSTREAM_RELAY_PORT):
        if not port_free(port):
            sys.exit(f"port {port} is in use")

    src = pathlib.Path(args.switchyard_src)
    commit = subprocess.run(["git", "-C", src, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", src, "status", "--porcelain"], capture_output=True, text=True).stdout
    version = subprocess.run([args.switchyard_bin, "--version"], capture_output=True, text=True).stdout.strip()
    identity = {"switchyard_commit": commit, "switchyard_tree_clean": not dirty.strip(), "switchyard_version": version}

    server_env = dict(os.environ, ANTHROPIC_API_KEY=key)
    with open(out / "switchyard-log.txt", "wb") as log:
        server = subprocess.Popen(
            [args.switchyard_bin, "--config", str(RIG / "config.toml"),
             "--host", "127.0.0.1", "--port", str(SERVER_PORT)],
            env=server_env, stdout=log, stderr=subprocess.STDOUT,
        )
    try:
        wait_port(SERVER_PORT)
        results = run_direct(out, key, args.runs) + run_minimal(out, args.runs)
        if args.claude:
            results += run_claude(out, args.runs)
    finally:
        server.terminate()
        server.wait()

    def count(case, predicate):
        return sum(1 for r in results if r["case"] == case and predicate(r))

    summary = {
        "direct_server_tool_ok": count("direct/server-tool", lambda r: r["status"] == 200 and r["searched"]),
        "direct_reencoded_tool_400": count("direct/reencoded-tool", lambda r: r["status"] == 400),
        "plan_forwarded_custom_tool_400": count(
            "minimal/sy-plan",
            lambda r: r["client_status"] == 400 and r["forwarded_web_search"] == [
                dict(REENCODED_TOOL)
            ],
        ),
        "pass_forwarded_server_tool_ok": count(
            "minimal/sy-pass",
            lambda r: r["client_status"] == 200 and r["forwarded_web_search"] == [SERVER_TOOL],
        ),
        "runs_per_case": args.runs,
    }
    expected = {
        "direct_server_tool_ok": args.runs,
        "direct_reencoded_tool_400": args.runs,
        "plan_forwarded_custom_tool_400": args.runs,
        "pass_forwarded_server_tool_ok": args.runs,
    }
    if args.claude:
        summary["claude_plan_websearch_failed"] = count(
            "claude/sy-plan",
            lambda r: r.get("upstream_status") == 400
            and r.get("forwarded_as_server_tool") is False
            and "input_schema" in r["result"],
        )
        summary["claude_pass_websearch_ok"] = count(
            "claude/sy-pass",
            lambda r: r.get("upstream_status") == 200
            and r.get("forwarded_as_server_tool") is True
            and r.get("search_results_returned") is True,
        )
        expected["claude_plan_websearch_failed"] = args.runs
        expected["claude_pass_websearch_ok"] = args.runs

    leaks = scan_for_key(out, key)
    (out / "results.json").write_text(
        json.dumps({"identity": identity, "summary": summary, "runs": results}, indent=2) + "\n"
    )
    print(json.dumps(identity))
    print(json.dumps(summary))
    if leaks:
        sys.exit(f"credential found in {len(leaks)} output files; do not commit {out}")
    if any(summary[name] != value for name, value in expected.items()):
        sys.exit("one or more cases did not match the claim")


if __name__ == "__main__":
    main()
