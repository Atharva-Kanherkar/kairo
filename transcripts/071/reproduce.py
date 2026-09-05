import argparse
import http.client
import json
import os
import signal
import subprocess
import sys
import time
import tempfile
import urllib.request

PORT = 4010
BASE_URL = f"http://127.0.0.1:{PORT}"
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "litellm-leak.yaml")
LITELLM_BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../tools/litellm-env/bin/litellm"))

def wait_for_ready(timeout=20):
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.Request(f"{BASE_URL}/health/liveliness")
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False

def http_get(path):
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
    conn.putheader("Host", f"127.0.0.1:{PORT}")
    conn.endheaders()
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    request_bytes = f"GET {path} HTTP/1.1\nHost: 127.0.0.1:{PORT}\n\n"
    response_headers = "".join(
        f"{key}: {value}\n" for key, value in resp.getheaders()
    )
    response_bytes = (
        f"HTTP/1.1 {resp.status} {resp.reason}\n{response_headers}\n{body}"
    )
    conn.close()
    return resp.status, body, request_bytes + response_bytes


def write_text(output_dir, name, content):
    with open(os.path.join(output_dir, name), "w", encoding="utf-8") as f:
        f.write(content)


def write_json(output_dir, name, content):
    with open(os.path.join(output_dir, name), "w", encoding="utf-8") as f:
        json.dump(content, f, indent=2)
        f.write("\n")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        help="capture directory; defaults to a new temporary reviewer directory",
    )
    args = parser.parse_args()
    output_dir = os.path.abspath(args.output_dir or tempfile.mkdtemp(prefix="kairo-071-"))
    os.makedirs(output_dir, exist_ok=True)
    cmd = [LITELLM_BIN, "--config", CONFIG_PATH, "--port", str(PORT)]
    print(f"Starting LiteLLM: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_for_ready():
            print("LiteLLM failed to start within timeout", file=sys.stderr)
            sys.exit(1)

        print("LiteLLM is ready. Running 5/5 determinism probes...")
        results = {
            "model_info_canary_present": 0,
            "model_info_v1_canary_present": 0,
            "models_control_clean": 0,
            "liveliness_control_clean": 0,
            "runs": 5,
        }

        last_model_info = None
        last_model_info_v1 = None
        last_models_control = None
        last_http = {}

        for r in range(1, 6):
            # Probe /model/info
            status, body, raw_http = http_get("/model/info")
            assert status == 200
            last_http["model-info.http"] = raw_http
            data = json.loads(body)
            last_model_info = data
            params = data["data"][0]["litellm_params"]
            has_canary = "CANARY_QUERY_KEY_IN_API_BASE" in params.get("api_base", "")
            if has_canary:
                results["model_info_canary_present"] += 1

            # Probe /v1/model/info
            status, body_v1, raw_http_v1 = http_get("/v1/model/info")
            assert status == 200
            last_http["model-info-v1.http"] = raw_http_v1
            data_v1 = json.loads(body_v1)
            last_model_info_v1 = data_v1
            params_v1 = data_v1["data"][0]["litellm_params"]
            has_canary_v1 = "CANARY_QUERY_KEY_IN_API_BASE" in params_v1.get("api_base", "")
            if has_canary_v1:
                results["model_info_v1_canary_present"] += 1

            # Control /v1/models
            status, body_models, raw_models = http_get("/v1/models")
            assert status == 200
            last_http["models-control.http"] = raw_models
            last_models_control = json.loads(body_models)
            if "CANARY" not in body_models:
                results["models_control_clean"] += 1

            # Control /health/liveliness
            status, body_live, raw_live = http_get("/health/liveliness")
            assert status == 200
            last_http["liveliness-control.http"] = raw_live
            if "CANARY" not in body_live:
                results["liveliness_control_clean"] += 1

        print(f"Results across 5 runs: {results}")

        write_json(output_dir, "model-info.json", last_model_info)
        write_json(output_dir, "model-info-v1.json", last_model_info_v1)
        write_json(output_dir, "models-control.json", last_models_control)
        write_json(output_dir, "client-results.json", results)
        for name, raw_http in last_http.items():
            write_text(output_dir, name, raw_http)

        assert results["model_info_canary_present"] == 5
        assert results["model_info_v1_canary_present"] == 5
        assert results["models_control_clean"] == 5
        assert results["liveliness_control_clean"] == 5
        print("All 5/5 probes succeeded deterministically!")
        print(f"Sanitized captures written to: {output_dir}")

    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

if __name__ == "__main__":
    main()
