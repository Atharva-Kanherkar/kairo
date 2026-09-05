import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

PORT = 4010
BASE_URL = f"http://127.0.0.1:{PORT}"
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "litellm-leak.yaml")
LITELLM_BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../tools/litellm-env/bin/litellm"))
TRANSCRIPTS_DIR = os.path.abspath(os.path.dirname(__file__))

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
    req = urllib.request.Request(f"{BASE_URL}{path}")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read().decode("utf-8")

def main():
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

        for r in range(1, 6):
            # Probe /model/info
            status, body = http_get("/model/info")
            data = json.loads(body)
            last_model_info = data
            params = data["data"][0]["litellm_params"]
            has_canary = "CANARY_QUERY_KEY_IN_API_BASE" in params.get("api_base", "")
            if has_canary:
                results["model_info_canary_present"] += 1

            # Probe /v1/model/info
            status, body_v1 = http_get("/v1/model/info")
            data_v1 = json.loads(body_v1)
            last_model_info_v1 = data_v1
            params_v1 = data_v1["data"][0]["litellm_params"]
            has_canary_v1 = "CANARY_QUERY_KEY_IN_API_BASE" in params_v1.get("api_base", "")
            if has_canary_v1:
                results["model_info_v1_canary_present"] += 1

            # Control /v1/models
            status, body_models = http_get("/v1/models")
            last_models_control = json.loads(body_models)
            if "CANARY" not in body_models:
                results["models_control_clean"] += 1

            # Control /health/liveliness
            status, body_live = http_get("/health/liveliness")
            if "CANARY" not in body_live:
                results["liveliness_control_clean"] += 1

        print(f"Results across 5 runs: {results}")

        with open(os.path.join(TRANSCRIPTS_DIR, "model-info.json"), "w") as f:
            json.dump(last_model_info, f, indent=2)

        with open(os.path.join(TRANSCRIPTS_DIR, "model-info-v1.json"), "w") as f:
            json.dump(last_model_info_v1, f, indent=2)

        with open(os.path.join(TRANSCRIPTS_DIR, "models-control.json"), "w") as f:
            json.dump(last_models_control, f, indent=2)

        with open(os.path.join(TRANSCRIPTS_DIR, "client-results.json"), "w") as f:
            json.dump(results, f, indent=2)

        assert results["model_info_canary_present"] == 5
        assert results["model_info_v1_canary_present"] == 5
        assert results["models_control_clean"] == 5
        assert results["liveliness_control_clean"] == 5
        print("All 5/5 probes succeeded deterministically!")

    finally:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

if __name__ == "__main__":
    main()
