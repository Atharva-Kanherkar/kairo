# 144, Dynamo's idle LoRA pin ignores the workers that host the adapter, so the request is dispatched to a worker that rejects it as an unknown adapter

- **Upstream**: [ai-dynamo/dynamo](https://github.com/ai-dynamo/dynamo), no matching ticket. Classification: `novel` as of 2026-11-11. Search terms used: `inactive LoRA`, `cold-start replica`, `single HRW`, `slot-aware pin`, `lora pin capacity`, `adapter not loaded`, `unknown model or LoRA adapter`, `LoRA allocation`, `lora route`, `loaded workers`; 75 LoRA-titled issues since 2026-06-01, the 245 open bug-labeled issues, and the open PR list checked. Closest artifacts are [#11163](https://github.com/ai-dynamo/dynamo/issues/11163) (a failed model-card publish leaves an adapter unroutable), [#14394](https://github.com/ai-dynamo/dynamo/issues/14394) (a request for nightly LoRA churn tests), and the open docs PR [#14526](https://github.com/ai-dynamo/dynamo/pull/14526) (which states that inactive pins may exceed worker capacity by design). None describes this host omission. Current upstream `main` on 2026-11-11 is `4b1a320423`; the four files below are unchanged since the pinned commit `3a524d0d70`.
- **Tool under test**: NVIDIA Dynamo. The files under test are byte-identical to upstream `main` on the reproduction date: `lib/lora/src/controller.rs`, sha256 `02e26bbf5032912892e0bae2b031c90c5cead352c9b2e13fd8daa2e464ba9d69`; `lib/lora/src/routing/hrw.rs`, sha256 `f429a4c68090248178f90dc8748b47d61511b00c46e8efdaceca6775f8222144`; `lib/lora/src/filter.rs`, sha256 `f7b2aa9bb3a04847ade1962c060ce1d111ef56fdc3e335790ede4dca7c06d5ce`; `components/src/dynamo/vllm/lora_state.py`, sha256 `40c260c36bf09c0a2616cd45f45fc4a5b2a63f0f72b425495f29f94d564f3d20`. Defect at `controller.rs:661-679`, in the inactive branch of `recompute_per_lora`, and at `filter.rs:206-216`, which forwards the pin. Reached by default: the allocation controller is enabled when `DYN_LORA_ENABLED=true` (`LoraAllocationConfig::default().enabled == true`), and `DYN_LORA_ALLOCATION_ALGORITHM` defaults to `hrw`.
- **Reproduced**: 2026-11-11, macOS arm64, no GPU, no vLLM worker. Rust 1.96.0, Python 3.12.13. The harness runs the real `dynamo-lora` crate from the pinned checkout, not a copy: `LoraController`, `LoraRoutingTable`, `LoraStateTracker`, `LoadEstimator`, `LoraFilter`, and `RendezvousHasher`. **1232 of 1887 seeded clusters dispatch the idle adapter to a worker that does not host it, while the same adapter under load dispatches to a host 1887 of 1887 times.** The minimal trigger is deterministic.

## What breaks

Dynamo's LoRA allocation controller gives every adapter with no recent load one HRW pin. The pin is computed over every live worker by hash rank only (`compute_replica_set`). It does not consult `LoraStateTracker::get_loaded_workers`, so the pin can name a worker that has never loaded the adapter. The filter's inactive branch returns that pin (`filter.rs:206-216`). The request reaches the worker, whose legacy `_resolve_lora_request` path looks the adapter up in `loaded_loras` and raises `InvalidArgument: unknown model or LoRA adapter: '<name>'`. A live worker that already hosts the adapter is never tried on that attempt.

Capacity is not the defect. The maintainer blog PR #14526 states that inactive pins may exceed worker capacity by design, and the legacy vLLM worker does not enforce `--max-loras` when an adapter arrives; it rejects a non-host before any load. The defect is that the pin ignores the set of workers that can actually serve the adapter.

Who it hurts: operators whose adapter is loaded on only some workers. That happens with the documented per-worker system API (`POST /v1/loras` on one pod), a pod that has not been reconciled yet, or a load that failed on some pods. The default `DynamoModel` CRD path loads every adapter on every pod, so the bug does not fire there; this finding is about the subset-loading cases, not the all-pods default.

Conditions: `DYN_LORA_ENABLED=true`, the default HRW allocator, at least two workers, an adapter loaded on a subset, and the adapter idle past the estimator window (90 s default: `timestep_secs` 3 times `rate_window_multiplier` 30).

Measured versus inferred: the routing table entry, the filter's candidate set, the host set, and the worker's `resolve_request` exception are **measured**, N of N. The HTTP status of the failed request is **traced** through `InvalidArgument` to the bindings' `Backend(InvalidArgument)` path, but not exercised against a live GPU worker here.

## Evidence

`transcripts/144-dynamo-lora-inactive-pin/`:

- `repro_inactive_pin.rs`, the minimal case and the active control. It builds two workers: worker 1 hosts `busy`; worker 10000 hosts `idle`. The HRW pin for `idle` is worker 1. The filter returns `[1]`. The active control, with one unit of load on `idle`, routes to worker 10000.
- `soak_inactive_pin.rs`, 2000 seeded clusters (1887 with a loaded adapter), each checked twice: once with the victim adapter idle and once with the same adapter active. Clusters have 2 to 7 workers, capacities 0 to 4, and up to 6 adapters. The victim is always loaded on at least one live worker, so a hosting worker always exists. The measured value is the filter's candidate set, not the first entry of the routing table.
- `capture_inactive_pin.rs` and `capture-inactive-pin.jsonl`, the two records the checker consumes.
- `worker_boundary.py`, the real `LoRAState.resolve_request` from `components/src/dynamo/vllm/lora_state.py` executed with stubbed import names. It raises `InvalidArgument` for the pinned worker and returns a `LoRARequest` for the hosting worker.
- `run-2026-11-11.txt`, the full run output.

### Minimal case

| step | worker 1 | worker 10000 |
|---|---|---|
| setup | hosts `busy` | hosts `idle` |
| controller pin for idle `idle` | **selected by HRW** | hosts the adapter |
| filter candidate set | `[1]` | not returned |
| worker result | `InvalidArgument: unknown model or LoRA adapter: 'idle'` | would return a `LoRARequest` |
| active control | not selected | selected |

### Soak

| dispatch | host only | non-host |
|---|---|---|
| idle adapter (blind HRW pin) | 655 | **1232 / 1887 (65.3 percent)** |
| same adapter active | 1887 | **0 / 1887** |

`non-host` means the filter's candidate set names at least one worker that does not host the adapter. The comparison is paired: the same cluster and the same adapter in both rows. The active filter never returns a non-host.

### Checker

`lora_dispatch_uses_loaded_hosts` freezes the invariant: when any worker hosts the adapter, every dispatched candidate must host it. A free slot is not enough, because the legacy worker rejects a non-host before loading. On the frozen capture it reports:

```
record 0 (bug-inactive-pin): idle dispatch for adapter 'idle' names worker 1,
which does not host it; hosting workers: [10000]
```

The active control record is conformant.

## Root cause

`lib/lora/src/controller.rs:661-679`:

```rust
// Inactive LoRAs: single HRW-pinned cold-start replica
for lora_name in inactive_loras {
    let pin = self.allocator.compute_replica_set(lora_name, workers, 1);
    ...
    self.update_routing_entry(lora_name, pin.len(), pin, false);
}
```

For HRW, `compute_replica_set` (`lib/lora/src/routing/hrw.rs:54-71`) ranks every worker by `blake3(lora_name, worker)` and takes the top one. It has no access to `LoraStateTracker::get_loaded_workers`. The filter's inactive branch then returns that pin whenever the worker is live (`filter.rs:206-216`); it does not re-check the host set.

The rest of the routing stack already states the missing rule. The filter's no-table fallback prefers known-loaded workers. The active branch returns `replica_set ∩ loaded ∩ available` before it falls back to the replica set. The active controller path uses the sticky slot-aware allocator. The inactive pin skips all of these host checks.

The load estimator's window makes the trigger ordinary: an adapter with no requests for 90 s (3 s tick times 30) becomes inactive, and the next request uses the route written while it was idle.

## Why this is a bug, not a hardening note

- The expected behavior is the stack's own: route LoRA traffic to a worker that hosts the adapter when one exists. The filter's no-table fallback and active branch implement that rule; the inactive pin does not.
- The maintainer ruling on capacity is explicit and does not apply: #14526 permits inactive pins to exceed capacity, but it does not permit a pin to a worker that cannot serve the adapter at all.
- The trigger is documented, supported usage: the per-worker system API and partial reconciliation are supported ways to load a subset.
- A real boundary is crossed: the client asks for an adapter that `/v1/models` lists and that a live worker serves, and the worker that receives the request rejects it.
- The fix is a code change, not a doc note: prefer the host set in the inactive pin, and keep the HRW fallback only when no worker hosts the adapter.

## Fix

In `recompute_per_lora`, when building the inactive pin, prefer workers that host the adapter. The smallest correct change is to intersect the candidate set with `observed.get_loaded_workers(lora_name)` before the HRW rank, and fall back to the full worker set only when the intersection is empty. The filter's `bounded_fallback` already implements the same preference and can be reused. Do not use free capacity as a proxy: a worker with a free slot but no loaded adapter still rejects the request on the legacy path. A regression test should assert that an idle adapter is pinned to a hosting worker whenever one exists, and that the fallback is a single deterministic worker only when none does.

## Open questions

- The MCF solver is out of scope here. The committed evidence runs the default HRW allocator. An exploratory soak of MCF (not committed) also produced non-host dispatches, but its overflow behavior and the #14526 capacity design need their own analysis.
- The HTTP 400 status is traced through the binding mapping, not measured against a live GPU worker.

## Independent review

The PR review comment on [kairo#109](https://github.com/Atharva-Kanherkar/kairo/pull/109) returned NEEDS EVIDENCE on 2026-11-11. It established the reproduction on a clean checkout and accepted the version and upstream status, then required three changes: measure the dispatch rather than the routing-table first entry, treat hosts rather than free slots as servable, and narrow the affected-user claim to subset-loaded adapters. This revision makes those changes and reports the dispatch-level numbers: idle 1232/1887, active 0/1887. A fresh review is pending.
