import asyncio
import json
import os
import sys
import time

sys.path.insert(0, "/Users/atharva/litellm-main")

os.environ["DATABASE_URL"] = "postgresql://atharva@localhost:5432/litellm_kairo"
os.environ["LITELLM_MASTER_KEY"] = "sk-master-key-1234"

import httpx
from fastapi import HTTPException
import litellm
import litellm.proxy.proxy_server as ps
from litellm.proxy.proxy_server import app, ProxyStartupEvent
from litellm.caching.caching import DualCache
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.hooks.parallel_request_limiter_v3 import PROXY_MaxParallelRequestsHandler_v3
from litellm.proxy.utils import InternalUsageCache, hash_token

WIRE_DIR = "/Users/atharva/kairo-114/transcripts/114/wire"
os.makedirs(WIRE_DIR, exist_ok=True)


async def run_reproduction():
    # 1. Initialize Prisma DB
    client_obj = await ProxyStartupEvent.setup_prisma_client(
        database_url=os.environ["DATABASE_URL"],
        proxy_logging_obj=ps.proxy_logging_obj,
        user_api_key_cache=ps.user_api_key_cache,
    )
    ps.prisma_client = client_obj
    ps.master_key = "sk-master-key-1234"

    team_id = f"team-kairo-114-{int(time.time())}"
    admin_headers = {"Authorization": "Bearer sk-master-key-1234"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: POST /team/new with max_parallel_requests = 1
        new_team_payload = {
            "team_id": team_id,
            "team_alias": "Kairo Team 114",
            "max_parallel_requests": 1,
        }
        with open(os.path.join(WIRE_DIR, "team-new-request.json"), "w") as f:
            json.dump(new_team_payload, f, indent=2)

        resp_new = await client.post("/team/new", headers=admin_headers, json=new_team_payload)
        new_team_result = resp_new.json()
        with open(os.path.join(WIRE_DIR, "team-new-response.json"), "w") as f:
            json.dump(new_team_result, f, indent=2)

        # Step 2: POST /team/update with max_parallel_requests = 2
        update_team_payload = {
            "team_id": team_id,
            "max_parallel_requests": 2,
        }
        with open(os.path.join(WIRE_DIR, "team-update-request.json"), "w") as f:
            json.dump(update_team_payload, f, indent=2)

        resp_update = await client.post("/team/update", headers=admin_headers, json=update_team_payload)
        update_team_result = resp_update.json()
        with open(os.path.join(WIRE_DIR, "team-update-response.json"), "w") as f:
            json.dump(update_team_result, f, indent=2)

        # Step 3: GET /team/info
        resp_info = await client.get(f"/team/info?team_id={team_id}", headers=admin_headers)
        info_result = resp_info.json()
        with open(os.path.join(WIRE_DIR, "team-info-response.json"), "w") as f:
            json.dump(info_result, f, indent=2)

        # Step 4: Test Limiter Behavior (Team vs Control Key)
        local_cache = DualCache()
        handler = PROXY_MaxParallelRequestsHandler_v3(internal_usage_cache=InternalUsageCache(local_cache))

        # Control Key: Key-level max_parallel_requests = 1
        ctrl_auth = UserAPIKeyAuth(api_key=hash_token("sk-ctrl-key"), max_parallel_requests=1)
        ctrl_results = []
        for i in range(5):
            try:
                await handler.async_pre_call_hook(
                    user_api_key_dict=ctrl_auth,
                    cache=local_cache,
                    data={"model": "gpt-4o"},
                    call_type="",
                )
                ctrl_results.append(200)
            except HTTPException as exc:
                ctrl_results.append(exc.status_code)
                if i == 1:
                    with open(os.path.join(WIRE_DIR, "control-key-429-response.json"), "w") as f:
                        json.dump({"status_code": exc.status_code, "detail": exc.detail}, f, indent=2)

        # Team Key: Belongs to team_id, key itself has no limit
        team_auth = UserAPIKeyAuth(api_key=hash_token("sk-team-key"), team_id=team_id)
        team_results = []
        for i in range(5):
            try:
                await handler.async_pre_call_hook(
                    user_api_key_dict=team_auth,
                    cache=local_cache,
                    data={"model": "gpt-4o"},
                    call_type="",
                )
                team_results.append(200)
            except HTTPException as exc:
                team_results.append(exc.status_code)

    if ps.prisma_client:
        await ps.prisma_client.disconnect()

    summary = {
        "team_new_status": resp_new.status_code,
        "team_new_max_parallel_requests": new_team_result.get("max_parallel_requests"),
        "team_update_status": resp_update.status_code,
        "team_update_max_parallel_requests": update_team_result.get("max_parallel_requests"),
        "team_info_max_parallel_requests": info_result.get("team_info", {}).get("max_parallel_requests"),
        "control_key_5_burst_statuses": ctrl_results,
        "team_key_5_burst_statuses": team_results,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(run_reproduction())
