//! Conformance suite over recorded wire transcripts.
//!
//! Each test loads bytes captured from a real gateway and asserts the verdict
//! the checker should return for that recording. A `Violation` assertion is a
//! reproduced, frozen bug: the day a gateway (or our own router) stops
//! violating the invariant, this test flips and tells us.

use kairo::checks::{
    allowlist_bounds_served_model, anthropic_response_toolcall_stop_reason,
    anthropic_server_tools_forwarded, anthropic_stop_sequence_reported,
    anthropic_stream_block_lifecycle, anthropic_stream_safety_stop_reason,
    anthropic_tool_choice_any_mapped_to_required, anthropic_toolcall_stop_reason, capture_records,
    client_cache_breakpoints_reach_provider, codex_namespace_only_applies_to_namespaced_tools,
    content_filter_preserved, document_body_forwarded, endpoint_response_family_preserved,
    executed_tool_results_preserved, forwarded_body_preserves_values,
    gemini_inline_media_preserved_in_chat_response, gemini_inline_media_preserved_in_chat_stream,
    id_conforms, image_url_cache_key_case_sensitive, instruction_messages_preserved,
    invalid_credential_rejected_before_upstream, is_error_forwarded, json_schema_forwarded,
    json_schema_property_forwarded, mcp_tool_executes_once, model_info_capture_identity,
    model_info_envelope_body, model_info_omits_api_base_secret, no_empty_text_alongside_tool_use,
    no_indexerror_leak, no_invented_cache_control, no_phantom_null_output_text,
    non_text_block_not_json_dumped, ogx_adaptive_thinking_loss, openai_stream_finish_reason,
    openai_stream_toolcall_type_never_null, openai_toolcall_id_charset,
    outbound_request_omits_secret, parallel_tool_disable_preserved, provider_request_id_preserved,
    reasoning_text_order_preserved, refusal_text_preserved, registration_resolvable_by_both_paths,
    replayed_calls_keep_returned_namespace, response_content_not_empty,
    response_conversation_preserves_history, response_omits_secret,
    responses_fallback_not_spliced_after_delivery, responses_fallback_preserves_delivered_indexes,
    responses_no_restart_after_output, responses_refusal_semantics_preserved,
    responses_single_lifecycle, stop_sequence_forwarded, thinking_not_leaked_as_visible_text,
    thinking_text_forwarded, tool_result_images_reach_provider, tool_strict_forwarded,
    toolcall_id_restored_upstream, truncation_preserved, upstream_bearer_is,
    upstream_omits_header_value, worker_loss_stays_invisible, FunctionToolFormat, Verdict,
    EMPTY_TEXT_ALONGSIDE_TOOL_USE, JSON_SCHEMA_ABSENT, JSON_SCHEMA_PROPERTY_ABSENT,
    REPLAYED_CALL_NAMESPACE_CHANGED, TOOL_RESULT_IMAGE_DROPPED,
};
use serde_json::Value;
use std::collections::BTreeMap;
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

// ---- bug 083: Switchyard drops cross-format conversation continuations ----

#[test]
fn switchyard_conversation_continuation_drops_history() {
    let forwarded = fixture("transcripts/083/forwarded.jsonl");
    let verdict = response_conversation_preserves_history(
        &forwarded,
        "CONVERSATION BUG RECALL_",
        "CONVERSATION BUG SEED_CANARY_081_",
        5,
    );
    assert!(matches!(verdict, Verdict::Violation(_)), "{verdict:?}");

    let capture = fixture("transcripts/083/capture-bug.jsonl");
    let captures: Vec<Value> = capture
        .lines()
        .map(|line| serde_json::from_str(line).expect("083 bug capture is JSON"))
        .collect();
    assert_eq!(captures.len(), 5);
    assert!(captures.iter().all(|record| {
        record["seed_canary_in_continuation_response"] == false
            && record["client_response_body_raw"]
                .as_str()
                .is_some_and(|body| !body.contains("SEED_CANARY_081_"))
    }));
}

#[test]
fn switchyard_previous_response_id_control_preserves_history() {
    assert_eq!(
        response_conversation_preserves_history(
            &fixture("transcripts/083/forwarded.jsonl"),
            "CONVERSATION CONTROL RECALL_",
            "CONVERSATION CONTROL SEED_CANARY_081_",
            5,
        ),
        Verdict::Conformant
    );
    let capture = fixture("transcripts/083/capture-control.jsonl");
    let captures: Vec<Value> = capture
        .lines()
        .map(|line| serde_json::from_str(line).expect("083 control capture is JSON"))
        .collect();
    assert_eq!(captures.len(), 5);
    assert!(captures
        .iter()
        .all(|record| record["seed_canary_in_continuation_response"] == true));
}

// ---- bug 084: LiteLLM removes provider-owned IDs from OpenAI pass-through ----

#[test]
fn litellm_alpha_search_drops_provider_request_id() {
    for trial in 1..=5 {
        let client = fixture(&format!(
            "transcripts/084/raw/observed/proxy-client-request-{trial:02}.json"
        ));
        let forwarded = fixture(&format!(
            "transcripts/084/raw/observed/proxy-upstream-request-{trial:02}.json"
        ));
        assert!(
            matches!(
                provider_request_id_preserved(&client, &forwarded),
                Verdict::Violation(_)
            ),
            "proxy trial {trial} must detect the dropped provider ID"
        );
    }
}

#[test]
fn litellm_alpha_search_direct_control_preserves_provider_request_id() {
    for trial in 1..=5 {
        let client = fixture("transcripts/084/raw/observed/client-request.json");
        let forwarded = fixture(&format!(
            "transcripts/084/raw/observed/direct-upstream-request-{trial:02}.json"
        ));
        assert_eq!(
            provider_request_id_preserved(&client, &forwarded),
            Verdict::Conformant,
            "direct control trial {trial} must preserve the provider ID"
        );
    }
}

#[test]
fn provider_request_id_checker_has_conformant_and_vacuous_controls() {
    assert_eq!(
        provider_request_id_preserved(r#"{"id":"req_123"}"#, r#"{"id":"req_123"}"#),
        Verdict::Conformant
    );
    assert_eq!(
        provider_request_id_preserved(r#"{"model":"m"}"#, r#"{"model":"m"}"#),
        Verdict::Conformant
    );
    assert!(matches!(
        provider_request_id_preserved(r#"{"id":"req_123"}"#, r#"{"id":"req_456"}"#),
        Verdict::Violation(_)
    ));
}

#[test]
fn codex_consumer_search_calls_lose_provider_request_id() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../transcripts/084/codex/runs");
    let mut violations = 0;
    for case in [
        "A-builtin-passthrough-default",
        "A-builtin-passthrough-live",
        "D-custom-passthrough-flag-default",
        "D-custom-passthrough-flag-live",
    ] {
        let search = root.join(case).join("search");
        let mut clients: Vec<_> = fs::read_dir(&search)
            .unwrap_or_else(|e| panic!("read {}: {e}", search.display()))
            .filter_map(|entry| {
                entry
                    .ok()
                    .map(|entry| entry.file_name().into_string().unwrap())
            })
            .filter(|name| name.starts_with("client-") && name.ends_with("-request-body.json"))
            .collect();
        clients.sort();
        assert!(
            !clients.is_empty(),
            "{case} must contain Codex search calls"
        );
        for client in clients {
            let upstream = client.replacen("client-", "upstream-", 1);
            let client_body = fs::read_to_string(search.join(&client)).unwrap();
            let upstream_body = fs::read_to_string(search.join(&upstream)).unwrap();
            assert!(
                matches!(
                    provider_request_id_preserved(&client_body, &upstream_body),
                    Verdict::Violation(_)
                ),
                "{case} {client}: Codex sent an id that LiteLLM must have dropped"
            );
            violations += 1;
        }
    }
    assert_eq!(
        violations, 24,
        "every captured Codex search call through the pass-through route loses its id"
    );
}

// ---- bug 085: LiteLLM Responses mid-stream fallback replays a delivered tool
// call and splices a second lifecycle into the public stream ----

const ISSUE_085_VERSIONS: [&str; 2] = ["1.102.1", "1.104.0"];
const ISSUE_085_SDKS: [&str; 4] = ["2.54.0", "3.13.0", "3.14.0", "3.19.2"];

/// openai-python 3.14.0 re-keys its Responses stream accumulator by
/// `output_index` (openai/openai-python#3126).
fn issue_085_sdk_tolerates_reuse(openai: &str) -> bool {
    let parts: Vec<u64> = openai.split('.').filter_map(|p| p.parse().ok()).collect();
    parts.as_slice() >= [3u64, 14, 0].as_slice()
}

fn issue_085_consumer(mode: &str, openai: &str, consumer: &Value) -> Result<(), String> {
    let calls: Vec<&str> = consumer
        .get("completed_tool_call_ids")
        .and_then(Value::as_array)
        .map(|ids| ids.iter().filter_map(Value::as_str).collect())
        .unwrap_or_default();
    let has_error = !matches!(consumer.get("error"), None | Some(Value::Null));
    let error_type = consumer.pointer("/error/type").and_then(Value::as_str);
    let no_final = matches!(consumer.get("final_response"), None | Some(Value::Null));
    let lifecycles = consumer
        .get("created_ids")
        .and_then(Value::as_array)
        .map(Vec::len);
    let ok = match mode {
        "trigger-duplicate-tool" | "trigger-duplicate-tool-response-failed" => {
            !has_error && calls == ["call_primary", "call_fallback"] && lifecycles == Some(2)
        }
        "trigger-sdk-crash-item-type" | "trigger-sdk-crash-partial-text" => {
            if issue_085_sdk_tolerates_reuse(openai) {
                let expected: &[&str] = if mode == "trigger-sdk-crash-item-type" {
                    &["call_primary", "call_fallback"]
                } else {
                    &["call_fallback"]
                };
                !has_error && !no_final && calls == expected
            } else {
                error_type == Some("AssertionError") && no_final
            }
        }
        "control-no-fault" => !has_error && calls == ["call_primary"] && lifecycles == Some(1),
        "control-fault-before-output" | "boundary-announced-only" => {
            !has_error && calls == ["call_fallback"]
        }
        "boundary-transport-drop" => has_error && no_final && !calls.contains(&"call_fallback"),
        _ => return Err(format!("unknown mode {mode}")),
    };
    if ok {
        Ok(())
    } else {
        Err(format!(
            "{mode} under openai {openai}: unexpected SDK outcome {consumer}"
        ))
    }
}

fn issue_085_expect(
    verdict: &Verdict,
    violation: bool,
    what: &str,
    index: usize,
) -> Result<(), String> {
    match (violation, verdict) {
        (true, Verdict::Violation(_)) | (false, Verdict::Conformant) => Ok(()),
        _ => Err(format!(
            "record {index}: {what} verdict {verdict:?} does not match the evidence"
        )),
    }
}

fn validate_issue_085_mode(record: &Value, mode: &str, index: usize) -> Result<(), String> {
    let response_raw = record
        .pointer("/client_response/body_raw")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("record {index} has no raw response"))?;
    let upstream: Vec<&str> = record
        .get("upstream_exchanges")
        .and_then(Value::as_array)
        .ok_or_else(|| format!("record {index} has no upstream exchanges"))?
        .iter()
        .filter_map(|exchange| exchange.get("response_body_raw").and_then(Value::as_str))
        .collect();
    let (expected_calls, spliced) = match mode {
        "trigger-duplicate-tool"
        | "trigger-duplicate-tool-response-failed"
        | "trigger-sdk-crash-item-type"
        | "trigger-sdk-crash-partial-text"
        | "boundary-announced-only" => (2, true),
        "control-fault-before-output" => (2, false),
        "control-no-fault" | "boundary-transport-drop" => (1, false),
        _ => return Err(format!("unknown mode {mode}")),
    };
    if upstream.len() != expected_calls {
        return Err(format!(
            "record {index} made {} upstream calls, expected {expected_calls}",
            upstream.len()
        ));
    }
    if mode != "boundary-transport-drop"
        && record
            .pointer("/client_response/status")
            .and_then(Value::as_u64)
            != Some(200)
    {
        return Err(format!("record {index} is not a successful public route"));
    }
    issue_085_expect(
        &responses_fallback_preserves_delivered_indexes(response_raw),
        spliced,
        "output-index",
        index,
    )?;
    issue_085_expect(
        &responses_no_restart_after_output(response_raw),
        spliced,
        "lifecycle-restart",
        index,
    )?;
    issue_085_expect(
        &responses_fallback_not_spliced_after_delivery(response_raw, &upstream),
        spliced,
        "fallback-splice",
        index,
    )?;
    let live_sdk = record
        .get("consumer_openai")
        .and_then(Value::as_str)
        .ok_or_else(|| format!("record {index} has no consumer SDK version"))?;
    let mut versions = vec![live_sdk];
    issue_085_consumer(
        mode,
        live_sdk,
        record
            .get("consumer")
            .ok_or_else(|| format!("record {index} has no consumer outcome"))?,
    )?;
    for replay in record
        .get("sdk_replays")
        .and_then(Value::as_array)
        .ok_or_else(|| format!("record {index} has no SDK replays"))?
    {
        let openai = replay
            .get("openai")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("record {index} has an unlabeled SDK replay"))?;
        issue_085_consumer(
            mode,
            openai,
            replay
                .get("consumer")
                .ok_or_else(|| format!("record {index} replay has no outcome"))?,
        )?;
        versions.push(openai);
    }
    if versions != ISSUE_085_SDKS {
        return Err(format!(
            "record {index} covers openai {versions:?}, expected {ISSUE_085_SDKS:?}"
        ));
    }
    Ok(())
}

fn issue_085_records(jsonl: &str, version: &str, mode: &str) -> Result<Vec<Value>, String> {
    let records = jsonl
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str::<Value>(line).map_err(|error| error.to_string()))
        .collect::<Result<Vec<_>, _>>()?;
    if records.len() != 5 {
        return Err(format!("expected five records, got {}", records.len()));
    }
    let tool_choice = if mode == "trigger-sdk-crash-partial-text" {
        "auto"
    } else {
        "required"
    };
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
        {
            return Err(format!("record {} is not the public route", index + 1));
        }
        let request_raw = record
            .pointer("/client_request/body_raw")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("record {} has no raw request", index + 1))?;
        let request: Value =
            serde_json::from_str(request_raw).map_err(|error| error.to_string())?;
        if request.get("model").and_then(Value::as_str) != Some(mode)
            || request.get("stream").and_then(Value::as_bool) != Some(true)
            || request.get("tool_choice").and_then(Value::as_str) != Some(tool_choice)
        {
            return Err(format!("record {} has wrong request shape", index + 1));
        }
        validate_issue_085_mode(record, mode, index + 1)?;
    }
    Ok(records)
}

fn issue_085_check(modes: &[&str]) {
    for version in ISSUE_085_VERSIONS {
        for mode in modes {
            let path = format!("transcripts/085/{version}/{mode}.jsonl");
            issue_085_records(&fixture(&path), version, mode)
                .unwrap_or_else(|error| panic!("{path}: {error}"));
        }
    }
}

#[test]
fn litellm_responses_fallback_replays_delivered_tool_call() {
    issue_085_check(&[
        "trigger-duplicate-tool",
        "trigger-duplicate-tool-response-failed",
    ]);
}

#[test]
fn litellm_responses_fallback_crashes_openai_python_before_3_14() {
    issue_085_check(&[
        "trigger-sdk-crash-item-type",
        "trigger-sdk-crash-partial-text",
    ]);
}

#[test]
fn litellm_responses_fallback_controls_and_boundaries() {
    issue_085_check(&[
        "control-no-fault",
        "control-fault-before-output",
        "boundary-announced-only",
        "boundary-transport-drop",
    ]);
}

#[test]
fn issue_085_checks_later_trials_and_rejects_malformed_evidence() {
    let original = fixture("transcripts/085/1.102.1/control-no-fault.jsonl");
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
    assert!(issue_085_records(&mutated, "1.102.1", "control-no-fault").is_err());

    let mut replays = original
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();
    replays[4]["sdk_replays"] = Value::Array(Vec::new());
    let dropped = replays
        .iter()
        .map(Value::to_string)
        .collect::<Vec<_>>()
        .join("\n");
    assert!(issue_085_records(&dropped, "1.102.1", "control-no-fault").is_err());

    let malformed = format!("{}\n{{", original.trim_end());
    assert!(issue_085_records(&malformed, "1.102.1", "control-no-fault").is_err());
}

#[test]
fn issue_085_splice_invariant_survives_renumbering() {
    // A gateway change that hid the second lifecycle and shifted the
    // fallback's indexes, but still replayed the call, would pass the index
    // and restart checks. The cross-hop splice check must still reject it.
    let line = fixture("transcripts/085/1.102.1/trigger-duplicate-tool.jsonl");
    let record: Value = serde_json::from_str(line.lines().next().unwrap()).unwrap();
    let raw = record
        .pointer("/client_response/body_raw")
        .and_then(Value::as_str)
        .unwrap();
    let mut created_seen = false;
    let mut fallback_started = false;
    let composed = raw
        .split("\n\n")
        .filter_map(|block| {
            let data = block.strip_prefix("data: ")?;
            let Ok(mut event) = serde_json::from_str::<Value>(data) else {
                return Some(block.to_owned());
            };
            if event["type"] == "response.created" {
                if created_seen {
                    fallback_started = true;
                    return None;
                }
                created_seen = true;
            }
            if fallback_started {
                if let Some(index) = event.get("output_index").and_then(Value::as_u64) {
                    event["output_index"] = (index + 1).into();
                }
            }
            Some(format!("data: {event}"))
        })
        .collect::<Vec<_>>()
        .join("\n\n");
    let upstream: Vec<&str> = record["upstream_exchanges"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|exchange| exchange["response_body_raw"].as_str())
        .collect();
    assert_eq!(
        responses_fallback_preserves_delivered_indexes(&composed),
        Verdict::Conformant
    );
    assert_eq!(
        responses_no_restart_after_output(&composed),
        Verdict::Conformant
    );
    assert!(matches!(
        responses_fallback_not_spliced_after_delivery(&composed, &upstream),
        Verdict::Violation(_)
    ));
}

const ISSUE_085_CODEX_CASES: [(&str, &str, u64); 7] = [
    ("1.102.1", "codex-trigger-duplicate-tool", 2),
    ("1.102.1", "codex-control-no-fault", 1),
    ("1.102.1", "codex-control-fault-before-output", 1),
    ("1.104.0", "codex-trigger-duplicate-tool", 2),
    ("1.104.0", "codex-control-no-fault", 1),
    ("1.104.0", "codex-control-fault-before-output", 1),
    ("1.102.1-live", "codex-trigger-live-fallback", 2),
];

fn issue_085_request(exchange: &Value) -> Result<Value, String> {
    let raw = exchange
        .get("request_body_raw")
        .and_then(Value::as_str)
        .ok_or("exchange has no request body")?;
    for marker in raw.split("[kairo-ref sha256:").skip(1) {
        let file = marker
            .split_once(" file:")
            .and_then(|(_, rest)| rest.split_once(']'))
            .map(|(file, _)| file)
            .ok_or("malformed shared reference")?;
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../transcripts/085/codex")
            .join(file);
        if !path.is_file() {
            return Err(format!("shared reference {file} is missing"));
        }
    }
    serde_json::from_str(raw).map_err(|error| error.to_string())
}

fn issue_085_tool_outputs(request: &Value) -> Vec<String> {
    request
        .get("input")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter(|item| {
                    item.get("type").and_then(Value::as_str) == Some("function_call_output")
                })
                .filter_map(|item| item.get("call_id").and_then(Value::as_str))
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

fn validate_issue_085_codex(
    record: &Value,
    dir: &str,
    case: &str,
    executions: u64,
    trial: usize,
) -> Result<(), String> {
    let version = dir.trim_end_matches("-live");
    if record.pointer("/target/version").and_then(Value::as_str) != Some(version)
        || record.pointer("/consumer/name").and_then(Value::as_str) != Some("codex-cli")
        || record.pointer("/consumer/version").and_then(Value::as_str) != Some("0.156.1")
        || record.get("case").and_then(Value::as_str) != Some(case)
        || record.get("trial").and_then(Value::as_u64) != Some(trial as u64)
        || record.get("codex_exit").and_then(Value::as_i64) != Some(0)
    {
        return Err(format!(
            "record {trial} has wrong provenance or Codex failed"
        ));
    }
    let executed = record
        .get("codex_events")
        .and_then(Value::as_array)
        .ok_or("no Codex events")?
        .iter()
        .filter(|event| {
            event.get("type").and_then(Value::as_str) == Some("item.completed")
                && event.pointer("/item/type").and_then(Value::as_str) == Some("command_execution")
        })
        .count() as u64;
    if executed != executions
        || record.get("ledger_lines").and_then(Value::as_u64) != Some(executions)
        || record.get("command_executions").and_then(Value::as_u64) != Some(executions)
    {
        return Err(format!(
            "record {trial}: Codex ran the side effect {executed} times, expected {executions}"
        ));
    }
    let client = record
        .get("client_exchanges")
        .and_then(Value::as_array)
        .ok_or("no client exchanges")?;
    if client.len() != 2
        || client.iter().any(|exchange| {
            exchange.get("path").and_then(Value::as_str) != Some("/v1/responses")
                || exchange.get("status").and_then(Value::as_u64) != Some(200)
        })
    {
        return Err(format!(
            "record {trial}: expected exactly two successful Codex requests"
        ));
    }
    let mut first_turn = None;
    let mut tool_outputs = Vec::new();
    for exchange in client {
        let outputs = issue_085_tool_outputs(&issue_085_request(exchange)?);
        if outputs.is_empty() {
            first_turn = exchange.get("response_body_raw").and_then(Value::as_str);
        } else {
            tool_outputs = outputs;
        }
    }
    let first_turn = first_turn.ok_or("no first turn")?;
    if tool_outputs.len() as u64 != executions {
        return Err(format!(
            "record {trial}: Codex returned {} tool results, expected {executions}",
            tool_outputs.len()
        ));
    }
    let spliced = executions == 2;
    issue_085_expect(
        &responses_fallback_preserves_delivered_indexes(first_turn),
        spliced,
        "output-index",
        trial,
    )?;
    issue_085_expect(
        &responses_no_restart_after_output(first_turn),
        spliced,
        "lifecycle-restart",
        trial,
    )?;
    let mut first_turn_upstream = Vec::new();
    for exchange in record
        .get("upstream_exchanges")
        .and_then(Value::as_array)
        .ok_or("no upstream exchanges")?
    {
        if issue_085_tool_outputs(&issue_085_request(exchange)?).is_empty() {
            first_turn_upstream.push(
                exchange
                    .get("response_body_raw")
                    .and_then(Value::as_str)
                    .ok_or("upstream exchange has no response")?,
            );
        }
    }
    // The live fallback hop goes straight to the provider and is not
    // captured, so the cross-hop check applies to the deterministic runs.
    if !dir.ends_with("-live") {
        issue_085_expect(
            &responses_fallback_not_spliced_after_delivery(first_turn, &first_turn_upstream),
            spliced,
            "fallback-splice",
            trial,
        )?;
    }
    Ok(())
}

#[test]
fn codex_runs_the_side_effect_twice_after_a_litellm_fallback() {
    for (dir, case, executions) in ISSUE_085_CODEX_CASES {
        let path = format!("transcripts/085/codex/{dir}/{case}.jsonl");
        let records = fixture(&path)
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| serde_json::from_str::<Value>(line).unwrap())
            .collect::<Vec<_>>();
        assert_eq!(records.len(), 5, "{path}: expected five Codex runs");
        for (index, record) in records.iter().enumerate() {
            validate_issue_085_codex(record, dir, case, executions, index + 1)
                .unwrap_or_else(|error| panic!("{path}: {error}"));
        }
    }
}

// ---- bug 086: Bifrost direct cache serves one API family's entry to the other ----

const ISSUE_086_CROSS_FAMILY: [(&str, &str); 4] = [
    ("chat_then_responses", "/v1/responses"),
    ("responses_then_chat", "/v1/chat/completions"),
    ("chat_stream_then_responses_stream", "/v1/responses"),
    ("responses_stream_then_chat_stream", "/v1/chat/completions"),
];

const ISSUE_086_CONTROLS: [(&str, &str); 4] = [
    ("control_chat_then_chat", "/v1/chat/completions"),
    ("control_responses_then_responses", "/v1/responses"),
    ("control_no_cache_key", "/v1/responses"),
    ("control_typed_responses_item", "/v1/responses"),
];

fn issue_086_first_endpoint(cell: &str) -> &'static str {
    if cell.starts_with("responses") || cell == "control_responses_then_responses" {
        "/v1/responses"
    } else {
        "/v1/chat/completions"
    }
}

#[test]
fn bifrost_direct_cache_serves_the_other_api_family() {
    for (cell, endpoint) in ISSUE_086_CROSS_FAMILY {
        let second = fixture(&format!(
            "transcripts/086/{cell}-second-client-response.http"
        ));
        let verdict = endpoint_response_family_preserved(endpoint, &second);
        assert!(
            matches!(verdict, Verdict::Violation(_)),
            "{cell}: a cached entry from the other API family must be flagged: {verdict:?}"
        );
        // The first call went upstream and is well formed for its own endpoint,
        // so the checker is not rejecting the gateway's normal output.
        let first = fixture(&format!(
            "transcripts/086/{cell}-first-client-response.http"
        ));
        assert_eq!(
            endpoint_response_family_preserved(issue_086_first_endpoint(cell), &first),
            Verdict::Conformant,
            "{cell}: the uncached first response must conform"
        );
    }
}

#[test]
fn bifrost_direct_cache_controls_keep_the_endpoint_family() {
    for (cell, endpoint) in ISSUE_086_CONTROLS {
        for phase in ["first", "second"] {
            let capture = fixture(&format!(
                "transcripts/086/{cell}-{phase}-client-response.http"
            ));
            let phase_endpoint = if phase == "first" {
                issue_086_first_endpoint(cell)
            } else {
                endpoint
            };
            assert_eq!(
                endpoint_response_family_preserved(phase_endpoint, &capture),
                Verdict::Conformant,
                "{cell} {phase} must keep its endpoint family"
            );
        }
    }
    // Vacuity guard: the same-family hit is conformant only for its own endpoint.
    let hit = fixture("transcripts/086/control_chat_then_chat-second-client-response.http");
    assert!(matches!(
        endpoint_response_family_preserved("/v1/responses", &hit),
        Verdict::Violation(_)
    ));
}

#[test]
fn bifrost_direct_cache_five_run_matrix_and_consumers() {
    let summary: Value =
        serde_json::from_str(&fixture("transcripts/086/results.json")).expect("086 summary JSON");
    assert_eq!(summary["complete"], true);
    assert_eq!(summary["runs"], 5);
    assert_eq!(summary["target"]["runtime_version"], "v2.2.3");
    assert_eq!(summary["target"]["vector_store"], "qdrant");
    let one_call = serde_json::json!([1, 1, 1, 1, 1]);
    let two_calls = serde_json::json!([2, 2, 2, 2, 2]);
    for (cell, _) in ISSUE_086_CROSS_FAMILY {
        assert_eq!(summary["cells"][cell]["second_wrong_family"], 5, "{cell}");
        assert_eq!(
            summary["cells"][cell]["upstream_calls_per_run"], one_call,
            "{cell}"
        );
    }
    for (cell, calls) in [
        ("control_chat_then_chat", &one_call),
        ("control_responses_then_responses", &one_call),
        ("control_no_cache_key", &two_calls),
        ("control_typed_responses_item", &two_calls),
    ] {
        assert_eq!(summary["cells"][cell]["second_wrong_family"], 0, "{cell}");
        assert_eq!(
            &summary["cells"][cell]["upstream_calls_per_run"], calls,
            "{cell}"
        );
    }

    let consumer = &summary["consumer"]["cells"];
    for (cell, failure) in [
        ("consumer_agents_after_chat", "AttributeError"),
        ("consumer_chat_after_agents", "AttributeError"),
        (
            "consumer_responses_stream_after_chat_stream",
            "RuntimeError",
        ),
        ("consumer_chat_stream_after_responses_stream", "ok"),
    ] {
        assert_eq!(consumer[cell]["final_healthy"], 0, "{cell}");
        assert_eq!(consumer[cell]["upstream_calls_per_run"], one_call, "{cell}");
        assert!(
            consumer[cell]["final_outcomes"].as_array().is_some_and(
                |outcomes| outcomes.len() == 5 && outcomes.iter().all(|o| o == failure)
            ),
            "{cell}: every run must end in {failure}"
        );
    }
    assert_eq!(
        consumer["consumer_control_agents_alone"]["final_healthy"],
        5
    );
    assert_eq!(
        consumer["consumer_control_agents_after_chat_no_cache_key"]["final_healthy"],
        5
    );
    assert_eq!(
        consumer["consumer_control_agents_after_chat_no_cache_key"]["upstream_calls_per_run"],
        two_calls
    );

    // The same matrix holds on the in-process store and on current dev.
    for (file, runtime, store) in [
        ("v2.2.3-chromem-results.json", "v2.2.3", "chromem"),
        ("dev-cefde78b-qdrant-results.json", "dev-cefde78b", "qdrant"),
    ] {
        let matrix: Value =
            serde_json::from_str(&fixture(&format!("transcripts/086/matrix/{file}")))
                .expect("086 matrix JSON");
        assert_eq!(matrix["complete"], true, "{file}");
        assert_eq!(matrix["target"]["runtime_version"], runtime, "{file}");
        assert_eq!(matrix["target"]["vector_store"], store, "{file}");
        for (cell, _) in ISSUE_086_CROSS_FAMILY {
            assert_eq!(
                matrix["cells"][cell]["second_wrong_family"], 5,
                "{file} {cell}"
            );
        }
        assert_eq!(matrix["persistence"]["runs_all_repeats_wrong"], 5, "{file}");
    }
}

#[test]
fn bifrost_direct_cache_entry_stays_poisoned_for_repeat_callers() {
    let summary: Value =
        serde_json::from_str(&fixture("transcripts/086/results.json")).expect("086 summary JSON");
    let persistence = &summary["persistence"];
    assert_eq!(persistence["repeats"], 3);
    assert_eq!(persistence["runs_all_repeats_wrong"], 5);
    assert_eq!(
        persistence["upstream_calls_per_run"],
        serde_json::json!([1, 1, 1, 1, 1])
    );
    for run in 1..=5 {
        for repeat in 1..=3 {
            let capture = fixture(&format!(
                "transcripts/086/runs/persistence_chat_then_responses-{run:02}-repeat{repeat}-client-response.http"
            ));
            assert!(
                matches!(
                    endpoint_response_family_preserved("/v1/responses", &capture),
                    Verdict::Violation(_)
                ),
                "run {run} repeat {repeat} must still be the poisoned entry"
            );
        }
    }
}

// ---- bug 087: LiteLLM router retry re-executes an MCP tool ----

fn issue_087_path(root: &str, relative: &str) -> PathBuf {
    let mut path = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    path.push("../../");
    path.push(root);
    path.push(relative);
    path
}

fn validate_issue_087_trial(
    root: &str,
    cell_name: &str,
    run: &Value,
    index: usize,
    tools: u64,
    upstream: u64,
    violation: bool,
) -> Result<(), String> {
    let trial = format!("{:02}", index + 1);
    if run.get("run").and_then(Value::as_str) != Some(&trial)
        || run.get("tool_executions").and_then(Value::as_u64) != Some(tools)
        || run.get("upstream_calls").and_then(Value::as_u64) != Some(upstream)
    {
        return Err(format!(
            "{cell_name} trial {trial}: summary counts are inconsistent"
        ));
    }
    let run_rel = format!("cells/{cell_name}/runs/run-{trial}");
    let ledger = fs::read_to_string(issue_087_path(
        root,
        &format!("{run_rel}/tool-ledger.jsonl"),
    ))
    .map_err(|error| format!("{cell_name} trial {trial}: cannot read ledger: {error}"))?;
    let verdict = mcp_tool_executes_once(&ledger);
    if violation != matches!(verdict, Verdict::Violation(_)) {
        return Err(format!(
            "{cell_name} trial {trial}: exactly-once verdict is {verdict:?}"
        ));
    }
    if ledger
        .lines()
        .filter(|line| !line.trim().is_empty())
        .count() as u64
        != tools
    {
        return Err(format!("{cell_name} trial {trial}: raw ledger disagrees"));
    }

    let upstream_dir = issue_087_path(root, &format!("{run_rel}/upstream"));
    let raw_upstream = fs::read_dir(&upstream_dir)
        .map_err(|error| format!("cannot read {}: {error}", upstream_dir.display()))?
        .filter_map(Result::ok)
        .filter(|entry| entry.path().extension().and_then(|value| value.to_str()) == Some("json"))
        .count() as u64;
    if raw_upstream != upstream {
        return Err(format!(
            "{cell_name} trial {trial}: raw upstream count disagrees"
        ));
    }

    let request = fs::read_to_string(issue_087_path(
        root,
        &format!("{run_rel}/client-request.http"),
    ))
    .map_err(|error| format!("{cell_name} trial {trial}: no client request: {error}"))?;
    if !request.starts_with("POST /v1/chat/completions HTTP/1.1")
        || !request.contains("\"type\":\"mcp\"")
        || !request.contains("\"require_approval\":\"never\"")
    {
        return Err(format!("{cell_name} trial {trial}: public request changed"));
    }
    let response = fs::read_to_string(issue_087_path(
        root,
        &format!("{run_rel}/client-response.http"),
    ))
    .map_err(|error| format!("{cell_name} trial {trial}: no client response: {error}"))?;
    let status = if cell_name == "control_fault_off" {
        200
    } else {
        500
    };
    if !response.starts_with(&format!("HTTP/1.1 {status} "))
        || run.get("client_status").and_then(Value::as_u64) != Some(status)
    {
        return Err(format!("{cell_name} trial {trial}: client status changed"));
    }
    Ok(())
}

fn validate_issue_087_matrix(
    summary: &Value,
    root: &str,
    version: &str,
    source_ref: &str,
) -> Result<(), String> {
    if summary.get("complete").and_then(Value::as_bool) != Some(true)
        || summary
            .get("failures")
            .and_then(Value::as_array)
            .is_none_or(|v| !v.is_empty())
        || summary.get("runs").and_then(Value::as_u64) != Some(5)
        || summary.pointer("/target/project").and_then(Value::as_str) != Some("BerriAI/litellm")
        || summary
            .pointer("/target/litellm_version")
            .and_then(Value::as_str)
            != Some(version)
        || summary
            .pointer("/target/source_ref")
            .and_then(Value::as_str)
            != Some(source_ref)
        || summary.pointer("/target/endpoint").and_then(Value::as_str)
            != Some("POST /v1/chat/completions")
    {
        return Err("matrix provenance or completion state is invalid".to_owned());
    }

    let cells = summary
        .get("cells")
        .and_then(Value::as_object)
        .ok_or("matrix has no cells object")?;
    if cells.len() != 4 {
        return Err(format!("matrix has {} cells, expected four", cells.len()));
    }

    let expected = [
        ("violation_default_retries", "on", None, 3_u64, 6_u64, true),
        ("violation_num_retries_2", "on", Some(2_u64), 3, 6, true),
        ("control_fault_off", "off", Some(2_u64), 1, 2, false),
        ("control_num_retries_0", "on", Some(0_u64), 1, 2, false),
    ];
    for (cell_name, fault, retries, tools, upstream, violation) in expected {
        let cell = cells
            .get(cell_name)
            .ok_or_else(|| format!("missing cell {cell_name}"))?;
        if cell.pointer("/spec/fault").and_then(Value::as_str) != Some(fault)
            || cell.pointer("/spec/router_retries").and_then(Value::as_u64) != retries
            || cell.pointer("/spec/sdk_retries").and_then(Value::as_u64) != Some(0)
        {
            return Err(format!("{cell_name}: cell specification changed"));
        }
        let runs = cell
            .get("runs")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("{cell_name}: no runs array"))?;
        if runs.len() != 5 {
            return Err(format!(
                "{cell_name}: expected five trials, got {}",
                runs.len()
            ));
        }
        for (index, run) in runs.iter().enumerate() {
            validate_issue_087_trial(root, cell_name, run, index, tools, upstream, violation)?;
        }
        let tool_counts = cell
            .get("tool_executions_per_run")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("{cell_name}: no tool count vector"))?;
        let upstream_counts = cell
            .get("upstream_calls_per_run")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("{cell_name}: no upstream count vector"))?;
        if tool_counts
            .iter()
            .any(|value| value.as_u64() != Some(tools))
            || upstream_counts
                .iter()
                .any(|value| value.as_u64() != Some(upstream))
            || tool_counts.len() != 5
            || upstream_counts.len() != 5
        {
            return Err(format!("{cell_name}: aggregate vectors are inconsistent"));
        }
    }
    Ok(())
}

#[test]
fn litellm_mcp_router_retries_reexecute_the_tool() {
    for (root, version, source_ref) in [
        ("transcripts/087", "1.102.1", "v1.102.1"),
        (
            "transcripts/087/matrix/main-2701e200",
            "1.104.0",
            "2701e2008baee4ed2c16cba7a56ea7f96d6d0981",
        ),
    ] {
        let summary: Value = serde_json::from_str(
            &fs::read_to_string(issue_087_path(root, "results.json"))
                .unwrap_or_else(|error| panic!("{root}: cannot read results: {error}")),
        )
        .unwrap_or_else(|error| panic!("{root}: malformed results JSON: {error}"));
        validate_issue_087_matrix(&summary, root, version, source_ref)
            .unwrap_or_else(|error| panic!("{root}: {error}"));
    }
}

#[test]
fn issue_087_checker_rejects_vacuous_malformed_and_inconsistent_evidence() {
    for ledger in ["", "not-json\n", "{\"seq\":1,\"entry\":\"\"}\n"] {
        assert!(matches!(
            mcp_tool_executes_once(ledger),
            Verdict::Violation(_)
        ));
    }
    assert_eq!(
        mcp_tool_executes_once("{\"seq\":7,\"entry\":\"alpha\"}\n"),
        Verdict::Conformant
    );
    assert!(matches!(
        mcp_tool_executes_once(
            "{\"seq\":7,\"entry\":\"alpha\"}\n{\"seq\":8,\"entry\":\"alpha\"}\n"
        ),
        Verdict::Violation(_)
    ));

    let root = "transcripts/087";
    let mut summary: Value = serde_json::from_str(
        &fs::read_to_string(issue_087_path(root, "results.json")).expect("087 results"),
    )
    .expect("087 summary JSON");
    summary["cells"]["violation_num_retries_2"]["runs"][4]["tool_executions"] =
        serde_json::json!(1);
    assert!(validate_issue_087_matrix(&summary, root, "1.102.1", "v1.102.1").is_err());

    let mut vacuous: Value = serde_json::from_str(
        &fs::read_to_string(issue_087_path(root, "results.json")).expect("087 results"),
    )
    .expect("087 summary JSON");
    vacuous["cells"]["control_fault_off"]["runs"] = serde_json::json!([]);
    assert!(validate_issue_087_matrix(&vacuous, root, "1.102.1", "v1.102.1").is_err());
}

// ---- bug 088: agentgateway Anthropic->OpenAI streaming, tool_calls[].type is null ----
//
// The opening tool-call delta announces "type":"function". Every argument
// continuation delta then re-sends "type":null, because the encoder leaves the
// field None and the upstream async-openai struct has no skip_serializing_if on
// it. openai-python treats `type` as a union tag and overwrites unconditionally,
// so the accumulated tag becomes null and its accumulator asserts.

#[test]
fn agentgateway_openai_stream_toolcall_type_null_violates() {
    let v = openai_stream_toolcall_type_never_null(&fixture("transcripts/088/observed.sse"));
    assert!(
        matches!(&v, Verdict::Violation(reason) if reason.contains("explicit null")),
        "expected an explicit-null violation, got {v:?}"
    );
}

#[test]
fn openai_stream_toolcall_type_absent_is_conformant() {
    // Same stream with the key omitted instead of nulled. An absent key is a
    // different wire value, which is the entire difference between the two.
    assert_eq!(
        openai_stream_toolcall_type_never_null(&fixture("transcripts/088/expected.sse")),
        Verdict::Conformant
    );
}

#[test]
fn openai_passthrough_stream_toolcall_type_is_conformant() {
    // Real OpenAI bytes through the gateway's passthrough route: no conversion,
    // no violation. This is the control that isolates conversion as the trigger.
    assert_eq!(
        openai_stream_toolcall_type_never_null(&fixture("transcripts/088/control-passthrough.sse")),
        Verdict::Conformant
    );
}

#[test]
fn checker_ignores_streams_without_tool_call_deltas() {
    // A plain text stream has no tool_calls to check, so the checker must not
    // report a violation for the absence of the construct.
    let text_stream = concat!(
        "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"hi\"}}]}\n\n",
        "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n",
        "data: [DONE]\n\n"
    );
    assert_eq!(
        openai_stream_toolcall_type_never_null(text_stream),
        Verdict::Conformant
    );
}

/// Bug 091: agentgateway's OpenAI Chat Completions to Anthropic Messages stream
/// translation re-opens a `tool_use` block that it already closed, when the
/// upstream interleaves non-empty text with a tool call's argument deltas.
///
/// `observed.sse` is the recorded client stream from agentgateway v1.5.0. It
/// opens index 0 as `tool_use`, closes it, opens index 1 as text, closes it,
/// then opens index 0 as `tool_use` again. One upstream tool call becomes two
/// client tool calls with a reused index and split arguments.
#[test]
fn agentgateway_reopens_tool_use_block_after_text_interleaves() {
    let v = anthropic_stream_block_lifecycle(&fixture("transcripts/091/rig/observed.sse"));
    assert!(
        matches!(&v, Verdict::Violation(reason) if reason.contains("opened twice")),
        "expected a reused-index violation against the recorded bytes, got {v:?}"
    );
    assert!(
        matches!(&v, Verdict::Violation(reason) if reason.contains("split across 2 tool_use blocks")),
        "the same stream also splits one tool call across two blocks, got {v:?}"
    );
}

/// Control: the empty-content-delta shape fixed upstream in #2147/#2148 is
/// conformant on the same binary and the same route. This is the discrimination
/// between the fixed case and this defect.
#[test]
fn agentgateway_empty_text_delta_between_arguments_is_conformant() {
    assert_eq!(
        anthropic_stream_block_lifecycle(&fixture("transcripts/091/rig/control-empty-delta.sse")),
        Verdict::Conformant
    );
}

/// Bug 094: Dynamo's Anthropic Messages endpoint stops at a requested stop
/// sequence but reports `stop_reason: "end_turn"` and `stop_sequence: null`,
/// unary and streamed. Each record pairs the client response with the text the
/// deterministic engine actually emitted before the frontend cancelled it.
/// Every record must fail on its own, so the capture proves N of N.
fn assert_every_094_record_violates(rel: &str) {
    let capture = fixture(rel);
    let lines: Vec<&str> = capture.lines().filter(|l| !l.trim().is_empty()).collect();
    assert_eq!(
        lines.len(),
        8,
        "{rel} must hold 5 unary and 3 streamed exchanges"
    );
    for (i, line) in lines.iter().enumerate() {
        let v = anthropic_stop_sequence_reported(line);
        assert!(
            matches!(&v, Verdict::Violation(reason) if reason.contains("\"</answer>\"") && reason.contains("end_turn")),
            "{rel} record {i}: expected the dropped </answer> stop to be caught, got {v:?}"
        );
    }
}

#[test]
fn dynamo_messages_stop_sequence_not_reported_violation() {
    assert_every_094_record_violates("transcripts/094/capture-dynamo-bug.jsonl");
}

/// The same defect on the current release, 1.5.0. Its text also ends in a
/// partial `</answer` (a separate leak fixed upstream by #14378), which must not
/// hide the wrong stop report.
#[test]
fn dynamo_release_messages_stop_sequence_not_reported_violation() {
    assert_every_094_record_violates("transcripts/094/capture-dynamo-release-1.5.0-bug.jsonl");
}

/// Length control at the delimiter boundary: with `max_tokens` equal to the
/// tokens before `</answer>`, the engine stops on length without emitting the
/// delimiter and Dynamo correctly reports `max_tokens`, on both versions.
#[test]
fn dynamo_messages_length_stop_at_delimiter_boundary_is_conformant() {
    assert_eq!(
        anthropic_stop_sequence_reported(&fixture(
            "transcripts/094/capture-dynamo-length-boundary.jsonl"
        )),
        Verdict::Conformant
    );
}

/// Provider control: the live Anthropic Messages API, same request and same
/// returned text, reports `stop_reason: "stop_sequence"` and names `</answer>`.
#[test]
fn anthropic_live_stop_sequence_report_is_conformant() {
    assert_eq!(
        anthropic_stop_sequence_reported(&fixture(
            "transcripts/094/capture-provider-control.jsonl"
        )),
        Verdict::Conformant
    );
}

/// Trigger-removed control: the same Dynamo frontend and generation with no
/// `stop_sequences` runs to its natural end and correctly reports `end_turn`,
/// on both versions.
#[test]
fn dynamo_messages_without_stop_sequences_is_conformant() {
    assert_eq!(
        anthropic_stop_sequence_reported(&fixture(
            "transcripts/094/capture-dynamo-trigger-removed.jsonl"
        )),
        Verdict::Conformant
    );
}

// ---- bug 095: NIXL's len==0 registration is invisible to the transfer path ----
//
// Recorded from ai-dynamo/nixl `main` = 464c86388d2c6373ab922d852fa245d81bfb5fb6,
// POSIX backend on the kernel AIO queue, real file, real nixlAgent. No mocks.

fn cells_095(cell: &str) -> Vec<String> {
    (1..=10)
        .map(|r| fixture(&format!("transcripts/095/cells/{cell}-round{r:02}.json")))
        .collect()
}

/// The claim: a FILE_SEG registered with `len == 0` at a nonzero offset is
/// accepted and deregisterable, but no transfer can resolve it. 10 of 10.
#[test]
fn nixl_len0_registration_at_nonzero_offset_is_unresolvable() {
    let trials = cells_095("B_len0_offset4096");
    assert_eq!(trials.len(), 10);
    for (i, trial) in trials.iter().enumerate() {
        let v = registration_resolvable_by_both_paths(trial);
        assert!(
            matches!(&v, Verdict::Violation(reason) if reason.contains("can be deregistered")),
            "round {}: {v:?}",
            i + 1
        );
    }
}

/// Control A: the same `len == 0` registration at offset 0. The wrapped add
/// cannot wrap at zero, so the transfer resolves and the bytes are verified on
/// disk. Offset is the only variable.
#[test]
fn nixl_len0_registration_at_offset_zero_resolves() {
    for trial in cells_095("A_len0_offset0") {
        assert_eq!(
            registration_resolvable_by_both_paths(&trial),
            Verdict::Conformant
        );
    }
}

/// Control B: the same offset with an explicit length. Length is the only
/// variable relative to the failing cell.
#[test]
fn nixl_explicit_len_at_the_same_offset_resolves() {
    for trial in cells_095("C_len4096_offset4096") {
        assert_eq!(
            registration_resolvable_by_both_paths(&trial),
            Verdict::Conformant
        );
    }
}

/// The `covered` flag the checker relies on must be recomputable from the
/// fixture, not taken on trust. Every recorded cell's transfer range must lie
/// inside its registration when `covered` is true, and `reg_end` must be the
/// unbounded form exactly when the registration was made with `len == 0`.
#[test]
fn nixl_recorded_cells_self_certify_their_containment() {
    for cell in [
        "A_len0_offset0",
        "B_len0_offset4096",
        "C_len4096_offset4096",
    ] {
        for trial in cells_095(cell) {
            let v: Value = serde_json::from_str(&trial).expect("cell record is JSON");
            let offset = v["offset"].as_u64().expect("offset");
            let xfer_end = v["xfer_end"].as_u64().expect("xfer_end");
            let reg_len = v["reg_len"].as_u64().expect("reg_len");
            let unbounded = v["reg_end"].as_str() == Some("SIZE_MAX");
            assert_eq!(unbounded, reg_len == 0, "{cell}: len 0 must mean unbounded");
            let reg_end = if unbounded {
                u64::MAX
            } else {
                offset + reg_len
            };
            assert_eq!(
                v["covered"].as_bool(),
                Some(offset <= xfer_end && xfer_end <= reg_end),
                "{cell}: covered must equal the containment the addresses imply"
            );
        }
    }
}

/// The asymmetry is the whole finding: the failing cell is still deregisterable.
#[test]
fn nixl_unresolvable_registration_is_still_deregisterable() {
    for trial in cells_095("D_len0_offset4096_dereg_only") {
        let v: Value = serde_json::from_str(&trial).expect("cell record is JSON");
        assert_eq!(
            v["register"].as_i64(),
            Some(0),
            "the registration was accepted"
        );
        assert_eq!(
            v["deregister"].as_i64(),
            Some(0),
            "and it can be removed again"
        );
        assert_eq!(v["createXferReq"].as_i64(), Some(-4), "but never resolved");
    }
}

/// The checker must not turn a correct refusal into a finding, and must not
/// accept records that cannot support a conclusion.
#[test]
fn nixl_registration_evidence_must_be_sound() {
    // Out of range, and never registered: both are correct refusals.
    for cell in [
        r#"{"register":0,"createXferReq":-4,"covered":false,"deregister":0}"#,
        r#"{"register":-3,"createXferReq":-4,"covered":true,"deregister":-4}"#,
    ] {
        assert_eq!(
            registration_resolvable_by_both_paths(cell),
            Verdict::Conformant,
            "{cell}"
        );
    }
    // Vacuous or malformed records must not pass as evidence either way.
    for bad in [
        "",
        "[]",
        r#"{"register":0,"deregister":0}"#,
        r#"{"register":0,"createXferReq":-4,"covered":"yes","deregister":0}"#,
    ] {
        assert!(
            matches!(
                registration_resolvable_by_both_paths(bad),
                Verdict::Violation(_)
            ),
            "{bad:?} must not pass as evidence"
        );
    }
}

// ---- bug 097: llm-d Router coordinator rounds large schema integers ----
//
// The coordinator decodes the request body into map[string]any (Go's
// encoding/json, no UseNumber) and re-marshals it for the render, prefill and
// decode legs. Every integer above 2^53 is therefore rounded before any engine
// sees it. The writeup pins handler line 125 at 567e35d752c8.

fn issue_095_capture(relative: &str) -> String {
    fixture(&format!("transcripts/097/capture/{relative}"))
}

#[test]
fn llm_d_coordinator_rounds_the_schema_integer_on_every_leg() {
    // Every leg the coordinator dispatches carries the rounded value, so the
    // defect is in the parse, not in one step's edit.
    for leg in ["render", "prefill", "decode"] {
        let verdict = forwarded_body_preserves_values(
            &issue_095_capture("large-01/client-request.http"),
            &issue_095_capture(&format!("large-01/{leg}-request.http")),
            &[
                "kv_transfer_params",
                "max_completion_tokens",
                "max_tokens",
                "stream",
            ],
        );
        assert!(
            matches!(&verdict, Verdict::Violation(reason) if reason.contains("client sent 9007199254740993, forwarded 9007199254740992")),
            "{leg} leg must report the rounding, got {verdict:?}"
        );
    }
}

#[test]
fn llm_d_coordinator_small_integer_control_preserves_the_value() {
    // Same request, same route, same coordinator, identifier 42: the value
    // survives, so the trigger is the magnitude and nothing else.
    assert_eq!(
        forwarded_body_preserves_values(
            &issue_095_capture("safe-01/client-request.http"),
            &issue_095_capture("safe-01/render-request.http"),
            &[]
        ),
        Verdict::Conformant
    );
}

#[test]
fn llm_d_coordinator_removed_from_the_path_preserves_the_value() {
    // The differential control: the same request sent straight to the upstream.
    // Identical bytes arrive with the integer intact.
    assert_eq!(
        forwarded_body_preserves_values(
            &issue_095_capture("large-01/client-request.http"),
            &issue_095_capture("direct-01/direct-request.http"),
            &[]
        ),
        Verdict::Conformant
    );
}

#[test]
fn llm_d_coordinator_prefill_leg_declares_only_its_transfer_fields() {
    // The prefill leg adds disaggregation metadata and caps the token budget.
    // Declared, that is conformant; undeclared, the same additions fail, so an
    // undisclosed field change still gets caught.
    let client = issue_095_capture("safe-02/client-request.http");
    let prefill = issue_095_capture("safe-02/prefill-request.http");
    let allowed = [
        "kv_transfer_params",
        "max_completion_tokens",
        "max_tokens",
        "stream",
    ];
    assert_eq!(
        forwarded_body_preserves_values(&client, &prefill, &allowed),
        Verdict::Conformant
    );
    assert!(matches!(
        forwarded_body_preserves_values(&client, &prefill, &[]),
        Verdict::Violation(reason) if reason.contains("was added")
    ));
}

#[test]
fn llm_d_coordinator_five_run_matrix_matches_the_recorded_capture() {
    let summary: Value = serde_json::from_str(&issue_095_capture("results.json"))
        .expect("095 summary must be valid JSON");
    assert_eq!(
        summary.get("pin").and_then(Value::as_str),
        Some("567e35d752c841521fd7c450e8335e5dae108a7c"),
        "the capture must be pinned to the reviewed commit"
    );
    let runs = summary
        .get("results")
        .and_then(Value::as_array)
        .expect("results array");
    assert_eq!(runs.len(), 15, "five runs per case across three cases");
    let mut corrupted = 0_usize;
    let mut clean = 0_usize;
    for run in runs {
        let case = run.get("case").and_then(Value::as_str).expect("case");
        let index = run.get("run").and_then(Value::as_u64).expect("run");
        let trial = format!("{case}-{index:02}");
        let submitted = run.get("submitted_id").and_then(Value::as_u64);
        let returned = run.get("returned_id").and_then(Value::as_u64);
        let errors = run
            .get("consumer_validation_errors")
            .and_then(Value::as_array)
            .expect("consumer_validation_errors");
        match case {
            "large" => {
                assert_eq!(submitted, Some(9_007_199_254_740_993), "{trial}: input");
                assert_eq!(returned, Some(9_007_199_254_740_992), "{trial}: returned");
                assert!(
                    errors.iter().any(|e| e
                        .as_str()
                        .is_some_and(|m| m.contains("is not one of [9007199254740993]"))),
                    "{trial}: the client's own validator must reject the rounded value"
                );
                for leg in ["render", "prefill", "decode"] {
                    let verdict = forwarded_body_preserves_values(
                        &issue_095_capture(&format!("{trial}/client-request.http")),
                        &issue_095_capture(&format!("{trial}/{leg}-request.http")),
                        &[
                            "kv_transfer_params",
                            "max_completion_tokens",
                            "max_tokens",
                            "stream",
                        ],
                    );
                    assert!(
                        matches!(verdict, Verdict::Violation(_)),
                        "{trial}: {leg} leg must violate, got {verdict:?}"
                    );
                }
                corrupted += 1;
            }
            "safe" | "direct" => {
                assert_eq!(submitted, returned, "{trial}: value must survive");
                assert!(errors.is_empty(), "{trial}: no validation errors");
                let legs: &[&str] = if case == "safe" {
                    &["render", "prefill", "decode"]
                } else {
                    &["direct"]
                };
                for leg in legs {
                    let verdict = forwarded_body_preserves_values(
                        &issue_095_capture(&format!("{trial}/client-request.http")),
                        &issue_095_capture(&format!("{trial}/{leg}-request.http")),
                        &[
                            "kv_transfer_params",
                            "max_completion_tokens",
                            "max_tokens",
                            "stream",
                        ],
                    );
                    assert_eq!(
                        verdict,
                        Verdict::Conformant,
                        "{trial}: {leg} leg must be conformant"
                    );
                }
                clean += 1;
            }
            other => panic!("unexpected case {other}"),
        }
    }
    assert_eq!(corrupted, 5, "5/5 large runs corrupted");
    assert_eq!(clean, 10, "5/5 safe and 5/5 direct runs preserved");
}

#[test]
fn llm_d_coordinator_capture_is_deterministic_across_runs() {
    // The rounding is a decode-time constant, not a race: every run forwarded
    // the same rounded value on the same legs.
    for run in 1..=5 {
        for leg in ["render", "prefill", "decode"] {
            let first = issue_095_capture(&format!("large-01/{leg}-request.http"));
            let other = issue_095_capture(&format!("large-{run:02}/{leg}-request.http"));
            let body = |raw: &str| raw.split_once("\r\n\r\n").unwrap().1.to_owned();
            assert_eq!(body(&first), body(&other), "run {run} {leg} leg drifted");
        }
    }
}

/// Whether a `float64` can hold this integer exactly. Every integer below 2^53
/// is exact; above it the ulp doubles at each power of two, so only multiples of
/// the spacing survive.
fn representable_in_f64(value: u64) -> bool {
    if value == 0 {
        return true;
    }
    let exponent = value.ilog2();
    if exponent < 53 {
        return true;
    }
    value.is_multiple_of(1_u64 << (exponent - 52))
}

#[test]
fn llm_d_coordinator_forwarded_values_match_float64_exactly() {
    // The trigger is the magnitude, not the value: the coordinator forwards
    // whatever a float64 decode produces, so a value only survives when a float64
    // can hold it. The boundary sweep pins that rule from both sides. 2^53+2
    // survives and 2^53+1 does not, which is the signature of a float64 decode
    // rather than a truncation or a format change.
    //
    // The forwarded values are compared as digit strings, not as numbers: a value
    // above 2^63 no longer round-trips through a typed JSON number, and a typed
    // comparison would report two rounded values as equal.
    let sweep = fixture("transcripts/097/capture/boundary/boundary.jsonl");
    let mut rows = 0_usize;
    let mut rounded = 0_usize;
    for line in sweep.lines().filter(|line| !line.trim().is_empty()) {
        let row: Value = serde_json::from_str(line).expect("boundary row");
        let submitted = row
            .get("submitted")
            .and_then(Value::as_u64)
            .expect("submitted");
        let rendered = line
            .split("\"render\": ")
            .nth(1)
            .and_then(|rest| rest.split(',').next())
            .expect("rendered value");
        if representable_in_f64(submitted) {
            assert_eq!(
                submitted.to_string(),
                rendered,
                "{submitted} is representable as a float64 and must survive"
            );
        } else {
            assert!(
                submitted.to_string() != rendered,
                "{submitted} is not representable as a float64 and must be reported as rounded"
            );
            rounded += 1;
        }
        rows += 1;
    }
    assert!(
        rows >= 8,
        "the sweep must cover both sides of 2^53, got {rows}"
    );
    assert!(
        rounded >= 4,
        "the sweep must show rounding too, got {rounded}"
    );
}

// ---- bug 100: Switchyard plan_execute rebuilds Anthropic server tools ----
//
// Plan/execute prepends its planning instruction on every planning turn, which
// drops same-format exact replay. The Anthropic codec then re-encodes the tool
// list from the normalized request, where a server tool such as
// `web_search_20250305` is a function with an empty schema. Anthropic rejects
// the result, and Claude Code's WebSearch returns an API error. Captures were
// recorded through rig/relay.py on both hops, against live Anthropic.

const ISSUE_100_BUILDS: [&str; 2] = ["main-ab7d2644", "v0.3.0"];
const ISSUE_100_REJECTION: &str = "tools.0.custom.input_schema.type: Field required";
const ISSUE_100_WEBSEARCH: &str = "Perform a web search for the query";

fn issue_100_capture(relative: &str) -> String {
    fixture(&format!("transcripts/100/capture/{relative}"))
}

fn issue_100_status(raw_http: &str) -> u16 {
    raw_http
        .split(' ')
        .nth(1)
        .and_then(|status| status.parse().ok())
        .expect("status line")
}

/// The single client and upstream exchange in a minimal or direct case.
fn issue_100_exchange(case: &str, hop: &str) -> (String, String) {
    (
        issue_100_capture(&format!("{case}/{hop}/001-request.http")),
        issue_100_capture(&format!("{case}/{hop}/001-response.http")),
    )
}

/// The Claude Code WebSearch sub-request in one hop: the only request whose
/// messages ask for a web search.
fn issue_100_websearch(run: &str, hop: &str) -> (String, String) {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    dir.push(format!(
        "../../transcripts/100/capture/main-ab7d2644/claude/{run}/{hop}"
    ));
    let mut found: Vec<(String, String)> = Vec::new();
    let mut requests: Vec<PathBuf> = fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("read {}: {e}", dir.display()))
        .map(|entry| entry.expect("dir entry").path())
        .filter(|path| path.to_string_lossy().ends_with("-request.http"))
        .collect();
    requests.sort();
    for request in requests {
        let raw = fs::read_to_string(&request).expect("request");
        if raw.contains(ISSUE_100_WEBSEARCH) {
            let response = request
                .to_string_lossy()
                .replace("-request.http", "-response.http");
            found.push((raw, fs::read_to_string(response).expect("response")));
        }
    }
    assert_eq!(found.len(), 1, "{run}/{hop}: one WebSearch sub-request");
    found.remove(0)
}

#[test]
fn anthropic_rejects_the_tool_switchyard_forwards_and_accepts_the_original() {
    // Rung 1 of the control ladder, no gateway: the provider runs the server
    // tool, and rejects the custom-function shape Switchyard sends in its place.
    for run in 1..=5 {
        let (_, served) = issue_100_exchange(
            &format!("main-ab7d2644/direct/server-tool-{run:02}"),
            "upstream",
        );
        assert_eq!(issue_100_status(&served), 200, "server tool run {run}");
        assert!(
            served.contains("web_search_tool_result"),
            "server tool run {run} must search"
        );
        let (_, rejected) = issue_100_exchange(
            &format!("main-ab7d2644/direct/reencoded-tool-{run:02}"),
            "upstream",
        );
        assert_eq!(
            issue_100_status(&rejected),
            400,
            "re-encoded tool run {run}"
        );
        assert!(
            rejected.contains(ISSUE_100_REJECTION),
            "re-encoded tool run {run}"
        );
    }
}

#[test]
fn switchyard_plan_execute_rebuilds_the_web_search_server_tool() {
    for build in ISSUE_100_BUILDS {
        for run in 1..=5 {
            let case = format!("{build}/minimal/sy-plan-{run:02}");
            let (client, client_response) = issue_100_exchange(&case, "client");
            let (forwarded, upstream_response) = issue_100_exchange(&case, "upstream");
            assert!(
                matches!(
                    anthropic_server_tools_forwarded(&client, &forwarded),
                    Verdict::Violation(reason)
                        if reason.contains("web_search_20250305") && reason.contains("\"input_schema\":{}")
                ),
                "{case}: the server tool must be reported as rebuilt"
            );
            assert_eq!(issue_100_status(&upstream_response), 400, "{case}");
            assert_eq!(issue_100_status(&client_response), 400, "{case}");
            assert!(client_response.contains(ISSUE_100_REJECTION), "{case}");
        }
    }
}

#[test]
fn switchyard_passthrough_forwards_the_web_search_server_tool_unchanged() {
    // Same server, same target, same request; only the router-added planning
    // instruction is absent, so exact replay survives.
    for build in ISSUE_100_BUILDS {
        for run in 1..=5 {
            let case = format!("{build}/minimal/sy-pass-{run:02}");
            let (client, client_response) = issue_100_exchange(&case, "client");
            let (forwarded, _) = issue_100_exchange(&case, "upstream");
            assert_eq!(
                anthropic_server_tools_forwarded(&client, &forwarded),
                Verdict::Conformant,
                "{case}"
            );
            assert_eq!(issue_100_status(&client_response), 200, "{case}");
            assert!(client_response.contains("web_search_tool_result"), "{case}");
        }
    }
}

#[test]
fn claude_code_websearch_fails_only_on_the_plan_execute_route() {
    for run in 1..=5 {
        let plan = format!("sy-plan-{run:02}");
        let (client, _) = issue_100_websearch(&plan, "client");
        let (forwarded, upstream_response) = issue_100_websearch(&plan, "upstream");
        assert!(
            !anthropic_server_tools_forwarded(&client, &forwarded).is_conformant(),
            "{plan}: Claude Code's server tool must be reported as rebuilt"
        );
        assert_eq!(issue_100_status(&upstream_response), 400, "{plan}");
        let stdout = issue_100_capture(&format!("main-ab7d2644/claude/{plan}/claude-stdout.json"));
        assert!(
            stdout.contains("input_schema.type: Field required"),
            "{plan}: Claude Code reports the rejection"
        );

        let pass = format!("sy-pass-{run:02}");
        let (client, _) = issue_100_websearch(&pass, "client");
        let (forwarded, upstream_response) = issue_100_websearch(&pass, "upstream");
        assert_eq!(
            anthropic_server_tools_forwarded(&client, &forwarded),
            Verdict::Conformant,
            "{pass}"
        );
        assert_eq!(issue_100_status(&upstream_response), 200, "{pass}");
        assert!(
            upstream_response.contains("web_search_tool_result"),
            "{pass}: search ran"
        );
    }
}

#[test]
fn switchyard_server_tool_matrix_matches_the_recorded_runs() {
    for (build, commit, claude) in [
        (
            "main-ab7d2644",
            "ab7d26443899239632e97d9d9d8aedaa58c96b99",
            true,
        ),
        ("v0.3.0", "336196f6fbfc97ddc71c1700f6092e564e9f23c2", false),
    ] {
        let results: Value =
            serde_json::from_str(&issue_100_capture(&format!("{build}/results.json")))
                .expect("results.json");
        assert_eq!(results["identity"]["switchyard_commit"], commit, "{build}");
        assert_eq!(
            results["identity"]["switchyard_tree_clean"], true,
            "{build}"
        );
        let summary = &results["summary"];
        assert_eq!(summary["runs_per_case"], 5, "{build}");
        for key in [
            "direct_server_tool_ok",
            "direct_reencoded_tool_400",
            "plan_forwarded_custom_tool_400",
            "pass_forwarded_server_tool_ok",
        ] {
            assert_eq!(summary[key], 5, "{build}: {key}");
        }
        if claude {
            assert_eq!(summary["claude_plan_websearch_failed"], 5, "{build}");
            assert_eq!(summary["claude_pass_websearch_ok"], 5, "{build}");
        }
    }
}

// ---- bug 101: Switchyard erases Responses host tools on a cross-format route ----

use kairo::checks::{host_tool_not_silently_erased, HOST_TOOL_SILENTLY_ERASED};

/// Reads a `capture-<case>.jsonl` file recorded by
/// `transcripts/101-switchyard-responses-builtin-tools-erased/run_repro.sh` and
/// checks every run with one checker call. The expected tool list is read from
/// the client's own request bytes, never from a field the transcript recorded.
fn assert_case(case: &str, conformant: bool) {
    let capture = fixture(&format!(
        "transcripts/101-switchyard-responses-builtin-tools-erased/capture-{case}.jsonl"
    ));
    let records: Vec<Value> = capture
        .lines()
        .map(|line| serde_json::from_str(line).expect("101 capture line is JSON"))
        .collect();
    assert!(!records.is_empty(), "{case} captured no runs");
    for record in &records {
        let status = u16::try_from(
            record["client_status"]
                .as_u64()
                .expect("101 status is a number"),
        )
        .expect("101 status fits in u16");
        let client = record["client_request"]
            .as_str()
            .expect("101 client request");
        let forwarded = record["forwarded_raw"]
            .as_str()
            .expect("101 forwarded body");
        let verdict = host_tool_not_silently_erased(status, client, forwarded);

        // Host tools in this client's request, derived from the request alone.
        let declared: Vec<String> = serde_json::from_str::<Value>(client)
            .expect("101 client request is JSON")
            .get("tools")
            .and_then(Value::as_array)
            .expect("101 client request has tools")
            .iter()
            .filter_map(|tool| tool.get("type").and_then(Value::as_str))
            .filter(|kind| !matches!(*kind, "function" | "custom"))
            .map(str::to_owned)
            .collect();

        if conformant {
            assert!(
                verdict.is_conformant(),
                "{case} run {} should satisfy the invariant, got {verdict:?}",
                record["run"]
            );
        } else {
            assert_eq!(
                verdict,
                Verdict::Violation(format!(
                    "{HOST_TOOL_SILENTLY_ERASED}: {declared:?} absent from the forwarded tools array"
                )),
                "{case} run {} should violate the invariant for {declared:?}",
                record["run"]
            );
            // The frozen failure is silent: the caller is told the call succeeded.
            assert_eq!(status, 200);
            assert!(
                !forwarded.contains("\"tools\"") || record["forwarded_tools"].is_null(),
                "the forwarded body must not reintroduce the erased tool"
            );
        }
    }
}

#[test]
fn switchyard_101_erases_mcp_tool_on_cross_format_routes() {
    assert_case("bug-mcp-chat", false);
    assert_case("bug-mcp-anth", false);
}

#[test]
fn switchyard_101_erases_every_responses_host_tool_it_cannot_express() {
    for case in [
        "bug-web-search-chat",
        "bug-file-search-chat",
        "bug-code-interpreter-chat",
        "bug-computer-use-chat",
        "bug-image-generation-chat",
        "bug-local-shell-chat",
        "bug-mcp-chat-stream",
    ] {
        assert_case(case, false);
    }
}

#[test]
fn switchyard_101_same_dialect_route_keeps_the_tool() {
    // The same client request, differing only in the `model` field that
    // selects the route, on the Responses-dialect route.
    for case in [
        "control-same-dialect-mcp",
        "control-same-dialect-computer-use",
        "control-same-dialect-image-generation",
        "control-same-dialect-web-search",
    ] {
        assert_case(case, true);
    }
}

#[test]
fn switchyard_101_function_tools_survive_the_same_routes() {
    // The hop itself is not lossy: only tools with no function shape disappear.
    assert_case("control-function-chat", true);
    assert_case("control-function-anth", true);
}

#[test]
fn switchyard_101_a_host_tool_with_an_id_becomes_an_empty_function() {
    // The erasure is a decode decision, not a filter on tool type: give the
    // same tool an `id` and it survives as a function nobody can run.
    let capture = fixture(
        "transcripts/101-switchyard-responses-builtin-tools-erased/capture-data-mcp-with-id-chat.jsonl",
    );
    for line in capture.lines() {
        let record: Value = serde_json::from_str(line).expect("101 capture line is JSON");
        let tools = record["forwarded_tools"]
            .as_array()
            .expect("101 forwarded tools array");
        assert_eq!(tools.len(), 1);
        assert_eq!(tools[0]["function"]["name"], "codex-tool-x1");
        assert_eq!(tools[0]["function"]["parameters"], serde_json::json!({}));
        assert!(
            !record["forwarded_raw"]
                .as_str()
                .expect("101 forwarded body")
                .contains("example.invalid"),
            "the MCP server URL must not survive into a function tool"
        );
    }
}

// ---- bug 102: Switchyard stamps an MCP namespace onto an un-namespaced tool ----

#[test]
fn switchyard_codex_namespace_hijacks_a_top_level_tool() {
    // The request declares a top-level `search` and a namespace holding its own
    // `search`. The backend was offered both, called the top-level one, and the
    // returned call must not have gained a namespace.
    for kind in ["buffered", "stream"] {
        let capture = fixture(&format!("transcripts/102/capture-bug-{kind}.jsonl"));
        let verdict = codex_namespace_only_applies_to_namespaced_tools(&capture, 5);
        assert!(
            matches!(verdict, Verdict::Violation(_)),
            "{kind}: {verdict:?}"
        );

        let records: Vec<Value> = capture
            .lines()
            .map(|line| serde_json::from_str(line).expect("102 bug capture is JSON"))
            .collect();
        assert_eq!(records.len(), 5, "{kind}");
        assert!(records.iter().all(|record| {
            record["called_upstream_name"] == "search"
                && record["call_names"] == serde_json::json!(["search"])
                && record["call_namespaces"] == serde_json::json!(["mcp__docs__"])
        }));
    }
}

#[test]
fn switchyard_codex_namespace_same_tool_set_valid_restore_is_conformant() {
    // The pair a valid restore produces is byte-identical to the hijack:
    // {"name": "search", "namespace": "mcp__docs__"}. Only the name the backend
    // was recorded calling separates them, so the checker must read the
    // forwarded record rather than the response alone. Without this case the
    // checker would flag correct gateway behaviour.
    for kind in ["buffered", "stream"] {
        let capture = fixture(&format!(
            "transcripts/102/capture-cross-qualified-{kind}.jsonl"
        ));
        let records: Vec<Value> = capture
            .lines()
            .map(|line| serde_json::from_str(line).expect("102 cross-qualified is JSON"))
            .collect();
        assert_eq!(records.len(), 5, "{kind}");
        assert!(records.iter().all(|record| {
            record["called_upstream_name"] == "mcp__docs____search"
                && record["call_names"] == serde_json::json!(["search"])
                && record["call_namespaces"] == serde_json::json!(["mcp__docs__"])
        }));
        assert_eq!(
            codex_namespace_only_applies_to_namespaced_tools(&capture, 5),
            Verdict::Conformant,
            "cross-qualified {kind}"
        );
    }
}

#[test]
fn switchyard_codex_namespace_trigger_removed_controls_are_conformant() {
    // Same gateway, same route. Only the tool set changes.
    for kind in ["buffered", "stream"] {
        // Namespace container only: the qualified call restores correctly.
        assert_eq!(
            codex_namespace_only_applies_to_namespaced_tools(
                &fixture(&format!(
                    "transcripts/102/capture-control-qualified-{kind}.jsonl"
                )),
                5,
            ),
            Verdict::Conformant,
            "control-qualified {kind}"
        );
        // Namespace container removed: nothing may come back namespaced.
        let local = fixture(&format!(
            "transcripts/102/capture-control-local-only-{kind}.jsonl"
        ));
        assert_eq!(
            codex_namespace_only_applies_to_namespaced_tools(&local, 5),
            Verdict::Conformant,
            "control-local-only {kind}"
        );
        let records: Vec<Value> = local
            .lines()
            .map(|line| serde_json::from_str(line).expect("102 local control is JSON"))
            .collect();
        assert!(records
            .iter()
            .all(|record| record["called_upstream_name"] == "search"
                && record["call_namespaces"] == serde_json::json!([])));
    }
}

#[test]
fn switchyard_codex_namespace_forwarded_bytes_offered_two_distinct_tools() {
    // Proves the backend chose the top-level tool rather than the namespaced one,
    // and that the two stayed distinct upstream with different descriptions.
    let forwarded = fixture("transcripts/102/forwarded.jsonl");
    let records: Vec<Value> = forwarded
        .lines()
        .map(|line| serde_json::from_str(line).expect("102 forwarded is JSON"))
        .collect();
    assert!(
        !records.is_empty(),
        "102 forwarded capture recorded no requests"
    );
    assert!(records.iter().any(|record| {
        record["called_upstream_name"] == "search"
            && record["tool_names_offered_upstream"]
                == serde_json::json!(["search", "mcp__docs____search"])
    }));
    for record in &records {
        let body: Value = serde_json::from_str(record["body_raw"].as_str().unwrap())
            .expect("102 forwarded body is JSON");
        for tool in body["tools"].as_array().expect("102 forwarded tools") {
            let name = tool
                .pointer("/function/name")
                .and_then(Value::as_str)
                .unwrap_or("");
            let description = tool
                .pointer("/function/description")
                .and_then(Value::as_str)
                .unwrap_or("");
            match name {
                "search" => assert_eq!(description, "LOCAL ripgrep over the working tree"),
                "mcp__docs____search" => {
                    assert_eq!(description, "REMOTE documentation search");
                }
                other => panic!("unexpected upstream tool {other:?}"),
            }
        }
    }
}

#[test]
fn switchyard_codex_namespace_checker_rejects_evidence_without_the_upstream_call() {
    // Without the recorded upstream call name the checker cannot tell a hijack
    // from a valid restore, so it must refuse rather than guess.
    let capture = fixture("transcripts/102/capture-bug-buffered.jsonl");
    let stripped: String = capture
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            let mut record: Value = serde_json::from_str(line).expect("102 capture is JSON");
            record
                .as_object_mut()
                .expect("102 record is an object")
                .remove("called_upstream_name");
            record.to_string()
        })
        .collect::<Vec<String>>()
        .join("\n");
    assert!(matches!(
        codex_namespace_only_applies_to_namespaced_tools(&stripped, 5),
        Verdict::Violation(_)
    ));
}

#[test]
fn switchyard_codex_namespace_hijack_is_not_confined_to_cross_format() {
    // Same claim on a Responses -> Responses passthrough route, so the defect is
    // not mistaken for a translation artifact between dialects.
    let bug = fixture("transcripts/102/capture-same-format-bug.jsonl");
    assert!(matches!(
        codex_namespace_only_applies_to_namespaced_tools(&bug, 5),
        Verdict::Violation(_)
    ));
    let records: Vec<Value> = bug
        .lines()
        .map(|line| serde_json::from_str(line).expect("102 same-format bug is JSON"))
        .collect();
    assert!(records
        .iter()
        .all(|record| record["called_upstream_name"] == "search"
            && record["call_namespaces"] == serde_json::json!(["mcp__docs__"])));

    // The same tool set with the qualified call is a valid restore.
    assert_eq!(
        codex_namespace_only_applies_to_namespaced_tools(
            &fixture("transcripts/102/capture-same-format-cross-qualified.jsonl"),
            5,
        ),
        Verdict::Conformant
    );
    // And with the namespace container removed, nothing comes back namespaced.
    assert_eq!(
        codex_namespace_only_applies_to_namespaced_tools(
            &fixture("transcripts/102/capture-same-format-control-local-only.jsonl"),
            5,
        ),
        Verdict::Conformant
    );
}

// ---- bug 103: Bifrost drops the namespace of a call to a searched-in tool ----
//
// On /anthropic/v1/messages to OpenAI, Bifrost rewrites a client tool search
// (Claude Code's ToolSearch) or Anthropic's server-side tool search into OpenAI
// tool_search items. When OpenAI returns the model's call to the loaded tool
// with a `namespace`, an Anthropic `tool_use` has no field for it, and Bifrost
// rebuilds the replayed call without one, so OpenAI rejects the next turn.
// GPT-5.4 and GPT-5.5 set the namespace on both search paths. The four GPT-6
// models tested set it only on the server-side path, so Claude Code's client
// search works there. Captures were recorded through rig/reproduce.py on both
// hops, against live OpenAI.

const ISSUE_103_DEV: &str = "dev-6c0f795d";
const ISSUE_103_HEAD: &str = "capture-9bcea677";
const ISSUE_103_GPT6: [&str; 4] = ["gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-6-astra"];
const ISSUE_103_TOOL: &str = "mcp__orders__lookup_order";
const ISSUE_103_REJECTION: &str = "Missing namespace for function_call 'mcp__orders__lookup_order'";
const ISSUE_103_CANARY: &str = "SHIPPED-CANARY-7731";

fn issue_103_capture(relative: &str) -> String {
    fixture(&format!("transcripts/103/{relative}"))
}

fn issue_103_status(raw_http: &str) -> u16 {
    raw_http
        .split(' ')
        .nth(1)
        .and_then(|status| status.parse().ok())
        .expect("status line")
}

fn issue_103_body(raw_http: &str) -> &str {
    raw_http
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .expect("http body")
}

/// Every exchange on one hop of a case, in order, as (request, response).
fn issue_103_exchanges(case: &str, hop: &str) -> Vec<(String, String)> {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    dir.push(format!("../../transcripts/103/{case}/{hop}"));
    let mut requests: Vec<PathBuf> = fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("read {}: {e}", dir.display()))
        .map(|entry| entry.expect("dir entry").path())
        .filter(|path| path.to_string_lossy().ends_with("-request.http"))
        .collect();
    requests.sort();
    requests
        .into_iter()
        .map(|request| {
            let response = request
                .to_string_lossy()
                .replace("-request.http", "-response.http");
            (
                fs::read_to_string(&request).expect("request"),
                fs::read_to_string(response).expect("response"),
            )
        })
        .collect()
}

/// The `POST /v1/responses` requests Bifrost sent to OpenAI in one case.
fn issue_103_forwarded(case: &str) -> Vec<(String, String)> {
    issue_103_exchanges(case, "upstream")
        .into_iter()
        .filter(|(request, _)| request.starts_with("POST /v1/responses "))
        .collect()
}

/// The first forwarded request that hands a tool result back, the provider
/// responses before it, and the provider's answer to it.
fn issue_103_replay(case: &str) -> (Vec<String>, String, String) {
    let forwarded = issue_103_forwarded(case);
    let at = forwarded
        .iter()
        .position(|(request, _)| request.contains("\"function_call_output\""))
        .unwrap_or_else(|| panic!("{case}: no forwarded tool result"));
    assert!(at > 0, "{case}: the tool result follows a provider turn");
    let earlier = forwarded[..at]
        .iter()
        .map(|(_, response)| issue_103_body(response).to_string())
        .collect();
    let (request, response) = forwarded[at].clone();
    (earlier, issue_103_body(&request).to_string(), response)
}

fn issue_103_judge(case: &str) -> (Verdict, String) {
    let (earlier, request, response) = issue_103_replay(case);
    let earlier: Vec<&str> = earlier.iter().map(String::as_str).collect();
    (
        replayed_calls_keep_returned_namespace(&earlier, &request),
        response,
    )
}

/// The namespaces OpenAI set on the model's calls to the order tool.
fn issue_103_returned_namespaces(raw_http: &str) -> Vec<String> {
    fn walk(value: &Value, out: &mut Vec<String>) {
        match value {
            Value::Array(items) => items.iter().for_each(|item| walk(item, out)),
            Value::Object(map) => {
                if map.get("type").and_then(Value::as_str) == Some("function_call")
                    && map.get("name").and_then(Value::as_str) == Some(ISSUE_103_TOOL)
                {
                    out.push(
                        map.get("namespace")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string(),
                    );
                }
                map.values().for_each(|value| walk(value, out));
            }
            _ => {}
        }
    }
    let body = issue_103_body(raw_http);
    let mut out = Vec::new();
    match serde_json::from_str::<Value>(body) {
        Ok(value) => walk(&value, &mut out),
        Err(_) => body
            .lines()
            .filter_map(|line| line.strip_prefix("data: "))
            .filter_map(|data| serde_json::from_str::<Value>(data).ok())
            .for_each(|value| walk(&value, &mut out)),
    }
    out.sort();
    out.dedup();
    out
}

fn issue_103_claude_result(stdout: &str) -> String {
    stdout
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .find(|event| event["type"] == "result")
        .and_then(|event| event["result"].as_str().map(str::to_string))
        .expect("Claude Code result event")
}

fn issue_103_ledger_lines(case: &str) -> usize {
    issue_103_capture(&format!("{case}/ledger.jsonl"))
        .lines()
        .filter(|line| !line.trim().is_empty())
        .count()
}

fn issue_103_rejected(response: &str) -> bool {
    issue_103_status(response) == 400 && response.contains(ISSUE_103_REJECTION)
}

#[test]
fn bifrost_103_openai_rejects_the_forwarded_replay_and_accepts_it_with_the_namespace() {
    // Rung 1, no gateway, GPT-5.4: the exact body Bifrost forwarded, sent to
    // OpenAI as recorded and with only `namespace` added to the replayed call.
    for run in 1..=5 {
        let case = format!("capture/{ISSUE_103_DEV}/direct");
        let forwarded =
            issue_103_capture(&format!("{case}/as-forwarded-{run:02}/001-request.http"));
        let rejected =
            issue_103_capture(&format!("{case}/as-forwarded-{run:02}/001-response.http"));
        let restored = issue_103_capture(&format!(
            "{case}/namespace-restored-{run:02}/001-request.http"
        ));
        let served = issue_103_capture(&format!(
            "{case}/namespace-restored-{run:02}/001-response.http"
        ));
        assert!(issue_103_rejected(&rejected), "run {run}");
        assert_eq!(issue_103_status(&served), 200, "run {run}");
        assert!(served.contains(ISSUE_103_CANARY), "run {run}");

        // The two bodies differ only by the namespace on the replayed call.
        let mut without: Value = serde_json::from_str(issue_103_body(&restored)).expect("json");
        for item in without["input"].as_array_mut().expect("input") {
            if let Some(map) = item.as_object_mut() {
                map.remove("namespace");
            }
        }
        let original: Value = serde_json::from_str(issue_103_body(&forwarded)).expect("json");
        assert_eq!(without, original, "run {run}");
    }
}

#[test]
fn bifrost_103_client_tool_search_replay_is_rejected_on_gpt_5() {
    for mode in ["nonstream", "stream"] {
        for run in 1..=5 {
            let case = format!("capture/{ISSUE_103_DEV}/minimal/deferred-{mode}-{run:02}");
            let forwarded = issue_103_forwarded(&case);
            assert_eq!(forwarded.len(), 1, "{case}");
            let (request, response) = &forwarded[0];
            assert!(request.contains("\"tool_search_output\""), "{case}");
            assert!(!request.contains("\"namespace\""), "{case}");
            assert!(issue_103_rejected(response), "{case}");
            let (_, client) = &issue_103_exchanges(&case, "client")[0];
            assert!(issue_103_rejected(client), "{case}");
        }
    }
    for run in 1..=2 {
        let case = format!("capture/{ISSUE_103_DEV}/minimal/deferred-nonstream-gpt-5.5-{run:02}");
        assert!(
            issue_103_rejected(&issue_103_forwarded(&case)[0].1),
            "{case}"
        );
    }
}

#[test]
fn bifrost_103_trigger_removed_eager_tool_is_served() {
    for mode in ["nonstream", "stream"] {
        for run in 1..=5 {
            let case = format!("capture/{ISSUE_103_DEV}/minimal/eager-{mode}-{run:02}");
            let forwarded = issue_103_forwarded(&case);
            assert_eq!(forwarded.len(), 1, "{case}");
            let (request, response) = &forwarded[0];
            assert!(!request.contains("tool_search"), "{case}");
            assert_eq!(issue_103_status(response), 200, "{case}");
            let (_, client) = &issue_103_exchanges(&case, "client")[0];
            assert_eq!(issue_103_status(client), 200, "{case}");
        }
    }
}

#[test]
fn bifrost_103_hosted_tool_search_replay_drops_the_returned_namespace() {
    // Anthropic's server-side tool_search_tool_bm25, turn 1 generated live.
    let mut cases: Vec<String> = (1..=5)
        .map(|run| format!("capture/{ISSUE_103_DEV}/minimal/hosted-nonstream-{run:02}"))
        .collect();
    for model in ISSUE_103_GPT6 {
        let runs = if model == "gpt-6.1-sol" { 3 } else { 2 };
        cases.extend((1..=runs).map(|run| {
            format!("{ISSUE_103_HEAD}/{model}/dev-9bcea677/minimal/hosted-nonstream-{run:02}")
        }));
    }
    assert_eq!(cases.len(), 14);
    for case in &cases {
        let forwarded = issue_103_forwarded(case);
        assert_eq!(forwarded.len(), 2, "{case}");
        // OpenAI put the searched-in tool in a namespace of its own name.
        assert_eq!(
            issue_103_returned_namespaces(&forwarded[0].1),
            vec![ISSUE_103_TOOL.to_string()],
            "{case}"
        );
        let (verdict, response) = issue_103_judge(case);
        assert!(
            matches!(&verdict, Verdict::Violation(reason) if reason.starts_with(REPLAYED_CALL_NAMESPACE_CHANGED)),
            "{case}: {verdict:?}"
        );
        assert!(issue_103_rejected(&response), "{case}");
    }
}

#[test]
fn bifrost_103_responses_route_forwards_the_namespace_the_client_sends() {
    // Rung 2: a Responses-dialect client can carry `namespace`, and the same
    // build forwards it. Without it GPT-5.4 answers with the same 400.
    for run in 1..=5 {
        let kept = format!("capture/{ISSUE_103_DEV}/responses/with-namespace-{run:02}");
        let (request, response) = &issue_103_forwarded(&kept)[0];
        assert!(request.contains("\"namespace\""), "{kept}");
        assert_eq!(issue_103_status(response), 200, "{kept}");
        assert!(response.contains(ISSUE_103_CANARY), "{kept}");

        let bare = format!("capture/{ISSUE_103_DEV}/responses/without-namespace-{run:02}");
        let (request, response) = &issue_103_forwarded(&bare)[0];
        assert!(!request.contains("\"namespace\""), "{bare}");
        assert!(issue_103_rejected(response), "{bare}");
    }
}

#[test]
fn bifrost_103_claude_code_halts_on_gpt_5_after_running_the_searched_in_tool() {
    for run in 1..=5 {
        let case = format!("capture/{ISSUE_103_DEV}/claude/tool-search-on-{run:02}");
        let result =
            issue_103_claude_result(&issue_103_capture(&format!("{case}/claude-stdout.jsonl")));
        assert!(result.starts_with("API Error: 400"), "{case}: {result}");
        assert!(result.contains(ISSUE_103_REJECTION), "{case}");
        // The MCP tool ran once before the session failed.
        assert_eq!(issue_103_ledger_lines(&case), 1, "{case}");

        let (verdict, response) = issue_103_judge(&case);
        assert!(
            matches!(&verdict, Verdict::Violation(reason) if reason.starts_with(REPLAYED_CALL_NAMESPACE_CHANGED)),
            "{case}: {verdict:?}"
        );
        assert!(issue_103_rejected(&response), "{case}");

        // Resuming the session replays the same history and fails the same way,
        // without running the tool again.
        let resumed = issue_103_claude_result(&issue_103_capture(&format!(
            "{case}/continue/claude-stdout.jsonl"
        )));
        assert!(resumed.contains(ISSUE_103_REJECTION), "{case}");
        let continued = issue_103_forwarded(&format!("{case}/continue"));
        assert!(!continued.is_empty(), "{case}");
        assert!(continued
            .iter()
            .all(|(_, response)| issue_103_rejected(response)));
        assert_eq!(issue_103_ledger_lines(&case), 1, "{case}");
    }
}

#[test]
fn bifrost_103_claude_code_without_tool_search_answers() {
    for run in 1..=5 {
        let case = format!("capture/{ISSUE_103_DEV}/claude/tool-search-off-{run:02}");
        let result =
            issue_103_claude_result(&issue_103_capture(&format!("{case}/claude-stdout.jsonl")));
        assert!(result.contains(ISSUE_103_CANARY), "{case}: {result}");
        assert_eq!(issue_103_ledger_lines(&case), 1, "{case}");
        for (request, response) in issue_103_forwarded(&case) {
            assert_eq!(issue_103_status(&response), 200, "{case}");
            assert!(!request.contains("\"tool_search\""), "{case}");
        }
    }
}

#[test]
fn bifrost_103_claude_code_completes_on_gpt_6_where_no_namespace_is_returned() {
    // Model scope: gpt-6.1-sol returns the call to a tool loaded by client
    // search with no namespace, so Bifrost's namespace-less replay is a correct
    // round trip, and Claude Code answers.
    for run in 1..=3 {
        let case =
            format!("{ISSUE_103_HEAD}/gpt-6.1-sol/dev-9bcea677/claude/tool-search-on-{run:02}");
        let result =
            issue_103_claude_result(&issue_103_capture(&format!("{case}/claude-stdout.jsonl")));
        assert!(result.contains(ISSUE_103_CANARY), "{case}: {result}");
        // The rig resumes every tool-search session once; on GPT-6 the resumed
        // session is served too and looks the order up again.
        let resumed = issue_103_claude_result(&issue_103_capture(&format!(
            "{case}/continue/claude-stdout.jsonl"
        )));
        assert!(!resumed.contains(ISSUE_103_REJECTION), "{case}: {resumed}");
        assert!(issue_103_forwarded(&format!("{case}/continue"))
            .iter()
            .all(|(_, response)| issue_103_status(response) == 200));
        assert_eq!(issue_103_ledger_lines(&case), 2, "{case}");
        let forwarded = issue_103_forwarded(&case);
        assert!(forwarded
            .iter()
            .any(|(request, _)| request.contains("\"tool_search_output\"")));
        let returned: Vec<String> = forwarded
            .iter()
            .flat_map(|(_, response)| issue_103_returned_namespaces(response))
            .collect();
        assert_eq!(returned, vec![String::new()], "{case}");
        let (verdict, response) = issue_103_judge(&case);
        assert_eq!(verdict, Verdict::Conformant, "{case}");
        assert_eq!(issue_103_status(&response), 200, "{case}");
        assert!(forwarded
            .iter()
            .all(|(_, response)| issue_103_status(response) == 200));
    }
}

#[test]
fn bifrost_103_client_tool_search_replay_is_served_on_gpt_6() {
    let mut cases = Vec::new();
    for mode in ["nonstream", "stream"] {
        cases.extend((1..=3).map(|run| {
            format!("{ISSUE_103_HEAD}/gpt-6.1-sol/dev-9bcea677/minimal/deferred-{mode}-{run:02}")
        }));
        for model in &ISSUE_103_GPT6[1..] {
            cases.extend((1..=2).map(|run| {
                format!("{ISSUE_103_HEAD}/{model}/dev-9bcea677/minimal/deferred-{mode}-{run:02}")
            }));
        }
    }
    for model in &ISSUE_103_GPT6[1..] {
        cases.extend((1..=2).map(|run| {
            format!("{ISSUE_103_HEAD}/gpt-6.1-sol/dev-9bcea677/minimal/deferred-nonstream-{model}-{run:02}")
        }));
    }
    assert_eq!(cases.len(), 24);
    for case in &cases {
        let forwarded = issue_103_forwarded(case);
        assert_eq!(forwarded.len(), 1, "{case}");
        let (request, response) = &forwarded[0];
        assert!(request.contains("\"tool_search_output\""), "{case}");
        assert!(!request.contains("\"namespace\""), "{case}");
        assert_eq!(issue_103_status(response), 200, "{case}");
    }
    // The same build still fails on GPT-5.4, so the difference is the model.
    for mode in ["nonstream", "stream"] {
        for run in 1..=2 {
            let case = format!(
                "{ISSUE_103_HEAD}/gpt-5.4-mini/dev-9bcea677/minimal/deferred-{mode}-{run:02}"
            );
            assert!(
                issue_103_rejected(&issue_103_forwarded(&case)[0].1),
                "{case}"
            );
        }
    }
}

#[test]
fn bifrost_103_release_v2_3_0_fails_earlier_for_a_fixed_reason() {
    // On the latest release the deferred request never gets a tool_search
    // (bifrost#8361, fixed on dev by #8503), so it fails on the first turn and
    // the namespace question never comes up.
    for mode in ["nonstream", "stream"] {
        for run in 1..=5 {
            let case = format!("capture/v2.3.0/minimal/deferred-{mode}-{run:02}");
            let (request, response) = &issue_103_forwarded(&case)[0];
            assert!(!request.contains("\"tool_search\""), "{case}");
            assert!(
                response.contains("Deferred tools require tools.tool_search"),
                "{case}"
            );
            assert!(!response.contains(ISSUE_103_REJECTION), "{case}");
            let eager = format!("capture/v2.3.0/minimal/eager-{mode}-{run:02}");
            assert_eq!(issue_103_status(&issue_103_forwarded(&eager)[0].1), 200);
        }
    }
}

fn issue_103_rows(results: &str) -> Vec<Value> {
    serde_json::from_str(&issue_103_capture(results)).expect("results.json")
}

fn issue_103_count(
    rows: &[Value],
    prefix: &str,
    predicate: &dyn Fn(&Value) -> bool,
) -> (usize, usize) {
    let matching: Vec<&Value> = rows
        .iter()
        .filter(|row| {
            row["case"]
                .as_str()
                .is_some_and(|case| case.starts_with(prefix))
        })
        .collect();
    (
        matching.iter().filter(|row| predicate(row)).count(),
        matching.len(),
    )
}

fn issue_103_row_rejected(row: &Value) -> bool {
    row["namespace_rejection"] == true && row["client_status"] == 400
}

fn issue_103_row_answered(row: &Value) -> bool {
    row["exit_code"] == 0 && row["canary_answer"] == true && row["tool_executions"] == 1
}

#[test]
fn bifrost_103_matrix_matches_the_recorded_runs() {
    let served = |row: &Value| row["canary_answer"] == true && row["client_status"] == 200;
    let rows = issue_103_rows("capture/results.json");
    let dev = |rest: &str| format!("{ISSUE_103_DEV}/{rest}");
    for (prefix, expected) in [
        (dev("minimal/deferred-nonstream-0"), (5, 5)),
        (dev("minimal/deferred-stream-0"), (5, 5)),
        (dev("minimal/hosted-nonstream-0"), (5, 5)),
        (dev("minimal/deferred-nonstream-gpt-5.5-0"), (2, 2)),
        (dev("responses/without-namespace-0"), (5, 5)),
        (dev("direct/as-forwarded-0"), (5, 5)),
    ] {
        assert_eq!(
            issue_103_count(&rows, &prefix, &issue_103_row_rejected),
            expected,
            "{prefix}"
        );
    }
    for prefix in [
        dev("minimal/eager-nonstream-0"),
        dev("minimal/eager-stream-0"),
        dev("responses/with-namespace-0"),
        dev("direct/namespace-restored-0"),
    ] {
        assert_eq!(issue_103_count(&rows, &prefix, &served), (5, 5), "{prefix}");
    }
    let halted = |row: &Value| {
        row["exit_code"] == 1
            && row["namespace_rejection"] == true
            && row["tool_executions"] == 1
            && row["continue_namespace_rejection"] == true
            && row["tool_executions_after_continue"] == 1
    };
    assert_eq!(
        issue_103_count(&rows, &dev("claude/tool-search-on-"), &halted),
        (5, 5)
    );
    assert_eq!(
        issue_103_count(
            &rows,
            &dev("claude/tool-search-off-"),
            &issue_103_row_answered
        ),
        (5, 5)
    );
}

#[test]
fn bifrost_103_gpt_6_matrix_matches_the_recorded_runs() {
    let ok = |row: &Value| row["namespace_rejection"] == false && row["client_status"] == 200;
    let head = "dev-9bcea677";
    let sol = issue_103_rows(&format!("{ISSUE_103_HEAD}/gpt-6.1-sol/results.json"));
    for prefix in ["minimal/deferred-nonstream-0", "minimal/deferred-stream-0"] {
        assert_eq!(
            issue_103_count(&sol, &format!("{head}/{prefix}"), &ok),
            (3, 3),
            "{prefix}"
        );
    }
    assert_eq!(
        issue_103_count(
            &sol,
            &format!("{head}/minimal/hosted-nonstream-0"),
            &issue_103_row_rejected
        ),
        (3, 3)
    );
    for session in ["tool-search-on-", "tool-search-off-"] {
        assert_eq!(
            issue_103_count(
                &sol,
                &format!("{head}/claude/{session}"),
                &issue_103_row_answered
            ),
            (3, 3),
            "{session}"
        );
    }
    for model in &ISSUE_103_GPT6[1..] {
        let other = issue_103_rows(&format!("{ISSUE_103_HEAD}/{model}/results.json"));
        for prefix in ["minimal/deferred-nonstream-0", "minimal/deferred-stream-0"] {
            assert_eq!(
                issue_103_count(&other, &format!("{head}/{prefix}"), &ok),
                (2, 2),
                "{model}"
            );
        }
        assert_eq!(
            issue_103_count(
                &other,
                &format!("{head}/minimal/hosted-nonstream-0"),
                &issue_103_row_rejected
            ),
            (2, 2),
            "{model}"
        );
    }
    let control = issue_103_rows(&format!("{ISSUE_103_HEAD}/gpt-5.4-mini/results.json"));
    for prefix in [
        "minimal/deferred-nonstream-0",
        "minimal/deferred-stream-0",
        "minimal/hosted-nonstream-0",
    ] {
        assert_eq!(
            issue_103_count(
                &control,
                &format!("{head}/{prefix}"),
                &issue_103_row_rejected
            ),
            (2, 2),
            "{prefix}"
        );
    }
}

// ---- bug 105: Bifrost drops message-level cache_control from Chat Completions ----
//
// opencode, through the `@ai-sdk/openai-compatible` provider Bifrost documents and
// `bifrost run opencode` generates, marks its system and latest user messages with
// `cache_control` on the message itself. Bifrost's ChatMessage has no such field,
// so the marker is lost before any provider sees it: Anthropic and OpenRouter get
// no breakpoint and never cache. The same markers on content blocks survive.
// Captured with transcripts/105/rig (relays on every hop), Bifrost v2.3.0.

fn issue_105_body(raw_http: &str) -> Value {
    let body = raw_http
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .expect("http body");
    serde_json::from_str(body).expect("json body")
}

fn issue_105_raw(relative: &str) -> String {
    fixture(&format!("transcripts/105/{relative}"))
}

/// The last `usage` object a buffered or streamed Chat Completions response carried.
fn issue_105_usage(raw_http: &str) -> Value {
    let body = raw_http
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .expect("http body");
    body.lines()
        .filter_map(|line| line.strip_prefix("data: "))
        .filter_map(|data| serde_json::from_str::<Value>(data).ok())
        .filter_map(|frame| frame.get("usage").filter(|u| u.is_object()).cloned())
        .next_back()
        .unwrap_or_else(|| panic!("no usage in response"))
}

fn issue_105_cached_tokens(raw_http: &str) -> u64 {
    issue_105_usage(raw_http)["prompt_tokens_details"]["cached_tokens"]
        .as_u64()
        .unwrap_or(0)
}

fn issue_105_rows() -> Vec<Value> {
    serde_json::from_str::<Value>(&issue_105_raw("wire/results.json"))
        .expect("results.json")
        .as_array()
        .expect("rows")
        .clone()
}

fn issue_105_exchange(row: &Value, hop: &str) -> String {
    let name = row["exchanges"][hop][0].as_str().expect("exchange name");
    name.to_string()
}

#[test]
fn bug105_message_level_cache_control_never_reaches_the_provider() {
    let mut counts: BTreeMap<(String, bool), usize> = BTreeMap::new();
    for row in issue_105_rows() {
        let case = row["case"].as_str().expect("case");
        let hop = if case.starts_with("anthropic-") {
            "bifrost-to-anthropic"
        } else if case.starts_with("openrouter-") {
            "bifrost-to-openrouter"
        } else {
            continue;
        };
        let request = issue_105_exchange(&row, "client-to-bifrost");
        let client = issue_105_raw(&format!("wire/client-to-bifrost/{request}"));
        let forwarded_name = issue_105_exchange(&row, hop);
        let forwarded = issue_105_raw(&format!("wire/{hop}/{forwarded_name}"));
        let client_json = issue_105_body(&client);
        let forwarded_json = issue_105_body(&forwarded);
        let message_level = client_json["messages"]
            .as_array()
            .expect("messages")
            .iter()
            .any(|m| m.get("cache_control").is_some());
        assert_eq!(
            message_level,
            case.contains("message-level"),
            "{case} {request}"
        );
        let verdict = client_cache_breakpoints_reach_provider(
            &client_json.to_string(),
            &forwarded_json.to_string(),
        );
        if message_level {
            assert!(
                matches!(&verdict, Verdict::Violation(reason) if reason.contains("forwarded body carries 0")),
                "{case} {forwarded_name}: {verdict:?}"
            );
        } else {
            assert_eq!(verdict, Verdict::Conformant, "{case} {forwarded_name}");
        }
        *counts
            .entry((case.to_string(), verdict.is_conformant()))
            .or_default() += 1;
    }
    for (case, conformant) in [
        ("anthropic-message-level", false),
        ("openrouter-message-level", false),
        ("anthropic-block-level-control", true),
        ("openrouter-block-level-control", true),
    ] {
        assert_eq!(
            counts.get(&(case.to_string(), conformant)),
            Some(&10),
            "{case}"
        );
    }
}

#[test]
fn bug105_dropped_breakpoints_bill_every_turn_uncached() {
    // Each trial sent the same turn twice with a fresh nonce; the second call can
    // only read a cache the first one wrote.
    let mut seen: BTreeMap<String, Vec<u64>> = BTreeMap::new();
    for row in issue_105_rows() {
        if row["call"].as_u64() != Some(2) {
            continue;
        }
        let case = row["case"].as_str().expect("case").to_string();
        let hop = match case.as_str() {
            "openrouter-message-level" | "openrouter-block-level-control" => {
                "bifrost-to-openrouter"
            }
            "direct-openrouter-message-level-control" => "client-to-openrouter-direct",
            _ => continue,
        };
        let name = issue_105_exchange(&row, hop).replace("-request.http", "-response.http");
        let response = issue_105_raw(&format!("wire/{hop}/{name}"));
        assert!(response.starts_with("HTTP/1.1 200"), "{case} {name}");
        seen.entry(case)
            .or_default()
            .push(issue_105_cached_tokens(&response));
    }
    assert_eq!(seen["openrouter-message-level"], vec![0; 5]);
    for control in [
        "openrouter-block-level-control",
        "direct-openrouter-message-level-control",
    ] {
        let cached = &seen[control];
        assert_eq!(cached.len(), 5, "{control}");
        assert!(
            cached.iter().all(|&tokens| tokens > 20_000),
            "{control}: {cached:?}"
        );
    }
}

#[test]
fn bug105_opencode_sessions_through_bifrost_never_cache() {
    let dir = |hop: &str| {
        let mut path = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        path.push(format!("../../transcripts/105/opencode/capture/{hop}"));
        let mut requests: Vec<String> = fs::read_dir(&path)
            .unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
            .map(|entry| {
                entry
                    .expect("entry")
                    .file_name()
                    .to_string_lossy()
                    .into_owned()
            })
            .filter(|name| name.ends_with("-request.http"))
            .collect();
        requests.sort();
        requests
    };
    let read = |hop: &str, name: &str| issue_105_raw(&format!("opencode/capture/{hop}/{name}"));

    let client = dir("client-to-bifrost");
    let upstream = dir("bifrost-to-openrouter");
    assert_eq!(client.len(), upstream.len());
    let mut judged = 0;
    for (sent, forwarded) in client.iter().zip(&upstream) {
        let client_json = issue_105_body(&read("client-to-bifrost", sent));
        let forwarded_raw = read("bifrost-to-openrouter", forwarded);
        let response = read(
            "bifrost-to-openrouter",
            &forwarded.replace("-request.http", "-response.http"),
        );
        assert_eq!(issue_105_cached_tokens(&response), 0, "{forwarded}");
        let marked = client_json["messages"]
            .as_array()
            .expect("messages")
            .iter()
            .any(|m| m.get("cache_control").is_some());
        if !marked {
            continue;
        }
        judged += 1;
        assert!(
            !client_cache_breakpoints_reach_provider(
                &client_json.to_string(),
                &issue_105_body(&forwarded_raw).to_string()
            )
            .is_conformant(),
            "{sent} -> {forwarded}"
        );
    }
    assert!(judged >= 25, "only {judged} marked opencode requests");

    // The same opencode build sent straight to OpenRouter reads the cache on
    // every step after the first of each of the five sessions.
    let direct = dir("client-to-openrouter-direct");
    let reads = direct
        .iter()
        .map(|name| {
            issue_105_cached_tokens(&read(
                "client-to-openrouter-direct",
                &name.replace("-request.http", "-response.http"),
            ))
        })
        .filter(|&tokens| tokens > 20_000)
        .count();
    assert!(reads >= 20, "direct cache reads: {reads}");
}

// ---- 106, withdrawn as a bug: Dynamo documents guided-decoding requests,
// including tool calls under default structural tags, as not migrated ----

/// Every run directory of one transcript group, as (cell, endpoint, SSE body,
/// killed worker). The probe writes `HTTP <status>` on the first line and then
/// the raw body bytes; `kill.json` records the worker it SIGKILLed mid-stream.
fn issue_106_runs(group: &str) -> Vec<(String, &'static str, String, Option<u64>)> {
    let mut root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    root.push("../../transcripts/106");
    root.push(group);
    let mut names: Vec<String> = fs::read_dir(&root)
        .unwrap_or_else(|e| panic!("read {}: {e}", root.display()))
        .map(|entry| entry.unwrap().file_name().into_string().unwrap())
        .collect();
    names.sort();
    names
        .into_iter()
        .map(|name| {
            let raw = fixture(&format!(
                "transcripts/106/{group}/{name}/client-response.raw"
            ));
            let (status, body) = raw.split_once('\n').expect("status line");
            assert_eq!(status, "HTTP 200", "{group}/{name}");
            let endpoint = if name.contains("messages") {
                "/v1/messages"
            } else if name.contains("responses") {
                "/v1/responses"
            } else {
                "/v1/chat/completions"
            };
            let killed = fs::read_to_string(root.join(&name).join("kill.json"))
                .ok()
                .map(|k| serde_json::from_str::<serde_json::Value>(&k).unwrap())
                .and_then(|k| k.get("worker").and_then(serde_json::Value::as_u64));
            (name, endpoint, body.to_owned(), killed)
        })
        .collect()
}

/// Documented behavior, kept as a measurement: on current Dynamo main the
/// default structural tags make every tool request a guided-decoding request,
/// which the migration docs exclude. SIGKILLing the serving worker 4 s into a
/// tool-call stream reaches the client as a 503 error event, an Anthropic
/// `overloaded_error`, or `response.failed`, 15/15, under `--migration-limit 3`.
/// The checker's precondition (a request documented as migratable) does not
/// hold here, so this records what clients see rather than a defect.
#[test]
fn dynamo_guided_tool_stream_worker_loss_reaches_client_as_documented() {
    let runs = issue_106_runs("main-default-kill");
    assert_eq!(runs.len(), 15);
    for (name, endpoint, body, killed) in runs {
        assert!(killed.is_some(), "{name}: no worker was killed");
        assert!(
            matches!(worker_loss_stays_invisible(endpoint, &body), Verdict::Violation(reason) if reason.contains("unavailable")),
            "{name}: expected the worker loss to surface"
        );
    }
}

/// With guidance off (`DYN_ENABLE_STRUCTURAL_TAG=false` only) the same tool
/// requests are not guided, so they are migratable: the same kill migrates
/// every stream, and each delivered tool call parses, 15/15.
#[test]
fn dynamo_tool_stream_worker_loss_structural_tags_off_is_conformant() {
    let runs = issue_106_runs("main-structural-tag-off-kill");
    assert_eq!(runs.len(), 15);
    for (name, endpoint, body, killed) in runs {
        assert!(killed.is_some(), "{name}: no worker was killed");
        assert_eq!(
            worker_loss_stays_invisible(endpoint, &body),
            Verdict::Conformant,
            "{name}"
        );
    }
}

/// Version control: release 1.5.1, whose structural tags default to off,
/// migrates the same tool-call streams, 6/6, plus its no-kill control.
#[test]
fn dynamo_release_1_5_1_tool_stream_worker_loss_is_conformant() {
    let runs = issue_106_runs("release-1.5.1-kill");
    assert_eq!(runs.len(), 7);
    let killed = runs.iter().filter(|run| run.3.is_some()).count();
    assert_eq!(killed, 6);
    for (name, endpoint, body, _) in runs {
        assert_eq!(
            worker_loss_stays_invisible(endpoint, &body),
            Verdict::Conformant,
            "{name}"
        );
    }
}

/// Text-only streams on current main migrate through the same kill on every
/// endpoint, and the unkilled tool and text requests finish normally.
#[test]
fn dynamo_text_stream_worker_loss_and_no_kill_controls_are_conformant() {
    let text = issue_106_runs("main-text-kill-controls");
    let streamed: Vec<_> = text
        .iter()
        .filter(|run| !run.0.contains("nonstream"))
        .collect();
    assert_eq!(streamed.len(), 4);
    for (name, endpoint, body, killed) in streamed {
        assert!(killed.is_some(), "{name}: no worker was killed");
        assert_eq!(
            worker_loss_stays_invisible(endpoint, body),
            Verdict::Conformant,
            "{name}"
        );
    }
    let controls = issue_106_runs("main-no-kill-controls");
    assert_eq!(controls.len(), 6);
    for (name, endpoint, body, killed) in controls {
        assert!(killed.is_none(), "{name}");
        assert_eq!(
            worker_loss_stays_invisible(endpoint, &body),
            Verdict::Conformant,
            "{name}"
        );
    }
}

// ---- 107: LiteLLM nests tool-result media in functionResponse.parts for every
// Gemini model; Gemini 2.5 rejects that shape, so the turn after an image tool
// result fails with HTTP 400 on every later turn of the session ----
// Captured with transcripts/107/rig: LiteLLM v1.106.2, v1.88.2 (last release
// before the nesting) and v1.106.2 with rig/fix.diff, each behind a recording
// relay to the live Gemini API.

fn issue_107_raw(relative: &str) -> String {
    fixture(&format!("transcripts/107/{relative}"))
}

fn issue_107_split(raw_http: &str) -> (u16, String) {
    let (head, body) = raw_http.split_once("\r\n\r\n").expect("http body");
    let status = head
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse().ok())
        .unwrap_or(0);
    (status, body.to_string())
}

fn issue_107_rows(index: &str) -> Vec<Value> {
    serde_json::from_str::<Value>(&issue_107_raw(index))
        .expect("results.json")
        .as_array()
        .expect("rows")
        .clone()
}

#[test]
fn bug107_gemini_2_5_rejects_the_tool_result_media_litellm_forwards() {
    let mut counts: BTreeMap<(String, String, bool), usize> = BTreeMap::new();
    for row in issue_107_rows("wire/results.json") {
        let cell = row["cell"].as_str().expect("cell");
        let build = row["build"].as_str().expect("build");
        let model = row["model"].as_str().expect("model");
        let read = |name: &str| issue_107_raw(&format!("wire/{cell}/{name}"));
        let (_, client) = issue_107_split(&read("client-request.http"));
        let (_, forwarded) =
            issue_107_split(&read(row["forwarded_request"].as_str().expect("fwd")));
        let (status, provider) =
            issue_107_split(&read(row["forwarded_response"].as_str().expect("fwd")));
        let verdict = tool_result_images_reach_provider(&client, &forwarded, status, &provider);
        let broken = build == "v1.106.2" && model.starts_with("gemini-2.5-");
        if broken {
            assert!(
                matches!(&verdict, Verdict::Violation(reason)
                    if reason.contains("HTTP 400: Multimodal function responses are not supported for this model.")),
                "{cell}: {verdict:?}"
            );
            // The client sees the same refusal, not a reply.
            let (client_status, _) = issue_107_split(&read("client-response.http"));
            assert_eq!(client_status, 400, "{cell}");
        } else {
            assert_eq!(verdict, Verdict::Conformant, "{cell}");
            assert_eq!(row["image_text_in_reply"], true, "{cell}");
        }
        *counts
            .entry((
                build.to_string(),
                model.to_string(),
                verdict.is_conformant(),
            ))
            .or_default() += 1;
    }
    for build in ["v1.106.2", "v1.88.2", "patched"] {
        for model in [
            "gemini-2.5-pro",
            "gemini-2.5-flash-lite",
            "gemini-3.8-flash",
        ] {
            let conformant = !(build == "v1.106.2" && model.starts_with("gemini-2.5-"));
            assert_eq!(
                counts.get(&(build.to_string(), model.to_string(), conformant)),
                Some(&9),
                "{build} {model}"
            );
        }
    }
}

#[test]
fn bug107_direct_gemini_takes_sibling_media_on_2_5_and_both_shapes_on_3() {
    let mut judged = 0;
    for row in issue_107_rows("direct/results.json") {
        let cell = row["cell"].as_str().expect("cell");
        let (_, request) = issue_107_split(&issue_107_raw(&format!("direct/{cell}/request.http")));
        let (status, body) =
            issue_107_split(&issue_107_raw(&format!("direct/{cell}/response.http")));
        let request: Value = serde_json::from_str(&request).expect("request json");
        let turn = request["contents"]
            .as_array()
            .expect("contents")
            .last()
            .expect("turn");
        let parts = turn["parts"].as_array().expect("parts");
        let nested = parts[0]["functionResponse"]["parts"].is_array();
        assert_eq!(nested, row["shape"] == "nested", "{cell}");
        assert_eq!(parts.len(), if nested { 1 } else { 2 }, "{cell}");
        let rejected = nested
            && row["model"]
                .as_str()
                .expect("model")
                .starts_with("gemini-2.5-");
        if rejected {
            assert_eq!(status, 400, "{cell}");
            assert!(
                body.contains("Multimodal function responses are not supported for this model.")
            );
        } else {
            assert_eq!(status, 200, "{cell}");
            assert!(body.contains("ZEBRA-7342"), "{cell}");
        }
        judged += 1;
    }
    assert_eq!(judged, 18);
}

#[test]
fn bug107_claude_code_session_cannot_continue_after_an_image_read() {
    let base =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../transcripts/107/agents/claude-code");
    let mut cells: Vec<String> = fs::read_dir(&base)
        .expect("claude-code dir")
        .filter_map(Result::ok)
        .filter(|e| e.path().is_dir())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    cells.sort();
    assert_eq!(cells.len(), 18);
    for cell in cells {
        let read = |name: &str| issue_107_raw(&format!("agents/claude-code/{cell}/{name}"));
        // Every session really returned an image from the Read tool.
        let image_results = read("session.jsonl")
            .lines()
            .filter_map(|line| serde_json::from_str::<Value>(line).ok())
            .filter(|e| e["type"] == "user")
            .flat_map(|e| {
                e["message"]["content"]
                    .as_array()
                    .cloned()
                    .unwrap_or_default()
            })
            .filter(|b| {
                b["type"] == "tool_result"
                    && b["content"]
                        .as_array()
                        .is_some_and(|c| c.iter().any(|x| x["type"] == "image"))
            })
            .count();
        assert!(image_results >= 1, "{cell}");
        let first: Value = serde_json::from_str(&read("out.json")).expect("out.json");
        let follow_up: Value = serde_json::from_str(&read("out2.json")).expect("out2.json");
        if cell.starts_with("g25") && cell.contains("-v1062-") {
            // The image turn fails, and so does a text-only follow-up in the same
            // session, because the tool result is replayed every turn.
            for (turn, out) in [("image", &first), ("follow-up", &follow_up)] {
                assert_eq!(out["is_error"], true, "{cell} {turn}");
                assert!(
                    out["result"].as_str().is_some_and(
                        |r| r.contains("Multimodal function responses are not supported")
                    ),
                    "{cell} {turn}"
                );
            }
        } else {
            assert_eq!(first["is_error"], false, "{cell}");
            assert_eq!(follow_up["is_error"], false, "{cell}");
            assert_eq!(read("answer.txt").trim(), "ZEBRA-7342", "{cell}");
            assert_eq!(read("ok.txt").trim(), "OK", "{cell}");
        }
    }
}

#[test]
fn bug107_image_pasted_as_text_into_the_function_response_is_not_delivery() {
    // The #23712 failure shape, recorded live: the v1.88.2 forwarded turn with its
    // image part removed and the data URL pasted into functionResponse.response.
    // Gemini 2.5 Pro accepts it with 200 and never sees the image, so a checker
    // that matched bytes anywhere in the body would call it delivered.
    let (_, client) = issue_107_split(&issue_107_raw(
        "wire/messages-g25p-v1.88.2-r1/client-request.http",
    ));
    let mut judged = 0;
    for run in 1..=3 {
        let cell = format!("adversarial/text-data-url-gemini-2.5-pro-r{run}");
        let (_, forwarded) = issue_107_split(&issue_107_raw(&format!("{cell}/request.http")));
        let (status, provider) = issue_107_split(&issue_107_raw(&format!("{cell}/response.http")));
        assert_eq!(status, 200, "{cell}");
        assert!(!provider.contains("ZEBRA-7342"), "{cell}");
        let image = issue_107_body_image(&client);
        assert!(
            forwarded.contains(&image),
            "{cell} still carries the bytes as text"
        );
        assert!(
            matches!(
                tool_result_images_reach_provider(&client, &forwarded, status, &provider),
                Verdict::Violation(reason) if reason.starts_with(TOOL_RESULT_IMAGE_DROPPED)
            ),
            "{cell}"
        );
        judged += 1;
    }
    assert_eq!(judged, 3);
}

/// The base64 image of the committed Anthropic-dialect client request.
fn issue_107_body_image(client_body: &str) -> String {
    let client: Value = serde_json::from_str(client_body).expect("client json");
    client["messages"][2]["content"][0]["content"][0]["source"]["data"]
        .as_str()
        .expect("tool_result image")
        .to_string()
}

// ---- bug 108: LiteLLM pass-through follows a cross-origin redirect with the
// provider api-key ----

#[test]
fn litellm_passthrough_redirect_sink_keeps_provider_api_key() {
    // The configured origin is allowed to hold the provider key. The redirect
    // target is not, but the recorded sink request carries it in `api-key`.
    let origin = upstream_omits_header_value(
        &fixture("transcripts/108/trigger/run-01/origin.jsonl"),
        "CANARY_PROVIDER_KEY",
    );
    assert!(
        matches!(origin, Verdict::Violation(_)),
        "the configured origin must receive the provider key: {origin:?}"
    );
    let sink = upstream_omits_header_value(
        &fixture("transcripts/108/trigger/run-01/sink.jsonl"),
        "CANARY_PROVIDER_KEY",
    );
    assert!(
        matches!(&sink, Verdict::Violation(reason) if reason.contains("api-key")),
        "a cross-origin 307 sink must be caught holding the provider api-key: {sink:?}"
    );
}

#[test]
fn litellm_passthrough_direct_origin_receives_key_but_sink_does_not() {
    // Same request and proxy config as the trigger, except the configured
    // origin answers 200. The key is expected at the origin and must not reach
    // the unrelated sink.
    let origin = upstream_omits_header_value(
        &fixture("transcripts/108/control/run-01/origin.jsonl"),
        "CANARY_PROVIDER_KEY",
    );
    assert!(
        matches!(origin, Verdict::Violation(_)),
        "the configured origin must receive the provider key: {origin:?}"
    );
    let sink = fixture("transcripts/108/control/run-01/sink.jsonl");
    assert!(
        sink.trim().is_empty(),
        "the control must not send a request to the redirect sink: {sink:?}"
    );
}

#[test]
fn litellm_passthrough_redirect_leak_5_of_5() {
    for run in 1..=5 {
        let path = format!("transcripts/108/trigger/run-{run:02}/result.json");
        let value: Value = serde_json::from_str(&fixture(&path)).expect("108 result");
        assert_eq!(value["client_status"], 200, "trigger run {run}");
        assert_eq!(value["origin_hits"], 1, "trigger run {run}");
        assert_eq!(value["sink_hits"], 1, "trigger run {run}");
        assert_eq!(value["sink_api_key"], true, "trigger run {run}");
        assert_eq!(value["sink_authorization"], false, "trigger run {run}");
    }
    for run in 1..=5 {
        let path = format!("transcripts/108/control/run-{run:02}/result.json");
        let value: Value = serde_json::from_str(&fixture(&path)).expect("108 control result");
        assert_eq!(value["client_status"], 200, "control run {run}");
        assert_eq!(value["origin_hits"], 1, "control run {run}");
        assert_eq!(value["sink_hits"], 0, "control run {run}");
        assert_eq!(value["sink_api_key"], false, "control run {run}");
    }
}

// ---- 110 (negative): LiteLLM /v1/messages stop_sequences on GPT-5 now fails with
// UnsupportedParamsError instead of silently dropping the stop ----

fn issue_110_raw(relative: &str) -> String {
    fixture(&format!("transcripts/110/{relative}"))
}

fn issue_110_split(raw_http: &str) -> (u16, String) {
    let (head, body) = raw_http.split_once("\r\n\r\n").expect("http body");
    let status = head
        .split_whitespace()
        .nth(1)
        .and_then(|code| code.parse().ok())
        .unwrap_or(0);
    (status, body.to_string())
}

fn issue_110_rows(index: &str) -> Vec<Value> {
    serde_json::from_str::<Value>(&issue_110_raw(index))
        .expect("results.json")
        .as_array()
        .expect("rows")
        .clone()
}

fn issue_110_text(response: &str) -> String {
    if let Ok(message) = serde_json::from_str::<Value>(response) {
        return message["content"]
            .as_array()
            .map(|blocks| blocks.iter().filter_map(|b| b["text"].as_str()).collect())
            .unwrap_or_default();
    }
    response
        .lines()
        .filter_map(|line| line.strip_prefix("data:"))
        .filter_map(|data| serde_json::from_str::<Value>(data.trim()).ok())
        .filter(|event| event["type"] == "content_block_delta")
        .filter_map(|event| event["delta"]["text"].as_str().map(str::to_string))
        .collect()
}

fn issue_110_forwarded(cell: &str) -> Vec<(String, Value)> {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    dir.push(format!("../../transcripts/110/wire/{cell}/upstream"));
    if !dir.exists() {
        return Vec::new();
    }
    let mut names: Vec<String> = fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("read {}: {e}", dir.display()))
        .map(|entry| {
            entry
                .expect("entry")
                .file_name()
                .to_string_lossy()
                .into_owned()
        })
        .filter(|name| name.ends_with("-request.http"))
        .collect();
    names.sort();
    names
        .iter()
        .map(|name| {
            let raw = issue_110_raw(&format!("wire/{cell}/upstream/{name}"));
            let (head, body) = raw.split_once("\r\n\r\n").expect("forwarded body");
            let path = head.split_whitespace().nth(1).expect("path").to_string();
            (path, serde_json::from_str(body).expect("forwarded json"))
        })
        .collect()
}

#[test]
fn bug110_gpt5_stop_sequences_fail_fast_on_main_and_are_silently_dropped_before() {
    let mut counts: BTreeMap<(String, String, bool), usize> = BTreeMap::new();
    for row in issue_110_rows("wire/results.json") {
        let cell = row["cell"].as_str().expect("cell");
        let build = row["build"].as_str().expect("build");
        let model = row["model"].as_str().expect("model");
        let stop_sent = row["stop_sequences_sent"].as_bool().expect("trigger");
        let (_, request) =
            issue_110_split(&issue_110_raw(&format!("wire/{cell}/client-request.http")));
        let request: Value = serde_json::from_str(&request).expect("client json");
        assert_eq!(request.get("stop_sequences").is_some(), stop_sent, "{cell}");
        let (status, response) =
            issue_110_split(&issue_110_raw(&format!("wire/{cell}/client-response.http")));
        let forwarded = issue_110_forwarded(cell);
        assert_eq!(
            row["forwarded_requests"].as_u64(),
            Some(forwarded.len() as u64),
            "{cell}"
        );
        let reasoning_model = model.starts_with("gpt-5");
        if !stop_sent {
            assert_eq!(status, 200, "{cell}");
            assert_eq!(forwarded.len(), 1, "{cell}");
            assert_eq!(forwarded[0].0, "/v1/responses", "{cell}");
        } else if reasoning_model && build == "main" {
            assert_eq!(status, 400, "{cell}");
            assert!(response.contains("UnsupportedParamsError"), "{cell}");
            assert!(
                forwarded.is_empty(),
                "{cell}: refused before any upstream call"
            );
        } else if reasoning_model || build == "pre" {
            assert_eq!(status, 200, "{cell}");
            assert_eq!(forwarded.len(), 1, "{cell}");
            assert_eq!(forwarded[0].0, "/v1/responses", "{cell}");
            assert!(forwarded[0].1.get("stop").is_none(), "{cell}: stop dropped");
            assert!(forwarded[0].1.get("stop_sequences").is_none(), "{cell}");
            assert!(
                issue_110_text(&response).contains("STOPHERE"),
                "{cell}: stop never fired"
            );
        } else {
            assert_eq!(status, 200, "{cell}");
            assert_eq!(forwarded.len(), 1, "{cell}");
            assert_eq!(forwarded[0].0, "/v1/chat/completions", "{cell}");
            assert_eq!(
                forwarded[0].1["stop"],
                serde_json::json!(["STOPHERE"]),
                "{cell}"
            );
            let text = issue_110_text(&response);
            assert!(
                text.starts_with("ALPHA") && !text.contains("STOPHERE"),
                "{cell}"
            );
        }
        *counts
            .entry((model.to_string(), build.to_string(), stop_sent))
            .or_default() += 1;
    }
    assert_eq!(counts.values().sum::<usize>(), 66);
    for model in ["gpt-5.4-mini", "gpt-5.5", "gpt-4.1-mini"] {
        for build in ["main", "pre", "fix"] {
            assert_eq!(counts[&(model.to_string(), build.to_string(), true)], 6);
        }
    }
}

#[test]
fn bug110_direct_openai_rejects_stop_on_gpt5_on_both_apis() {
    let mut judged = 0;
    for row in issue_110_rows("direct/results.json") {
        let cell = row["cell"].as_str().expect("cell");
        let (status, body) =
            issue_110_split(&issue_110_raw(&format!("direct/{cell}/response.http")));
        let (_, request) = issue_110_split(&issue_110_raw(&format!("direct/{cell}/request.http")));
        let request: Value = serde_json::from_str(&request).expect("request json");
        let stop_sent = request.get("stop").is_some();
        let gpt5 = row["model"].as_str().expect("model").starts_with("gpt-5");
        if stop_sent && gpt5 {
            assert_eq!(status, 400, "{cell}");
            assert!(body.contains("'stop'"), "{cell}");
        } else {
            assert_eq!(status, 200, "{cell}");
        }
        judged += 1;
    }
    assert_eq!(judged, 21);
}

#[test]
fn bug110_dropping_stop_corrupts_the_react_agent_tool_input() {
    let mut clean: BTreeMap<String, usize> = BTreeMap::new();
    for row in issue_110_rows("agents/langchain-react/results.json") {
        let cell = row["cell"].as_str().expect("cell");
        let first = issue_110_raw(&format!(
            "agents/langchain-react/{cell}/client/001-request.http"
        ));
        let (_, body) = first.split_once("\n\n").expect("tap body");
        let request: Value = serde_json::from_str(body).expect("agent request json");
        assert_eq!(
            request["stop_sequences"],
            serde_json::json!(["\nObservation"]),
            "{cell}"
        );
        let executions: Vec<String> =
            issue_110_raw(&format!("agents/langchain-react/{cell}/ledger.txt"))
                .lines()
                .filter(|line| !line.is_empty())
                .map(|line| serde_json::from_str::<String>(line).expect("ledger json"))
                .collect();
        let key = format!(
            "{}-{}",
            row["model"].as_str().expect("model"),
            row["build"].as_str().expect("build")
        );
        if key == "gpt-5.4-mini-main" {
            assert!(executions.is_empty(), "{cell}: 400 before the tool runs");
        } else if key == "gpt-4.1-mini-main" {
            assert_eq!(executions, ["A-17"], "{cell}");
        } else {
            assert!(
                executions.iter().any(|sku| sku != "A-17"),
                "{cell}: {executions:?}"
            );
        }
        *clean.entry(key).or_default() += usize::from(executions == ["A-17"]);
    }
    assert_eq!(clean["gpt-4.1-mini-main"], 3);
    assert_eq!(clean["gpt-5.4-mini-pre"], 0);
    assert_eq!(clean["gpt-5.4-mini-fix"], 0);
}
// ---- bug 098: image route model allowlist bypass ----

const ISSUE_098_ALLOWED: &[&str] = &["allowed-model"];

fn issue_098(case: &str, file: &str) -> String {
    fixture(&format!("transcripts/098/runs/run-01/{case}/{file}"))
}

fn issue_098_verdict(case: &str, ledger: &str) -> Verdict {
    allowlist_bounds_served_model(
        &issue_098(case, "client-request.http"),
        &issue_098(case, "client-response.http"),
        &issue_098(case, ledger),
        ISSUE_098_ALLOWED,
    )
}

#[test]
fn litellm_098_generation_query_model_bypasses_key_allowlist() {
    let request = issue_098("gen_query_denied", "client-request.http");
    assert!(
        request.starts_with("POST /v1/images/generations?model=denied-model HTTP/1.1"),
        "fixture must bind the denied model in the query string"
    );
    assert!(
        request.contains(r#""model":"allowed-model""#),
        "fixture must name the allowed model in the body, or auth would reject it"
    );
    let verdict = issue_098_verdict("gen_query_denied", "upstream-denied.jsonl");
    assert!(
        matches!(verdict, Verdict::Violation(_)),
        "the denied upstream served the request: {verdict:?}"
    );
}

#[test]
fn litellm_098_generation_deployment_model_bypasses_key_allowlist() {
    let request = issue_098("gen_deploy_denied", "client-request.http");
    assert!(
        request.starts_with("POST /openai/deployments/denied-model/images/generations HTTP/1.1"),
        "fixture must bind the denied model in the deployment path"
    );
    let verdict = issue_098_verdict("gen_deploy_denied", "upstream-denied.jsonl");
    assert!(
        matches!(verdict, Verdict::Violation(_)),
        "the denied upstream served the request: {verdict:?}"
    );
}

#[test]
fn litellm_098_edit_query_model_bypasses_key_allowlist() {
    let request = issue_098("edit_query_denied", "client-request.http");
    assert!(
        request.starts_with("POST /v1/images/edits?model=denied-model HTTP/1.1"),
        "fixture must bind the denied model in the query string"
    );
    assert!(
        request.contains("name=\"model\"\r\n\r\nallowed-model"),
        "fixture must name the allowed model in the multipart body"
    );
    let verdict = issue_098_verdict("edit_query_denied", "upstream-denied.jsonl");
    assert!(
        matches!(verdict, Verdict::Violation(_)),
        "the denied upstream served the edit: {verdict:?}"
    );
}

#[test]
fn litellm_098_edit_deployment_model_bypasses_key_allowlist() {
    let request = issue_098("edit_deploy_denied", "client-request.http");
    assert!(
        request.starts_with("POST /openai/deployments/denied-model/images/edits HTTP/1.1"),
        "fixture must bind the denied model in the deployment path"
    );
    let verdict = issue_098_verdict("edit_deploy_denied", "upstream-denied.jsonl");
    assert!(
        matches!(verdict, Verdict::Violation(_)),
        "the denied upstream served the edit: {verdict:?}"
    );
}

#[test]
fn litellm_098_body_model_denied_is_rejected_before_upstream() {
    let verdict = issue_098_verdict("gen_body_denied", "upstream-denied.jsonl");
    assert_eq!(
        verdict,
        Verdict::Conformant,
        "a body-only denied model is the control that must still return 403"
    );
}

#[test]
fn litellm_098_allowed_model_is_served() {
    assert_eq!(
        issue_098_verdict("gen_no_query", "upstream-allowed.jsonl"),
        Verdict::Conformant,
        "an allowed model with no URL override must be served"
    );
    assert_eq!(
        issue_098_verdict("gen_query_allowed", "upstream-allowed.jsonl"),
        Verdict::Conformant,
        "an allowed model in the query string must be served"
    );
}

#[test]
fn litellm_098_edit_allowed_model_is_served() {
    assert_eq!(
        issue_098_verdict("edit_no_query", "upstream-allowed.jsonl"),
        Verdict::Conformant,
        "an allowed model with no URL override must be served"
    );
}

#[test]
fn litellm_098_checker_rejects_a_rejection_that_reached_upstream() {
    // The body-denied control has a 403 and an empty ledger. Attach the denied
    // call from a bypass case: only a checker that reads the ledger turns this
    // into a Violation, so the upstream half cannot silently pass.
    let verdict = allowlist_bounds_served_model(
        &issue_098("gen_body_denied", "client-request.http"),
        &issue_098("gen_body_denied", "client-response.http"),
        &issue_098("gen_query_denied", "upstream-denied.jsonl"),
        ISSUE_098_ALLOWED,
    );
    assert!(
        matches!(verdict, Verdict::Violation(_)),
        "a rejected request that still reached upstream must be caught: {verdict:?}"
    );
}

#[test]
fn litellm_098_checker_rejects_an_allowed_model_that_was_not_served() {
    // A 200 for an allowed model with no upstream call is not a served request.
    let verdict = allowlist_bounds_served_model(
        &issue_098("gen_no_query", "client-request.http"),
        &issue_098("gen_no_query", "client-response.http"),
        &issue_098("gen_body_denied", "upstream-allowed.jsonl"),
        ISSUE_098_ALLOWED,
    );
    assert!(
        matches!(verdict, Verdict::Violation(_)),
        "an allowed model with no upstream call must be caught: {verdict:?}"
    );
}
