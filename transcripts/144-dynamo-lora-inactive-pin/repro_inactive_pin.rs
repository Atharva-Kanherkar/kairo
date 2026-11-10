// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Reproduce: an idle LoRA's HRW pin can land on a worker that is at capacity
//! and does not host the adapter, while another live worker hosts it and has a
//! free slot.
//!
//! Run from the Dynamo checkout at 3a524d0d70:
//!   cargo test -p dynamo-lora --test repro_inactive_pin -- --nocapture

use std::sync::Arc;

use dynamo_kv_router::protocols::WorkerWithDpRank;
use dynamo_lora::filter::LoraFilter;
use dynamo_lora::load_estimator::LoadEstimator;
use dynamo_lora::routing::RendezvousHasher;
use dynamo_lora::{
    AllocationAlgorithmType, LoraAllocationConfig, LoraAllocator, LoraController, LoraInfo,
    LoraRoutingTable, LoraStateTracker,
};

fn worker(id: u64) -> WorkerWithDpRank {
    WorkerWithDpRank::new(id, 0)
}

fn info(name: &str, cap: u32) -> LoraInfo {
    LoraInfo {
        name: name.to_string(),
        max_gpu_lora_count: Some(cap),
    }
}

struct Cluster {
    full: WorkerWithDpRank,
    idle_host: WorkerWithDpRank,
    rt: LoraRoutingTable,
    st: LoraStateTracker,
    le: Arc<LoadEstimator>,
    controller: LoraController,
}

fn cluster() -> Cluster {
    let rt = LoraRoutingTable::new();
    let st = LoraStateTracker::new();
    let le = Arc::new(LoadEstimator::new());
    let mut cfg = LoraAllocationConfig::default();
    cfg.algorithm = AllocationAlgorithmType::Hrw;
    cfg.scale_down_cooldown_ticks = 0;
    let controller = LoraController::new(cfg, rt.clone(), st.clone(), le.clone());

    let idle_host = worker(10_000);
    // Pick a worker id the HRW pin ranks above the worker that hosts the adapter.
    let full = (1..5000u64)
        .map(worker)
        .find(|candidate| {
            RendezvousHasher::compute_score("idle", *candidate)
                > RendezvousHasher::compute_score("idle", idle_host)
        })
        .expect("some worker id must outrank the loaded host");

    // `full`: one LoRA slot, used by a different adapter.
    st.handle_mdc_addition(full, &info("busy", 1));
    // `idle_host`: hosts the idle adapter and has one free slot (1 of 2 used).
    st.handle_mdc_addition(idle_host, &info("idle", 2));

    Cluster {
        full,
        idle_host,
        rt,
        st,
        le,
        controller,
    }
}

#[test]
fn idle_pin_lands_on_a_full_worker_that_lacks_the_adapter() {
    let mut c = cluster();
    c.controller.recompute_now();

    let route = c.rt.get_config("idle").expect("idle route exists");
    assert!(!route.is_active, "the adapter has no load, so it is inactive");
    assert_eq!(route.replica_set, vec![c.full], "blind HRW pin");

    let filter = LoraFilter::new(c.rt.clone(), c.st.clone());
    let selected = filter.filter_worker_ids_for_lora(
        Some("idle"),
        &[c.full.worker_id, c.idle_host.worker_id],
    );
    assert_eq!(
        selected,
        vec![c.full.worker_id],
        "the router sends the request to the full worker"
    );

    // The pinned worker cannot serve it.
    assert!(!c.st.is_loaded("idle", &c.full));
    assert_eq!(c.st.free_slots(&c.full), 0, "at capacity with 'busy'");

    // A servable worker exists.
    assert!(c.st.is_loaded("idle", &c.idle_host));
    assert!(c.st.free_slots(&c.idle_host) > 0);

    println!(
        "idle adapter 'idle' -> worker {} (capacity 1, hosts 'busy'); \
         worker {} hosts 'idle' with a free slot",
        c.full.worker_id, c.idle_host.worker_id
    );

    // Control: the slot-aware allocator (the active path also passes a prior set;
    // non-sticky here) picks the loaded, non-full host.
    let usage = std::collections::HashMap::from([
        (c.full, (1usize, 1usize)),
        (c.idle_host, (1usize, 2usize)),
    ]);
    let slot_aware = RendezvousHasher.compute_replica_set_with_slots(
        "idle",
        &[c.full, c.idle_host],
        1,
        &usage,
    );
    assert_eq!(slot_aware, vec![c.idle_host]);
    println!(
        "slot-aware control (non-sticky) -> worker {}",
        c.idle_host.worker_id
    );
}

#[test]
fn active_placement_control_keeps_the_loaded_host() {
    let mut c = cluster();
    c.le.increment_load("idle");
    c.controller.recompute_now();

    let route = c.rt.get_config("idle").expect("idle route exists");
    assert!(route.is_active);
    assert_eq!(
        route.replica_set,
        vec![c.idle_host],
        "an active adapter stays on the loaded, non-full worker"
    );
    println!(
        "active control -> worker {}",
        route.replica_set[0].worker_id
    );
}
