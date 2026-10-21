# 106, Dynamo silently disables request migration for every tool-carrying request, so a worker loss kills agent turns that migration would have saved

- **Upstream**: [ai-dynamo/dynamo](https://github.com/ai-dynamo/dynamo). No upstream report found on 2026-10-21. The interaction is between [#7930](https://github.com/ai-dynamo/dynamo/pull/7930) (merged 2026-04-08, disables migration for guided-decoding requests) and [#13654](https://github.com/ai-dynamo/dynamo/pull/13654) (merged 2026-10-07, turns structural tags on by default). Details in Gate 3.
- **Tool under test**: Dynamo `main` [`7aaf17b62691a5dc1141f71da1d5bca72ce6667d`](https://github.com/ai-dynamo/dynamo/commit/7aaf17b62691a5dc1141f71da1d5bca72ce6667d) (2026-10-20), runtime wheel built from source and Python components from the same checkout, with vLLM 0.31.0. Rechecked by source against `main` [`d7126e2af4d8de8ca36c5732207bea3e2deec282`](https://github.com/ai-dynamo/dynamo/commit/d7126e2af4d8de8ca36c5732207bea3e2deec282) (2026-10-21): every defect-relevant line is identical. Version control: release `ai-dynamo==1.5.1` with vLLM 0.28.0.
- **Reproduced**: 2026-10-20 17:20 to 18:57 UTC on a RunPod secure-cloud pod, one NVIDIA RTX A6000 (driver 595.91.07, CUDA 13.2), Python 3.12.3. Real vLLM workers serving `Qwen/Qwen3-4B-Instruct-2507`, tool-call parser `hermes`, frontend `--migration-limit 3`, default structural-tag settings.

## What breaks

Dynamo's frontend can migrate a request: when the worker serving a stream
dies, it replays the prompt plus the tokens already generated to another worker,
and the client never notices. Since #13654 made structural tags the default,
any request that carries `tools` on a parser with a structural-tag builder gets
an xgrammar structural tag in `sampling_options.guided_decoding`. The migration
operator has refused to migrate any request with `guided_decoding` set since
#7930, a guard written for JSON-schema output. The two rules meet, and every
tool-carrying request now runs with zero migration retries, whatever
`--migration-limit` says.

Agent traffic always carries tools: Claude Code on `/v1/messages`, Codex on
`/v1/responses`, OpenAI-SDK agents on `/v1/chat/completions`. When a worker dies
mid-generation (crash, OOM kill, GPU fault, node loss, spot preemption, or a
shutdown past its grace period), the operator expects the turn to move to a
healthy worker. Instead the client receives a 503 error event, an Anthropic
`overloaded_error`, or `response.failed`, and the agent turn is lost. Text-only
requests in the same deployment still migrate, so the loss is invisible unless
the operator reads a per-request WARN line in the frontend log.

## Gate 1: correctness

### Exact reproduction

The scripts are committed under
[`transcripts/106/scripts/`](../../transcripts/106/scripts/). On a one-GPU host
with CUDA 13:

```text
bash install.sh                       # uv venv + ai-dynamo[vllm] nightly deps (vLLM 0.31.0)
REV=7aaf17b62 bash build.sh           # clone dynamo, maturin build of the runtime wheel at that commit
uv pip uninstall ai-dynamo && uv pip install --no-deps --force-reinstall wheels/main/ai_dynamo_runtime-*.whl
TP=hermes ./rig.sh start 3            # frontend --migration-limit 3 + two vLLM workers, PYTHONPATH=dynamo/components/src
python probe.py chat_tools --kill none --out runs/ctl      # control, no kill
python probe.py chat_tools --kill sigkill --after-secs 4 --out runs/kill
python analyze.py runs/kill/client-response.raw chat_tools
./matrix.sh def 5 sigkill chat_tools:s4 messages_tools:s4 responses_tools:s4
```

`rig.sh` runs the frontend as
`python -m dynamo.frontend --http-port 8000 --discovery-backend file --event-plane zmq --migration-limit 3 --enable-anthropic-api --router-mode round-robin`
and each worker as
`python -m dynamo.vllm --model Qwen/Qwen3-4B-Instruct-2507 --discovery-backend file --event-plane zmq --no-kv-events --gpu-memory-utilization 0.42 --max-model-len 8192 --dyn-tool-call-parser hermes`.
File discovery and the ZMQ event plane only replace etcd and NATS on a single
host; KV events are off because round-robin routing does not use them.

`probe.py` sends one streaming request at temperature 0 asking the model to call
`write_file` with the integers 1 to 200 as content. Four seconds in, it reads
each worker's `dynamo_component_inflight_requests{dynamo_endpoint="generate"}`,
SIGKILLs the worker that holds the request together with its EngineCore child,
writes `kill.json`, and reads the stream to the end. An unkilled tool call takes
about 11.8 s, so the kill lands mid-generation. Between runs `rig.sh ensure`
restarts the dead worker. There are no credentials; the API key strings in the
consumer script are dummies for a local server.

- Expected behavior: with migration retries left, the request continues on the
  surviving worker and the client receives one complete tool call, as it does
  for text-only requests on the same build and for tool requests on 1.5.1.
- Observed behavior: the frontend logs
  `Guided-decoding request: migration disabled` when the request arrives. After
  the kill it logs `migration retries exhausted` and `Migration limit exhausted`
  at attempt 0, and the client receives the failure in-stream with HTTP 200:
  - `/v1/chat/completions`: `data: {"error":{"message":"Service temporarily unavailable","type":"service_unavailable","code":503}}` and then `[DONE]`, with no tool call and no `finish_reason`.
  - `/v1/messages`: `message_start`, then `event: error` with `overloaded_error`. No content block, no `message_delta`, no `message_stop`.
  - `/v1/responses`: `response.created`, `response.in_progress`, then `response.failed` with `{"code":"server_error","message":"Service temporarily unavailable"}`.
- Raw request evidence: `client-request.json` in every run directory, the exact
  body bytes sent.
- Raw response evidence: `client-response.raw` in every run directory. The first
  line is the HTTP status the probe recorded; the rest is the body bytes exactly
  as received (`httpx` `iter_raw`). Response headers were not saved.
- Forwarded-request evidence: not captured. The frontend-to-worker hop is
  Dynamo's internal TCP request plane. The frontend log
  ([`transcripts/106/logs/frontend.log`](../../transcripts/106/logs/frontend.log))
  records the migration decision per request instead.
- Reproduction rate: 15/15 with default settings (5 per endpoint), each with a
  recorded mid-stream kill.
- Smallest isolated trigger: the structural tag. On the same build, with the
  same model, prompt and kill, setting only `DYN_ENABLE_STRUCTURAL_TAG=false` on
  the frontend and workers migrates 15/15.

### Control

| Rung | Result | Evidence |
|---|---|---|
| No kill, same build, same tool requests | chat and responses tool calls complete with exactly 1 to 200 (2/2), the Anthropic SDK no-kill turn executes its tool call (1/1), and the four text controls finish cleanly | `main-no-kill-controls/`, `consumer/default-nokill.jsonl` |
| Trigger removed, `DYN_ENABLE_STRUCTURAL_TAG=false` only | 15/15 migrate (`migration retry scheduled` attempt 1); each stream ends normally with a tool call carrying exactly 1 to 200 | `main-structural-tag-off-kill/`, `logs/nofn-nost.txt` |
| Same build, same kill, text request without `tools` | migrates on chat, chat with a stop sequence, messages and responses (stream e300), and non-streamed chat, 5/5; content is exactly 1 to 250 (or 1 to 179 at the stop) | `main-text-kill-controls/` |
| Earlier release, default settings (`ai-dynamo==1.5.1`, structural tags default off) | 6/6 tool streams migrate and complete; no-kill control clean | `release-1.5.1-kill/` |

The trigger-removed rung changes one setting, and the request then migrates on
the same build, model, GPU and kill timing. The 1.5.1 rung shows the same tool
requests migrated before the default changed. So the failure belongs to the
default structural tag reaching the migration guard, not to vLLM, the model,
the probe, or the kill method.

### Frontend log, per rig start

Counted from `logs/frontend.log` by the `== start` marker `rig.sh` writes on
each start (the 1.5.1 workers were restarted by hand after a memory race and
share the 18:40 marker):

| Rig start (UTC) | Build and setting | `Guided-decoding request: migration disabled` | `migration retry scheduled` |
|---|---|---|---|
| 17:51:55 | main, default | 15 | 0 |
| 18:07:37 | main, `DYN_ENABLE_STRUCTURAL_TAG=false` | 0 | 21 (15 probe + 6 SDK runs) |
| 18:32:19 | main, default, SDK runs | 8 | 0 |
| 18:40:37 | 1.5.1, default | 0 | 6 |

The 17:23 and 17:39 starts are scouting on the `1.6.0.dev20261019` nightly and
on main. The nightly also logs the guard for its tool request; it is not
counted as evidence.

### False-positive checks

- [x] Tested the exact behavior: a migration-eligible worker loss
      (`worker_disconnected`, the same reason text streams migrate on) on a
      tool-carrying request, under `--migration-limit 3`.
- [x] Pinned the target: main `7aaf17b62` built from source; current main
      `d7126e2af` re-read with identical defect lines
      (`migration.rs:391-410`, `structural_tag.rs:185-198`,
      `runtime_args.py:576-637`); 33 commits in between, of which only
      #16704 (MiniMax M3 guided output) and #15281 (`vllm_xargs`) touch the
      preprocessor and neither touches migration or the structural-tag default.
- [x] Ruled out bad configuration: defaults for structural tags, a documented
      parser, the documented migration flag. File discovery, ZMQ and round-robin
      are single-host substitutes for etcd, NATS and KV routing; text requests
      migrate under the same configuration.
- [x] Ruled out malformed input: the same tool requests complete without a kill
      and complete after a kill when the trigger is removed.
- [x] Model nondeterminism is irrelevant: the claim is about whether the
      frontend migrates at all, which it decides from the request before any
      token is generated. Requests ran at temperature 0, and every completed
      tool call carried exactly 1 to 200.
- [x] Not created by the harness: the kill is a real SIGKILL of a real vLLM
      worker; the same probe and kill succeed on the control rungs.
- [x] Sanitized: no credentials exist in the setup. The only address in the
      logs is the pod's container-internal `172.27.0.2`.

## Gate 2: usefulness

### Who gets bitten

- Affected user: any Dynamo deployment on current `main`, a nightly since
  2026-10-07, or a pre-release cut from it, that serves agent traffic with a structural-tag parser and
  enables migration. The docs list the parsers as `qwen3_coder`,
  `nemotron_nano`, `hermes`, `qwen25`, `deepseek_v3_2`, `deepseek_v4`,
  `kimi_k2`, `kimi_k3`, `inkling`, and `glm47` with parsers v2
  (`docs/fern/pages/use-cases/tool-calling-and-reasoning/structural-tag.md`).
  Migration is the documented answer to rolling upgrades, scale-downs and
  unexpected worker loss (`docs/fern/pages/kubernetes/fault-tolerance/graceful-shutdown.md:59`).
- Real workflow: a coding agent (Claude Code, Codex, an OpenAI-SDK agent) runs
  a multi-minute turn that ends in a tool call. A worker in the pool is lost.
- Preconditions and likely frequency: migration enabled, a structural-tag
  parser on the worker, and a worker lost while a request is in flight. Every
  tool-carrying request is unprotected, so in an agent deployment the share of
  traffic that migration covers drops to roughly the text-only requests. How
  often workers die depends on the fleet and is not measured here.

### Observable consequence

- User action: an agent sends a turn with `tools`; a worker dies mid-generation.
- Wire-level defect: the frontend never retries. The client gets an in-stream
  error (chat), `overloaded_error` (messages) or `response.failed` (responses)
  4.1 s into the request.
- End-user or agent-level failure: the official SDKs raise, and the agent turn
  ends without the tool call.
- Consumer-boundary demonstration (`transcripts/106/consumer/`, `consumer.py`):
  one agent turn that asks for `write_file`, executes the returned call, and
  checks it wrote exactly 1 to 200. `max_retries=0` in both SDKs so a
  client-side retry cannot mask a gateway failure.

| SDK | Default settings, worker killed at 4 s | Same kill, `DYN_ENABLE_STRUCTURAL_TAG=false` | Default settings, no kill |
|---|---|---|---|
| `openai` 3.35.1, chat completions stream | 3/3 `openai.APIError: Service temporarily unavailable` | 3/3 tool executed, 200 numbers, exact | 1/1 tool executed |
| `anthropic` 1.15.0, `messages.stream` | 3/3 `anthropic.APIStatusError` `overloaded_error` | 3/3 tool executed, 200 numbers, exact | 1/1 tool executed |

- Measured impact: 15/15 raw streams and 6/6 SDK turns fail with default
  settings; 15/15 and 6/6 succeed with only the structural tag disabled; 6/6
  succeed on 1.5.1.
- Inferred impact, not measured: Claude Code retries `overloaded_error`, so
  there the loss probably becomes a full regeneration of the turn (latency and
  tokens) rather than a hard stop. Codex and SDK agents without retry wrappers
  lose the turn. Operators also get one WARN line per tool request, which hides
  the problem in log noise.

### Bug or not

- **Is the expected behavior really the spec?** Yes. The migration page's
  "Known Limitations" says migration "is silently skipped for two request
  types": `n > 1`, and "guided decoding (structured output / JSON schema)"
  (`request-migration.md:97-107`). Tool calling is not listed. The
  structural-tag page says auto tool choice "remains optional" and the tag
  "constrains the output only if the model starts the model-native tool-call
  format". The same tool requests migrated on 1.5.1.
- **Have maintainers already ruled on it?** No ruling covers tool requests.
  #7930 (2026-04-08) deliberately disabled migration for guided decoding, giving
  JSON-schema output restarting at the schema root as the reason; structural
  tags were off by default then. #13654 (2026-10-07) changed the defaults
  (`DYN_ENABLE_STRUCTURAL_TAG` false to true, scope auto to always); its
  description and comments never mention migration.
- **Is the trigger supported usage?** Yes: default structural-tag settings, a
  documented parser, the documented `--migration-limit`.
- **Is a real boundary crossed?** This is not a disclosure claim. The broken
  contract is the fault-tolerance promise of `--migration-limit`, which now
  excludes a request class the docs do not list.
- **What fix would a maintainer ship?** Keep the guard for constraints that
  apply from the first token (`json`, `regex`, `choice`, `grammar`, forced
  `tool_choice`), and let auto `tool_choice` structural tags migrate, at least
  while the stream has not entered a tool call. Not verified here: that a
  migrated stream that had already entered a tool call stays well formed with
  the tag enabled. With the tag disabled, 15/15 did.
- **Label**: `bug`.

## Gate 3: upstream status

- Date checked: 2026-10-21
- Upstream commit checked: main `d7126e2af4d8de8ca36c5732207bea3e2deec282`
  by source; reproduced on `7aaf17b62`
- Search terms: `migration structural tag`, `migration tool calls`,
  `migration guided decoding`, `migration disabled tool`,
  `Guided-decoding request: migration disabled`, `structural tag migration`,
  `migration-limit tools`, in issues and pull requests
- Pull requests read: [#7930](https://github.com/ai-dynamo/dynamo/pull/7930)
  (introduces the guard, closes #7634), [#13654](https://github.com/ai-dynamo/dynamo/pull/13654)
  (default flip, zero mentions of migration in body or comments),
  [#13790](https://github.com/ai-dynamo/dynamo/pull/13790) and
  [#12576](https://github.com/ai-dynamo/dynamo/pull/12576) (guided decoding and
  guided tool calls, no migration change),
  [#14872](https://github.com/ai-dynamo/dynamo/pull/14872) (migration docs after
  grace expiry)
- Issues read: [#16192](https://github.com/ai-dynamo/dynamo/issues/16192)
  (protocol follow-ups; its "migration" mentions are API migration, unrelated)
- Documentation read: `request-migration.md`, `structural-tag.md`,
  `graceful-shutdown.md` on main
- Release check: `v1.5.1` sets `dyn_enable_structural_tag: bool = False`
  (`runtime_args.py:42`, `env_var="DYN_ENABLE_STRUCTURAL_TAG", default=False`
  at 284-285), and its tool streams migrate 6/6.

Classification:

- [ ] Novel
- [ ] Duplicate, open and still reproducible
- [ ] Fixed on current release
- [x] Regression: tool-carrying requests migrated on release 1.5.1 and stopped
      migrating on `main` when #13654 changed the structural-tag default. The
      latest stable release is not affected. `main` and the nightlies are
      (`1.6.0.dev20261019` logged the guard during scouting). Of the 1.6.0
      pre-release tags, `v1.6.0-beam-1.0-501b-dev.1` carries the default-on
      resolver and the guard (`runtime_args.py:632-637`, `migration.rs:381`),
      while `v1.6.0-muse-spark-open-dev.1` and `v1.6.0-glm-5.3-flash-dev.1`
      still default to off. No upstream report exists.
- [ ] Documented behavior
- [ ] Discussed upstream without a dedicated ticket
- [ ] Incomplete, current upstream state could not be verified

## Root cause

1. `components/src/dynamo/common/configuration/groups/runtime_args.py:576-637`,
   `resolve_structural_tag_config`: with no flag set, it returns
   `StructuralTagConfig(scope="always", schema="auto")`. Only an explicit
   `--dyn-structural-tag false` or the legacy `DYN_ENABLE_STRUCTURAL_TAG=false`
   returns `None`. The worker publishes this config with its model.
2. `lib/llm/src/preprocessor/structural_tag.rs:185-198`,
   `structural_tag_decision`: with a published config and a parser that has a
   builder, an auto `tool_choice` request with tools is `Required`.
3. `lib/llm/src/preprocessor/tool_choice.rs:68` onward,
   `apply_tool_choice_guided_decoding`, installs the tag in
   `sampling_options.guided_decoding.structural_tag`.
4. `lib/llm/src/migration.rs:391-410`, in `RetryManager::build`: any
   `guided_decoding.is_some()` sets `retries_left = 0` and logs
   `Guided-decoding request: migration disabled`. The guard does not tell a
   triggered structural tag (auto tool choice, free text until the model opens
   a tool call) from a constraint that applies from the first token.
5. On a worker disconnect the stream fails at attempt 0 with
   `Migration limit exhausted`, and each endpoint renders the error in its own
   format.

## Wire evidence

All under [`transcripts/106/`](../../transcripts/106/), described in its
README:

- `main-default-kill/`: 15 runs (5 chat, 5 messages, 5 responses). Each has
  `client-request.json`, `client-response.raw` and `kill.json`.
- `main-structural-tag-off-kill/`: the same 15 with the trigger removed.
- `release-1.5.1-kill/`: 6 kill runs and 1 no-kill control on 1.5.1.
- `main-text-kill-controls/`: text requests killed mid-stream on main.
- `main-no-kill-controls/`: unkilled tool and text requests on main.
- `consumer/`: SDK outcomes as JSON lines.
- `logs/frontend.log`: the full frontend log for every rig start, ANSI codes
  stripped; `logs/nofn-*.txt` are the matrix summaries.
- `environment/`: GPU and driver fingerprint, tested revision, `pip freeze` of
  both environments.

## Frozen invariant

- The invariant, in words: when the worker serving a stream dies and migration
  retries remain, the loss is invisible to the client. The stream has no error
  event, ends in the endpoint's normal terminal state, and every delivered tool
  call has arguments that parse as JSON.
- Checker: `worker_loss_stays_invisible(endpoint, sse_body)` in
  `crates/harness/src/checks.rs`. It covers chat completions, Anthropic
  messages and Responses, fails closed on empty streams and unknown endpoints,
  and judges only client-visible bytes. A fix that migrates tool requests passes
  it, and so does a fix that never constrains them.
- Conformance tests in `crates/harness/tests/conformance.rs`:
  `dynamo_tool_stream_worker_loss_default_structural_tags_violation` (15/15
  violations, each run must record a kill),
  `dynamo_tool_stream_worker_loss_structural_tags_off_is_conformant` (15/15),
  `dynamo_release_1_5_1_tool_stream_worker_loss_is_conformant` (7/7),
  `dynamo_text_stream_worker_loss_and_no_kill_controls_are_conformant` (4 killed
  text streams, 6 unkilled controls).
- Unit test `worker_loss_checker_judges_every_endpoint_and_fails_closed`
  covers each endpoint's pass and fail shapes, a cut tool call, and the
  fail-closed cases.

## Validation

| Check | Command | Result |
|---|---|---|
| Reproduction | `./matrix.sh def 5 sigkill chat_tools:s4 messages_tools:s4 responses_tools:s4` | 15/15 surfaced the worker loss |
| Control, trigger removed | same with `DYN_ENABLE_STRUCTURAL_TAG=false` | 15/15 migrated, tool calls exact |
| Control, release | same cells on `ai-dynamo==1.5.1` | 6/6 migrated |
| Consumer | `consumer.py openai|anthropic --kill-secs 4` | 6/6 SDK exceptions; 6/6 executed with trigger removed |
| Harness | `cargo test --workspace` | PASS, 320 tests (234 conformance, 76 unit, 10 adversarial) |
| Formatting | `cargo fmt --all -- --check` | PASS |
| Lint | `cargo clippy --workspace --all-targets -- -D warnings` | PASS |
| README counts | `python3 tools/update-readme-counts.py --check` | PASS |

## Not verified

- Only the `hermes` parser on vLLM. Other parsers with builders and the SGLang
  path should behave the same by the code path above, but were not run.
  TensorRT-LLM and the vLLM Rust sidecar default structural tags off.
- Only SIGKILL. A graceful SIGTERM whose grace period expires reaches the same
  guard by the code path, but was not run.
- That migrating a stream already inside a tool call stays well formed with the
  structural tag enabled, which the proposed fix depends on.
- Response headers were not captured; the body bytes under dispute were.
- The pinned-main build is a dev-profile wheel built on the pod, not a published
  artifact. The `1.6.0.dev20261019` nightly showed the same guard during
  scouting.

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized response is
      committed. Prompts are synthetic, and the model is public.
- [x] Only a pod rented for this test was exercised; no hosted service or third
      party.
- [x] The pull request contains one finding.
- [x] Unrelated working tree changes are preserved.

## Author verdict

- Correctness: PASS
- Usefulness: PASS
- Upstream status: PASS, classified `regression`
- Overall: ACCEPT, pending independent review

## Independent review

Pending. The reviewer must rerun the critical path on a GPU host, using the
scripts in `transcripts/106/scripts/`.
