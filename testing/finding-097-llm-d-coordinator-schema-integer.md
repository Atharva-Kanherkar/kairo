# Finding 097 test contract

## Functional behavior
A coordinator may add transfer metadata and cap the prefill token budget. It must preserve the mathematical values in the client response_format JSON Schema. A record ID in an integer enum must not change.

## Unit tests
The schema-preservation checker compares the client and forwarded schema. It detects altered, absent, or malformed schemas, accepts reordered object keys, and does not mistake intended transfer-field changes for violations. Integer comparisons must preserve values above 2^53.

## Integration and functional tests
Build llm-d Router at 567e35d752c841521fd7c450e8335e5dae108a7c. Run cmd/coordinator with the documented OpenAI format, shared-storage connectors, and render/encode/prefill/decode steps. Exercise POST /v1/chat/completions over its real TLS listener. Capture client and upstream raw HTTP bytes for five runs each of a large integer enum, a safe integer control, and a direct-upstream control. Check all render/prefill/decode requests.

## Smoke tests
The real coordinator health endpoint answers before cases execute.

## Consumer boundary
Use a real JSON Schema validator to validate a deterministic schema-conformant capture response against the client schema. Report capture-only measured consequences separately from any production engine inference. Do not claim live-model behavior was tested.

## E2E tests
Full GPU inference is not applicable to the forwarding invariant. No claim about model generation or GPU execution.

## Manual reproduction
python3 transcripts/097/reproduce.py --target /Users/atharva/llm-d-router --binary /tmp/llm-d-coordinator-567e35d --output /tmp/kairo-097-rerun

## Acceptance
All three Kairo gates and repository checks must pass. An independent read-only reviewer must rerun the critical path and falsify the claim before ACCEPT. No publication is authorized.
