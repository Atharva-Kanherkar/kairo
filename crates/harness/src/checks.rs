//! Conformance checkers. Each one encodes an invariant that a lossless
//! tool-call translation MUST satisfy, and runs against recorded wire bytes.
//!
//! A checker returns [`Verdict::Conformant`] when the invariant holds and
//! [`Verdict::Violation`] (with a human-readable reason) when it does not.
//! Run against a buggy gateway's recorded transcript, the checker reports the
//! violation; run against a correct implementation, it reports conformance.
//! Same checkers, both directions.

use serde_json::Value;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    Conformant,
    Violation(String),
}

impl Verdict {
    pub fn is_conformant(&self) -> bool {
        matches!(self, Verdict::Conformant)
    }
}

/// Parse an SSE body into the JSON payload of each `data:` line, skipping
/// `data: [DONE]` and any `event:` lines. Malformed data lines are ignored.
fn sse_data_json(body: &str) -> Vec<Value> {
    body.lines()
        .filter_map(|line| line.strip_prefix("data: "))
        .filter(|d| *d != "[DONE]")
        .filter_map(|d| serde_json::from_str::<Value>(d).ok())
        .collect()
}

/// Invariant (bug 002a, 001): if a streamed Anthropic response contains a
/// `tool_use` block, the terminal `stop_reason` MUST be `tool_use`. A tool
/// call finished as `end_turn`/`stop` makes agent loops halt without running
/// the tool.
pub fn anthropic_toolcall_stop_reason(sse: &str) -> Verdict {
    let events = sse_data_json(sse);
    let has_tool_use = events.iter().any(|e| {
        e.get("type").and_then(Value::as_str) == Some("content_block_start")
            && e.pointer("/content_block/type").and_then(Value::as_str) == Some("tool_use")
    });
    if !has_tool_use {
        return Verdict::Conformant; // nothing to check
    }
    let stop_reason = events
        .iter()
        .filter(|e| e.get("type").and_then(Value::as_str) == Some("message_delta"))
        .find_map(|e| {
            e.pointer("/delta/stop_reason")
                .and_then(Value::as_str)
                .map(str::to_owned)
        });
    match stop_reason.as_deref() {
        Some("tool_use") => Verdict::Conformant,
        other => Verdict::Violation(format!(
            "tool_use block present but stop_reason is {other:?}, expected \"tool_use\""
        )),
    }
}

/// Invariant (bug 002a): if an OpenAI chat stream emits any `tool_calls`
/// delta, the terminal `finish_reason` MUST be `tool_calls`, not `stop`.
pub fn openai_stream_finish_reason(sse: &str) -> Verdict {
    let chunks = sse_data_json(sse);
    let has_toolcall_delta = chunks.iter().any(|c| {
        c.pointer("/choices/0/delta/tool_calls")
            .and_then(Value::as_array)
            .is_some_and(|a| !a.is_empty())
    });
    if !has_toolcall_delta {
        return Verdict::Conformant;
    }
    let finish = chunks
        .iter()
        .filter_map(|c| {
            c.pointer("/choices/0/finish_reason")
                .and_then(Value::as_str)
        })
        .next_back()
        .map(str::to_owned);
    match finish.as_deref() {
        Some("tool_calls") => Verdict::Conformant,
        other => Verdict::Violation(format!(
            "tool_calls delta present but finish_reason is {other:?}, expected \"tool_calls\""
        )),
    }
}

/// True when `id` satisfies the OpenAI / Anthropic tool-call id contract:
/// `^[A-Za-z0-9_-]{1,64}$`.
pub fn id_conforms(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// Invariant (bug 004): every tool-call id an OpenAI chat response carries
/// MUST satisfy the id contract. Catches the reasoning-signature smuggled into
/// a 382-char id full of `+ / =`.
pub fn openai_toolcall_id_charset(response_json: &str) -> Verdict {
    let v: Value = match serde_json::from_str(response_json) {
        Ok(v) => v,
        Err(e) => return Verdict::Violation(format!("unparseable response: {e}")),
    };
    let calls = v
        .pointer("/choices/0/message/tool_calls")
        .and_then(Value::as_array);
    let Some(calls) = calls else {
        return Verdict::Conformant; // no tool calls to check
    };
    for call in calls {
        if let Some(id) = call.get("id").and_then(Value::as_str) {
            if !id_conforms(id) {
                let bad: String = id
                    .chars()
                    .filter(|c| !(c.is_ascii_alphanumeric() || *c == '_' || *c == '-'))
                    .collect::<std::collections::BTreeSet<_>>()
                    .into_iter()
                    .collect();
                return Verdict::Violation(format!(
                    "tool_call id violates ^[A-Za-z0-9_-]{{1,64}}$: length {}, illegal chars [{}]",
                    id.len(),
                    bad
                ));
            }
        }
    }
    Verdict::Conformant
}

/// Invariant (bug 010A): a backend `finish_reason` of `content_filter` must
/// not be translated to Anthropic `end_turn`, which means the model finished
/// naturally. Given the buffered OpenAI response and the translated Anthropic
/// stop_reason, flag the erasure.
pub fn content_filter_preserved(openai_finish: &str, anthropic_stop_reason: &str) -> Verdict {
    if openai_finish == "content_filter" && anthropic_stop_reason == "end_turn" {
        return Verdict::Violation(
            "content_filter translated to end_turn: the safety signal is erased".into(),
        );
    }
    Verdict::Conformant
}

/// Invariant (bug 010B): within an Anthropic stream, the relative order of
/// `thinking` and `text` content blocks must match the order the backend
/// emitted them. `source_order` and `emitted_order` are the block-type
/// sequences (e.g. `["thinking", "text"]`). Flag any mismatch.
pub fn reasoning_text_order_preserved(source_order: &[&str], emitted_order: &[&str]) -> Verdict {
    let keep = |seq: &[&str]| -> Vec<String> {
        seq.iter()
            .filter(|t| **t == "thinking" || **t == "text")
            .map(ToString::to_string)
            .collect()
    };
    let src = keep(source_order);
    let out = keep(emitted_order);
    if src != out {
        return Verdict::Violation(format!(
            "reasoning/text order changed: backend emitted {src:?}, client received {out:?}"
        ));
    }
    Verdict::Conformant
}

/// Parse the `body` object out of a capture-rig jsonl line
/// (`{"path":..., "body": ...}`).
fn capture_body(jsonl: &str) -> Result<Value, String> {
    let line = jsonl.lines().find(|l| !l.trim().is_empty()).unwrap_or("");
    let v: Value = serde_json::from_str(line).map_err(|e| format!("unparseable capture: {e}"))?;
    Ok(v.get("body").cloned().unwrap_or(Value::Null))
}

fn body_dump(body: &Value) -> String {
    serde_json::to_string(body).unwrap_or_default()
}

/// Walk a JSON value and collect every string.
fn collect_strings(v: &Value, out: &mut Vec<String>) {
    match v {
        Value::String(s) => out.push(s.clone()),
        Value::Array(a) => a.iter().for_each(|x| collect_strings(x, out)),
        Value::Object(m) => m.values().for_each(|x| collect_strings(x, out)),
        _ => {}
    }
}

fn jsonl_contains_string(jsonl: &str, needle: &str) -> bool {
    match capture_body(jsonl) {
        Ok(body) => {
            let mut strings = Vec::new();
            collect_strings(&body, &mut strings);
            strings.iter().any(|s| s.contains(needle)) || body_dump(&body).contains(needle)
        }
        Err(_) => jsonl.contains(needle),
    }
}

/// Invariant (bug 016 / Switchyard): thinking text from an Anthropic request
/// MUST appear in the forwarded body as reasoning, not vanish. A forwarded
/// transcript that has dropped `thinking_text` entirely is a violation.
pub fn thinking_text_forwarded(forwarded_jsonl: &str, thinking_text: &str) -> Verdict {
    if jsonl_contains_string(forwarded_jsonl, thinking_text) {
        Verdict::Conformant
    } else {
        Verdict::Violation(format!(
            "thinking text {thinking_text:?} is absent from the forwarded upstream body"
        ))
    }
}

/// Invariant (bug 016 / LiteLLM): private thinking MUST NOT be rewritten as
/// ordinary visible assistant text (`output_text` / message `content`).
pub fn thinking_not_leaked_as_visible_text(forwarded_jsonl: &str, thinking_text: &str) -> Verdict {
    let Ok(body) = capture_body(forwarded_jsonl) else {
        return Verdict::Violation("unparseable capture".into());
    };
    // Responses input items.
    if let Some(input) = body.get("input").and_then(Value::as_array) {
        for item in input {
            if item.get("role").and_then(Value::as_str) == Some("assistant") {
                if let Some(content) = item.get("content").and_then(Value::as_array) {
                    for part in content {
                        let ty = part.get("type").and_then(Value::as_str).unwrap_or("");
                        let text = part.get("text").and_then(Value::as_str).unwrap_or("");
                        if matches!(ty, "output_text" | "input_text" | "text")
                            && text.contains(thinking_text)
                        {
                            return Verdict::Violation(format!(
                                "thinking text leaked as visible {ty}: {thinking_text:?}"
                            ));
                        }
                    }
                }
            }
        }
    }
    // OpenAI chat messages.
    if let Some(messages) = body.get("messages").and_then(Value::as_array) {
        for msg in messages {
            if msg.get("role").and_then(Value::as_str) == Some("assistant") {
                if let Some(content) = msg.get("content").and_then(Value::as_str) {
                    if content.contains(thinking_text) {
                        return Verdict::Violation(format!(
                            "thinking text leaked as assistant content: {thinking_text:?}"
                        ));
                    }
                }
            }
        }
    }
    Verdict::Conformant
}

/// Invariant (bug 017): `disable_parallel_tool_use: true` (Anthropic) MUST
/// survive as `parallel_tool_calls: false` (OpenAI/Responses) or as the
/// original Anthropic flag.
pub fn parallel_tool_disable_preserved(forwarded_jsonl: &str) -> Verdict {
    let Ok(body) = capture_body(forwarded_jsonl) else {
        return Verdict::Violation("unparseable capture".into());
    };
    if body.get("parallel_tool_calls") == Some(&Value::Bool(false)) {
        return Verdict::Conformant;
    }
    if body
        .pointer("/tool_choice/disable_parallel_tool_use")
        .and_then(Value::as_bool)
        == Some(true)
    {
        return Verdict::Conformant;
    }
    Verdict::Violation(
        "disable_parallel_tool_use was dropped; forwarded body has neither parallel_tool_calls=false nor disable_parallel_tool_use=true".into(),
    )
}

/// Invariant (bug 018): a user-supplied document body's bytes MUST appear in
/// the forwarded request. Silent deletion is a violation.
pub fn document_body_forwarded(forwarded_jsonl: &str, document_body: &str) -> Verdict {
    if jsonl_contains_string(forwarded_jsonl, document_body) {
        Verdict::Conformant
    } else {
        Verdict::Violation(format!(
            "document body {document_body:?} is absent from the forwarded upstream body"
        ))
    }
}

/// Invariant (bugs 007 / 018): a non-text block MUST NOT be JSON-dumped into
/// a text string. `marker` is a distinctive substring of that dump
/// (e.g. `"type":"document"` or `"type":"image"`).
pub fn non_text_block_not_json_dumped(forwarded_jsonl: &str, marker: &str) -> Verdict {
    let Ok(body) = capture_body(forwarded_jsonl) else {
        return Verdict::Violation("unparseable capture".into());
    };
    let mut strings = Vec::new();
    collect_strings(&body, &mut strings);
    if strings.iter().any(|s| s.contains(marker)) {
        return Verdict::Violation(format!(
            "non-text block JSON-dumped into a text string (contains {marker:?})"
        ));
    }
    Verdict::Conformant
}

/// Invariant (bug 006): an Anthropic `is_error: true` tool result MUST leave
/// an error marker the target model can see (`is_error`, or equivalent).
pub fn is_error_forwarded(forwarded_jsonl: &str) -> Verdict {
    let Ok(body) = capture_body(forwarded_jsonl) else {
        return Verdict::Violation("unparseable capture".into());
    };
    let dump = body_dump(&body);
    if dump.contains("\"is_error\":true") || dump.contains("\"is_error\": true") {
        return Verdict::Conformant;
    }
    Verdict::Violation(
        "is_error:true was dropped; forwarded body has no error marker on the tool result".into(),
    )
}

/// Invariant (bug 019): a translator MUST NOT invent `cache_control` the
/// client did not send.
pub fn no_invented_cache_control(forwarded_jsonl: &str) -> Verdict {
    let Ok(body) = capture_body(forwarded_jsonl) else {
        return Verdict::Violation("unparseable capture".into());
    };
    if body_dump(&body).contains("cache_control") {
        return Verdict::Violation(
            "forwarded body contains cache_control the client did not send".into(),
        );
    }
    Verdict::Conformant
}

/// Reason returned when a `tool_use` turn also carries a fabricated empty
/// `text` block. Tests match this string so a parse error cannot pass as the
/// 045 finding.
pub const EMPTY_TEXT_ALONGSIDE_TOOL_USE: &str =
    "Anthropic tool-call response contains an empty text block the model did not emit";

fn is_anthropic_sse(body: &str) -> bool {
    body.lines()
        .any(|line| line.starts_with("event: ") || line.starts_with("data: "))
}

fn block_is_tool_use(b: &Value) -> bool {
    b.get("type").and_then(Value::as_str) == Some("tool_use")
}

fn block_is_empty_text(b: &Value) -> bool {
    b.get("type").and_then(Value::as_str) == Some("text")
        && b.get("text")
            .and_then(Value::as_str)
            .unwrap_or("")
            .is_empty()
}

fn empty_text_with_tool_use(content: &[Value]) -> Verdict {
    if !content.iter().any(block_is_tool_use) {
        return Verdict::Conformant;
    }
    if content.iter().any(block_is_empty_text) {
        return Verdict::Violation(EMPTY_TEXT_ALONGSIDE_TOOL_USE.into());
    }
    Verdict::Conformant
}

/// Invariant (bug 045): an Anthropic Messages body that contains `tool_use`
/// MUST NOT also contain a fabricated empty `text` block. OpenAI-shaped
/// upstreams send `content: null` with `tool_calls`; a lossless translator
/// emits only the tool_use blocks. An empty text block is a phantom turn.
///
/// Accepts a non-stream JSON Messages body or an Anthropic SSE stream.
/// Streaming is judged on `content_block_start` events: an empty `text`
/// delta is normal, an empty `text` *block* next to `tool_use` is not.
pub fn no_empty_text_alongside_tool_use(response: &str) -> Verdict {
    if is_anthropic_sse(response) {
        return no_empty_text_alongside_tool_use_sse(response);
    }
    let v: Value = match serde_json::from_str(response) {
        Ok(v) => v,
        Err(e) => return Verdict::Violation(format!("unparseable response: {e}")),
    };
    let Some(content) = v.get("content").and_then(Value::as_array) else {
        return Verdict::Conformant;
    };
    empty_text_with_tool_use(content)
}

fn no_empty_text_alongside_tool_use_sse(sse: &str) -> Verdict {
    let events = sse_data_json(sse);
    let mut blocks = Vec::new();
    for e in &events {
        if e.get("type").and_then(Value::as_str) != Some("content_block_start") {
            continue;
        }
        if let Some(block) = e.get("content_block") {
            blocks.push(block.clone());
        }
    }
    empty_text_with_tool_use(&blocks)
}

/// Invariant (bug 009): a Responses `output` array MUST NOT contain a
/// `message` item whose `output_text.text` is JSON null.
pub fn no_phantom_null_output_text(response_json: &str) -> Verdict {
    let v: Value = match serde_json::from_str(response_json) {
        Ok(v) => v,
        Err(e) => return Verdict::Violation(format!("unparseable response: {e}")),
    };
    let Some(output) = v.get("output").and_then(Value::as_array) else {
        return Verdict::Conformant;
    };
    for item in output {
        if item.get("type").and_then(Value::as_str) != Some("message") {
            continue;
        }
        if let Some(content) = item.get("content").and_then(Value::as_array) {
            for part in content {
                if part.get("type").and_then(Value::as_str) == Some("output_text")
                    && part.get("text").is_none_or(Value::is_null)
                {
                    return Verdict::Violation(
                        "Responses output contains a message item with output_text.text=null"
                            .into(),
                    );
                }
            }
        }
    }
    Verdict::Conformant
}

/// Invariant (bug 008): a translation failure MUST NOT surface as a raw
/// Python `IndexError` (`"list index out of range"`).
pub fn no_indexerror_leak(error_or_response_json: &str) -> Verdict {
    let v: Value = match serde_json::from_str(error_or_response_json) {
        Ok(v) => v,
        Err(_) => return Verdict::Conformant,
    };
    let msg = v
        .pointer("/error/message")
        .and_then(Value::as_str)
        .unwrap_or("");
    if msg.contains("list index out of range") {
        return Verdict::Violation(
            "unhandled IndexError leaked to the client as 'list index out of range'".into(),
        );
    }
    Verdict::Conformant
}

/// Invariant (bug 020): the credential a proxy sends upstream MUST be the
/// deployment's configured key. A client JSON field `api_key` must not
/// replace it, and a later request that did not send `api_key` must not
/// inherit a previous caller's key.
pub fn upstream_bearer_is(forwarded_jsonl: &str, expected_bearer: &str) -> Verdict {
    let line = forwarded_jsonl
        .lines()
        .find(|l| !l.trim().is_empty())
        .unwrap_or("");
    let v: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(e) => return Verdict::Violation(format!("unparseable capture: {e}")),
    };
    let Some(headers) = v.get("headers").and_then(Value::as_object) else {
        return Verdict::Violation("capture has no headers object".into());
    };
    let auth = headers.iter().find_map(|(k, val)| {
        if k.eq_ignore_ascii_case("authorization") {
            val.as_str().map(str::to_owned)
        } else {
            None
        }
    });
    let Some(auth) = auth else {
        return Verdict::Violation("upstream request has no Authorization header".into());
    };
    let got = auth
        .strip_prefix("Bearer ")
        .or_else(|| auth.strip_prefix("bearer "))
        .unwrap_or(auth.as_str());
    if got == expected_bearer {
        Verdict::Conformant
    } else {
        Verdict::Violation(format!(
            "upstream Authorization used {got:?}, expected deployment key {expected_bearer:?}"
        ))
    }
}

/// Invariant (bug 023): a proxy MUST NOT forward client secret-bearing
/// headers (for example `api-key` or `OpenAI-Organization`) to the
/// upstream. The capture's header values must not contain `needle`.
pub fn upstream_omits_header_value(forwarded_jsonl: &str, needle: &str) -> Verdict {
    let line = forwarded_jsonl
        .lines()
        .find(|l| !l.trim().is_empty())
        .unwrap_or("");
    let v: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(e) => return Verdict::Violation(format!("unparseable capture: {e}")),
    };
    let Some(headers) = v.get("headers").and_then(Value::as_object) else {
        return Verdict::Violation("capture has no headers object".into());
    };
    for (name, val) in headers {
        if val.as_str().is_some_and(|s| s.contains(needle)) {
            return Verdict::Violation(format!(
                "upstream header {name:?} contains client secret marker {needle:?}"
            ));
        }
    }
    Verdict::Conformant
}

/// Invariant (bug 024): a proxy MUST NOT return deployment secrets
/// (`extra_headers`, `aws_session_token`, query keys in `api_base`) in a
/// client-visible response body. `/v1/models` and `/health/liveliness`
/// are the controls that already satisfy this.
pub fn response_omits_secret(body: &str, needle: &str) -> Verdict {
    if body.contains(needle) {
        Verdict::Violation(format!(
            "response body contains deployment secret marker {needle:?}"
        ))
    } else {
        Verdict::Conformant
    }
}

/// Invariant (bug 030): the non-streaming sibling of
/// [`anthropic_toolcall_stop_reason`]. If a non-streamed Anthropic Messages
/// response carries a `tool_use` content block, its top-level `stop_reason`
/// MUST be `tool_use`. Pairing the two checkers is what isolates a defect to
/// the streaming serializer: same turn, same upstream, one shape conformant
/// and the other not.
pub fn anthropic_response_toolcall_stop_reason(response_json: &str) -> Verdict {
    let Ok(body) = serde_json::from_str::<Value>(response_json) else {
        return Verdict::Violation("response body is not valid JSON".to_string());
    };
    let has_tool_use = body
        .get("content")
        .and_then(Value::as_array)
        .is_some_and(|blocks| {
            blocks
                .iter()
                .any(|b| b.get("type").and_then(Value::as_str) == Some("tool_use"))
        });
    if !has_tool_use {
        return Verdict::Conformant; // nothing to check
    }
    match body.get("stop_reason").and_then(Value::as_str) {
        Some("tool_use") => Verdict::Conformant,
        other => Verdict::Violation(format!(
            "tool_use block present but stop_reason is {other:?}, expected \"tool_use\""
        )),
    }
}

/// Invariant (bug 032): a client-supplied stop sequence MUST reach the upstream.
/// A gateway that drops it silently changes where the model stops generating,
/// and the client cannot detect that its instruction was discarded.
pub fn stop_sequence_forwarded(forwarded_jsonl: &str, sequence: &str) -> Verdict {
    if jsonl_contains_string(forwarded_jsonl, sequence) {
        Verdict::Conformant
    } else {
        Verdict::Violation(format!(
            "stop sequence {sequence:?} is absent from the forwarded upstream body"
        ))
    }
}

/// Structured output must still be named on the forwarded upstream body.
/// A distinctive schema token in user text is not enough: the wire field
/// itself has to survive (`type: json_schema` or a `json_schema` key),
/// outside conversation content.
pub fn json_schema_forwarded(forwarded_jsonl: &str) -> Verdict {
    match capture_body(forwarded_jsonl) {
        Ok(body) if has_json_schema_wire_field(&body) => Verdict::Conformant,
        Ok(_) => Verdict::Violation(
            "json_schema / structured output is absent from the forwarded upstream body".into(),
        ),
        Err(_) => Verdict::Violation("unparseable capture".into()),
    }
}

/// True when `json_schema` is a request field, not a word in the prompt.
/// Skips `messages` / `input` / `content` so a user string cannot mint Conformant.
fn has_json_schema_wire_field(v: &Value) -> bool {
    match v {
        Value::Object(m) => {
            if m.contains_key("json_schema")
                || m.get("type").and_then(Value::as_str) == Some("json_schema")
            {
                return true;
            }
            m.iter().any(|(k, child)| {
                !matches!(k.as_str(), "messages" | "input" | "content")
                    && has_json_schema_wire_field(child)
            })
        }
        Value::Array(a) => a.iter().any(has_json_schema_wire_field),
        _ => false,
    }
}

/// True when an upstream response says the turn was cut off at the output-token
/// ceiling. Understands both spellings: Responses (`status: "incomplete"` with
/// `incomplete_details.reason: "max_output_tokens"`) and Chat Completions
/// (`finish_reason: "length"`).
fn upstream_truncated(upstream: &Value) -> bool {
    let responses_shape = upstream.get("status").and_then(Value::as_str) == Some("incomplete")
        && upstream
            .pointer("/incomplete_details/reason")
            .and_then(Value::as_str)
            .is_some_and(|r| r.contains("max_output_tokens") || r.contains("max_tokens"));
    let chat_shape = upstream
        .pointer("/choices/0/finish_reason")
        .and_then(Value::as_str)
        == Some("length");
    responses_shape || chat_shape
}

/// Invariant (bug 035): a turn the UPSTREAM truncated MUST NOT be reported to an
/// Anthropic client as `end_turn`. Anthropic spells truncation `max_tokens`;
/// `end_turn` asserts the model finished on its own, so a caller cannot tell a
/// complete answer from a cut-off one and will not know to continue.
///
/// Both halves of the exchange are required, and that is the point: `end_turn` is
/// also the ordinary success value, so a checker that saw only the client response
/// would flag every finished turn and could never have a passing control. The
/// upstream payload is what makes "truncated" observable.
pub fn truncation_preserved(upstream_response_json: &str, client_response_json: &str) -> Verdict {
    let Ok(upstream) = serde_json::from_str::<Value>(upstream_response_json) else {
        return Verdict::Violation("upstream body is not valid JSON".to_string());
    };
    let Ok(client) = serde_json::from_str::<Value>(client_response_json) else {
        return Verdict::Violation("client body is not valid JSON".to_string());
    };
    if !upstream_truncated(&upstream) {
        return Verdict::Conformant; // nothing to preserve
    }
    match client.get("stop_reason").and_then(Value::as_str) {
        Some("end_turn") => Verdict::Violation(
            "upstream truncated the turn at the token ceiling but the client was told \
             stop_reason \"end_turn\"; truncation is unreportable to the caller"
                .to_string(),
        ),
        _ => Verdict::Conformant,
    }
}

/// Invariant (bug 036): a non-empty upstream turn MUST NOT reach the client as an
/// empty `content` array. Whatever the upstream said (text, a refusal, a tool
/// call), the client has to receive something; an empty turn is indistinguishable
/// from the model saying nothing at all.
pub fn response_content_not_empty(response_json: &str) -> Verdict {
    let Ok(body) = serde_json::from_str::<Value>(response_json) else {
        return Verdict::Violation("response body is not valid JSON".to_string());
    };
    match body.get("content").and_then(Value::as_array) {
        Some(blocks) if blocks.is_empty() => Verdict::Violation(
            "client received an empty content array for a turn the upstream filled".to_string(),
        ),
        Some(_) => Verdict::Conformant,
        None => Verdict::Violation("response has no content array".to_string()),
    }
}

/// Invariant (bug 037): when a gateway rewrites an upstream tool-call id to satisfy
/// a client-side charset contract, it MUST reverse the rewrite before sending the
/// id back upstream. The upstream never issued the sanitized id; echoing it breaks
/// the multi-turn tool loop against any provider that validates call ids.
pub fn toolcall_id_restored_upstream(forwarded_jsonl: &str, original_id: &str) -> Verdict {
    if jsonl_contains_string(forwarded_jsonl, original_id) {
        Verdict::Conformant
    } else {
        Verdict::Violation(format!(
            "upstream id {original_id:?} was not restored; the sanitized form was sent back instead"
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn id_contract_basics() {
        assert!(id_conforms("call_abc123"));
        assert!(id_conforms("functions_list_skills_0"));
        assert!(!id_conforms("functions.list_skills:0")); // dot and colon
        assert!(!id_conforms(&"x".repeat(65))); // too long
        assert!(!id_conforms("")); // empty
    }

    #[test]
    fn truncation_checker_needs_the_upstream_to_decide() {
        let truncated =
            r#"{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"}}"#;
        let finished = r#"{"status":"completed"}"#;
        let client_end_turn = r#"{"stop_reason":"end_turn"}"#;

        // Truncated upstream reported as end_turn: the defect.
        assert!(matches!(
            truncation_preserved(truncated, client_end_turn),
            Verdict::Violation(_)
        ));
        // The SAME client body is conformant when the upstream did not truncate.
        // A checker reading only the client side could not tell these apart.
        assert_eq!(
            truncation_preserved(finished, client_end_turn),
            Verdict::Conformant
        );
        // Truncated and correctly reported.
        assert_eq!(
            truncation_preserved(truncated, r#"{"stop_reason":"max_tokens"}"#),
            Verdict::Conformant
        );
        // Chat-completions spelling of the same upstream signal.
        assert!(matches!(
            truncation_preserved(
                r#"{"choices":[{"finish_reason":"length"}]}"#,
                client_end_turn
            ),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn empty_content_checker_flags_empty_array() {
        assert!(matches!(
            response_content_not_empty(r#"{"content":[]}"#),
            Verdict::Violation(_)
        ));
        assert_eq!(
            response_content_not_empty(r#"{"content":[{"type":"text","text":"hi"}]}"#),
            Verdict::Conformant
        );
        assert!(matches!(
            response_content_not_empty(r#"{"id":"x"}"#),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn json_schema_forwarded_needs_the_wire_field() {
        let present = r#"{"body":{"text":{"format":{"type":"json_schema"}}}}"#;
        assert_eq!(json_schema_forwarded(present), Verdict::Conformant);
        let openai = r#"{"body":{"response_format":{"type":"json_schema"}}}"#;
        assert_eq!(json_schema_forwarded(openai), Verdict::Conformant);
        let dropped = r#"{"body":{"model":"x","messages":[{"content":"ping"}]}}"#;
        assert!(matches!(
            json_schema_forwarded(dropped),
            Verdict::Violation(_)
        ));
        // Prompt text naming the token is not a surviving wire field.
        let in_user_text =
            r#"{"body":{"model":"x","messages":[{"content":"please use json_schema"}]}}"#;
        assert!(matches!(
            json_schema_forwarded(in_user_text),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn toolcall_id_restore_checker() {
        let restored = r#"{"path":"/v1/responses","body":{"input":[{"call_id":"call/a+b"}]}}"#;
        assert_eq!(
            toolcall_id_restored_upstream(restored, "call/a+b"),
            Verdict::Conformant
        );
        let sanitized =
            r#"{"path":"/v1/responses","body":{"input":[{"call_id":"hash_call_a_b"}]}}"#;
        assert!(matches!(
            toolcall_id_restored_upstream(sanitized, "call/a+b"),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn response_stop_reason_checker_flags_end_turn() {
        let violating = r#"{"content":[{"type":"tool_use","name":"x"}],"stop_reason":"end_turn"}"#;
        assert!(matches!(
            anthropic_response_toolcall_stop_reason(violating),
            Verdict::Violation(_)
        ));
        let conformant = r#"{"content":[{"type":"tool_use","name":"x"}],"stop_reason":"tool_use"}"#;
        assert_eq!(
            anthropic_response_toolcall_stop_reason(conformant),
            Verdict::Conformant
        );
        // A turn with no tool_use block has nothing to check.
        let text_only = r#"{"content":[{"type":"text","text":"hi"}],"stop_reason":"end_turn"}"#;
        assert_eq!(
            anthropic_response_toolcall_stop_reason(text_only),
            Verdict::Conformant
        );
    }

    #[test]
    fn stop_reason_checker_flags_end_turn() {
        let sse = "event: content_block_start\n\
            data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"tool_use\",\"name\":\"x\"}}\n\n\
            event: message_delta\n\
            data: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\"}}\n";
        assert!(matches!(
            anthropic_toolcall_stop_reason(sse),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn upstream_bearer_flags_overridden_key() {
        let cap = r#"{"path":"/v1/chat/completions","headers":{"authorization":"Bearer CANARY"},"body":{}}"#;
        assert!(matches!(
            upstream_bearer_is(cap, "sk-x"),
            Verdict::Violation(_)
        ));
        let ok = r#"{"path":"/v1/chat/completions","headers":{"authorization":"Bearer sk-x"},"body":{}}"#;
        assert_eq!(upstream_bearer_is(ok, "sk-x"), Verdict::Conformant);
    }

    #[test]
    fn upstream_omits_header_value_flags_forwarded_canary() {
        let cap = r#"{"headers":{"api-key":"CANARY_AZURE_API_KEY"}}"#;
        assert!(matches!(
            upstream_omits_header_value(cap, "CANARY_AZURE_API_KEY"),
            Verdict::Violation(_)
        ));
        let ok = r#"{"headers":{"authorization":"Bearer x"}}"#;
        assert_eq!(
            upstream_omits_header_value(ok, "CANARY_AZURE_API_KEY"),
            Verdict::Conformant
        );
    }

    #[test]
    fn empty_text_alongside_tool_use_flags_phantom() {
        let bad = r#"{"content":[{"type":"text","text":""},{"type":"tool_use","name":"Read","id":"x","input":{}}],"stop_reason":"tool_use"}"#;
        assert_eq!(
            no_empty_text_alongside_tool_use(bad),
            Verdict::Violation(EMPTY_TEXT_ALONGSIDE_TOOL_USE.into())
        );
        let ok = r#"{"content":[{"type":"tool_use","name":"Read","id":"x","input":{}}],"stop_reason":"tool_use"}"#;
        assert_eq!(no_empty_text_alongside_tool_use(ok), Verdict::Conformant);
        assert_ne!(
            no_empty_text_alongside_tool_use("not-json"),
            Verdict::Violation(EMPTY_TEXT_ALONGSIDE_TOOL_USE.into()),
            "malformed JSON must not be reported as the 045 phantom"
        );
        let sse_ok = "event: content_block_start\n\
            data: {\"type\":\"content_block_start\",\"content_block\":{\"type\":\"tool_use\",\"name\":\"Read\"}}\n\n";
        assert_eq!(
            no_empty_text_alongside_tool_use(sse_ok),
            Verdict::Conformant
        );
        let sse_bad = "event: content_block_start\n\
            data: {\"type\":\"content_block_start\",\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n\
            event: content_block_start\n\
            data: {\"type\":\"content_block_start\",\"content_block\":{\"type\":\"tool_use\",\"name\":\"Read\"}}\n\n";
        assert_eq!(
            no_empty_text_alongside_tool_use(sse_bad),
            Verdict::Violation(EMPTY_TEXT_ALONGSIDE_TOOL_USE.into())
        );
        let sse_empty_delta = "event: content_block_start\n\
            data: {\"type\":\"content_block_start\",\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n\
            event: content_block_delta\n\
            data: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\"\"}}\n\n";
        assert_eq!(
            no_empty_text_alongside_tool_use(sse_empty_delta),
            Verdict::Conformant,
            "empty text without tool_use is not 045"
        );
    }

    #[test]
    fn response_omits_secret_flags_canary() {
        assert!(matches!(
            response_omits_secret(
                r#"{"extra_headers":{"Authorization":"Bearer CANARY_EXTRA_HEADERS_AUTHORIZATION"}}"#,
                "CANARY_EXTRA_HEADERS_AUTHORIZATION"
            ),
            Verdict::Violation(_)
        ));
        assert_eq!(
            response_omits_secret(r#"{"id":"mock"}"#, "CANARY_EXTRA_HEADERS_AUTHORIZATION"),
            Verdict::Conformant
        );
    }
}
