"""Deterministic Gemini API: generateContent and streamGenerateContent with text + inlineData parts."""

import json

from kairo_verify import Reply
from rigs import bifrost

MODEL = "gemini/gemini-2.5-flash-image"
RED_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def make_responder(caption="Here is a red square.", image=RED_PNG):
    usage = {"promptTokenCount": 8, "candidatesTokenCount": 1300, "totalTokenCount": 1308,
             "candidatesTokensDetails": [{"modality": "IMAGE", "tokenCount": 1290}, {"modality": "TEXT", "tokenCount": 10}]}

    def respond(cap):
        if cap.method == "GET":
            return Reply.json({"models": [{"name": "models/gemini-2.5-flash-image"}]})
        prompt = json.dumps((cap.json or {}).get("contents"))
        parts = [{"text": "plain answer"}] if "TEXT ONLY" in prompt else \
            [{"text": caption}, {"inlineData": {"mimeType": "image/png", "data": image}}]
        if ":streamGenerateContent" in cap.path:
            chunks = [{"candidates": [{"content": {"role": "model", "parts": [p]}, "index": 0}],
                       "modelVersion": "gemini-2.5-flash-image"} for p in parts]
            chunks[-1]["candidates"][0]["finishReason"] = "STOP"
            chunks[-1]["usageMetadata"] = usage
            return Reply.sse(chunks)
        return Reply.json({"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP",
                                           "index": 0}], "usageMetadata": usage, "modelVersion": "gemini-2.5-flash-image"})
    return respond


def gateway_config(upstream_url):
    return bifrost.config({"gemini": {"keys": [{"name": "k", "value": "kairo-gemini-key", "weight": 1, "models": ["*"]}],
                                      "network_config": {"base_url": upstream_url, "max_retries": 0}}})


def chat_body(prompt="Draw a red square and describe it.", stream=False):
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}]}
    if stream:
        body["stream"] = True
    return body
