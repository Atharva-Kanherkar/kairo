//! Conformance suite over recorded wire transcripts.
//!
//! Each test loads bytes captured from a real gateway and asserts the verdict
//! the checker should return for that recording. A `Violation` assertion is a
//! reproduced, frozen bug: the day a gateway (or our own router) stops
//! violating the invariant, this test flips and tells us.

use kairo::checks::{
    anthropic_response_toolcall_stop_reason, anthropic_stream_safety_stop_reason,
    anthropic_tool_choice_any_mapped_to_required, anthropic_toolcall_stop_reason, capture_records,
    content_filter_preserved, document_body_forwarded, executed_tool_results_preserved,
    gemini_inline_media_preserved_in_chat_response, gemini_inline_media_preserved_in_chat_stream,
    id_conforms, image_url_cache_key_case_sensitive, instruction_messages_preserved,
    invalid_credential_rejected_before_upstream, is_error_forwarded, json_schema_forwarded,
    json_schema_property_forwarded, model_info_capture_identity, model_info_envelope_body,
    model_info_omits_api_base_secret, no_empty_text_alongside_tool_use, no_indexerror_leak,
    no_invented_cache_control, no_phantom_null_output_text, non_text_block_not_json_dumped,
    ogx_adaptive_thinking_loss, openai_stream_finish_reason, openai_toolcall_id_charset,
    outbound_request_omits_secret, parallel_tool_disable_preserved, reasoning_text_order_preserved,
    refusal_text_preserved, response_content_not_empty, response_omits_secret,
    responses_refusal_semantics_preserved, responses_single_lifecycle, stop_sequence_forwarded,
    thinking_not_leaked_as_visible_text, thinking_text_forwarded, tool_strict_forwarded,
    toolcall_id_restored_upstream, truncation_preserved, upstream_bearer_is,
    upstream_omits_header_value, FunctionToolFormat, Verdict, EMPTY_TEXT_ALONGSIDE_TOOL_USE,
    JSON_SCHEMA_ABSENT, JSON_SCHEMA_PROPERTY_ABSENT,
};
use serde_json::Value;
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

// ---- bug 023: Switchyard forwards api-key and OpenAI org/project headers ----

#[test]
fn switchyard_control_does_not_invent_api_key_header() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/023/cap-control.jsonl"),
        "CANARY_AZURE_API_KEY",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "a request with no api-key header should not grow one: {v:?}"
    );
}

#[test]
fn switchyard_forwards_api_key_header() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/023/cap-api-key.jsonl"),
        "CANARY_AZURE_API_KEY",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "client api-key must be caught on the upstream wire: {v:?}"
    );
}

#[test]
fn switchyard_forwards_openai_organization_header() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/023/cap-openai-org.jsonl"),
        "CANARY_OPENAI_ORG",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "client OpenAI-Organization must be caught on the upstream wire: {v:?}"
    );
}

#[test]
fn switchyard_strips_reserved_x_api_key() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/023/cap-x-api-key-stripped.jsonl"),
        "CANARY_X_API_KEY",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "reserved x-api-key should still be stripped: {v:?}"
    );
}

// ---- bug 024: LiteLLM GET /health returns extra_headers and aws_session_token ----

#[test]
fn litellm_health_leaks_extra_headers() {
    let v = response_omits_secret(
        &fixture("transcripts/024/health.json"),
        "CANARY_EXTRA_HEADERS_AUTHORIZATION",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "GET /health must be caught returning extra_headers.Authorization: {v:?}"
    );
}

#[test]
fn litellm_health_leaks_aws_session_token() {
    let v = response_omits_secret(
        &fixture("transcripts/024/health.json"),
        "CANARY_AWS_SESSION_TOKEN_VALUE",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "GET /health must be caught returning aws_session_token: {v:?}"
    );
}

#[test]
fn litellm_models_control_omits_health_secrets() {
    let body = fixture("transcripts/024/models-control.json");
    assert_eq!(
        response_omits_secret(&body, "CANARY_EXTRA_HEADERS_AUTHORIZATION"),
        Verdict::Conformant,
        "/v1/models must not grow extra_headers"
    );
    assert_eq!(
        response_omits_secret(&body, "CANARY_AWS_SESSION_TOKEN_VALUE"),
        Verdict::Conformant,
        "/v1/models must not grow aws_session_token"
    );
}

#[test]
fn litellm_chat_control_omits_health_secrets() {
    let v = response_omits_secret(
        &fixture("transcripts/024/chat-control.json"),
        "CANARY_EXTRA_HEADERS_AUTHORIZATION",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "a chat completion must not echo deployment extra_headers: {v:?}"
    );
}

#[test]
fn litellm_liveliness_control_omits_health_secrets() {
    let v = response_omits_secret(
        &fixture("transcripts/024/liveliness-control.json"),
        "CANARY_EXTRA_HEADERS_AUTHORIZATION",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "/health/liveliness must stay a boolean alive probe: {v:?}"
    );
}

// ---- bug 025: Switchyard transport errors echo ?key= from base_url ----

#[test]
fn switchyard_transport_error_leaks_query_key() {
    let v = response_omits_secret(
        &fixture("transcripts/025/transport-query-key.json"),
        "CANARY_ADMIN_QUERY_KEY",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "a transport 502 must be caught echoing base_url ?key=: {v:?}"
    );
}

#[test]
fn switchyard_transport_error_without_query_key_is_clean() {
    let v = response_omits_secret(
        &fixture("transcripts/025/transport-control.json"),
        "CANARY_ADMIN_QUERY_KEY",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "a 502 whose base_url has no query key must not invent one: {v:?}"
    );
}

#[test]
fn switchyard_chat_control_omits_query_key() {
    let v = response_omits_secret(
        &fixture("transcripts/025/chat-control.json"),
        "CANARY_ADMIN_QUERY_KEY",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "a successful chat completion must not echo a query key: {v:?}"
    );
}

// ---- bug 026: LiteLLM JSON extra_headers / headers / organization ----

#[test]
fn litellm_026_control_uses_deployment_key() {
    let v = upstream_bearer_is(&fixture("transcripts/026/cap-control.jsonl"), "sk-x");
    assert_eq!(
        v,
        Verdict::Conformant,
        "request without extra_headers should use the configured mock key: {v:?}"
    );
}

#[test]
fn litellm_extra_headers_authorization_overrides_deployment() {
    let v = upstream_bearer_is(
        &fixture("transcripts/026/cap-extra-headers-auth.jsonl"),
        "sk-x",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "JSON extra_headers.Authorization must be caught replacing the deployment key: {v:?}"
    );
}

#[test]
fn litellm_headers_field_authorization_overrides_deployment() {
    let v = upstream_bearer_is(&fixture("transcripts/026/cap-headers-auth.jsonl"), "sk-x");
    assert!(
        matches!(v, Verdict::Violation(_)),
        "JSON headers.Authorization must be caught replacing the deployment key: {v:?}"
    );
}

#[test]
fn litellm_json_organization_becomes_openai_organization_header() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/026/cap-organization.jsonl"),
        "CANARY_BODY_ORGANIZATION",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "JSON organization must be caught as upstream OpenAI-Organization: {v:?}"
    );
}

#[test]
fn litellm_extra_headers_openai_org_forwarded() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/026/cap-extra-headers-org.jsonl"),
        "CANARY_BODY_OPENAI_ORG",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "JSON extra_headers.OpenAI-Organization must be caught on the upstream wire: {v:?}"
    );
}

#[test]
fn litellm_extra_headers_api_key_forwarded() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/026/cap-extra-headers-azure.jsonl"),
        "CANARY_BODY_AZURE_API_KEY",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "JSON extra_headers.api-key must be caught on the upstream wire: {v:?}"
    );
}

#[test]
fn litellm_026_control_omits_org_canary() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/026/cap-control.jsonl"),
        "CANARY_BODY_ORGANIZATION",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "a request with no organization field should not grow one: {v:?}"
    );
}

// ---- bug 027: Switchyard forwards x-goog-api-key ----

#[test]
fn switchyard_control_does_not_invent_x_goog_api_key() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/026/cap-sy-control.jsonl"),
        "CANARY_SY_X_GOOG",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "a request with no x-goog-api-key should not grow one: {v:?}"
    );
}

#[test]
fn switchyard_forwards_x_goog_api_key() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/026/cap-sy-x-goog.jsonl"),
        "CANARY_SY_X_GOOG",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "client x-goog-api-key must be caught on the upstream wire: {v:?}"
    );
}

// ---- bug 028: LiteLLM /gemini pass-through copies ?key= in x-goog-upload-url ----

#[test]
fn litellm_gemini_passthrough_leaks_upload_url_key() {
    let v = response_omits_secret(
        &fixture("transcripts/028/pt-upload-leak.json"),
        "CANARY_GEMINI_PASSTHROUGH_KEY",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "/gemini upload response headers must be caught echoing ?key=: {v:?}"
    );
}

#[test]
fn litellm_chat_control_omits_gemini_passthrough_key() {
    let v = response_omits_secret(
        &fixture("transcripts/028/chat-control.json"),
        "CANARY_GEMINI_PASSTHROUGH_KEY",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "a chat completion must not echo the pass-through query key: {v:?}"
    );
}

#[test]
fn litellm_plain_upload_control_omits_gemini_passthrough_key() {
    let v = response_omits_secret(
        &fixture("transcripts/028/pt-upload-plain-control.json"),
        "CANARY_GEMINI_PASSTHROUGH_KEY",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "a simple /gemini/upload without resumable headers must stay clean: {v:?}"
    );
}

#[test]
fn litellm_closed_port_passthrough_omits_query_key() {
    let v = response_omits_secret(
        &fixture("transcripts/028/closed-port-control.json"),
        "CANARY_CLOSED_PORT_KEY",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "a closed-port /gemini 500 must not echo ?key= the way Switchyard 025 does: {v:?}"
    );
}

// ---- bug 030: Bifrost /anthropic/v1/messages streaming drops stop_reason=tool_use ----
//
// Third tool under test. The streaming checker below is the SAME one written for
// bug 001 against LiteLLM: the invariant is a property of the Anthropic wire
// contract, not of any one gateway, so it ports to Bifrost unchanged.

#[test]
fn bifrost_anthropic_stream_loses_toolcall_stop_reason() {
    let v = anthropic_toolcall_stop_reason(&fixture("transcripts/030/anthropic-stream.sse"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Bifrost v1.6.11 streaming must be caught ending a tool_use turn as end_turn: {v:?}"
    );
}

#[test]
fn bifrost_anthropic_nonstream_control_keeps_toolcall_stop_reason() {
    // Same turn, same upstream, non-streaming: conformant. This is what isolates
    // the defect to the streaming serializer rather than the translation as a whole.
    let body = fixture("transcripts/030/anthropic-nonstream.json");
    assert_eq!(
        anthropic_response_toolcall_stop_reason(&body),
        Verdict::Conformant,
        "the non-streaming Anthropic route maps the same turn correctly"
    );
    // Conformant alone is vacuous: the checker also returns it for a turn carrying no
    // tool_use block at all. Flip the reason and require a Violation, which only the
    // tool_use path can produce, so a fixture that lost its tool call fails here
    // instead of passing as a silent false green.
    let flipped = body.replace(
        "\"stop_reason\": \"tool_use\"",
        "\"stop_reason\": \"end_turn\"",
    );
    assert_ne!(
        flipped, body,
        "fixture no longer contains stop_reason tool_use"
    );
    assert!(
        matches!(
            anthropic_response_toolcall_stop_reason(&flipped),
            Verdict::Violation(_)
        ),
        "control is vacuous: fixture carries no tool_use block for the checker to judge"
    );
}

#[test]
fn bifrost_openai_stream_control_keeps_toolcall_finish_reason() {
    // The OpenAI-shaped streaming route on the same gateway and turn is conformant,
    // so the upstream really did report a tool call.
    let sse = fixture("transcripts/030/openai-stream.sse");
    assert_eq!(
        openai_stream_finish_reason(&sse),
        Verdict::Conformant,
        "the OpenAI streaming route preserves tool_calls on the same turn"
    );
    // Same vacuity guard: `openai_stream_finish_reason` short-circuits to Conformant
    // when the stream has no tool_calls delta, so a text-only fixture would satisfy
    // the assertion above without proving anything about the upstream.
    let flipped = sse.replace(
        "\"finish_reason\":\"tool_calls\"",
        "\"finish_reason\":\"stop\"",
    );
    assert_ne!(
        flipped, sse,
        "fixture no longer contains finish_reason tool_calls"
    );
    assert!(
        matches!(openai_stream_finish_reason(&flipped), Verdict::Violation(_)),
        "control is vacuous: fixture carries no tool_calls delta for the checker to judge"
    );
}

// ---- bugs 031-037: Bifrost Anthropic-ingress translation losses ----
//
// All frozen from one offline rig (transcripts/bifrost-rig/): a Bifrost gateway
// whose only provider is a capture upstream, so no provider keys are involved and
// every fixture is replayable. Request-side fixtures are capture-format JSONL and
// are read by the SAME checkers written for LiteLLM and Switchyard, unchanged.

#[test]
fn bifrost_drops_disable_parallel_tool_use() {
    let v = parallel_tool_disable_preserved(&fixture("transcripts/031/upstream-request.jsonl"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Bifrost must be caught dropping disable_parallel_tool_use: {v:?}"
    );
}

#[test]
fn bifrost_openai_route_keeps_parallel_tool_calls() {
    // Control: the same gateway forwards the flag on its OpenAI route, so the loss
    // above is specific to the Anthropic ingress and not a limit of the upstream.
    let v =
        parallel_tool_disable_preserved(&fixture("transcripts/031/control-openai-upstream.jsonl"));
    assert_eq!(
        v,
        Verdict::Conformant,
        "the OpenAI route forwards parallel_tool_calls=false: {v:?}"
    );
}

#[test]
fn bifrost_drops_stop_sequences() {
    let v = stop_sequence_forwarded(
        &fixture("transcripts/032/upstream-request.jsonl"),
        "STOPPROBE",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Bifrost must be caught dropping stop_sequences: {v:?}"
    );
}

#[test]
fn bifrost_openai_route_keeps_stop_sequences() {
    let v = stop_sequence_forwarded(
        &fixture("transcripts/032/control-openai-upstream.jsonl"),
        "STOPPROBE",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "the OpenAI route forwards stop: {v:?}"
    );
}

// ---- bug 040: Switchyard drops Anthropic output_format ----

#[test]
fn switchyard_drops_anthropic_output_format() {
    let v = json_schema_forwarded(&fixture("transcripts/040/sy-output-format-upstream.jsonl"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Switchyard Anthropic ingress must be caught dropping output_format: {v:?}"
    );
}

#[test]
fn switchyard_openai_route_keeps_response_format() {
    let v = json_schema_forwarded(&fixture(
        "transcripts/040/sy-openai-response-format-upstream.jsonl",
    ));
    assert_eq!(
        v,
        Verdict::Conformant,
        "Switchyard OpenAI route forwards response_format: {v:?}"
    );
}

#[test]
fn litellm_messages_keeps_output_format() {
    let v = json_schema_forwarded(&fixture("transcripts/040/ll-output-format-upstream.jsonl"));
    assert_eq!(
        v,
        Verdict::Conformant,
        "LiteLLM /v1/messages maps output_format onto Responses text.format: {v:?}"
    );
}

// ---- bug 041: LiteLLM /v1/messages drops stop_sequences ----

#[test]
fn litellm_messages_drops_stop_sequences() {
    let v = stop_sequence_forwarded(
        &fixture("transcripts/040/ll-stop-upstream.jsonl"),
        "STOPPROBE",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "LiteLLM /v1/messages must be caught dropping stop_sequences: {v:?}"
    );
}

#[test]
fn litellm_openai_route_keeps_stop_sequences() {
    let v = stop_sequence_forwarded(
        &fixture("transcripts/040/ll-openai-stop-upstream.jsonl"),
        "STOPPROBE",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "LiteLLM OpenAI route forwards stop: {v:?}"
    );
}

// ---- bug 064: LiteLLM /v1/messages drops function-tool strictness ----

#[test]
fn litellm_drops_anthropic_tool_strictness() {
    let records = capture_records(&fixture(
        "transcripts/064/litellm-messages-strict-upstream.jsonl",
    ))
    .expect("064 captures parse");
    assert_eq!(records.len(), 5, "064 must retain five live capture runs");
    for (line, _) in &records {
        let capture: serde_json::Value = serde_json::from_str(line).expect("064 capture record");
        assert_eq!(
            capture["path"], "/v1/responses",
            "064 Messages ingress must target Responses"
        );
        let v = tool_strict_forwarded(line, "strict_probe", FunctionToolFormat::OpenAiResponses);
        assert!(
            matches!(v, Verdict::Violation(_)),
            "LiteLLM Anthropic ingress must be caught dropping strict: {v:?}"
        );
    }
    let results: serde_json::Value =
        serde_json::from_str(&fixture("transcripts/064/litellm-messages-results.json"))
            .expect("064 client results parse");
    let rows = results.as_array().expect("064 client results array");
    assert_eq!(rows.len(), 5, "064 must retain five client results");
    assert!(
        rows.iter().all(|row| row["status"] == 200),
        "064 must remain a silent HTTP 200 loss"
    );
}

#[test]
fn litellm_openai_route_keeps_tool_strictness() {
    let fixture = fixture("transcripts/064/litellm-openai-strict-control.jsonl");
    let capture: serde_json::Value = serde_json::from_str(&fixture).expect("064 OpenAI control");
    assert_eq!(capture["path"], "/v1/chat/completions");
    let v = tool_strict_forwarded(&fixture, "strict_probe", FunctionToolFormat::OpenAiChat);
    assert_eq!(
        v,
        Verdict::Conformant,
        "LiteLLM OpenAI ingress must keep function strictness: {v:?}"
    );
}

#[test]
fn responses_tool_strictness_direct_control() {
    let fixture = fixture("transcripts/064/responses-strict-direct-control.jsonl");
    let capture: serde_json::Value = serde_json::from_str(&fixture).expect("064 direct control");
    assert_eq!(capture["path"], "/v1/responses");
    let v = tool_strict_forwarded(
        &fixture,
        "strict_probe",
        FunctionToolFormat::OpenAiResponses,
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "Responses-native function tools must carry strict at the tool level: {v:?}"
    );
}

// ---- bug 066: Switchyard /v1/messages drops function-tool strictness ----

#[test]
fn switchyard_drops_anthropic_tool_strictness() {
    let records = capture_records(&fixture(
        "transcripts/066/switchyard-anthropic-strict-upstream.jsonl",
    ))
    .expect("066 captures parse");
    assert_eq!(records.len(), 5, "066 must retain five capture runs");
    for (line, _) in &records {
        let capture: serde_json::Value = serde_json::from_str(line).expect("066 capture record");
        assert_eq!(
            capture["path"], "/v1/chat/completions",
            "066 Messages ingress must target OpenAI Chat"
        );
        let v = tool_strict_forwarded(line, "strict_probe", FunctionToolFormat::OpenAiChat);
        assert!(
            matches!(v, Verdict::Violation(_)),
            "Switchyard Anthropic ingress must be caught dropping strict: {v:?}"
        );
    }
    let results: serde_json::Value =
        serde_json::from_str(&fixture("transcripts/066/switchyard-strict-results.json"))
            .expect("066 client results parse");
    let rows = results.as_array().expect("066 client results array");
    assert_eq!(rows.len(), 5, "066 must retain five client results");
    assert!(
        rows.iter().all(|row| {
            row["client_request"]["path"] == "/v1/messages"
                && row["client_request"]["tool_strict"] == true
                && row["upstream_tool"]["function_strict"].is_null()
                && row["client_response"]["http_status"] == 200
        }),
        "066 must remain a silent HTTP 200 loss"
    );
}

#[test]
fn switchyard_openai_route_keeps_tool_strictness() {
    let records = capture_records(&fixture(
        "transcripts/066/switchyard-openai-strict-control.jsonl",
    ))
    .expect("066 control captures parse");
    assert_eq!(
        records.len(),
        5,
        "066 control must retain five capture runs"
    );
    for (line, _) in &records {
        let capture: serde_json::Value = serde_json::from_str(line).expect("066 control record");
        assert_eq!(
            capture["path"], "/v1/chat/completions",
            "066 OpenAI control must target OpenAI Chat"
        );
        let v = tool_strict_forwarded(line, "strict_probe", FunctionToolFormat::OpenAiChat);
        assert_eq!(
            v,
            Verdict::Conformant,
            "Switchyard OpenAI ingress must keep function strictness: {v:?}"
        );
    }
}

// ---- bug 065: Switchyard demotes Responses instruction messages ----

#[test]
fn switchyard_responses_instruction_loss() {
    let records = capture_records(&fixture(
        "transcripts/065/responses-instruction-loss-upstream.jsonl",
    ))
    .expect("065 captures parse");
    assert_eq!(records.len(), 5);
    let expected = [
        ("system", "SYSTEM-INSTRUCTION"),
        ("developer", "DEVELOPER-INSTRUCTION"),
        ("user", "USER-INPUT"),
    ];
    for (line, _) in &records {
        assert!(matches!(
            instruction_messages_preserved(line, &expected),
            Verdict::Violation(_)
        ));
    }
    let results: serde_json::Value = serde_json::from_str(&fixture(
        "transcripts/065/responses-instruction-loss-results.json",
    ))
    .expect("065 client results parse");
    assert!(results.as_array().is_some_and(|rows| rows.len() == 5
        && rows.iter().all(|row| {
            row["client_request"]["input"][0]["role"] == "system"
                && row["client_request"]["input"][1]["role"] == "developer"
                && row["upstream_messages"][0]["role"] == "user"
                && row["upstream_messages"][1]["role"] == "user"
                && row["client_response"]["http_status"] == 200
                && row["client_response"]["status"] == "completed"
        })));
}

#[test]
fn switchyard_responses_string_instruction_control() {
    let v = instruction_messages_preserved(
        &fixture("transcripts/065/responses-string-instruction-control.jsonl"),
        &[
            ("system", "TOP-STRING-INSTRUCTION"),
            ("user", "USER-CONTROL"),
        ],
    );
    assert_eq!(v, Verdict::Conformant);
}

// ---- bug 068: Switchyard erases Chat refusal text on Anthropic output ----

fn issue_068_records(rel: &str) -> Vec<serde_json::Value> {
    fixture(rel)
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).unwrap_or_else(|e| panic!("{rel}: {e}")))
        .collect()
}

#[test]
fn switchyard_drops_structured_refusal_on_anthropic_translation() {
    let rel = "transcripts/068/switchyard-anthropic-refusal-loss.jsonl";
    let records = issue_068_records(rel);
    assert_eq!(records.len(), 5, "{rel} must remain the five-run capture");

    for (index, record) in records.iter().enumerate() {
        assert_eq!(record["request"]["path"], "/v1/chat/completions");
        assert!(
            record.pointer("/request/headers").is_none(),
            "{rel} line {} must not retain credential-bearing headers",
            index + 1
        );

        let upstream = record["upstream_response"]["body_raw"]
            .as_str()
            .unwrap_or_else(|| panic!("{rel} line {} has no upstream body", index + 1));
        let upstream_json: serde_json::Value = serde_json::from_str(upstream)
            .unwrap_or_else(|e| panic!("{rel} line {} upstream body: {e}", index + 1));
        assert_eq!(
            upstream_json.pointer("/choices/0/message/refusal"),
            Some(&serde_json::json!("REFUSALPROBE cannot help")),
            "{rel} line {} must contain the structured upstream refusal",
            index + 1
        );

        let client = &record["client_response"];
        assert_eq!(
            client["type"],
            "message",
            "{rel} line {} must remain the Anthropic response path",
            index + 1
        );
        assert_eq!(
            client["content"],
            serde_json::json!([{"type":"text","text":""}]),
            "{rel} line {} must contain only the invented empty text block",
            index + 1
        );
        assert_eq!(
            refusal_text_preserved(upstream, &client.to_string()),
            Verdict::Violation(
                "upstream refusal text \"REFUSALPROBE cannot help\" is absent from the client response"
                    .to_string()
            ),
            "{rel} line {} must freeze refusal-text erasure",
            index + 1
        );
    }
}

#[test]
fn switchyard_openai_route_keeps_structured_refusal() {
    let rel = "transcripts/068/switchyard-openai-refusal-control.jsonl";
    let records = issue_068_records(rel);
    assert_eq!(records.len(), 5, "{rel} must remain the five-run control");

    for (index, record) in records.iter().enumerate() {
        assert_eq!(record["request"]["path"], "/v1/chat/completions");
        assert!(
            record.pointer("/request/headers").is_none(),
            "{rel} line {} must not retain credential-bearing headers",
            index + 1
        );

        let upstream = record["upstream_response"]["body_raw"]
            .as_str()
            .unwrap_or_else(|| panic!("{rel} line {} has no upstream body", index + 1));
        let upstream_json: serde_json::Value = serde_json::from_str(upstream)
            .unwrap_or_else(|e| panic!("{rel} line {} upstream body: {e}", index + 1));
        assert_eq!(
            upstream_json.pointer("/choices/0/message/refusal"),
            Some(&serde_json::json!("REFUSALPROBE cannot help")),
            "{rel} line {} must contain the control refusal",
            index + 1
        );

        let client = &record["client_response"];
        assert_eq!(
            client["object"],
            "chat.completion",
            "{rel} line {} must remain the OpenAI response path",
            index + 1
        );
        assert_eq!(
            client.pointer("/choices/0/message/refusal"),
            Some(&serde_json::json!("REFUSALPROBE cannot help")),
            "{rel} line {} must preserve the refusal for the client",
            index + 1
        );
        assert_eq!(
            refusal_text_preserved(upstream, &client.to_string()),
            Verdict::Conformant,
            "{rel} line {} must remain a passing control",
            index + 1
        );
    }
}

#[test]
fn switchyard_messages_keeps_stop_sequences() {
    let v = stop_sequence_forwarded(
        &fixture("transcripts/040/sy-stop-upstream.jsonl"),
        "STOPPROBE",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "Switchyard Anthropic ingress forwards stop: {v:?}"
    );
}

// ---- bug 042: GoModel drops Anthropic output_format ----

#[test]
fn gomodel_drops_anthropic_output_format() {
    let v = json_schema_forwarded(&fixture("transcripts/042/gm-output-format-upstream.jsonl"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "GoModel Anthropic ingress must be caught dropping output_format: {v:?}"
    );
}

#[test]
fn gomodel_openai_route_keeps_response_format() {
    let v = json_schema_forwarded(&fixture(
        "transcripts/042/gm-openai-response-format-upstream.jsonl",
    ));
    assert_eq!(
        v,
        Verdict::Conformant,
        "GoModel OpenAI route forwards response_format: {v:?}"
    );
}

// ---- bug 043: GoModel drops disable_parallel_tool_use ----

#[test]
fn gomodel_drops_disable_parallel_tool_use() {
    let v = parallel_tool_disable_preserved(&fixture("transcripts/042/gm-parallel-upstream.jsonl"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "GoModel must be caught dropping disable_parallel_tool_use: {v:?}"
    );
}

#[test]
fn gomodel_openai_route_keeps_parallel_tool_calls() {
    let v = parallel_tool_disable_preserved(&fixture(
        "transcripts/042/gm-openai-parallel-upstream.jsonl",
    ));
    assert_eq!(
        v,
        Verdict::Conformant,
        "the OpenAI route forwards parallel_tool_calls=false: {v:?}"
    );
}

// ---- bug 051: AxonHub drops Anthropic output_format ----

#[test]
fn axonhub_drops_anthropic_output_format() {
    let rel = "transcripts/051/ah-output-format-upstream.jsonl";
    let records = capture_records(&fixture(rel)).unwrap_or_else(|e| panic!("{rel}: {e}"));
    assert_eq!(
        records.len(),
        5,
        "{rel} must still be the 5/5 capture, not a single leftover line"
    );
    for (i, (line, body)) in records.iter().enumerate() {
        assert!(
            body.get("model")
                .and_then(serde_json::Value::as_str)
                .is_some(),
            "{rel} line {} must still be a chat-completions request",
            i + 1
        );
        assert!(
            body.get("messages")
                .and_then(serde_json::Value::as_array)
                .is_some(),
            "{rel} line {} must still carry messages or the drop is unobservable",
            i + 1
        );
        assert!(
            body.get("response_format").is_none() && body.get("json_schema").is_none(),
            "{rel} line {} must still be the dropped form",
            i + 1
        );
        assert_eq!(
            json_schema_forwarded(line),
            Verdict::Violation(JSON_SCHEMA_ABSENT.into()),
            "{rel} line {} must be the 051 drop, not a parse error",
            i + 1
        );
    }
}

#[test]
fn axonhub_openai_route_keeps_response_format() {
    let rel = "transcripts/051/ah-chat-format-upstream.jsonl";
    let records = capture_records(&fixture(rel)).unwrap_or_else(|e| panic!("{rel}: {e}"));
    assert_eq!(
        records.len(),
        5,
        "{rel} must still be the 5/5 capture, not a single leftover line"
    );
    for (i, (line, body)) in records.iter().enumerate() {
        assert!(
            body.get("response_format")
                .and_then(|rf| rf.get("json_schema"))
                .is_some(),
            "{rel} line {} must still carry response_format.json_schema",
            i + 1
        );
        assert_eq!(
            json_schema_forwarded(line),
            Verdict::Conformant,
            "AxonHub OpenAI route line {} forwards response_format: {:?}",
            i + 1,
            json_schema_forwarded(line)
        );
        // Conformant alone is vacuous: the checker also returns it when the
        // schema is present. Strip the field and require JSON_SCHEMA_ABSENT
        // so a fixture that lost its schema fails instead of a silent
        // false green.
        let mut flipped = body.clone();
        flipped.as_object_mut().unwrap().remove("response_format");
        let rec = serde_json::json!({"body": flipped});
        assert_eq!(
            json_schema_forwarded(&rec.to_string()),
            Verdict::Violation(JSON_SCHEMA_ABSENT.into()),
            "control line {} is vacuous: fixture carries no json_schema field for the checker to judge",
            i + 1
        );
    }
}

#[test]
fn bifrost_drops_thinking_history() {
    let v = thinking_text_forwarded(
        &fixture("transcripts/033/upstream-request.jsonl"),
        "THINKPROBE",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Bifrost must be caught dropping the assistant thinking block: {v:?}"
    );
}

#[test]
fn bifrost_thinking_is_dropped_not_leaked() {
    // Which failure mode matters: Switchyard drops thinking, LiteLLM leaks it as
    // visible text. Bifrost drops it, so the leak checker is conformant here.
    let v = thinking_not_leaked_as_visible_text(
        &fixture("transcripts/033/upstream-request.jsonl"),
        "THINKPROBE",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "thinking is dropped, not leaked into visible text: {v:?}"
    );
}

/// Read one string field out of a frozen JSON transcript.
fn json_str(rel: &str, pointer: &str) -> String {
    let body: serde_json::Value =
        serde_json::from_str(&fixture(rel)).unwrap_or_else(|e| panic!("{rel} is not JSON: {e}"));
    body.pointer(pointer)
        .and_then(serde_json::Value::as_str)
        .unwrap_or_else(|| panic!("{rel} has no string at {pointer}"))
        .to_string()
}

#[test]
fn bifrost_erases_content_filter_to_end_turn() {
    // Both sides come off the frozen transcripts: the OpenAI-route control records
    // what the upstream reported, the Anthropic transcript what the client was told.
    // Nothing here is hardcoded, so mutating either fixture fails this test.
    let upstream_finish = json_str(
        "transcripts/034/control-openai-response.json",
        "/choices/0/finish_reason",
    );
    let client_stop = json_str("transcripts/034/anthropic-response.json", "/stop_reason");
    assert_eq!(
        upstream_finish, "content_filter",
        "the upstream transcript must still be the filtered turn"
    );
    let v = content_filter_preserved(&upstream_finish, &client_stop);
    assert!(
        matches!(v, Verdict::Violation(_)),
        "upstream {upstream_finish:?} delivered as {client_stop:?} must be caught: {v:?}"
    );
}

#[test]
fn bifrost_openai_route_keeps_content_filter() {
    // Control: the same gateway, same upstream turn, on its OpenAI route still
    // reports content_filter. The signal exists; only the Anthropic mapping loses it.
    let body: serde_json::Value =
        serde_json::from_str(&fixture("transcripts/034/control-openai-response.json"))
            .expect("control fixture is valid JSON");
    let finish = body
        .pointer("/choices/0/finish_reason")
        .and_then(serde_json::Value::as_str);
    assert_eq!(
        finish,
        Some("content_filter"),
        "the OpenAI route must still report content_filter on the same turn"
    );
    // A route that passes the signal through must not be flagged.
    assert_eq!(
        content_filter_preserved(finish.unwrap_or_default(), finish.unwrap_or_default()),
        Verdict::Conformant,
        "the OpenAI route preserves the signal and must read conformant"
    );
}

#[test]
fn bifrost_reports_truncation_as_end_turn() {
    // Both halves are frozen: the upstream that truncated, and the client body.
    let v = truncation_preserved(
        &fixture("transcripts/035/upstream-response.json"),
        &fixture("transcripts/035/anthropic-response.json"),
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "a truncated turn reported as end_turn must be caught: {v:?}"
    );
}

#[test]
fn bifrost_untruncated_turn_reported_as_end_turn_is_fine() {
    // The control the invariant needs. This client body carries the SAME `end_turn`
    // as the violation above; only the upstream differs. Conformant here is what
    // proves the checker judges truncation rather than flagging every end_turn.
    assert_eq!(
        json_str(
            "transcripts/035/control-anthropic-response.json",
            "/stop_reason"
        ),
        "end_turn",
        "the control must itself be an end_turn body, or it proves nothing"
    );
    let v = truncation_preserved(
        &fixture("transcripts/035/control-upstream-response.json"),
        &fixture("transcripts/035/control-anthropic-response.json"),
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "an untruncated turn reported as end_turn is correct: {v:?}"
    );
}

#[test]
fn bifrost_drops_refusal_content_entirely() {
    let v = response_content_not_empty(&fixture("transcripts/036/anthropic-response.json"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "an emptied refusal turn must be caught: {v:?}"
    );
}

#[test]
fn bifrost_plain_turn_keeps_content() {
    // Control: an ordinary turn through the same route keeps its blocks, so the
    // empty array above is not simply how this gateway answers.
    let v = response_content_not_empty(&fixture("transcripts/036/control-plain-response.json"));
    assert_eq!(
        v,
        Verdict::Conformant,
        "a plain turn keeps its content blocks: {v:?}"
    );
}

// ---- bug 067: LiteLLM erases Responses refusal text on Anthropic output ----

fn issue_067_records(rel: &str) -> Vec<serde_json::Value> {
    fixture(rel)
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).unwrap_or_else(|e| panic!("{rel}: {e}")))
        .collect()
}

#[test]
fn litellm_drops_structured_refusal_on_anthropic_translation() {
    let rel = "transcripts/067/litellm-anthropic-refusal-loss.jsonl";
    let records = issue_067_records(rel);
    assert_eq!(records.len(), 5, "{rel} must remain the five-run capture");

    for (index, record) in records.iter().enumerate() {
        assert_eq!(record["request"]["path"], "/v1/responses");
        assert_eq!(record["request"]["user_agent"], "litellm/1.99.0");
        assert!(
            record.pointer("/request/headers").is_none(),
            "{rel} line {} must not retain credential-bearing headers",
            index + 1
        );

        let upstream = record["upstream_response"]["body_raw"]
            .as_str()
            .unwrap_or_else(|| panic!("{rel} line {} has no upstream body", index + 1));
        let upstream_json: serde_json::Value = serde_json::from_str(upstream)
            .unwrap_or_else(|e| panic!("{rel} line {} upstream body: {e}", index + 1));
        assert_eq!(
            upstream_json.pointer("/output/0/content/0/refusal"),
            Some(&serde_json::json!("REFUSALPROBE cannot help")),
            "{rel} line {} must contain the structured upstream refusal",
            index + 1
        );

        let client = &record["client_response"];
        assert!(
            client["content"].as_array().is_some_and(Vec::is_empty),
            "{rel} line {} must contain the observed empty Anthropic content",
            index + 1
        );
        let client_json = client.to_string();
        assert!(matches!(
            response_content_not_empty(&client_json),
            Verdict::Violation(_)
        ));
        assert_eq!(
            refusal_text_preserved(upstream, &client_json),
            Verdict::Violation(
                "upstream refusal text \"REFUSALPROBE cannot help\" is absent from the client response"
                    .to_string()
            ),
            "{rel} line {} must freeze refusal-text erasure",
            index + 1
        );
    }
}

#[test]
fn litellm_openai_route_keeps_structured_refusal() {
    let rel = "transcripts/067/litellm-openai-refusal-control.jsonl";
    let records = issue_067_records(rel);
    assert_eq!(records.len(), 5, "{rel} must remain the five-run control");

    for (index, record) in records.iter().enumerate() {
        assert_eq!(record["request"]["path"], "/v1/chat/completions");
        assert!(
            record.pointer("/request/headers").is_none(),
            "{rel} line {} must not retain credential-bearing headers",
            index + 1
        );

        let upstream = record["upstream_response"]["body_raw"]
            .as_str()
            .unwrap_or_else(|| panic!("{rel} line {} has no upstream body", index + 1));
        let upstream_json: serde_json::Value = serde_json::from_str(upstream)
            .unwrap_or_else(|e| panic!("{rel} line {} upstream body: {e}", index + 1));
        assert_eq!(
            upstream_json.pointer("/choices/0/message/refusal"),
            Some(&serde_json::json!("REFUSALPROBE cannot help")),
            "{rel} line {} must contain the control refusal",
            index + 1
        );

        let client = &record["client_response"];
        assert_eq!(
            client.pointer("/choices/0/message/provider_specific_fields/refusal"),
            Some(&serde_json::json!("REFUSALPROBE cannot help")),
            "{rel} line {} must preserve the refusal for the client",
            index + 1
        );
        assert_eq!(
            refusal_text_preserved(upstream, &client.to_string()),
            Verdict::Conformant,
            "{rel} line {} must remain a passing control",
            index + 1
        );
    }
}

#[test]
fn bifrost_does_not_restore_sanitized_toolcall_id() {
    let v = toolcall_id_restored_upstream(
        &fixture("transcripts/037/upstream-request-turn2.jsonl"),
        "call/with+punct=and.dots:1",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "the sanitized id must be caught going back upstream unrestored: {v:?}"
    );
}

#[test]
fn bifrost_sanitized_id_is_charset_clean_for_the_client() {
    // The rewrite itself is correct and worth keeping: the id handed to the client
    // satisfies the tool-call id contract that the raw upstream id violated. Both
    // ids are read from the frozen roundtrip, so a gateway that stopped sanitizing
    // (or emitted a different client id) fails here instead of passing on literals.
    let original = json_str("transcripts/037/roundtrip.json", "/upstream_original_id");
    let client = json_str("transcripts/037/roundtrip.json", "/client_received_id");
    assert!(
        !id_conforms(&original),
        "the raw upstream id {original:?} should violate the id contract"
    );
    assert!(
        id_conforms(&client),
        "the id handed to the client {client:?} must satisfy the id contract"
    );
    assert_ne!(original, client, "the rewrite must actually change the id");
}

// ---- bug 045: Switchyard invents an empty text block on non-stream tool calls ----

fn anthropic_messages_content(rel: &str) -> Vec<serde_json::Value> {
    let body: serde_json::Value =
        serde_json::from_str(&fixture(rel)).unwrap_or_else(|e| panic!("{rel} is not JSON: {e}"));
    let content = body
        .get("content")
        .and_then(serde_json::Value::as_array)
        .cloned()
        .unwrap_or_else(|| panic!("{rel} has no content array"));
    assert!(
        content
            .iter()
            .any(|b| b.get("type").and_then(serde_json::Value::as_str) == Some("tool_use")),
        "{rel} must contain a tool_use block or the checker is vacuous"
    );
    content
}

fn content_has_empty_text(content: &[serde_json::Value]) -> bool {
    content.iter().any(|b| {
        b.get("type").and_then(serde_json::Value::as_str) == Some("text")
            && b.get("text")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("")
                .is_empty()
    })
}

fn assert_phantom_empty_text(rel: &str) {
    let content = anthropic_messages_content(rel);
    assert!(
        content_has_empty_text(&content),
        "{rel} must still carry the phantom empty text block"
    );
    assert_eq!(
        no_empty_text_alongside_tool_use(&fixture(rel)),
        Verdict::Violation(EMPTY_TEXT_ALONGSIDE_TOOL_USE.into()),
        "{rel} must be the 045 phantom, not a parse error"
    );
}

#[test]
fn switchyard_nonstrm_invents_empty_text_before_tool_use() {
    assert_phantom_empty_text("transcripts/045/phantom-empty-text.json");
}

#[test]
fn switchyard_live_gemini_nonstrm_invents_empty_text() {
    assert_phantom_empty_text("transcripts/045/gemini-nonstrm-phantom.json");
}

#[test]
fn switchyard_anthropic_passthrough_has_no_empty_text() {
    let rel = "transcripts/045/anthropic-haiku-tool-only.json";
    let content = anthropic_messages_content(rel);
    assert!(
        !content_has_empty_text(&content),
        "Haiku control must not already carry an empty text block"
    );
    let body = fixture(rel);
    assert_eq!(
        no_empty_text_alongside_tool_use(&body),
        Verdict::Conformant,
        "same-format Anthropic backend emits only tool_use"
    );
    // Conformant alone is vacuous: the checker also returns it for `{}`. Inject
    // the phantom and require the 045 reason, so a fixture that lost its
    // tool_use fails here instead of passing as a silent false green.
    let mut flipped: serde_json::Value =
        serde_json::from_str(&body).expect("Haiku control is JSON");
    flipped["content"]
        .as_array_mut()
        .unwrap()
        .insert(0, serde_json::json!({"type":"text","text":""}));
    assert_eq!(
        no_empty_text_alongside_tool_use(&flipped.to_string()),
        Verdict::Violation(EMPTY_TEXT_ALONGSIDE_TOOL_USE.into()),
        "control is vacuous: fixture carries no tool_use block for the checker to judge"
    );
}

#[test]
fn switchyard_live_gemini_stream_has_no_empty_text() {
    // The production Claude Code path. A streaming regression that started
    // emitting an empty text block would leave the non-stream tests green.
    let sse = fixture("transcripts/045/gemini-stream-clean.sse");
    assert!(
        sse.contains("\"type\":\"tool_use\""),
        "stream fixture must start a tool_use block"
    );
    assert!(
        !sse.contains("\"type\":\"text\""),
        "stream fixture must not already carry a text block"
    );
    assert_eq!(
        no_empty_text_alongside_tool_use(&sse),
        Verdict::Conformant,
        "live Gemini stream must start at tool_use with no empty text event"
    );
    let flipped = format!(
        "event: content_block_start\n\
         data: {{\"type\":\"content_block_start\",\"content_block\":{{\"type\":\"text\",\"text\":\"\"}}}}\n\n\
         {sse}"
    );
    assert_eq!(
        no_empty_text_alongside_tool_use(&flipped),
        Verdict::Violation(EMPTY_TEXT_ALONGSIDE_TOOL_USE.into()),
        "control is vacuous: stream fixture carries no tool_use block for the checker to judge"
    );
}

// ---- bugs 057-062: any-llm Messages bridge encode-side losses ----

fn assert_any_llm_records(rel: &str) -> Vec<(String, serde_json::Value)> {
    let records = capture_records(&fixture(rel)).unwrap_or_else(|e| panic!("{rel}: {e}"));
    assert_eq!(
        records.len(),
        5,
        "{rel} must still be the 5/5 capture, not a single leftover line"
    );
    records
}

fn has_tool_role_message(body: &serde_json::Value) -> bool {
    body.get("messages")
        .and_then(serde_json::Value::as_array)
        .is_some_and(|m| {
            m.iter()
                .any(|msg| msg.get("role") == Some(&serde_json::json!("tool")))
        })
}

#[test]
fn any_llm_drops_thinking_history() {
    let rel = "transcripts/057/al-thinking-history-upstream.jsonl";
    let think_absent = format!(
        "thinking text {:?} is absent from the forwarded upstream body",
        "THINKPROBE"
    );
    for (i, (line, body)) in assert_any_llm_records(rel).iter().enumerate() {
        assert!(
            body.get("messages")
                .and_then(serde_json::Value::as_array)
                .is_some(),
            "{rel} line {} must still carry messages",
            i + 1
        );
        assert_eq!(
            thinking_text_forwarded(line, "THINKPROBE"),
            Verdict::Violation(think_absent.clone()),
            "{rel} line {} must still drop thinking history",
            i + 1
        );
    }
}

#[test]
fn any_llm_thinking_is_dropped_not_leaked() {
    let rel = "transcripts/057/al-thinking-history-upstream.jsonl";
    for (i, (line, _)) in assert_any_llm_records(rel).iter().enumerate() {
        assert_eq!(
            thinking_not_leaked_as_visible_text(line, "THINKPROBE"),
            Verdict::Conformant,
            "{rel} line {} drops thinking rather than leaking it",
            i + 1
        );
    }
}

#[test]
fn any_llm_drops_disable_parallel_tool_use() {
    let rel = "transcripts/057/al-parallel-upstream.jsonl";
    let dropped = "disable_parallel_tool_use was dropped; forwarded body has neither parallel_tool_calls=false nor disable_parallel_tool_use=true".to_string();
    for (i, (line, body)) in assert_any_llm_records(rel).iter().enumerate() {
        assert!(
            body.get("tools")
                .and_then(serde_json::Value::as_array)
                .is_some(),
            "{rel} line {} must still carry tools",
            i + 1
        );
        assert_eq!(
            parallel_tool_disable_preserved(line),
            Verdict::Violation(dropped.clone()),
            "{rel} line {} must still drop disable_parallel_tool_use",
            i + 1
        );
    }
}

#[test]
fn any_llm_completion_keeps_parallel_tool_calls() {
    let rel = "transcripts/057/al-completion-control-upstream.jsonl";
    for (i, (line, _)) in assert_any_llm_records(rel).iter().enumerate() {
        assert_eq!(
            parallel_tool_disable_preserved(line),
            Verdict::Conformant,
            "{rel} line {} forwards parallel_tool_calls: false",
            i + 1
        );
    }
}

#[test]
fn any_llm_drops_is_error_on_tool_result() {
    let rel = "transcripts/057/al-is-error-upstream.jsonl";
    let dropped =
        "is_error:true was dropped; forwarded body has no error marker on the tool result"
            .to_string();
    for (i, (line, body)) in assert_any_llm_records(rel).iter().enumerate() {
        assert!(
            has_tool_role_message(body),
            "{rel} line {} must still carry a tool result",
            i + 1
        );
        assert_eq!(
            is_error_forwarded(line),
            Verdict::Violation(dropped.clone()),
            "{rel} line {} must still drop is_error",
            i + 1
        );
    }
}

#[test]
fn any_llm_drops_image_in_tool_result() {
    let rel = "transcripts/057/al-toolresult-image-upstream.jsonl";
    let png_absent =
        "document body \"iVBORw0KGgo\" is absent from the forwarded upstream body".to_string();
    for (i, (line, body)) in assert_any_llm_records(rel).iter().enumerate() {
        assert!(
            has_tool_role_message(body),
            "{rel} line {} must still carry a tool result",
            i + 1
        );
        assert_eq!(
            document_body_forwarded(line, "iVBORw0KGgo"),
            Verdict::Violation(png_absent.clone()),
            "{rel} line {} must still drop PNG bytes",
            i + 1
        );
        assert!(
            !line.contains("image_url"),
            "{rel} line {} must not map the tool-result image to image_url",
            i + 1
        );
    }
}

#[test]
fn any_llm_user_image_control_keeps_png_bytes() {
    let rel = "transcripts/057/al-user-image-upstream.jsonl";
    for (i, (line, _)) in assert_any_llm_records(rel).iter().enumerate() {
        assert_eq!(
            document_body_forwarded(line, "iVBORw0KGgo"),
            Verdict::Conformant,
            "{rel} line {} forwards user-content PNG bytes",
            i + 1
        );
        assert!(
            line.contains("image_url"),
            "{rel} line {} must map user-content image to image_url",
            i + 1
        );
    }
}

#[test]
fn any_llm_drops_document_in_tool_result() {
    let rel = "transcripts/057/al-toolresult-document-upstream.jsonl";
    let doc_absent =
        "document body \"DOCBODY\" is absent from the forwarded upstream body".to_string();
    for (i, (line, body)) in assert_any_llm_records(rel).iter().enumerate() {
        assert!(
            has_tool_role_message(body),
            "{rel} line {} must still carry a tool result",
            i + 1
        );
        assert_eq!(
            document_body_forwarded(line, "DOCBODY"),
            Verdict::Violation(doc_absent.clone()),
            "{rel} line {} must still drop DOCBODY",
            i + 1
        );
    }
}

#[test]
fn any_llm_user_document_control_keeps_docbody() {
    let rel = "transcripts/057/al-user-document-upstream.jsonl";
    for (i, (line, _)) in assert_any_llm_records(rel).iter().enumerate() {
        assert_eq!(
            document_body_forwarded(line, "DOCBODY"),
            Verdict::Conformant,
            "{rel} line {} forwards user-content DOCBODY",
            i + 1
        );
    }
}

#[test]
fn any_llm_wrong_output_format_shape_forwards_empty_schema() {
    let rel = "transcripts/057/al-output-format-empty-schema-upstream.jsonl";
    for (i, (line, body)) in assert_any_llm_records(rel).iter().enumerate() {
        assert!(
            body.get("response_format").is_some(),
            "{rel} line {} must still carry response_format (the silent-loss trap)",
            i + 1
        );
        assert_eq!(
            json_schema_forwarded(line),
            Verdict::Conformant,
            "{rel} line {} still names json_schema on the wire",
            i + 1
        );
        assert_eq!(
            json_schema_property_forwarded(line, "city"),
            Verdict::Violation(JSON_SCHEMA_PROPERTY_ABSENT.into()),
            "{rel} line {} must still forward an empty schema shell",
            i + 1
        );
    }
}

#[test]
fn any_llm_output_config_shape_keeps_schema() {
    let rel = "transcripts/057/al-output-format-control-upstream.jsonl";
    for (i, (line, _)) in assert_any_llm_records(rel).iter().enumerate() {
        assert_eq!(
            json_schema_property_forwarded(line, "city"),
            Verdict::Conformant,
            "{rel} line {} forwards the full schema on the documented output_config path",
            i + 1
        );
    }
}

#[test]
fn any_llm_stop_sequences_honest_negative() {
    let rel = "transcripts/057/al-stop-upstream.jsonl";
    for (i, (line, _)) in assert_any_llm_records(rel).iter().enumerate() {
        assert_eq!(
            stop_sequence_forwarded(line, "STOPPROBE"),
            Verdict::Conformant,
            "{rel} line {} maps stop_sequences to stop",
            i + 1
        );
    }
}

// ---- bug 063: Switchyard follows redirects with live x-api-key / x-goog-api-key ----

fn switchyard_redirect_live_rows(prefix: &str) -> Vec<serde_json::Value> {
    let v: serde_json::Value =
        serde_json::from_str(&fixture("transcripts/063/live-real-scoreboard.json"))
            .expect("063 live-real-scoreboard.json");
    v.as_array()
        .expect("scoreboard array")
        .iter()
        .filter(|row| {
            row.get("tag")
                .and_then(|t| t.as_str())
                .is_some_and(|t| t.starts_with(prefix))
        })
        .cloned()
        .collect()
}

#[test]
fn switchyard_redirect_sink_keeps_live_anthropic_api_key() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/063/live-anth-api-key-env-sink.jsonl"),
        "REDACTED_ANTHROPIC_API_KEY",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "a 307 sink must be caught still holding the live Anthropic x-api-key: {v:?}"
    );
}

#[test]
fn switchyard_redirect_sink_keeps_live_anthropic_extra_header() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/063/live-anth-extra-header-sink.jsonl"),
        "REDACTED_ANTHROPIC_API_KEY",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "extra_headers x-api-key must also arrive on the 307 sink: {v:?}"
    );
}

#[test]
fn switchyard_redirect_sink_keeps_live_goog_extra_header() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/063/live-goog-extra-header-sink.jsonl"),
        "REDACTED_GEMINI_API_KEY",
    );
    assert!(
        matches!(v, Verdict::Violation(_)),
        "a 307 sink must be caught still holding extra_headers x-goog-api-key: {v:?}"
    );
}

#[test]
fn switchyard_redirect_sink_strips_live_openai_bearer() {
    let v = upstream_omits_header_value(
        &fixture("transcripts/063/live-openai-bearer-sink.jsonl"),
        "REDACTED_OPENAI_API_KEY",
    );
    assert_eq!(
        v,
        Verdict::Conformant,
        "the live OpenAI Bearer control must not appear on the 307 sink: {v:?}"
    );
    let origin = upstream_omits_header_value(
        &fixture("transcripts/063/live-openai-bearer-origin.jsonl"),
        "REDACTED_OPENAI_API_KEY",
    );
    assert!(
        matches!(origin, Verdict::Violation(_)),
        "the origin hop must still have been holding the live Bearer: {origin:?}"
    );
}

#[test]
fn switchyard_redirect_live_scoreboard_5_of_5() {
    for (prefix, sink_hit) in [
        ("sy_anth_api_key_env", "FULL:ANTHROPIC_API_KEY"),
        ("sy_anth_extra_header", "FULL:ANTHROPIC_API_KEY"),
        ("sy_goog_extra_header", "FULL:GEMINI_API_KEY"),
    ] {
        let rows = switchyard_redirect_live_rows(prefix);
        assert_eq!(rows.len(), 5, "{prefix} must be 5 live runs");
        for row in &rows {
            assert_eq!(row["status"], 200, "{prefix} client HTTP");
            let hits = row["sink_hits"]
                .as_array()
                .expect("sink_hits")
                .iter()
                .filter_map(|h| h.as_str())
                .collect::<Vec<_>>();
            assert!(
                hits.contains(&sink_hit),
                "{prefix} sink must contain {sink_hit}, got {hits:?}"
            );
            assert!(row["client_hits"].as_array().is_some_and(Vec::is_empty));
        }
    }
    let oa = switchyard_redirect_live_rows("sy_openai_bearer");
    assert_eq!(oa.len(), 5);
    for row in &oa {
        assert_eq!(row["status"], 200);
        assert!(row["sink_hits"].as_array().is_some_and(Vec::is_empty));
        assert_eq!(row["origin_has_authorization"], true);
        assert_eq!(row["sink_has_authorization"], false);
    }
}

// ---- bug 069: Switchyard loses refusal typing on OpenAI Responses output ----

struct Issue069Case {
    commit: &'static str,
    scenario: &'static str,
    client_path: &'static str,
    streaming: bool,
    first_exchange: usize,
}

fn issue_069_case(rel: &str) -> Issue069Case {
    let commit = if rel.contains("switchyard-main-") {
        "7a23989cbe18f1c6c67ee03684ce76bd5901a27d"
    } else if rel.contains("switchyard-pr623-") {
        "2765f46972bf89a96beb5b2158b0fc56a3a72288"
    } else {
        panic!("unexpected issue 069 target: {rel}");
    };
    let (scenario, client_path, streaming, first_exchange) =
        if rel.ends_with("responses-buffered.jsonl") {
            ("responses-buffered", "/v1/responses", false, 1)
        } else if rel.ends_with("responses-stream.jsonl") {
            ("responses-stream", "/v1/responses", true, 6)
        } else if rel.ends_with("chat-buffered-control.jsonl") {
            ("chat-buffered-control", "/v1/chat/completions", false, 11)
        } else if rel.ends_with("chat-stream-control.jsonl") {
            ("chat-stream-control", "/v1/chat/completions", true, 16)
        } else {
            panic!("unexpected issue 069 scenario: {rel}");
        };
    Issue069Case {
        commit,
        scenario,
        client_path,
        streaming,
        first_exchange,
    }
}

fn assert_issue_069_envelope(
    rel: &str,
    record: &serde_json::Value,
    index: usize,
    case: &Issue069Case,
    binary_sha: &str,
) {
    let line = index + 1;
    assert_eq!(
        record["target"]["repository"],
        "https://github.com/NVIDIA-NeMo/Switchyard"
    );
    assert_eq!(record["target"]["commit"], case.commit, "{rel} line {line}");
    assert_eq!(record["target"]["binary_version"], "0.2.0");
    assert_eq!(record["target"]["binary_sha256"], binary_sha);
    assert!(
        record["target"]["rustc_version"]
            .as_str()
            .is_some_and(|version| version.starts_with("rustc 1.96.1 ")),
        "{rel} line {line} must bind the compiler version"
    );
    assert_eq!(
        record["target"]["configuration"],
        "openai_chat backend, passthrough route, max_retries=0"
    );
    assert_eq!(record["trial"], line);
    assert_eq!(record["scenario"], case.scenario);
    assert_eq!(record["client_request"]["method"], "POST");
    assert_eq!(record["client_request"]["path"], case.client_path);
    assert_eq!(record["client_request"]["content_type"], "application/json");
    assert_eq!(
        record["upstream_exchange"]["exchange_index"],
        case.first_exchange + index
    );
    assert_eq!(record["upstream_exchange"]["method"], "POST");
    assert_eq!(record["upstream_exchange"]["path"], "/v1/chat/completions");
    assert_eq!(
        record["upstream_exchange"]["content_type"],
        "application/json"
    );
    assert_eq!(record["upstream_exchange"]["response_status"], 200);
    assert_eq!(record["client_response"]["status"], 200);
}

fn assert_issue_069_requests(rel: &str, record: &serde_json::Value, case: &Issue069Case) {
    let client_body: serde_json::Value =
        serde_json::from_str(record["client_request"]["body_raw"].as_str().unwrap()).unwrap();
    let mut expected_client = if case.client_path == "/v1/responses" {
        serde_json::json!({
            "model": "main", "input": "REFUSALPROBE trigger", "max_output_tokens": 32
        })
    } else {
        serde_json::json!({
            "model": "main",
            "messages": [{"role": "user", "content": "REFUSALPROBE trigger"}],
            "max_tokens": 32
        })
    };
    if case.streaming {
        expected_client["stream"] = serde_json::json!(true);
    }
    assert_eq!(client_body, expected_client, "{rel} client request");

    let upstream_body: serde_json::Value =
        serde_json::from_str(record["upstream_exchange"]["body_raw"].as_str().unwrap()).unwrap();
    let mut expected_upstream = serde_json::json!({
        "model": "captured-model",
        "messages": [{"role": "user", "content": "REFUSALPROBE trigger"}]
    });
    let token_field = if case.client_path == "/v1/responses" {
        "max_completion_tokens"
    } else {
        "max_tokens"
    };
    expected_upstream[token_field] = serde_json::json!(32);
    if case.streaming {
        expected_upstream["stream"] = serde_json::json!(true);
        expected_upstream["stream_options"] = serde_json::json!({"include_usage": true});
    }
    assert_eq!(upstream_body, expected_upstream, "{rel} upstream request");
}

fn assert_issue_069_upstream_response(rel: &str, record: &serde_json::Value, case: &Issue069Case) {
    let upstream_raw = record["upstream_exchange"]["response_body_raw"]
        .as_str()
        .unwrap();
    if case.streaming {
        assert_eq!(
            record["upstream_exchange"]["response_content_type"],
            "text/event-stream; charset=utf-8"
        );
        assert_eq!(
            record["client_response"]["content_type"],
            "text/event-stream"
        );
        assert!(upstream_raw.ends_with("data: [DONE]\n\n"));
        let events = upstream_raw
            .lines()
            .filter_map(|line| line.strip_prefix("data: "))
            .filter(|data| *data != "[DONE]")
            .map(|data| serde_json::from_str::<serde_json::Value>(data).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(events.len(), 3, "{rel} upstream SSE event count");
        assert!(events.iter().all(|event| {
            event["object"] == "chat.completion.chunk" && event["model"] == "captured-model"
        }));
        let refusal = events
            .iter()
            .filter_map(|event| {
                event
                    .pointer("/choices/0/delta/refusal")
                    .and_then(serde_json::Value::as_str)
            })
            .collect::<String>();
        assert_eq!(refusal, "REFUSALPROBE cannot help");
        assert_eq!(events[2]["choices"][0]["finish_reason"], "stop");
        return;
    }

    assert_eq!(
        record["upstream_exchange"]["response_content_type"],
        "application/json"
    );
    assert_eq!(
        record["client_response"]["content_type"],
        "application/json"
    );
    let upstream: serde_json::Value = serde_json::from_str(upstream_raw).unwrap();
    assert_eq!(upstream["object"], "chat.completion");
    assert_eq!(upstream["model"], "captured-model");
    assert_eq!(upstream["choices"].as_array().unwrap().len(), 1);
    assert_eq!(
        upstream["choices"][0]["message"]["content"],
        serde_json::Value::Null
    );
    assert_eq!(
        upstream["choices"][0]["message"]["refusal"],
        "REFUSALPROBE cannot help"
    );
    assert_eq!(upstream["choices"][0]["finish_reason"], "stop");
}

fn issue_069_records(rel: &str) -> Vec<serde_json::Value> {
    let records = fixture(rel)
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            serde_json::from_str::<serde_json::Value>(line).unwrap_or_else(|e| panic!("{rel}: {e}"))
        })
        .collect::<Vec<_>>();
    assert_eq!(records.len(), 5, "{rel} must remain the five-run capture");
    let case = issue_069_case(rel);
    let binary_sha = records[0]["target"]["binary_sha256"]
        .as_str()
        .unwrap_or_else(|| panic!("{rel} target binary SHA-256"));
    assert_eq!(binary_sha.len(), 64, "{rel} binary SHA-256 length");
    assert!(binary_sha.bytes().all(|byte| byte.is_ascii_hexdigit()));
    for (index, record) in records.iter().enumerate() {
        assert_issue_069_envelope(rel, record, index, &case, binary_sha);
        assert_issue_069_requests(rel, record, &case);
        assert_issue_069_upstream_response(rel, record, &case);
    }
    records
}

#[test]
fn switchyard_issue_069_expected_responses_shapes_are_conformant() {
    let buffered_upstream =
        r#"{"choices":[{"message":{"content":null,"refusal":"REFUSALPROBE cannot help"}}]}"#;
    assert_eq!(
        responses_refusal_semantics_preserved(
            buffered_upstream,
            &fixture("transcripts/069/expected-responses-buffered.json")
        ),
        Verdict::Conformant
    );

    let stream_upstream =
        "data: {\"choices\":[{\"delta\":{\"refusal\":\"REFUSALPROBE cannot help\"}}]}\n\n";
    assert_eq!(
        responses_refusal_semantics_preserved(
            stream_upstream,
            &fixture("transcripts/069/expected-responses-stream.sse")
        ),
        Verdict::Conformant
    );
}

#[test]
fn switchyard_main_drops_responses_refusal_semantics() {
    for rel in [
        "transcripts/069/switchyard-main-responses-buffered.jsonl",
        "transcripts/069/switchyard-main-responses-stream.jsonl",
    ] {
        let records = issue_069_records(rel);
        for (index, record) in records.iter().enumerate() {
            assert_eq!(record["client_request"]["path"], "/v1/responses");
            assert_eq!(record["client_response"]["status"], 200);
            assert_eq!(record["consumer"]["classified_as_refusal"], false);
            let upstream = record["upstream_exchange"]["response_body_raw"]
                .as_str()
                .unwrap_or_else(|| panic!("{rel} line {} upstream raw body", index + 1));
            let client = record["client_response"]["body_raw"]
                .as_str()
                .unwrap_or_else(|| panic!("{rel} line {} client raw body", index + 1));
            assert!(upstream.contains("REFUSALPROBE cannot help"));
            assert!(matches!(
                responses_refusal_semantics_preserved(upstream, client),
                Verdict::Violation(_)
            ));
        }
    }
}

#[test]
fn switchyard_pr623_flattens_responses_refusal_to_output_text() {
    for rel in [
        "transcripts/069/switchyard-pr623-responses-buffered.jsonl",
        "transcripts/069/switchyard-pr623-responses-stream.jsonl",
    ] {
        let records = issue_069_records(rel);
        for (index, record) in records.iter().enumerate() {
            assert_eq!(record["client_request"]["path"], "/v1/responses");
            assert_eq!(record["client_response"]["status"], 200);
            assert_eq!(record["consumer"]["classified_as_refusal"], false);
            assert_eq!(
                record["consumer"]["ordinary_output_text"],
                "REFUSALPROBE cannot help"
            );
            let upstream = record["upstream_exchange"]["response_body_raw"]
                .as_str()
                .unwrap_or_else(|| panic!("{rel} line {} upstream raw body", index + 1));
            let client = record["client_response"]["body_raw"]
                .as_str()
                .unwrap_or_else(|| panic!("{rel} line {} client raw body", index + 1));
            assert!(matches!(
                responses_refusal_semantics_preserved(upstream, client),
                Verdict::Violation(_)
            ));
            assert!(
                !client.contains("response.refusal") && !client.contains("\"type\":\"refusal\""),
                "{rel} line {} must remain semantically flattened",
                index + 1
            );
        }
    }
}

#[test]
fn switchyard_chat_route_preserves_the_same_refusal_control() {
    for rel in [
        "transcripts/069/switchyard-main-chat-buffered-control.jsonl",
        "transcripts/069/switchyard-main-chat-stream-control.jsonl",
        "transcripts/069/switchyard-pr623-chat-buffered-control.jsonl",
        "transcripts/069/switchyard-pr623-chat-stream-control.jsonl",
    ] {
        let records = issue_069_records(rel);
        for (index, record) in records.iter().enumerate() {
            assert_eq!(record["client_request"]["path"], "/v1/chat/completions");
            assert_eq!(record["upstream_exchange"]["path"], "/v1/chat/completions");
            assert_eq!(record["client_response"]["status"], 200);
            assert_eq!(record["consumer"]["classified_as_refusal"], true);
            assert_eq!(
                record["consumer"]["refusal_text"],
                "REFUSALPROBE cannot help"
            );
            assert_eq!(
                record["upstream_exchange"]["response_body_raw"],
                record["client_response"]["body_raw"],
                "{rel} line {} must preserve the same raw refusal response",
                index + 1
            );
        }
    }
}

// ---- bug 070: Switchyard drops disable_parallel_tool_use when a specific tool is forced ----

#[test]
fn switchyard_drops_specific_tool_disable_parallel() {
    let v = parallel_tool_disable_preserved(&fixture(
        "transcripts/070/switchyard-specific-tool-disable-upstream.jsonl",
    ));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "Switchyard must be caught dropping disable_parallel_tool_use with tool(name): {v:?}"
    );
    let records = capture_records(&fixture(
        "transcripts/070/switchyard-specific-tool-disable-upstream.jsonl",
    ))
    .expect("070 captures parse");
    assert_eq!(records.len(), 5, "070 must retain five capture runs");
    for (line, body) in &records {
        assert!(
            body.get("tools")
                .and_then(serde_json::Value::as_array)
                .is_some(),
            "070 line must still carry tools"
        );
        assert!(
            body.get("tool_choice")
                .and_then(|c| c.get("function"))
                .and_then(|f| f.get("name"))
                .is_some(),
            "070 line must still carry tool_choice function name"
        );
        assert!(
            body.get("parallel_tool_calls").is_none(),
            "070 line must still be the dropped form"
        );
        assert_eq!(
            parallel_tool_disable_preserved(line),
            Verdict::Violation(
                "disable_parallel_tool_use was dropped; forwarded body has neither parallel_tool_calls=false nor disable_parallel_tool_use=true"
                    .into()
            )
        );
    }
}

#[test]
fn switchyard_specific_tool_disable_parallel_control() {
    let v = parallel_tool_disable_preserved(&fixture(
        "transcripts/070/switchyard-specific-tool-disable-control-upstream.jsonl",
    ));
    assert_eq!(
        v,
        Verdict::Conformant,
        "Switchyard OpenAI route must keep parallel_tool_calls:false with tool(name): {v:?}"
    );
    let records = capture_records(&fixture(
        "transcripts/070/switchyard-specific-tool-disable-control-upstream.jsonl",
    ))
    .expect("070 control captures parse");
    assert_eq!(
        records.len(),
        5,
        "070 control must retain five capture runs"
    );
    for (line, _) in &records {
        assert_eq!(
            parallel_tool_disable_preserved(line),
            Verdict::Conformant,
            "070 control line must preserve the flag"
        );
    }
}

// ---- bug 071: LiteLLM GET /model/info returns api_base query-key credentials ----
//
// Fixtures are capture envelopes: {"request_path": ..., "status": ..., "body": ...}.
// Route identity and HTTP status are checked FIRST, so a swapped-route capture or a
// 404 body cannot pass an identical-body test; only then is the body inspected for
// the leaked credential.

const CANARY: &str = "CANARY_QUERY_KEY_IN_API_BASE";

#[test]
fn litellm_model_info_leaks_api_base_credentials() {
    let envelope = fixture("transcripts/071/model-info.json");
    assert_eq!(
        model_info_capture_identity(
            &envelope,
            &fixture("transcripts/071/model-info.http"),
            "/model/info"
        ),
        Verdict::Conformant,
        "capture must be identifiably GET /model/info with a 200 status"
    );
    let body = model_info_envelope_body(&envelope).expect("envelope has a body");
    let v = model_info_omits_api_base_secret(&body, CANARY);
    assert_eq!(
        v,
        Verdict::Violation(format!(
            "model/info litellm_params.api_base contains secret marker {CANARY:?}"
        )),
        "GET /model/info must leak the marker, not merely fail JSON validation"
    );
}

#[test]
fn litellm_model_info_v1_leaks_api_base_credentials() {
    let envelope = fixture("transcripts/071/model-info-v1.json");
    assert_eq!(
        model_info_capture_identity(
            &envelope,
            &fixture("transcripts/071/model-info-v1.http"),
            "/v1/model/info"
        ),
        Verdict::Conformant,
        "capture must be identifiably GET /v1/model/info with a 200 status"
    );
    let body = model_info_envelope_body(&envelope).expect("envelope has a body");
    let v = model_info_omits_api_base_secret(&body, CANARY);
    assert_eq!(
        v,
        Verdict::Violation(format!(
            "model/info litellm_params.api_base contains secret marker {CANARY:?}"
        )),
        "GET /v1/model/info must leak the marker, not merely fail JSON validation"
    );
}

#[test]
fn litellm_model_info_route_identity_rejects_swapped_capture() {
    // Swap the two envelopes' claimed routes: same (real) bodies, wrong identity.
    // An identical-body test alone would pass this; the identity check must not.
    let model_info = fixture("transcripts/071/model-info.json");
    let model_info_v1 = fixture("transcripts/071/model-info-v1.json");
    let wire = fixture("transcripts/071/model-info.http");
    let wire_v1 = fixture("transcripts/071/model-info-v1.http");
    assert!(
        matches!(
            model_info_capture_identity(&model_info, &wire_v1, "/model/info"),
            Verdict::Violation(_)
        ),
        "a /model/info capture must not pass as /v1/model/info"
    );
    assert!(
        matches!(
            model_info_capture_identity(&model_info_v1, &wire, "/v1/model/info"),
            Verdict::Violation(_)
        ),
        "a /v1/model/info capture must not pass as /model/info"
    );
    // A 404 body reusing the same shape as a genuine capture must not pass either.
    let not_found = r#"{"request_path":"/model/info","status":404,"body":{"detail":"Not Found"}}"#;
    assert!(matches!(
        model_info_capture_identity(not_found, &wire, "/model/info"),
        Verdict::Violation(_)
    ));
    assert!(matches!(
        model_info_capture_identity(
            &model_info,
            &wire.replace("200 OK", "404 Not Found"),
            "/model/info"
        ),
        Verdict::Violation(_)
    ));
}

#[test]
fn litellm_models_control_whole_body_omits_secrets() {
    let envelope = fixture("transcripts/071/models-control.json");
    assert_eq!(
        model_info_capture_identity(
            &envelope,
            &fixture("transcripts/071/models-control.http"),
            "/v1/models"
        ),
        Verdict::Conformant,
        "control capture must be identifiably GET /v1/models with a 200 status"
    );
    let body = model_info_envelope_body(&envelope).expect("envelope has a body");
    let parsed: Value = serde_json::from_str(&body).expect("control is JSON");
    assert!(
        parsed
            .get("data")
            .and_then(Value::as_array)
            .is_some_and(|data| !data.is_empty()),
        "models control must contain a nonempty model list"
    );
    assert_eq!(
        response_omits_secret(&body, "CANARY"),
        Verdict::Conformant,
        "/v1/models whole-body control must not contain the api_base canary anywhere"
    );
}

#[test]
fn litellm_liveliness_control_whole_body_omits_secrets() {
    let envelope = fixture("transcripts/071/liveliness-control.json");
    assert_eq!(
        model_info_capture_identity(
            &envelope,
            &fixture("transcripts/071/liveliness-control.http"),
            "/health/liveliness"
        ),
        Verdict::Conformant,
        "control capture must be identifiably GET /health/liveliness with a 200 status"
    );
    let body = model_info_envelope_body(&envelope).expect("envelope has a body");
    assert_eq!(
        serde_json::from_str::<Value>(&body).expect("liveliness is JSON"),
        Value::String("I'm alive!".to_string())
    );
    assert_eq!(
        response_omits_secret(&body, "CANARY"),
        Verdict::Conformant,
        "/health/liveliness whole-body control must not contain the api_base canary"
    );
}

#[test]
fn litellm_models_control_catches_canary_outside_api_base_path() {
    // Negative coverage: a canary placed outside data[].litellm_params.api_base
    // (where the narrow model_info checker looks) must still be caught by the
    // whole-body response_omits_secret control used for /v1/models.
    let leaked_elsewhere =
        r#"{"object":"list","data":[{"id":"leaked-CANARY_QUERY_KEY_IN_API_BASE"}]}"#;
    assert!(
        matches!(
            response_omits_secret(leaked_elsewhere, CANARY),
            Verdict::Violation(_)
        ),
        "the whole-body control must still catch a canary anywhere in the response"
    );
}

#[test]
fn litellm_current_release_model_info_and_controls() {
    for (name, path, leaks) in [
        ("model-info", "/model/info", true),
        ("model-info-v1", "/v1/model/info", true),
        ("models-control", "/v1/models", false),
        ("liveliness-control", "/health/liveliness", false),
    ] {
        let prefix = format!("transcripts/071/current-1.100.0/{name}");
        let envelope = fixture(&format!("{prefix}.json"));
        assert_eq!(
            model_info_capture_identity(&envelope, &fixture(&format!("{prefix}.http")), path),
            Verdict::Conformant,
        );
        let body = model_info_envelope_body(&envelope).expect("capture has a body");
        if leaks {
            assert_eq!(
                model_info_omits_api_base_secret(&body, CANARY),
                Verdict::Violation(format!(
                    "model/info litellm_params.api_base contains secret marker {CANARY:?}"
                )),
            );
        } else {
            assert_eq!(response_omits_secret(&body, "CANARY"), Verdict::Conformant);
        }
    }
}

// ---- bug 072: Bifrost Anthropic tool_choice "any" dialect leak on OpenAI wire ----

fn issue_072_exchanges(mode: &str, profile: &str, case: &str) -> Vec<serde_json::Value> {
    let path = format!("transcripts/072/{mode}/{profile}-{case}.jsonl");
    let records: Vec<serde_json::Value> = fixture(&path)
        .lines()
        .map(|line| serde_json::from_str(line).expect("raw exchange JSON"))
        .collect();
    assert_eq!(records.len(), 5, "{path}: require every trial");
    let upstream_path = if profile == "responses" {
        "/v1/responses"
    } else {
        "/v1/chat/completions"
    };
    for (index, record) in records.iter().enumerate() {
        assert_eq!(record["run"], index + 1, "{path}: run identity");
        assert_eq!(record["profile"], profile);
        assert_eq!(record["case"], case);
        assert_eq!(
            record["mode"],
            if mode == "live" {
                "live-openai"
            } else {
                "keyless-capture"
            }
        );
        assert_eq!(record["path"], upstream_path);
        assert_eq!(record["forwarded_request"]["path"], upstream_path);
        let raw: serde_json::Value =
            serde_json::from_str(record["forwarded_request"]["body_raw"].as_str().unwrap())
                .unwrap();
        assert_eq!(record["body"], raw, "{path}: raw wire is authoritative");
        assert_eq!(raw["model"], "gpt-4o");
        let rejected = mode == "live" && case == "any";
        let status = if rejected { 400 } else { 200 };
        for stage in [
            "client_request",
            "forwarded_request",
            "upstream_response",
            "client_response",
        ] {
            let _: serde_json::Value =
                serde_json::from_str(record[stage]["body_raw"].as_str().unwrap()).unwrap();
            for key in record[stage]["headers"].as_object().unwrap().keys() {
                assert!(matches!(
                    key.as_str(),
                    "content-type" | "date" | "x-request-id"
                ));
            }
        }
        assert_eq!(record["client_response"]["status"], status);
        assert_eq!(record["upstream_response"]["status"], status);
        if case == "any" || case == "named" {
            assert_eq!(record["client_request"]["path"], "/anthropic/v1/messages");
            let input: serde_json::Value =
                serde_json::from_str(record["client_request"]["body_raw"].as_str().unwrap())
                    .unwrap();
            let expected = if case == "any" {
                serde_json::json!({"type":"any"})
            } else {
                serde_json::json!({"type":"tool", "name":"get_weather"})
            };
            assert_eq!(input["tool_choice"], expected);
            assert_eq!(record["consumer"]["tool_dispatches"], i32::from(!rejected));
        }
        if rejected {
            let error: serde_json::Value =
                serde_json::from_str(record["upstream_response"]["body_raw"].as_str().unwrap())
                    .unwrap();
            assert_eq!(error["error"]["param"], "tool_choice");
            assert_eq!(error["error"]["code"], "invalid_value");
            assert!(error["error"]["message"]
                .as_str()
                .unwrap()
                .contains("'any'"));
            assert_eq!(record["consumer"]["outcome"], "BadRequestError");
        }
    }
    records
}

#[test]
fn bifrost_leaks_anthropic_tool_choice_any() {
    for mode in ["local", "live"] {
        for profile in ["responses", "chat"] {
            for record in issue_072_exchanges(mode, profile, "any") {
                assert_eq!(record["body"]["tool_choice"], "any");
                assert!(matches!(
                    anthropic_tool_choice_any_mapped_to_required(&record.to_string()),
                    Verdict::Violation(_)
                ));
            }
        }
    }
}

#[test]
fn bifrost_openai_responses_keeps_tool_choice_required() {
    for mode in ["local", "live"] {
        for case in ["required", "direct-required"] {
            for record in issue_072_exchanges(mode, "responses", case) {
                assert_eq!(
                    anthropic_tool_choice_any_mapped_to_required(&record.to_string()),
                    Verdict::Conformant
                );
            }
        }
    }
}

#[test]
fn bifrost_openai_chat_keeps_tool_choice_required() {
    for mode in ["local", "live"] {
        for case in ["required", "direct-required"] {
            for record in issue_072_exchanges(mode, "chat", case) {
                assert_eq!(
                    anthropic_tool_choice_any_mapped_to_required(&record.to_string()),
                    Verdict::Conformant
                );
            }
        }
    }
}

#[test]
fn bifrost_named_and_auto_tool_choice_controls() {
    for mode in ["local", "live"] {
        for profile in ["responses", "chat"] {
            for record in issue_072_exchanges(mode, profile, "named") {
                assert_eq!(record["body"]["tool_choice"]["type"], "function");
                let field = if profile == "responses" {
                    "/body/tool_choice/name"
                } else {
                    "/body/tool_choice/function/name"
                };
                assert_eq!(record.pointer(field).unwrap(), "get_weather");
            }
            if mode == "local" {
                for record in issue_072_exchanges(mode, profile, "auto") {
                    assert_eq!(record["body"]["tool_choice"], "auto");
                }
            }
        }
    }
}

// ---- bug 074: LiteLLM MCP auto-execution splices response lifecycles ----

fn validate_issue_074_mode(
    record: &Value,
    request: &Value,
    mode: &str,
    index: usize,
) -> Result<(), String> {
    let upstream_count = record
        .get("upstream_exchanges")
        .and_then(Value::as_array)
        .map(Vec::len);
    let call_count = record
        .get("mcp_calls")
        .and_then(Value::as_array)
        .map(Vec::len);
    let error_type = record
        .pointer("/consumer/error/type")
        .and_then(Value::as_str);
    match mode {
        "trigger" => {
            if upstream_count != Some(2)
                || call_count != Some(1)
                || error_type != Some("AssertionError")
                || record.pointer("/consumer/final_response") != Some(&Value::Null)
            {
                return Err(format!("record {index} does not prove trigger impact"));
            }
        }
        "approval" => {
            if upstream_count != Some(1)
                || call_count != Some(0)
                || !record
                    .pointer("/consumer/error")
                    .is_some_and(Value::is_null)
                || request
                    .pointer("/tools/0/require_approval")
                    .and_then(Value::as_str)
                    != Some("always")
            {
                return Err(format!("record {index} is not the approval control"));
            }
        }
        "no-tool" => {
            if upstream_count != Some(1)
                || call_count != Some(0)
                || !record
                    .pointer("/consumer/error")
                    .is_some_and(Value::is_null)
                || request.get("tools").is_some()
            {
                return Err(format!("record {index} is not the no-tool control"));
            }
        }
        _ => return Err(format!("unknown mode {mode}")),
    }
    Ok(())
}

fn issue_074_records(
    jsonl: &str,
    version: &str,
    mode: &str,
    should_conform: bool,
) -> Result<Vec<Value>, String> {
    let records = jsonl
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str::<Value>(line).map_err(|error| error.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    if records.len() != 5 {
        return Err(format!("expected five records, got {}", records.len()));
    }
    for (index, record) in records.iter().enumerate() {
        if record.pointer("/target/project").and_then(Value::as_str) != Some("BerriAI/litellm")
            || record.pointer("/target/version").and_then(Value::as_str) != Some(version)
            || record.get("mode").and_then(Value::as_str) != Some(mode)
            || record.get("trial").and_then(Value::as_u64) != Some((index + 1) as u64)
        {
            return Err(format!("record {} has wrong provenance", index + 1));
        }
        if record
            .pointer("/client_request/path")
            .and_then(Value::as_str)
            != Some("/v1/responses")
            || record
                .pointer("/client_response/status")
                .and_then(Value::as_u64)
                != Some(200)
        {
            return Err(format!(
                "record {} is not a successful public route",
                index + 1
            ));
        }
        let request_raw = record
            .pointer("/client_request/body_raw")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("record {} has no raw request", index + 1))?;
        let request: Value =
            serde_json::from_str(request_raw).map_err(|error| error.to_string())?;
        if request.get("model").and_then(Value::as_str) != Some("mock")
            || request.get("stream").and_then(Value::as_bool) != Some(true)
        {
            return Err(format!("record {} has wrong request shape", index + 1));
        }
        let response_raw = record
            .pointer("/client_response/body_raw")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("record {} has no raw response", index + 1))?;
        let verdict = responses_single_lifecycle(response_raw);
        let verdict_matches = matches!(verdict, Verdict::Conformant) == should_conform;
        if !verdict_matches {
            return Err(format!(
                "record {} lifecycle verdict is {verdict:?}, expected conformant={should_conform}",
                index + 1,
            ));
        }
        validate_issue_074_mode(record, &request, mode, index + 1)?;
    }
    Ok(records)
}

#[test]
fn litellm_mcp_auto_execution_splices_response_lifecycles() {
    for version in ["1.99.0", "1.100.0"] {
        let path = format!("transcripts/074/{version}/trigger.jsonl");
        issue_074_records(&fixture(&path), version, "trigger", false)
            .unwrap_or_else(|error| panic!("{path}: {error}"));
    }
}

#[test]
fn litellm_mcp_stream_controls_have_one_lifecycle() {
    for version in ["1.99.0", "1.100.0"] {
        for mode in ["approval", "no-tool"] {
            let path = format!("transcripts/074/{version}/{mode}.jsonl");
            issue_074_records(&fixture(&path), version, mode, true)
                .unwrap_or_else(|error| panic!("{path}: {error}"));
        }
    }
}

#[test]
fn issue_074_checks_later_trials_and_rejects_malformed_evidence() {
    let original = fixture("transcripts/074/1.100.0/approval.jsonl");
    let mut records = original
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();
    records[4]["client_response"]["body_raw"] = Value::String("data: not-json\n\n".into());
    let mutated = records
        .iter()
        .map(Value::to_string)
        .collect::<Vec<_>>()
        .join("\n");
    assert!(issue_074_records(&mutated, "1.100.0", "approval", true).is_err());

    let malformed = format!("{}\n{{", original.trim_end());
    assert!(issue_074_records(&malformed, "1.100.0", "approval", true).is_err());
}

// ---- bug 075: Bifrost Gemini chat-completions drops inlineData image/audio parts ----

fn issue_075_records(path: &str) -> Vec<Value> {
    let records: Vec<Value> = fixture(path)
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|line| serde_json::from_str(line).expect("raw exchange JSON"))
        .collect();
    assert!(!records.is_empty(), "{path}: expected at least one run");
    for (index, record) in records.iter().enumerate() {
        assert_eq!(record["run"], (index + 1) as u64, "{path}: run identity");
        // Every stage must carry a well-formed client_request/client_response
        // pair; body_raw is the wire text, never a reconstructed summary.
        for stage in ["client_request", "client_response"] {
            assert!(
                record[stage]["body_raw"].as_str().is_some(),
                "{path} record {}: missing {stage}.body_raw",
                index + 1
            );
        }
    }
    records
}

#[test]
fn bifrost_gemini_chat_completions_drops_inline_image() {
    for record in issue_075_records("transcripts/075/live/chat-nonstream.jsonl") {
        assert_eq!(record["path"], "/v1/chat/completions");
        assert!(
            record["image_present_in_upstream"]
                .as_bool()
                .unwrap_or(false),
            "control precondition: Gemini must have generated an image this run"
        );
        let upstream = record["upstream_response"]
            .as_str()
            .expect("upstream_response must be a raw JSON string");
        let client_body_raw = record["client_response"]["body_raw"]
            .as_str()
            .expect("client_response.body_raw must be present");
        assert!(
            matches!(
                gemini_inline_media_preserved_in_chat_response(upstream, client_body_raw),
                Verdict::Violation(_)
            ),
            "expected the recorded exchange to reproduce the inlineData drop"
        );
    }
}

#[test]
fn bifrost_gemini_chat_completions_stream_drops_inline_image() {
    for record in issue_075_records("transcripts/075/live/chat-stream.jsonl") {
        assert!(
            record["image_present_in_upstream_chunk"]
                .as_bool()
                .unwrap_or(false),
            "control precondition: a streamed chunk must have carried an inlineData part"
        );
        let sse = record["client_response"]["body_raw"]
            .as_str()
            .expect("client_response.body_raw must be present");
        assert!(
            matches!(
                gemini_inline_media_preserved_in_chat_stream(sse),
                Verdict::Violation(_)
            ),
            "expected the recorded stream to reproduce the inlineData drop"
        );
    }
}

#[test]
fn bifrost_gemini_responses_control_preserves_inline_image() {
    // Control: same Bifrost binary, same model, same prompt, different route.
    // The Responses API dialect converter does handle inlineData, so the
    // image must be present here even though chat completions drops it.
    for record in issue_075_records("transcripts/075/live/responses-control.jsonl") {
        assert_eq!(record["path"], "/v1/responses");
        assert!(
            record["image_content_block_present"]
                .as_bool()
                .unwrap_or(false),
            "responses control did not surface an image content block"
        );
        let body: Value =
            serde_json::from_str(record["client_response"]["body_raw"].as_str().unwrap())
                .expect("client_response.body_raw must be valid JSON");
        let has_image_block = body
            .get("output")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|item| item.get("content"))
            .filter_map(Value::as_array)
            .flatten()
            .any(|block| {
                matches!(
                    block.get("type").and_then(Value::as_str),
                    Some("input_image" | "output_image" | "image")
                )
            });
        assert!(
            has_image_block,
            "raw wire body has no image content block despite image_content_block_present=true"
        );
    }
}

#[test]
fn bifrost_gemini_direct_control_returns_inline_image() {
    // Control: bypasses Bifrost entirely, proving the image comes from Gemini
    // itself and is not an artifact of the capture harness.
    for record in issue_075_records("transcripts/075/live/direct-gemini-control.jsonl") {
        assert!(
            record["image_present"].as_bool().unwrap_or(false),
            "direct Gemini control did not report an image"
        );
        let body: Value =
            serde_json::from_str(record["client_response"]["body_raw"].as_str().unwrap())
                .expect("client_response.body_raw must be valid JSON");
        // This is Gemini's own generateContent wire shape, not an OpenAI chat
        // completions body, so check for the inlineData part directly rather
        // than reusing the OpenAI-shaped checker above.
        let has_inline_data = body
            .pointer("/candidates/0/content/parts")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .any(|part| {
                part.pointer("/inlineData/data")
                    .and_then(Value::as_str)
                    .is_some_and(|d| !d.is_empty())
            });
        assert!(
            has_inline_data,
            "direct Gemini wire response has no inlineData part"
        );
    }
}

// ---- bug 076: LiteLLM vector store lets an internal user export a server credential ----

fn issue_076_upstream_requests(path: &str, store_id: &str) -> Vec<String> {
    let records: Vec<Value> = fixture(path)
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).expect("raw exchange JSON"))
        .collect();
    assert_eq!(records.len(), 3, "{path}: expected three trials");
    records
        .iter()
        .enumerate()
        .map(|(index, record)| {
            assert_eq!(
                record.get("trial").and_then(Value::as_u64),
                Some((index + 1) as u64),
                "{path}: trial identity"
            );
            let client_request = record
                .get("client_request_raw")
                .and_then(Value::as_str)
                .expect("client request must be literal HTTP");
            let expected_start = format!("POST /v1/vector_stores/{store_id}/search HTTP/1.1\r\n");
            assert!(
                client_request.starts_with(&expected_start),
                "{path}: wrong public entry point"
            );
            assert!(
                record
                    .get("client_response_raw")
                    .and_then(Value::as_str)
                    .is_some_and(|response| response.starts_with("HTTP/1.1 200 OK\r\n")),
                "{path}: search did not return HTTP 200"
            );
            let upstream_request = record
                .get("upstream_request_raw")
                .and_then(Value::as_str)
                .expect("upstream request must be literal HTTP");
            assert!(
                upstream_request.starts_with(&expected_start),
                "{path}: wrong upstream request target"
            );
            upstream_request.to_string()
        })
        .collect()
}

#[test]
fn litellm_vector_store_exposes_server_credential_to_caller_endpoint() {
    for request in
        issue_076_upstream_requests("transcripts/076/live/exploit.jsonl", "kairo-exploit-store")
    {
        assert!(matches!(
            outbound_request_omits_secret(&request, "[SERVER_PROVIDER_CREDENTIAL]"),
            Verdict::Violation(_)
        ));
        for absent in ["[CALLER_CONTROL_CREDENTIAL]", "[INTERNAL_USER_KEY]"] {
            assert_eq!(
                outbound_request_omits_secret(&request, absent),
                Verdict::Conformant
            );
        }
    }
}

#[test]
fn litellm_vector_store_caller_owned_key_control_omits_server_credential() {
    for request in
        issue_076_upstream_requests("transcripts/076/live/control.jsonl", "kairo-control-store")
    {
        assert_eq!(
            outbound_request_omits_secret(&request, "[SERVER_PROVIDER_CREDENTIAL]"),
            Verdict::Conformant
        );
        assert!(matches!(
            outbound_request_omits_secret(&request, "[CALLER_CONTROL_CREDENTIAL]"),
            Verdict::Violation(_)
        ));
        assert_eq!(
            outbound_request_omits_secret(&request, "[INTERNAL_USER_KEY]"),
            Verdict::Conformant
        );
    }
}

// ---- bug 078: same filtered turn, opposite safety verdicts by transport ----
//
// Bifrost 1.8.6 consults IncompleteDetails on the streaming Anthropic egress
// (content_filter -> refusal, correct) but ignores it on the non-streaming
// egress (StopReason nil -> end_turn, safety erased). The OpenAI route on the
// same gateway reports content_filter on the same turn, so the upstream really
// did filter. An eval built in one mode invalidates qualification in the other.

#[test]
fn bifrost_anthropic_nonstream_erases_filter_to_end_turn() {
    // Frozen violation: the mock upstream returned status incomplete with
    // incomplete_details content_filter, and the non-streaming client was told
    // end_turn with the text "blocked" as if it were a normal answer.
    // Reuses the 010A/034 checker unchanged: the invariant is a property of
    // the safety signal, not of one gateway version.
    let body = fixture("transcripts/078/filter-nonstream.json");
    let stop = serde_json::from_str::<Value>(&body)
        .expect("filter-nonstream.json must be valid JSON")["stop_reason"]
        .as_str()
        .unwrap_or_default()
        .to_owned();
    let v = content_filter_preserved("content_filter", &stop);
    assert!(
        matches!(v, Verdict::Violation(_)),
        "non-streaming must be caught erasing content_filter to end_turn: {v:?}"
    );
}

#[test]
fn bifrost_anthropic_stream_reports_filter_as_refusal() {
    // Same upstream turn, streaming transport: conformant. This is what makes
    // 078 a divergence finding rather than a repeat of 034: one transport was
    // fixed, the other was not, and they now disagree with each other.
    let sse = fixture("transcripts/078/filter-stream.sse");
    assert_eq!(
        anthropic_stream_safety_stop_reason(&sse, "refusal"),
        Verdict::Conformant,
        "streaming reports the same filtered turn as refusal"
    );
    // Vacuity guard: demand end_turn of the refusal stream and require a
    // Violation, so a fixture that lost its terminal fails instead of passing.
    assert!(
        matches!(
            anthropic_stream_safety_stop_reason(&sse, "end_turn"),
            Verdict::Violation(_)
        ),
        "control is vacuous: stream fixture carries no refusal terminal to judge"
    );
}

#[test]
fn bifrost_anthropic_plain_turn_agrees_across_transports() {
    // Controls: an unfiltered turn is end_turn on both transports, so the
    // checkers above distinguish filtered from finished rather than flagging
    // every end_turn.
    let body = fixture("transcripts/078/plain-nonstream.json");
    let stop = serde_json::from_str::<Value>(&body)
        .expect("plain-nonstream.json must be valid JSON")["stop_reason"]
        .as_str()
        .unwrap_or_default()
        .to_owned();
    assert_eq!(
        stop, "end_turn",
        "plain non-stream control must stay end_turn"
    );
    let sse = fixture("transcripts/078/plain-stream.sse");
    assert_eq!(
        anthropic_stream_safety_stop_reason(&sse, "end_turn"),
        Verdict::Conformant,
        "plain stream control must stay end_turn"
    );
    // Vacuity guard for the plain stream leg: a refusal terminal must NOT
    // satisfy an end_turn expectation, so a filtered fixture can never pass
    // this control by accident.
    let flipped = sse.replace(
        "\"stop_reason\":\"end_turn\"",
        "\"stop_reason\":\"refusal\"",
    );
    assert_ne!(flipped, sse, "stream fixture carries no end_turn terminal");
    assert!(
        matches!(
            anthropic_stream_safety_stop_reason(&flipped, "end_turn"),
            Verdict::Violation(_)
        ),
        "control is vacuous: checker accepts a refusal terminal as end_turn"
    );
}

#[test]
fn bifrost_openai_route_control_keeps_content_filter() {
    // The OpenAI route on the same gateway and turn reports content_filter,
    // proving the upstream really did filter and the loss is isolated to the
    // non-streaming Anthropic serializer.
    let body = fixture("transcripts/078/control-openai-filter.json");
    let finish = serde_json::from_str::<Value>(&body)
        .expect("control-openai-filter.json must be valid JSON")["choices"][0]["finish_reason"]
        .as_str()
        .unwrap_or_default()
        .to_owned();
    assert_eq!(
        finish, "content_filter",
        "OpenAI route must preserve the filter signal"
    );
    assert_eq!(
        content_filter_preserved(&finish, "refusal"),
        Verdict::Conformant,
        "a preserved filter signal must not trip the erasure checker"
    );
}

// ---- bug 079: forged Bifrost virtual key opens an authenticated Realtime session ----

#[test]
fn bifrost_realtime_forged_virtual_key_reaches_upstream() {
    let client = fixture("transcripts/079/forged-realtime.http");
    let upstream = fixture("transcripts/079/upstream-forged-handshake.http");
    let verdict = invalid_credential_rejected_before_upstream(&client, &upstream);
    assert!(
        matches!(verdict, Verdict::Violation(_)),
        "forged key that upgrades and reaches upstream must be caught: {verdict:?}"
    );
}

#[test]
fn bifrost_http_forged_virtual_key_is_rejected_before_upstream() {
    let client = fixture("transcripts/079/control-http-forged-key.http");
    assert_eq!(
        invalid_credential_rejected_before_upstream(&client, ""),
        Verdict::Conformant,
        "ordinary inference control rejects the identical nonexistent key"
    );
}

#[test]
fn bifrost_realtime_auth_checker_has_nonvacuous_controls() {
    let absent = fixture("transcripts/079/absent-realtime.http");
    assert_eq!(
        invalid_credential_rejected_before_upstream(&absent, ""),
        Verdict::Conformant,
        "missing-credential Realtime control is rejected without upstream work"
    );

    let valid = fixture("transcripts/079/valid-realtime.http");
    let upstream = fixture("transcripts/079/upstream-forged-handshake.http");
    assert!(valid.contains("x-bf-vk: sk-bf-valid-synthetic-realtime-079"));
    assert!(valid.contains("HTTP/1.1 101 Switching Protocols"));
    assert!(upstream.contains("Authorization: Bearer <BIFROST_PROVIDER_AUTH>"));

    // Conformant alone is vacuous for the upstream half of the invariant. Every
    // passing control above pairs a rejected status with an empty upstream, so a
    // checker that ignored `upstream_request` and always assumed no upstream
    // contact would satisfy all of them and still call the 079 violation a
    // violation, because the forged case already fails on its 101 status. Keep
    // the rejected status and attach a real upstream handshake: only the
    // reached-upstream branch can turn that into a Violation, so a checker that
    // stops consulting its second argument fails here instead of passing as a
    // silent false green.
    assert!(
        matches!(
            invalid_credential_rejected_before_upstream(&absent, &upstream),
            Verdict::Violation(_)
        ),
        "control is vacuous: checker ignores whether a rejected request still reached upstream"
    );
}

// ---- bug 077: Bifrost returns a custom provider credential to a VK caller ----

const ISSUE_077_SECRET: &str = "CANARY_ADMIN_ONLY_PROVIDER_SECRET_077";
const ISSUE_077_AUTH_CONTROL: &str = "CANARY_AUTHORIZATION_CONTROL_077";
const ISSUE_077_SAFE_CONTROL: &str = "CANARY_SAFE_TRACE_077";

fn issue_077_response_body(exchange: &str) -> Value {
    let (_, body) = exchange
        .rsplit_once("\r\n\r\n")
        .expect("077 exchange has response headers and a body");
    serde_json::from_str(body).expect("077 client response body is JSON")
}

#[test]
fn bifrost_provider_response_header_leaks_admin_secret() {
    let observed = fixture("transcripts/077/observed.http");
    assert!(
        observed.starts_with("POST /v1/responses HTTP/1.1\r\n"),
        "fixture must exercise the public Responses route"
    );
    assert!(
        observed.contains("HTTP/1.1 200 OK\r\n"),
        "fixture must be a successful response, not an error echo"
    );
    assert_eq!(
        response_omits_secret(&observed, ISSUE_077_SECRET),
        Verdict::Violation(format!(
            "response body contains deployment secret marker {ISSUE_077_SECRET:?}"
        )),
        "the full client-visible exchange must expose the administrator canary"
    );

    let body = issue_077_response_body(&observed);
    assert_eq!(
        body.pointer("/extra_fields/provider_response_headers/X-Provider-Secret")
            .and_then(Value::as_str),
        Some(ISSUE_077_SECRET),
        "the second client-visible copy must remain frozen in JSON metadata"
    );
}

#[test]
fn bifrost_provider_response_secret_controls_are_discriminating() {
    let observed = fixture("transcripts/077/observed.http");
    let upstream = fixture("transcripts/077/upstream.http");
    assert!(
        upstream.contains(&format!(
            "Authorization: Bearer {ISSUE_077_AUTH_CONTROL}\r\n"
        )),
        "upstream must actually emit the exact-name credential control"
    );
    assert_eq!(
        response_omits_secret(&observed, ISSUE_077_AUTH_CONTROL),
        Verdict::Conformant,
        "Bifrost must strip the Authorization control from the whole client exchange"
    );
    assert!(
        observed.contains(&format!("X-Safe-Trace: {ISSUE_077_SAFE_CONTROL}\r\n")),
        "benign provider response headers must still pass"
    );
    assert_eq!(
        issue_077_response_body(&observed)
            .pointer("/extra_fields/provider_response_headers/X-Safe-Trace")
            .and_then(Value::as_str),
        Some(ISSUE_077_SAFE_CONTROL),
        "benign provider metadata must still pass in JSON"
    );
}

#[test]
fn bifrost_provider_response_secret_expected_exchange_is_clean() {
    let expected = fixture("transcripts/077/expected.http");
    assert!(expected.contains("HTTP/1.1 200 OK\r\n"));
    assert_eq!(
        response_omits_secret(&expected, ISSUE_077_SECRET),
        Verdict::Conformant,
        "a fixed successful response must omit the secret everywhere"
    );
    assert!(expected.contains(ISSUE_077_SAFE_CONTROL));
}

#[test]
fn bifrost_provider_response_secret_unauthenticated_control_is_closed() {
    let control = fixture("transcripts/077/unauthenticated-control.http");
    assert!(
        control.starts_with("POST /v1/responses HTTP/1.1\r\n"),
        "control must send the same public request"
    );
    assert!(
        control.contains("HTTP/1.1 401 Unauthorized\r\n"),
        "control must prove inference authentication is enforced"
    );
    assert_eq!(
        response_omits_secret(&control, ISSUE_077_SECRET),
        Verdict::Conformant,
        "an unauthenticated caller must not receive the provider secret"
    );
}

#[test]
fn bifrost_provider_response_secret_management_boundary_refuses_virtual_key() {
    // The same virtual key that receives the secret at /v1/responses must be
    // refused by the management API that would otherwise expose provider config.
    // Upstream builds APIMiddleware with allowVirtualKeyAuth=false precisely
    // because "a VK is not an admin/session credential", so this fixture pins the
    // privilege gap the leak crosses.
    let boundary = fixture("transcripts/077/management-boundary-control.http");
    assert!(
        boundary.starts_with("GET /api/providers HTTP/1.1\r\n"),
        "control must target the provider configuration route"
    );
    assert!(
        boundary.contains("x-bf-vk: <VIRTUAL_KEY>\r\n"),
        "control must present the same virtual key that succeeds at inference"
    );
    assert!(
        boundary.contains("HTTP/1.1 401 Unauthorized\r\n"),
        "the virtual key must not authenticate against the management API"
    );
    assert_eq!(
        response_omits_secret(&boundary, ISSUE_077_SECRET),
        Verdict::Conformant,
        "the supported read path must not hand the virtual key the provider secret"
    );
}

#[test]
fn bifrost_provider_response_secret_summary_covers_all_trials() {
    let summary: Value =
        serde_json::from_str(&fixture("transcripts/077/results.json")).expect("077 summary JSON");
    assert_eq!(summary["complete"], true);
    assert_eq!(summary["runs"], 5);
    assert_eq!(
        summary["target"]["management_auth_enabled"], true,
        "the boundary claim requires the management API to be authenticated"
    );
    assert_eq!(
        summary["target"]["commit"],
        "44a562431ee0463cb1afe0e5833cde07d7921701"
    );
    for pointer in [
        "/authenticated/http_200",
        "/authenticated/secret_in_http_header",
        "/authenticated/secret_in_json_metadata",
        "/authenticated/consumer_extracted_admin_secret",
        "/same_response_controls/authorization_stripped_from_header_and_json",
        "/same_response_controls/safe_trace_preserved_in_header_and_json",
        "/unauthenticated_control/http_401",
        "/unauthenticated_control/secret_absent",
        "/management_boundary_control/virtual_key_refused_by_config_api",
        "/management_boundary_control/secret_absent",
    ] {
        assert_eq!(summary.pointer(pointer).and_then(Value::as_u64), Some(5));
    }
}

// ---- bug 080: Dynamo ImageLoader lowercases the whole URL for its cache key ----

#[test]
fn dynamo_image_cache_case_collision_violation() {
    // Frozen bug: /Cat.png and /cat.png share one lowered cache key, so the
    // second request decodes the first image and the origin sees one hit.
    let v = image_url_cache_key_case_sensitive(&fixture("transcripts/080/capture-bug.jsonl"));
    assert!(
        matches!(v, Verdict::Violation(_)),
        "the case-colliding cache key must be caught: {v:?}"
    );
}

#[test]
fn dynamo_image_cache_cache_off_control_is_conformant() {
    // Control: with the cache disabled the same URL pair fetches both
    // resources; the checker must accept it.
    let v = image_url_cache_key_case_sensitive(&fixture("transcripts/080/capture-control.jsonl"));
    assert_eq!(
        v,
        Verdict::Conformant,
        "cache-off runs must satisfy the invariant"
    );
}

#[test]
fn dynamo_image_cache_distinct_urls_control_is_conformant() {
    // Control: URLs differing beyond case never collide; checker is silent.
    let v = image_url_cache_key_case_sensitive(&fixture("transcripts/080/capture-distinct.jsonl"));
    assert_eq!(v, Verdict::Conformant);
}

// ---- bug 081: OGX /v1/messages translation-mode losses ----

/// Load one kairo 081 case file: an array of
/// `{trial, request, client_status, client_response, forwarded}` records.
fn ogx_cases(rel: &str) -> Vec<Value> {
    let raw = fixture(rel);
    let parsed: Value = serde_json::from_str(&raw).unwrap_or_else(|e| panic!("{rel}: {e}"));
    parsed
        .as_array()
        .unwrap_or_else(|| panic!("{rel} must be an array of cases"))
        .clone()
}

fn ogx_client_response(case: &Value) -> String {
    case.get("client_response")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

#[test]
fn ogx_adaptive_thinking_is_silently_ignored() {
    // A request with thinking {"type":"adaptive"} returns 200 and the
    // forwarded OpenAI body carries no thinking or reasoning configuration.
    let rel = "transcripts/081/ogx-adaptive-thinking-cases.json";
    assert!(matches!(
        ogx_adaptive_thinking_loss(&fixture(rel), 5),
        Verdict::Violation(_)
    ));
    let cases = ogx_cases(rel);
    assert_eq!(cases.len(), 5, "{rel} must hold 5 of 5 trials");
    for (i, case) in cases.iter().enumerate() {
        assert_eq!(
            case["client_status"],
            200,
            "{rel} trial {} must return 200",
            i + 1
        );
        let forwarded = &case["forwarded"];
        assert!(
            forwarded.get("thinking").is_none()
                && forwarded.get("reasoning").is_none()
                && forwarded.get("reasoning_effort").is_none(),
            "{rel} trial {} must drop adaptive thinking from the forwarded body",
            i + 1
        );
    }
}

#[test]
fn ogx_enabled_thinking_control_fails_closed() {
    // Control: OGX's own convention for unsupported thinking configs is to
    // refuse. The same surface refuses {"type":"enabled"} with a 400.
    let rel = "transcripts/081/ogx-enabled-thinking-control-cases.json";
    let cases = ogx_cases(rel);
    assert_eq!(cases.len(), 5, "{rel} must hold 5 of 5 trials");
    for (i, case) in cases.iter().enumerate() {
        assert_eq!(case["client_status"], 400, "{rel} trial {}", i + 1);
        let client = ogx_client_response(case);
        assert!(
            client.contains("invalid_request_error"),
            "{rel} trial {} must be an Anthropic invalid_request_error",
            i + 1
        );
    }
}

// ---- bug 082: repeated agent-mode calls overwrite an executed result ----

#[test]
fn bifrost_agent_mode_same_name_call_loses_one_executed_result() {
    let executions = fixture("transcripts/082/violation-executions.json");
    let response = fixture("transcripts/082/violation-client-response.http");
    let verdict = executed_tool_results_preserved(&executions, &response);
    assert!(
        matches!(verdict, Verdict::Violation(_)),
        "two executed same-name calls must not collapse to one result: {verdict:?}"
    );
}

#[test]
fn bifrost_agent_mode_result_controls_preserve_every_execution() {
    for cell in ["control_distinct", "control_single"] {
        let executions = fixture(&format!("transcripts/082/{cell}-executions.json"));
        let response = fixture(&format!("transcripts/082/{cell}-client-response.http"));
        assert_eq!(
            executed_tool_results_preserved(&executions, &response),
            Verdict::Conformant,
            "{cell} must preserve every executed result"
        );
    }

    // Vacuity guard: a conformant response must fail when the execution index
    // demands a second distinct result that the response does not contain.
    let single_response = fixture("transcripts/082/control_single-client-response.http");
    let two_executions = fixture("transcripts/082/control_distinct-executions.json");
    assert!(matches!(
        executed_tool_results_preserved(&two_executions, &single_response),
        Verdict::Violation(_)
    ));
}

#[test]
fn bifrost_agent_mode_five_run_matrix_reaches_the_consumer_boundary() {
    let summary: Value =
        serde_json::from_str(&fixture("transcripts/082/results.json")).expect("082 summary JSON");
    assert_eq!(summary["complete"], true);
    assert_eq!(summary["runs"], 5);
    assert_eq!(
        summary.pointer("/cells/violation/executions_per_run"),
        Some(&serde_json::json!([2, 2, 2, 2, 2]))
    );
    assert_eq!(
        summary.pointer("/cells/violation/reported_results_per_run"),
        Some(&serde_json::json!([1, 1, 1, 1, 1]))
    );
    assert_eq!(
        summary.pointer("/cells/control_distinct/reported_results_per_run"),
        Some(&serde_json::json!([2, 2, 2, 2, 2]))
    );
    assert_eq!(
        summary.pointer("/cells/control_single/reported_results_per_run"),
        Some(&serde_json::json!([1, 1, 1, 1, 1]))
    );
    assert_eq!(
        summary.pointer("/consumer/violation/duplicate_executions_per_run"),
        Some(&serde_json::json!([1, 1, 1, 1, 1]))
    );
    assert_eq!(
        summary.pointer("/consumer/control_distinct/duplicate_executions_per_run"),
        Some(&serde_json::json!([0, 0, 0, 0, 0]))
    );
}
