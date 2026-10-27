# Truncated answers come back as `end_turn` from `/anthropic/v1/messages` (non-streaming)

When an OpenAI model behind Bifrost runs into its output-token limit, the
Responses API reports it as `status: "incomplete"` with
`incomplete_details.reason: "max_output_tokens"`. Through Bifrost's
Anthropic-compatible endpoint, a non-streaming request returns the partial text
with:

```json
{"stop_reason": "end_turn"}
```

Our client relies on `max_tokens` to know it has to continue or raise the
limit, so truncated answers are shown to users as if complete. With
`"stream": true`, the same turn correctly ends with `stop_reason: "max_tokens"`.

We also noticed the two modes disagree for a turn the upstream stopped with
`incomplete_details.reason: "content_filter"`: streaming reports one stop
reason and non-streaming reports `end_turn`. The same upstream result should
produce the same Anthropic stop reason in both modes.
