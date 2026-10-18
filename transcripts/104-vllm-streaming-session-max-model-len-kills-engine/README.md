# 104 transcript: vLLM resumable streaming session grows past `max_model_len` and kills the engine

Finding writeup: [`../../issues/104-vllm-streaming-session-max-model-len-kills-engine/README.md`](../../issues/104-vllm-streaming-session-max-model-len-kills-engine/README.md)

## What is recorded here

Two repro programs and their raw runs against vLLM `v1`:

- `repro_session_overlength.py` drives the real scheduler from
  `tests/v1/core/utils.py` with no model and no engine, five rounds,
  deterministic, and asserts the scheduler schedules tokens the KV cache
  manager never allocated slots for.
- `engine_session_overlength.py` drives the real `AsyncLLM` engine (multi-process
  `EngineCore`, real worker) with `facebook/opt-125m` and a resumable
  streaming-input session whose second chunk pushes the accumulated session past
  `max_model_len`. Every crash below is `EngineDeadError` on the client and a
  fatal error in the `EngineCore` process.

## Environment

- macOS arm64 (Apple Silicon), CPU backend (`device_config=cpu`), vLLM from
  `tools/vllm`, checked out at `237d99b4aff67ecfedacca7acb0ee93f299fa54e`
  (2026-10-17) for the `-237d99b4a` logs and at `e744eacf682f2b1c7f91d2dcf348a015046e56b9`
  (2026-10-18, current `main` HEAD) for the `-e744eac` logs.
- Model: `facebook/opt-125m` from the local HF cache, no network needed.
- Engine config (identical in every engine log, from the engine's own config dump):
  `max_seq_len=1024`, `block_size=32`, `num_gpu_blocks_override=33` (32 usable
  blocks after the permanently reserved null block = exactly 1024 tokens =
  `max_model_len`), `enforce_eager=True`, `enable_prefix_caching=True`,
  `enable_chunked_prefill=True`, no speculative config.
- `gpu_memory_utilization` is 0.35 in the `237d99b4a` runs and 0.2 in the
  `e744eac` runs. On the CPU backend this flag only gates startup against free
  RAM; the KV pool size is fixed by `num_gpu_blocks_override`, so the runs are
  equivalent. Each committed `e744eac` log contains exactly one engine run.

## How to rerun

From `tools/vllm` with the project venv:

```sh
.venv/bin/python ../vllm-hunt/repro_session_overlength.py
.venv/bin/python ../vllm-hunt/engine_session_overlength.py --chunk2 1000 --label bug   # 32+1+1000 = 1033 > 1024
.venv/bin/python ../vllm-hunt/engine_session_overlength.py --chunk2 991  --label bnd   # 32+1+991  = 1024 == max_model_len
.venv/bin/python ../vllm-hunt/engine_session_overlength.py --controls                  # clean + rejection controls
.venv/bin/python ../vllm-hunt/engine_session_overlength.py --single 1023               # plain request, clean
.venv/bin/python ../vllm-hunt/engine_session_overlength.py --single 1024               # plain request, API rejects
```

Chunk arithmetic: chunk 1 is a 32-token prompt and the session generates
`max_tokens=2`; when the chunk completes, `_update_request_as_session` keeps all
output tokens but the final sampled one, so the session carries 33 tokens into
chunk 2. Chunk 2 lengths used: 990 (total 1023), 991 (1024), 992 (1025),
1000 (1033).

## Cells

| File | Scenario | Result |
|---|---|---|
| `scheduler-repro-e744eac.txt` | scheduler-level, 5 rounds, pool exactly 1024 tokens | BUG `computed=1032`, 64 blocks held, 65 needed; 4 controls clean; 5/5 `REPRO OK` |
| `engine-1023-clean-237d99b4a.txt` | session total 1023 | clean finish, 3 outputs, no error |
| `engine-1024-assert-237d99b4a.txt` | session total 1024 | `AssertionError: Sampled token IDs exceed the max model length. Total number of tokens: 1025 > max_model_len: 1024` at `gpu_model_runner.py:3569`, `EngineDeadError` |
| `engine-1024-assert-e744eac.txt` | same, current `main` HEAD | identical crash |
| `engine-controls-237d99b4a.txt` | one engine: under-limit session (833), oversized plain request (1033), health request, then the 1024 boundary session | session clean; plain request rejected by the API (`VLLMValidationError`); health request fine; boundary session kills the engine; every later request `EngineDeadError` |
| `engine-1025-broadcast-237d99b4a.txt` | session total 1025 (over by one) | `ValueError: could not broadcast input array from shape (1025,) into shape (1024,)` at `gpu_input_batch.py:371`, `EngineDeadError` |
| `engine-1033-spaced-237d99b4a.txt` | session total 1033, chunks spaced | same broadcast crash (1033,) vs (1024,) |
| `engine-1033-spaced-run2-237d99b4a.txt` | repeat | identical |
| `engine-1033-bunched-237d99b4a.txt` | chunks yielded back to back | identical, 0.7s |
| `engine-1033-spaced-e744eac.txt` | same, current `main` HEAD | identical |
| `engine-single-1023-237d99b4a.txt` | plain request, 1023 tokens | clean finish |
| `engine-single-1024-237d99b4a.txt` | plain request, 1024 tokens | `VLLMValidationError` at the API (prompt + requested output exceeds the model length) |
| `engine-single-1025-237d99b4a.txt` | plain request, 1025 tokens | `VLLMValidationError` at the API |
| `fuzz-discovery-seed{85,86,91,92}.ops.txt` | the resumable-session ops traces recorded when the scheduler fuzzer first surfaced the over-length scheduling | discovery records; the smallest shape is replayed with exact expected values by `repro_session_overlength.py` and `scheduler-repro-e744eac.txt` |

In every engine crash log the scheduler's own dump of the offending step is
retained, e.g. `prompt_token_ids_len=1033`, `num_computed_tokens=33`,
`num_scheduled_tokens={...: 1000}`, `block_ids=([1...32])`, followed by the
worker traceback and `EngineCore encountered a fatal error`.

No credential, API key, or private prompt appears in any capture; all prompts are
synthetic token IDs, and the model runs offline from the local cache.
