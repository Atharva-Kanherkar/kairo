"""End-to-end repro: two concurrent multimodal requests are handed the same ring
buffer bytes, so request B's image embeddings overwrite request A's in place.

This drives the verbatim `NixlWriteEmbeddingSender` and `NixlWriteEmbeddingReceiver`
from the pinned Dynamo checkout (commit 5593e8857), including the real
`receive_embeddings` / `send_embeddings` round trip and the real notification
protocol. The only substitute is the NIXL agent, replaced by a fake that performs
the operation a NIXL WRITE performs over RDMA: copy the source bytes into the
target address range. Everything about how that address range is chosen,
published, and re-read is Dynamo code.

Mechanism. RingBuffer.get_buffer uses `allocated_start_idx` as the exclusive lower
bound of the reusable head region, and guards a post-wrap allocation with

    if self.wrapped_around and end_idx > self.allocated_start_idx:

That bound is only valid before the first wrap. After the wrap branch runs, a
fresh allocation lands at [0, size) while `allocated_start_idx` keeps its old,
larger value, so the guard admits a second post-wrap allocation at [0, size) that
overlaps the still-live first one. The receiver publishes
`transfer_tensor.data_ptr()` to the peer, so both requests' writes land on the
same bytes. Request A returns a tensor holding request B's image.

Only `RingBuffer`, `MonolithicCounter`, the sender and receiver classes, and the
two request models are used verbatim from the checkout; their source ranges are
listed and hashed below.
"""

import asyncio
import ast
import base64
import hashlib
import json
import os
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Awaitable, Dict, List, Optional

import msgspec
import torch
from pydantic import BaseModel

REPO = "/Users/atharva/Documents/ChatGPT/NVIDIA_DYNAMO"
SOURCE_FILE = REPO + "/components/src/dynamo/common/multimodal/embedding_transfer.py"

# --- Load the classes under test verbatim from the pinned checkout -----------
# Importing the module runs dynamo.common.multimodal.__init__ and
# dynamo.common.utils.__init__, which import the compiled dynamo._core,
# dynamo.llm, and dynamo.prometheus_names extensions. None of the classes below
# use them, so each class is extracted by ast line range and exec'd unmodified.
# The sha256 of the whole file and of the extracted slice are printed below.
with open(SOURCE_FILE, "r", encoding="utf-8") as fh:
    _source = fh.read()

_source_sha256 = hashlib.sha256(_source.encode("utf-8")).hexdigest()
_lines = _source.splitlines(keepends=True)
_tree = ast.parse(_source)

WANTED = (
    "torch_dtype_to_string",
    "torch_dtype_from_string",
    "TransferRequest",
    "AbstractEmbeddingReceiver",
    "AbstractEmbeddingSender",
    "MonolithicCounter",
    "RingBuffer",
    "NixlTransferRequest",
    "NixlWriteEmbeddingSender",
    "NixlWriteEmbeddingReceiver",
)


class _Quiet:
    def debug(self, *a, **k):
        pass

    warning = error = info = debug


_ns = {
    "torch": torch,
    "asyncio": asyncio,
    "msgspec": msgspec,
    "BaseModel": BaseModel,
    "Any": Any,
    "Awaitable": Awaitable,
    "Dict": Dict,
    "List": List,
    "Optional": Optional,
    "ABC": ABC,
    "abstractmethod": abstractmethod,
    "logger": _Quiet(),
    "base64": base64,
    "time": time,
    "uuid": uuid,
}

_extracted_ranges = []
_extracted_text = []
for _node in _tree.body:
    if isinstance(_node, (ast.ClassDef, ast.FunctionDef)) and _node.name in WANTED:
        _chunk = "".join(_lines[_node.lineno - 1 : _node.end_lineno])
        _extracted_ranges.append((_node.name, _node.lineno, _node.end_lineno))
        _extracted_text.append(_chunk)
        exec(compile(_chunk, f"{SOURCE_FILE}::{_node.name}", "exec"), _ns)

_extracted_sha256 = hashlib.sha256("".join(_extracted_text).encode("utf-8")).hexdigest()

MonolithicCounter = _ns["MonolithicCounter"]
RingBuffer = _ns["RingBuffer"]
TransferRequest = _ns["TransferRequest"]
NixlTransferRequest = _ns["NixlTransferRequest"]
NixlWriteEmbeddingSender = _ns["NixlWriteEmbeddingSender"]
NixlWriteEmbeddingReceiver = _ns["NixlWriteEmbeddingReceiver"]

MIB = 1024 * 1024


class _FakeNixlAgentConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeNixlAgent:
    """Stands in for nixl._api.nixl_agent.

    Implements only the calls the two Dynamo classes make. The load-bearing method
    is `transfer`: a NIXL WRITE is a remote memcpy into the target address range,
    so this copies the registered source bytes there. `initialize_xfer` receives
    that address range from `get_xfer_descs`, which the receiver built from the
    pointer it published.

    Agents are registered in a class-level table so notifications route between
    them the way they do over RDMA: the sender delivers a completion notification
    into the receiver's inbox, keyed by the sender's agent id, which is exactly the
    key `receive_embeddings` polls on.

    Registered memory is also process-wide. The receiver publishes a pointer that
    lives in the receiver's address space and the sender WRITEs to it without ever
    mapping it locally; here both agents share one process, so a single pointer
    table models the remote address space.
    """

    registry = {}
    memory = {}
    refcounts = {}
    pinned = set()
    # Senders whose `_state_update` loop is running. See send_notif.
    senders = {}
    WAKE_BUDGET = 8

    def __init__(self, agent_id, config=None):
        self.agent_id = agent_id
        self.notifs = {}
        self._consumed = {}
        self.write_log = []
        _FakeNixlAgent.registry[agent_id] = self


    # -- notifications -------------------------------------------------------
    def send_notif(self, remote_agent_id, notif_msg):
        """Deliver a notification to the remote agent's inbox.

        NIXL routes this over the fabric to the named agent, so the payload lands
        in the target's inbox keyed by this agent's id, which is exactly the key
        the peer polls on.

        Harness note. `NixlWriteEmbeddingSender._state_update` only calls
        `get_new_notifs()` after it is woken by its `transfer_queue`, so a
        notification that arrives while the loop is parked on `queue.get()` is not
        seen until the next `send_embeddings` enqueues a task indicator. A real
        encode worker is continuously busy, so its queue is always being refilled
        and the loop is always polling. To reproduce that steady state without
        depending on unrelated timing, delivering a notification to a sender also
        wakes that sender's loop, exactly as the next real request would. This
        affects only when the loop polls, never what it computes.
        """
        target = _FakeNixlAgent.registry.get(remote_agent_id)
        if target is None:
            return
        target.notifs.setdefault(self.agent_id, []).append(notif_msg)
        sender = _FakeNixlAgent.senders.get(remote_agent_id)
        if sender is not None:
            # Enough wakeups for every pending handshake, so a burst of
            # concurrent transfers is drained rather than one per real request.
            for _ in range(_FakeNixlAgent.WAKE_BUDGET):
                sender.transfer_queue.put_nowait("task_indicator")

    def get_new_notifs(self):
        """Return notifications that arrived since the last call, and drain them.

        Real NIXL hands over only new notifications; one already delivered is never
        delivered twice. The sender's `_state_update` depends on that, because it
        looks each handshake up in `transfer_tracker` and a repeat would raise
        inside that loop's own `except Exception` backoff. Consumption is tracked
        per sender key and by position, because a sender can deliver several
        notifications under one key over the life of the process.
        """
        fresh = {}
        for key, payloads in self.notifs.items():
            already = self._consumed.get(key, 0)
            if len(payloads) > already:
                fresh[key] = list(payloads[already:])
                self._consumed[key] = len(payloads)
        return fresh

    def update_notifs(self):
        """Return the notifications still in the inbox. The receiver removes the
        ones it matches itself, so this must not drain."""
        return {k: list(v) for k, v in self.notifs.items()}

    # -- memory registration -------------------------------------------------
    def get_agent_metadata(self):
        return f"meta-{self.agent_id}".encode()

    def add_remote_agent(self, metadata):
        return f"remote-{metadata.decode()}"

    def register_memory(self, tensor):
        _FakeNixlAgent.memory[tensor.data_ptr()] = tensor
        return ("desc", tensor.data_ptr())

    def deregister_memory(self, desc):
        # The receiver registers its ring buffer exactly once at construction and
        # never deregisters it. Only the sender deregisters per-transfer source
        # registrations. Refcounted here so deregistering one sender's source
        # cannot unmap the receiver's buffer, which lives in the same table.
        key = desc[1]
        _FakeNixlAgent.refcounts[key] = _FakeNixlAgent.refcounts.get(key, 1) - 1
        if _FakeNixlAgent.refcounts[key] <= 0 and key not in _FakeNixlAgent.pinned:
            _FakeNixlAgent.memory.pop(key, None)

    def pin(self, tensor):
        """Mark memory that is registered once and must never be unmapped, which
        is how the receiver treats its ring buffer."""
        _FakeNixlAgent.pinned.add(tensor.data_ptr())

    def get_xfer_descs(self, arg, mem_type=None):
        if isinstance(arg, torch.Tensor):
            return ("srcdesc", arg.data_ptr(), arg.numel() * arg.element_size())
        ptr, nbytes = arg[0][0], arg[0][1]
        return ("tgtdesc", ptr, nbytes)

    # -- the WRITE -----------------------------------------------------------
    def initialize_xfer(self, direction, src, tgt, remote_agent_id, done_signal):
        return {"src": src, "tgt": tgt, "done": done_signal, "state": "PENDING"}

    def _resolve(self, ptr):
        """Find the registered tensor containing an address.

        The receiver registers its ring buffer once, as one contiguous allocation
        (`NixlWriteEmbeddingReceiver.__init__` line 660), and then publishes
        `data_ptr()` of whatever slice `RingBuffer.get_buffer` returned. A real
        NIXL agent knows the registered region and the NIC addresses any offset
        within it, so resolution is by containment, not exact pointer match.
        """
        for base, tensor in _FakeNixlAgent.memory.items():
            span = tensor.numel() * tensor.element_size()
            if base <= ptr < base + span:
                return tensor, ptr - base
        raise KeyError(f"no registered region contains address {ptr}")

    def transfer(self, handle, done_signal):
        src_ptr, dst_ptr, nbytes = handle["src"][1], handle["tgt"][1], handle["src"][2]
        src, src_off = self._resolve(src_ptr)
        dst, dst_off = self._resolve(dst_ptr)
        assert dst.element_size() == 1, "the ring buffer is int8"
        # A WRITE is a byte copy at the published offset inside the region.
        dst.view(-1)[dst_off : dst_off + nbytes] = (
            src.view(-1)[src_off : src_off + nbytes].view(torch.int8)
        )
        handle["state"] = "DONE"
        self.write_log.append((src_ptr, dst_ptr, nbytes))
        self.send_notif(RECEIVER_AGENT_ID, done_signal)

    def check_xfer_state(self, handle):
        return handle["state"]


RECEIVER_AGENT_ID = "receiver"


def image_embeddings(tag: int, nbytes: int) -> torch.Tensor:
    """One image's embedding tensor. Every element is the tag, so the value read
    back identifies which image's bytes a request received."""
    return torch.full((nbytes // 2,), float(tag), dtype=torch.float16)


def build_receiver(buffer_size):
    receiver = NixlWriteEmbeddingReceiver.__new__(NixlWriteEmbeddingReceiver)
    # Mirror NixlWriteEmbeddingReceiver.__init__, minus the NIXL agent handle.
    receiver.ring_buffer = RingBuffer(buffer_size)
    receiver.transfer_tensor = receiver.ring_buffer.buffer_tensor
    receiver.nixl_agent = _FakeNixlAgent(RECEIVER_AGENT_ID, _FakeNixlAgentConfig())
    receiver.nixl_agent.register_memory(receiver.transfer_tensor)
    # NixlWriteEmbeddingReceiver registers the ring buffer once in __init__ and
    # never releases it, so it must survive every sender-side deregistration.
    receiver.nixl_agent.pin(receiver.transfer_tensor)
    receiver.remote_agents = {}
    receiver.to_buffer_id = {}
    receiver.id_counter = MonolithicCounter()
    receiver.receive_timeout = 60
    receiver.agent_metadata = receiver.nixl_agent.get_agent_metadata()
    return receiver


def build_sender(agent_id):
    sender = NixlWriteEmbeddingSender.__new__(NixlWriteEmbeddingSender)
    sender.sender_id = agent_id
    sender.nixl_agent = _FakeNixlAgent(agent_id, _FakeNixlAgentConfig())
    sender.remote_agents = {}
    sender.agent_metadata_b64 = base64.b64encode(
        sender.nixl_agent.get_agent_metadata()
    ).decode("utf-8")
    sender.transfer_tracker = {}
    sender.registered_descs = {}
    sender.id_counter = MonolithicCounter()
    sender.transfer_queue = asyncio.Queue()
    sender.transfer_timeout = 60
    sender._state_update_task = asyncio.create_task(sender._state_update())
    _FakeNixlAgent.senders[agent_id] = sender
    return sender


async def one_transfer(sender, receiver, tag, nbytes):
    """The real request path.

    `sender.send_embeddings` stages the image embeddings and returns a
    TransferRequest; `receiver.receive_embeddings` picks a ring buffer range with
    `RingBuffer.get_buffer`, publishes its `data_ptr()` to the sender, and waits
    for the completion notification; the sender's own `_state_update` loop issues
    the WRITE into that range. The returned tensor is what the prefill worker
    attaches to the request and the engine consumes.
    """
    embeddings = image_embeddings(tag, nbytes)
    request, fut = await sender.send_embeddings(embeddings, stage_embeddings=True)
    tensor_id, embedding_tensor = await receiver.receive_embeddings(request)
    await asyncio.wait_for(fut, timeout=15)
    return tensor_id, embedding_tensor, embeddings


def aliased_pairs(ring):
    live = ring.allocated_buffer_id_to_range
    ids = sorted(live)
    return [
        (a, b)
        for i, a in enumerate(ids)
        for b in ids[i + 1 :]
        if live[a][0] < live[b][1] and live[b][0] < live[a][1]
    ]


async def run_case(buffer_size, label, plan, quiet=False):
    """Run a scripted sequence of real requests.

    `plan` is a list of (tag, payload_mib, release) triples describing requests in
    arrival order. Each request performs the real transfer. `release` True means
    the request finishes and its buffer returns to the pool, which is what the
    prefill worker does once the engine has consumed the embeddings; False means
    the request is still in flight and keeps holding its buffer.

    The last entry is always left in flight, because the defect needs two
    in-flight requests at once. Concurrency also keeps the encode worker's
    transfer queue fed, so its `_state_update` loop stays awake and polls for
    write requests exactly as it does in a live deployment.
    """
    _FakeNixlAgent.registry = {}
    _FakeNixlAgent.senders = {}
    _FakeNixlAgent.memory = {}
    _FakeNixlAgent.refcounts = {}
    _FakeNixlAgent.pinned = set()
    receiver = build_receiver(buffer_size)
    sender = build_sender("sender")
    ring = receiver.ring_buffer
    held = []
    try:
        for i, (tag, payload_mib, release) in enumerate(plan):
            if payload_mib == 0:
                # Release marker: an earlier in-flight request finishes now. No
                # transfer happens, only the real release_tensor path runs.
                receiver.release_tensor(held.pop(0)[0])
                if not quiet:
                    print(f"  request {i}: an earlier in-flight request completed "
                          f"and released; live buffers "
                          f"{sorted(ring.allocated_buffer_id_to_range)}")
                continue
            tensor_id, tensor, _ = await one_transfer(
                sender, receiver, tag, payload_mib * MIB
            )
            if release:
                receiver.release_tensor(tensor_id)
                state = "completed and released"
            else:
                held.append((tensor_id, tensor, tag))
                state = "IN FLIGHT"
            if not quiet:
                print(f"  request {i} (image {tag}, {payload_mib} MiB): {state}; "
                      f"live buffers {sorted(ring.allocated_buffer_id_to_range)}")
        print()

        pairs = aliased_pairs(ring)
        live = dict(ring.allocated_buffer_id_to_range)
        if not quiet:
            print(f"[{label}] buffer={buffer_size // MIB} MiB")
        for bid, (s, e) in sorted(live.items()):
            if not quiet:
                print(f"  id={bid} [{s},{e})  {e - s} bytes  "
                      f"ptr={ring.buffer_tensor[s:e].data_ptr()}")
        if not quiet:
            print(f"  pairs of live buffers that alias: {pairs}")
            print()

        # Read back what each in-flight request actually holds. Each tensor is a
        # view into the shared ring buffer, so an overlap shows up here.
        values = []
        for tensor_id, tensor, tag in held:
            got = int(tensor.view(-1)[0].item())
            values.append((tag, got))
            if not quiet:
                print(f"  request sent image {tag} and holds embedding value {got}")
        contaminated = any(got != tag for tag, got in values)
        if not quiet:
            print(f"  at least one request received another request's image: "
                  f"{contaminated}")
            print()
        return {
            "label": label,
            "buffer_mib": buffer_size // MIB,
            "aliased_pairs": pairs,
            "live_ranges": live,
            "values": values,
            "contaminated": contaminated,
        }
    finally:
        for tensor_id, _, _ in held:
            try:
                receiver.release_tensor(tensor_id)
            except KeyError:
                pass
        sender._state_update_task.cancel()


async def main():
    print("=" * 78)
    print("Dynamo multimodal embedding transfer: aliased ring buffer ranges")
    print("=" * 78)
    print(f"source file      : {SOURCE_FILE}")
    print(f"file sha256      : {_source_sha256}")
    print(f"extracted classes: {[(n, f'L{a}-L{b}') for n, a, b in _extracted_ranges]}")
    print(f"extracted sha256 : {_extracted_sha256}")
    print()

    # Arrival order and sizes mirror the verified op script
    # get(20), get(3), release(0), get(12), get(14).
    #
    # Request 0 (20 MiB) and request 1 (3 MiB) both stay in flight, so the tail is
    # nearly full. Request 0 then completes and releases, which is what advances
    # allocated_start_idx to 20. Request 2 (12 MiB) no longer fits in the 1 MiB
    # tail, so it wraps to [0,12) and stays in flight. Request 3 (14 MiB) also
    # cannot fit in the tail, so it wraps again and is admitted at [0,14),
    # overlapping request 2's still-live [0,12).
    bug_plan = [
        (1, 20, False),
        (2, 3, False),
        (1, 0, True),   # request 0 completes here and releases
        (3, 12, False),
        (4, 14, False),
    ]
    bug = await run_case(24 * MIB, "BUG: two in-flight requests both wrap", bug_plan)

    # Control: the identical request sequence and identical payloads, served from
    # a ring buffer large enough that neither late request needs to wrap. Same
    # code path, same concurrency, only the buffer size differs.
    control_plan = [
        (1, 20, False),
        (2, 3, False),
        (1, 0, True),
        (3, 12, False),
        (4, 14, False),
    ]
    control = await run_case(64 * MIB, "CONTROL: requests do not both wrap",
                             control_plan)

    # N of N: repeat both cases and count the outcomes, so determinism is
    # measured rather than asserted.
    runs = 25
    bug_hits = 0
    control_hits = 0
    for _ in range(runs):
        if (await run_case(24 * MIB, "BUG repeat", bug_plan, quiet=True))["contaminated"]:
            bug_hits += 1
        if (await run_case(64 * MIB, "CONTROL repeat", control_plan, quiet=True))["contaminated"]:
            control_hits += 1
    print(f"N of N: bug case contaminated {bug_hits} of {runs} runs")
    print(f"N of N: control case contaminated {control_hits} of {runs} runs")
    print()

    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    print(f"  BUG aliased live buffer pairs      : {len(bug['aliased_pairs'])} "
          f"{bug['aliased_pairs']}")
    print(f"  BUG per-request (sent, received)   : {bug['values']}")
    print(f"  CONTROL aliased pairs              : {len(control['aliased_pairs'])} "
          f"{control['aliased_pairs']}")
    print(f"  CONTROL per-request (sent, received): {control['values']}")
    print(f"  N of N bug runs contaminated        : {bug_hits}/{runs}")
    print(f"  N of N control runs contaminated    : {control_hits}/{runs}")
    print(f"  generated at                       : {datetime.now(timezone.utc).isoformat()}")

    # Machine-readable capture for the Rust invariant checker
    # `ring_buffer_allocations_are_disjoint`.
    capture = [
        {
            "case": "bug",
            "buffer_bytes": bug["buffer_mib"] * MIB,
            "plan": [
                {"image_tag": t, "payload_bytes": p * MIB, "released": p == 0}
                for (t, p, _r) in bug_plan
            ],
            "live_buffer_ranges": [
                list(rng) for _bid, rng in sorted(bug["live_ranges"].items())
            ],
            "aliased_pairs": [list(pair) for pair in bug["aliased_pairs"]],
            "per_request_sent_and_received": [list(v) for v in bug["values"]],
        },
        {
            "case": "control",
            "buffer_bytes": control["buffer_mib"] * MIB,
            "plan": [
                {"image_tag": t, "payload_bytes": p * MIB, "released": p == 0}
                for (t, p, _r) in control_plan
            ],
            "live_buffer_ranges": [
                list(rng) for _bid, rng in sorted(control["live_ranges"].items())
            ],
            "aliased_pairs": [list(pair) for pair in control["aliased_pairs"]],
            "per_request_sent_and_received": [list(v) for v in control["values"]],
        },
    ]
    out = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "capture-embedding-crossover.jsonl",
    )
    with open(out, "w", encoding="utf-8") as fh:
        for record in capture:
            # The repo's capture convention: each JSONL line wraps the payload in
            # a "body" object, which is what `capture_records` unwraps.
            fh.write(json.dumps({"body": record}) + "\n")
    print(f"wrote {out}")


asyncio.run(main())
