# The probe corpus: the Anthropic /v1/messages field space, enumerated.
#
# This file is the denominator. Every probe here is one column of the coverage
# matrix; every (gateway, probe) pair is one cell. Cells that come back
# PRESERVED are as much of the result as the ones that come back DROPPED, and
# both get reported.
#
# Axes:
#   request  - a top-level request parameter on /v1/messages
#   content  - a message content block or a field on one
#   header   - a request header (including ones that must NOT be forwarded)
#   response - an upstream reply shape, checked against what the client sees
#
# `known` maps gateway name to the kairo issue that documents this loss on
# THAT gateway. Attribution matters: 012 is a LiteLLM issue, so Bifrost passing
# the same probe is a result, not a regression. Per gateway, its own known
# probes are the positive controls: if 032 comes back clean on Bifrost, the rig
# is not exercising the path, not that the bug was fixed.
from __future__ import annotations

import json

PRESERVED = "PRESERVED"
DROPPED = "DROPPED"
MANGLED = "MANGLED"
EXPECTED_LOSS = "EXPECTED_LOSS"  # no equivalent exists in the target format
REJECTED = "REJECTED"
ERROR = "ERROR"
SKIPPED = "SKIPPED"

SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "ok": {"type": "boolean"}},
    "required": ["city", "ok"],
    "additionalProperties": False,
}

TOOL = {
    "name": "get_weather",
    "description": "Get current weather for a location",
    "input_schema": {
        "type": "object",
        "properties": {"location": {"type": "string"}},
        "required": ["location"],
    },
}

HI = [{"role": "user", "content": "hi"}]


# ---------- deep-search helpers ----------


def walk(v):
    """Yield every (key, value) pair anywhere in a JSON value."""
    if isinstance(v, dict):
        for k, child in v.items():
            yield k, child
            yield from walk(child)
    elif isinstance(v, list):
        for child in v:
            yield from walk(child)


def has_key(body, *names, skip=("messages", "input", "content")):
    """True when any of `names` appears as an object key outside prompt text.

    The skip list matters: a distinctive token echoed into user text is not a
    surviving wire field. This is the same discipline as the 040 checker.
    """
    names = set(names)

    def rec(v):
        if isinstance(v, dict):
            if names & set(v.keys()):
                return True
            return any(rec(c) for k, c in v.items() if k not in skip)
        if isinstance(v, list):
            return any(rec(c) for c in v)
        return False

    return rec(body)


def has_json_schema(body):
    """Port of checks.rs::has_json_schema_wire_field.

    Matches a `json_schema` key OR `type: "json_schema"` as a value, skipping
    prompt-bearing fields. The `type` clause is what catches the Responses API
    spelling, `text.format.type = "json_schema"`, which is how LiteLLM and
    Bifrost both carry an Anthropic schema.
    """
    skip = ("messages", "input", "content")

    def rec(v):
        if isinstance(v, dict):
            if "json_schema" in v or v.get("type") == "json_schema":
                return True
            return any(rec(c) for k, c in v.items() if k not in skip)
        if isinstance(v, list):
            return any(rec(c) for c in v)
        return False

    return rec(body)


def value_at(body, path, default=None):
    cur = body
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return default
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def any_value(body, needle):
    """True when `needle` appears as any value anywhere (including in text)."""
    return needle in json.dumps(body)


def system_text_present(body, needle):
    """True when the system prompt survived, in any OpenAI-side spelling.

    Four shapes in the wild: a `system`/`developer` role message inside
    `messages` (Chat Completions) or inside `input` (Responses), a top-level
    `system`, and top-level `instructions` (Responses). LiteLLM uses
    `instructions`; Bifrost uses a system-role entry in `input`. Scanning only
    `messages` reads both of those as a dropped system prompt.
    """
    for field in ("messages", "input"):
        for m in value_at(body, field, []) or []:
            if isinstance(m, dict) and m.get("role") in ("system", "developer"):
                if needle in json.dumps(m.get("content", "")):
                    return True
    for field in ("system", "instructions"):
        if needle in json.dumps(value_at(body, field, "") or ""):
            return True
    return False


# ---------- probe definition ----------


class Probe:
    def __init__(
        self,
        pid,
        axis,
        field,
        body,
        expect,
        severity="medium",
        known=None,
        canned=None,
        control=None,
        headers=None,
        note="",
    ):
        self.id = pid
        self.axis = axis
        self.field = field
        self.body = body
        self.expect = expect
        self.severity = severity
        self.known = known
        self.canned = canned  # upstream reply to stage (response-axis probes)
        self.control = control  # OpenAI-route body proving the gateway CAN
        self.headers = headers or {}
        self.note = note

    def __repr__(self):
        return f"<Probe {self.id}>"


def req(pid, field, body, expect, **kw):
    return Probe(pid, "request", field, body, expect, **kw)


def content(pid, field, body, expect, **kw):
    return Probe(pid, "content", field, body, expect, **kw)


def header(pid, field, body, expect, **kw):
    return Probe(pid, "header", field, body, expect, **kw)


def cred(pid, field, body, expect, **kw):
    """A credential-leak probe. Always inverted: PRESERVED means no leak."""
    return Probe(pid, "credential", field, body, expect, **kw)


def response(pid, field, canned, expect, **kw):
    return Probe(pid, "response", field, dict(model="M", max_tokens=64, messages=HI),
                 expect, canned=canned, **kw)


def base(**kw):
    b = {"model": "M", "max_tokens": 64, "messages": HI}
    b.update(kw)
    return b


# ---------- the corpus ----------
# fwd = forwarded upstream body, hdr = forwarded upstream headers,
# cli = the response the client received from the gateway.

PROBES = [
    # ---- sanity cells: if these fail the rig is wrong, not the gateway ----
    req("req.model", "model", base(),
        lambda fwd, hdr, cli: PRESERVED if value_at(fwd, "model") else DROPPED,
        severity="sanity", note="rig sanity check"),
    req("req.messages", "messages", base(),
        lambda fwd, hdr, cli: PRESERVED
        if value_at(fwd, "messages") or value_at(fwd, "input") else DROPPED,
        severity="sanity",
        note="`input` is the Responses spelling; both ingresses use it"),
    req("req.max_tokens", "max_tokens", base(),
        lambda fwd, hdr, cli: PRESERVED
        if has_key(fwd, "max_tokens", "max_completion_tokens",
                   "max_output_tokens") else DROPPED,
        severity="high"),

    # ---- top-level request parameters ----
    req("req.system", "system", base(system="SYSPROBE-7731"),
        lambda fwd, hdr, cli: PRESERVED if system_text_present(fwd, "SYSPROBE-7731")
        else DROPPED,
        severity="high"),
    req("req.stop_sequences", "stop_sequences", base(stop_sequences=["STOPPROBE"]),
        lambda fwd, hdr, cli: PRESERVED if any_value(value_at(fwd, "stop", ""), "STOPPROBE")
        or any_value(value_at(fwd, "stop_sequences", ""), "STOPPROBE") else DROPPED,
        severity="high", known={"bifrost": "032", "litellm": "041"},
        control=dict(model="M", max_tokens=64, stop=["STOPPROBE"],
                     messages=[{"role": "user", "content": "hi"}])),
    req("req.temperature", "temperature", base(temperature=0.3),
        lambda fwd, hdr, cli: PRESERVED if has_key(fwd, "temperature") else DROPPED),
    req("req.top_p", "top_p", base(top_p=0.4),
        lambda fwd, hdr, cli: PRESERVED if has_key(fwd, "top_p") else DROPPED),
    req("req.top_k", "top_k", base(top_k=7),
        lambda fwd, hdr, cli: PRESERVED if has_key(fwd, "top_k") else EXPECTED_LOSS,
        severity="low", note="no OpenAI chat equivalent; documents the boundary"),
    req("req.metadata.user_id", "metadata.user_id",
        base(metadata={"user_id": "USERPROBE-4412"}),
        lambda fwd, hdr, cli: PRESERVED if any_value(fwd, "USERPROBE-4412") else DROPPED,
        severity="medium", note="abuse-tracking signal; maps to OpenAI `user`"),
    req("req.service_tier", "service_tier", base(service_tier="standard_only"),
        lambda fwd, hdr, cli: PRESERVED if has_key(fwd, "service_tier") else DROPPED,
        severity="low"),
    req("req.stream", "stream", base(stream=True),
        lambda fwd, hdr, cli: PRESERVED if value_at(fwd, "stream") is True else DROPPED,
        severity="high"),

    # ---- structured output / reasoning controls ----
    req("req.output_config.format", "output_config.format",
        base(output_config={"format": {"type": "json_schema", "name": "city",
                                       "schema": SCHEMA}}),
        lambda fwd, hdr, cli: PRESERVED if has_json_schema(fwd) else DROPPED,
        severity="high", known={"switchyard": "040", "gomodel": "042", "axonhub": "051"},
        control=dict(model="M", max_tokens=64,
                     messages=[{"role": "user", "content": "hi"}],
                     response_format={"type": "json_schema",
                                      "json_schema": {"name": "city", "schema": SCHEMA,
                                                      "strict": True}})),
    req("req.output_format.legacy", "output_format (deprecated spelling)",
        base(output_format={"type": "json_schema", "schema": SCHEMA}),
        lambda fwd, hdr, cli: PRESERVED if has_json_schema(fwd) else DROPPED,
        severity="high", known={"switchyard": "040", "gomodel": "042", "axonhub": "051"},
        note="the spelling the 040 family was frozen against"),
    req("req.output_config.effort", "output_config.effort",
        base(output_config={"effort": "low"}),
        lambda fwd, hdr, cli: PRESERVED
        if has_key(fwd, "reasoning_effort", "effort", "reasoning") else DROPPED,
        severity="medium"),
    req("req.thinking.adaptive", "thinking (adaptive)",
        base(thinking={"type": "adaptive"}),
        lambda fwd, hdr, cli: PRESERVED
        if has_key(fwd, "reasoning", "reasoning_effort", "thinking") else DROPPED,
        severity="high"),
    req("req.thinking.disabled", "thinking (disabled)",
        base(thinking={"type": "disabled"}),
        lambda fwd, hdr, cli: PRESERVED
        if has_key(fwd, "reasoning", "reasoning_effort", "thinking") else EXPECTED_LOSS,
        severity="low", note="absence is a defensible mapping for `disabled`"),
    req("req.thinking.budget_tokens", "thinking.budget_tokens (legacy)",
        base(thinking={"type": "enabled", "budget_tokens": 1024}),
        lambda fwd, hdr, cli: PRESERVED
        if has_key(fwd, "reasoning", "reasoning_effort", "budget_tokens") else DROPPED,
        severity="medium"),

    # ---- tools ----
    req("req.tools.schema", "tools[].input_schema", base(tools=[TOOL]),
        # The field under test is the schema, not the name. A gateway that
        # echoes `get_weather` into tool_choice while dropping `parameters`
        # would otherwise score as preserved.
        lambda fwd, hdr, cli: PRESERVED
        if (has_key(fwd, "parameters", "input_schema", skip=())
            and any_value(fwd, "location")) else DROPPED,
        severity="high"),
    req("req.tools.strict", "tools[].strict",
        base(tools=[dict(TOOL, strict=True)]),
        lambda fwd, hdr, cli: PRESERVED if has_key(fwd, "strict") else DROPPED,
        severity="medium"),
    req("req.tool_choice.auto", "tool_choice: auto",
        base(tools=[TOOL], tool_choice={"type": "auto"}),
        lambda fwd, hdr, cli: PRESERVED
        if value_at(fwd, "tool_choice") in ("auto", {"type": "auto"})
        or value_at(fwd, "tool_choice.type") == "auto" else MANGLED,
        severity="medium"),
    req("req.tool_choice.any", "tool_choice: any -> required",
        base(tools=[TOOL], tool_choice={"type": "any"}),
        lambda fwd, hdr, cli: PRESERVED
        if value_at(fwd, "tool_choice") in ("required", "any")
        or value_at(fwd, "tool_choice.type") in ("required", "any") else MANGLED,
        severity="high"),
    req("req.tool_choice.named", "tool_choice: tool(name)",
        base(tools=[TOOL], tool_choice={"type": "tool", "name": "get_weather"}),
        lambda fwd, hdr, cli: PRESERVED
        if any_value(value_at(fwd, "tool_choice", {}), "get_weather") else MANGLED,
        severity="high"),
    req("req.disable_parallel_tool_use", "tool_choice.disable_parallel_tool_use",
        base(tools=[TOOL],
             tool_choice={"type": "auto", "disable_parallel_tool_use": True}),
        lambda fwd, hdr, cli: PRESERVED
        if value_at(fwd, "parallel_tool_calls") is False else DROPPED,
        severity="high", known={"switchyard": "017", "litellm": "017", "bifrost": "031", "gomodel": "043"},
        control=dict(model="M", max_tokens=64,
                     messages=[{"role": "user", "content": "hi"}],
                     parallel_tool_calls=False,
                     tools=[{"type": "function",
                             "function": {"name": "get_weather",
                                          "parameters": TOOL["input_schema"]}}])),

    # ---- newer surface, largely unmapped: worth measuring, not assuming ----
    req("req.mcp_servers", "mcp_servers",
        base(mcp_servers=[{"type": "url", "name": "probe",
                           "url": "https://mcp.example.invalid/sse"}]),
        lambda fwd, hdr, cli: PRESERVED if has_key(fwd, "mcp_servers") else EXPECTED_LOSS,
        severity="low"),
    req("req.context_management", "context_management",
        base(context_management={"edits": [{"type": "clear_tool_uses_20250919"}]}),
        lambda fwd, hdr, cli: PRESERVED
        if has_key(fwd, "context_management") else EXPECTED_LOSS,
        severity="low"),
    req("req.cache_control.toplevel", "cache_control (top-level)",
        base(cache_control={"type": "ephemeral"}),
        lambda fwd, hdr, cli: PRESERVED
        if has_key(fwd, "cache_control") else EXPECTED_LOSS,
        severity="low", note="019 is the inverse: invented where none was sent"),

    # ---- message content blocks ----
    content("content.image.base64", "image (base64)",
            base(messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png",
                                             "data": "iVBORw0KGgo="}},
                {"type": "text", "text": "hi"}]}]),
            lambda fwd, hdr, cli: PRESERVED
            if has_key(fwd, "image_url", skip=()) else DROPPED,
            severity="high", known={"litellm": "012"}),
    content("content.document.pdf", "document (pdf base64)",
            base(messages=[{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64",
                                                "media_type": "application/pdf",
                                                "data": "JVBERi0="}},
                {"type": "text", "text": "hi"}]}]),
            lambda fwd, hdr, cli: PRESERVED
            if has_key(fwd, "file", "input_file", "file_data", skip=()) else DROPPED,
            severity="high", known={"switchyard": "018", "litellm": "018"}),
    content("content.tool_result.is_error", "tool_result.is_error",
            base(messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_probe1", "name": "get_weather",
                     "input": {"location": "Paris"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_probe1",
                     "is_error": True, "content": "ERRPROBE boom"}]}],
                 tools=[TOOL]),
            lambda fwd, hdr, cli: PRESERVED
            if has_key(fwd, "is_error", skip=()) or any_value(fwd, "ERRPROBE") else DROPPED,
            severity="high", known={"switchyard": "006", "litellm": "006"},
            note="OpenAI has no is_error; surfacing the text is the minimum bar"),
    content("content.tool_result.image", "tool_result with image block",
            base(messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_probe2", "name": "get_weather",
                     "input": {"location": "Paris"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_probe2", "content": [
                        {"type": "image", "source": {"type": "base64",
                                                     "media_type": "image/png",
                                                     "data": "iVBORw0KGgo="}}]}]}],
                 tools=[TOOL]),
            lambda fwd, hdr, cli: PRESERVED
            if has_key(fwd, "image_url", skip=()) else MANGLED,
            severity="high", known={"switchyard": "007", "litellm": "007"},
            note="007 froze this being JSON-stringified into a text blob"),
    content("content.thinking.history", "thinking block in history",
            base(messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": [
                    {"type": "thinking", "thinking": "THINKPROBE-9021",
                     "signature": "sig"},
                    {"type": "text", "text": "ok"}]},
                {"role": "user", "content": "again"}]),
            lambda fwd, hdr, cli: PRESERVED
            if any_value(fwd, "THINKPROBE-9021") else DROPPED,
            severity="high", known={"switchyard": "016", "litellm": "016", "bifrost": "033"}),
    content("content.cache_control.block", "cache_control on a content block",
            base(messages=[{"role": "user", "content": [
                {"type": "text", "text": "hi",
                 "cache_control": {"type": "ephemeral"}}]}]),
            lambda fwd, hdr, cli: PRESERVED
            if has_key(fwd, "cache_control", skip=()) else EXPECTED_LOSS,
            severity="low"),
    content("content.tool_use.id", "tool_use.id round trip",
            base(messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": [
                    {"type": "tool_use", "id": "toolu_IDPROBE_8899",
                     "name": "get_weather", "input": {"location": "Paris"}}]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_IDPROBE_8899",
                     "content": "ok"}]}],
                 tools=[TOOL]),
            lambda fwd, hdr, cli: PRESERVED
            if any_value(fwd, "IDPROBE_8899") else MANGLED,
            severity="high", known={"switchyard": "005", "bifrost": "037"},
            note="a sanitizer with no inverse shows up here as MANGLED"),

    # ---- credential handling ----
    # Vector matters more than name. Reviewing against the issue writeups:
    # 020 and 026 are JSON *body* bypasses, and both writeups say the
    # same-named HTTP headers are correctly dropped on the default config, so
    # a header probe tests the control and can never reproduce the defect.
    # 023 names `api-key` / `OpenAI-Organization` / `OpenAI-Project` and 027
    # names `x-goog-api-key`; neither is the Anthropic `x-api-key`.
    #
    # Every probe below is inverted: PRESERVED means the gateway did NOT leak.
    cred("cred.body.api_key", "body `api_key` must not become upstream auth",
         base(api_key="BODYKEYCANARY-7788"),
         lambda fwd, hdr, cli: DROPPED
         if "BODYKEYCANARY-7788" in json.dumps(hdr) else PRESERVED,
         severity="high", known={"litellm": "020"},
         note="020's actual vector: body api_key forwarded as Authorization"),
    cred("cred.body.extra_headers", "body `extra_headers` must not reach the wire",
         base(extra_headers={"OpenAI-Organization": "ORGCANARY-5150",
                             "Authorization": "Bearer EHCANARY-6620"},
              organization="ORGCANARY-5150"),
         lambda fwd, hdr, cli: DROPPED
         if ("ORGCANARY-5150" in json.dumps(hdr)
             or "EHCANARY-6620" in json.dumps(hdr)) else PRESERVED,
         severity="high", known={"litellm": "026"},
         note="026's actual vector: JSON extra_headers / organization"),
    cred("cred.header.api_key", "client `api-key` header must not be forwarded",
         base(), lambda fwd, hdr, cli: DROPPED
         if "APIKEYCANARY-2301" in json.dumps(hdr) else PRESERVED,
         severity="high", known={"switchyard": "023"},
         headers={"api-key": "APIKEYCANARY-2301"},
         note="023: Azure-style credential header, not reserved, forwarded"),
    cred("cred.header.x_goog_api_key", "client `x-goog-api-key` must not be forwarded",
         base(), lambda fwd, hdr, cli: DROPPED
         if "GOOGCANARY-9944" in json.dumps(hdr) else PRESERVED,
         severity="high", known={"switchyard": "027"},
         headers={"x-goog-api-key": "GOOGCANARY-9944"}),
    cred("cred.header.openai_organization",
         "client `openai-organization` must not be forwarded",
         base(), lambda fwd, hdr, cli: DROPPED
         if "ORGHDRCANARY-5150" in json.dumps(hdr) else PRESERVED,
         severity="high", known={"switchyard": "023"},
         headers={"openai-organization": "ORGHDRCANARY-5150"},
         note="LiteLLM drops this by default; that is 020/026's stated control"),
    cred("cred.header.authorization",
         "client `Authorization` must not be forwarded",
         base(), lambda fwd, hdr, cli: DROPPED
         if "CLIENTSECRET-3311" in json.dumps(hdr) else PRESERVED,
         severity="high",
         headers={"authorization": "Bearer CLIENTSECRET-3311"},
         note="no issue claims this; it is 020's documented control path"),
    cred("cred.header.anthropic_x_api_key",
         "client `x-api-key` must not be forwarded",
         base(), lambda fwd, hdr, cli: DROPPED
         if "XKEYSECRET-7742" in json.dumps(hdr) else PRESERVED,
         severity="high",
         headers={"x-api-key": "XKEYSECRET-7742"},
         note="the Anthropic client credential; distinct from 023's api-key"),

    # ---- headers ----
    header("header.anthropic_beta", "anthropic-beta forwarded or mapped",
           base(), lambda fwd, hdr, cli: PRESERVED
           if "context-management-2025-06-27" in json.dumps(hdr) else DROPPED,
           severity="medium",
           headers={"anthropic-beta": "context-management-2025-06-27"}),

    # ---- response side: upstream shape vs what the client sees ----
    response("resp.finish_reason.stop", "finish_reason stop -> end_turn",
             {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
              "choices": [{"index": 0, "finish_reason": "stop",
                           "message": {"role": "assistant", "content": "ok"}}],
              "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                        "total_tokens": 2}},
             lambda fwd, hdr, cli: PRESERVED
             if value_at(cli, "stop_reason") == "end_turn" else MANGLED,
             severity="high", known={"litellm": "001/002"}),
    response("resp.finish_reason.length", "finish_reason length -> max_tokens",
             {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
              "choices": [{"index": 0, "finish_reason": "length",
                           "message": {"role": "assistant", "content": "ok"}}],
              "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                        "total_tokens": 2}},
             lambda fwd, hdr, cli: PRESERVED
             if value_at(cli, "stop_reason") == "max_tokens" else MANGLED,
             severity="high", known={"bifrost": "035"}),
    response("resp.finish_reason.content_filter", "content_filter signal survives",
             {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
              "choices": [{"index": 0, "finish_reason": "content_filter",
                           "message": {"role": "assistant", "content": ""}}],
              "usage": {"prompt_tokens": 1, "completion_tokens": 0,
                        "total_tokens": 1}},
             lambda fwd, hdr, cli: PRESERVED
             if value_at(cli, "stop_reason") not in ("end_turn", None) else MANGLED,
             severity="high", known={"switchyard": "010A", "bifrost": "034"},
             note="erasing a safety signal into end_turn is the 034 defect"),
    response("resp.empty_text_before_tool_use", "no invented empty text block",
             {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
              "choices": [{"index": 0, "finish_reason": "tool_calls",
                           "message": {"role": "assistant", "content": None,
                                       "tool_calls": [
                                           {"id": "call_1", "type": "function",
                                            "function": {"name": "get_weather",
                                                         "arguments": "{}"}}]}}],
              "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                        "total_tokens": 2}},
             lambda fwd, hdr, cli: MANGLED
             if any(b.get("type") == "text" and b.get("text") == ""
                    for b in (value_at(cli, "content") or [])
                    if isinstance(b, dict)) else PRESERVED,
             severity="high", known={"litellm": "009", "switchyard": "045"},
             note="inverted: MANGLED means an empty text block was invented"),
    response("resp.refusal", "upstream refusal content survives",
             {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
              "choices": [{"index": 0, "finish_reason": "stop",
                           "message": {"role": "assistant", "content": None,
                                       "refusal": "REFUSALPROBE cannot help"}}],
              "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                        "total_tokens": 2}},
             lambda fwd, hdr, cli: PRESERVED
             if any_value(cli, "REFUSALPROBE") else DROPPED,
             severity="high", known={"bifrost": "036"}),
    response("resp.tool_call_id", "upstream tool_call id reaches the client",
             {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
              "choices": [{"index": 0, "finish_reason": "tool_calls",
                           "message": {"role": "assistant", "content": None,
                                       "tool_calls": [
                                           {"id": "call_IDECHO_6001",
                                            "type": "function",
                                            "function": {"name": "get_weather",
                                                         "arguments": "{}"}}]}}],
              "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                        "total_tokens": 2}},
             lambda fwd, hdr, cli: PRESERVED
             if any_value(cli, "IDECHO_6001") else MANGLED,
             severity="high", known={"litellm": "004", "bifrost": "037"}),
    response("resp.usage", "usage counts survive translation",
             {"id": "c", "object": "chat.completion", "created": 0, "model": "m",
              "choices": [{"index": 0, "finish_reason": "stop",
                           "message": {"role": "assistant", "content": "ok"}}],
              "usage": {"prompt_tokens": 41, "completion_tokens": 17,
                        "total_tokens": 58}},
             lambda fwd, hdr, cli: PRESERVED
             if value_at(cli, "usage.input_tokens") == 41 else MANGLED,
             severity="medium"),
]


def by_axis(axis):
    return [p for p in PROBES if p.axis == axis]


def by_id(pid):
    for p in PROBES:
        if p.id == pid:
            return p
    return None


# Probes whose expectation is inverted: a "clean" result means the gateway did
# NOT do the bad thing. Reported separately so the matrix reads correctly.
# Probes whose expectation is inverted: PRESERVED means the gateway did NOT do
# the bad thing. Every credential probe is inverted by construction.
INVERTED = ({p.id for p in PROBES if p.axis == "credential"}
            | {"resp.empty_text_before_tool_use"})
