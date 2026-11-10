// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Emit one capture record for the inactive-pin bug and one for the active control.

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

struct Scenario {
    full: WorkerWithDpRank,
    idle_host: WorkerWithDpRank,
    rt: LoraRoutingTable,
    st: LoraStateTracker,
    le: Arc<LoadEstimator>,
    controller: LoraController,
}

fn scenario() -> Scenario {
    let rt = LoraRoutingTable::new();
    let st = LoraStateTracker::new();
    let le = Arc::new(LoadEstimator::new());
    let mut cfg = LoraAllocationConfig::default();
    cfg.algorithm = AllocationAlgorithmType::Hrw;
    cfg.scale_down_cooldown_ticks = 0;
    let controller = LoraController::new(cfg, rt.clone(), st.clone(), le.clone());

    let idle_host = worker(10_000);
    let full = (1..5000u64)
        .map(worker)
        .find(|candidate| {
            RendezvousHasher::compute_score("idle", *candidate)
                > RendezvousHasher::compute_score("idle", idle_host)
        })
        .expect("some worker id must outrank the loaded host");

    st.handle_mdc_addition(full, &info("busy", 1));
    st.handle_mdc_addition(idle_host, &info("idle", 2));

    Scenario {
        full,
        idle_host,
        rt,
        st,
        le,
        controller,
    }
}

fn record(case: &str, s: &Scenario) {
    let route = s.rt.get_config("idle").expect("route exists");
    let filter = LoraFilter::new(s.rt.clone(), s.st.clone());
    let selected = filter.filter_worker_ids_for_lora(
        Some("idle"),
        &[s.full.worker_id, s.idle_host.worker_id],
    );
    println!(
        "{{\"body\":{{\"case\":\"{case}\",\"adapter\":\"idle\",\"workers\":[\
         {{\"id\":{},\"capacity\":1,\"loaded\":[\"busy\"]}},\
         {{\"id\":{},\"capacity\":2,\"loaded\":[\"idle\"]}}],\
         \"route\":{{\"is_active\":{},\"replica_set\":[{}]}},\
         \"selected\":[{}]}}}}",
        s.full.worker_id,
        s.idle_host.worker_id,
        route.is_active,
        route
            .replica_set
            .iter()
            .map(|w| w.worker_id.to_string())
            .collect::<Vec<_>>()
            .join(","),
        selected
            .iter()
            .map(|id| id.to_string())
            .collect::<Vec<_>>()
            .join(",")
    );
}

#[test]
fn emit_capture() {
    let mut s = scenario();
    s.controller.recompute_now();
    record("bug-inactive-pin", &s);

    let mut s = scenario();
    s.le.increment_load("idle");
    s.controller.recompute_now();
    record("control-active", &s);
}
