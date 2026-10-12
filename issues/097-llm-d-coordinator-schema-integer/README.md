# 097, the llm-d Router coordinator rounds large JSON integers before every leg, so a structured-output schema constrains the model to a value the client's own validator rejects

- **Upstream**: [llm-d/llm-d-router](https://github.com/llm-d/llm-d-router).
  The defect class is already ruled a bug in this repository and fixed in two of
  its three components:
  [#2518](https://github.com/llm-d/llm-d-router/issues/2518) "Request payload
  repackaging silently loses precision for large JSON integers", **closed
  2026-08-25 as fixed** by
  [#2519](https://github.com/llm-d/llm-d-router/pull/2519) (merged), whose release
  note reads "Preserve JSON number precision when HTTP request payloads are
  mutated and repackaged". That fix covered the EPP parsers.
  [sidecar #2590/#2591](https://github.com/llm-d/llm-d-router/pull/2591) fixed the
  same class in the sidecar.
  The coordinator's own copy of the root cause is tracked as
  [#2621](https://github.com/llm-d/llm-d-router/issues/2621) (**open**,
  2026-08-31), which reports the sibling symptom, key reordering, and states the
  expectation: "Fields the coordinator does not modify are forwarded
  byte-for-byte to prefill, decode and render."
  [#3141](https://github.com/llm-d/llm-d-router/pull/3141) (**open**,
  2026-10-03) is a bug fix for #2621 that would also stop the rounding by
  forwarding untouched fields as raw JSON. It is mergeable but unmerged as of
  2026-10-10, so the defect is live on current main.
  No issue or pull request was found for the coordinator's large-integer rounding
  itself.
- **Tool under test**: llm-d Router at commit
  `567e35d752c841521fd7c450e8335e5dae108a7c`
  (2026-10-09, module version `v0.10.0-rc.1.0.20261009160539-567e35d752c8`), the
  `cmd/coordinator` service built from that clean checkout with go1.27.2.
  Route: `POST /v1/chat/completions` on the coordinator's own TLS listener.
  Configuration as documented for a disaggregated OpenAI-format pipeline:
  `use_openai_format: true`, `kv_connector: kv-shared-storage`,
  `ec_connector: ec-shared-storage`, steps `replace-media-urls`, `render`,
  `encode`, `prefill`, `decode`.
- **Upstream dependency**: the local capture server described in
  `transcripts/097/rig/build-and-run.md`. It is not a mock of the coordinator; it
  stands in for the inference workers the coordinator addresses, which is the
  measurement this claim needs: what the coordinator forwards. No inference
  engine, no GPU, and no provider key are involved.
- **Consumer under test**: `jsonschema` 4.25.1 `Draft202012Validator` on Python
  3.9.6, validating the engine's answer against the schema the client sent.
- **Reproduced**: 2026-10-09, rerun from a fresh build 2026-10-10, macOS arm64.
  **5 of 5** runs per case. 15 runs in the recorded matrix, 9 more in the
  boundary sweep.
- **Label**: `bug`.

## What breaks

A platform serves agents that need a specific record identified in the model's
answer, and asks for it with structured output: `response_format` set to
`json_schema`, `strict: true`, with a schema whose `enum` (or `const`, or
`minimum`/`maximum` pair) pins one large integer. Large integers are ordinary
here. They are database primary keys, snowflake ids, 64-bit counters, and
account ids issued by other systems. Any of them above `2^53` is a victim.

The client sends this:

```json
{"model":"m","messages":[{"role":"user","content":"Return the allowed record ID."}],"max_tokens":32,"response_format":{"type":"json_schema","json_schema":{"name":"record","strict":true,"schema":{"type":"object","properties":{"record_id":{"type":"integer","enum":[9007199254740993]}},"required":["record_id"],"additionalProperties":false}}}}}
```

The coordinator parses the body into a `map[string]any` and re-marshals it for
every leg. By the time the render request leaves, the schema reads

```json
"enum":[9007199254740992]
```

and the same rounded value appears on the prefill and decode legs, 5 of 5 runs.
The client sent one number and the engine was asked for a different one.

The consequence at the closest practical consumer is the schema no longer
matching the request that produced it. An engine that constrains generation to
the schema it was handed can now only produce a value the client's own validator
rejects, and the recorded capture demonstrates exactly that: the capture
upstream answers within the schema it received, as a guided-decoding engine
would, and validating that answer against the client's schema gives

```text
9007199254740992 is not one of [9007199254740993]
```

5 of 5. This is not a model failure and not a validation bug in the client. The
client's schema is intact, the answer is well formed, and the two disagree
because a number was changed in between. The operator sees a schema validation
error on a response that "looks right", and the most likely debugging path is
the wrong one: the schema, the client, or the model.

Two conditions make it worse in practice. The client's own request is never
rewritten, so a request-body diff against what the client sent shows nothing
wrong. And the gateway logs nothing: the coordinator's own timeline for the
failing runs (`transcripts/097/capture/coordinator-log.txt`) is a clean,
successful request per run with no error and no warning, and the client got
HTTP 200.

The inverse also holds and is the same bug: a `const`, `minimum`, `maximum`,
`multipleOf`, or any other pinned large integer in a request body is forwarded
rounded, so the constraint the engine enforces is not the one the client asked
for.

## Wire evidence

`transcripts/097/capture/large-01/client-request.http`, the request as it
arrived, verbatim:

```text
POST /v1/chat/completions HTTP/1.1
Host: localhost:62576
Content-Type: application/json
X-Request-Id: large-01
Content-Length: 352
Connection: close

{"model":"capture-model","messages":[{"role":"user","content":"Return the allowed record ID."}],"max_tokens":32,"response_format":{"type":"json_schema","json_schema":{"name":"record","strict":true,"schema":{"type":"object","properties":{"record_id":{"type":"integer","enum":[9007199254740993]}},"required":["record_id"],"additionalProperties":false}}}}
```

`transcripts/097/capture/large-01/render-request.http`, the bytes the coordinator
sent upstream. Only the disputed value and the key order differ; the key order
is a consequence of the re-marshal and is covered by separate upstream issue
#2621, and nothing else in the request changed:

```text
POST /v1/chat/completions/render HTTP/1.1
Host: 127.0.0.1:62575
User-Agent: Go-http-client/1.1
Content-Length: 352
Content-Type: application/json
X-Llm-D-Revision-Decision-Id: a43bfc7b-0811-48d5-ae71-a026c3cfe095
X-Request-Id: large-01
Accept-Encoding: gzip

{"max_tokens":32,"messages":[{"content":"Return the allowed record ID.","role":"user"}],"model":"capture-model","response_format":{"json_schema":{"name":"record","schema":{"additionalProperties":false,"properties":{"record_id":{"enum":[9007199254740992],"type":"integer"}},"required":["record_id"],"type":"object"},"strict":true},"type":"json_schema"}}
```

`prefill-request.http` and `decode-request.http` in the same cell carry the same
rounded value, so the change is made once, before any leg runs, not by one step.
Each also carries the transfer fields the coordinator is expected to add
(`kv_transfer_params`, `max_completion_tokens`, `stream`) and the prefill leg's
`max_tokens: 1` budget cap; those are intended edits and are the reason the
checker takes a declared-field allowlist.

**Controls, each one rung of the differential ladder, 5 of 5 each.**

- `safe-01`, same coordinator, same route, same request with the identifier
  `42`: every leg forwards `42`, and validation is clean. This isolates the
  magnitude as the trigger.
- `direct-01`, the coordinator removed: the same request sent straight to the
  upstream. The forwarded bytes carry `9007199254740993` and validation is
  clean. This isolates the coordinator as the cause, not the client, the
  validator, the capture upstream, or the network.
- `boundary/`, nine magnitudes swept in one coordinator process. The first
  integer that changes is `2^53 + 1`; `2^53` survives, and so does `2^53 + 2`,
  because that is the next float the format can hold. `2^63 - 1` and `2^64 - 1`
  lose far more. The pattern is the signature of a float64 decode, not a
  truncation, a format change, or a per-value bug:

  ```text
               submitted ->              forwarded  outcome
                      42 ->                     42  preserved
        9007199254740992 ->       9007199254740992  preserved
        9007199254740993 ->       9007199254740992  ROUNDED
        9007199254740994 ->       9007199254740994  preserved
        9007199254740995 ->       9007199254740996  ROUNDED
       18014398509481984 ->      18014398509481984  preserved
       18014398509481985 ->      18014398509481984  ROUNDED
     9223372036854775807 ->    9223372036854776000  ROUNDED
    18446744073709551615 ->    18446744073709552000  ROUNDED
  ```

- `boundary/b-08/render-request.http`, the raw forwarded bytes for the largest
  case, showing `"enum":[18446744073709552000]`.

`transcripts/097/capture/results.json` records all 15 runs, including the
consumer validation errors, and
`transcripts/097/capture/coordinator-log.txt` records the coordinator's own
timeline for the ten coordinator-routed runs (five `large`, five `safe`): no
error, no warning, one successful request per run. The five `direct` runs bypass
the coordinator, so they appear in no coordinator log.

## Root cause

One line in the request handler.

`pkg/coordinator/server/handlers.go:125`:

```go
var parsed map[string]any
if err := json.Unmarshal(body, &parsed); err != nil {
```

`encoding/json` decodes a JSON number into `float64` unless the decoder opts
into `UseNumber`, and the map stored on `RequestContext.Body` is what the steps
forward. The three marshal sites that turn it back into bytes are
`pkg/coordinator/steps/render.go:333`, `pkg/coordinator/steps/prefill.go:86`, and
`pkg/coordinator/steps/decode_proxy.go:54`, and a `float64` encoder writes the
nearest representable value. The result is that a request the client sent exactly
is forwarded with its integers rounded, on every leg, including the decode leg
that produces the client's answer.

The repository already states this rule twice in comments, for two other
components:

```go
// pkg/sidecar/proxy/connector_ec_common.go:174-177
// UseNumber keeps a number outside float64 range readable: Python's json
// parses it, so vLLM serves a body the default decoder would reject, and
// dropping the turn would leave its images unprimed. json.Number also
// re-marshals as the literal the client sent.
```

```go
// pkg/common/request/tokens.go:138-140
// ... UseNumber keeps that refusal off bodies the model server parses: a
// number outside float64 range fails a default []any decode while leaving the
// document valid.
```

**A one-line fix a maintainer would ship.** Decode the body with the project's
existing number-preserving helper, `parserutil.Unmarshal` in
`pkg/epp/framework/plugins/requesthandling/parsers/util/json.go` (introduced by
#2519 and used by all three EPP parsers), or add `dec.UseNumber()` to a decoder
in `handleInference` as `pkg/common/request/tokens.go:149` and the sidecar
already do. Forwarding untouched fields as raw JSON, which open PR #3141 does
for #2621, also stops the rounding and is the larger change.

The upstream evidence for the fix being wanted is not a docstring: #2518 was
filed as a bug, closed as fixed, and shipped with a release note naming the
user-facing change; the sidecar equivalent was merged the same month; and #2621
states byte-for-byte forwarding as the coordinator's expected behaviour.

## Bug or not

- **Is the expected behavior really the spec?** Yes, and the spec is the project's
  own ruling. #2518 is the same one-line defect with a different entry point, was
  accepted as a bug, closed as fixed, and released with a note about it. The
  coordinator's expectation is written in open issue #2621. Two other components
  in the same tree carry comments saying the body must keep numbers outside
  float64 range and must re-marshal as the literal the client sent. Nothing in
  the project documents, tests, or demonstrates intentional rounding; there is no
  test asserting a rounded forward, and no comment marking it as intended.
- **Have maintainers already ruled on it?** Yes, twice, in favour of this report
  and against the code: #2519 for the EPP path and #2591 for the sidecar. The
  remaining work is the third component. No denylist or commit classifies the
  field the other way.
- **Is the trigger supported usage?** Yes. `POST /v1/chat/completions` with a
  `response_format` json_schema is the documented OpenAI structured-output
  contract, `use_openai_format: true` with the `render`, `encode`, `prefill` and
  `decode` steps is the documented disaggregated pipeline, and the connectors
  used are the shared-storage ones. Nothing is disabled, no secret is involved,
  and no exotic layout is required. A structured-output request needs no field
  beyond the ordinary ones.
- **Is a real boundary crossed?** The coordinator is trusted to carry the request
  to the workers. It changes a number in it, silently, and the client's schema
  and the engine's constraint no longer agree. The failure surfaces as the
  client's own validation error rather than a gateway error.
- **What fix would a maintainer ship?** One line: decode the body with the
  existing `UseNumber` helper. Not a doc edit, not "do not send large integers",
  not an unsupported configuration.

## Upstream status

Searched on 2026-10-10 in `llm-d/llm-d-router` with `gh search issues` on:
`float64`, `precision`, `integer`, `UseNumber`, `round`, `9007199254740993`,
`coordinator precision`, `large integer`, plus the same terms restricted to the
coordinator component.

- **#2518** (closed 2026-08-25, fixed by #2519): the same root cause
  (`json.Unmarshal` into a generic map), reported for the EPP request parsers.
  The reproduction in that issue is a `seed` of `9007199254740993` rounding to
  `9007199254740992`, the same value pair as here.
- **#2621** (open 2026-08-31): the coordinator's copy of the same root cause,
  reported as nested key reordering. It names `handleInference` and the same
  three marshal sites, and states byte-for-byte forwarding as the expectation.
- **#3141** (open 2026-10-03, kind bug, mergeable, review required): the fix for
  #2621. It decodes only the fields the handler and steps read and leaves
  everything else a `json.RawMessage`, which also preserves untouched integers.
- No issue or pull request reports the coordinator's large-integer rounding.
  Searching `9007199254740993` returns #2518 only.

Classification: **discussed-no-ticket** for this variant. The defect class has a
bug ruling and a merged fix elsewhere in the repository, the coordinator's root
cause has an open ticket with an open fix pull request for a sibling symptom,
and the precision variant specifically has no ticket. It is not `fixed`: main at
`567e35d7`, nine days after #3141 was opened, still rounds, and the recorded
runs on a binary built from that commit are the evidence. It is not `duplicate-open`
either, because the open ticket describes key order, not value precision, and a
reviewer following #2621's reproduction would not produce this failure.

Because the fix for the sibling symptom is not merged, this finding is worth
reporting on its own: it is the second consumer-visible consequence of the same
line, and it is the one that changes what the engine is asked to produce.

## Test

`crates/harness/src/checks.rs`, `forwarded_body_preserves_values`. The invariant
is stated as a property of the wire, not as a description of this bug: a gateway
that decodes a request body into a generic map and re-marshals it for every hop
must forward the mathematical value of every JSON number it does not
intentionally change. The comparison ignores object key order, because a
re-marshaled Go map sorts its keys and that is a separate defect, and it takes a
declared allowlist for the transfer fields a hop is allowed to add or rewrite.
Integer comparison never routes through `f64`, because two distinct integers in
`[2^53, 2^54)` are the same `float64` and an `f64` comparison would call the
rounding conformant. Integers are compared exactly up to `u64::MAX`; beyond that
the check cannot be exact, and the checker documents that as its own limit.

`crates/harness/tests/conformance.rs` asserts the verdict in both directions
against frozen bytes:

- `Violation` on all three legs of `large-01`, naming both values.
- `Conformant` on `safe-01` (same route, identifier 42).
- `Conformant` on the coordinator-removed control, comparing the `large`
  client request against the `direct` forwarded bytes.
- `Conformant` on the prefill leg once its transfer fields are declared, and
  `Violation` on the same bytes with an empty allowlist, so an undisclosed field
  change still fails.
- The five-run matrix: 15 runs, 5 corrupted with a consumer validation error and
  10 clean, each cell's forwarded bytes re-checked by the checker.
- Determinism across the five large runs, and the boundary sweep, where a
  representable value must survive and an unrepresentable one must be reported
  as rounded.
- Seven unit tests in `checks.rs` cover reordered keys, declared orchestration
  fields (including that the allowlist applies only at the top level, so a
  rounded integer inside a nested object is still reported), absent and
  malformed evidence, and other value changes.

## Reproduce

```bash
# pin and build, see transcripts/097/rig/build-and-run.md
git clone https://github.com/llm-d/llm-d-router
cd llm-d-router && git checkout 567e35d752c841521fd7c450e8335e5dae108a7c
go build -o /tmp/llm-d-coordinator-567e35d ./cmd/coordinator

python3 -m venv /tmp/kairo-097-venv
/tmp/kairo-097-venv/bin/pip install jsonschema==4.25.1

python3 transcripts/097/reproduce.py \
  --target /path/to/llm-d-router \
  --binary /tmp/llm-d-coordinator-567e35d \
  --output /tmp/kairo-097-rerun
```

Expected output:

```text
{"large_schema_corrupted": 5, "safe_controls": 5, "direct_controls": 5, "runs_per_case": 5}
```

The script writes the full client and forwarded bytes plus `results.json` under
the output directory, asserts the pinned and clean checkout, waits for the
coordinator's own `/healthz` before running any case, and exits non-zero if any
assertion fails. A rerun from a fresh build on 2026-10-10 printed the same line.

The frozen bytes are re-checked by the harness, so a change in the target shows
up as a test flip:

```bash
cargo test --workspace
python3 tools/update-readme-counts.py --check
```

## Limitations

- One commit of one component was run. `main` at `567e35d7` is 2026-10-09, which
  is after open PR #3141 was opened, so the fix is not in it. Whether a later
  commit fixes it was not tested, and #3141 was not built or run here.
- No inference engine. The capture upstream answers deterministically within the
  schema it received, which is what a guided-decoding engine does, but no real
  model's behaviour under the rounded schema was measured, and no claim about
  generated text is made.
- Streaming was not exercised. Both the client requests and the forwarded legs
  are buffered JSON, so the streamed forwarding path is unmeasured.
- `stream: false` requests on the OpenAI dialect only. The `/v1/messages` path,
  `/v1/responses`, `use_openai_format: false`, and the sidecar were not run.
- The sidecar's own `UseNumber` sites were read, not run, and its behaviour with
  a large integer in an untouched field is not measured here.
- One schema shape was swept: a single-property object with an integer `enum`.
  `const`, `minimum`, `maximum`, `multipleOf`, and nested occurrences follow the
  same code path but were not swept individually.
- macOS arm64 only. The rounding is a property of Go's `encoding/json` on any
  platform, but no second platform was executed.
- Only the coordinator's forward was measured. Nothing about the EPP, the
  gateway, or any worker was tested.
