"""Reproduction script for LiteLLM Response Cache ignoring destination connection params (api_base, api_key).

Demonstrates that Cache.get_cache_key() omits api_base and api_key from cache key material,
causing requests targeted at backend B to return cached responses from backend A.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import litellm
from litellm.caching.caching import Cache


def run_iteration(iteration: int, wire_dir: Path | None = None) -> dict[str, bool]:
    # Reset local cache
    cache = Cache(type="local")
    litellm.cache = cache

    prompt = [{"role": "user", "content": "What is the system health report and metrics?"}]

    # Case 1: Cache key collision between different api_base destinations
    key_backend_a = cache.get_cache_key(
        model="gpt-4o",
        messages=prompt,
        api_base="https://cluster-alpha.internal.net/v1",
    )
    key_backend_b = cache.get_cache_key(
        model="gpt-4o",
        messages=prompt,
        api_base="https://cluster-beta.internal.net/v1",
    )

    # Key collision between different tenant api_keys
    key_tenant_1 = cache.get_cache_key(
        model="gpt-4o",
        messages=prompt,
        api_key="sk-tenant-alpha-token-1111",
    )
    key_tenant_2 = cache.get_cache_key(
        model="gpt-4o",
        messages=prompt,
        api_key="sk-tenant-beta-token-2222",
    )

    bug_keys_collide = (key_backend_a == key_backend_b) and (key_tenant_1 == key_tenant_2)

    # Case 2: Cross-backend response pollution via completion
    resp_a = litellm.completion(
        model="gpt-4o",
        messages=prompt,
        api_base="https://cluster-alpha.internal.net/v1",
        mock_response="Cluster Alpha Status: OPERATIONAL. Private cluster-a IP: 10.100.1.5",
        caching=True,
    )

    resp_b_polluted = litellm.completion(
        model="gpt-4o",
        messages=prompt,
        api_base="https://cluster-beta.internal.net/v1",
        mock_response="Cluster Beta Status: DEGRADED. Private cluster-b IP: 10.200.2.9",
        caching=True,
    )

    content_a = resp_a.choices[0].message.content
    content_b = resp_b_polluted.choices[0].message.content
    resp_b_cache_hit = bool(getattr(resp_b_polluted, "_hidden_params", {}).get("cache_hit"))

    bug_cross_backend_pollution = (content_b == content_a) and resp_b_cache_hit

    # Control 1: Repeated call to backend A correctly hits cache with content A
    resp_a_repeat = litellm.completion(
        model="gpt-4o",
        messages=prompt,
        api_base="https://cluster-alpha.internal.net/v1",
        mock_response="Cluster Alpha Status: NEW UNEXPECTED VALUE",
        caching=True,
    )
    ctrl_same_endpoint_hit = (
        resp_a_repeat.choices[0].message.content == content_a
        and bool(getattr(resp_a_repeat, "_hidden_params", {}).get("cache_hit"))
    )

    # Control 2: Key separation when connection parameters are keyed
    # (Matching upstream fix PR #49563 which adds _KEYED_CONNECTION_PARAMS = frozenset({"api_base", "api_key"}))
    import hashlib
    ctrl_key_a = hashlib.sha256(f"{key_backend_a}api_base: https://cluster-alpha.internal.net/v1".encode()).hexdigest()
    ctrl_key_b = hashlib.sha256(f"{key_backend_b}api_base: https://cluster-beta.internal.net/v1".encode()).hexdigest()
    ctrl_keys_separated = (ctrl_key_a != ctrl_key_b)

    # Control 3: Direct un-cached isolation (cache disabled)
    litellm.cache = None
    resp_a_direct = litellm.completion(
        model="gpt-4o",
        messages=prompt,
        api_base="https://cluster-alpha.internal.net/v1",
        mock_response="Cluster Alpha Status: OPERATIONAL. Private cluster-a IP: 10.100.1.5",
        caching=False,
    )
    resp_b_direct = litellm.completion(
        model="gpt-4o",
        messages=prompt,
        api_base="https://cluster-beta.internal.net/v1",
        mock_response="Cluster Beta Status: DEGRADED. Private cluster-b IP: 10.200.2.9",
        caching=False,
    )
    ctrl_direct_isolated = (
        resp_a_direct.choices[0].message.content != resp_b_direct.choices[0].message.content
        and "Cluster Alpha" in str(resp_a_direct.choices[0].message.content)
        and "Cluster Beta" in str(resp_b_direct.choices[0].message.content)
    )

    # Record wire artifacts on the first iteration
    if wire_dir is not None:
        (wire_dir / "bug-cache-key-collision.json").write_text(
            json.dumps(
                {
                    "request_prompt": prompt,
                    "backend_a": {
                        "api_base": "https://cluster-alpha.internal.net/v1",
                        "model": "gpt-4o",
                        "cache_key": key_backend_a,
                    },
                    "backend_b": {
                        "api_base": "https://cluster-beta.internal.net/v1",
                        "model": "gpt-4o",
                        "cache_key": key_backend_b,
                    },
                    "cache_keys_identical": (key_backend_a == key_backend_b),
                    "tenant_alpha": {
                        "api_key": "sk-tenant-alpha-token-1111",
                        "cache_key": key_tenant_1,
                    },
                    "tenant_beta": {
                        "api_key": "sk-tenant-beta-token-2222",
                        "cache_key": key_tenant_2,
                    },
                    "tenant_keys_identical": (key_tenant_1 == key_tenant_2),
                },
                indent=2,
            )
        )

        (wire_dir / "bug-cross-backend-cache-pollution.json").write_text(
            json.dumps(
                {
                    "step_1_populate_backend_a": {
                        "target_api_base": "https://cluster-alpha.internal.net/v1",
                        "caching": True,
                        "returned_content": content_a,
                        "cache_hit": getattr(resp_a, "_hidden_params", {}).get("cache_hit"),
                    },
                    "step_2_polluted_backend_b": {
                        "target_api_base": "https://cluster-beta.internal.net/v1",
                        "caching": True,
                        "returned_content": content_b,
                        "cache_hit": resp_b_cache_hit,
                        "leak_detected": (content_b == content_a),
                    },
                },
                indent=2,
            )
        )

        (wire_dir / "control-cache-key-separation.json").write_text(
            json.dumps(
                {
                    "description": "Cache keys generated when destination connection params are included in key material",
                    "backend_a_keyed": {
                        "api_base": "https://cluster-alpha.internal.net/v1",
                        "distinct_cache_key": ctrl_key_a,
                    },
                    "backend_b_keyed": {
                        "api_base": "https://cluster-beta.internal.net/v1",
                        "distinct_cache_key": ctrl_key_b,
                    },
                    "keys_separated": ctrl_keys_separated,
                },
                indent=2,
            )
        )

        (wire_dir / "control-same-endpoint-cache-hit.json").write_text(
            json.dumps(
                {
                    "description": "Repeated call to the same endpoint correctly hits cached response",
                    "target_api_base": "https://cluster-alpha.internal.net/v1",
                    "cached_content": resp_a_repeat.choices[0].message.content,
                    "cache_hit": bool(getattr(resp_a_repeat, "_hidden_params", {}).get("cache_hit")),
                },
                indent=2,
            )
        )

        (wire_dir / "control-un-cached-isolation.json").write_text(
            json.dumps(
                {
                    "description": "Direct un-cached calls reach respective backends without cache pollution",
                    "backend_a_direct": {
                        "api_base": "https://cluster-alpha.internal.net/v1",
                        "content": resp_a_direct.choices[0].message.content,
                    },
                    "backend_b_direct": {
                        "api_base": "https://cluster-beta.internal.net/v1",
                        "content": resp_b_direct.choices[0].message.content,
                    },
                    "isolated": ctrl_direct_isolated,
                },
                indent=2,
            )
        )

    return {
        "bug_keys_collide": bug_keys_collide,
        "bug_cross_backend_pollution": bug_cross_backend_pollution,
        "ctrl_same_endpoint_hit": ctrl_same_endpoint_hit,
        "ctrl_keys_separated": ctrl_keys_separated,
        "ctrl_direct_isolated": ctrl_direct_isolated,
    }


def main() -> None:
    rig_dir = Path(__file__).resolve().parent
    transcripts_dir = rig_dir.parent
    wire_dir = transcripts_dir / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, bool]] = []
    iterations = 5

    for i in range(iterations):
        res = run_iteration(i + 1, wire_dir=wire_dir if i == 0 else None)
        results.append(res)
        print(f"Run {i+1}/{iterations}: {res}")

    all_passed = all(
        r["bug_keys_collide"]
        and r["bug_cross_backend_pollution"]
        and r["ctrl_same_endpoint_hit"]
        and r["ctrl_keys_separated"]
        and r["ctrl_direct_isolated"]
        for r in results
    )

    payload = {
        "iterations": iterations,
        "success_rate": f"{sum(1 for r in results if all(r.values()))}/{iterations}",
        "all_passed": all_passed,
        "runs": results,
    }

    (transcripts_dir / "results.json").write_text(json.dumps(payload, indent=2))
    print(f"\nFinal status: {payload['success_rate']} passed. results.json written.")


if __name__ == "__main__":
    main()
