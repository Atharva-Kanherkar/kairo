# Transcripts for finding 094

Dynamo `/v1/messages` stops on a requested stop sequence but reports `end_turn` and `stop_sequence: null`.

| Path | What it holds |
|---|---|
| `dynamo/` | Client request and response bytes on `ai-dynamo==1.6.0.dev20261004`: `0001`-`0005` unary bug case, `0006`-`0008` streamed bug case, `0009`-`0011` chat-surface control with `nvext.stop_reason`, `0012`-`0014` trigger removed, `0015`-`0020` length stop at the delimiter boundary (`max_tokens: 10`, 3 unary, 3 streamed). `run-log.txt` is the run summary. |
| `dynamo/forwarded/` | Engine-side record per exchange: the forwarded request (token ids and stop conditions), decoded prompt, the planned completion (`planned_completion`, a script, not output), what the engine actually handed to the runtime (`emitted_payloads`, `emitted_token_ids`, `emitted_text`), and `termination`. `lifecycle.jsonl` lists planned and sent token counts. |
| `dynamo-release-1.5.0/` | The same matrix on the `1.5.0` release, with its own `forwarded/` engine records. |
| `anthropic-live/` | Provider control against the live Anthropic Messages API (`claude-haiku-4-5`), 5 unary and 3 streamed. The API key, organization, workspace, request id, trace, rate-limit, and CF-RAY headers are replaced with `[REDACTED]`. |
| `consumer/` | `anthropic-tools` `ToolUser` runs on the nightly: wire bytes, `tool-ledger.jsonl` (one line per real tool execution), `run-log.txt`, and engine-side records with emitted output. |
| `rig/` | Everything needed to rerun: the deterministic engine, start script, recording proxy, drivers, provider control, consumer script, scripted completions, and `versions.txt`. |
| `capture-*.jsonl` | Machine-readable records read by `anthropic_stop_sequence_reported`, built from the raw files by `build_capture.py`. `generation` is the engine's `emitted_text`. Each record names its response file and engine record, and the builder asserts that every request pairs with its engine record. |

Client requests to Dynamo carry the placeholder key `dynamo-local`. No real credential appears anywhere in this directory.
