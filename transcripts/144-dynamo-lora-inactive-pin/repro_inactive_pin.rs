// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Reproduce: an idle LoRA's HRW pin ignores the workers that host the adapter,
//! so the router dispatches the request to a worker that rejects it as an
//! unknown adapter while another live worker hosts it.
//!
//! Run from the Dynamo checkout at 3a524d0d70:
//!   cargo test -p dynamo-lora --test repro_inactive_pin -- --nocapture

use std::sync::Arc;

use dynamo_kv_router::protocols::WorkerWithDpRank;
use dynamo_lora::filter::LoraFilter;
use dynamo_lora::load_estimator::LoadEstimator;
use dynamo_lora::routing::RendezvousHasher;
use dynamo_lora::{
    AllocationAlgorithmType, LoraAllocationConfig, LoraController, LoraInfo, LoraRoutingTable,
    LoraStateTracker,
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
    pin_target: WorkerWithDpRank,
    host: WorkerWithDpRank,
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

    let host = worker(10_000);
    // Pick a worker id the HRW pin ranks above the worker that hosts the adapter.
    let pin_target = (1..5000u64)
        .map(worker)
        .find(|candidate| {
            RendezvousHasher::compute_score("idle", *candidate)
                > RendezvousHasher::compute_score("idle", host)
        })
        .expect("some worker id must outrank the loaded host");

    // `pin_target` hosts a different adapter. `host` hosts `idle`.
    st.handle_mdc_addition(pin_target, &info("busy", 1));
    st.handle_mdc_addition(host, &info("idle", 2));

    Cluster {
        pin_target,
        host,
        rt,
        st,
        le,
        controller,
    }
}

fn selected(c: &Cluster) -> Vec<u64> {
    let filter = LoraFilter::new(c.rt.clone(), c.st.clone());
    filter.filter_worker_ids_for_lora(
        Some("idle"),
        &[c.pin_target.worker_id, c.host.worker_id],
    )
}

#[test]
fn idle_pin_dispatches_to_a_worker_that_does_not_host_the_adapter() {
    let mut c = cluster();
    c.controller.recompute_now();

    let route = c.rt.get_config("idle").expect("idle route exists");
    assert!(!route.is_active, "the adapter has no load, so it is inactive");
    assert_eq!(
        route.replica_set,
        vec![c.pin_target],
        "blind HRW pin over every live worker"
    );

    let selected = selected(&c);
    assert_eq!(
        selected,
        vec![c.pin_target.worker_id],
        "the router sends the request to the pinned worker"
    );

    // The dispatched worker does not host the adapter and cannot serve it.
    assert!(!c.st.is_loaded("idle", &c.pin_target));
    // A hosting worker exists.
    assert!(c.st.is_loaded("idle", &c.host));

    println!(
        "idle adapter 'idle' -> worker {} (hosts 'busy', not 'idle'); \
         worker {} hosts 'idle' and was not dispatched",
        c.pin_target.worker_id, c.host.worker_id
    );
}

#[test]
fn active_placement_control_dispatches_to_the_host() {
    let mut c = cluster();
    c.le.increment_load("idle");
    c.controller.recompute_now();

    let route = c.rt.get_config("idle").expect("idle route exists");
    assert!(route.is_active);
    assert_eq!(
        route.replica_set,
        vec![c.host],
        "an active adapter stays on the worker that hosts it"
    );

    let selected = selected(&c);
    assert_eq!(
        selected,
        vec![c.host.worker_id],
        "the active filter dispatches to the host"
    );
    println!("active control -> worker {}", c.host.worker_id);
}
