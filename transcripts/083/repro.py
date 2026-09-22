#!/usr/bin/env python3
import http.client
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
HOST = "127.0.0.1:19312"


def request(scope, body):
    raw = json.dumps(body, separators=(",", ":"))
    connection = http.client.HTTPConnection(HOST)
    connection.request(
        "POST",
        "/v1/responses",
        body=raw,
        headers={"content-type": "application/json"},
    )
    response = connection.getresponse()
    response_raw = response.read().decode()
    connection.close()
    if response.status != 200:
        raise RuntimeError(f"Switchyard returned {response.status}: {response_raw}")
    return raw, response_raw, json.loads(response_raw)


def run(mode, trial):
    marker = "BUG" if mode == "bug" else "CONTROL"
    seed_canary = f"CONVERSATION {marker} SEED_CANARY_081_{trial}"
    conversation_id = f"conv_081_{mode}_{trial}"
    seed = {
        "model": "switchyard/chat",
        "input": seed_canary,
        "conversation": conversation_id,
    }
    _, _, seed_response = request("caller", seed)
    follow = {
        "model": "switchyard/chat",
        "input": f"CONVERSATION {marker} RECALL_{trial}",
    }
    if mode == "bug":
        follow["conversation"] = conversation_id
    else:
        follow["previous_response_id"] = seed_response["id"]
    request_raw, response_raw, response = request("caller", follow)
    preserved = seed_canary in response_raw
    record = {
        "trial": trial,
        "mode": mode,
        "conversation_id": conversation_id,
        "seed_canary": seed_canary,
        "client_request_body_raw": request_raw,
        "client_response_body_raw": response_raw,
        "seed_canary_in_continuation_response": preserved,
        "response_id": response["id"],
    }
    return record


def main():
    for name in ["capture-bug.jsonl", "capture-control.jsonl"]:
        (ROOT / name).write_text("")
    counts = {}
    for mode in ["bug", "control"]:
        records = [run(mode, trial) for trial in range(1, 6)]
        (ROOT / f"capture-{mode}.jsonl").write_text(
            "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records)
        )
        counts[mode] = sum(record["seed_canary_in_continuation_response"] for record in records)
    print(json.dumps({"bug_preserved": counts["bug"], "control_preserved": counts["control"]}))
    if counts != {"bug": 0, "control": 5}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
