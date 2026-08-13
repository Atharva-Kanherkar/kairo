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
        .find_map(|e| e.pointer("/delta/stop_reason").and_then(Value::as_str).map(str::to_owned));
    match stop_reason.as_deref() {
        Some("tool_use") => Verdict::Conformant,
        other => Verdict::Violation(format!(
            "tool_use block present but stop_reason is {:?}, expected \"tool_use\"",
            other
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
            .map(|a| !a.is_empty())
            .unwrap_or(false)
    });
    if !has_toolcall_delta {
        return Verdict::Conformant;
    }
    let finish = chunks
        .iter()
        .filter_map(|c| c.pointer("/choices/0/finish_reason").and_then(Value::as_str))
        .last()
        .map(str::to_owned);
    match finish.as_deref() {
        Some("tool_calls") => Verdict::Conformant,
        other => Verdict::Violation(format!(
            "tool_calls delta present but finish_reason is {:?}, expected \"tool_calls\"",
            other
        )),
    }
}

/// True when `id` satisfies the OpenAI / Anthropic tool-call id contract:
/// `^[A-Za-z0-9_-]{1,64}$`.
pub fn id_conforms(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// Invariant (bug 004): every tool-call id an OpenAI chat response carries
/// MUST satisfy the id contract. Catches the reasoning-signature smuggled into
/// a 382-char id full of `+ / =`.
pub fn openai_toolcall_id_charset(response_json: &str) -> Verdict {
    let v: Value = match serde_json::from_str(response_json) {
        Ok(v) => v,
        Err(e) => return Verdict::Violation(format!("unparseable response: {e}")),
    };
    let calls = v.pointer("/choices/0/message/tool_calls").and_then(Value::as_array);
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
        assert!(matches!(anthropic_toolcall_stop_reason(sse), Verdict::Violation(_)));
    }
}
