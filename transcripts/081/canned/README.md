# Canned OpenAI-compatible upstream responses for kairo 081.

models.json is served for GET /v1/models so OGX registers the model.
Each scenario NAME is selected by the marker "|scenario:NAME|" in the first
user message text. NAME.sse streams; NAME.json is non-streaming.

Scenarios:
- default: plain text answer, finish_reason stop (baseline control)
- length:  finish_reason length (positive control for stop_reason mapping)
- toolcall: one tool_call, finish_reason tool_calls (tool translation control)
- contentfilter: content null, finish_reason content_filter (bug C)
- contentfilter-stream: SSE variant of contentfilter (bug C, streaming)
- refusal: message.refusal set, content null, finish_reason stop (bug D)
- refusal-stream: SSE variant of refusal (bug D, streaming)
- interleave: text -> tool_call -> text SSE (stream block lifecycle probe)
