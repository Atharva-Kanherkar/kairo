// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

//! Soak: the same random clusters, one adapter active and the same adapter idle.
//!
//! A worker can serve an adapter if it already hosts it (no new slot) or has a
//! free slot for a lazy load. Any other route is unservable. The adapter is
//! always loaded on at least one live worker, so a servable route always exists.
//!
//! Run from the Dynamo checkout at 3a524d0d70:
//!   cargo test -p dynamo-lora --test soak_inactive_pin -- --nocapture

use std::collections::HashMap;
use std::sync::Arc;

use dynamo_kv_router::protocols::WorkerWithDpRank;
use dynamo_lora::load_estimator::LoadEstimator;
use dynamo_lora::{
    AllocationAlgorithmType, LoraAllocationConfig, LoraController, LoraInfo, LoraRoutingTable,
    LoraStateTracker,
};

fn worker(id: u64) -> WorkerWithDpRank {
    WorkerWithDpRank::new(id, 0)
}

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        self.0
    }

    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
}

fn info(name: &str, cap: u32) -> LoraInfo {
    LoraInfo {
        name: name.to_string(),
        max_gpu_lora_count: Some(cap),
    }
}

/// Build one cluster and return (loaded_here, free_here) for the victim route.
fn run_trial(seed: u64, active: bool) -> Result<(bool, bool), String> {
    let mut rng = Rng(seed.wrapping_mul(0x9E37_79B9_7F4A_7C15) | 1);
    let worker_count = 2 + rng.below(6) as usize;
    let workers: Vec<WorkerWithDpRank> =
        (0..worker_count).map(|i| worker(100 + i as u64)).collect();

    let rt = LoraRoutingTable::new();
    let st = LoraStateTracker::new();
    let le = Arc::new(LoadEstimator::new());
    let mut cfg = LoraAllocationConfig::default();
    cfg.algorithm = AllocationAlgorithmType::Hrw;
    cfg.scale_down_cooldown_ticks = 0;
    let mut controller = LoraController::new(cfg, rt.clone(), st.clone(), le.clone());

    let mut caps = HashMap::new();
    for w in &workers {
        let cap = rng.below(5) as u32;
        caps.insert(*w, cap);
        st.set_worker_capacity(*w, cap);
    }

    let adapter_names: Vec<String> = (0..6).map(|i| format!("adapter-{i}")).collect();
    for w in &workers {
        let cap = caps[w] as usize;
        let take = if cap == 0 {
            0
        } else {
            rng.below(cap as u64 + 1) as usize
        };
        let mut chosen = Vec::new();
        while chosen.len() < take {
            let name = &adapter_names[rng.below(adapter_names.len() as u64) as usize];
            if !chosen.contains(name) {
                chosen.push(name.clone());
            }
        }
        for name in chosen {
            st.handle_mdc_addition(*w, &info(&name, caps[w]));
        }
    }

    let mut known: Vec<String> = st.list_loras();
    known.sort();
    if known.is_empty() {
        return Err("no adapter loaded in this trial".to_string());
    }
    let victim = known[rng.below(known.len() as u64) as usize].clone();

    if active {
        le.increment_load(&victim);
    }

    controller.recompute_now();
    let route = rt
        .get_config(&victim)
        .ok_or_else(|| "controller wrote no route".to_string())?;
    if active != route.is_active {
        return Err("victim route has the wrong activity".to_string());
    }
    let chosen = *route
        .replica_set
        .first()
        .ok_or_else(|| "empty route".to_string())?;

    let loaded_here = st.is_loaded(&victim, &chosen);
    let free_here = st.free_slots(&chosen) > 0;

    if st.get_loaded_workers(&victim).is_empty() {
        return Err("adapter not loaded anywhere".to_string());
    }

    Ok((loaded_here, free_here))
}

fn soak(active: bool) -> (u64, u64, u64, u64) {
    let mut trials = 0u64;
    let mut loaded = 0u64;
    let mut free_only = 0u64;
    let mut unservable = 0u64;
    for seed in 1..=2000u64 {
        if let Ok((loaded_here, free_here)) = run_trial(seed, active) {
            trials += 1;
            if loaded_here {
                loaded += 1;
            } else if free_here {
                free_only += 1;
            } else {
                unservable += 1;
            }
        }
    }
    (trials, loaded, free_only, unservable)
}

#[test]
fn idle_pins_are_unservable_in_a_soak() {
    let (trials, loaded, free_only, unservable) = soak(false);
    println!(
        "idle: trials={trials} loaded={loaded} free_only={free_only} unservable={unservable}"
    );
    assert!(trials > 100);
    assert!(
        unservable > 0,
        "the blind idle pin must produce unservable routes in this soak"
    );
}

#[test]
fn active_routes_are_servable_control() {
    let (trials, loaded, free_only, unservable) = soak(true);
    println!(
        "active: trials={trials} loaded={loaded} free_only={free_only} unservable={unservable}"
    );
    assert!(trials > 100);
    assert_eq!(unservable, 0, "the active path must never pick an unservable worker");
}
