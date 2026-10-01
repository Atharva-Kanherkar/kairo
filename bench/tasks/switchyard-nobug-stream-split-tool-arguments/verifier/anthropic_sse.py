"""Helpers for reading Anthropic Messages SSE."""

from kairo_verify import sse_events


def stop_reason(raw):
    for e in sse_events(raw):
        j = e["json"] or {}
        if j.get("type") == "message_delta":
            return (j.get("delta") or {}).get("stop_reason")
    return None


def block_types(raw):
    return [((e["json"] or {}).get("content_block") or {}).get("type") for e in sse_events(raw)
            if (e["json"] or {}).get("type") == "content_block_start"]


def text(raw):
    out = []
    for e in sse_events(raw):
        j = e["json"] or {}
        d = j.get("delta") or {}
        if j.get("type") == "content_block_delta" and d.get("type") == "text_delta":
            out.append(d.get("text", ""))
    return "".join(out)
