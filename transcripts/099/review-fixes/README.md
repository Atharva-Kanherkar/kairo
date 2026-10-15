# Fixes for the round-1 review (PR #65, comment 6184498286)

1. `sse_data_json` now accepts `data:` with or without the optional space, as
   the SSE specification allows. Regression unit test
   `engine_calls_checker_accepts_sse_data_without_the_optional_space` fails on
   the old helper and passes now. The reviewer's case, the committed
   `capture/nightly-tpc1/hcl_ctrl-openai-stream-01` control with every
   `data: ` rewritten to `data:`, now returns `Conformant`, as does the
   original.
2. `rig/start.sh` creates `/rig/logs` before launching either service.
3. `rig/capture.py` waits in `wait_final` until the engine record's
   `termination` is something other than `running`. `rig/test_capture.py`
   writes a `running` record, finalizes it 0.5 s later, and asserts the wait
   returns the final record; both of its tests fail under the old truthiness
   rule.

`clean-rig-capture/` is the evidence for 2 and 3: on 2026-10-15 a fresh
container (the same `python:3.12-slim` image with the pinned nightly installed)
got a copy of only the committed `rig/` directory mounted at `/rig`, with no
`/rig/logs`. Commands, in order:

```text
ls /rig/logs                      # No such file or directory
TPC=1 ./start.sh hermes none      # frontend up; /rig/logs/{engine,frontend}.log created
python3 /rig/capture.py /rig/out 1 hcl_real hcl_ctrl
python3 /rig/test_capture.py      # OK
```

All 8 engine records are `finished_stop`. Checked with
`engine_tool_calls_reach_client`: the 4 `hcl_real` cells lose the call, and
the 4 `hcl_ctrl` cells are conformant.
