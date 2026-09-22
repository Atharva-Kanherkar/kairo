"""Key-safe direct Anthropic adaptive-thinking control for kairo 081.

The script uses only the official HTTPS API. It records sanitized raw HTTP
request and response bytes and never records the API key or request IDs.
"""
import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path


API = "https://api.anthropic.com"
VERSION = "2023-06-01"
SYNTHETIC_PROMPT = "Synthetic kairo 081 adaptive-thinking control. Reply with one short sentence."


def sanitize(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = key.lower()
            private_id = (
                lowered in {"request_id", "request-id", "container", "message_id"}
                or (lowered == "id" and isinstance(item, str) and item.startswith("msg_"))
            )
            if private_id:
                result[key] = "[REDACTED_REQUEST_IDENTIFIER]"
            else:
                result[key] = sanitize(item)
        return result
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"req_[A-Za-z0-9_-]+", "[REDACTED_REQUEST_IDENTIFIER]", value)
    return value


def request_bytes(method, path, headers, body):
    lines = [f"{method} {path} HTTP/1.1", "Host: api.anthropic.com"]
    for key, value in headers.items():
        if key.lower() == "x-api-key":
            value = "[REDACTED_ANTHROPIC_API_KEY]"
        elif "request" in key.lower() or key.lower() == "traceparent":
            value = "[REDACTED_REQUEST_IDENTIFIER]"
        lines.append(f"{key}: {value}")
    separator = "\n\n" if body else "\n"
    suffix = "\n" if body else ""
    return ("\n".join(lines) + separator + body.decode() + suffix).encode()


def response_bytes(status, headers, body):
    lines = [f"HTTP/1.1 {status}"]
    for key, value in headers.items():
        lowered = key.lower()
        private_markers = (
            "request", "trace", "organization", "workspace", "account",
            "tenant", "ratelimit", "cf-ray",
        )
        if any(marker in lowered for marker in private_markers):
            value = "[REDACTED_PRIVATE_METADATA]"
        lines.append(f"{key}: {value}")
    return ("\n".join(lines) + "\n\n" + body.decode(errors="replace") + "\n").encode()


def call(path, body, key):
    encoded = json.dumps(body, separators=(",", ":")).encode()
    headers = {
        "x-api-key": key,
        "anthropic-version": VERSION,
        "content-type": "application/json",
        "accept": "application/json",
    }
    request = urllib.request.Request(API + path, data=encoded, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=90) as reply:
            raw = reply.read()
            return reply.status, dict(reply.headers), raw, request_bytes("POST", path, headers, encoded)
    except urllib.error.HTTPError as error:
        raw = error.read()
        return error.code, dict(error.headers), raw, request_bytes("POST", path, headers, encoded)


def discover_model(key):
    headers = {"x-api-key": key, "anthropic-version": VERSION, "accept": "application/json"}
    request = urllib.request.Request(API + "/v1/models", headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=30) as reply:
            raw = reply.read()
            return reply.status, dict(reply.headers), raw, request_bytes("GET", "/v1/models", headers, b"")
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read(), request_bytes("GET", "/v1/models", headers, b"")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--trials", type=int, default=5)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        (output / "live-anthropic-summary.json").write_text(json.dumps({
            "status": "blocked", "credential_present": False,
            "blocker": "ANTHROPIC_API_KEY is not set",
        }, indent=2) + "\n", encoding="utf-8")
        print("live Anthropic control blocked: ANTHROPIC_API_KEY is not set")
        return 2

    model_status, model_headers, model_raw, model_request = discover_model(key)
    (output / "live-anthropic-models-request.http").write_bytes(model_request)
    try:
        model_json = json.loads(model_raw)
    except json.JSONDecodeError:
        model_json = {"raw": model_raw.decode(errors="replace")}
    (output / "live-anthropic-models-response.http").write_bytes(
        response_bytes(model_status, model_headers, json.dumps(sanitize(model_json), separators=(",", ":")).encode())
    )
    models = [item.get("id") for item in model_json.get("data", []) if isinstance(item, dict) and item.get("id")]
    preferred = next((name for name in models if "sonnet-4" in name or "opus-4" in name), None)
    model = os.environ.get("ANTHROPIC_MODEL") or preferred or (models[0] if models else None)
    selected = next((item for item in model_json.get("data", []) if item.get("id") == model), {})
    adaptive_supported = (
        selected.get("capabilities", {})
        .get("thinking", {})
        .get("types", {})
        .get("adaptive", {})
        .get("supported")
        is True
    )
    summary = {
        "status": "blocked",
        "credential_present": True,
        "model_discovery_status": model_status,
        "model": model,
        "adaptive_thinking_advertised": adaptive_supported,
        "trials": [],
    }
    if not model:
        summary["blocker"] = "authenticated models endpoint returned no usable model"
        (output / "live-anthropic-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"live Anthropic control blocked: models endpoint HTTP {model_status}")
        return 2

    body = {
        "model": model,
        "max_tokens": 128,
        "thinking": {"type": "adaptive"},
        "messages": [{"role": "user", "content": SYNTHETIC_PROMPT}],
    }
    for trial in range(1, args.trials + 1):
        status, headers, raw, request = call("/v1/messages", body, key)
        (output / f"live-anthropic-trial-{trial}-request.http").write_bytes(request)
        try:
            parsed = sanitize(json.loads(raw))
            body_bytes = json.dumps(parsed, separators=(",", ":")).encode()
        except json.JSONDecodeError:
            body_bytes = raw
        (output / f"live-anthropic-trial-{trial}-response.http").write_bytes(
            response_bytes(status, headers, body_bytes)
        )
        structural = False
        if status == 200:
            try:
                response = json.loads(raw)
                structural = response.get("type") == "message" and isinstance(response.get("content"), list)
            except json.JSONDecodeError:
                pass
        summary["trials"].append({"trial": trial, "status": status, "structural_response": structural})
    successful = sum(item["structural_response"] for item in summary["trials"])
    summary["status"] = "pass" if adaptive_supported and successful == args.trials else "blocked"
    if summary["status"] != "pass":
        summary["blocker"] = (
            "the selected model did not advertise adaptive thinking or the direct Anthropic "
            "endpoint did not return a structural 200 message for every trial"
        )
    (output / "live-anthropic-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"live Anthropic control: {successful}/{args.trials} structural responses (model discovery HTTP {model_status})")
    return 0 if summary["status"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
