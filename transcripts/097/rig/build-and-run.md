# Rig: llm-d Router coordinator rounds large schema integers

Everything here runs on CPU. No GPU, no inference engine, no provider keys.
The upstream the coordinator talks to is a local capture server that records the
forwarded bytes verbatim.

## Pin

```
git clone https://github.com/llm-d/llm-d-router
cd llm-d-router && git rev-parse HEAD
# 567e35d752c841521fd7c450e8335e5dae108a7c
```

`git status --porcelain` must be empty. `transcripts/097/reproduce.py` asserts
both, so a dirty checkout fails the run instead of producing ambiguous bytes.

## Build the coordinator

```
cd /path/to/llm-d-router
go build -o /tmp/llm-d-coordinator-567e35d ./cmd/coordinator
```

`go.mod` declares `go 1.27.2`, so with the default `GOTOOLCHAIN=auto` an older
local Go fetches and uses that toolchain. The recorded binary reports:

```
$ go version -m /tmp/llm-d-coordinator-567e35d | head -1
/tmp/llm-d-coordinator-567e35d: go1.27.2
	mod	github.com/llm-d/llm-d-router	v0.10.0-rc.1.0.20261009160539-567e35d752c8
```

A rebuild from the same clean checkout on 2026-10-10 reproduced 5/5, 5/5, 5/5.

## Install the JSON Schema consumer

The consumer boundary is a real validator, not a shape assertion. Any recent
`jsonschema` works; the recorded runs used 4.25.1.

```
python3 -m venv /tmp/kairo-097-venv
/tmp/kairo-097-venv/bin/pip install jsonschema
/tmp/kairo-097-venv/bin/python -c "import importlib.metadata as m; print(m.version('jsonschema'))"
# 4.25.1
```

## Run

```
python3 transcripts/097/reproduce.py \
  --target /path/to/llm-d-router \
  --binary /tmp/llm-d-coordinator-567e35d \
  --output /tmp/kairo-097-rerun
```

The script starts a threaded capture server on a loopback port, writes the
coordinator configuration it will use to `<output>/coordinator.yaml`, starts the
coordinator on its own loopback port with TLS (the coordinator's
`secure_serving: true` generates a self-signed certificate), and waits for
`GET /healthz` to answer before any case runs. Every run writes the client
request and response bytes plus each leg's forwarded request bytes under
`<output>/<case>-<run>/`, and the summary to `<output>/results.json`.

It exits non-zero if any assertion fails, so a rerun is its own gate.

## Boundary sweep

```
python3 transcripts/097/boundary_sweep.py \
  --target /path/to/llm-d-router \
  --binary /tmp/llm-d-coordinator-567e35d \
  --output /tmp/kairo-097-boundary
```

Submits nine integer magnitudes and reads the render leg's forwarded value back
for each. Writes `boundary.jsonl`, `coordinator-log.txt`, `coordinator.yaml`
and one cell per value under the output directory, which it replaces on each
run.

## What the rig asserts

`capture/results.json` is written by `reproduce.py` and re-checked by
`crates/harness/tests/conformance.rs`, so the frozen bytes and the assertions
cannot drift apart:

- `large`: 5/5 forwarded a rounded value on the render, prefill and decode legs,
  and the capture response failed the client's own schema validation 5/5.
- `safe`: 5/5 forwarded the value unchanged, 5/5 validation clean.
- `direct`: 5/5 sent to the capture upstream with the coordinator removed,
  5/5 unchanged, 5/5 clean.

## Not measured here

- No inference engine. The capture upstream answers deterministically, so
  nothing here says what vLLM or any other engine would generate given the
  rounded schema.
- Streaming is not exercised. The rig uses `stream: false` requests, so the
  streamed forwarding path was not measured.
- Only `use_openai_format: true` with the documented `render`, `encode`,
  `prefill` and `decode` steps was run. Other pipelines, the `/v1/responses`
  path, and the sidecar were not exercised.
