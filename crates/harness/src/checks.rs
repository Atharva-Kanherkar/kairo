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

#[derive(Clone, Copy)]
pub enum FunctionToolFormat {
    OpenAiResponses,
    OpenAiChat,
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

/// Invariant (bug 076): a streamed Anthropic turn the upstream safety-filtered
/// must terminate with the same safety `stop_reason` the scenario demands.
/// `expected` is the refusal-family reason for the filtered turn (e.g.
/// "refusal"); any other terminal reason, including a clean `end_turn` or a
/// missing terminal, means the transport decided the safety verdict instead
/// of the upstream signal.
pub fn anthropic_stream_safety_stop_reason(sse: &str, expected: &str) -> Verdict {
    let events = sse_data_json(sse);
    let stop_reason = events
        .iter()
        .filter(|e| e.get("type").and_then(Value::as_str) == Some("message_delta"))
        .find_map(|e| {
            e.pointer("/delta/stop_reason")
                .and_then(Value::as_str)
                .map(str::to_owned)
        });
    match stop_reason {
        Some(reason) if reason == expected => Verdict::Conformant,
        other => Verdict::Violation(format!(
            "stream terminal stop_reason is {other:?}, expected {expected:?}"
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

/// Invariant (bug 074): one client-visible Responses stream represents one
/// response lifecycle. Internal agent or tool rounds must not introduce a second
/// `response.created` / `response.completed` pair or reuse an output index for an
/// unrelated item. Standard SDK accumulators keep one snapshot per stream and
/// cannot safely merge a fresh response namespace into it.
pub fn responses_single_lifecycle(sse: &str) -> Verdict {
    let mut events = Vec::new();
    for line in sse.lines() {
        let Some(data) = line.strip_prefix("data: ") else {
            continue;
        };
        if data == "[DONE]" {
            continue;
        }
        let event: Value = match serde_json::from_str(data) {
            Ok(event) => event,
            Err(error) => {
                return Verdict::Violation(format!(
                    "Responses stream contains an unparseable data frame: {error}"
                ));
            }
        };
        events.push(event);
    }
    if events.is_empty() {
        return Verdict::Violation("Responses stream contains no JSON events".into());
    }

    let created = events
        .iter()
        .enumerate()
        .filter(|(_, event)| event.get("type").and_then(Value::as_str) == Some("response.created"))
        .collect::<Vec<_>>();
    let completed = events
        .iter()
        .enumerate()
        .filter(|(_, event)| {
            event.get("type").and_then(Value::as_str) == Some("response.completed")
        })
        .collect::<Vec<_>>();
    if created.len() != 1 || completed.len() != 1 {
        return Verdict::Violation(format!(
            "one Responses stream has {} response.created and {} response.completed events, expected one each",
            created.len(),
            completed.len()
        ));
    }
    if created[0].0 >= completed[0].0 || completed[0].0 + 1 != events.len() {
        return Verdict::Violation(
            "response.completed is not the sole terminal JSON event for its lifecycle".into(),
        );
    }
    let created_id = created[0].1.pointer("/response/id").and_then(Value::as_str);
    let completed_id = completed[0]
        .1
        .pointer("/response/id")
        .and_then(Value::as_str);
    if created_id.is_none() || created_id != completed_id {
        return Verdict::Violation(format!(
            "response identity changed between created {created_id:?} and completed {completed_id:?}"
        ));
    }

    let mut indexes = std::collections::BTreeMap::new();
    for event in &events {
        if event.get("type").and_then(Value::as_str) != Some("response.output_item.added") {
            continue;
        }
        let Some(index) = event.get("output_index").and_then(Value::as_u64) else {
            return Verdict::Violation(
                "response.output_item.added has no numeric output_index".into(),
            );
        };
        let Some(item_id) = event.pointer("/item/id").and_then(Value::as_str) else {
            return Verdict::Violation("response.output_item.added has no item id".into());
        };
        if let Some(previous) = indexes.insert(index, item_id) {
            if previous != item_id {
                return Verdict::Violation(format!(
                    "output_index {index} identifies unrelated items {previous:?} and {item_id:?}"
                ));
            }
        }
    }
    Verdict::Conformant
}

/// Invariant (bug 085): a Responses stream MAY legitimately contain more than
/// one `response.created` event, because a mid-stream provider fallback is a
/// documented feature (LiteLLM PR #28215). But once a `response.output_index`
/// has been assigned to an item by one lifecycle, no LATER lifecycle in the
/// same public stream may assign that same index to a *different* item.
///
/// This is deliberately narrower than [`responses_single_lifecycle`]: a
/// fallback that replays before any output item was ever announced (the
/// primary failed pre-first-chunk) is conformant here even though it also
/// produces two `response.created` events. What is never conformant is an
/// index collision between unrelated items, because the official OpenAI SDK's
/// per-event accumulator dispatches on `output_index` without knowing a new
/// lifecycle started, so a reused index corrupts its snapshot (an already
/// materialized item is replaced or misread) regardless of whether the
/// colliding items are the same type.
pub fn responses_fallback_preserves_delivered_indexes(sse: &str) -> Verdict {
    let events = sse_data_json(sse);
    if events.is_empty() {
        return Verdict::Violation("Responses stream contains no JSON events".into());
    }
    let mut owner: std::collections::BTreeMap<u64, &str> = std::collections::BTreeMap::new();
    for event in &events {
        if event.get("type").and_then(Value::as_str) != Some("response.output_item.added") {
            continue;
        }
        let Some(index) = event.get("output_index").and_then(Value::as_u64) else {
            return Verdict::Violation(
                "response.output_item.added has no numeric output_index".into(),
            );
        };
        let Some(item_id) = event.pointer("/item/id").and_then(Value::as_str) else {
            return Verdict::Violation("response.output_item.added has no item id".into());
        };
        if let Some(previous) = owner.insert(index, item_id) {
            if previous != item_id {
                return Verdict::Violation(format!(
                    "output_index {index} was already assigned to {previous:?} by an earlier \
                     lifecycle and is reused for unrelated item {item_id:?} by a later one"
                ));
            }
        }
    }
    Verdict::Conformant
}

/// Invariant (bug 085): once a public Responses stream has announced an
/// output item, it must not start another response lifecycle. LiteLLM's own
/// chat-completions fallback re-raises instead of retrying once any content
/// has streamed (BerriAI/litellm#34627), because a retry "would otherwise send
/// a second, unrelated response after content the client already received".
/// A second `response.created` before any output item, from a fallback for a
/// primary that failed before producing output, is conformant.
pub fn responses_no_restart_after_output(sse: &str) -> Verdict {
    let events = sse_data_json(sse);
    if events.is_empty() {
        return Verdict::Violation("Responses stream contains no JSON events".into());
    }
    let mut first_item: Option<&str> = None;
    for event in &events {
        match event.get("type").and_then(Value::as_str) {
            Some("response.output_item.added") if first_item.is_none() => {
                first_item = Some(
                    event
                        .pointer("/item/id")
                        .and_then(Value::as_str)
                        .unwrap_or("<no id>"),
                );
            }
            Some("response.created") => {
                if let Some(item) = first_item {
                    return Verdict::Violation(format!(
                        "a new response lifecycle started after output item {item:?} was already delivered"
                    ));
                }
            }
            _ => {}
        }
    }
    Verdict::Conformant
}

fn output_item_ids(events: &[Value]) -> Vec<String> {
    events
        .iter()
        .filter(|e| e.get("type").and_then(Value::as_str) == Some("response.output_item.added"))
        .filter_map(|e| e.pointer("/item/id").and_then(Value::as_str))
        .map(str::to_owned)
        .collect()
}

/// Invariant (bug 085), across hops: when an upstream attempt ends without
/// completing after the client already received one of its output items, no
/// output item from a later upstream attempt may appear in the same public
/// stream. This holds however the gateway renumbers indexes or merges
/// lifecycles, so it rejects a fallback that is spliced in after delivered
/// output even when the client-side stream looks like one clean response.
/// `upstream_sses` are the raw provider streams in attempt order.
pub fn responses_fallback_not_spliced_after_delivery(
    client_sse: &str,
    upstream_sses: &[&str],
) -> Verdict {
    let client = sse_data_json(client_sse);
    if client.is_empty() {
        return Verdict::Violation("client stream contains no JSON events".into());
    }
    let delivered = output_item_ids(&client);
    let attempts: Vec<Vec<Value>> = upstream_sses.iter().map(|s| sse_data_json(s)).collect();
    for (index, attempt) in attempts.iter().enumerate() {
        let completed = attempt.iter().any(|e| {
            matches!(
                e.get("type").and_then(Value::as_str),
                Some("response.completed" | "response.incomplete")
            )
        });
        let forwarded: Vec<String> = output_item_ids(attempt)
            .into_iter()
            .filter(|id| delivered.contains(id))
            .collect();
        if completed || forwarded.is_empty() {
            continue;
        }
        for later in &attempts[index + 1..] {
            if let Some(spliced) = output_item_ids(later)
                .into_iter()
                .find(|id| delivered.contains(id))
            {
                return Verdict::Violation(format!(
                    "upstream attempt {index} failed after the client received {forwarded:?}; \
                     item {spliced:?} from a later attempt was spliced into the same stream"
                ));
            }
        }
    }
    Verdict::Conformant
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
pub fn capture_body(jsonl: &str) -> Result<Value, String> {
    let line = jsonl.lines().find(|l| !l.trim().is_empty()).unwrap_or("");
    parse_capture_line(line)
}

/// Every non-empty jsonl record as `(line, body)`. Use this when a fixture
/// claims N/N: `capture_body` (and checkers that call it) only read line 1.
pub fn capture_records(jsonl: &str) -> Result<Vec<(String, Value)>, String> {
    jsonl
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|line| parse_capture_line(line).map(|body| (line.to_string(), body)))
        .collect()
}

/// A provider-owned top-level request `id` MUST survive a pass-through hop.
/// The field is compared as JSON, so string, number, and null IDs are checked
/// without coercion. If the client did not send an ID, there is nothing to
/// preserve.
pub fn provider_request_id_preserved(client_json: &str, forwarded_json: &str) -> Verdict {
    let client: Value = match serde_json::from_str(client_json) {
        Ok(value) => value,
        Err(error) => return Verdict::Violation(format!("unparseable client JSON: {error}")),
    };
    let forwarded: Value = match serde_json::from_str(forwarded_json) {
        Ok(value) => value,
        Err(error) => return Verdict::Violation(format!("unparseable forwarded JSON: {error}")),
    };
    let Some(client_object) = client.as_object() else {
        return Verdict::Violation("client JSON is not an object".into());
    };
    let Some(expected_id) = client_object.get("id") else {
        return Verdict::Conformant;
    };
    match forwarded.get("id") {
        Some(actual_id) if actual_id == expected_id => Verdict::Conformant,
        Some(actual_id) => Verdict::Violation(format!(
            "provider-owned top-level id changed from {expected_id} to {actual_id}"
        )),
        None => Verdict::Violation("provider-owned top-level id was dropped".into()),
    }
}

/// Check the adaptive-thinking invariant from either a case-array fixture or
/// capture-rig JSONL. A 200 response with a non-empty forwarded request and no
/// `thinking` or `reasoning` field is the reproduced loss. A gateway that
/// forwards an explicit reasoning configuration or rejects the unsupported
/// request is conformant.
pub fn ogx_adaptive_thinking_loss(evidence: &str, expected_trials: usize) -> Verdict {
    let parsed = serde_json::from_str::<Value>(evidence).ok();
    let cases: Vec<Value> = match parsed {
        Some(Value::Array(items)) => items,
        Some(_) => return Verdict::Violation("case fixture is not an array".to_owned()),
        None => match capture_records(evidence) {
            Ok(records) => records
                .into_iter()
                .map(|(_, body)| {
                    serde_json::json!({
                        "client_status": 200,
                        "forwarded": body,
                    })
                })
                .collect(),
            Err(error) => return Verdict::Violation(error),
        },
    };
    if cases.len() != expected_trials {
        return Verdict::Violation(format!(
            "expected {expected_trials} trials, found {}",
            cases.len()
        ));
    }
    let mut saw_forwarded_success = false;
    for (index, case) in cases.iter().enumerate() {
        let status = case.get("client_status").and_then(Value::as_i64);
        let forwarded = case.get("forwarded").unwrap_or(&Value::Null);
        if status == Some(400) && forwarded.is_null() {
            continue;
        }
        if status != Some(200) {
            return Verdict::Violation(format!(
                "trial {} has status {status:?}, expected 200 or fail-closed 400",
                index + 1
            ));
        }
        let Some(messages) = forwarded.get("messages").and_then(Value::as_array) else {
            return Verdict::Violation(format!(
                "trial {} has no non-empty forwarded messages",
                index + 1
            ));
        };
        if messages.is_empty() {
            return Verdict::Violation(format!(
                "trial {} forwarded an empty messages array",
                index + 1
            ));
        }
        saw_forwarded_success = true;
        let has_config = object_has_key(forwarded, "thinking")
            || object_has_key(forwarded, "reasoning")
            || object_has_key(forwarded, "reasoning_effort");
        if !has_config {
            return Verdict::Violation(format!(
                "trial {} accepted adaptive thinking without a thinking or reasoning configuration",
                index + 1
            ));
        }
    }
    if !saw_forwarded_success {
        return Verdict::Conformant;
    }
    Verdict::Conformant
}

fn object_has_key(value: &Value, key: &str) -> bool {
    match value {
        Value::Object(fields) => {
            fields.contains_key(key) || fields.values().any(|child| object_has_key(child, key))
        }
        Value::Array(items) => items.iter().any(|child| object_has_key(child, key)),
        _ => false,
    }
}

fn parse_capture_line(line: &str) -> Result<Value, String> {
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

fn collect_keyed_strings(v: &Value, key: &str, out: &mut Vec<String>) {
    match v {
        Value::Array(items) => items
            .iter()
            .for_each(|item| collect_keyed_strings(item, key, out)),
        Value::Object(fields) => {
            for (name, value) in fields {
                if name == key {
                    if let Some(text) = value.as_str() {
                        out.push(text.to_string());
                    }
                }
                collect_keyed_strings(value, key, out);
            }
        }
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

/// Invariant (bug 072): Anthropic `tool_choice: {"type": "any"}` forwarded to an
/// OpenAI-compatible backend MUST be translated to `"required"`. Emitting the bare
/// string `"any"` leaks the Anthropic dialect onto the OpenAI wire where it is rejected
/// with HTTP 400.
pub fn anthropic_tool_choice_any_mapped_to_required(forwarded_jsonl: &str) -> Verdict {
    let Ok(body) = capture_body(forwarded_jsonl) else {
        return Verdict::Violation("unparseable capture".into());
    };
    let tool_choice = body.get("tool_choice");
    if tool_choice == Some(&Value::String("required".into())) {
        Verdict::Conformant
    } else if tool_choice == Some(&Value::String("any".into()))
        || body.pointer("/tool_choice/type").and_then(Value::as_str) == Some("any")
    {
        Verdict::Violation(
            "tool_choice was forwarded as 'any' instead of being mapped to 'required'".into(),
        )
    } else {
        Verdict::Violation(format!(
            "expected tool_choice to be 'required', got {tool_choice:?}"
        ))
    }
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

/// Invariant (bug 064): a strict function-tool constraint must survive as
/// `strict: true` on the target format's function tool.
pub fn tool_strict_forwarded(
    forwarded_jsonl: &str,
    tool_name: &str,
    target_format: FunctionToolFormat,
) -> Verdict {
    let Ok(body) = capture_body(forwarded_jsonl) else {
        return Verdict::Violation("unparseable capture".into());
    };
    let Some(tools) = body.get("tools").and_then(Value::as_array) else {
        return Verdict::Violation("forwarded body has no tools array".into());
    };
    let Some(tool) = tools.iter().find(|tool| {
        tool.get("type").and_then(Value::as_str) == Some("function")
            && match target_format {
                FunctionToolFormat::OpenAiResponses => {
                    tool.get("name").and_then(Value::as_str) == Some(tool_name)
                }
                FunctionToolFormat::OpenAiChat => {
                    tool.pointer("/function/name").and_then(Value::as_str) == Some(tool_name)
                }
            }
    }) else {
        return Verdict::Violation(format!(
            "forwarded body has no function tool named {tool_name:?}"
        ));
    };
    let strict = match target_format {
        FunctionToolFormat::OpenAiResponses => tool.get("strict").and_then(Value::as_bool),
        FunctionToolFormat::OpenAiChat => tool.pointer("/function/strict").and_then(Value::as_bool),
    };
    if strict == Some(true) {
        Verdict::Conformant
    } else {
        Verdict::Violation(format!(
            "strict:true was dropped from forwarded function tool {tool_name:?}"
        ))
    }
}

/// Invariant (bug 065): translated instruction messages retain their roles,
/// content, and precedence order on the OpenAI Chat wire.
pub fn instruction_messages_preserved(forwarded_jsonl: &str, expected: &[(&str, &str)]) -> Verdict {
    let Ok(body) = capture_body(forwarded_jsonl) else {
        return Verdict::Violation("unparseable capture".into());
    };
    let Some(messages) = body.get("messages").and_then(Value::as_array) else {
        return Verdict::Violation("forwarded body has no messages array".into());
    };
    let observed = messages
        .iter()
        .map(|message| {
            (
                message.get("role").and_then(Value::as_str).unwrap_or(""),
                message.get("content").and_then(Value::as_str).unwrap_or(""),
            )
        })
        .collect::<Vec<_>>();
    if observed == expected {
        Verdict::Conformant
    } else {
        Verdict::Violation(format!(
            "instruction messages changed: expected {expected:?}, observed {observed:?}"
        ))
    }
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

/// Invariant (bug 024 / bug 071): a client-visible response body MUST NOT
/// contain the caller-supplied secret marker `needle`. This checker knows
/// nothing about which fields carry secrets; it is a whole-body substring
/// scan, and the caller is responsible for choosing a marker (a canary
/// planted in `extra_headers`, `aws_session_token`, an `api_base` query key,
/// or anywhere else) that would only appear if that specific secret leaked.
pub fn response_omits_secret(body: &str, needle: &str) -> Verdict {
    if body.contains(needle) {
        Verdict::Violation(format!(
            "response body contains deployment secret marker {needle:?}"
        ))
    } else {
        Verdict::Conformant
    }
}

/// Invariant (bug 079): a credential known to be invalid must be rejected at
/// the public gateway boundary, before any request is authenticated to an
/// upstream provider. `client_exchange` is a request and response separated
/// by the transcript marker, and `upstream_request` is empty when no upstream
/// connection was observed.
pub fn invalid_credential_rejected_before_upstream(
    client_exchange: &str,
    upstream_request: &str,
) -> Verdict {
    let Some((_, response)) = client_exchange.split_once("--- RESPONSE ---") else {
        return Verdict::Violation("client transcript has no response marker".to_owned());
    };
    let status = response
        .lines()
        .find(|line| line.starts_with("HTTP/"))
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok());
    let rejected = matches!(status, Some(401 | 403));
    let reached_upstream = !upstream_request.trim().is_empty();

    if rejected && !reached_upstream {
        Verdict::Conformant
    } else {
        Verdict::Violation(format!(
            "invalid credential received client status {status:?}; upstream connection observed: {reached_upstream}"
        ))
    }
}

/// Invariant (bug 076): an outbound HTTP request selected by an authenticated
/// caller MUST NOT contain a credential outside that caller's scope. The
/// caller supplies a unique marker for the privileged credential. This scans
/// the complete request so it remains valid if a provider changes its auth
/// header name or moves authentication into the request target or body.
pub fn outbound_request_omits_secret(raw_http_request: &str, needle: &str) -> Verdict {
    if needle.is_empty() {
        return Verdict::Violation("secret marker is empty".to_string());
    }
    let Some((head, _body)) = raw_http_request.split_once("\r\n\r\n") else {
        return Verdict::Violation("outbound capture is not a complete HTTP request".to_string());
    };
    let Some(request_line) = head.lines().next() else {
        return Verdict::Violation("outbound capture has no request line".to_string());
    };
    let mut parts = request_line.split_whitespace();
    if parts.next().is_none()
        || parts.next().is_none()
        || !parts
            .next()
            .is_some_and(|version| version.starts_with("HTTP/"))
        || parts.next().is_some()
    {
        return Verdict::Violation("outbound capture has a malformed request line".to_string());
    }
    if raw_http_request.contains(needle) {
        Verdict::Violation(format!(
            "outbound request contains privileged credential marker {needle:?}"
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

/// 040 / 042 / 051 finding. Parse errors must not pass as this reason.
pub const JSON_SCHEMA_ABSENT: &str =
    "json_schema / structured output is absent from the forwarded upstream body";

/// Structured output must still be named on the forwarded upstream body.
/// A distinctive schema token in user text is not enough: the wire field
/// itself has to survive (`type: json_schema` or a `json_schema` key),
/// outside conversation content.
pub fn json_schema_forwarded(forwarded_jsonl: &str) -> Verdict {
    match capture_body(forwarded_jsonl) {
        Ok(body) if has_json_schema_wire_field(&body) => Verdict::Conformant,
        Ok(_) => Verdict::Violation(JSON_SCHEMA_ABSENT.into()),
        Err(_) => Verdict::Violation("unparseable capture".into()),
    }
}

/// Parse errors must not pass as this reason.
pub const JSON_SCHEMA_PROPERTY_ABSENT: &str =
    "json_schema.schema is present but a client-supplied property was dropped from it";

/// Invariant (062 / any-llm): a property the client placed in
/// `output_format` / `response_format.json_schema.schema` MUST survive on the
/// wire, not just an empty `{}` schema shell.
pub fn json_schema_property_forwarded(forwarded_jsonl: &str, property: &str) -> Verdict {
    let Ok(body) = capture_body(forwarded_jsonl) else {
        return Verdict::Violation("unparseable capture".into());
    };
    if schema_properties(&body)
        .and_then(|p| p.get(property))
        .is_some()
    {
        return Verdict::Conformant;
    }
    Verdict::Violation(JSON_SCHEMA_PROPERTY_ABSENT.into())
}

fn schema_properties(body: &Value) -> Option<&serde_json::Map<String, Value>> {
    body.get("response_format")
        .and_then(|rf| rf.get("json_schema"))
        .and_then(|js| js.get("schema"))
        .and_then(|s| s.get("properties"))
        .and_then(Value::as_object)
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

/// Invariant (bug 067): every structured refusal string returned upstream MUST
/// remain representable somewhere in the client response. The target dialect
/// may keep a `refusal` field or map the text to an ordinary content block, so
/// the client side is searched by value rather than by one dialect-specific
/// JSON path.
pub fn refusal_text_preserved(upstream_response_json: &str, client_response_json: &str) -> Verdict {
    let Ok(upstream) = serde_json::from_str::<Value>(upstream_response_json) else {
        return Verdict::Violation("upstream response body is not valid JSON".to_string());
    };
    let Ok(client) = serde_json::from_str::<Value>(client_response_json) else {
        return Verdict::Violation("client response body is not valid JSON".to_string());
    };

    let mut refusals = Vec::new();
    collect_keyed_strings(&upstream, "refusal", &mut refusals);
    if refusals.is_empty() {
        return Verdict::Violation("upstream response contains no refusal text".to_string());
    }

    let mut client_strings = Vec::new();
    collect_strings(&client, &mut client_strings);
    for refusal in refusals {
        if !client_strings.iter().any(|value| value == &refusal) {
            return Verdict::Violation(format!(
                "upstream refusal text {refusal:?} is absent from the client response"
            ));
        }
    }
    Verdict::Conformant
}

fn refusal_strings(response: &str) -> Result<Vec<String>, String> {
    if response.lines().any(|line| line.starts_with("data:")) {
        let events = sse_data_json(response);
        if events.is_empty() {
            return Err("upstream response has no parseable SSE data events".to_string());
        }
        let chat_refusal = events
            .iter()
            .flat_map(|event| {
                event
                    .get("choices")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
            })
            .filter_map(|choice| choice.pointer("/delta/refusal").and_then(Value::as_str))
            .collect::<String>();
        if !chat_refusal.is_empty() {
            return Ok(vec![chat_refusal]);
        }
        let response_done_refusals = events
            .iter()
            .filter(|event| {
                event.get("type").and_then(Value::as_str) == Some("response.refusal.done")
            })
            .filter_map(|event| event.get("refusal").and_then(Value::as_str))
            .map(str::to_string)
            .collect::<Vec<_>>();
        if !response_done_refusals.is_empty() {
            return Ok(response_done_refusals);
        }
        let mut refusals = Vec::new();
        for event in &events {
            collect_keyed_strings(event, "refusal", &mut refusals);
        }
        return Ok(refusals);
    }

    let body = serde_json::from_str::<Value>(response)
        .map_err(|_| "upstream response body is not valid JSON".to_string())?;
    let mut refusals = Vec::new();
    collect_keyed_strings(&body, "refusal", &mut refusals);
    Ok(refusals)
}

fn responses_event_key(event: &Value) -> Option<(&str, u64, u64)> {
    Some((
        event.get("item_id")?.as_str()?,
        event.get("output_index")?.as_u64()?,
        event.get("content_index")?.as_u64()?,
    ))
}

fn refusal_part_event_matches(
    event: &Value,
    event_type: &str,
    key: (&str, u64, u64),
    expected_refusal: Option<&str>,
) -> bool {
    if event.get("type").and_then(Value::as_str) != Some(event_type)
        || responses_event_key(event) != Some(key)
        || event.pointer("/part/type").and_then(Value::as_str) != Some("refusal")
    {
        return false;
    }
    expected_refusal.is_none_or(|expected| {
        event.pointer("/part/refusal").and_then(Value::as_str) == Some(expected)
    })
}

/// Invariant (bug 069): a structured refusal returned by an upstream dialect
/// MUST remain machine-identifiable when encoded as an OpenAI Responses result.
/// Buffered Responses use a `refusal` content part. Streams use refusal delta
/// and done events. Merely copying the explanation into `output_text` preserves
/// bytes but destroys the semantic signal used by refusal-aware consumers.
pub fn responses_refusal_semantics_preserved(
    upstream_response: &str,
    client_response: &str,
) -> Verdict {
    let upstream_refusals = match refusal_strings(upstream_response) {
        Ok(refusals) if !refusals.is_empty() => refusals,
        Ok(_) => {
            return Verdict::Violation(
                "upstream response contains no structured refusal signal".to_string(),
            );
        }
        Err(message) => return Verdict::Violation(message),
    };

    if client_response
        .lines()
        .any(|line| line.starts_with("data:"))
    {
        let events = sse_data_json(client_response);
        if events.is_empty() {
            return Verdict::Violation(
                "client response has no parseable Responses SSE data events".to_string(),
            );
        }
        for refusal in upstream_refusals {
            let represented = events
                .iter()
                .filter(|event| {
                    event.get("type").and_then(Value::as_str) == Some("response.refusal.done")
                        && event.get("refusal").and_then(Value::as_str) == Some(refusal.as_str())
                })
                .filter_map(responses_event_key)
                .any(|key| {
                    let delta_text = events
                        .iter()
                        .filter(|event| {
                            event.get("type").and_then(Value::as_str)
                                == Some("response.refusal.delta")
                                && responses_event_key(event) == Some(key)
                        })
                        .filter_map(|event| event.get("delta").and_then(Value::as_str))
                        .collect::<String>();
                    delta_text == refusal
                        && events.iter().any(|event| {
                            refusal_part_event_matches(
                                event,
                                "response.content_part.added",
                                key,
                                None,
                            )
                        })
                        && events.iter().any(|event| {
                            refusal_part_event_matches(
                                event,
                                "response.content_part.done",
                                key,
                                Some(refusal.as_str()),
                            )
                        })
                });
            if !represented {
                return Verdict::Violation(format!(
                    "upstream refusal {refusal:?} is not represented by one correlated Responses refusal content-part lifecycle"
                ));
            }
        }
        return Verdict::Conformant;
    }

    let Ok(client) = serde_json::from_str::<Value>(client_response) else {
        return Verdict::Violation("client response body is not valid JSON".to_string());
    };
    let typed_refusals = client
        .get("output")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|item| item.get("type").and_then(Value::as_str) == Some("message"))
        .filter(|item| item.get("role").and_then(Value::as_str) == Some("assistant"))
        .filter_map(|item| item.get("content").and_then(Value::as_array))
        .flatten()
        .filter(|part| part.get("type").and_then(Value::as_str) == Some("refusal"))
        .filter_map(|part| part.get("refusal").and_then(Value::as_str))
        .collect::<Vec<_>>();
    for refusal in upstream_refusals {
        if !typed_refusals.contains(&refusal.as_str()) {
            return Verdict::Violation(format!(
                "upstream refusal {refusal:?} is not a typed Responses refusal content part"
            ));
        }
    }
    Verdict::Conformant
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

/// Invariant (bug 071): model-info `data[].litellm_params.api_base` must not
/// contain the caller-supplied secret marker. This is a substring check, not
/// a general URL credential detector. Empty or malformed deployment lists fail
/// closed; other response shapes use the whole-body `response_omits_secret` check.
pub fn model_info_omits_api_base_secret(response_json: &str, canary_secret: &str) -> Verdict {
    if canary_secret.is_empty() {
        return Verdict::Violation("secret marker is empty".to_string());
    }
    let Ok(val) = serde_json::from_str::<Value>(response_json) else {
        return Verdict::Violation("response is not valid JSON".to_string());
    };
    let Some(data) = val.get("data").and_then(Value::as_array) else {
        return Verdict::Violation("model/info response is missing data array".to_string());
    };
    if data.is_empty() {
        return Verdict::Violation("model/info response data array is empty".to_string());
    }
    for item in data {
        let Some(params) = item.get("litellm_params").and_then(Value::as_object) else {
            return Verdict::Violation(
                "model/info entry is missing litellm_params object".to_string(),
            );
        };
        if let Some(api_base) = params.get("api_base") {
            let Some(api_base) = api_base.as_str() else {
                return Verdict::Violation("model/info api_base is not a string".to_string());
            };
            if api_base.contains(canary_secret) {
                return Verdict::Violation(format!(
                    "model/info litellm_params.api_base contains secret marker {canary_secret:?}"
                ));
            }
        }
    }
    Verdict::Conformant
}

/// Extract a capture envelope's JSON body after checking its wire identity.
pub fn model_info_envelope_body(envelope_json: &str) -> Result<String, String> {
    let v: Value =
        serde_json::from_str(envelope_json).map_err(|e| format!("unparseable envelope: {e}"))?;
    let body = v
        .get("body")
        .ok_or_else(|| "capture envelope is missing a body field".to_string())?;
    Ok(body.to_string())
}

/// Cross-check a model-info/control envelope against its recorded HTTP/1.1
/// GET exchange: route, 200 status, and JSON body. These pinned-runtime captures
/// use Content-Length, not chunked encoding; unsupported framing fails closed.
pub fn model_info_capture_identity(
    envelope_json: &str,
    raw_http: &str,
    expected_path: &str,
) -> Verdict {
    let v: Value = match serde_json::from_str(envelope_json) {
        Ok(v) => v,
        Err(e) => return Verdict::Violation(format!("capture envelope is not valid JSON: {e}")),
    };
    let path = v.get("request_path").and_then(Value::as_str);
    if path != Some(expected_path) {
        return Verdict::Violation(format!(
            "capture request_path {path:?} does not match the expected route {expected_path:?}"
        ));
    }
    let status = v.get("status").and_then(Value::as_u64);
    if status != Some(200) {
        return Verdict::Violation(format!(
            "capture status {status:?} is not the expected 200 OK"
        ));
    }
    let Some((request, response)) = raw_http.split_once("\r\n\r\n") else {
        return Verdict::Violation("capture is missing CRLF request framing".to_string());
    };
    if request.split("\r\n").next() != Some(format!("GET {expected_path} HTTP/1.1").as_str()) {
        return Verdict::Violation(
            "wire request line does not match the expected route".to_string(),
        );
    }
    let Some((headers, body)) = response.split_once("\r\n\r\n") else {
        return Verdict::Violation("capture is missing CRLF response framing".to_string());
    };
    if headers.split("\r\n").next() != Some("HTTP/1.1 200 OK") {
        return Verdict::Violation("wire status is not 200 OK".to_string());
    }
    let content_length = headers
        .split("\r\n")
        .filter_map(|line| line.split_once(':'))
        .find(|(name, _)| name.eq_ignore_ascii_case("content-length"))
        .and_then(|(_, length)| length.trim().parse::<usize>().ok());
    if content_length != Some(body.len()) {
        return Verdict::Violation("wire Content-Length does not match the body".to_string());
    }
    let Ok(wire_body) = serde_json::from_str::<Value>(body) else {
        return Verdict::Violation("wire response body is not JSON".to_string());
    };
    if v.get("body") != Some(&wire_body) {
        return Verdict::Violation("envelope body differs from the wire response".to_string());
    }
    Verdict::Conformant
}

/// Invariant (bug 083): a Responses continuation by `conversation` must carry
/// the same canonical history as a continuation by `previous_response_id`.
/// The capture records exact Chat requests forwarded after Switchyard's local
/// materialization; each recall request must contain its seed canary.
pub fn response_conversation_preserves_history(
    forwarded_jsonl: &str,
    recall_prefix: &str,
    seed_canary_prefix: &str,
    expected_trials: usize,
) -> Verdict {
    if recall_prefix.is_empty() || seed_canary_prefix.is_empty() || expected_trials == 0 {
        return Verdict::Violation("conversation checker received an empty invariant".into());
    }
    let mut continuations = 0;
    let mut missing = 0;
    for line in forwarded_jsonl
        .lines()
        .filter(|line| !line.trim().is_empty())
    {
        let record: Value = match serde_json::from_str(line) {
            Ok(record) => record,
            Err(error) => {
                return Verdict::Violation(format!(
                    "forwarded capture is not valid JSONL: {error}"
                ));
            }
        };
        let Some(raw) = record.get("body_raw").and_then(Value::as_str) else {
            return Verdict::Violation("forwarded capture is missing body_raw".into());
        };
        let body: Value = match serde_json::from_str(raw) {
            Ok(body) => body,
            Err(error) => {
                return Verdict::Violation(format!(
                    "forwarded body_raw is not valid JSON: {error}"
                ));
            }
        };
        if body.get("messages").and_then(Value::as_array).is_none() {
            return Verdict::Violation("forwarded body is missing messages".into());
        }
        let dump = body_dump(&body);
        if dump.contains(recall_prefix) {
            continuations += 1;
            if !dump.contains(seed_canary_prefix) {
                missing += 1;
            }
        }
    }
    if continuations != expected_trials {
        return Verdict::Violation(format!(
            "expected {expected_trials} conversation continuations, found {continuations}"
        ));
    }
    if missing > 0 {
        return Verdict::Violation(format!(
            "{missing}/{expected_trials} conversation continuations omit their seed history"
        ));
    }
    Verdict::Conformant
}

/// Look for a Gemini `inlineData` part (a base64 image/audio blob) anywhere in
/// a parsed `generateContent` response's first candidate. Returns the MIME
/// type when one is present.
fn gemini_inline_data_mime(upstream: &Value) -> Option<String> {
    let parts = upstream
        .pointer("/candidates/0/content/parts")?
        .as_array()?;
    for part in parts {
        if let Some(data) = part.pointer("/inlineData/data").and_then(Value::as_str) {
            if !data.is_empty() {
                return Some(
                    part.pointer("/inlineData/mimeType")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_string(),
                );
            }
        }
    }
    None
}

/// Does an OpenAI chat-completions message content value represent the media
/// content block shape (`image_url` / `input_audio`), as opposed to plain
/// text or nothing at all?
fn chat_content_has_media_block(content: &Value) -> bool {
    content.as_array().is_some_and(|blocks| {
        blocks.iter().any(|b| {
            matches!(
                b.get("type").and_then(Value::as_str),
                Some("image_url" | "input_audio")
            )
        })
    })
}

/// Does a single element of an `images[]` sibling array look like a real
/// image reference, rather than an empty or unrelated object? This is the
/// shape bifrost's own upstream PR
/// [#4907](https://github.com/maximhq/bifrost/pull/4907) (open, "Closes
/// #2367") adds: `ChatAssistantMessage.Images []ChatAssistantMessageImage`,
/// each element `{"type": "image_url", "image_url": {"url": "data:..."}}`.
/// Accept that exact shape plus a few reasonably-shaped variants (a direct
/// `url`/`data`/`b64_json`/mime-type-ish key), but never an empty object or
/// one that carries none of these keys.
fn looks_like_image_ref(entry: &Value) -> bool {
    let Some(obj) = entry.as_object() else {
        return false;
    };
    // #4907 shape: {"type": "image_url", "image_url": {"url": "data:..."}}
    if obj
        .get("image_url")
        .and_then(|v| v.pointer("/url"))
        .and_then(Value::as_str)
        .is_some_and(|u| !u.is_empty())
    {
        return true;
    }
    for key in ["url", "data", "b64_json", "mime_type", "mimeType"] {
        if obj
            .get(key)
            .and_then(Value::as_str)
            .is_some_and(|s| !s.is_empty())
        {
            return true;
        }
    }
    false
}

/// Does a message or delta object carry a non-empty `images[]` sibling array
/// (bifrost's own upstream PR #4907 shape) with at least one real image
/// reference in it? An empty array, or one containing only unrelated
/// objects, does not count.
fn message_has_images_sibling(message_or_delta: &Value) -> bool {
    message_or_delta
        .get("images")
        .and_then(Value::as_array)
        .is_some_and(|images| !images.is_empty() && images.iter().any(looks_like_image_ref))
}

/// Is media (an image or audio blob) preserved somewhere client-visible in a
/// chat-completions `message` or streamed `delta` object? Two shapes count:
/// an OpenAI-style content block (`image_url`/`input_audio` inside
/// `content[]`), or a non-empty `images[]` sibling array on the message
/// itself, the shape bifrost's own upstream PR #4907 is adding for the
/// OpenAI/OpenRouter provider. Only one hardcoded shape would make this
/// invariant blind to the real fix landing upstream in that shape.
fn media_preserved_in_message(message_or_delta: &Value) -> bool {
    let content_ok = message_or_delta
        .get("content")
        .is_some_and(chat_content_has_media_block);
    content_ok || message_has_images_sibling(message_or_delta)
}

/// Invariant (bug 075): when Gemini's raw `generateContent` response carries an
/// `inlineData` part (a base64 image or audio blob, as image-generation models
/// such as `gemini-2.5-flash-image` return), Bifrost's OpenAI-shaped chat
/// completions conversion MUST surface that media somewhere client-visible in
/// the response: either an OpenAI-style content block, or an `images[]`
/// sibling array as bifrost's own upstream PR #4907 is adding. Silently
/// finishing the turn with text-only (or empty) content and no `images[]` is a
/// violation: the caller gets HTTP 200 and billed image tokens but no image.
///
/// `upstream_response_json` is Gemini's raw response (as embedded by Bifrost's
/// `send_back_raw_request`/`send_back_raw_response` provider options, or
/// captured directly). `client_response_json` is Bifrost's chat.completions
/// JSON body returned to the caller.
pub fn gemini_inline_media_preserved_in_chat_response(
    upstream_response_json: &str,
    client_response_json: &str,
) -> Verdict {
    let Ok(upstream) = serde_json::from_str::<Value>(upstream_response_json) else {
        return Verdict::Violation("upstream response body is not valid JSON".to_string());
    };
    let Some(mime) = gemini_inline_data_mime(&upstream) else {
        return Verdict::Conformant; // nothing to preserve
    };
    let Ok(client) = serde_json::from_str::<Value>(client_response_json) else {
        return Verdict::Violation("client response body is not valid JSON".to_string());
    };
    let Some(message) = client.pointer("/choices/0/message") else {
        return Verdict::Violation(format!(
            "upstream returned an inlineData part ({mime}) but the client response has no message field at all"
        ));
    };
    if media_preserved_in_message(message) {
        return Verdict::Conformant;
    }
    Verdict::Violation(format!(
        "upstream returned an inlineData part ({mime}) but chat completions message has no \
         image_url/input_audio content block and no non-empty images[] sibling array \
         (message: {message})"
    ))
}

/// Streaming counterpart of [`gemini_inline_media_preserved_in_chat_response`].
/// Walks every `chat.completion.chunk` in the SSE body, grouped by each
/// chunk's `id` (the correlation field for one exchange). For every group
/// where at least one chunk embeds a Gemini raw response
/// (`extra_fields.raw_response`) carrying an `inlineData` part, that same
/// exchange's chunks MUST reflect the media somewhere: it does not have to be
/// the same chunk that carried the raw upstream payload, since a correct fix
/// (including one that follows bifrost's own upstream PR #4907 `images[]`
/// shape) is free to flush image data in a different chunk than the
/// accompanying text.
pub fn gemini_inline_media_preserved_in_chat_stream(sse: &str) -> Verdict {
    let chunks = sse_data_json(sse);
    let mut order: Vec<String> = Vec::new();
    let mut upstream_media_by_id: std::collections::HashMap<String, bool> =
        std::collections::HashMap::new();
    let mut delta_media_by_id: std::collections::HashMap<String, bool> =
        std::collections::HashMap::new();
    for chunk in &chunks {
        let id = chunk
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if !upstream_media_by_id.contains_key(&id) {
            order.push(id.clone());
            upstream_media_by_id.insert(id.clone(), false);
            delta_media_by_id.insert(id.clone(), false);
        }

        let raw_response = chunk.pointer("/extra_fields/raw_response");
        let upstream: Option<Value> = match raw_response {
            Some(Value::String(s)) => serde_json::from_str(s).ok(),
            Some(v @ Value::Object(_)) => Some(v.clone()),
            _ => None,
        };
        if let Some(upstream) = upstream {
            if gemini_inline_data_mime(&upstream).is_some() {
                upstream_media_by_id.insert(id.clone(), true);
            }
        }
        if let Some(delta) = chunk.pointer("/choices/0/delta") {
            if media_preserved_in_message(delta) {
                delta_media_by_id.insert(id.clone(), true);
            }
        }
    }

    let mut saw_any_upstream_media = false;
    for id in &order {
        if !upstream_media_by_id.get(id).copied().unwrap_or(false) {
            continue;
        }
        saw_any_upstream_media = true;
        if !delta_media_by_id.get(id).copied().unwrap_or(false) {
            return Verdict::Violation(format!(
                "a chunk in stream exchange {id:?} carried an upstream inlineData part but no \
                 chunk in that same exchange carried a matching image_url/input_audio content \
                 block or non-empty images[] sibling array in delta"
            ));
        }
    }
    if !saw_any_upstream_media {
        return Verdict::Conformant; // nothing to preserve
    }
    Verdict::Conformant
}

/// Invariant (bug 080): two media URLs that differ only in letter case are
/// distinct resources (RFC 3986 paths and queries are case-sensitive). A
/// cache key that lowercases the whole URL makes the second request silently
/// receive the first resource. For every loader session that requested a
/// case-colliding pair, the origin must receive one hit per requested URL and
/// each decoded image must match the resource the origin serves for that URL.
///
/// Runs against the capture JSONL written by `transcripts/080/repro_case_collision.py`.
/// Each record: `requested` (URL paths), `origin_hits` (paths the origin saw),
/// `decoded` (pixel rows returned per request), `origin_colors` (path -> color).
pub fn image_url_cache_key_case_sensitive(jsonl: &str) -> Verdict {
    let records = match capture_records(jsonl) {
        Ok(r) => r,
        Err(e) => return Verdict::Violation(format!("unparseable capture: {e}")),
    };
    for (idx, (_, record)) in records.iter().enumerate() {
        let requested: Vec<String> =
            match record.get("requested").and_then(Value::as_array).map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            }) {
                Some(r) => r,
                None => {
                    return Verdict::Violation(format!(
                        "record {idx}: missing requested list: {record}"
                    ))
                }
            };
        let has_case_collision = requested.iter().enumerate().any(|(i, u)| {
            requested[i + 1..]
                .iter()
                .any(|v| u != v && u.to_lowercase() == v.to_lowercase())
        });
        if !has_case_collision {
            continue; // nothing this record can say about the invariant
        }
        let hits: Vec<String> = match record
            .get("origin_hits")
            .and_then(Value::as_array)
            .map(|a| {
                a.iter()
                    .filter_map(Value::as_str)
                    .map(str::to_owned)
                    .collect()
            }) {
            Some(h) => h,
            None => {
                return Verdict::Violation(format!(
                    "record {idx}: missing origin_hits list: {record}"
                ))
            }
        };
        let decoded: Vec<Vec<i64>> =
            match record.get("decoded").and_then(Value::as_array).map(|a| {
                a.iter()
                    .filter_map(|p| {
                        p.as_array()
                            .map(|rgb| rgb.iter().filter_map(Value::as_i64).collect::<Vec<i64>>())
                    })
                    .collect()
            }) {
                Some(d) => d,
                None => {
                    return Verdict::Violation(format!(
                        "record {idx}: missing decoded list: {record}"
                    ))
                }
            };
        let Some(Value::Object(colors)) = record.get("origin_colors") else {
            return Verdict::Violation(format!(
                "record {idx}: missing origin_colors map: {record}"
            ));
        };
        for (i, url) in requested.iter().enumerate() {
            if !hits.iter().any(|h| h == url) {
                return Verdict::Violation(format!(
                    "record {idx}: requested '{url}' never reached the origin \
                     (hits: {hits:?}); a cache key conflating case-differing URLs \
                     served another resource"
                ));
            }
            let expected = colors
                .get(url)
                .and_then(Value::as_array)
                .map(|rgb| rgb.iter().filter_map(Value::as_i64).collect::<Vec<i64>>());
            match (decoded.get(i), expected) {
                (Some(got), Some(want)) if got != &want => {
                    return Verdict::Violation(format!(
                        "record {idx}: '{url}' decoded to {got:?}, origin serves {want:?}; \
                         the case-differing URL pair collided on one cache key"
                    ));
                }
                _ => {}
            }
        }
    }
    Verdict::Conformant
}

/// Invariant (bug 082): every distinct auto-executed tool call must retain a
/// separately client-visible result. `executions_json` is an execution index
/// with `executions[].tool_call_id` and `executions[].effect_marker`; the
/// markers must come from the recorded tool outputs, not from an expected
/// response fixture. `client_exchange` is the complete HTTP response.
///
/// A tool may legally be called more than once in one turn. Tool names are not
/// identities, so a summary keyed only by tool name must not collapse results
/// from calls carrying distinct tool-call IDs.
pub fn executed_tool_results_preserved(executions_json: &str, client_exchange: &str) -> Verdict {
    let Ok(execution_index) = serde_json::from_str::<Value>(executions_json) else {
        return Verdict::Violation("execution index is not valid JSON".to_owned());
    };
    let Some(executions) = execution_index.get("executions").and_then(Value::as_array) else {
        return Verdict::Violation("execution index has no executions array".to_owned());
    };
    if executions.is_empty() {
        return Verdict::Violation("execution index is empty".to_owned());
    }

    let Some((head, body)) = client_exchange.split_once("\r\n\r\n") else {
        return Verdict::Violation("client capture is not a complete HTTP response".to_owned());
    };
    let status = head
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1));
    if status != Some("200") {
        return Verdict::Violation(format!("client capture status is {status:?}, expected 200"));
    }
    if serde_json::from_str::<Value>(body).is_err() {
        return Verdict::Violation("client response body is not valid JSON".to_owned());
    }

    let mut ids = std::collections::HashSet::new();
    let mut markers = std::collections::HashSet::new();
    for (index, execution) in executions.iter().enumerate() {
        let Some(tool_call_id) = execution.get("tool_call_id").and_then(Value::as_str) else {
            return Verdict::Violation(format!("execution {index} has no non-string tool_call_id"));
        };
        let Some(effect_marker) = execution.get("effect_marker").and_then(Value::as_str) else {
            return Verdict::Violation(format!(
                "execution {index} has no non-string effect_marker"
            ));
        };
        if tool_call_id.is_empty() || effect_marker.is_empty() {
            return Verdict::Violation(format!(
                "execution {index} has an empty tool-call ID or result marker"
            ));
        }
        if !ids.insert(tool_call_id) {
            return Verdict::Violation(format!(
                "execution index repeats tool-call ID {tool_call_id:?}"
            ));
        }
        if !markers.insert(effect_marker) {
            return Verdict::Violation(format!(
                "execution index repeats result marker {effect_marker:?}"
            ));
        }
        if !body.contains(effect_marker) {
            return Verdict::Violation(format!(
                "executed call {tool_call_id:?} result marker {effect_marker:?} is absent from the client response"
            ));
        }
    }
    Verdict::Conformant
}

/// Invariant (bug 086): a successful response from an OpenAI-compatible
/// endpoint carries that endpoint's own response family, whichever layer
/// produced it (upstream, cache, or plugin). `/v1/chat/completions` returns a
/// `chat.completion` object or a stream of `chat.completion.chunk` objects
/// with a finish reason; `/v1/responses` returns a `response` object or a
/// stream of `response.*` events that starts with `response.created` and
/// ends in a terminal event. `client_exchange` is the complete raw HTTP
/// response the client received.
///
/// The check reads only the public OpenAI discriminators (`object`, `type`),
/// so it does not depend on how a gateway stores or replays entries. A
/// non-2xx or unparseable capture is a violation, not a vacuous pass.
pub fn endpoint_response_family_preserved(endpoint: &str, client_exchange: &str) -> Verdict {
    let Some((head, body)) = client_exchange.split_once("\r\n\r\n") else {
        return Verdict::Violation("capture has no HTTP header terminator".to_owned());
    };
    let status = head
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok());
    match status {
        Some(code) if (200..300).contains(&code) => {}
        other => {
            return Verdict::Violation(format!(
                "not a successful response (status {other:?}); the family invariant needs a 2xx"
            ))
        }
    }
    let streamed = head.lines().any(|line| {
        let lower = line.to_ascii_lowercase();
        lower.starts_with("content-type:") && lower.contains("text/event-stream")
    });
    match (endpoint, streamed) {
        ("/v1/chat/completions", false) => json_body_family(body, "chat.completion", "choices"),
        ("/v1/responses", false) => json_body_family(body, "response", "output"),
        ("/v1/chat/completions", true) => chat_stream_family(body),
        ("/v1/responses", true) => responses_stream_family(body),
        _ => Verdict::Violation(format!("no family rule for endpoint {endpoint:?}")),
    }
}

/// A non-streamed body must be a JSON object whose `object` names the
/// endpoint's family and that carries the family's result array.
fn json_body_family(body: &str, object: &str, array_field: &str) -> Verdict {
    match serde_json::from_str::<Value>(body.trim()) {
        Ok(value)
            if value.get("object").and_then(Value::as_str) == Some(object)
                && value.get(array_field).is_some_and(Value::is_array) =>
        {
            Verdict::Conformant
        }
        Ok(value) => Verdict::Violation(format!(
            "expected a {object} body, got: {}",
            truncate_for_reason(&value.to_string())
        )),
        Err(error) => Verdict::Violation(format!("{object} body is not JSON: {error}")),
    }
}

fn chat_stream_family(body: &str) -> Verdict {
    let payloads = sse_data_json(body);
    if payloads.is_empty() {
        return Verdict::Violation("chat completions stream carried no JSON data".to_owned());
    }
    if let Some(foreign) = payloads.iter().find(|payload| {
        payload.get("object").and_then(Value::as_str) != Some("chat.completion.chunk")
    }) {
        return Verdict::Violation(format!(
            "chat completions stream carried a non chat.completion.chunk payload: {}",
            truncate_for_reason(&foreign.to_string())
        ));
    }
    let finished = payloads.iter().any(|payload| {
        payload
            .get("choices")
            .and_then(Value::as_array)
            .is_some_and(|choices| {
                choices.iter().any(|choice| {
                    choice
                        .get("finish_reason")
                        .and_then(Value::as_str)
                        .is_some()
                })
            })
    });
    if finished {
        Verdict::Conformant
    } else {
        Verdict::Violation("chat completions stream never reported a finish_reason".to_owned())
    }
}

fn responses_stream_family(body: &str) -> Verdict {
    let payloads = sse_data_json(body);
    let types: Vec<&str> = payloads
        .iter()
        .map(|payload| payload.get("type").and_then(Value::as_str).unwrap_or(""))
        .collect();
    if types.is_empty() {
        return Verdict::Violation("responses stream carried no JSON data".to_owned());
    }
    if let Some(foreign) = types.iter().find(|kind| !kind.starts_with("response.")) {
        return Verdict::Violation(format!(
            "responses stream carried a non response.* payload (type {foreign:?})"
        ));
    }
    if types.first() != Some(&"response.created") {
        return Verdict::Violation(format!(
            "responses stream did not start with response.created (first {:?})",
            types.first()
        ));
    }
    let terminal = [
        "response.completed",
        "response.failed",
        "response.incomplete",
    ];
    if types.last().is_some_and(|kind| terminal.contains(kind)) {
        Verdict::Conformant
    } else {
        Verdict::Violation(format!(
            "responses stream ended without a terminal event (last {:?})",
            types.last()
        ))
    }
}

fn truncate_for_reason(text: &str) -> String {
    text.chars().take(160).collect()
}

/// Invariant (bug 087): one client turn may execute a side-effecting MCP tool
/// at most once. The ledger is JSON Lines written at the consumer boundary,
/// with a positive, consecutive `seq` and a non-empty `entry` per execution.
/// Empty or malformed evidence fails closed instead of passing vacuously.
pub fn mcp_tool_executes_once(ledger_jsonl: &str) -> Verdict {
    let mut count = 0_u64;
    let mut previous = None;
    for (index, line) in ledger_jsonl.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let Ok(record) = serde_json::from_str::<Value>(line) else {
            return Verdict::Violation(format!("ledger line {} is not valid JSON", index + 1));
        };
        let Some(sequence) = record.get("seq").and_then(Value::as_u64) else {
            return Verdict::Violation(format!(
                "ledger line {} has no positive integer seq",
                index + 1
            ));
        };
        if sequence == 0 || previous.is_some_and(|value| sequence != value + 1) {
            return Verdict::Violation(format!(
                "ledger line {} has non-consecutive seq {sequence}",
                index + 1
            ));
        }
        if record
            .get("entry")
            .and_then(Value::as_str)
            .is_none_or(str::is_empty)
        {
            return Verdict::Violation(format!("ledger line {} has no non-empty entry", index + 1));
        }
        previous = Some(sequence);
        count += 1;
    }
    match count {
        1 => Verdict::Conformant,
        0 => Verdict::Violation("tool ledger is empty".to_owned()),
        other => Verdict::Violation(format!(
            "one client turn executed the MCP tool {other} times, expected once"
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ogx_adaptive_checker_is_non_vacuous_and_checks_trial_count() {
        let violating = r#"[
          {"client_status":200,"forwarded":{"messages":[{"role":"user","content":"synthetic"}],"model":"m"}},
          {"client_status":200,"forwarded":{"messages":[{"role":"user","content":"synthetic"}],"model":"m"}}
        ]"#;
        assert!(matches!(
            ogx_adaptive_thinking_loss(violating, 2),
            Verdict::Violation(_)
        ));
        assert!(matches!(
            ogx_adaptive_thinking_loss(violating, 1),
            Verdict::Violation(_)
        ));
        assert!(matches!(
            ogx_adaptive_thinking_loss("[]", 1),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn ogx_adaptive_checker_accepts_forwarded_or_rejected_controls() {
        let forwarded = r#"[
          {"client_status":200,"forwarded":{"messages":[{"role":"user","content":"synthetic"}],"reasoning_effort":"medium"}}
        ]"#;
        assert_eq!(
            ogx_adaptive_thinking_loss(forwarded, 1),
            Verdict::Conformant
        );
        let rejected = r#"[
          {"client_status":400,"forwarded":null}
        ]"#;
        assert_eq!(ogx_adaptive_thinking_loss(rejected, 1), Verdict::Conformant);
    }

    #[test]
    fn ogx_adaptive_checker_rejects_mixed_trials() {
        let mixed = r#"[
          {"client_status":200,"forwarded":{"messages":[{"role":"user","content":"synthetic"}],"reasoning_effort":"medium"}},
          {"client_status":200,"forwarded":{"messages":[{"role":"user","content":"synthetic"}]}}
        ]"#;
        assert!(matches!(
            ogx_adaptive_thinking_loss(mixed, 2),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn ogx_adaptive_checker_parses_jsonl_and_rejects_forwarded_config_loss() {
        let jsonl = "{\"body\":{\"messages\":[{\"role\":\"user\",\"content\":\"synthetic\"}],\"model\":\"m\"}}\n{\"body\":{\"messages\":[{\"role\":\"user\",\"content\":\"synthetic\"}],\"model\":\"m\"}}\n";
        assert!(matches!(
            ogx_adaptive_thinking_loss(jsonl, 2),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn id_contract_basics() {
        assert!(id_conforms("call_abc123"));
        assert!(id_conforms("functions_list_skills_0"));
        assert!(!id_conforms("functions.list_skills:0")); // dot and colon
        assert!(!id_conforms(&"x".repeat(65))); // too long
        assert!(!id_conforms("")); // empty
    }

    #[test]
    fn image_cache_case_collision_is_caught() {
        let bug = r#"{"body":{"scenario":"bug","cache_size":8,"requested":["/Cat.png","/cat.png"],"origin_hits":["/Cat.png"],"decoded":[[255,0,0],[255,0,0]],"origin_colors":{"/Cat.png":[255,0,0],"/cat.png":[0,0,255]}}}"#;
        let v = image_url_cache_key_case_sensitive(bug);
        assert!(matches!(v, Verdict::Violation(_)), "must catch: {v:?}");
    }

    #[test]
    fn image_cache_case_distinct_urls_is_conformant() {
        let control = r#"{"body":{"scenario":"cache-off","cache_size":0,"requested":["/Cat.png","/cat.png"],"origin_hits":["/Cat.png","/cat.png"],"decoded":[[255,0,0],[0,0,255]],"origin_colors":{"/Cat.png":[255,0,0],"/cat.png":[0,0,255]}}}"#;
        assert_eq!(
            image_url_cache_key_case_sensitive(control),
            Verdict::Conformant
        );
    }

    #[test]
    fn image_cache_no_case_pair_skips() {
        let no_pair = r#"{"body":{"scenario":"distinct","cache_size":8,"requested":["/Cat.png","/dog.png"],"origin_hits":["/Cat.png","/dog.png"],"decoded":[[255,0,0],[0,255,0]],"origin_colors":{"/Cat.png":[255,0,0],"/cat.png":[0,0,255],"/dog.png":[0,255,0]}}}"#;
        assert_eq!(
            image_url_cache_key_case_sensitive(no_pair),
            Verdict::Conformant
        );
    }

    #[test]
    fn executed_tool_result_checker_is_nonvacuous() {
        let executions = r#"{"executions":[
            {"tool_call_id":"call_alpha","effect_marker":"RESULT_ALPHA"},
            {"tool_call_id":"call_beta","effect_marker":"RESULT_BETA"}
        ]}"#;
        let complete = "HTTP/1.1 200 OK\r\n\r\n{\"content\":\"RESULT_ALPHA RESULT_BETA\"}";
        assert_eq!(
            executed_tool_results_preserved(executions, complete),
            Verdict::Conformant
        );
        let repeated =
            "HTTP/1.1 200 OK\r\n\r\n{\"content\":\"RESULT_ALPHA RESULT_ALPHA RESULT_BETA\"}";
        assert_eq!(
            executed_tool_results_preserved(executions, repeated),
            Verdict::Conformant,
            "an extra client-visible copy does not erase either executed result"
        );

        let missing = "HTTP/1.1 200 OK\r\n\r\n{\"content\":\"RESULT_ALPHA\"}";
        assert!(matches!(
            executed_tool_results_preserved(executions, missing),
            Verdict::Violation(_)
        ));
        assert!(matches!(
            executed_tool_results_preserved(r#"{"executions":[]}"#, complete),
            Verdict::Violation(_)
        ));
        let duplicate_id = executions.replace("call_beta", "call_alpha");
        assert!(matches!(
            executed_tool_results_preserved(&duplicate_id, complete),
            Verdict::Violation(_)
        ));
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
    fn refusal_text_checker_accepts_cross_format_representations() {
        let upstream = r#"{"output":[{"content":[{"type":"refusal","refusal":"cannot help"}]}]}"#;
        assert!(matches!(
            refusal_text_preserved(upstream, r#"{"content":[]}"#),
            Verdict::Violation(_)
        ));
        assert_eq!(
            refusal_text_preserved(
                upstream,
                r#"{"choices":[{"message":{"provider_specific_fields":{"refusal":"cannot help"}}}]}"#,
            ),
            Verdict::Conformant
        );
        assert_eq!(
            refusal_text_preserved(
                upstream,
                r#"{"content":[{"type":"text","text":"cannot help"}]}"#,
            ),
            Verdict::Conformant
        );
    }

    #[test]
    fn responses_refusal_checker_requires_typed_buffered_content() {
        let upstream = r#"{"choices":[{"message":{"content":null,"refusal":"cannot help"}}]}"#;
        let conformant = r#"{"output":[{"type":"message","role":"assistant","content":[{"type":"refusal","refusal":"cannot help"}]}]}"#;
        assert_eq!(
            responses_refusal_semantics_preserved(upstream, conformant),
            Verdict::Conformant
        );

        let flattened = r#"{"output":[{"type":"message","content":[{"type":"output_text","text":"cannot help"}]}]}"#;
        assert!(matches!(
            responses_refusal_semantics_preserved(upstream, flattened),
            Verdict::Violation(_)
        ));
        assert!(matches!(
            responses_refusal_semantics_preserved(r#"{"choices":[]}"#, conformant),
            Verdict::Violation(_)
        ));
        assert!(matches!(
            responses_refusal_semantics_preserved(upstream, "not-json"),
            Verdict::Violation(_)
        ));
        let wrong_output_item = r#"{"output":[{"type":"function_call","role":"assistant","content":[{"type":"refusal","refusal":"cannot help"}]}]}"#;
        assert!(matches!(
            responses_refusal_semantics_preserved(upstream, wrong_output_item),
            Verdict::Violation(_)
        ));
        assert!(matches!(
            responses_refusal_semantics_preserved("not-json", conformant),
            Verdict::Violation(_)
        ));
        let two_upstream =
            r#"{"choices":[{"message":{"refusal":"first"}},{"message":{"refusal":"second"}}]}"#;
        let two_client = r#"{"output":[{"type":"message","role":"assistant","content":[{"type":"refusal","refusal":"first"},{"type":"refusal","refusal":"second"}]}]}"#;
        assert_eq!(
            responses_refusal_semantics_preserved(two_upstream, two_client),
            Verdict::Conformant
        );
        assert!(matches!(
            responses_refusal_semantics_preserved(two_upstream, conformant),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn responses_refusal_checker_requires_stream_delta_and_done() {
        let upstream = "data: {\"choices\":[{\"delta\":{\"refusal\":\"cannot \"}}]}\n\n\
            data: {\"choices\":[{\"delta\":{\"refusal\":\"help\"}}]}\n\n";
        let conformant = "event: response.content_part.added\n\
            data: {\"type\":\"response.content_part.added\",\"item_id\":\"msg_1\",\"output_index\":0,\"content_index\":0,\"part\":{\"type\":\"refusal\",\"refusal\":\"\"}}\n\n\
            event: response.refusal.delta\n\
            data: {\"type\":\"response.refusal.delta\",\"item_id\":\"msg_1\",\"output_index\":0,\"content_index\":0,\"delta\":\"cannot help\"}\n\n\
            event: response.refusal.done\n\
            data: {\"type\":\"response.refusal.done\",\"item_id\":\"msg_1\",\"output_index\":0,\"content_index\":0,\"refusal\":\"cannot help\"}\n\n\
            event: response.content_part.done\n\
            data: {\"type\":\"response.content_part.done\",\"item_id\":\"msg_1\",\"output_index\":0,\"content_index\":0,\"part\":{\"type\":\"refusal\",\"refusal\":\"cannot help\"}}\n\n";
        assert_eq!(
            responses_refusal_semantics_preserved(upstream, conformant),
            Verdict::Conformant
        );

        let flattened = "event: response.output_text.delta\n\
            data: {\"type\":\"response.output_text.delta\",\"delta\":\"cannot help\"}\n\n";
        assert!(matches!(
            responses_refusal_semantics_preserved(upstream, flattened),
            Verdict::Violation(_)
        ));
        let missing_done = "event: response.refusal.delta\n\
            data: {\"type\":\"response.refusal.delta\",\"delta\":\"cannot help\"}\n\n";
        assert!(matches!(
            responses_refusal_semantics_preserved(upstream, missing_done),
            Verdict::Violation(_)
        ));
        let crossed_items = conformant.replace(
            "\"type\":\"response.refusal.delta\",\"item_id\":\"msg_1\"",
            "\"type\":\"response.refusal.delta\",\"item_id\":\"msg_2\"",
        );
        assert!(matches!(
            responses_refusal_semantics_preserved(upstream, &crossed_items),
            Verdict::Violation(_)
        ));
        assert!(matches!(
            responses_refusal_semantics_preserved("data: not-json\n\n", conformant),
            Verdict::Violation(_)
        ));
        assert!(matches!(
            responses_refusal_semantics_preserved(upstream, "data: not-json\n\n"),
            Verdict::Violation(_)
        ));
        let responses_upstream =
            "data: {\"type\":\"response.refusal.delta\",\"delta\":\"cannot help\"}\n\n\
            data: {\"type\":\"response.refusal.done\",\"refusal\":\"cannot help\"}\n\n";
        assert_eq!(
            responses_refusal_semantics_preserved(responses_upstream, conformant),
            Verdict::Conformant
        );
        let two_upstream =
            "data: {\"type\":\"response.refusal.done\",\"refusal\":\"cannot help\"}\n\n\
            data: {\"type\":\"response.refusal.done\",\"refusal\":\"second refusal\"}\n\n";
        let second_lifecycle = conformant
            .replace("msg_1", "msg_2")
            .replace("cannot help", "second refusal")
            .replace("\"output_index\":0", "\"output_index\":1");
        let two_client = format!("{conformant}{second_lifecycle}");
        assert_eq!(
            responses_refusal_semantics_preserved(two_upstream, &two_client),
            Verdict::Conformant
        );
        assert!(matches!(
            responses_refusal_semantics_preserved(two_upstream, conformant),
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
        assert_eq!(
            json_schema_forwarded(dropped),
            Verdict::Violation(JSON_SCHEMA_ABSENT.into())
        );
        // Prompt text naming the token is not a surviving wire field.
        let in_user_text =
            r#"{"body":{"model":"x","messages":[{"content":"please use json_schema"}]}}"#;
        assert_eq!(
            json_schema_forwarded(in_user_text),
            Verdict::Violation(JSON_SCHEMA_ABSENT.into())
        );
        let two = capture_records("{\"body\":{\"a\":1}}\n{\"body\":{\"b\":2}}\n").unwrap();
        assert_eq!(two.len(), 2);
        assert_eq!(two[0].1.get("a").and_then(Value::as_i64), Some(1));
        assert_eq!(two[1].1.get("b").and_then(Value::as_i64), Some(2));
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

    #[test]
    fn tool_strict_checker_requires_the_function_tool_field() {
        let missing =
            r#"{"body":{"tools":[{"type":"function","name":"x","parameters":{"strict":true}}]}}"#;
        assert!(matches!(
            tool_strict_forwarded(missing, "x", FunctionToolFormat::OpenAiResponses),
            Verdict::Violation(_)
        ));
        let responses = r#"{"body":{"tools":[{"type":"function","name":"x","strict":true}]}}"#;
        assert_eq!(
            tool_strict_forwarded(responses, "x", FunctionToolFormat::OpenAiResponses),
            Verdict::Conformant
        );
        let chat =
            r#"{"body":{"tools":[{"type":"function","function":{"name":"x","strict":true}}]}}"#;
        assert_eq!(
            tool_strict_forwarded(chat, "x", FunctionToolFormat::OpenAiChat),
            Verdict::Conformant
        );
        let unrelated = r#"{"body":{"tools":[{"type":"function","name":"other","strict":true},{"type":"function","name":"x"}]}}"#;
        assert!(matches!(
            tool_strict_forwarded(unrelated, "x", FunctionToolFormat::OpenAiResponses),
            Verdict::Violation(_)
        ));
        let non_function = r#"{"body":{"tools":[{"type":"custom","name":"x","strict":true}]}}"#;
        assert!(matches!(
            tool_strict_forwarded(non_function, "x", FunctionToolFormat::OpenAiResponses),
            Verdict::Violation(_)
        ));
        let responses_wrong_place =
            r#"{"body":{"tools":[{"type":"function","name":"x","function":{"strict":true}}]}}"#;
        assert!(matches!(
            tool_strict_forwarded(
                responses_wrong_place,
                "x",
                FunctionToolFormat::OpenAiResponses
            ),
            Verdict::Violation(_)
        ));
        let chat_wrong_place =
            r#"{"body":{"tools":[{"type":"function","strict":true,"function":{"name":"x"}}]}}"#;
        assert!(matches!(
            tool_strict_forwarded(chat_wrong_place, "x", FunctionToolFormat::OpenAiChat),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn model_info_omits_api_base_secret_flags_leaked_key() {
        let bad = r#"{"data":[{"model_name":"x","litellm_params":{"api_base":"http://127.0.0.1:9996/v1?key=CANARY_KEY"}}]}"#;
        assert!(matches!(
            model_info_omits_api_base_secret(bad, "CANARY_KEY"),
            Verdict::Violation(_)
        ));
        let ok = r#"{"data":[{"model_name":"x","litellm_params":{"model":"openai/x"}}]}"#;
        assert_eq!(
            model_info_omits_api_base_secret(ok, "CANARY_KEY"),
            Verdict::Conformant
        );
        assert!(matches!(
            model_info_omits_api_base_secret(r"{}", "CANARY_KEY"),
            Verdict::Violation(_)
        ));
        // An empty data array has nothing to check: fail closed, do not pass vacuously.
        assert!(matches!(
            model_info_omits_api_base_secret(r#"{"data":[]}"#, "CANARY_KEY"),
            Verdict::Violation(_)
        ));
        for malformed in [
            "not-json",
            r#"{"data":[null]}"#,
            r#"{"data":[{}]}"#,
            r#"{"data":[{"litellm_params":null}]}"#,
            r#"{"data":[{"litellm_params":{"api_base":42}}]}"#,
        ] {
            assert!(matches!(
                model_info_omits_api_base_secret(malformed, "CANARY_KEY"),
                Verdict::Violation(_)
            ));
        }
        assert!(matches!(
            model_info_omits_api_base_secret(ok, ""),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn model_info_capture_identity_flags_swapped_route_and_bad_status() {
        let good = r#"{"request_path":"/model/info","status":200,"body":{"data":[]}}"#;
        let wire = "GET /model/info HTTP/1.1\r\nHost: localhost\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 11\r\n\r\n{\"data\":[]}";
        assert_eq!(
            model_info_capture_identity(good, wire, "/model/info"),
            Verdict::Conformant
        );
        // Swapped route: body captured from a different endpoint than claimed.
        let swapped = r#"{"request_path":"/v1/model/info","status":200,"body":{"data":[]}}"#;
        assert!(matches!(
            model_info_capture_identity(swapped, wire, "/model/info"),
            Verdict::Violation(_)
        ));
        // A 404 (or any non-200) must not pass as a genuine model-info capture.
        let not_found =
            r#"{"request_path":"/model/info","status":404,"body":{"detail":"not found"}}"#;
        assert!(matches!(
            model_info_capture_identity(not_found, wire, "/model/info"),
            Verdict::Violation(_)
        ));
        assert!(matches!(
            model_info_capture_identity("not-json", wire, "/model/info"),
            Verdict::Violation(_)
        ));
        assert!(matches!(
            model_info_capture_identity(r#"{"status":200,"body":{}}"#, wire, "/model/info"),
            Verdict::Violation(_)
        ));
        for bad_wire in [
            wire.replace("GET /model/info", "GET /v1/model/info"),
            wire.replace("200 OK", "404 Not Found"),
            wire.replace("{\"data\":[]}", "{\"data\":{}}"),
            wire.replace("Content-Length: 11", "Content-Length: 12"),
            wire.replace("\r\n", "\n"),
        ] {
            assert!(matches!(
                model_info_capture_identity(good, &bad_wire, "/model/info"),
                Verdict::Violation(_)
            ));
        }
    }

    #[test]
    fn model_info_envelope_body_extracts_body() {
        let envelope =
            r#"{"request_path":"/model/info","status":200,"body":{"data":[{"model_name":"x"}]}}"#;
        let body = model_info_envelope_body(envelope).expect("envelope has a body");
        let parsed: Value = serde_json::from_str(&body).expect("body is valid JSON");
        assert_eq!(
            parsed.pointer("/data/0/model_name").and_then(Value::as_str),
            Some("x")
        );
        assert!(model_info_envelope_body("not-json").is_err());
        assert!(model_info_envelope_body(r#"{"status":200}"#).is_err());
    }

    #[test]
    fn response_omits_secret_catches_canary_outside_litellm_params() {
        for leaked_elsewhere in [
            r#"{"object":"list","data":[{"id":"CANARY_QUERY_KEY_IN_API_BASE-leaked-id"}]}"#,
            r#"{"data":[],"debug":"CANARY_QUERY_KEY_IN_API_BASE"}"#,
        ] {
            assert!(matches!(
                response_omits_secret(leaked_elsewhere, "CANARY_QUERY_KEY_IN_API_BASE"),
                Verdict::Violation(_)
            ));
        }
    }

    #[test]
    fn outbound_request_omits_secret_checks_complete_wire_request() {
        let leaked = "POST /v1/vector_stores/vs/search HTTP/1.1\r\nAuthorization: Bearer SERVER_CANARY\r\n\r\n{}";
        assert!(matches!(
            outbound_request_omits_secret(leaked, "SERVER_CANARY"),
            Verdict::Violation(_)
        ));

        let caller_owned = "POST /v1/vector_stores/vs/search HTTP/1.1\r\nAuthorization: Bearer CALLER_CANARY\r\n\r\n{}";
        assert_eq!(
            outbound_request_omits_secret(caller_owned, "SERVER_CANARY"),
            Verdict::Conformant
        );

        for malformed in ["", "not-http\r\n\r\n", "POST /missing-version\r\n\r\n"] {
            assert!(matches!(
                outbound_request_omits_secret(malformed, "SERVER_CANARY"),
                Verdict::Violation(_)
            ));
        }
        assert!(matches!(
            outbound_request_omits_secret(caller_owned, ""),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn anthropic_tool_choice_any_checker() {
        let any_fwd = r#"{"path":"/v1/responses","body":{"tool_choice":"any"}}"#;
        assert!(matches!(
            anthropic_tool_choice_any_mapped_to_required(any_fwd),
            Verdict::Violation(_)
        ));

        let req_fwd = r#"{"path":"/v1/responses","body":{"tool_choice":"required"}}"#;
        assert_eq!(
            anthropic_tool_choice_any_mapped_to_required(req_fwd),
            Verdict::Conformant
        );

        for body in [
            serde_json::json!({}),
            serde_json::json!({"tool_choice": null}),
            serde_json::json!({"tool_choice": false}),
            serde_json::json!({"tool_choice": "auto"}),
            serde_json::json!({"tool_choice": {"mode": "required"}}),
            serde_json::json!({"tool_choice": {"type": "required"}}),
            serde_json::json!({"tool_choice": {"type": "any", "mode": "required"}}),
        ] {
            let capture = serde_json::json!({"path": "/v1/responses", "body": body});
            assert!(matches!(
                anthropic_tool_choice_any_mapped_to_required(&capture.to_string()),
                Verdict::Violation(_)
            ));
        }
    }

    #[test]
    fn responses_single_lifecycle_checker() {
        let created = r#"data: {"type":"response.created","response":{"id":"resp_one"}}"#;
        let added = r#"data: {"type":"response.output_item.added","output_index":0,"item":{"id":"msg_one"}}"#;
        let completed = r#"data: {"type":"response.completed","response":{"id":"resp_one"}}"#;
        let valid = format!("{created}\n\n{added}\n\n{completed}\n\ndata: [DONE]\n\n");
        assert_eq!(responses_single_lifecycle(&valid), Verdict::Conformant);

        let second_created = r#"data: {"type":"response.created","response":{"id":"resp_two"}}"#;
        let duplicate = format!("{created}\n\n{completed}\n\n{second_created}\n\n{completed}\n\n");
        assert!(matches!(
            responses_single_lifecycle(&duplicate),
            Verdict::Violation(_)
        ));

        let colliding = format!(
            "{created}\n\n{added}\n\ndata: {{\"type\":\"response.output_item.added\",\"output_index\":0,\"item\":{{\"id\":\"msg_two\"}}}}\n\n{completed}\n\n"
        );
        assert!(matches!(
            responses_single_lifecycle(&colliding),
            Verdict::Violation(_)
        ));

        for invalid in [
            "",
            "data: not-json\n\n",
            created,
            completed,
            r#"data: {"type":"response.created","response":{"id":"a"}}

data: {"type":"response.completed","response":{"id":"b"}}

"#,
            r#"data: {"type":"response.created","response":{"id":"a"}}

data: {"type":"response.completed","response":{"id":"a"}}

data: {"type":"response.output_text.delta","delta":"late"}

"#,
        ] {
            assert!(matches!(
                responses_single_lifecycle(invalid),
                Verdict::Violation(_)
            ));
        }
    }

    #[test]
    fn responses_fallback_preserves_delivered_indexes_checker() {
        let created_a = r#"data: {"type":"response.created","response":{"id":"resp_a"}}"#;
        let added_fc = r#"data: {"type":"response.output_item.added","output_index":0,"item":{"id":"fc_primary"}}"#;
        let created_b = r#"data: {"type":"response.created","response":{"id":"resp_b"}}"#;

        // Bug 085: a completed primary tool call's index is reused by the
        // fallback's unrelated item. Two response.created events alone are not
        // the violation (that is a documented fallback feature); the reused
        // index for a different item is.
        let duplicate = format!(
            "{created_a}\n\n{added_fc}\n\n{created_b}\n\ndata: {{\"type\":\"response.output_item.added\",\"output_index\":0,\"item\":{{\"id\":\"fc_fallback\"}}}}\n\n"
        );
        assert!(matches!(
            responses_fallback_preserves_delivered_indexes(&duplicate),
            Verdict::Violation(_)
        ));

        // Same shape, but the second lifecycle's item is a different type
        // (message vs function_call) at the same index, still a collision.
        let item_type_swap = format!(
            "{created_a}\n\n{added_fc}\n\n{created_b}\n\ndata: {{\"type\":\"response.output_item.added\",\"output_index\":0,\"item\":{{\"id\":\"msg_fallback\"}}}}\n\n"
        );
        assert!(matches!(
            responses_fallback_preserves_delivered_indexes(&item_type_swap),
            Verdict::Violation(_)
        ));

        // Control: the primary failed before announcing any output item, so
        // there is nothing for the fallback's index 0 to collide with. Two
        // response.created events here are conformant.
        let pre_first_chunk_retry = format!(
            "{created_a}\n\n{created_b}\n\ndata: {{\"type\":\"response.output_item.added\",\"output_index\":0,\"item\":{{\"id\":\"fc_fallback\"}}}}\n\n"
        );
        assert_eq!(
            responses_fallback_preserves_delivered_indexes(&pre_first_chunk_retry),
            Verdict::Conformant
        );

        // Control: no retry at all, single lifecycle.
        let single = format!("{created_a}\n\n{added_fc}\n\n");
        assert_eq!(
            responses_fallback_preserves_delivered_indexes(&single),
            Verdict::Conformant
        );

        // Same item id reused at the same index across lifecycles is fine
        // (e.g. the fallback carrying forward the same logical item).
        let same_item = format!(
            "{created_a}\n\n{added_fc}\n\n{created_b}\n\ndata: {{\"type\":\"response.output_item.added\",\"output_index\":0,\"item\":{{\"id\":\"fc_primary\"}}}}\n\n"
        );
        assert_eq!(
            responses_fallback_preserves_delivered_indexes(&same_item),
            Verdict::Conformant
        );

        for invalid in [
            "",
            "data: not-json\n\n",
            r#"data: {"type":"response.output_item.added","item":{"id":"a"}}"#,
            r#"data: {"type":"response.output_item.added","output_index":0,"item":{}}"#,
        ] {
            assert!(matches!(
                responses_fallback_preserves_delivered_indexes(invalid),
                Verdict::Violation(_)
            ));
        }
    }

    #[test]
    fn responses_no_restart_after_output_checker() {
        let created_a = r#"data: {"type":"response.created","response":{"id":"resp_a"}}"#;
        let created_b = r#"data: {"type":"response.created","response":{"id":"resp_b"}}"#;
        let added = |id: &str, index: u64| {
            format!(
                r#"data: {{"type":"response.output_item.added","output_index":{index},"item":{{"id":"{id}"}}}}"#
            )
        };
        // Bug 085: the fallback's lifecycle starts after the primary's call
        // was delivered. Renumbering the fallback's item does not help.
        for index in [0, 1] {
            let restarted = format!(
                "{created_a}\n\n{}\n\n{created_b}\n\n{}\n\n",
                added("fc_primary", 0),
                added("fc_fallback", index)
            );
            assert!(matches!(
                responses_no_restart_after_output(&restarted),
                Verdict::Violation(_)
            ));
        }
        // Control: the primary failed before any output item.
        let before_output = format!(
            "{created_a}\n\n{created_b}\n\n{}\n\n",
            added("fc_fallback", 0)
        );
        assert_eq!(
            responses_no_restart_after_output(&before_output),
            Verdict::Conformant
        );
        let single = format!("{created_a}\n\n{}\n\n", added("fc_primary", 0));
        assert_eq!(
            responses_no_restart_after_output(&single),
            Verdict::Conformant
        );
        for invalid in ["", "data: not-json\n\n"] {
            assert!(matches!(
                responses_no_restart_after_output(invalid),
                Verdict::Violation(_)
            ));
        }
    }

    #[test]
    fn responses_fallback_not_spliced_after_delivery_checker() {
        let added = |id: &str, index: u64| {
            format!(
                r#"data: {{"type":"response.output_item.added","output_index":{index},"item":{{"id":"{id}"}}}}"#
            )
        };
        let created = r#"data: {"type":"response.created","response":{"id":"resp"}}"#;
        let error = r#"data: {"type":"error","error":{"code":"server_error"}}"#;
        let completed = r#"data: {"type":"response.completed","response":{"id":"resp"}}"#;
        let primary_failed = format!("{created}\n\n{}\n\n{error}\n\n", added("fc_primary", 0));
        let primary_ok = format!("{created}\n\n{}\n\n{completed}\n\n", added("fc_primary", 0));
        let prefail = format!("{created}\n\n{error}\n\n");
        let fallback = format!(
            "{created}\n\n{}\n\n{completed}\n\n",
            added("fc_fallback", 0)
        );

        // Bug 085 as LiteLLM emits it, and the same splice hidden behind one
        // clean lifecycle with renumbered indexes: both are violations.
        let two_lifecycles = format!(
            "{created}\n\n{}\n\n{created}\n\n{}\n\n",
            added("fc_primary", 0),
            added("fc_fallback", 0)
        );
        let composed = format!(
            "{created}\n\n{}\n\n{}\n\n{completed}\n\n",
            added("fc_primary", 0),
            added("fc_fallback", 1)
        );
        for client in [&two_lifecycles, &composed] {
            assert!(matches!(
                responses_fallback_not_spliced_after_delivery(
                    client,
                    &[&primary_failed, &fallback]
                ),
                Verdict::Violation(_)
            ));
        }
        // Controls: nothing delivered before the failure, no failure at all,
        // and a failed attempt whose items never reached the client.
        let fallback_only = format!("{created}\n\n{created}\n\n{}\n\n", added("fc_fallback", 0));
        assert_eq!(
            responses_fallback_not_spliced_after_delivery(&fallback_only, &[&prefail, &fallback]),
            Verdict::Conformant
        );
        let single = format!("{created}\n\n{}\n\n", added("fc_primary", 0));
        assert_eq!(
            responses_fallback_not_spliced_after_delivery(&single, &[&primary_ok]),
            Verdict::Conformant
        );
        let unseen = format!("{created}\n\n{}\n\n", added("fc_fallback", 0));
        assert_eq!(
            responses_fallback_not_spliced_after_delivery(&unseen, &[&primary_failed, &fallback]),
            Verdict::Conformant
        );
        assert!(matches!(
            responses_fallback_not_spliced_after_delivery("", &[&primary_failed]),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn gemini_inline_media_checker_needs_the_upstream_to_decide() {
        let with_image = r#"{"candidates":[{"content":{"parts":[
            {"text":"here you go"},
            {"inlineData":{"mimeType":"image/png","data":"Zm9v"}}
        ]}}]}"#;
        let text_only = r#"{"candidates":[{"content":{"parts":[{"text":"no image today"}]}}]}"#;
        let client_text_only =
            r#"{"choices":[{"message":{"role":"assistant","content":"here you go"}}]}"#;
        let client_with_block = r#"{"choices":[{"message":{"role":"assistant","content":[
            {"type":"image_url","image_url":{"url":"data:image/png;base64,Zm9v"}}
        ]}}]}"#;

        // The defect: upstream generated an image, the client response dropped it.
        assert!(matches!(
            gemini_inline_media_preserved_in_chat_response(with_image, client_text_only),
            Verdict::Violation(_)
        ));
        // Fixed behavior: the same upstream, but the client response carries the block.
        assert_eq!(
            gemini_inline_media_preserved_in_chat_response(with_image, client_with_block),
            Verdict::Conformant
        );
        // Nothing to preserve: upstream never generated an image this turn.
        assert_eq!(
            gemini_inline_media_preserved_in_chat_response(text_only, client_text_only),
            Verdict::Conformant
        );
    }

    #[test]
    fn gemini_inline_media_checker_rejects_malformed_input() {
        assert!(matches!(
            gemini_inline_media_preserved_in_chat_response("not json", "{}"),
            Verdict::Violation(_)
        ));
        let with_image = r#"{"candidates":[{"content":{"parts":[
            {"inlineData":{"mimeType":"image/png","data":"Zm9v"}}
        ]}}]}"#;
        assert!(matches!(
            gemini_inline_media_preserved_in_chat_response(with_image, "not json"),
            Verdict::Violation(_)
        ));
        assert!(matches!(
            gemini_inline_media_preserved_in_chat_response(with_image, "{}"),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn gemini_inline_media_stream_checker() {
        let chunk_with_image_dropped = r#"data: {"choices":[{"delta":{"role":"assistant"}}],"extra_fields":{"raw_response":"{\"candidates\":[{\"content\":{\"parts\":[{\"inlineData\":{\"mimeType\":\"image/png\",\"data\":\"Zm9v\"}}]}}]}"}}

data: [DONE]
"#;
        assert!(matches!(
            gemini_inline_media_preserved_in_chat_stream(chunk_with_image_dropped),
            Verdict::Violation(_)
        ));

        let chunk_with_image_preserved = r#"data: {"choices":[{"delta":{"content":[{"type":"image_url","image_url":{"url":"data:image/png;base64,Zm9v"}}]}}],"extra_fields":{"raw_response":"{\"candidates\":[{\"content\":{\"parts\":[{\"inlineData\":{\"mimeType\":\"image/png\",\"data\":\"Zm9v\"}}]}}]}"}}

data: [DONE]
"#;
        assert_eq!(
            gemini_inline_media_preserved_in_chat_stream(chunk_with_image_preserved),
            Verdict::Conformant
        );

        let chunk_text_only = r#"data: {"choices":[{"delta":{"content":"hi"}}],"extra_fields":{"raw_response":"{\"candidates\":[{\"content\":{\"parts\":[{\"text\":\"hi\"}]}}]}"}}

data: [DONE]
"#;
        assert_eq!(
            gemini_inline_media_preserved_in_chat_stream(chunk_text_only),
            Verdict::Conformant
        );
    }

    #[test]
    fn gemini_inline_media_checker_still_flags_the_observed_caption_only_drop() {
        // The exact shape currently observed live (transcripts/075/live/chat-nonstream.jsonl):
        // a real caption comes through in message.content as a plain string, but there is no
        // content array, no image_url/input_audio block, and no images[] sibling array at all.
        // This must still score Violation; broadening the checker for #4907 must not paper over
        // the still-current defect.
        let with_image = r#"{"candidates":[{"content":{"parts":[
            {"text":"Here's that image for you! "},
            {"inlineData":{"mimeType":"image/png","data":"Zm9v"}}
        ]}}]}"#;
        let client_caption_only = r#"{"choices":[{"message":{
            "role":"assistant","content":"Here's that image for you! "
        }}]}"#;
        assert!(matches!(
            gemini_inline_media_preserved_in_chat_response(with_image, client_caption_only),
            Verdict::Violation(_)
        ));
    }

    #[test]
    fn gemini_inline_media_checker_accepts_upstream_pr_4907_images_sibling_shape() {
        // Regression test for B1: bifrost's own upstream has an open PR,
        // https://github.com/maximhq/bifrost/pull/4907 ("Closes #2367"), that adds the
        // assistant-side image carrier as a SIBLING field `ChatAssistantMessage.Images` ->
        // JSON `"images": [{"type": "image_url", "image_url": {"url": "data:..."}}]`, not as a
        // content block. If Gemini's chat.go is ever fixed following that same shape, the
        // checker must recognize it as Conformant, not Violation, or the frozen invariant could
        // never detect the real fix landing.
        let with_image = r#"{"candidates":[{"content":{"parts":[
            {"text":"here you go"},
            {"inlineData":{"mimeType":"image/png","data":"Zm9v"}}
        ]}}]}"#;
        let client_with_images_sibling = r#"{"choices":[{"message":{
            "role":"assistant",
            "content":"here you go",
            "images":[{"type":"image_url","image_url":{"url":"data:image/png;base64,Zm9v"}}]
        }}]}"#;
        assert_eq!(
            gemini_inline_media_preserved_in_chat_response(with_image, client_with_images_sibling),
            Verdict::Conformant
        );

        // An empty images[] array must NOT count: that is not a real fix, just an empty field.
        let client_empty_images = r#"{"choices":[{"message":{
            "role":"assistant","content":"here you go","images":[]
        }}]}"#;
        assert!(matches!(
            gemini_inline_media_preserved_in_chat_response(with_image, client_empty_images),
            Verdict::Violation(_)
        ));

        // Streaming counterpart: the images[] sibling array on `delta` in a later chunk than the
        // one carrying the raw upstream inlineData part must still be recognized.
        let stream_with_images_sibling = r#"data: {"choices":[{"delta":{"role":"assistant","content":"here you go"}}],"extra_fields":{"raw_response":"{\"candidates\":[{\"content\":{\"parts\":[{\"inlineData\":{\"mimeType\":\"image/png\",\"data\":\"Zm9v\"}}]}}]}"},"id":"exch-1"}

data: {"choices":[{"delta":{"images":[{"type":"image_url","image_url":{"url":"data:image/png;base64,Zm9v"}}]}}],"id":"exch-1"}

data: [DONE]
"#;
        assert_eq!(
            gemini_inline_media_preserved_in_chat_stream(stream_with_images_sibling),
            Verdict::Conformant
        );
    }

    #[test]
    fn response_conversation_checker_requires_complete_valid_evidence() {
        let clean = r#"{"body_raw":"{\"messages\":[{\"role\":\"user\",\"content\":\"SEED CANARY\"},{\"role\":\"assistant\",\"content\":\"ok\"},{\"role\":\"user\",\"content\":\"RECALL\"}]}"}"#;
        assert_eq!(
            response_conversation_preserves_history(clean, "RECALL", "SEED CANARY", 1,),
            Verdict::Conformant
        );
        for evidence in ["", "not-json", r#"{"auth_scope":"attacker"}"#] {
            assert!(matches!(
                response_conversation_preserves_history(evidence, "RECALL", "SEED CANARY", 1,),
                Verdict::Violation(_)
            ));
        }
    }

    #[test]
    fn endpoint_family_checker_rejects_vacuous_or_foreign_evidence() {
        let json = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n";
        let sse = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n";
        let chat = r#"{"object":"chat.completion","choices":[{"index":0,"message":{"role":"assistant","content":"x"},"finish_reason":"stop"}]}"#;
        let response = r#"{"object":"response","output":[]}"#;
        assert_eq!(
            endpoint_response_family_preserved("/v1/chat/completions", &format!("{json}{chat}")),
            Verdict::Conformant
        );
        assert_eq!(
            endpoint_response_family_preserved("/v1/responses", &format!("{json}{response}")),
            Verdict::Conformant
        );
        let responses_stream =
            "data: {\"type\":\"response.created\"}\n\ndata: {\"type\":\"response.completed\"}\n\n";
        assert_eq!(
            endpoint_response_family_preserved(
                "/v1/responses",
                &format!("{sse}{responses_stream}")
            ),
            Verdict::Conformant
        );
        let cases = [
            ("/v1/responses", format!("{json}{chat}")),
            ("/v1/chat/completions", format!("{json}{response}")),
            ("/v1/responses", format!("{json}null")),
            ("/v1/chat/completions", format!("{json}not-json")),
            (
                "/v1/chat/completions",
                format!("HTTP/1.1 500 Internal Server Error\r\n\r\n{chat}"),
            ),
            ("/v1/chat/completions", chat.to_owned()),
            ("/v1/embeddings", format!("{json}{chat}")),
            (
                "/v1/responses",
                format!("{sse}data: {{\"type\":\"response.created\"}}\n\n"),
            ),
            ("/v1/chat/completions", format!("{sse}{responses_stream}")),
            ("/v1/chat/completions", format!("{sse}data: [DONE]\n\n")),
        ];
        for (endpoint, capture) in cases {
            assert!(
                matches!(
                    endpoint_response_family_preserved(endpoint, &capture),
                    Verdict::Violation(_)
                ),
                "{endpoint} must reject {capture:?}"
            );
        }
    }
}
