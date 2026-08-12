# NNN — one-line failure statement

- **Upstream**: link(s) to the cited issue(s), state at reproduction date
- **Tool under test**: name + exact version/commit
- **Reproduced**: date, environment (model, provider, flags)

## What breaks

Plain-English description of the observable failure and who it hurts
(which agent loops / SDKs key off the broken field).

## Wire evidence

- `upstream.http` — what the provider actually sent (captured, verbatim)
- `observed.http` — what the tool emitted to the client
- `expected.http` — what a lossless pipe must emit

## Root cause (if found)

File/line in the tool where the translation goes wrong.

## Test

`cases/NNN.rs` — replay assertion. States the invariant, not the bug:
e.g. "a streamed tool call's reconstructed arguments byte-equal the
provider's arguments."
