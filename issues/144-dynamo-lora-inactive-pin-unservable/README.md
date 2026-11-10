# 144, Dynamo's idle LoRA pin ignores capacity and loaded copies, so the first request after an idle window is routed to a worker that cannot serve the adapter

- **Upstream**: [ai-dynamo/dynamo](https://github.com/ai-dynamo/dynamo), no matching ticket. Classification: `novel` as of 2026-11-10. Search terms used: `inactive LoRA`, `cold-start replica`, `single HRW`, `slot-aware pin`, `lora pin capacity`, `adapter not loaded`, `unknown model or LoRA`, `LoRA allocation`, `lora route`, `loaded workers`; 75 LoRA-titled issues since 2026-06-01, the 245 open bug-labeled issues, and the open PR list checked. Closest adjacent artifacts are [#11163](https://github.com/ai-dynamo/dynamo/issues/11163) (a failed model-card publish leaves an adapter unroutable), [#14394](https://github.com/ai-dynamo/dynamo/issues/14394) (a request for nightly LoRA churn tests), and the open docs PR [#14526](https://github.com/ai-dynamo/dynamo/pull/14526) (which describes inactive adapters as one deterministic HRW pin but does not bless a pin that cannot serve). None describes this pin. Date checked 2026-11-10 against upstream `main` = `3a524d0d70`.
- **Tool under test**: NVIDIA Dynamo. The files under test are byte-identical to upstream `main` on the reproduction date: `lib/lora/src/controller.rs`, sha256 `02e26bbf5032912892e0bae2b031c90c5cead352c9b2e13fd8daa2e464ba9d69`; `lib/lora/src/routing/hrw.rs`, sha256 `f429a4c68090248178f90dc8748b47d61511b00c46e8efdaceca6775f8222144`; `lib/lora/src/filter.rs`, sha256 `f7b2aa9bb3a04847ade1962c060ce1d111ef56fdc3e335790ede4dca7c06d5ce`; `components/src/dynamo/vllm/lora_state.py`, sha256 `40c260c36bf09c0a2616cd45f45fc4a5b2a63f0f72b425495f29f94d564f3d20`. Defect at `controller.rs:661-679`, in the inactive branch of `recompute_per_lora`, and at `filter.rs:206-216`, which forwards the pin. Reached by default: the allocation controller is enabled when `DYN_LORA_ENABLED=true` (`LoraAllocationConfig::default().enabled == true`), and `DYN_LORA_ALLOCATION_ALGORITHM` defaults to `hrw`.
- **Reproduced**: 2026-11-10, macOS arm64, no GPU, no vLLM worker. Rust 1.96.0, Python 3.12.13. The harness runs the real `dynamo-lora` crate from the pinned checkout, not a copy: `LoraController`, `LoraRoutingTable`, `LoraStateTracker`, `LoadEstimator`, `LoraFilter`, and `RendezvousHasher`. **529 of 1887 seeded clusters route the idle adapter to a worker that neither hosts it nor has a free slot, while the same adapter under load is unservable 0 of 1887 times.** The minimal trigger is deterministic; the 28.0 percent rate is the soak result below, and it is a lower bound (see the soak section).

## What breaks

Dynamo's LoRA allocation controller gives every adapter with no recent load one HRW pin. The pin is computed over every live worker by hash rank only (`compute_replica_set`). It does not consult the worker's LoRA capacity, the slots already in use, or the workers that actually host the adapter. The active HRW branch of the same function does all three: it discounts the adapter's own loaded slots, uses the sticky slot-aware allocator, and charges residual usage.

When the pin lands on a worker that is already at `--max-loras` with other adapters, the routing filter returns that worker anyway. The request is dispatched there, the worker's `LoRAState.resolve_request` does not find the adapter in `loaded_loras`, and it raises `InvalidArgument: unknown model or LoRA adapter: '<name>'` before generation starts. This request path resolves the name and rejects; it does not load the adapter. The adapter is listed in `/v1/models`, is loaded on another live worker, and that worker has a free slot.

The pin and the cold-start selection are logged at info and debug level, but the request failure is not correlated to them; the client sees only the worker's rejection. A retry can succeed after the next controller tick (3 s default) moves the route.

Who it hurts: anyone serving more LoRA adapters than a worker's `--max-loras`, which is the documented reason the allocation controller exists. Multi-tenant LoRA pools keep most adapters idle most of the time, so every adapter crosses into the inactive state. In the paired soak, 28.0 percent of the idle routes are provably unservable, and the first request after the estimator window (90 s default: `timestep_secs` 3 times `rate_window_multiplier` 30) is the one that hits the pin.

Conditions: `DYN_LORA_ENABLED=true`, the default HRW allocator, at least two workers, at least one worker at its LoRA capacity, and the adapter loaded on a different worker. All are ordinary states for a LoRA pool under capacity pressure.

Measured versus inferred: the routing table entry, the filter's candidate set, the pinned worker's capacity and loaded set, and the worker's `resolve_request` exception are **measured**, N of N. The HTTP status of the failed request is **traced** through `InvalidArgument` to the bindings' `Backend(InvalidArgument)` path, but not exercised against a live GPU worker here.

## Evidence

`transcripts/144-dynamo-lora-inactive-pin/`:

- `repro_inactive_pin.rs`, the minimal case and two controls. It builds two workers: worker 1 has capacity 1 and hosts `busy`; worker 10000 has capacity 2, hosts `idle`, and has one free slot. The HRW pin for `idle` is worker 1. The filter returns `[1]`. The active control, with one unit of load on `idle`, routes to worker 10000.
- `soak_inactive_pin.rs`, 2000 seeded clusters (1887 with a loaded adapter), each checked twice: once with the victim adapter idle and once with the same adapter active. Clusters have 2 to 7 workers, capacities 0 to 4, and up to 6 adapters. The victim is always loaded on at least one live worker, so a servable route always exists.
- `capture_inactive_pin.rs` and `capture-inactive-pin.jsonl`, the two records the checker consumes.
- `worker_boundary.py`, the real `LoRAState.resolve_request` from `components/src/dynamo/vllm/lora_state.py` executed with stubbed import names. It raises `InvalidArgument` for the pinned worker and returns a `LoRARequest` for the loaded worker.
- `run-2026-11-10.txt`, the full run output.

### Minimal case

| step | worker 1 | worker 10000 |
|---|---|---|
| setup | capacity 1, hosts `busy` | capacity 2, hosts `idle` (1 of 2 slots) |
| controller pin for idle `idle` | **selected by HRW** | hosts the adapter, free slot |
| filter candidate set | `[1]` | not returned |
| worker result | `InvalidArgument: unknown model or LoRA adapter: 'idle'` | would return a `LoRARequest` |
| active control | not selected | selected |

### Soak

| route | loaded copy | free slot only | unservable |
|---|---|---|---|
| idle adapter (blind HRW pin) | 655 | 703 | **529 / 1887 (28.0 percent)** |
| same adapter active | 945 | 942 | **0 / 1887** |

`unservable` means the route names a worker that does not host the adapter and whose `loaded_loras` count already equals its capacity. The comparison is paired: the same cluster and the same adapter in both rows. The active HRW branch never produces this state.

The 28.0 percent is a lower bound. It counts only routes that fail under both models of the worker: even if a free slot were enough for a lazy load, these routes have no free slot and no loaded copy. Under the stricter host-only model implied by the legacy vLLM path, 1232 of 1887 idle routes (65.3 percent) fail, but the soak's active first placements are free-slot non-hosts 942 of 1887 times. That is a first-placement count; the active filter still prefers a loaded host that is in the set, and the minimal active control shows the request path. The broader lazy-load question is separate from this claim; the 28.0 percent is the part the active HRW path gets right and the idle path does not.

### Checker

`lora_routes_are_provably_servable` freezes the conservative invariant: when any worker can serve the adapter (it hosts it, or it has a free slot), no route may name a worker that can do neither. It intentionally accepts a free-slot non-host, so the 28.0 percent is a lower bound, not the full idle failure rate. On the frozen capture it reports:

```
record 0 (bug-inactive-pin): idle route for adapter 'idle' names worker 1,
which neither hosts the adapter nor has a free LoRA slot
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

For HRW, `compute_replica_set` (`lib/lora/src/routing/hrw.rs:54-71`) ranks every worker by `blake3(lora_name, worker)` and takes the top one. It has no access to capacity or to `LoraStateTracker::get_loaded_workers`.

The active branch (`controller.rs:579-660`) instead uses `compute_replica_set_with_slots_sticky`, which skips full workers, retains prior placements, and is charged against `residual_usage`. The controller's own dropped-active pin path (`controller.rs:460-462`) states the rule the inactive branch misses: "Pin worker selection is slot-aware against this tick's PROJECTED usage ... so a pin never targets a slot already claimed this tick." The zero-capacity test (`test_zero_capacity_fails_closed_drops_non_warm_route`) states the consequence: "Rebinding would pin LoRA traffic to a worker that has no LoRA capacity and would just fail the load."

The filter is consistent with its input. For an inactive entry it returns `config.replica_set.first()` whenever that worker is available (`filter.rs:206-216`). It does not re-check capacity or the loaded set, so the controller's bad pin reaches the wire.

The load estimator's window makes the trigger ordinary: an adapter with no requests for 90 s (3 s tick times 30) becomes inactive, and the next request uses the route written while it was idle.

## Why this is a bug, not a hardening note

- The expected behavior is the controller's own: do not pin adapter traffic to a worker that cannot serve it. The active HRW path and the zero-capacity path implement that rule; the inactive branch does not.
- The trigger is documented, supported usage: `DYN_LORA_ENABLED=true` plus vLLM `--max-loras`, with more adapters than slots.
- A real boundary is crossed: the client asks for an adapter that `/v1/models` lists and that another worker is serving, and the worker that receives the request rejects it.
- The fix is a code change, not a doc note: run the inactive pin through the same slot-aware sticky placement with `replica_factor = 1`, or filter the candidate list to workers that host the adapter or have a free slot, and charge the pin against residual usage.

## Fix

In `recompute_per_lora`, compute `own_loaded` and `eff_usage` once for the inactive adapter as the active branch does, then call `compute_replica_set_with_slots_sticky(lora_name, workers, 1, &eff_usage, &prior)` and charge the chosen worker in `residual_usage`. When the adapter has no prior entry, the slot-aware allocator still avoids a full worker and only falls back to one when every worker is full. A regression test should assert that an idle adapter is never pinned to a worker whose `loaded_loras == capacity` and which does not host it, while a loaded, non-full worker exists.

## Open questions

- The MCF solver is out of scope here. The committed evidence runs the default HRW allocator. An exploratory soak of MCF (not committed) also produced unservable idle routes and unservable active routes, so it needs its own investigation rather than being folded into this claim. A complete fix should treat an existing loaded copy as zero cost in both algorithms.
- The filter's active fallback returns workers without a loaded copy on the assumption that the request can lazy-load there. The legacy vLLM path resolves the name and rejects; those free-slot routes are outside this claim (see the lower-bound paragraph).
