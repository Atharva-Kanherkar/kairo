"""Deterministic repro: Dynamo's multimodal RingBuffer hands out two live buffers
that occupy the same bytes, so one request's image embeddings overwrite another's.

RingBuffer.get_buffer tracks `allocated_start_idx` as the exclusive lower bound of
the reusable head region. That bound is only valid before the first wrap. After the
wrap branch runs, a fresh allocation is placed at [0, size) while
`allocated_start_idx` keeps its old, larger value, so the overlap guard

    if self.wrapped_around and end_idx > self.allocated_start_idx:

compares against a stale bound and admits a second post-wrap allocation at [0, size)
that overlaps the still-live first one. The returned tensors are slices of the one
shared `buffer_tensor`, and NixlWriteEmbeddingReceiver.receive_embeddings publishes
`transfer_tensor.data_ptr()` to the peer, so two requests end up writing and reading
the same address range.

The allocator is pure Python + torch and is imported verbatim from the pinned
checkout at 5593e8857. Only `dynamo.runtime` (a compiled Rust extension) is
shimmed; the exercised path never touches it.
"""


import random
from datetime import datetime, timezone

import torch

REPO = "/Users/atharva/Documents/ChatGPT/NVIDIA_DYNAMO"
SOURCE_FILE = REPO + "/components/src/dynamo/common/multimodal/embedding_transfer.py"

# --- Load RingBuffer verbatim from the pinned checkout ----------------------
# Importing the module would execute dynamo.common.multimodal.__init__ and
# dynamo.common.utils.__init__, which pull in the compiled dynamo._core,
# dynamo.llm, and dynamo.prometheus_names extensions. RingBuffer and
# MonolithicCounter touch none of them: `torch` is their only dependency, so we
# lift the two class definitions out of the file verbatim with ast and exec
# them. Nothing in the exercised code is retyped or reimplemented here.
import ast
import hashlib


with open(SOURCE_FILE, "r", encoding="utf-8") as fh:
    _source = fh.read()

_source_sha256 = hashlib.sha256(_source.encode("utf-8")).hexdigest()
_tree = ast.parse(_source)

_lines = _source.splitlines(keepends=True)
_wanted = ("MonolithicCounter", "RingBuffer")
_extracted = []
for _node in _tree.body:
    if isinstance(_node, ast.ClassDef) and _node.name in _wanted:
        _start = _node.lineno - 1  # decorators would shift this; none here
        _end = _node.end_lineno
        _chunk = "".join(_lines[_start:_end])
        _extracted.append((_node.name, _node.lineno, _node.end_lineno))
        exec(compile(_chunk, f"{SOURCE_FILE}:{_node.name}", "exec"), globals())

_extracted_sha256 = hashlib.sha256(
    "".join(
        "".join(_lines[n.lineno - 1 : n.end_lineno])
        for n in _tree.body
        if isinstance(n, ast.ClassDef) and n.name in _wanted
    ).encode("utf-8")
).hexdigest()


print(f"source file      : {SOURCE_FILE}")
print(f"file sha256      : {_source_sha256}")
print(f"extracted classes: {[(n, f'L{a}-L{b}') for n, a, b in _extracted]}")
print(f"extracted sha256 : {_extracted_sha256}")
print(f"RingBuffer.get_buffer defined at line: "
      f"{RingBuffer.get_buffer.__code__.co_firstlineno}")

GIB = 1024 * 1024 * 1024


def trace(label, ops, buffer_size):
    """Run an op script against a fresh RingBuffer and report every overlap."""
    ring = RingBuffer(buffer_size)
    live = {}
    overlaps = []
    log = []
    for op in ops:
        if op[0] == "get":
            size = op[1]
            buffer_id, tensor = ring.get_buffer(size)
            if tensor is None:
                log.append(f"  get_buffer({size}) -> (None, None)")
                continue
            start, end = ring.allocated_buffer_id_to_range[buffer_id]
            for other_id, (o_start, o_end) in live.items():
                if start < o_end and o_start < end:
                    overlaps.append(
                        {
                            "new_id": buffer_id,
                            "new_range": (start, end),
                            "live_id": other_id,
                            "live_range": (o_start, o_end),
                            "size": size,
                        }
                    )
            live[buffer_id] = (start, end)
            log.append(
                f"  get_buffer({size}) -> id={buffer_id} [{start},{end})  "
                f"ptr={tensor.data_ptr()}"
            )
        else:
            buffer_id = op[1]
            ring.release_buffer(buffer_id)
            live.pop(buffer_id, None)
            log.append(f"  release_buffer({buffer_id})")

    state = (
        f"free_start={ring.free_start_idx} allocated_start={ring.allocated_start_idx} "
        f"wrapped={ring.wrapped_around} freed_list={ring.freed_list}"
    )
    print(f"[{label}] buffer_size={buffer_size}")
    print("\n".join(log))
    print(f"  state after: {state}")
    print(f"  live ranges: {live}")
    if overlaps:
        for o in overlaps:
            print(
                f"  *** OVERLAP: id={o['new_id']} {o['new_range']} "
                f"collides with live id={o['live_id']} {o['live_range']} "
                f"(size={o['size']})"
            )
    else:
        print("  no overlap")
    print()
    return overlaps


def mib(n):
    return n * 1024 * 1024


print("=" * 78)
print("Dynamo RingBuffer: does get_buffer ever return two live buffers that alias?")
print("=" * 78)
print()

# --- Case 1: minimal 5-op trace at a tiny buffer size -----------------------
# Two allocations, release the first, then force two consecutive wraps.
tiny = trace(
    "case1 minimal, tiny buffer",
    [("get", 18), ("get", 1), ("release", 0), ("get", 17), ("get", 16)],
    24,
)

# --- Case 2: same 5-op shape at the production default buffer size ----------
# NixlWriteEmbeddingReceiver.__init__ defaults to 2*8*1024*1024*256*2 = 8 GiB.
# For the second wrap to be admitted the first allocation must exceed half the
# buffer, so these are the sizes of large multi-image / video requests, which is
# exactly what the 8 GiB default is sized for.
prod_ops = [
    ("get", mib(5120)),
    ("get", mib(344)),
    ("release", 0),
    ("get", mib(4096)),
    ("get", mib(4608)),
]
prod = trace("case2 same shape, production 8 GiB default", prod_ops, 8 * GIB)

# --- Case 3: prove the tensors really share memory, and show the corruption --
print("=" * 78)
print("case3: the two overlapping tensors alias one buffer, so bytes cross over")
print("=" * 78)
ring = RingBuffer(24)
id_a, tensor_a = ring.get_buffer(18)
id_b, tensor_b = ring.get_buffer(1)
ring.release_buffer(id_a)
id_c, tensor_c = ring.get_buffer(17)  # post-wrap allocation
id_d, tensor_d = ring.get_buffer(16)  # overlaps C
print(f"  id_c={id_c} range={ring.allocated_buffer_id_to_range[id_c]}")
print(f"  id_d={id_d} range={ring.allocated_buffer_id_to_range[id_d]}")
print(f"  live buffers: {sorted(ring.allocated_buffer_id_to_range)}")
print(f"  tensor_c.data_ptr()={tensor_c.data_ptr()} tensor_d.data_ptr()={tensor_d.data_ptr()}")


def fill(tensor, value):
    """Simulate the peer's NIXL WRITE landing in the published address range.

    receive_embeddings publishes transfer_tensor.data_ptr() to the peer and the
    peer's WRITE is a plain copy into that address range, so writing the tensor
    is byte-for-byte what the transfer does.
    """
    tensor.fill_(value)
    return tensor


fill(tensor_c, 61)
observed_after_c = int(tensor_c[0].item())
fill(tensor_d, 62)
observed_after_both = int(tensor_c[0].item())
print(f"  C reads {61} after its own WRITE, then {observed_after_both} after D's WRITE")
aliasing_proven = observed_after_c == 61 and observed_after_both == 62
print(f"  D's WRITE silently rewrote C's live bytes: {aliasing_proven}")
print()

# --- Case 4: control. Same op script, buffer large enough to never wrap twice -
print("=" * 78)
print("case4 CONTROL: same op shapes on a buffer with no second wrap")
print("=" * 78)
ctl_ring = RingBuffer(4096)
ctl_live = {}
ctl_overlaps = []
for op in [("get", 3000), ("get", 100), ("release", 0), ("get", 900), ("get", 500)]:
    if op[0] == "get":
        bid, t = ctl_ring.get_buffer(op[1])
        if t is None:
            print(f"  get_buffer({op[1]}) -> (None, None) (correctly refused)")
            continue
        s, e = ctl_ring.allocated_buffer_id_to_range[bid]
        for oid, (os_, oe) in ctl_live.items():
            if s < oe and os_ < e:
                ctl_overlaps.append((bid, (s, e), oid, (os_, oe)))
        ctl_live[bid] = (s, e)
        print(f"  get_buffer({op[1]}) -> id={bid} [{s},{e})")
    else:
        ctl_ring.release_buffer(op[1])
        ctl_live.pop(op[1], None)
        print(f"  release_buffer({op[1]})")
print(f"  overlaps: {ctl_overlaps if ctl_overlaps else 'none'}")
print()

# --- Case 5: N of N determinism on the minimal trigger ----------------------
print("=" * 78)
print("case5: N of N - minimal trigger repeated, fresh RingBuffer each run")
print("=" * 78)
MINIMAL_OPS = [("get", 18), ("get", 1), ("release", 0), ("get", 17), ("get", 16)]
runs = 200
n_of_n = 0
for i in range(runs):
    r = RingBuffer(24)
    live = {}
    hit = False
    for op in MINIMAL_OPS:
        if op[0] == "get":
            bid, t = r.get_buffer(op[1])
            if t is None:
                continue
            s, e = r.allocated_buffer_id_to_range[bid]
            for oid, (os_, oe) in live.items():
                if s < oe and os_ < e:
                    hit = True
            live[bid] = (s, e)
        else:
            r.release_buffer(op[1])
            live.pop(op[1], None)
    n_of_n += 1 if hit else 0
print(f"  reproduced {n_of_n} of {runs} runs")
print()

# --- Case 6: randomized soak. Buffer size does not change the arithmetic ----
# get_buffer decides purely on index comparisons, so the defect is scale
# invariant. Soak at 4096 B with proportionally scaled request sizes to keep the
# same wrap geometry, which case2 already confirmed at the real 8 GiB default.
print("=" * 78)
print("case6: randomized soak, 4096 B buffer (same wrap geometry as case2)")
print("=" * 78)

SOAK_BUFFER = 4096
SOAK_MIN = SOAK_BUFFER // 4096  # 1 unit
soak_rng = random.Random(20261001)
soak_overlaps = []
soak_allocs = 0
SOAK_TRIALS = 4000
for trial in range(SOAK_TRIALS):
    ring = RingBuffer(SOAK_BUFFER)
    live = {}
    for _ in range(soak_rng.randint(4, 12)):
        if soak_rng.random() < 0.6:
            # Request sizes that can each be up to ~half the buffer, which is
            # the geometry that produces the second wrap.
            size = soak_rng.randint(SOAK_MIN, SOAK_BUFFER // 2)
            bid, t = ring.get_buffer(size)
            if t is None:
                continue
            soak_allocs += 1
            s, e = ring.allocated_buffer_id_to_range[bid]
            for oid, (os_, oe) in live.items():
                if s < oe and os_ < e:
                    soak_overlaps.append(
                        {
                            "trial": trial,
                            "new": (bid, s, e, size),
                            "live": (oid, os_, oe),
                            "live_bytes": sum(e2 - s2 for s2, e2 in live.values()),
                        }
                    )
            live[bid] = (s, e)
        else:
            if not live:
                continue
            victim = soak_rng.choice(list(live))
            ring.release_buffer(victim)
            del live[victim]

print(f"  trials: {SOAK_TRIALS}, allocations checked: {soak_allocs}")
print(f"  overlapping allocations found: {len(soak_overlaps)}")
soak_trials_with_overlap = len({o["trial"] for o in soak_overlaps})
print(f"  trials containing at least one overlap: {soak_trials_with_overlap}")
if soak_overlaps:
    first = soak_overlaps[0]
    print(f"  first overlap: trial {first['trial']}")
    print(f"    new id={first['new'][0]} [{first['new'][1]},{first['new'][2]}) "
          f"size={first['new'][3]}")
    print(f"    collided with live id={first['live'][0]} "
          f"[{first['live'][1]},{first['live'][2]})")
    print(f"    live bytes at that moment: {first['live_bytes']} "
          f"({first['live_bytes'] / SOAK_BUFFER:.0%} of the buffer)")
print()

# --- Case 7: control. Small sizes only, same soak, never two wraps ----------
print("=" * 78)
print("case7 CONTROL: same soak, request sizes too small to wrap twice")
print("=" * 78)
clean_overlaps = []
clean_allocs = 0
for trial in range(SOAK_TRIALS):
    ring = RingBuffer(SOAK_BUFFER)
    live = {}
    rng = random.Random(trial)
    for _ in range(rng.randint(4, 12)):
        if rng.random() < 0.6:
            size = rng.randint(SOAK_MIN, SOAK_BUFFER // 16)
            bid, t = ring.get_buffer(size)
            if t is None:
                continue
            clean_allocs += 1
            s, e = ring.allocated_buffer_id_to_range[bid]
            for oid, (os_, oe) in live.items():
                if s < oe and os_ < e:
                    clean_overlaps.append((bid, (s, e), oid, (os_, oe)))
            live[bid] = (s, e)
        else:
            if not live:
                continue
            victim = rng.choice(list(live))
            ring.release_buffer(victim)
            del live[victim]
print(f"  allocations checked: {clean_allocs}")
print(f"  overlapping allocations found: {len(clean_overlaps)}")
print()

print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"  source file                : embedding_transfer.py @ 5593e8857")
print(f"  file sha256                : {_source_sha256}")
print(f"  RingBuffer.get_buffer      : L{_extracted[1][1]}-L{_extracted[1][2]} of the file")
print(f"  case1 minimal overlap      : {len(tiny)}")
print(f"  case2 production 8 GiB     : {len(prod)}")
print(f"  case3 aliasing proven      : {aliasing_proven}")
print(f"  case4 control (no 2nd wrap): {len(ctl_overlaps)}")
print(f"  case5 N of N               : {n_of_n}/{runs}")
print(f"  case6 soak overlaps        : {len(soak_overlaps)} in "
      f"{soak_trials_with_overlap}/{SOAK_TRIALS} trials")
print(f"  case7 control (small sizes): {len(clean_overlaps)}")
print(f"  generated at               : {datetime.now(timezone.utc).isoformat()}")
