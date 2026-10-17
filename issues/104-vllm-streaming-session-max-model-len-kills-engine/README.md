# 104, A resumable streaming session that grows past `max_model_len` kills EngineCore instead of length-capping the session

- **Upstream**: [vllm-project/vllm](https://github.com/vllm-project/vllm). The defect is reported and fixed in open, unmerged pull requests, and it is covered by a referenced security advisory whose fix PR has sat conflicted since 2026-08-12. Details and links in Gate 3.
- **Tool under test**: vLLM `v1` engine, commit [`237d99b4aff67ecfedacca7acb0ee93f299fa54e`](https://github.com/vllm-project/vllm/commit/237d99b4aff67ecfedacca7acb0ee93f299fa54e) (2026-10-17) and [`e744eacf682f2b1c7f91d2dcf348a015046e56b9`](https://github.com/vllm-project/vllm/commit/e744eacf682f2b1c7f91d2dcf348a015046e56b9), the `main` HEAD at the time of check on 2026-10-18 (main has since moved one commit to `763e3f7c911308fa609fb9437642929efe955798`, a spec-decode config fix whose parent is `e744eac`; no defect-relevant file changed)
- **Reproduced**: 2026-10-18, macOS arm64, CPU backend, `facebook/opt-125m` offline, `max_model_len=1024` with a pool of exactly 1024 usable KV tokens (33 blocks of 32, one reserved as the null block), defaults otherwise

## What breaks

vLLM's streaming-input feature lets one request ID receive successive input
chunks as a resumable session. Each chunk is validated on its own against
`max_model_len`; the accumulated session is never re-checked, and the waiting
path never reserves a slot for the sampled token. When the accumulated session
reaches `max_model_len` exactly, the prefill schedules every token up to the
limit, the worker samples one token over it, and a bookkeeping assertion in the
model runner fires. One token more, and the worker admits a request whose prompt
is wider than the model runner's `token_ids_cpu` row. Both raise inside the
worker, become a fatal error in `EngineCore`, and surface to every connected
client as `EngineDeadError`. The engine does not recover; every later request
fails, and an HTTP deployment reports an unhealthy server.

A single authenticated client with one session is enough. The 32 blocks of the
KV pool mean the crash needs no memory pressure and no concurrency; the
server refuses to start with a smaller pool, and it refuses a plain request of
the same size at the API. Only the session path overruns.

## Gate 1: correctness

### Exact reproduction

```text
cd tools/vllm            # pinned checkout of vllm-project/vllm, .venv with the CPU build
.venv/bin/python ../vllm-hunt/engine_session_overlength.py --chunk2 1000 --label bug  # total 1033
.venv/bin/python ../vllm-hunt/engine_session_overlength.py --chunk2 991  --label bnd  # total 1024
.venv/bin/python ../vllm-hunt/repro_session_overlength.py                            # scheduler-level, model-free
```

The engine program drives the real `AsyncLLM` (multi-process `EngineCore`, real
worker) with an async-generator prompt: chunk 1 is a 32-token prompt with
`max_tokens=2`; after the chunk completes the session carries 33 tokens (the
last sampled token is discarded on the fold); chunk 2 is 990, 991, 992 or 1000
tokens. Every chunk is legal by itself. The scheduler program replays the same
scenario against the real scheduler from `tests/v1/core/utils.py`, no model, and
asserts the scheduled tokens exceed the KV the cache manager allocated.

- Expected behavior: an update that would take the session to or beyond
  `max_model_len` ends that session with a length-limit result, as open fix PRs
  #42502 and #51900 specify, and the engine keeps serving.
- Observed behavior: at a session total of exactly `max_model_len` the worker
  asserts; one token more it fails to broadcast the prompt into its row buffer.
  Both kill `EngineCore`.
- Raw logs: [`transcripts/104-vllm-streaming-session-max-model-len-kills-engine/`](../../transcripts/104-vllm-streaming-session-max-model-len-kills-engine/),
  one file per run, holding the engine config dump, the scheduler's dump of the
  offending step, the worker traceback, and the client result line.
- Reproduction rate: 8/8 engine runs with a session total of 1024 or more ended
  in `EngineDeadError` (4/4 at 1033 including one bunched-chunks run, 3/3 at
  1024, 1/1 at 1025), on both the pinned commit and current `main` HEAD; 4/4
  runs at 1023 or below finished cleanly; 3/3 plain requests of the same sizes
  were rejected by the API with `VLLMValidationError`.
- Smallest isolated trigger: one token. Chunk 2 of 990 (total 1023) finishes
  cleanly; 991 (total 1024) kills the engine; 992 (total 1025) kills it by the
  second crash site.

### Evidence, one run of each crash site

```text
# engine-1033-spaced-e744eac.txt, current main HEAD, session total 1033
scheduler dump: NewRequestData(req_id=sess-mainbug-...,prompt_token_ids_len=1033,
  num_computed_tokens=33, block_ids=([1..32])), num_scheduled_tokens={...: 1000}
worker: File "vllm/v1/worker/gpu_input_batch.py", line 371, in add_request
          self.token_ids_cpu[req_index, :num_prompt_tokens] = request.prompt_token_ids
        ValueError: could not broadcast input array from shape (1033,) into shape (1024,)
EngineCore: EngineCore encountered a fatal error.
client: [mainbug] outputs=2 finished=False error=EngineDeadError

# engine-1024-assert-e744eac.txt, current main HEAD, session total 1024
scheduler dump: NewRequestData(req_id=sess-mainbnd-...,prompt_token_ids_len=1024,
  num_computed_tokens=33, block_ids=([1..32])), num_scheduled_tokens={...: 991}
worker: File "vllm/v1/worker/gpu_model_runner.py", line 3569, in _bookkeeping_sync
          assert end_idx <= self.max_model_len
        AssertionError: Sampled token IDs exceed the max model length.
        Total number of tokens: 1025 > max_model_len: 1024
EngineCore: EngineCore encountered a fatal error.
client: [mainbnd] outputs=2 finished=False error=EngineDeadError
```

The scheduler-level run is deterministic and model-free. Five rounds on current
`main`:

```text
BUG     : scheduled=1000 status=WAITING_FOR_STREAMING_REQ computed=1032 blocks=64/65 max_model_len=1024 over_model_len=True kv_overrun=True
CTRL-A  : scheduled=800  ... computed=832  blocks=52/52 ... over_model_len=False kv_overrun=False
CTRL-B  : scheduled=1000 ... computed=1032 blocks=65/65 max_model_len=2048 ... False False
CTRL-C  : scheduled=991  ... computed=1023 blocks=64/64 ... False False
round 5: REPRO OK
```

The bug row is the invariant in words: 1032 tokens computed, 64 blocks held
(the entire usable pool), 65 blocks required.

### Control ladder

| Rung | Result | Evidence |
|---|---|---|
| Under the limit, same shape | session total 833 and 1023 finish cleanly, 2/2 | `engine-controls-237d99b4a.txt`, `engine-1023-clean-237d99b4a.txt` |
| Plain, non-session request of the same size | 1023 finishes; 1024, 1025, 1033 rejected by the API with `VLLMValidationError` before any worker work; the engine stays healthy after the rejection | `engine-single-10{23,24,25}-237d99b4a.txt`, `engine-controls-237d99b4a.txt` |
| Trigger removed, one token less | change only chunk 2 from 991 to 990 and the same session finishes with no error | `engine-1023-clean-237d99b4a.txt` |
| Chunk spacing | spaced chunks and back-to-back chunks crash identically | `engine-1033-bunched-237d99b4a.txt`, 0.7s |
| Aftermath | every request issued after the crash, including a health-sized one, gets `EngineDeadError` | `engine-controls-237d99b4a.txt` |

### False-positive checks

- [x] `git ls-remote https://github.com/vllm-project/vllm.git HEAD` confirms
      `e744eacf682f2b1c7f91d2dcf348a015046e56b9` was `main` HEAD on 2026-10-18; the
      crash reproduces there and on the 2026-10-17 commit. `main` has since moved
      one commit (`763e3f7c`, spec-decode config), and an independent reviewer
      checked the defect-relevant files are unchanged between `e744eac` and
      `763e3f7c` by commit list and re-read of the tree.
- [x] The real public entry point is exercised: `AsyncLLM.generate` with an
      async-generator prompt is the same call the realtime speech endpoint
      makes (`vllm/entrypoints/speech_to_text/realtime/serving.py:60`,
      "Transform audio stream into StreamingInput for engine.generate()"), and
      `tests/v1/e2e/general/test_streaming_input.py` uses the identical shape.
- [x] No model nondeterminism is involved: the scheduler-level reproduction is
      deterministic with no model at all, and the engine crash sites are token
      accounting, not sampling output.
- [x] No memory pressure is involved: the pool is exactly `max_model_len`
      (1024 usable tokens), and the engine refuses to start with less, so the
      crash fires with an idle, fully available cache.
- [x] Not operator misuse: documented feature, documented endpoint, default
      settings, legal chunks, authenticated client.
- [x] Not a client bug: the client sends chunks that each satisfy the documented
      per-request limit; only the server can know the accumulated session.
- [x] No credential, API key, or private prompt is committed; all prompts are
      synthetic token IDs and the model runs offline from the local cache.
- [x] Not verified on CUDA: no GPU on this machine. Both crash sites are the
      model runner's host-side bookkeeping (a numpy row sized
      `(max_num_reqs, max_model_len)` and an accounting assertion), which is the
      same code on CUDA; upstream report #38428 shows the same assertion on a
      consumer GPU.

## Gate 2: usefulness

### Who gets bitten

- Affected user: anyone serving vLLM `v1` with the realtime or streaming-input
  API. That is the documented shape for live transcription, continuous video,
  and telephony-style agents, where a long-lived session accumulates input
  under one request ID by design.
- Real workflow: a realtime client streams audio or frames for minutes or hours;
  the session grows past `max_model_len` through completely normal use, and the
  server is expected to cap that one session, not die.

### The chain from user action to failure

1. The client opens a streaming session and sends chunks; each chunk is
   validated alone and accepted.
2. Chunk 2 grows the accumulated session to `max_model_len` (or beyond).
3. `_update_request_as_session` extends the prompt with no cap, and the waiting
   path schedules the full prefill with no reservation for the sampled token
   (Root cause below).
4. The KV cache manager clamps the allocation to `max_model_len`, so the
   scheduler schedules more tokens than it allocated slots for.
5. The worker either asserts while recording the sampled token (exactly at the
   limit) or fails to broadcast the prompt into a row buffer sized for
   `max_model_len` (over the limit). Either way `EngineCore` raises a fatal
   error.
6. Every in-flight and later request on the server fails with
   `EngineDeadError`; an HTTP deployment reports unhealthy. The open security
   PR #51900 measures the HTTP shape: `/health` 503 with the engine wedged.

### Consumer boundary, measured

`AsyncLLM` is the engine API the realtime endpoint calls. In
`engine-controls-237d99b4a.txt`, after the boundary session crashes the engine,
a subsequent ordinary request gets `EngineDeadError`, which is the same failure
every concurrent user sees. The end-to-end HTTP `/v1/realtime` path was not
executed on this machine (it needs a realtime speech model); the referenced
advisory and open PR #51900 pin the HTTP-visible effect to an unhealthy engine
for all users, and are cited rather than reproduced here. Measured on the engine
boundary: the engine is dead 8/8 times when a session total reaches 1024 or
more, and cleanly serving 4/4 times at 1023 or less.

### Alternative explanations ruled out

- Client error: the same chunks through a plain, non-session request of the same
  total are rejected before any worker work (3/3), so the sizes themselves are
  handled; only accumulation is.
- Configuration error: defaults. Prefix caching, chunked prefill, eager mode,
  and the pool override only pin the exact-edge size.
- Model behavior: the scheduler-level reproduction has no model, and the crash
  is in bookkeeping, not sampling.
- Version drift: reproduced on the pinned commit and on current `main` HEAD the
  next day, with workers refusing to start on unstartable pools (the KV check
  runs at startup, so a pool below `max_model_len` is not a valid configuration
  at all; the overrun needs no pool pressure).
- Adjacent, distinct family: #60137 (paused streaming sessions deadlock when the
  KV cache is full) and its fix PR #59339 are about KV pressure with many
  sessions; this defect needs one session, no pressure, and is about token
  accounting past `max_model_len`.

### Conditions and frequency

Preconditions: the streaming-input session API (AsyncLLM async-generator
prompts, realtime endpoint) under the default settings. Frequency inside that
condition: every session that reaches `max_model_len`; the threshold is exact
to one token. The trigger needs two chunks because the first chunk pauses the
session before the fold, so short one-shot uses never see it.

### Bug or not

- **Is the expected behavior really the spec?** Yes. Plain requests are refused
  at `max_model_len` by the API with output reserved
  (`_token_len_check`, `renderers/params.py:515`), the model runner assertion is
  written as an invariant of the scheduler, and the two open fix PRs agree on
  the remedy: #42502 "finishes the request with FINISHED_LENGTH_CAPPED (same as
  any other length-exceeded request)", #51900 "ends that session with a normal
  length-limit result, and keeps the engine healthy". The referenced advisory
  classifies the missing cap as a service-availability issue.
- **Have maintainers already ruled on it?** They have agreed on the requirement
  in writing (two fix PRs, one with a security advisory). Neither is merged; the
  tracker issues that preceded them were closed by automation, not by a ruling.
- **Is the trigger supported usage?** Yes: documented streaming-input feature
  with tests, documented realtime endpoint, defaults, legal per-chunk inputs.
- **Is a real boundary crossed?** Yes. The availability boundary of a shared
  inference server: one authenticated client's session kills the engine for
  every concurrent user. Secondary: the model's advertised context ceiling is
  exceeded instead of enforced.
- **What fix would a maintainer ship?** One sentence: check the renewed prompt
  plus one sampled token against `max_model_len` in
  `_update_request_as_session` (and clamp the waiting-path prefill), and finish
  the session with `FINISHED_LENGTH_CAPPED`, which is exactly what #42502 and
  #51900 propose.
- **Label**: `bug`.

## Gate 3: upstream status

- Date checked: 2026-10-18
- Upstream commit checked: `e744eacf682f2b1c7f91d2dcf348a015046e56b9`, the
  `main` HEAD at the time of check by `git ls-remote`; crash sites confirmed
  present in this tree. On the same day `main` moved one commit to `763e3f7c`
  (spec-decode config); the defect-relevant files are unchanged, verified by
  commit list and tree re-read
- Search terms: `Sampled token IDs exceed the max model length`,
  `could not broadcast input array`, `streaming input crash`,
  `resumable max_model_len`, `streaming_update overflow`,
  `realtime session length`, `cumulative length cap`, `max_model_len session`,
  plus issue and PR search for the resumable path
- Directly read and characterized: #42489, #42502, #38428, #38483, #39996,
  #40072, #45022, #51900, #51948, #53876, #49722, #58778, #60137, #59339

What exists upstream:

| Artifact | State | Relation to this claim |
|---|---|---|
| [vllm#42489](https://github.com/vllm-project/vllm/issues/42489) "Same-request resumable streaming_update can overflow prompt width in gpu_input_batch.add_request" | closed 2026-09-12 by stale automation | the over-the-limit crash site, same `gpu_input_batch` broadcast failure; still live in the tree on 2026-10-18 |
| [vllm#42502](https://github.com/vllm-project/vllm/pull/42502) "Guard resumable streaming updates against max_model_len overflow" | **open**, unmerged since 2026-05-13, reviewed by a maintainer, currently `needs-rebase`, adds 4 CPU scheduler tests | the exact fix this writeup would propose; a maintainer closed #40072 explicitly in favor of it, calling it "the same guard at the right layer: pre-mutation validation inside `_update_request_as_session` with a correct FINISHED_LENGTH_CAPPED finish reason at both call sites" |
| [vllm#38428](https://github.com/vllm-project/vllm/issues/38428) "EngineDeadError (AssertionError) on max_model_len overflow during realtime audio streaming" | closed `not_planned` 2026-07-29 by stale automation | the exactly-at-limit assertion, reported from realtime audio on CUDA hardware |
| [vllm#38483](https://github.com/vllm-project/vllm/pull/38483) "Handle max_model_len overflow gracefully instead of crashing" | closed 2026-07-29, unmerged | the assertion fix, not shipped |
| [vllm#39996](https://github.com/vllm-project/vllm/issues/39996) "[Bug] Fatal AssertionError: Encoder KV cache fails to evict tokens, exceeding max_model_len in long-lived WebSocket sessions" | closed with #40072 | the realtime-audio field report behind the early fix attempt; multiple independent repros cited in the thread |
| [vllm#40072](https://github.com/vllm-project/vllm/pull/40072) "fix: prevent streaming sessions from exceeding max_model_len" | closed 2026-09-29, unmerged | same three-layer scheduler remedy at the two call sites plus the WAITING clamp; closed in favor of #42502 as the right layer |
| [vllm#45022](https://github.com/vllm-project/vllm/pull/45022) "Voxtral realtime: fix boot OOM and engine crash at max_model_len" | merged 2026-10-09 | model-side cap for Voxtral realtime only; the PR states the scheduler, runner and output-processor changes were dropped and the generic scheduler guard is deferred to #42502. Its test `test_voxtral_realtime_generator_max_model_len` fails with `EngineDeadError` without the buffer cap |
| [vllm#51900](https://github.com/vllm-project/vllm/pull/51900) "Cap the cumulative length of realtime sessions" | **open**, `needs-rebase`, references advisory GHSA-v2m4-5w72-jf6h | names the cumulative session overflow as a shared-engine availability defect and caps it; the advisory page returned HTTP 404 unauthenticated on 2026-10-18, so it is referenced, not published |
| [vllm#51948](https://github.com/vllm-project/vllm/issues/51948), [#53876](https://github.com/vllm-project/vllm/pull/53876) | open RFC and draft | long-running session retention; same feature area, not a fix for the cap |
| [vllm#49722](https://github.com/vllm-project/vllm/pull/49722) "Finalize streaming requests at terminal sentinel" | open | output-path lifecycle work for streaming requests; adjacent, not a length cap |
| [vllm#58778](https://github.com/vllm-project/vllm/pull/58778) "Keep resumable sessions that stop while preempted" | merged 2026-10-17 (commit `f98ae6a`) | session-lifecycle work in the same fold; keeps sessions that stop while preempted and reworks `_update_request_as_session`. Does not cap cumulative length; the merge is included in the tested `e744eac` tree |
| [vllm#60137](https://github.com/vllm-project/vllm/issues/60137), [#59339](https://github.com/vllm-project/vllm/pull/59339) | open | paused sessions deadlock under KV-cache pressure; distinct mechanism (this needs no pressure) |

Classification:

- [ ] Novel for the exact claim
- [x] Duplicate, open and still reproducible: the defect is reported (#42489,
      #38428) and fix proposals are open (#42502, #51900, the latter security
      tagged), but nothing is merged and the crash reproduces on current `main`.
      Both tracker issues were closed by stale automation, not by a fix, and a
      maintainer closed the early fix attempt #40072 in favor of #42502 while
      explicitly deferring the generic guard there in merged #45022. Labeled and
      linked per the repository decision rule.
- [ ] Fixed on current release
- [ ] Regression
- [ ] Documented behavior
- [ ] Discussed upstream without a dedicated ticket
- [ ] Incomplete, current upstream state could not be verified

## Root cause

`vllm/v1/core/sched/scheduler.py:1728`, `_update_request_as_session`. The fold
keeps every output token but the final sampled one and then extends the prompt
with the update's tokens (1756-1757) and recomputes `num_prompt_tokens`
(1760). No comparison against `max_model_len` is made here or at either
call site (`_handle_stopped_request`, 2552-2572; `add_request`, 2705-2715). The
per-request validation sees the chunk alone (`_token_len_check`,
`vllm/renderers/params.py:515`, limit `max_input_tokens = max_total_tokens`);
the accumulated session length is never validated again.

The second half is scheduling. In the waiting loop, `num_new_tokens` is
`request.num_tokens - num_computed_tokens` (1184, under a `token_budget`);
the clamp that holds back `num_sampled_tokens_per_step` exists only on the
running path (`scheduler.py:769-776`). A waiting session therefore prefills up
to the accumulated length with no reservation for the token the step will
sample.

The KV cache manager allocates with
`num_tokens_need_slot = min(num_tokens_main_model + num_lookahead_tokens,
max_model_len)` (`vllm/v1/core/kv_cache_manager.py:542`), so the allocation is
clamped while the scheduled compute is not. At a session total of exactly
`max_model_len` the worker's `_bookkeeping_sync` records the sampled token one
past the limit and trips `assert end_idx <= self.max_model_len`
(`vllm/v1/worker/gpu_model_runner.py:3564-3570`). At a total above
`max_model_len`, `GPUInputBatch.add_request` broadcasts the prompt into
`token_ids_cpu[req_index, :num_prompt_tokens]`, a row sized `max_model_len`
(`vllm/v1/worker/gpu_input_batch.py:371`), and raises
`ValueError: could not broadcast ...`. Both exceptions escape the worker RPC
(`vllm/v1/executor/multiproc_executor.py:1055`) and become
`EngineCore encountered a fatal error` (`vllm/v1/engine/core.py:1545`).

## Wire evidence

All under [`transcripts/104-vllm-streaming-session-max-model-len-kills-engine/`](../../transcripts/104-vllm-streaming-session-max-model-len-kills-engine/):

- `scheduler-repro-e744eac.txt`: deterministic, model-free scheduler run, the
  invariant counted in blocks (64 held, 65 needed) and the four controls.
- `engine-1033-spaced-{237d99b4a,e744eac}.txt` and
  `engine-1033-bunched-237d99b4a.txt`: over-the-limit crash, spaced and
  bunched, pinned commit and current `main`.
- `engine-1024-assert-{237d99b4a,e744eac}.txt`: exactly-at-limit crash.
- `engine-1025-broadcast-237d99b4a.txt`: over-by-one crash.
- `engine-controls-237d99b4a.txt`: clean under-limit session, plain-request API
  rejection, health request, boundary crash, dead engine afterwards.
- `engine-single-10{23,24,25}-237d99b4a.txt`: plain requests at and around the
  limit, all handled correctly by the API.
- `fuzz-discovery-seed{85,86,91,92}.ops.txt`: the resumable-session ops
  traces recorded when the scheduler fuzzer first surfaced this class of
  over-length scheduling; the smallest shape is replayed with exact expected
  values by `repro_session_overlength.py` and `scheduler-repro-e744eac.txt`.
- `repro_session_overlength.py`, `engine_session_overlength.py`: the two
  programs, committed with the logs.

## Frozen invariant

- The invariant, in words: the tokens a step schedules for a request never
  exceed the KV slots that request holds, and a request is never scheduled past
  `max_model_len`.
- `repro_session_overlength.py` freezes it at the scheduler boundary with no
  model, no engine and no RNG: it asserts `computed <= max_model_len` and
  `ceil(computed / block) <= blocks_held` after the session update, with
  three controls.
- No Rust conformance case is added: the kairo suite replays HTTP captures, and
  this defect is below the HTTP translation layer, in the engine's token
  accounting. The Python repro is the permanent test; upstream fix PR #42502
  carries the equivalent CPU unit tests for the same invariant.

## Validation

| Check | Command | Result |
|---|---|---|
| Scheduler reproduction | `.venv/bin/python ../vllm-hunt/repro_session_overlength.py` | PASS, 5/5 rounds `REPRO OK` |
| Engine reproduction | `engine_session_overlength.py --chunk2 1000` and `--chunk2 991` | PASS, both `EngineDeadError` with the expected worker exception |
| Engine controls | `engine_session_overlength.py --controls`, `--single 1023/1024/1025` | PASS, clean finish, API rejections, healthy engine |
| Current `main` check | `git ls-remote` + rerun at `e744eac`; reviewer commit-list check of `763e3f7c` | PASS, crash sites present, both crashes reproduced, defect-relevant files unchanged on today's HEAD |
| README counts | `python3 tools/update-readme-counts.py --check` | PASS, 75 bugs, 301 tests |
| Harness | `cargo fmt --all -- --check`, `cargo clippy --workspace --all-targets -- -D warnings`, `cargo test --workspace` | PASS, clean, 311 tests green (227 conformance, 74 unit, 10 adversarial) |

## Security and scope

- [x] No API key, credential, private prompt, or unsanitized provider response
      is committed. All prompts are synthetic token IDs; the model runs offline.
- [x] Only the local engine was exercised; no hosted service, no third-party
      tenant, no rate limit bypass.
- [x] The writeup states what was not verified: HTTP `/v1/realtime` end-to-end
      (needs a realtime speech model) and CUDA (no GPU on this machine).
- [x] The advisory GHSA-v2m4-5w72-jf6h is referenced as cited from the public
      PR #51900; it was not retrievable unauthenticated on 2026-10-18 and no
      private correspondence is reproduced.
- [x] The pull request contains one finding.
- [x] Unrelated working tree changes are preserved.

## Author verdict

- Correctness: PASS
- Usefulness: PASS
- Upstream status: PASS, classified `duplicate-open` and linked
- Overall: ACCEPT

## Independent review

An independent review ran on 2026-10-18 under the
`kairo-reproduction-reviewer` protocol (read-only, reviewer logs kept outside
the repository). Verdict: **ACCEPT**, all three gates PASS.

- Reran, not repeated: the scheduler repro 5/5 with control values matching
  `scheduler-repro-e744eac.txt` exactly; the boundary session at 991 crashed
  with the same assertion and `EngineDeadError`; 990 finished clean; 992 and
  1000 crashed at the broadcast site; plain 1023 finished and plain 1024 was
  rejected with `VLLMValidationError`. Updated N of N: 12/12 engine runs at a
  session total of 1024 or more died (`EngineDeadError`), 7/7 at 1023 or below
  were clean, 4/4 plain same-size requests were rejected by the API.
- Falsification attempts: changing only the pool size (40 blocks, 1280 usable
  tokens) leaves both crash sites unchanged, so pool pressure is not the cause;
  32 blocks refuses to start (`estimated maximum model length is 992`), so 33 is
  the exact startup minimum and the crash runs have idle cache; removing the
  trigger by one token is clean; bunched and spaced chunks crash identically;
  non-session requests of the same sizes are rejected by the API.
- Verified: every file and line citation in this writeup exists in the tested
  tree and reads as described; the tested `e744eac` is one commit behind the
  live `main` (`763e3f7c`) with no change to any defect-relevant file; the
  upstream artifacts and states in Gate 3 match the live pages (including
  #40072's closure in favor of #42502 and #45022's model-side cap deferring the
  generic guard to #42502); counts check passes; no credential-like strings and
  no em dashes in the committed artifacts.
- Corrections required and applied: the transcript's memory-gate sentence about
  earlier aborted starts was removed; the fuzz row no longer cites uncommitted
  numbers; the `num_prompt_tokens` citation was fixed to line 1760; the
  `main`-HEAD wording, the #58778 mention, and the missing #39996, #40072 and
  #45022 rows were fixed.
- Residual uncertainty, unchanged and stated: CUDA is not locally tested, the
  HTTP `/v1/realtime` path is not executed end-to-end, the pinned `237d99b4a`
  logs are author-produced (the reviewer could not check that commit out
  without touching the pinned tree), and the referenced advisory is not
  published.
