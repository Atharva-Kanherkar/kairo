"""OpenAI Responses upstream that reflects selected request headers on its response,
plus a governance-enabled Bifrost config with one provider-scoped virtual key."""

from kairo_verify import Reply
from rigs import bifrost

VIRTUAL_KEY = "sk-bf-kairo-team-vk-0001"
REFLECT = ("x-provider-secret", "x-gateway-token", "x-safe-trace")

RESPONSE = {"id": "resp_kairo", "object": "response", "created_at": 1789250000, "status": "completed",
            "model": "mock-model", "output": [{"id": "msg_1", "type": "message", "status": "completed",
                                               "role": "assistant", "content": [{"type": "output_text",
                                                                                 "text": "synthetic-ok", "annotations": []}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}


def respond(cap):
    if cap.method == "GET":
        return Reply.json({"object": "list", "data": [{"id": "mock-model", "object": "model"}]})
    headers = {name: cap.headers[name] for name in REFLECT if name in cap.headers}
    headers["Authorization"] = "Bearer upstream-authorization-control"
    return Reply.json(RESPONSE, headers=headers)


def gateway_config(upstream_url, extra_headers):
    return bifrost.config(
        {"mockoai": {
            "keys": [{"id": "mock-key", "name": "mock-key", "value": "sk-kairo-provider", "weight": 1, "models": ["*"]}],
            "network_config": {"base_url": upstream_url, "allow_private_network": True, "max_retries": 0,
                               "extra_headers": extra_headers},
            "custom_provider_config": {"base_provider_type": "openai"},
        }},
        client={"enforce_auth_on_inference": True},
        config_store={"enabled": True, "type": "sqlite", "config": {"path": "__APP_DIR__/config.db"}},
        logs_store={"enabled": False},
        governance={
            "auth_config": {"is_enabled": True, "admin_username": "kairo-admin", "admin_password": "kairo-admin-pass-9"},
            "virtual_keys": [{"id": "vk-kairo-team", "name": "team", "value": VIRTUAL_KEY, "is_active": True,
                              "provider_configs": [{"provider": "mockoai", "allowed_models": ["*"],
                                                    "key_ids": ["mock-key"], "weight": 1}]}],
        },
    )


def responses_body():
    return {"model": "mockoai/mock-model", "input": "Return the deterministic fixture.", "max_output_tokens": 16}
