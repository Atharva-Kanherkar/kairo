# 096, NIXL `releaseXferReq` truncation is cancellation semantics, not a lost-bytes bug

**Verdict: not a bug in the framing first proposed. Kept as an honest negative.**

## The claim that was withdrawn

An earlier draft of this folder claimed that `nixlAgent::releaseXferReq()`
returning `NIXL_SUCCESS` is a bug because most of the requested bytes are never
written. The measurement behind it is real and is kept in
`transcripts/096-release-truncation-negative/`. The interpretation was wrong.

## What was measured

NIXL `main` = `464c86388d2c6373ab922d852fa245d81bfb5fb6`, POSIX backend, kernel
AIO queue, real file, real `nixlAgent`. 512 descriptors of 512 KiB, each region
filled with its own index byte so the file records which regions landed.
`transcripts/096-release-truncation-negative/rig/repro_release_truncates.cpp`.

| descriptors | written when released early | missing | written when polled to terminal |
|---|---|---|---|
| 64 | 64 | 0 | 64 |
| 128 | 128 | 0 | 128 |
| 130 | 128 | 2 | 130 |
| 512 | 128 | 384 | 512 |

10 of 10 rounds, zero variance, `release` = `NIXL_SUCCESS` every time.

## Why that is not a bug

`releaseXferReq` is documented as: *"If the transfer is active, it will be
canceled, or return an error if the transfer cannot be aborted"*
(`src/api/cpp/nixl.h:381`). Releasing an active transfer means cancelling it. A
cancelled transfer is *supposed* to leave requested bytes unwritten.

nixl#1955 states the maintainer's contract precisely: *"Release should succeed
only after all entries referencing the request have completed **or been
canceled**."* Unwritten bytes are the expected outcome of the "canceled" branch.
The proposed invariant, that a successful release must imply every requested byte
was transferred, conflates cancellation with completion and would reject every
correctly cancelled transfer. It was wrong, and the checker built on it was
removed rather than kept.

The batch arithmetic is real but it measures the cancel boundary, not a defect:
the engine's queue submits at most 64 iocbs per `post()`
(`src/plugins/posix/linux_aio_io_queue.cpp:185`) and the release path polls once,
so at most 128 of 512 ever reach the kernel. That is consistent with a transfer
that was cancelled after two batches.

## The defect that does exist, and where it is filed

The entries that were neither completed nor cancelled still hold the freed
request handle as their callback context, which is the use-after-free reported in
[nixl#1955](https://github.com/ai-dynamo/nixl/issues/1955) (OPEN) and
[nixl#2067](https://github.com/ai-dynamo/nixl/issues/2067) (CLOSED). The contract
sentence above is a contract about those entries, not about the bytes on disk.

This finding therefore adds no independent defect to 1955. It is recorded here so
the measurement and the reasoning are not lost, and so the same framing is not
proposed again.

## Reviewer record

An independent review returned `REJECT` on this finding. Its central objection,
that a successful release does not imply a completed transfer and that
cancellation may legitimately leave bytes unwritten, was correct and is the
reason this folder is a negative. Its secondary objections were also adopted:
`fsync` return values are now checked in the 095 harness, short reads are no
longer accepted silently, and the second finding's artifacts were split out of
this folder into [095](https://github.com/ai-dynamo/kairo/blob/main/issues/095-nixl-len0-registration-invisible-to-transfers).