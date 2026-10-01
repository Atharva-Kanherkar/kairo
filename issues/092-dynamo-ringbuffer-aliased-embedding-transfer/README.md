# 092, Dynamo's multimodal `RingBuffer` hands two in-flight requests the same bytes, so one request's image embeddings overwrite another's in place

- **Upstream**: [ai-dynamo/dynamo](https://github.com/ai-dynamo/dynamo), no matching ticket. Classification: `novel` as of 2026-10-01. Search terms used: `RingBuffer`, `get_buffer`, `embedding_transfer`, `allocated_start_idx`, `ring buffer wrap multimodal`, `nixl write data corruption`, `embedding transfer wrong image`, `multimodal embedding mixup`, `ring buffer aliasing`, `multimodal cross request`; issues and PRs #6651 (the PR that added this code), #14795, #11179, #13626, #12524, #13327, #15209 checked and unrelated. Commit history for the file (5 most recent: #10094, #9073, #7850, #7482, #6913) shows no fix. Date checked 2026-10-01 against upstream `main` = `938d89b25e28`.
- **Tool under test**: NVIDIA Dynamo. The file under test is byte-identical to upstream `main` on the reproduction date: `components/src/dynamo/common/multimodal/embedding_transfer.py`, sha256 `3114c81e35ef96a95da7a381c0dfa362a912ec524c212e49bcebf3d1a36a5a56` on both the local checkout (commit `5593e8857`) and `raw.githubusercontent.com/ai-dynamo/dynamo/main` fetched the same day. Defect at `embedding_transfer.py:345`, in `RingBuffer.get_buffer`, introduced with the NIXL WRITE embedding transfer in [#6651](https://github.com/ai-dynamo/dynamo/pull/6651) (merged 2026-03-02) and unchanged since. Reached by default: `vllm/handlers.py:1351` builds `NixlWriteEmbeddingReceiver()` when `config.embedding_transfer_mode == NIXL_WRITE`, and `vllm/backend_args.py:198` sets `default=EmbeddingTransferMode.NIXL_WRITE.value` for `--embedding-transfer-mode`.
- **Reproduced**: 2026-10-01, macOS arm64, no GPU, no NIXL fabric. Python 3.12 venv (torch 2.14.1 CPU, msgspec, pydantic). **25/25 bug runs and 0/25 control runs contaminated, zero variance**; the allocator-level harness reproduces 200/200, and a 4,000-trial randomized soak finds the same overlap with no control trial producing it.

## What breaks

`RingBuffer` is the fixed staging area that a multimodal prefill worker hands to its encode worker over RDMA. Each in-flight request is given a byte range, the range's address is published to the peer, and the peer writes that request's image embeddings into it. The allocator's overlap guard compares against a watermark that is only valid before the first wrap, so **two requests that are in flight at the same time can be given ranges that cover the same bytes**. The second request's NIXL WRITE lands inside the first request's live range, and the first request goes on to answer about the wrong image.

Nothing raises. No log line is written. Both requests return HTTP 200.

Who it hurts: anyone serving a multimodal model through Dynamo vLLM with the default `--embedding-transfer-mode nixl-write`, on workloads large enough to wrap the 8 GiB ring buffer. The buffer is sized for roughly 1,024 multimodal token items, so wrapping is a matter of how many images a request carries and how many requests overlap, not of any unusual configuration.

Conditions: the tail of the buffer must be too small for the next request, and two wrapping requests must overlap in time. Both are ordinary states for a prefill worker under concurrent multimodal load.

Measured versus inferred: the allocator handing out aliasing ranges, and one request's returned tensor holding another request's bytes, are **measured**, N of N. The model answering about the wrong image is **inferred** from the measured tensor contents, because no GPU was available here to run a real vision model. The chain is direct: the tensor the prefill worker attaches to the request is a view of exactly the range the allocator returned, and `_accumulate_embeddings` feeds it to the engine (`vllm/multimodal_utils/prefill_worker_utils.py:466`).

## Root cause

`components/src/dynamo/common/multimodal/embedding_transfer.py:345`:

```python
# If the allocation will go over end boundary, simply try allocate from the start
if self.free_start_idx + size > self.end_idx:
    # Not enough space even after wrap around, reject the allocation early
    # so we don't mark the remaining space "used"
    if self.allocated_start_idx < size:
        return None, None
    # add artificial entry to freed_list to treat the remaining space to be
    # allocated and released.
    self.freed_list[self.free_start_idx] = self.end_idx
    self.free_start_idx = 0
    self.wrapped_around = True
start_idx = self.free_start_idx
end_idx = start_idx + size

# Check availability of the buffer, if the allocation overlaps with allocated buffer,
# return None for the caller to retry later after some buffers are released.
if self.wrapped_around and end_idx > self.allocated_start_idx:
    return None, None
```

The comment on line 343 states the intended invariant: *"if the allocation overlaps with allocated buffer, return None"*. The guard on line 345 does not implement it.

`allocated_start_idx` is the exclusive lower bound of the reusable head region, which is correct only **before** the first wrap. The wrap branch on lines 329-339 places the new allocation at `[0, size)` and leaves `allocated_start_idx` at its old, larger value. A later allocation that also wraps takes the same branch, resets `free_start_idx` to `0` again, and is measured against that stale watermark. Because the stale bound is larger than the first post-wrap allocation, the guard admits a second allocation at `[0, size2)` that overlaps the still-live `[0, size1)`.

The freed-list side has the same hole. Line 337 writes an artificial `freed_list[x] = end_idx` that claims the whole tail is one contiguous free run. `_flush_freed_list` (line 296) then chains `allocated_start_idx` straight past any buffer still live inside that tail, so `[free_start_idx, allocated_start_idx)` is not actually free and the guard is unsound from that direction too.

Either way the invariant is broken only when a release advances the watermark between two wraps. The shipped test `common/tests/multimodal/test_embedding_transfer.py::test_wrap_around` (line 222) only ever reaches **one** wrap, and no test in the suite asserts that two live buffers are disjoint, so the gap is untested.

The same file also returns the buffer to the pool on a receive timeout while the peer's WRITE may still be in flight (`embedding_transfer.py:773`), which is a second route to the same aliased state. That one is not reproduced here and is left as a lead.

## Wire evidence

`transcripts/092/`:

- `repro_receiver_crossover.py`, the end-to-end harness. It drives the verbatim `NixlWriteEmbeddingSender` and `NixlWriteEmbeddingReceiver`, including the real `send_embeddings` / `receive_embeddings` round trip and the real notification handshake. Each class is extracted by `ast` line range from the pinned file and `exec`'d unmodified; the whole-file sha256 and the extracted-slice sha256 are printed by the harness so a reviewer can confirm nothing was retyped. The only substitute is the NIXL agent, replaced by a fake whose `transfer` performs the byte copy a NIXL WRITE performs over RDMA into the address range the receiver published. Everything about how that range is chosen, published, written, and read back is Dynamo code.
- `capture-embedding-crossover.jsonl`, the machine-readable capture consumed by the Rust checker. Two records: the bug case and a control.
- `repro-run-2026-10-01.txt`, the full end-to-end run output including the N of N counts.
- `repro_ringbuffer_overlap.py`, an independent allocator-only harness. It lifts `RingBuffer` and `MonolithicCounter` verbatim and runs seven cases: the minimal trigger, the same shape at the production 8 GiB default, a byte-level aliasing demonstration, a no-second-wrap control, a 200-run determinism count, a 4,000-trial randomized soak, and a small-sizes control soak.
- `repro-ringbuffer-2026-10-01.txt`, its full output.

### Bug case

24 MiB ring buffer. Requests arrive in this order, with sizes scaled from the verified 24-byte minimal trigger:

| step | request | payload | buffer after |
|---|---|---|---|
| 1 | image 1 | 20 MiB | live `[0, 20971520)` |
| 2 | image 2 | 3 MiB | live `[0, 20971520)`, `[20971520, 24117248)` |
| 3 | image 1 completes and releases | | watermark `allocated_start_idx` advances to 20971520 |
| 4 | image 3 | 12 MiB | wraps to `[0, 12582912)`, stays in flight |
| 5 | image 4 | 14 MiB | wraps again, admitted at `[0, 14680064)` |

At step 4 the tail has only 1 MiB free, so image 3 wraps. At step 5 the tail is still too small, so image 4 wraps too, and the stale-watermark guard admits it over image 3's live range:

```
[BUG: two in-flight requests both wrap] buffer=24 MiB
  id=1 [20971520,24117248)  3145728 bytes  ptr=27011317760
  id=2 [0,12582912)  12582912 bytes  ptr=26990346240
  id=3 [0,14680064)  14680064 bytes  ptr=26990346240
  pairs of live buffers that alias: [(2, 3)]

  request sent image 2 and holds embedding value 2
  request sent image 3 and holds embedding value 4
  request sent image 4 and holds embedding value 4
  at least one request received another request's image: True
```

`id=2` and `id=3` have the **same `data_ptr()`**. The request that sent image 3 holds image 4's embeddings.

### Control

The identical request sequence, identical payloads, and identical concurrency on a 64 MiB buffer, large enough that neither late request wraps:

```
[CONTROL: requests do not both wrap] buffer=64 MiB
  id=1 [20971520,24117248)  3145728 bytes  ptr=27078426624
  id=2 [24117248,36700160)  12582912 bytes  ptr=27081572352
  id=3 [36700160,51380224)  14680064 bytes  ptr=27094155264
  pairs of live buffers that alias: []

  request sent image 2 and holds embedding value 2
  request sent image 3 and holds embedding value 3
  request sent image 4 and holds embedding value 4
  at least one request received another request's image: False
```

Only the buffer size differs. This isolates the allocator's wrap behavior as the cause and rules out the request pattern, the payloads, and the transfer path.

### Determinism and scale

- End-to-end: **25/25** bug runs contaminated, **0/25** control runs contaminated.
- Allocator-only minimal trigger: **200/200**.
- Randomized soak at the production geometry (4,000 trials, 15,742 allocations checked): 13 overlapping allocations across 6 trials; the first at 91% of the buffer occupied. The control soak (19,088 allocations, sizes too small to wrap twice): **0** overlaps.

The defect is scale invariant, because `get_buffer` decides purely on index comparisons. The same 5-step shape was confirmed at the production default of `NixlWriteEmbeddingReceiver(buffer_size=2*8*1024*1024*256*2)`, 8 GiB:

```
[production B=8 GiB] buffer=8589934592
  get(7129645711)  -> id=0 [0,7129645711)
  get(1073741824)  -> id=1 [7129645711,8203387535)
  release(0)
  get(4294967296)  -> id=2 [0,4294967296)
  get(4982162063)  -> id=3 [0,4982162063)  *** ALIASES [(2, (0, 4294967296))] ***
```

## Test

- `ring_buffer_allocations_are_disjoint` in `crates/harness/src/checks.rs`. The invariant, not the bug: buffers a ring allocator has handed out and not yet released must cover disjoint byte ranges, and every in-flight request must hold the embeddings of the image it sent. It reads only recorded capture, so it does not care how the ranges were produced.
- Conformance: `dynamo_ring_buffer_aliased_live_allocations_violation` asserts `Violation` on `transcripts/092/capture-embedding-crossover.jsonl`; `dynamo_ring_buffer_non_wrapping_control_is_conformant` asserts `Conformant` on the control record from the **same** capture file, so the checker is proven to accept a correct allocator on the very same request sequence. The day Dynamo stops aliasing, the violation test flips and says so.
- Unit tests for the checker: `ring_buffer_aliased_live_ranges_are_caught`, `ring_buffer_disjoint_live_ranges_are_conformant`, `ring_buffer_matched_request_markers_are_conformant`, `ring_buffer_empty_live_set_is_conformant`, `ring_buffer_malformed_capture_is_rejected`.

## Bug or not

- **Is the expected behavior really the spec?** Yes. The code's own comment on line 343 states the invariant it fails to enforce, and the class is documented as a ring buffer that "reuses memory without wrapped-around allocations". Nothing documents aliasing as intended, and a test for one wrap passing says nothing about two.
- **Have maintainers already ruled on it?** No. The introducing PR #6651 merged 2026-03-02 with this code intact. No issue, PR, or review comment addresses the second-wrap case. The file's five most recent commits are unrelated.
- **Is the trigger supported usage?** Yes. Default configuration: `--embedding-transfer-mode` defaults to `nixl-write`, and the 8 GiB ring buffer is the shipped default size. The trigger is two overlapping multimodal requests whose payloads exceed the remaining tail, which is ordinary load.
- **Is a real boundary crossed?** Yes. Two distinct client requests, which may belong to different users, are served from one another's embedding bytes. The prefill worker's staging area is supposed to isolate concurrent requests from each other.
- **What fix would a maintainer ship?** One sentence: recompute the wrap decision against the live occupancy instead of the stale `allocated_start_idx` watermark, so a second post-wrap allocation is refused rather than admitted at `[0, size)`, and do not write an artificial `freed_list` entry that spans live buffers.

Label: `bug`.

## What was not verified

- No GPU was available, so the run did not execute a real vision model. The model answering about the wrong image is inferred from the measured tensor contents, not measured on a served response.
- No NIXL fabric was present. The WRITE was performed by a fake agent that copies bytes into the published range, which is the operation NIXL performs; it is not a measurement of RDMA behavior, and it cannot rule out fabric-specific effects.
- The frequency in a specific production deployment was not measured. The trigger needs the tail to be too small for the next request, so it scales with image count per request and request concurrency. The soak bounds the shape of the trigger, not its rate in the field.
- The related receive-timeout path at `embedding_transfer.py:773`, which also returns a buffer to the pool while a WRITE may be outstanding, was identified but not reproduced.
