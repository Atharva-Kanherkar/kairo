# The sweep rig

kairo's issue folders answer "does gateway X drop field Y". The sweep answers
the question underneath that: **of every field a cross-format gateway has to
carry, how many survive, on each gateway, measured the same way.**

That is the denominator. "34 defects" has no base rate. "Of 44 probed fields
across 5 gateways, 220 cells, N preserved" does, and it makes the passes as
reportable as the failures.

```
tools/sweep-and-pr.sh                 # 60 minutes, capture only, draft PR
tools/sweep-and-pr.sh --live          # add the live impact leg (real keys)
tools/sweep-and-pr.sh --minutes 20    # shorter budget
tools/sweep-and-pr.sh --no-pr         # run and write files, do not push
python3 -m tools.sweep.sweep --dry-run    # corpus self-check, no network
```

## What it does

| phase | budget | what happens |
|---|---|---|
| 0 preflight | | start the capture mock, attach or launch each gateway |
| 1 rectangular | ~60% | every gateway times every probe, one pass |
| 2 determinism | ~20% | repeat only the non-clean cells to N runs (default 5) |
| 3 live impact | ~15% | `--live` only: what the loss costs a real run |
| 4 report + PR | ~5% | reserved up front, never skipped |

Phase 4's budget is reserved before phase 1 starts, so a run that hits its
deadline still writes its matrix. A timeout costs depth, never results.

Phase 2 deliberately repeats only the non-clean cells. Determinism is leg three
of the method in CONTRIBUTING, and it is the cells that came back dirty where
"5/5" is load-bearing. Spending the same budget re-confirming passes would buy
much less.

## Outputs

- `issues/MATRIX.md` , the matrix by axis, with preservation rates and a legend.
- `issues/CANDIDATES.md` , non-clean cells with no matching kairo issue, ranked,
  plus any known defect that came back clean.
- `transcripts/sweep/<runid>/` , the wire bytes per cell, credential-scrubbed.

## What it deliberately does not do

**It does not write issue folders.** Every folder in `issues/` is a hand-verified
claim with a control, a determinism count, and a writeup that says what was
checked and what was not. Generating those would undercut the one thing that
makes this repo worth citing. The sweep hands you ranked leads and frozen bytes;
the claim is still yours, one bug per PR.

**It does not commit to main.** It refuses unless you are on a clean `main`,
branches, verifies `cargo test` and the README counter check are green, and
opens the PR as a draft.

**It does not hide an absent gateway.** A gateway that cannot be attached to or
launched is recorded as `--` across its column with the reason. An absent
gateway and a clean gateway must never look the same in the results.

## The probe corpus

`probes.py` is the interesting file. 44 probes over four axes:

- **request** (26): the top-level `/v1/messages` parameter space, from
  `stop_sequences` and `output_config.format` through `thinking`, the three
  `tool_choice` shapes, `metadata.user_id`, `service_tier`, and the newer
  surface (`mcp_servers`, `context_management`, `cache_control`).
- **content** (7): image and document blocks, `tool_result.is_error`,
  multimodal tool results, thinking blocks replayed in history, tool-use id
  round trips.
- **header** (4): the credential-leak checks, plus `anthropic-beta`.
- **response** (7): upstream shapes translated back to the client, including
  `finish_reason` mapping, `content_filter`, refusal content, and invented
  empty text blocks.

19 of the 44 are **positive controls**: fields a kairo issue already documents
as lost. If those come back clean on a first run, the rig is not exercising the
path, and that reading is surfaced in `CANDIDATES.md` rather than celebrated.

Four probes are **inverted**: the three credential-leak headers and the
invented-empty-text check report clean when the gateway did *not* do the bad
thing. The matrix accounts for this and the legend says so.

`EXPECTED_LOSS` is its own verdict, separate from `DROPPED`. `top_k` has no
OpenAI chat equivalent, so its absence is a documented boundary rather than a
defect, and burying that in the same bucket as a real drop would inflate the
numbers.

## Adding a probe

```python
req("req.my_field", "my_field",
    base(my_field={"probe": "MARKER-1234"}),
    lambda fwd, hdr, cli: PRESERVED if has_key(fwd, "target_name") else DROPPED,
    severity="high",
    control=dict(model="M", max_tokens=64, target_name=...,   # optional
                 messages=[{"role": "user", "content": "hi"}]))
```

Then run `python3 -m tools.sweep.sweep --dry-run`. The self-check runs every
probe against a synthetic gateway that forwards everything and one that
forwards nothing, and fails any checker that can never fire or can never pass.
It caught three of its own fixtures the first time it ran.

`has_key` skips `messages` / `input` / `content` by default, so a marker echoed
into prompt text is not mistaken for a surviving wire field. That is the same
discipline as the 040 checker.

## Gateways

Pre-filled for LiteLLM, Switchyard, Bifrost, GoModel, and AxonHub, using the
same launch shapes as the frozen repro blocks in `issues/`.

Every adapter supports **attach mode**: if something is already listening on the
ingress port, the sweep uses it as-is. That is the path for AxonHub, whose
channels live in its database rather than a config file, and for anyone running
a build the launch recipe does not fit. Start the gateway with its backend
pointed at `http://127.0.0.1:9990/v1`, then run the sweep.

Adding a gateway is one subclass in `gateways.py`: a port, a `write_config` that
points the backend at `self.mock_base()`, and a `launch_argv`.

## Credentials

The capture leg needs no keys at all. Its three leak probes send synthetic
markers (`CLIENTSECRET-3311` and friends), so a gateway that forwards the
client's credential upstream is caught without a real key ever existing in the
run.

The live leg does use real keys, read from the environment and never written.
Everything it records passes through `redact()` first, which scrubs both the
literal values of known key env vars and anything matching the usual key
shapes. Given that this repo has five issues about gateways leaking
credentials, a rig that leaked one into a committed transcript would be its own
punchline.

## A note on the mock

`mock.py` is separate from `tools/capture_headers.py` on purpose. That file is
frozen evidence-producing tooling cited by the repro blocks in existing issue
writeups; changing it would invalidate their reproducibility. The sweep mock
adds two things it needs: a canned reply re-read from disk on every request, so
the runner can stage a different upstream response per probe without a restart,
and SSE replies for the streaming probes.
