//! Adversarial self-tests for `anthropic_stream_block_lifecycle`.
//!
//! A checker that cannot fail is worse than no checker, because a frozen
//! `Violation` assertion then certifies nothing. Each case here is built to
//! break the checker in a specific way, and each is asserted directly against
//! the checker rather than against a transcript.

use kairo::checks::{anthropic_stream_block_lifecycle, Verdict};

fn ev(index: u64, block_type: &str, id: &str) -> String {
    format!(
        "data: {{\"type\":\"content_block_start\",\"index\":{index},\
         \"content_block\":{{\"type\":\"{block_type}\",\"id\":\"{id}\",\"name\":\"n\",\
         \"input\":{{}}}}}}\n\n"
    )
}

fn text_delta(index: u64, text: &str) -> String {
    format!(
        "data: {{\"type\":\"content_block_delta\",\"index\":{index},\
         \"delta\":{{\"type\":\"text_delta\",\"text\":\"{text}\"}}}}\n\n"
    )
}

fn args_delta(index: u64, partial: &str) -> String {
    format!(
        "data: {{\"type\":\"content_block_delta\",\"index\":{index},\
         \"delta\":{{\"type\":\"input_json_delta\",\"partial_json\":\"{partial}\"}}}}\n\n"
    )
}

fn stop(index: u64) -> String {
    format!("data: {{\"type\":\"content_block_stop\",\"index\":{index}}}\n\n")
}

#[test]
fn detects_reused_index() {
    let sse = format!(
        "{}{}{}{}{}",
        ev(0, "tool_use", "call_a"),
        args_delta(0, "{}"),
        stop(0),
        ev(0, "tool_use", "call_a"),
        stop(0)
    );
    assert!(
        matches!(&anthropic_stream_block_lifecycle(&sse), Verdict::Violation(r) if r.contains("opened twice")),
        "a reused block index must violate"
    );
}

#[test]
fn detects_split_tool_call_by_id() {
    // Two well-formed, distinctly-indexed blocks that share one tool-call id.
    // Neither index is reused, so only the id rule can catch this.
    let sse = format!(
        "{}{}{}{}{}{}",
        ev(0, "tool_use", "call_a"),
        args_delta(0, "{\\\"cit"),
        stop(0),
        ev(1, "tool_use", "call_a"),
        args_delta(1, "y\\\":\\\"sf\\\"}"),
        stop(1)
    );
    let v = anthropic_stream_block_lifecycle(&sse);
    assert!(
        matches!(&v, Verdict::Violation(r) if r.contains("split across 2 tool_use blocks")),
        "a tool call split across two blocks must violate, got {v:?}"
    );
}

#[test]
fn distinct_tool_calls_conform() {
    let sse = format!(
        "{}{}{}{}{}{}",
        ev(0, "tool_use", "call_a"),
        args_delta(0, "{}"),
        stop(0),
        ev(1, "tool_use", "call_b"),
        args_delta(1, "{}"),
        stop(1)
    );
    assert_eq!(
        anthropic_stream_block_lifecycle(&sse),
        Verdict::Conformant,
        "two distinct parallel tool calls are legitimate and must conform"
    );
}

#[test]
fn text_between_tool_blocks_conforms() {
    let sse = format!(
        "{}{}{}{}{}{}",
        ev(0, "text", ""),
        text_delta(0, "checking"),
        stop(0),
        ev(1, "tool_use", "call_a"),
        args_delta(1, "{}"),
        stop(1)
    );
    assert_eq!(
        anthropic_stream_block_lifecycle(&sse),
        Verdict::Conformant,
        "text before a tool call is normal and must not be flagged"
    );
}

#[test]
fn reopened_index_after_stop_is_reported_even_if_first_start_was_text() {
    let sse = format!(
        "{}{}{}{}",
        ev(0, "text", ""),
        text_delta(0, "hi"),
        stop(0),
        ev(0, "text", "")
    );
    assert!(
        matches!(
            &anthropic_stream_block_lifecycle(&sse),
            Verdict::Violation(_)
        ),
        "reusing an index after a stop is a violation regardless of block type"
    );
}

/// The adversarial case the previous checker failed: a `tool_use` wrapper that
/// appears with NO `input_json_delta` at all must not be mistaken for preserved
/// media or for a conformant block.
#[test]
fn tool_use_without_any_argument_delta_still_conforms() {
    // Anthropic permits an empty input object, delivered as a start with `{}`
    // and no deltas. That is conformant and must not be flagged as a split.
    let sse = format!("{}{}", ev(0, "tool_use", "call_a"), stop(0));
    assert_eq!(anthropic_stream_block_lifecycle(&sse), Verdict::Conformant);
}

#[test]
fn empty_and_malformed_input_is_conformant_not_a_panic() {
    for sse in [
        "",
        "data: [DONE]\n\n",
        "data: not-json\n\n",
        "data: {\"type\":\"content_block_start\"}\n\n",
        "data: {\"type\":\"content_block_delta\",\"index\":0}\n\n",
        "event: ping\ndata: {}\n\n",
    ] {
        assert_eq!(
            anthropic_stream_block_lifecycle(sse),
            Verdict::Conformant,
            "input {sse:?} must be ignored rather than misread"
        );
    }
}

#[test]
fn three_way_split_reports_the_run_count() {
    let sse = format!(
        "{}{}{}{}{}{}{}",
        ev(0, "tool_use", "call_a"),
        args_delta(0, "{"),
        stop(0),
        ev(1, "tool_use", "call_a"),
        args_delta(1, "}"),
        stop(1),
        ev(2, "tool_use", "call_a")
    );
    assert!(
        matches!(&anthropic_stream_block_lifecycle(&sse), Verdict::Violation(r) if r.contains("split across 3 tool_use blocks")),
        "a three-way split must be reported as such"
    );
}

/// The masking case from the previous review: an unrelated non-text block
/// elsewhere in the stream must not let a split tool call pass. The split is
/// identified by tool-call id, not by counting blocks or looking for any media.
#[test]
fn unrelated_media_elsewhere_cannot_mask_a_split_tool_call() {
    let sse = format!(
        "{}{}{}{}{}{}{}{}",
        // A perfectly ordinary image block, indices 0 and 1.
        "data: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"image\",\"source\":{}}}\n\n",
        "data: {\"type\":\"content_block_stop\",\"index\":0}\n\n",
        // The split tool call at indices 2 and 3, sharing one id.
        ev(2, "tool_use", "call_a"),
        args_delta(2, "{\\\"cit"),
        stop(2),
        ev(3, "tool_use", "call_a"),
        args_delta(3, "y\\\":\\\"sf\\\"}"),
        stop(3)
    );
    let v = anthropic_stream_block_lifecycle(&sse);
    assert!(
        matches!(&v, Verdict::Violation(r) if r.contains("call_a")),
        "the split call_a must be reported even though an image block is present, got {v:?}"
    );
}

/// A tool call whose id legitimately appears once, alongside a different tool
/// call, must conform even when text blocks sit between them.
#[test]
fn text_between_two_distinct_tool_calls_conforms() {
    let sse = format!(
        "{}{}{}{}{}{}{}{}{}",
        ev(0, "tool_use", "call_a"),
        args_delta(0, "{}"),
        stop(0),
        ev(1, "text", ""),
        text_delta(1, "now the other"),
        stop(1),
        ev(2, "tool_use", "call_b"),
        args_delta(2, "{}"),
        stop(2)
    );
    assert_eq!(
        anthropic_stream_block_lifecycle(&sse),
        Verdict::Conformant,
        "two distinct calls separated by text is a legal stream shape"
    );
}
