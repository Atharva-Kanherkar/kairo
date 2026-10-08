# 095, a NIXL `len == 0` registration is invisible to the transfer path at any nonzero address

## Claim

A registration that the tool accepts, and that `deregisterMem` can later find
again, must also be resolvable by the transfer path. NIXL implements its
`len == 0` convention in only one of its two lookup paths, so a `FILE_SEG`,
`OBJ_SEG` or `BLK_SEG` region registered with `len == 0` can be registered and
deregistered but never transferred, unless its address happens to be 0.

- **Upstream project**: [ai-dynamo/nixl](https://github.com/ai-dynamo/nixl).
- **Cited upstream issue**: none. Classification `novel` as of 2026-10-08.
- **Target type**: open-source, run locally.
- **Tested release or commit**: `main` =
  `464c86388d2c6373ab922d852fa245d81bfb5fb6` ("fix(obj): reject duplicate devId
  registrations (#2131)"), shallow clone fetched 2026-10-08.
- **Client dialect and endpoint**: NIXL's public C++ API, `nixlAgent`. No HTTP.
- **Backend dialect and provider or capture upstream**: POSIX plugin, kernel AIO
  queue (`libaio`), a real file. Not a mock.
- **Model, if relevant**: not applicable.
- **Relevant configuration**: defaults. `createBackend("POSIX", {})`, no custom
  parameters, default queue sizes.

## What breaks

`nixlSecDescList` normalizes `len == 0` to `SIZE_MAX` for the three
object-shaped segment types, meaning "this whole object from this address". It
does so twice:

- `normalizeSecDesc()`, `src/infra/mem_section.h:131`, on the way in, so the
  stored entry has `len == SIZE_MAX`.
- `normalizeQuery()`, `src/infra/mem_section.h:150`, inside `getIndex()`, which
  is the lookup `remDescList` uses. It re-normalizes the query, so deregistration
  matches and succeeds.

`getCoveringIndex()` (`mem_section.h:108`, `nixl_memory_section.cpp:515`) has no
`normalizeQuery` call. It calls `nixlBasicDesc::covers()`
(`src/api/cpp/nixl_descriptors.h:115`), which evaluates

```
(devId == query.devId) && (addr <= query.addr) &&
    ((addr + len) >= (query.addr + query.len))
```

With `len == SIZE_MAX` and `addr != 0` that add wraps: `4096 + SIZE_MAX` is 4095
mod 2^64. The bound becomes `addr - 1`, which is below every query that starts at
or after `addr`, so `covers()` is false for every possible query. The entry
cannot be found by the transfer path.

So `nixlMemSection::populate()` returns `NIXL_ERR_UNKNOWN`,
`createXferReq` reports `NIXL_ERR_NOT_FOUND`, and the log line says *"no
specified or potential backend had the required registrations to be able to do
the transfer"*, which is indistinguishable from never having registered. At
`addr == 0` the add cannot wrap and the same registration works, so the
behaviour turns on an address the caller did not choose as a switch.

Who it hurts: anyone registering whole-object regions for a block or object
store. `BLK_SEG` is a first-class public memory type, exposed as `BLOCK` in the
Python API (`src/api/python/_api.py:271`). A caller that follows the codebase's
own `len == 0` convention gets a registration it can create and destroy and never
use, and an error message that blames the wrong thing.

Conditions: any `len == 0` registration at a nonzero address, for the three
object-shaped segment types. Block segments are addressed by offset, so a
nonzero address is the normal case, not an edge case.

Measured versus inferred: the failed resolution, the successful deregistration
and the successful transfers at offset 0 and with an explicit length are
**measured**, 10 of 10, with byte verification on disk. The serving consequence
is **inferred**: no block-store backend was runnable here, so no serving
workload is claimed.

## Wire evidence

`transcripts/095/`:

- `rig/probe_unbounded_len.cpp`, the harness. Real `nixlAgent`, real POSIX
  plugin, real file. Each cell runs in its own agent against a fresh file. Cells
  that report a transfer also read 4 KiB back off disk and compare.
- `rig/probe_covers_rootcause.cpp`, root cause, compiled against the pinned
  checkout's own headers and calling the real `nixlBasicDesc::covers()` with the
  values `normalizeSecDesc` produces. No logic is copied.
- `cells/`, one JSON record per cell per round, 4 cells x 10 rounds.
- `rig/root-cause-covers.txt`, its output.
- `rig/environment.txt`, pinned commit and toolchain.

The only variable under test is the address, then the length. Same backend, same
file, same 4 KiB transfer:

| cell | reg `len` | offset | register | createXferReq | bytes verified | deregister |
|---|---|---|---|---|---|---|
| A | 0 | 0 | 0 | **0** | yes | 0 |
| B | 0 | 4096 | 0 | **-4** | no | **0** |
| C | 4096 | 4096 | 0 | **0** | yes | 0 |
| D | 0 | 4096 | 0 | -4 | no | **0** |

A versus B isolates the offset. B versus C isolates the length. Each cell has one
distinct outcome across 10 rounds.

Root cause output, from the real `covers()`:

```
base{addr=0,    len=SIZE_MAX}.covers(q{4096,4096}) = true
base{addr=4096, len=SIZE_MAX}.covers(q{4096,4096}) = false
base{addr=4096, len=4096}   .covers(q{4096,4096}) = true
(4096 + SIZE_MAX) mod 2^64 = 4095
```

## Root cause

`src/infra/mem_section.h:150`. `normalizeQuery()` exists precisely to make the
lookup side agree with the registration side, and it is applied in `getIndex()`
but not in `getCoveringIndex()`. A one-line fix is to resolve the query through
`normalizeQuery()` in `getCoveringIndex()` as well, or to make `covers()`
saturate rather than wrap when `len == SIZE_MAX`.

## Test

`registration_resolvable_by_both_paths()` in `crates/harness/src/checks.rs`. The
invariant is about the tool, not about NIXL: a registration must not be visible to
one lookup path and invisible to the other. It reads a cell record and flags only
the combination that cannot be right, accepted for registration, removable by the
descriptor that created it, yet unresolvable for a range the registration covers.
A cell where the range is outside the registration, or where nothing was
registered, is conformant, so a correct refusal is never scored as a finding.

Covered by `nixl_len0_registration_at_nonzero_offset_is_unresolvable`,
`nixl_len0_registration_at_offset_zero_resolves`,
`nixl_explicit_len_at_the_same_offset_resolves`,
`nixl_unresolvable_registration_is_still_deregisterable` and
`nixl_registration_evidence_must_be_sound` in
`crates/harness/tests/conformance.rs`.

## Not verified

- `OBJ_SEG` and `BLK_SEG` share the code path by construction
  (`usesUnboundedLen()` lists all three), but neither backend is exercisable
  here: GUSLI's `BLK_SEG` branch is still a `Todo` stub and the OBJ plugin needs
  an S3 endpoint. Only `FILE_SEG` is claimed end to end.
- No in-tree caller passes `len == 0` today, so this is a live convention with no
  current consumer rather than a hit on someone's hot path. That is the main
  reason the consequence is stated as a misleading error and a dead registration
  rather than data loss.
- The release path's behaviour is a separate matter and is recorded as an honest
  negative in
  [096](https://github.com/ai-dynamo/kairo/blob/main/issues/096-nixl-release-truncation-is-cancellation-not-a-bug).