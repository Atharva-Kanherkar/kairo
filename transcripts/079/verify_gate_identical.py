#!/usr/bin/env python3
"""Verify the Bifrost Realtime admission gate is byte-identical on the tested
release commit and the current dev commit recorded in the issue writeup.

This backs the writeup's "current dev remains affected" claim with a rerunnable
check instead of a hash pasted into prose. It reads public source only and needs
no credential.
"""

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import urllib.request


RELEASE_COMMIT = "c193745d2a713e9f58f021d43e138df5eb7e038a"
DEV_COMMIT = "6fbefaf2d71b0386f9ec040ca43001372a22d6fc"
GATE_PATH = "transports/bifrost-http/handlers/realtimeauthgate.go"
EXPECTED_SHA256 = "7c2726032eeb131ed7df6518dc0813608f96e92a857e4a25ab7d4cfef8e13b6f"
RAW_URL = "https://raw.githubusercontent.com/maximhq/bifrost/{commit}/{path}"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def fetch(commit):
    url = RAW_URL.format(commit=commit, path=GATE_PATH)
    with urllib.request.urlopen(url, timeout=30) as response:
        require(response.status == 200, f"unexpected status {response.status} for {url}")
        return response.read()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("gate-identity.json"),
    )
    args = parser.parse_args()

    digests = {}
    sizes = {}
    for label, commit in (("release", RELEASE_COMMIT), ("dev", DEV_COMMIT)):
        body = fetch(commit)
        digests[label] = hashlib.sha256(body).hexdigest()
        sizes[label] = len(body)

    require(
        digests["release"] == digests["dev"],
        f"admission gate is not byte-identical across commits: {digests}",
    )
    require(
        digests["release"] == EXPECTED_SHA256,
        f"gate sha256 {digests['release']} does not match the recorded "
        f"{EXPECTED_SHA256}; the writeup needs updating",
    )

    result = {
        "path": GATE_PATH,
        "release_commit": RELEASE_COMMIT,
        "dev_commit": DEV_COMMIT,
        "sha256": digests["release"],
        "bytes": sizes["release"],
        "byte_identical": True,
        "checked": datetime.date.today().isoformat(),
        "source": "raw.githubusercontent.com/maximhq/bifrost",
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print("VERDICT: release and dev admission gates are byte-identical")


if __name__ == "__main__":
    main()
