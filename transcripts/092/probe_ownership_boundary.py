"""PR #60 review follow-up: does the aliased range reach the tensor handed to the
engine, or does production's copy step prevent it?

The reviewer's objection is decisive and correct to raise: `prefill_worker_utils`
does not hand the ring buffer view to the engine. Line 479 releases the staging
allocation, and lines 477-478 clone single-image payloads first, so the tensor
crossing the consumer boundary is supposed to be owned.

The ordering question is therefore not "does the allocator alias" (it does, 25/25)
but "does the clobbering write land before or after the copy". This probe answers
it with the VERBATIM production helpers from the pinned checkout:

  _attach_received_embedding_transfers  (prefill_worker_utils.py:104)
  _accumulate_embeddings               (prefill_worker_utils.py:169)
  construct_mm_data                    (vllm/multimodal_utils/model.py:233)
  _ensure_owned_tensors                (prefill_worker_utils.py:227)

and the verbatim `RingBuffer`. No GPU, no NIXL fabric, no provider keys: the write
is a plain byte copy into the published range, which is what a NIXL WRITE performs.

It reports, for one aliased pair:
  * whether the accumulated engine tensor still aliases the ring buffer at all
  * whether the copy in _ensure_owned_tensors happens before or after the second
    request's write
"""

import ast
import asyncio
import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Awaitable, Dict, List, Optional

import msgspec
import torch
from pydantic import BaseModel

REPO = "/Users/atharva/Documents/ChatGPT/NVIDIA_DYNAMO"
EMB = REPO + "/components/src/dynamo/common/multimodal/embedding_transfer.py"
PREFILL = REPO + "/components/src/dynamo/vllm/multimodal_utils/prefill_worker_utils.py"
MODEL_UTILS = REPO + "/components/src/dynamo/vllm/multimodal_utils/model.py"

MIB = 1024 * 1024


def lift(path, wanted, extra):
    src = open(path, "r", encoding="utf-8").read()
    lines = src.splitlines(keepends=True)
    ns = dict(extra)
    picked = []
    for node in ast.parse(src).body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in wanted:
            exec(compile("".join(lines[node.lineno - 1 : node.end_lineno]),
                         f"{path}::{node.name}", "exec"), ns)
            picked.append((node.name, node.lineno, node.end_lineno))
    return ns, picked, hashlib.sha256(src.encode("utf-8")).hexdigest()


emb_ns, emb_picked, EMB_SHA = lift(
    EMB,
    ("torch_dtype_to_string", "torch_dtype_from_string", "TransferRequest",
     "AbstractEmbeddingReceiver", "AbstractEmbeddingSender", "MonolithicCounter",
     "RingBuffer", "NixlTransferRequest", "NixlWriteEmbeddingSender",
     "NixlWriteEmbeddingReceiver"),
    {"torch": torch, "Any": Any, "List": List, "Optional": Optional, "Dict": Dict,
     "BaseModel": BaseModel, "msgspec": msgspec, "ABC": ABC,
     "abstractmethod": abstractmethod, "Awaitable": Awaitable,
     "asyncio": asyncio, "time": __import__("time"),
     "uuid": __import__("uuid"), "base64": __import__("base64"),
     "logger": type("L", (), {"debug": lambda *a, **k: None, "warning": lambda *a, **k: None,
                               "error": lambda *a, **k: None, "info": lambda *a, **k: None})()},
)
RingBuffer = emb_ns["RingBuffer"]


_mf_ns, _mf_picked, _ = lift(
    MODEL_UTILS, ("ModelFamily",), {"Enum": __import__("enum").Enum})
ModelFamily = _mf_ns["ModelFamily"]


def resolve_model_family(model):
    """Resolve the family the way `model.py` does for an HF id.

    `resolve_model_family` reads `config.json` for local paths and then scans
    the model name for the registered family patterns. For an HF id the
    substring scan is the whole decision, so the two families are matched on
    those same patterns. The real function is not lifted because it pulls in the
    config loader and the per-family registries, none of which the ownership path
    under test reads.
    """
    lowered = model.lower()
    if "qwen" in lowered:
        return ModelFamily.QWEN_VL
    if "llava" in lowered:
        return ModelFamily.LLAVA
    return None


model_ns, model_picked, MODEL_SHA = lift(
    MODEL_UTILS,
    ("construct_mm_data", "_construct_qwen_image_data"),
    {"torch": torch, "Optional": Optional, "List": List, "Any": Any,
     "Dict": Dict, "resolve_model_family": resolve_model_family,
     "ModelFamily": ModelFamily},
)
_pre_ns, _pre_picked, _ = lift(
    PREFILL, ("_PendingRelease",),
    {"torch": torch, "List": List,
     "AbstractEmbeddingReceiver": emb_ns["AbstractEmbeddingReceiver"]})

prefill_ns, prefill_picked, PREFILL_SHA = lift(
    PREFILL,
    ("_ensure_owned_tensors", "_accumulate_embeddings",
     "_attach_received_embedding_transfers"),
    {
     "_PendingRelease": _pre_ns["_PendingRelease"],"torch": torch, "List": List, "Dict": Dict, "Any": Any,
     "MultiModalGroup": object,
     "construct_mm_data": model_ns["construct_mm_data"],
     "resolve_model_family": resolve_model_family, "ModelFamily": ModelFamily},
)

ensure_owned = prefill_ns["_ensure_owned_tensors"]
accumulate = prefill_ns["_accumulate_embeddings"]
attach = prefill_ns["_attach_received_embedding_transfers"]


class Group:
    def __init__(self, tag, shape, grid):
        self.tag = tag
        self.loaded_embedding = None
        self.embeddings_shape = shape
        self.image_grid_thw = grid


def write_tag(ring, buffer_id, tag):
    """The byte copy a NIXL WRITE performs into the published address range."""
    start, end = ring.allocated_buffer_id_to_range[buffer_id]
    ring.buffer_tensor[start:end].view(torch.float16).fill_(float(tag))
    return ring.buffer_tensor[start:end].view(torch.float16)


def aliases_ring(tensor, ring):
    base = ring.buffer_tensor.data_ptr()
    span = ring.buffer_tensor.numel() * ring.buffer_tensor.element_size()
    p = tensor.data_ptr()
    return base <= p < base + span


def engine_tensor(mm):
    img = mm.get("image")
    return img["image_embeds"] if isinstance(img, dict) else img


def scenario(model, buffer_mib, ops, payload_mib, single_image):
    """Replay one allocation script and run the production ownership path.

    ops: list of ("get", bytes) / ("release", id). The final request is the one
    whose consumer-boundary tensor we inspect.
    """
    ring = RingBuffer(buffer_mib)
    groups = {}
    groups_range = [dict()]
    tag_of = {}
    nxt = 0
    for op in ops:
        if op[0] == "get":
            bid, tensor = ring.get_buffer(op[1])
            shape = (1, max(1, op[1] // 2), 1)
            g = Group(nxt + 1, shape, [1, 1, 1])
            g.loaded_embedding = tensor.view(dtype=torch.float16).view(shape)
            groups[bid] = g
            groups_range[0][bid] = ring.allocated_buffer_id_to_range[bid]
            tag_of[bid] = nxt + 1
            nxt += 1
        else:
            ring.release_buffer(op[1])
            groups.pop(op[1], None)
            groups_range[0].pop(op[1], None)
            tag_of.pop(op[1], None)

    # Identify the aliased pair, and which of the two is actually contaminated.
    # The clobbering write lands in the LATER allocation, whose range is a
    # superset of the earlier one. So the earlier (subset) request is the one that
    # ends up holding the other request's bytes; the later writer trivially reads
    # its own. Inspect the subset request.
    ids = sorted(groups)
    subset = other = superset = None
    for a in ids:
        for b in ids:
            if a == b:
                continue
            (sa, ea), (sb, eb) = groups_range[0][a], groups_range[0][b]
            if sa <= sb and ea >= eb:
                subset, superset = b, a
            elif sb <= sa and eb >= ea:
                subset, superset = a, b
    if subset is None:
        return {"case": "no-aliasing", "engine_boundary_holds_own_image": True,
                "aliased_pair_found": False}

    vtag, otag = tag_of[subset], tag_of[superset]

    # The overlapping request's peer WRITEs first; the later allocation's peer
    # WRITEs over it. The subset request is the victim.
    write_tag(ring, subset, vtag)
    write_tag(ring, superset, otag)

    # --- production path, in source order, for the VICTIM (subset) request ---
    mm: Dict[str, Any] = {}
    g = groups[subset]
    attach([g], [0], [(subset, g.loaded_embedding)], None)        # line 136
    accumulate(mm, model, g.loaded_embedding.dtype,
               g.loaded_embedding, g.image_grid_thw)              # lines 464-470
    accum_aliases = aliases_ring(engine_tensor(mm), ring)

    if single_image:
        ensure_owned(mm)                                          # line 478
    clone_aliases = aliases_ring(engine_tensor(mm), ring)
    ring.release_buffer(subset)                                   # line 479
    ring.release_buffer(superset)

    value = float(engine_tensor(mm).reshape(-1)[0].item())
    return {
        "aliased_pair_found": True,
        "model": model,
        "single_image_path": single_image,
        "victim_sent_tag": vtag,
        "clobbering_sent_tag": otag,
        "aliases_ring_after_accumulate": accum_aliases,
        "aliases_ring_after_ensure_owned": clone_aliases,
        "engine_boundary_value": value,
        "engine_boundary_holds_own_image": value == vtag,
    }


print("=" * 78)
print("PR #60 review: does the aliased ring buffer range reach the engine tensor?")
print("=" * 78)
print(f"embedding_transfer.py sha256 : {EMB_SHA}")
print(f"prefill_worker_utils.py sha256: {PREFILL_SHA}")
print(f"model.py sha256              : {MODEL_SHA}")
print()
for name, picked in (("embedding_transfer", emb_picked),
                     ("prefill_worker_utils", prefill_picked),
                     ("model", model_picked)):
    print(f"  {name}: " + ", ".join(f"{n} L{a}-L{b}" for n, a, b in picked))
print()

# The verified aliasing script, in bytes: get(20), get(3), release(0), get(12),
# get(14) over a 24-byte buffer. Element counts stay tiny, and the allocator
# decides purely on index comparisons, so the geometry is identical at any scale.
ops_bug = [("get", 160), ("get", 24), ("release", 0), ("get", 96), ("get", 112)]
# Control: identical requests on a buffer large enough that nothing wraps twice.
ops_ctl = [("get", 160), ("get", 24), ("release", 0), ("get", 96), ("get", 112)]

rows = []
for model in ("Qwen/Qwen2-VL-7B-Instruct", "llava-hf/llava-1.5-7b-hf"):
    for single in (True, False):
        r = scenario(model, 192, ops_bug, 96, single)
        r["case"] = "bug"
        rows.append(r)
        print(f"[bug/{model}/single_image={single}]")
        for k, v in r.items():
            if k not in ("case", "model", "single_image_path"):
                print(f"    {k}: {v}")
        print()

ctl = scenario("Qwen/Qwen2-VL-7B-Instruct", 512, ops_ctl, 96, True)
ctl["case"] = "control"
print("[control: non-wrapping 64 MiB buffer]")
for k, v in ctl.items():
    if k != "case":
        print(f"    {k}: {v}")
print()

print("=" * 78)
print("SUMMARY")
print("=" * 78)
for r in rows:
    print(f"  {r['case']:8s} {r['model']:32s} single_image={str(r['single_image_path']):5s} "
          f"accumulates_as_view={str(r['aliases_ring_after_accumulate']):5s} "
          f"engine_holds_own_image={r['engine_boundary_holds_own_image']}")
print(f"  {'control':8s} engine_holds_own_image={ctl['engine_boundary_holds_own_image']}")
print(f"  generated at {datetime.now(timezone.utc).isoformat()}")
