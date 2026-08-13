//! Conformance suite over recorded wire transcripts.
//!
//! Each test loads bytes captured from a real gateway and asserts the verdict
//! the checker should return for that recording. A `Violation` assertion is a
//! reproduced, frozen bug: the day a gateway (or our own router) stops
//! violating the invariant, this test flips and tells us.

use kairo::checks::{
    anthropic_toolcall_stop_reason, content_filter_preserved, document_body_forwarded,
    is_error_forwarded, no_indexerror_leak, no_invented_cache_control, no_phantom_null_output_text,
    non_text_block_not_json_dumped, openai_stream_finish_reason, openai_toolcall_id_charset,
    parallel_tool_disable_preserved, reasoning_text_order_preserved,
    thinking_not_leaked_as_visible_text, thinking_text_forwarded, upstream_bearer_is, Verdict,
};
use std::fs;
use std::path::PathBuf;

/// Repo-root-relative path to a recorded transcript.
fn fixture(rel: &str) -> String {
    let mut p = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    p.push("../../");
    p.push(rel);
    fs::read_to_string(&p).unwrap_or_else(|e| panic!("read {}: {e}", p.display()))
}

// ---- bug 001: LiteLLM 1.82 vs 1.96 stop_reason on Anthropic stream ----

#[test]
fn litellm_196_marks_toolcall_stop_reason_correctly() {
    // Current LiteLLM (1.96.2) is conformant on this path, the control.
    let v = anthropic_toolcall_stop_reason(&fixture("transcripts/001/gemma-stream.sse"));
    assert_eq!(
        v,
        Verdict::Conformant,
        "1.96.2 should label the tool call stop_reason=tool_use"
    );
}

#[test]
fn litellm_182_violates_stop_reason_regression() {
    // LiteLLM 1.82.0 mislabels it end_turn, the frozen regression.
    let v = anthropic_toolcall_stop_reason(&fixture("transcripts/001/gemma-stream-182.sse"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "1.82.0 should be caught: {v:?}"
    );
}

// ---- bug 002a: ollama_chat/ stream finishes as "stop" ----

#[test]
fn litellm_ollama_stream_finish_reason_violation() {
    let v = openai_stream_finish_reason(&fixture("transcripts/002/qwen3-ollama-chat-stream.sse"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "ollama_chat stream should be caught finishing as stop: {v:?}"
    );
}

// ---- bug 004: Gemini signature smuggled into the tool-call id ----

#[test]
fn litellm_gemini_toolcall_id_charset_violation() {
    let v = openai_toolcall_id_charset(&fixture("transcripts/004/turn1-response.json"));
    match v {
        Verdict::Violation(reason) => {
            assert!(
                reason.contains("length"),
                "should report the oversized id: {reason}"
            );
        }
        Verdict::Conformant => panic!("the 382-char id must be caught"),
    }
}

// ---- bug 010A: Switchyard maps content_filter to end_turn (their #369) ----

#[test]
fn switchyard_content_filter_erased_violation() {
    // Captured: upstream finish_reason content_filter, client got stop_reason end_turn.
    let v = content_filter_preserved("content_filter", "end_turn");
    assert!(
        matches!(v, Verdict::Violation(_)),
        "content_filter erasure must be caught: {v:?}"
    );
}

// ---- bug 010B: Switchyard reorders reasoning/text within a chunk (their #242) ----

#[test]
fn switchyard_reasoning_text_reorder_violation() {
    // Captured: backend emitted thinking then text in one chunk; client got text then thinking.
    let v = reasoning_text_order_preserved(&["thinking", "text"], &["text", "thinking"]);
    assert!(
        matches!(v, Verdict::Violation(_)),
        "reasoning/text reorder must be caught: {v:?}"
    );
}

#[test]
fn separate_chunk_order_is_conformant() {
    // Control: separate chunks preserve order.
    let v = reasoning_text_order_preserved(
        &["thinking", "text", "thinking", "text"],
        &["thinking", "text", "thinking", "text"],
    );
    assert_eq!(v, Verdict::Conformant);
}

// ---- bug 006: is_error dropped on Anthropic -> OpenAI/Responses ----

#[test]
fn switchyard_drops_is_error() {
    let v = is_error_forwarded(&fixture("transcripts/014/capture.jsonl"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Switchyard must be caught dropping is_error: {v:?}"
    );
}

#[test]
fn litellm_drops_is_error_on_messages_to_responses() {
    let v = is_error_forwarded(&fixture("transcripts/016/cap-litellm-is-error.jsonl"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "LiteLLM must be caught dropping is_error: {v:?}"
    );
}

// ---- bug 007: multimodal tool_result JSON-dumped (Switchyard) or deleted (LiteLLM) ----

#[test]
fn switchyard_json_dumps_image_in_tool_result() {
    let v = non_text_block_not_json_dumped(
        &fixture("transcripts/007/capA.jsonl"),
        "\"type\":\"image\"",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Switchyard must be caught dumping image JSON: {v:?}"
    );
}

#[test]
fn litellm_deletes_image_in_tool_result() {
    let v = document_body_forwarded(
        &fixture("transcripts/016/cap-litellm-image-toolresult.jsonl"),
        "iVBORw0KGgo",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "LiteLLM must be caught deleting the image payload: {v:?}"
    );
}

// ---- bug 008: IndexError leaked as HTTP 500 ----

#[test]
fn litellm_indexerror_leak_violation() {
    let v = no_indexerror_leak(&fixture("transcripts/probe/bug008-clientbody.json"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "IndexError leak must be caught: {v:?}"
    );
}

// ---- bug 009: phantom null output_text on /v1/responses ----

#[test]
fn litellm_phantom_null_output_text_violation() {
    let v = no_phantom_null_output_text(&fixture("transcripts/probe/resp009-1.json"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "phantom null text must be caught: {v:?}"
    );
}

// ---- bug 016: thinking history destroyed ----

#[test]
fn switchyard_drops_thinking_from_request() {
    let v = thinking_text_forwarded(
        &fixture("transcripts/016/cap-thinking.jsonl"),
        "simple arithmetic",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Switchyard must be caught dropping thinking: {v:?}"
    );
}

#[test]
fn litellm_leaks_thinking_as_output_text() {
    let v = thinking_not_leaked_as_visible_text(
        &fixture("transcripts/016/cap-litellm-thinking.jsonl"),
        "simple arithmetic",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "LiteLLM must be caught leaking thinking into output_text: {v:?}"
    );
}

// ---- bug 017: disable_parallel_tool_use dropped ----

#[test]
fn switchyard_drops_disable_parallel_tool_use() {
    let v = parallel_tool_disable_preserved(&fixture("transcripts/016/cap-parallel.jsonl"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Switchyard must be caught dropping parallel disable: {v:?}"
    );
}

#[test]
fn litellm_drops_disable_parallel_tool_use() {
    let v = parallel_tool_disable_preserved(&fixture("transcripts/016/cap-litellm-parallel.jsonl"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "LiteLLM must be caught dropping parallel disable: {v:?}"
    );
}

#[test]
fn same_format_openai_preserves_parallel_tool_calls_false() {
    // Control: LiteLLM OpenAI-chat same-format keeps parallel_tool_calls: false.
    let v = parallel_tool_disable_preserved(&fixture(
        "transcripts/016/cap-litellm-openai-strict.jsonl",
    ));
    assert_eq!(
        v,
        Verdict::Conformant,
        "same-format OpenAI path should keep the flag: {v:?}"
    );
}

// ---- bug 018: user document dropped or JSON-dumped ----

#[test]
fn litellm_deletes_user_document() {
    let v = document_body_forwarded(
        &fixture("transcripts/016/cap-litellm-document.jsonl"),
        "THE DOCUMENT BODY",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "LiteLLM must be caught deleting the document: {v:?}"
    );
}

#[test]
fn switchyard_json_dumps_user_document() {
    let v = non_text_block_not_json_dumped(
        &fixture("transcripts/016/cap-document.jsonl"),
        "\"type\":\"document\"",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Switchyard must be caught dumping the document: {v:?}"
    );
}

// ---- bug 019: Switchyard invents cache_control on Anthropic backends ----

#[test]
fn switchyard_invents_cache_control_on_anthropic_backend() {
    let v = no_invented_cache_control(&fixture("transcripts/016/cap-openai-strict.jsonl"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Switchyard must be caught inventing cache_control: {v:?}"
    );
}

// ---- bug 020: client api_key overrides (and can stick to) the deployment key ----

#[test]
fn litellm_control_uses_deployment_key() {
    let v = upstream_bearer_is(&fixture("transcripts/020/cap-control.jsonl"), "sk-x");
    assert_eq!(
        v,
        Verdict::Conformant,
        "request without api_key should use the configured mock key: {v:?}"
    );
}

#[test]
fn litellm_client_api_key_overrides_deployment() {
    let v = upstream_bearer_is(&fixture("transcripts/020/cap-override.jsonl"), "sk-x");
    assert!(
        matches!(v, Verdict::Violation(_)),
        "client body api_key must be caught replacing the deployment key: {v:?}"
    );
}

#[test]
fn litellm_overridden_api_key_sticks_to_next_caller() {
    let v = upstream_bearer_is(&fixture("transcripts/020/cap-sticky.jsonl"), "sk-x");
    assert!(
        matches!(v, Verdict::Violation(_)),
        "a later request that did not send api_key must not inherit the previous caller's key: {v:?}"
    );
}
