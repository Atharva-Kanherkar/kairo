# 144 transcript: Dynamo idle LoRA pin

Target: `ai-dynamo/dynamo` at `3a524d0d70` (upstream `main` on 2026-11-10; the four files are unchanged on current `main` `4b1a320423`).

## Run

From a clean Dynamo checkout at the pinned commit:

```sh
cp transcripts/144-dynamo-lora-inactive-pin/repro_inactive_pin.rs /path/to/dynamo/lib/lora/tests/
cp transcripts/144-dynamo-lora-inactive-pin/soak_inactive_pin.rs /path/to/dynamo/lib/lora/tests/
cp transcripts/144-dynamo-lora-inactive-pin/capture_inactive_pin.rs /path/to/dynamo/lib/lora/tests/inactive_pin_capture.rs
cp transcripts/144-dynamo-lora-inactive-pin/worker_boundary.py /path/to/dynamo/

cd /path/to/dynamo
cargo test -p dynamo-lora --test repro_inactive_pin -- --nocapture
cargo test -p dynamo-lora --test soak_inactive_pin -- --nocapture --test-threads=1
cargo test -p dynamo-lora --test inactive_pin_capture -- --nocapture
python3.12 worker_boundary.py
```

The three Rust files are integration tests over the real `dynamo-lora` crate. They do not copy the controller or the filter. The Python file loads the real `components/src/dynamo/vllm/lora_state.py` and stubs only the imported names (`vllm.lora.request.LoRARequest`, `LoRAInfo`, `InvalidArgument`).

## Expected

- `repro_inactive_pin`: idle adapter `idle` dispatches to worker 1, which hosts `busy` and not `idle`; worker 10000 hosts `idle` and is not dispatched. The active control dispatches to worker 10000.
- `soak_inactive_pin`: idle 655 host-only, 1232 non-host of 1887; active 1887 host-only, 0 non-host.
- `inactive_pin_capture`: two JSONL records, one bug and one control.
- `worker_boundary`: `InvalidArgument: unknown model or LoRA adapter: 'idle'` on the worker without it, and a `LoRARequest` on the worker that hosts it.

## Files

- `repro_inactive_pin.rs`, minimal case and active control.
- `soak_inactive_pin.rs`, paired 2000-cluster soak at the filter's dispatch boundary.
- `capture_inactive_pin.rs`, emits the two records.
- `capture-inactive-pin.jsonl`, the recorded records.
- `worker_boundary.py`, the worker-side rejection.
- `run-2026-11-11.txt`, the full run output.
