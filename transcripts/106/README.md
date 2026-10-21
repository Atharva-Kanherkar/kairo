# Transcripts for finding 106

Dynamo tool-carrying requests are not migrated when their worker dies. See
[`issues/106-dynamo-tool-requests-skip-migration/`](../../issues/106-dynamo-tool-requests-skip-migration/README.md).

Recorded 2026-10-20 17:20 to 18:57 UTC on one RunPod secure-cloud pod: NVIDIA
RTX A6000, driver 595.91.07, CUDA 13.2, image
`runpod/pytorch:1.4.1-cu1300-torch291-ubuntu2404`, Python 3.12.3. Model
`Qwen/Qwen3-4B-Instruct-2507`, two vLLM workers, tool-call parser `hermes`,
frontend `--migration-limit 3`.

## Layout

Every run directory holds:

- `client-request.json`: the exact request body sent.
- `client-response.raw`: the HTTP status the probe recorded on the first line
  (`HTTP 200`), then the response body bytes exactly as received
  (`httpx` `iter_raw`). Response headers were not saved.
- `kill.json`, for runs with a kill: the worker index and PID that was
  SIGKILLed (with its EngineCore child) and the delay after the request was
  sent. Runs without this file were not killed.

Run directory names are `<tag>-<cell>-<trigger>-<run>`. The cell gives the
endpoint: `chat*` is `/v1/chat/completions`, `messages*` is `/v1/messages`,
`responses*` is `/v1/responses`, and `*_tools` cells carry a `write_file` tool.
`s4` means killed 4 s after sending, `e300` after 300 SSE events.

| Directory | Build and setting | Runs |
|---|---|---|
| `main-default-kill/` | main `7aaf17b62`, defaults | 15 tool streams killed (5 per endpoint) |
| `main-structural-tag-off-kill/` | same build, `DYN_ENABLE_STRUCTURAL_TAG=false` on frontend and workers | the same 15 |
| `release-1.5.1-kill/` | `ai-dynamo==1.5.1`, vLLM 0.28.0, defaults | 6 tool streams killed, 1 no-kill control |
| `main-text-kill-controls/` | main `7aaf17b62`, defaults | 4 text streams and 1 non-streamed text request killed |
| `main-no-kill-controls/` | main `7aaf17b62`, defaults | 6 requests, no kill |

- `consumer/`: one JSON line per SDK agent turn from `scripts/consumer.py`.
  `default.jsonl` is defaults with a kill at 4 s, `nost.jsonl` is the same with
  structural tags off, and `default-nokill.jsonl` is defaults with no kill.
- `logs/frontend.log`: the frontend log for every rig start, with ANSI color
  codes removed and nothing else changed. Each start begins with an
  `== start <UTC time>` line. Dynamo's own log text is kept verbatim.
- `logs/nofn-default.txt`, `logs/nofn-nost.txt`: the matrix summaries for the
  two 15-run sets.
- `environment/`: `fingerprint.txt` (GPU, driver, CUDA, tested revision),
  `main-REVISION`, and `freeze-main.txt` / `freeze-151.txt` (package lists of
  the two environments).
- `scripts/`: `install.sh`, `build.sh`, `rig.sh`, `probe.py`, `analyze.py`,
  `matrix.sh`, `nofn.sh`, `consumer.py`, as run on the pod.

Worker logs are not committed. They hold vLLM startup output and progress bars,
and the migration decisions are in the frontend log.
