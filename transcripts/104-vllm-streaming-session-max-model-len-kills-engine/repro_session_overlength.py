"""Minimal deterministic repro: a streaming-input session accumulates input
chunks past max_model_len. Each chunk is legal on its own (the API validates
chunks, not the session total), _update_request_as_session extends the token
count without a cap, and the scheduler's waiting path never clamps its prefill
chunk to max_model_len. The KVCacheManager clamps the *allocation* to
max_model_len, so the scheduler schedules more tokens than it allocated KV
slots for: the request computes tokens past its block table.

Engine-startable configuration: 65 blocks x 16 = 64 usable blocks = 1024
tokens = max_model_len (the exact edge the engine's startup guard allows).

Deterministic, model-free, no RNG. Run from tools/vllm:

    .venv/bin/python ../vllm-hunt/repro_session_overlength.py
"""

import sys

sys.path.insert(0, "/Users/atharva/kairo/tools/vllm")

from tests.v1.core.utils import create_scheduler  # noqa: E402
from vllm.sampling_params import SamplingParams  # noqa: E402
from vllm.utils.hashing import sha256  # noqa: E402
from vllm.v1.core.kv_cache_utils import (  # noqa: E402
    get_request_block_hasher,
    init_none_hash,
)
from vllm.v1.outputs import ModelRunnerOutput  # noqa: E402
from vllm.v1.request import Request  # noqa: E402

BLOCK = 16
TOKEN = 8000
SAMPLED = 9999


def make_block_hasher():
    init_none_hash(sha256)
    return get_request_block_hasher(BLOCK, sha256)


def make_request(rid, prompt_len, max_tokens=1, arrival=0.0, resumable=False):
    sp = SamplingParams(ignore_eos=True, max_tokens=max_tokens)
    return Request(
        request_id=rid,
        prompt_token_ids=[TOKEN + i for i in range(prompt_len)],
        sampling_params=sp,
        pooling_params=None,
        arrival_time=arrival,
        resumable=resumable,
        session_id=rid if resumable else None,
        block_hasher=make_block_hasher() if resumable else None,
    )


def step(sched):
    """One schedule / mock-execute / update_from_output cycle."""
    out = sched.schedule()
    req_ids = [r.req_id for r in out.scheduled_new_reqs]
    req_ids.extend(out.scheduled_cached_reqs.req_ids)
    model_out = ModelRunnerOutput(
        req_ids=req_ids,
        req_id_to_index={rid: i for i, rid in enumerate(req_ids)},
        sampled_token_ids=[[SAMPLED] for _ in req_ids],
        logprobs=None,
        prompt_logprobs_dict={},
        pooler_output=[],
    )
    sched.update_from_output(out, model_out)
    return out


def state(sched, rid):
    req = sched.requests.get(rid)
    if req is None:
        return "gone"
    blocks = len(sched.kv_cache_manager.get_block_ids(rid)[0])
    return (req.status, req.num_computed_tokens, blocks)


def case(num_blocks, max_model_len, chunk2_len):
    """Session: chunk1 = 32 tokens, then one update of chunk2_len tokens."""
    sched = create_scheduler(
        num_blocks=num_blocks,
        max_num_seqs=4,
        max_num_batched_tokens=1024,
        enable_prefix_caching=False,
        max_model_len=max_model_len,
        block_size=BLOCK,
    )
    sched.add_request(make_request("s1", 32, max_tokens=1, resumable=True))
    for _ in range(4):  # chunk 1 prefill, sample, compute output, pause
        if sched.requests["s1"].status.name == "WAITING_FOR_STREAMING_REQ":
            break
        step(sched)

    sched.add_request(
        make_request("s1", chunk2_len, max_tokens=1, arrival=1.0, resumable=True)
    )
    out = step(sched)
    return sched, out


def report(name, sched, out):
    status, computed, blocks = state(sched, "s1")
    need = -(-computed // BLOCK) if isinstance(computed, int) else 0
    over_model_len = isinstance(computed, int) and computed > sched.max_model_len
    over_table = need > blocks
    print(
        f"{name}: scheduled={out.total_num_scheduled_tokens} "
        f"status={status} computed={computed} blocks={blocks}/{need} "
        f"max_model_len={sched.max_model_len} "
        f"over_model_len={over_model_len} kv_overrun={over_table}"
    )
    return over_model_len, over_table


def main() -> int:
    rounds = 5
    all_ok = True
    for rnd in range(rounds):
        ok = True
        # BUG: accumulated 32 + 1000 = 1032 > 1024 = max_model_len = pool.
        sched, out = case(65, 1024, 1000)
        over_model_len, kv_overrun = report("BUG     ", sched, out)
        ok = ok and over_model_len and kv_overrun

        # CTRL-A: chunk2 fits under max_model_len (total 832 <= 1024).
        sched, out = case(65, 1024, 800)
        om, ko = report("CTRL-A  ", sched, out)
        ok = ok and not om and not ko

        # CTRL-B: same chunks but pool and max_model_len sized for them.
        sched, out = case(129, 2048, 1000)
        om, ko = report("CTRL-B  ", sched, out)
        ok = ok and not om and not ko

        # CTRL-C: pooled boundary, chunk2 one token under the boundary.
        sched, out = case(65, 1024, 991)  # total 32 + 991 = 1023 <= 1024
        om, ko = report("CTRL-C  ", sched, out)
        ok = ok and not om and not ko

        print(f"round {rnd + 1}: {'REPRO OK' if ok else 'REPRO MISMATCH'}")
        all_ok = all_ok and ok
        print()
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
