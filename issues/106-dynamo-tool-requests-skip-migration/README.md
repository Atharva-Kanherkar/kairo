# 106, Dynamo tool requests are not migrated when their worker dies: documented guided-decoding behavior, not a bug

**Verdict: not a bug. Withdrawn after independent review (REJECT) and kept as an
honest negative. Label `feature-request`, upstream class `documented-behavior`.**

- **Upstream**: [ai-dynamo/dynamo](https://github.com/ai-dynamo/dynamo). The behavior comes from [#7930](https://github.com/ai-dynamo/dynamo/pull/7930) (merged 2026-04-08, no migration for guided-decoding requests) together with [#13654](https://github.com/ai-dynamo/dynamo/pull/13654) (merged 2026-10-07, structural tags on by default).
- **Tool under test**: Dynamo `main` [`7aaf17b62691a5dc1141f71da1d5bca72ce6667d`](https://github.com/ai-dynamo/dynamo/commit/7aaf17b62691a5dc1141f71da1d5bca72ce6667d) (2026-10-20), runtime wheel built from source and Python components from the same checkout, with vLLM 0.31.0. Comparison release: `ai-dynamo==1.5.1` with vLLM 0.28.0. Guard and default unchanged on `main` [`4d13286d23b5c37dfd3f3fb2f8f99c29d0e8b2c7`](https://github.com/ai-dynamo/dynamo/commit/4d13286d23b5c37dfd3f3fb2f8f99c29d0e8b2c7) (2026-10-21, checked by the reviewer).
- **Reproduced**: 2026-10-20 17:20 to 18:57 UTC on a RunPod secure-cloud pod, one NVIDIA RTX A6000 (driver 595.91.07, CUDA 13.2), Python 3.12.3, two real vLLM workers serving `Qwen/Qwen3-4B-Instruct-2507`, tool-call parser `hermes`, frontend `--migration-limit 3`. Independently reproduced on 2026-10-21 05:02 to 05:29 UTC on an A40.

## The claim that was withdrawn

The first draft of this folder said that Dynamo "silently disables request
migration for every tool-carrying request", calling it a regression from #13654
that breaks the fault-tolerance promise of `--migration-limit`. The measurement
is real and kept below. The claim that this violates Dynamo's contract was wrong.

## What was measured

Since #13654, structural tags are on by default. A request that carries `tools`
on a parser with a structural-tag builder therefore gets an xgrammar structural
tag in `sampling_options.guided_decoding`. The migration operator gives any
request with `guided_decoding` zero retries (`lib/llm/src/migration.rs:391-410`)
and logs `Guided-decoding request: migration disabled`. When the worker serving
such a request is killed mid-stream, the client receives the failure in-stream
with HTTP 200:

- `/v1/chat/completions`: `data: {"error":{"message":"Service temporarily unavailable","type":"service_unavailable","code":503}}`, then `[DONE]`.
- `/v1/messages`: `message_start`, then `event: error` with `overloaded_error`, and no `message_stop`.
- `/v1/responses`: `response.created`, `response.in_progress`, then `response.failed` with `server_error`.

| Condition (worker SIGKILLed 4 s into a tool-call stream) | Result | Evidence |
|---|---|---|
| main `7aaf17b62`, default settings | 15/15 failures reach the client (5 per endpoint) | `transcripts/106/main-default-kill/` |
| same build, only `DYN_ENABLE_STRUCTURAL_TAG=false` | 15/15 migrate; each tool call carries exactly 1 to 200 | `transcripts/106/main-structural-tag-off-kill/` |
| `ai-dynamo==1.5.1`, defaults (structural tags off there) | 6/6 migrate | `transcripts/106/release-1.5.1-kill/` |
| main, text requests without `tools`, same kill | 5/5 migrate with exact content | `transcripts/106/main-text-kill-controls/` |
| main, no kill | tool and text requests finish cleanly | `transcripts/106/main-no-kill-controls/` |

At the SDK boundary (`transcripts/106/consumer/`, `max_retries=0`), `openai`
3.35.1 raised `APIError` and `anthropic` 1.15.0 raised `APIStatusError`
`overloaded_error`, 3/3 each, with default settings. With guidance off, both
executed the tool call 3/3.

Frontend log (`transcripts/106/logs/frontend.log.txt`), counted per rig start:

| Rig start (UTC) | Build and setting | `migration disabled` warnings | `migration retry scheduled` |
|---|---|---|---|
| 17:51:55 | main, default | 15 | 0 |
| 18:07:37 | main, `DYN_ENABLE_STRUCTURAL_TAG=false` | 0 | 21 (15 probe + 6 SDK runs) |
| 18:32:19 | main, default, SDK runs | 8 | 0 |
| 18:40:37 | 1.5.1, default | 0 | 6 |

The 17:23 and 17:39 starts are scouting on the `1.6.0.dev20261019` nightly and
on main, and are not counted.

### How to rerun

Scripts are in [`transcripts/106/scripts/`](../../transcripts/106/scripts/). On
a one-GPU host with CUDA 13:

```text
bash install.sh                       # uv venv + ai-dynamo[vllm] nightly deps (vLLM 0.31.0)
REV=7aaf17b62 bash build.sh           # maturin build of the runtime wheel at that commit
uv pip uninstall ai-dynamo && uv pip install --no-deps --force-reinstall wheels/main/ai_dynamo_runtime-*.whl
TP=hermes ./rig.sh start 3            # frontend --migration-limit 3 + two vLLM workers
./matrix.sh def 5 sigkill chat_tools:s4 messages_tools:s4 responses_tools:s4
DYN_ENABLE_STRUCTURAL_TAG=false TP=hermes ./rig.sh start 3 && ./matrix.sh nost 5 sigkill chat_tools:s4 messages_tools:s4 responses_tools:s4
python consumer.py openai --kill-secs 4; python consumer.py anthropic --kill-secs 4
```

`rig.sh` runs the frontend as
`python -m dynamo.frontend --http-port 8000 --discovery-backend file --event-plane zmq --migration-limit 3 --enable-anthropic-api --router-mode round-robin`
and each worker as
`python -m dynamo.vllm --model Qwen/Qwen3-4B-Instruct-2507 --discovery-backend file --event-plane zmq --no-kv-events --gpu-memory-utilization 0.42 --max-model-len 8192 --dyn-tool-call-parser hermes`.
`probe.py` finds the worker holding the request through
`dynamo_component_inflight_requests`, SIGKILLs it and its EngineCore child, and
reads the stream to the end.

## Why this is not a bug

Dynamo documents both halves of the behavior:

- The structural-tag page is titled **"Structural Tag (Guided Decoding for Tool
  Calls)"** and opens: "Structural tags use xgrammar guided decoding to
  constrain model output to a valid tool call format at the token level"
  ([`structural-tag.md:4-12`](https://github.com/ai-dynamo/dynamo/blob/4d13286d23b5c37dfd3f3fb2f8f99c29d0e8b2c7/docs/fern/pages/use-cases/tool-calling-and-reasoning/structural-tag.md#L4)).
  It also says structural tags are enabled by default.
- The migration page lists guided decoding under "Known Limitations": migration
  "is not supported for requests that use guided decoding", and "the error is
  propagated to the client instead of migrating"
  ([`request-migration.md:105-107`](https://github.com/ai-dynamo/dynamo/blob/4d13286d23b5c37dfd3f3fb2f8f99c29d0e8b2c7/docs/fern/pages/kubernetes/fault-tolerance/request-migration.md#L107)).
- #7930 added the guard and its test deliberately, to keep a replayed request
  from restarting the decoder's grammar state at the root and corrupting the
  output.

Default tool requests are guided-decoding requests by Dynamo's own
documentation, so they fall inside a documented exclusion. The first draft read
"guided decoding (structured output / JSON schema)" as excluding tool calls and
missed the structural-tag page's title. Turning guidance off makes the requests
migratable again, but that changes which requests are eligible. It does not
show that resuming a constrained request is supported, so the 15/15 control
does not establish a broken promise.

Bug-or-not answers:

- Expected behavior is the spec: **no**. The docs say guided-decoding requests
  are not migrated, and they call tool structural tags guided decoding.
- Maintainers already ruled: yes. #7930 deliberately propagates the failure.
  #13654 then made tool requests guided by default. That trade-off is
  documented, even though its PR text does not mention migration.
- Supported usage: yes, but supported usage does not override the documented
  exclusion.
- Boundary crossed: none. This is not a disclosure claim.
- The fix a maintainer would ship is a feature: carry or rebuild the decoder's
  structural-tag state on the new worker, or migrate auto `tool_choice`
  requests while the stream has not yet entered a tool call, so constrained tool
  requests can migrate safely.
- **Label: `feature-request`.** Only `bug` passes the usefulness gate.

What would change the verdict: a Dynamo document or maintainer statement that
promises migration for these constrained requests, and a reproduction of that
promise being broken with guidance kept on.

## Upstream status

- Checked 2026-10-21 against release `v1.5.1` (structural tags default off,
  `runtime_args.py:42` and `284-285`) and `main` `d7126e2af` and `4d13286d`
  (guard at `migration.rs:391-410` and the default at `runtime_args.py:576-637`
  unchanged). The beam-1.0 pre-release `v1.6.0-beam-1.0-501b-dev.1` carries the
  default-on setting; `v1.6.0-muse-spark-open-dev.1` and
  `v1.6.0-glm-5.3-flash-dev.1` still default off.
- Searched issues, pull requests, discussions, releases and docs for the exact
  warning text, `migration structural tag`, `migration tool calls`,
  `migration guided decoding`, `migration-limit tools` and `failover`. Read
  #7930, #7634, #13654, #13790, #12576, #14872, #13600 and discussions #14153,
  #13018 and #5114. None promises migration for guided requests.
- Classification: **`documented-behavior`**. Upstream it is at most a feature
  request (safe migration of constrained tool requests), or a docs note that the
  migration page's guided-decoding limitation now covers default tool calls.
  Neither has been filed.

## Frozen measurement and checker

- `worker_loss_stays_invisible(endpoint, sse_body)` in
  `crates/harness/src/checks.rs` states the invariant for requests the gateway
  documents as migratable: after a mid-stream worker loss the client sees no
  error event, a normal terminal event, and every started tool call finishing
  with JSON-parseable arguments. Its precondition does not hold for Dynamo's
  default tool requests, which is why this folder is not a bug.
- Review fixes: SSE data that is not JSON now fails instead of being skipped,
  and a Responses function call that is started (`output_item.added`) but never
  finished (no `function_call_arguments.done` and no finished output item) now
  fails. The unit test `worker_loss_checker_judges_every_endpoint_and_fails_closed`
  carries both of the reviewer's counterexamples.
- Conformance tests in `crates/harness/tests/conformance.rs` replay the real
  streams:
  - `dynamo_guided_tool_stream_worker_loss_reaches_client_as_documented` records
    that the 15 default-setting failures reach the client.
  - `dynamo_tool_stream_worker_loss_structural_tags_off_is_conformant` (15/15),
    `dynamo_release_1_5_1_tool_stream_worker_loss_is_conformant` (7/7) and
    `dynamo_text_stream_worker_loss_and_no_kill_controls_are_conformant` (4 killed
    text streams, 6 unkilled) pass the invariant.
  - Every killed run must record a kill, so no run passes vacuously.

## Validation

| Check | Command | Result |
|---|---|---|
| Harness | `cargo test --workspace` | PASS, 320 tests (234 conformance, 76 unit, 10 adversarial) |
| Formatting | `cargo fmt --all -- --check` | PASS |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| README counts | `python3 tools/update-readme-counts.py --check` and `python3 -m unittest discover -s tools -p 'test*readme*.py'` | PASS |

## Not verified

- Only the `hermes` parser on vLLM, and only SIGKILL.
- Whether migrating a stream that is already inside a tool call would stay well
  formed with guidance on. That question belongs to the feature request.
- Response headers were not captured; the body bytes were.

## Independent review

An independent review on 2026-10-21, at
[PR #72](https://github.com/Atharva-Kanherkar/kairo/pull/72#issuecomment-6308981679),
returned **REJECT**:

- Correctness: PASS. The reviewer rebuilt `7aaf17b62` on an A40 and reran 3 kills per
  endpoint each way: 9/9 failures reached the client with default settings and
  9/9 tool calls completed with guidance off. The decoded worker RPCs carried
  structural tags in all 17 default tool requests and no guided decoding in all
  22 tag-disabled attempts.
- Usefulness: FAIL. The exclusion is documented, so the label is `feature-request`.
- Upstream status: PASS, `documented-behavior`.

The review also found two checker holes and a missing log. All are fixed here:

- The checker skipped malformed SSE and missed a started but unfinished
  Responses call.
- `transcripts/106/logs/frontend.log` was promised but never committed, because
  `.gitignore` drops `*.log`. It is now `frontend.log.txt`.

The verdict is accepted as correct.

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized response is
      committed. Prompts are synthetic, the model is public, and the only address
      in the logs is the pod's container-internal `172.27.0.2`.
- [x] Only a pod rented for this test was exercised.
- [x] One folder, one claim.
