"""Engine-level repro: a streaming-input session whose accumulated tokens pass
max_model_len makes the scheduler compute tokens beyond the KV it allocated.

Drives the real AsyncLLM engine (CPU, opt-125m) with an engine-startable
exact-edge pool: 65 blocks x 16 = 64 usable = 1024 tokens = max_model_len.
Chunk 1: 32 tokens. Chunk 2: 1000 tokens. Each chunk passes the per-request
limit check on its own; the session total (1032+) does not.

Run from tools/vllm:

    .venv/bin/python ../vllm-hunt/engine_session_overlength.py [--chunk2 1000]
"""

import argparse
import asyncio
import sys
import time
from collections.abc import AsyncGenerator

sys.path.insert(0, "/Users/atharva/kairo/tools/vllm")

from vllm.engine.arg_utils import AsyncEngineArgs  # noqa: E402
from vllm.engine.protocol import StreamingInput  # noqa: E402
from vllm.inputs.engine import tokens_input  # noqa: E402
from vllm.sampling_params import SamplingParams  # noqa: E402
from vllm.v1.engine.async_llm import AsyncLLM  # noqa: E402

MODEL = "facebook/opt-125m"
MAX_MODEL_LEN = 1024
NUM_BLOCKS = 33
BLOCK_SIZE = 32


def make_engine():
    engine_args = AsyncEngineArgs(
        model=MODEL,
        max_model_len=MAX_MODEL_LEN,
        num_gpu_blocks_override=NUM_BLOCKS,
        block_size=BLOCK_SIZE,
        gpu_memory_utilization=0.2,
        enforce_eager=True,
        disable_log_stats=True,
    )
    return AsyncLLM.from_engine_args(engine_args)


def tokens(start: int, n: int) -> list[int]:
    return list(range(start, start + n))


async def input_gen(chunk2: int, spaced: bool = True) -> AsyncGenerator[StreamingInput, None]:
    yield StreamingInput(prompt=tokens_input(tokens(1000, 32)))
    if spaced:
        await asyncio.sleep(0.5)
    yield StreamingInput(prompt=tokens_input(tokens(5000, chunk2)))
    await asyncio.sleep(0.5)


async def run(chunk2: int, label: str, spaced: bool = True) -> int:
    engine = make_engine()
    sp = SamplingParams(max_tokens=2, ignore_eos=True, temperature=0.0)
    n_outputs = 0
    finished = False
    error = None
    t0 = time.monotonic()
    try:
        async for out in engine.generate(
            input_gen(chunk2, spaced), sp, request_id=f"sess-{label}"
        ):
            n_outputs += 1
            if out.finished:
                finished = True
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    elapsed = round(time.monotonic() - t0, 1)
    print(
        f"[{label}] chunk2={chunk2} outputs={n_outputs} finished={finished} "
        f"elapsed={elapsed}s error={error}"
    )
    try:
        engine.shutdown()
    except Exception:  # noqa: BLE001
        pass
    await asyncio.sleep(0.5)
    return 0 if error is None else 2


async def single_len(n: int) -> int:
    """One plain request with an n-token prompt, max_tokens=2."""
    engine = make_engine()
    sp = SamplingParams(max_tokens=2, ignore_eos=True, temperature=0.0)
    n_outputs, finished, error = 0, False, None
    try:
        async for out in engine.generate(
            tokens_input(tokens(5000, n)), sp, request_id=f"single-{n}"
        ):
            n_outputs += 1
            finished = finished or out.finished
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
    print(
        f"[single-{n}] outputs={n_outputs} finished={finished} error={error}"
    )
    try:
        engine.shutdown()
    except Exception:  # noqa: BLE001
        pass
    await asyncio.sleep(0.5)
    return 0 if error is None else 2


async def controls() -> int:
    """One engine, four cases: under-limit session, oversized single request
    (must be rejected by the API), health check, then the boundary session
    that reaches exactly max_model_len (expected to kill the engine)."""
    engine = make_engine()
    sp = SamplingParams(max_tokens=2, ignore_eos=True, temperature=0.0)
    fails = 0

    for label, chunk2 in [("ctrl-under", 800)]:
        n_outputs, finished, error = 0, False, None
        try:
            async for out in engine.generate(
                input_gen(chunk2), sp, request_id=f"sess-{label}"
            ):
                n_outputs += 1
                finished = finished or out.finished
        except Exception as e:  # noqa: BLE001
            error = f"{type(e).__name__}: {e}"
        print(
            f"[{label}] chunk2={chunk2} outputs={n_outputs} "
            f"finished={finished} error={error}"
        )
        fails += (error is not None) or (not finished)

    # Single request with the same total size: the API must reject it.
    err_single = None
    try:
        async for _ in engine.generate(
            tokens_input(tokens(5000, 1033)), sp, request_id="single-oversized"
        ):
            pass
    except Exception as e:  # noqa: BLE001
        err_single = f"{type(e).__name__}: {e}"
    print(f"[ctrl-single-1033] error={err_single}")
    fails += err_single is None

    # Engine health after the rejection: a normal request must still finish.
    ok_after = False
    try:
        async for out in engine.generate(
            tokens_input(tokens(100, 8)), sp, request_id="health-check"
        ):
            ok_after = ok_after or out.finished
    except Exception as e:  # noqa: BLE001
        print(f"[ctrl-health] error={type(e).__name__}: {e}")
    print(f"[ctrl-health] finished={ok_after}")
    fails += not ok_after

    # Boundary session: 33 + 991 = 1024 = max_model_len exactly.
    err_boundary = None
    finished_b = False
    try:
        async for out in engine.generate(
            input_gen(991), sp, request_id="sess-ctrl-boundary"
        ):
            finished_b = finished_b or out.finished
    except Exception as e:  # noqa: BLE001
        err_boundary = f"{type(e).__name__}: {e}"
    print(f"[ctrl-boundary] finished={finished_b} error={err_boundary}")

    # The engine is expected to be dead now: any request must fail.
    err_after = None
    try:
        async for _ in engine.generate(
            tokens_input(tokens(100, 8)), sp, request_id="after-boundary"
        ):
            pass
    except Exception as e:  # noqa: BLE001
        err_after = f"{type(e).__name__}: {e}"
    print(f"[after-boundary] error={err_after}")

    try:
        engine.shutdown()
    except Exception:  # noqa: BLE001
        pass
    await asyncio.sleep(0.5)
    ok = fails == 0 and err_boundary is not None
    print(f"controls: {'OK' if ok else 'MISMATCH'}")
    return 0 if ok else 1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunk2", type=int, default=1000)
    ap.add_argument("--label", default="case")
    ap.add_argument("--controls", action="store_true")
    ap.add_argument("--bunched", action="store_true")
    ap.add_argument("--single", type=int, default=None)
    args = ap.parse_args()
    if args.single is not None:
        return await single_len(args.single)
    if args.controls:
        return await controls()
    return await run(args.chunk2, args.label, spaced=not args.bunched)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
