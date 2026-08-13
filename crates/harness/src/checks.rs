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
}
