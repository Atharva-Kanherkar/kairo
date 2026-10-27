"""Deterministic Gemini API: no candidates for prompts containing EMPTY, else a text answer."""

import json

from kairo_verify import Reply


def respond(cap):
    if "EMPTY" in json.dumps(cap.json or {}):
        return Reply.json({"candidates": [], "promptFeedback": {"blockReason": "OTHER"},
                           "usageMetadata": {"promptTokenCount": 3, "totalTokenCount": 3}})
    return Reply.json({"candidates": [{"content": {"role": "model", "parts": [{"text": "gemini ok"}]}, "finishReason": "STOP",
                                       "index": 0}], "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2,
                                                                       "totalTokenCount": 5}})


def proxy_config(upstream_url):
    return {"model_list": [{"model_name": "gem", "litellm_params": {"model": "gemini/gemini-2.5-flash",
                                                                     "api_base": upstream_url, "api_key": "kairo-gemini-key"}}],
            "litellm_settings": {"telemetry": False}}
