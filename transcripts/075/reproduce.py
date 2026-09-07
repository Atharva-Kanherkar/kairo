#!/usr/bin/env python3
"""Record issue 075 against real Bifrost, calling live Gemini.

Bifrost's Gemini chat-completions dialect converter silently drops
`inlineData` parts (base64 image/audio blobs) both in the unary path
(ToBifrostChatResponse) and the streaming path (ToBifrostChatCompletionStream).
This script proves it with real Gemini image-generation output.

Wire evidence strategy: rather than a man-in-the-middle relay, this script
turns on Bifrost's own `send_back_raw_request` / `send_back_raw_response`
provider options. Bifrost then embeds the exact bytes it sent to Gemini and
the exact bytes Gemini sent back inside its own API response
(`extra_fields.raw_request` / `extra_fields.raw_response`). That gives us
forwarded-request and upstream-response wire evidence without building a
proxy, and ties it unambiguously to the same HTTP exchange the client saw.

Only GEMINI_API_KEY is read from the environment or .env. It is never
written to a transcript: Bifrost sends the key as a header, not inside the
JSON body, and this script greps every captured byte string for the key
value before persisting, refusing to write if it appears.

Base64 image payloads are large (~1-1.5 MB per image). To keep the repo
lean, any base64 blob longer than TRUNCATE_THRESHOLD is truncated in the
stored transcript to a short prefix/suffix plus the sha256 and original
length of the full value, computed before truncation. This preserves proof
that real image bytes were present (or absent) without bloating the repo.
Re-running this script reproduces the full untruncated bytes locally.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import subprocess
import time
import urllib.request
import urllib.error

TRANSIENT_ERRORS = (socket.timeout, TimeoutError, urllib.error.URLError, ConnectionError)
from datetime import datetime, timezone
from pathlib import Path

MODEL = "gemini-2.5-flash-image"
PROMPT = "Generate an image of a small red apple on a white background."
RUNS = 3
MAX_PRECONDITION_RETRIES = 5
TRUNCATE_THRESHOLD = 500
KEEP_PREFIX = 120
KEEP_SUFFIX = 40


class ReproductionError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReproductionError(message)


def sha256_hex(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def truncate_long_strings(obj, secret):
    """Recursively replace long base64-shaped string values with a
    sha256+length marker. Applied identically everywhere so raw and parsed
    copies of the same payload stay byte-identical to each other."""
    if isinstance(obj, str):
        require(not secret or secret not in obj, "credential appeared in captured value")
        if len(obj) > TRUNCATE_THRESHOLD:
            # Bifrost embeds raw provider JSON as a *string* field (e.g.
            # extra_fields.raw_response), not a nested object. If this long
            # string is itself JSON, recurse into it and re-serialize so the
            # truncation lands on the actual base64 leaf, not on raw text that
            # happens to contain JSON syntax (which would corrupt it).
            try:
                nested = json.loads(obj)
            except (json.JSONDecodeError, TypeError):
                nested = None
            if isinstance(nested, (dict, list)):
                return json.dumps(truncate_long_strings(nested, secret))
            digest = sha256_hex(obj)
            prefix = obj[:KEEP_PREFIX]
            suffix = obj[-KEEP_SUFFIX:]
            return (
                f"{prefix}...TRUNCATED-FOR-REPO-SIZE"
                f"(sha256={digest},orig_len={len(obj)})...{suffix}"
            )
        return obj
    if isinstance(obj, dict):
        return {k: truncate_long_strings(v, secret) for k, v in obj.items()}
    if isinstance(obj, list):
        return [truncate_long_strings(v, secret) for v in obj]
    return obj


def sanitize_sse_string(raw_text, secret):
    """Sanitize a text/event-stream body line by line: each `data: {...}`
    payload is parsed and truncated independently, since the stream as a
    whole is not one JSON document. Heartbeat comments and [DONE] pass
    through unchanged (they carry no secrets or long blobs)."""
    require(not secret or secret not in raw_text, "credential appeared in SSE capture")
    out_lines = []
    for line in raw_text.split("\n"):
        if line.startswith("data: ") and line[len("data: "):].strip() != "[DONE]":
            payload = line[len("data: "):]
            out_lines.append("data: " + sanitize_json_string(payload, secret))
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def sanitize_json_string(raw_text, secret):
    """Parse a JSON string, truncate long values, and re-serialize. If it
    isn't valid JSON (shouldn't happen for our captures), just check for the
    secret and truncate as a flat string."""
    try:
        parsed = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        require(not secret or secret not in str(raw_text), "credential in non-JSON capture")
        return raw_text
    sanitized = truncate_long_strings(parsed, secret)
    return json.dumps(sanitized, ensure_ascii=True)


def find_inline_image(parsed_gemini_response):
    """Return (mime_type, data_len) of the first inlineData image part found
    in a parsed Gemini generateContent response, or None."""
    if not isinstance(parsed_gemini_response, dict):
        return None
    for candidate in parsed_gemini_response.get("candidates", []) or []:
        content = candidate.get("content") or {}
        for part in content.get("parts", []) or []:
            inline = part.get("inlineData")
            if inline and inline.get("data"):
                return (inline.get("mimeType"), len(inline["data"]))
    return None


def verify_binary(binary, expected_revision):
    text = subprocess.check_output(["go", "version", "-m", binary], text=True)
    require(f"vcs.revision={expected_revision}" in text, "binary does not match pinned commit")
    digest = hashlib.sha256()
    with open(binary, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    modified = "vcs.modified=true" in text
    return {
        "revision": expected_revision,
        "sha256": digest.hexdigest(),
        "go_build_info": text,
        "vcs_modified": modified,
        "vcs_modified_reason": (
            "go's build tooling added one dependency hash line to the tracked file "
            "transports/go.sum (github.com/maximhq/bifrost/plugins/mocker "
            "v1.5.37/go.mod, an already-pinned dependency) during the build, which is "
            "what git and Go's embedded vcs.modified flag see as a local "
            "modification; verified with `git status --short` (M transports/go.sum) "
            "and `git diff transports/go.sum`. The untracked placeholder "
            "ui/index.html added to satisfy the //go:embed all:ui directive is NOT "
            "the cause: it lives under transports/bifrost-http/ui/, which is "
            "gitignored (`git check-ignore -v transports/bifrost-http/ui/index.html` "
            "matches .gitignore:21 `/transports/bifrost-http/ui/`), so git and go's "
            "vcs.modified detection never see it. Neither cause touches "
            "core/providers/gemini/chat.go or any other backend code under test."
            if modified
            else None
        ),
    }


def load_key(env_file):
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    if os.path.exists(env_file):
        for line in Path(env_file).read_text().splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise ReproductionError("GEMINI_API_KEY is required")


def wait_for_ready(url, proc, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require(proc.poll() is None, f"bifrost process exited early with code {proc.returncode}")
        try:
            req = urllib.request.Request(url + "/api/health")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.3)
    raise ReproductionError("Bifrost did not become ready")


def http_post(url, path, body, secret, headers=None, timeout=100):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url + path,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    request_capture = {
        "method": "POST",
        "path": path,
        "headers": {"content-type": "application/json"},
        "body_raw": sanitize_json_string(data.decode("utf-8"), secret),
    }
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            status = resp.status
            resp_headers = {
                k.lower(): v
                for k, v in resp.getheaders()
                if k.lower() in ("content-type", "date")
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        resp_headers = {}
    text = raw.decode("utf-8", errors="replace")
    require(not secret or secret not in text, "credential appeared in client response body")
    response_capture = {
        "status": status,
        "headers": resp_headers,
        "body_raw": sanitize_json_string(text, secret),
    }
    return request_capture, response_capture, text


def http_post_stream(url, path, body, secret, timeout=100):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    request_capture = {
        "method": "POST",
        "path": path,
        "headers": {"content-type": "application/json"},
        "body_raw": sanitize_json_string(data.decode("utf-8"), secret),
    }
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    text = raw.decode("utf-8", errors="replace")
    require(not secret or secret not in text, "credential appeared in client stream body")
    response_capture = {
        "status": status,
        "headers": {},
        "body_raw": sanitize_sse_string(text, secret),
    }
    return request_capture, response_capture, text


def parse_sse_chunks(stream_text):
    """Split a captured text/event-stream body into JSON chunk dicts,
    skipping heartbeat comments and the terminal [DONE]."""
    chunks = []
    for line in stream_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[len("data: "):]
        if payload.strip() == "[DONE]":
            continue
        try:
            chunks.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return chunks


def direct_gemini_call(secret):
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    )
    body = {"contents": [{"parts": [{"text": PROMPT}]}]}
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url + f"?key={secret}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    request_capture = {
        "method": "POST",
        "path": f"/v1beta/models/{MODEL}:generateContent",
        "headers": {"content-type": "application/json"},
        "body_raw": sanitize_json_string(data.decode("utf-8"), secret),
    }
    try:
        with urllib.request.urlopen(req, timeout=100) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    text = raw.decode("utf-8", errors="replace")
    require(secret not in text, "credential leaked into direct Gemini response body")
    require(status == 200, f"direct Gemini call failed with status {status}")
    parsed = json.loads(text)
    response_capture = {
        "status": status,
        "headers": {},
        "body_raw": sanitize_json_string(text, secret),
    }
    return request_capture, response_capture, parsed


def write_record(output_dir, filename, record):
    path = Path(output_dir) / filename
    with path.open("a") as stream:
        stream.write(json.dumps(record, ensure_ascii=True) + "\n")


def run(args):
    secret = load_key(args.env_file)
    require(os.path.exists(args.bifrost_bin), f"binary not found at {args.bifrost_bin}")
    build_info = verify_binary(args.bifrost_bin, args.revision)

    output = Path(args.output_dir)
    require(not output.exists() or not any(output.iterdir()), "output directory contains evidence; use a fresh directory")
    live_dir = output / "live"
    live_dir.mkdir(parents=True, exist_ok=True)

    app_dir = Path(args.app_dir)
    app_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "$schema": "https://www.getbifrost.ai/schema",
        "providers": {
            "gemini": {
                "keys": [
                    {"name": "gemini-key", "value": "env.GEMINI_API_KEY", "models": ["*"]}
                ],
                "send_back_raw_request": True,
                "send_back_raw_response": True,
                "network_config": {"default_request_timeout_in_seconds": 120},
            }
        },
    }
    (app_dir / "config.json").write_text(json.dumps(config, indent=2))
    (output / "config.json").write_text(json.dumps(config, indent=2) + "\n")

    metadata = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live-gemini",
        "model": MODEL,
        "runs_per_case": RUNS,
        "target": build_info,
        "credential_variable": "GEMINI_API_KEY",
        "upstream_origin": "https://generativelanguage.googleapis.com (direct control) and via Bifrost for the chat/responses cases",
        "complete": False,
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")

    child_env = {
        k: v for k, v in os.environ.items() if "GEMINI_API_KEY" != k
    }
    child_env["GEMINI_API_KEY"] = secret
    proc = subprocess.Popen(
        [
            args.bifrost_bin,
            "-host", "127.0.0.1",
            "-app-dir", str(app_dir),
            "-port", str(args.port),
            "-log-level", "warn",
        ],
        env=child_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{args.port}"
    try:
        wait_for_ready(url, proc)

        results = {"chat-nonstream": [], "chat-stream": [], "responses-control": [], "direct-gemini-control": []}

        for trial in range(1, RUNS + 1):
            # --- Case 1: chat completions, non-streaming (the bug) ---
            # Gemini image generation is not deterministic: it occasionally
            # answers with text only. That is model nondeterminism, not the
            # gateway defect under test, so retry until the precondition
            # (Gemini actually generated an image this call) holds, same as
            # we would for any other flaky-provider-response precondition.
            body = {"model": MODEL, "messages": [{"role": "user", "content": PROMPT}]}
            image_in_upstream = None
            for attempt in range(MAX_PRECONDITION_RETRIES):
                try:
                    client_req, client_resp, raw_text = http_post(url, "/v1/chat/completions", body, secret)
                    parsed = json.loads(raw_text)
                except TRANSIENT_ERRORS as exc:
                    print(f"run {trial}: nonstream attempt {attempt + 1} hit transient error {type(exc).__name__}, retrying", flush=True)
                    continue
                ef = parsed.get("extra_fields", {})
                raw_request = ef.get("raw_request")
                raw_response = ef.get("raw_response")
                if isinstance(raw_response, str):
                    raw_response_parsed = json.loads(raw_response)
                else:
                    raw_response_parsed = raw_response
                image_in_upstream = find_inline_image(raw_response_parsed)
                if image_in_upstream is not None:
                    break
                print(f"run {trial}: nonstream attempt {attempt + 1} produced no image from Gemini, retrying", flush=True)
            content = parsed["choices"][0]["message"].get("content")
            image_tokens = (
                parsed.get("usage", {}).get("completion_tokens_details", {}).get("image_tokens", 0)
            )
            record = {
                "case": "chat-nonstream",
                "run": trial,
                "path": "/v1/chat/completions",
                "client_request": client_req,
                "client_response": client_resp,
                "forwarded_request": sanitize_json_string(json.dumps(raw_request), secret) if raw_request is not None else None,
                "upstream_response": sanitize_json_string(json.dumps(raw_response_parsed), secret),
                "image_present_in_upstream": image_in_upstream is not None,
                "image_tokens_billed": image_tokens,
                "image_content_block_in_client_response": (
                    isinstance(parsed["choices"][0]["message"].get("content"), list)
                    and any(
                        b.get("type") in ("image_url", "input_audio")
                        for b in parsed["choices"][0]["message"]["content"]
                    )
                ) if isinstance(content, list) else False,
                "client_response_content_str": content if isinstance(content, str) else None,
            }
            write_record(live_dir, "chat-nonstream.jsonl", record)
            results["chat-nonstream"].append(record)
            require(image_in_upstream is not None, "control precondition failed: Gemini did not return an image this run")
            require(not record["image_content_block_in_client_response"], "bug did not reproduce: image block present in chat completions response")

            # --- Case 2: chat completions, streaming (the bug, streaming path) ---
            stream_image_found = False
            stream_image_leaked_to_delta = False
            chunks = []
            for attempt in range(MAX_PRECONDITION_RETRIES):
                try:
                    client_req_s, client_resp_s, stream_text = http_post_stream(
                        url, "/v1/chat/completions", {**body, "stream": True}, secret
                    )
                except TRANSIENT_ERRORS as exc:
                    print(f"run {trial}: stream attempt {attempt + 1} hit transient error {type(exc).__name__}, retrying", flush=True)
                    continue
                chunks = parse_sse_chunks(stream_text)
                stream_image_found = False
                stream_image_leaked_to_delta = False
                for chunk in chunks:
                    ef_c = chunk.get("extra_fields", {})
                    rr = ef_c.get("raw_response")
                    if isinstance(rr, str):
                        try:
                            rr_parsed = json.loads(rr)
                        except json.JSONDecodeError:
                            rr_parsed = None
                    else:
                        rr_parsed = rr
                    found = find_inline_image(rr_parsed) if rr_parsed else None
                    if found:
                        stream_image_found = True
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        dcontent = delta.get("content")
                        if isinstance(dcontent, list) and any(
                            b.get("type") in ("image_url", "input_audio") for b in dcontent
                        ):
                            stream_image_leaked_to_delta = True
                if stream_image_found:
                    break
                print(f"run {trial}: stream attempt {attempt + 1} produced no image from Gemini, retrying", flush=True)
            record_s = {
                "case": "chat-stream",
                "run": trial,
                "path": "/v1/chat/completions (stream=true)",
                "client_request": client_req_s,
                "client_response": client_resp_s,
                "chunk_count": len(chunks),
                "image_present_in_upstream_chunk": stream_image_found,
                "image_content_block_in_any_delta": stream_image_leaked_to_delta,
            }
            write_record(live_dir, "chat-stream.jsonl", record_s)
            results["chat-stream"].append(record_s)
            require(stream_image_found, "control precondition failed: no streamed chunk carried an inlineData image this run")
            require(not stream_image_leaked_to_delta, "bug did not reproduce: image block present in a streaming delta")

            # --- Case 3: responses API through Bifrost (control, same gateway) ---
            resp_body = {"model": MODEL, "input": PROMPT}
            found_image_block = False
            for attempt in range(MAX_PRECONDITION_RETRIES):
                try:
                    client_req_r, client_resp_r, raw_text_r = http_post(url, "/v1/responses", resp_body, secret)
                    parsed_r = json.loads(raw_text_r)
                except TRANSIENT_ERRORS as exc:
                    print(f"run {trial}: responses-control attempt {attempt + 1} hit transient error {type(exc).__name__}, retrying", flush=True)
                    continue
                found_image_block = False
                for item in parsed_r.get("output", []) or []:
                    for block in item.get("content", []) or []:
                        if block.get("type") in ("input_image", "output_image", "image"):
                            found_image_block = True
                if found_image_block:
                    break
                print(f"run {trial}: responses-control attempt {attempt + 1} produced no image from Gemini, retrying", flush=True)
            record_r = {
                "case": "responses-control",
                "run": trial,
                "path": "/v1/responses",
                "client_request": client_req_r,
                "client_response": client_resp_r,
                "image_content_block_present": found_image_block,
            }
            write_record(live_dir, "responses-control.jsonl", record_r)
            results["responses-control"].append(record_r)
            require(found_image_block, "control failed: /v1/responses did not surface the image either")

            # --- Case 4: direct Gemini call, bypassing Bifrost entirely (control) ---
            direct_image = None
            for attempt in range(MAX_PRECONDITION_RETRIES):
                try:
                    dreq, dresp, dparsed = direct_gemini_call(secret)
                except TRANSIENT_ERRORS as exc:
                    print(f"run {trial}: direct-gemini-control attempt {attempt + 1} hit transient error {type(exc).__name__}, retrying", flush=True)
                    continue
                direct_image = find_inline_image(dparsed)
                if direct_image is not None:
                    break
                print(f"run {trial}: direct-gemini-control attempt {attempt + 1} produced no image from Gemini, retrying", flush=True)
            record_d = {
                "case": "direct-gemini-control",
                "run": trial,
                "path": f"/v1beta/models/{MODEL}:generateContent",
                "client_request": dreq,
                "client_response": dresp,
                "image_present": direct_image is not None,
                "image_mime_type": direct_image[0] if direct_image else None,
                "image_data_len": direct_image[1] if direct_image else None,
            }
            write_record(live_dir, "direct-gemini-control.jsonl", record_d)
            results["direct-gemini-control"].append(record_d)
            require(direct_image is not None, "control failed: direct Gemini call returned no image")

            print(f"run {trial}/{RUNS}: bug reproduced (chat unary+stream dropped image), both controls show the image", flush=True)

        metadata["complete"] = True
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        print(f"{RUNS}/{RUNS} runs: chat completions (unary and streaming) dropped the image; both controls preserved it.")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=str(Path(__file__).resolve().parents[2] / ".env"))
    parser.add_argument("--output-dir", required=True, help="New directory; existing evidence is never overwritten")
    parser.add_argument("--app-dir", default="/tmp/kairo-075-appdir")
    parser.add_argument("--bifrost-bin", default=os.environ.get("BIFROST_BIN", "/tmp/bifrost-http"))
    parser.add_argument("--revision", default="e1045c07eb81bab18df2243f3f421f48450848ff")
    parser.add_argument("--port", type=int, default=18075)
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, ReproductionError) else type(exc).__name__
        print(f"Reproduction failed: {detail}. Inspect sanitized captures, not credentials.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
