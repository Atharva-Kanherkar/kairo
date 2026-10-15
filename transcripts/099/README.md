# 099 transcripts

All bytes here were recorded on 2026-10-15. See `rig/environment.txt` for pins.

- `realmodel/<prompt>-NN/`: raw OpenRouter request (key redacted inline) and
  response for Qwen3-235B-A22B-2507, plus the extracted `completion.txt`.
  `verbatim` asks for a fixture holding a sample Hermes call; `edit` is a
  read-then-rewrite agent turn on an existing fixture. `capture_real.py` made
  them.
- `capture/<build>/<case>-<dialect>-<mode>-NN/`: one exchange with the real
  Dynamo frontend. `client-request.http` and `client-response.http` are the
  exact socket bytes (streams are still chunk-framed).
  `engine-emitted.json` is the scripted engine's record of the tokens it
  actually emitted for that request (`emitted_text`, `emitted_token_ids`).
  Builds: `nightly-tpc1`, `nightly-tpc5` (tokens per engine chunk),
  `release-1.5.0-tpc1`, and `nightly-realmodel-tpc1` (the 12 well-formed real
  completions replayed verbatim). Cases ending in `_ctrl` are the same
  completion with only the closer inside the string escaped as `<\/tool_call>`,
  which decodes to the identical argument value.
- `rig/`: the scripted engine (Dynamo's public `LLMEngine` backend API), the
  start script, the capture client, the consumer loop, and every scripted
  completion in `rig/scripts/`.
- `consumer/agent_loop.jsonl`: 30 runs of a minimal write_file agent loop over
  the OpenAI and Anthropic SDKs.
- `libprobe/`: a 30-line Rust program calling `dynamo-parsers` 10.0.2 directly,
  and its output.
