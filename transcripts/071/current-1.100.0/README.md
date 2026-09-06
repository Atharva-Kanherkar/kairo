# Issue 071 Current-Release Check

Checked 2026-09-06 against `litellm[proxy]==1.100.0`, the latest GitHub release at
the time of this run. Tag `v1.100.0` resolves to
`e4f25265704e2b2c6cf6e81be2e4c5cffff896f4`.

The same issue 071 runner and unchanged `litellm-leak.yaml` were used with only
the executable path and expected package version changed. The runner verifies
the installed package version before starting the actual LiteLLM CLI. No provider
credentials are inherited and no provider completion is requested.

| Probe | Result |
|---|---|
| `GET /model/info` | query canary disclosed 5/5 |
| `GET /v1/model/info` | query canary disclosed 5/5 |
| `GET /v1/models` | no canary 5/5 |
| `GET /health/liveliness` | no canary 5/5 |

Each `.http` file contains the exact request and response bytes from the fifth
probe, with CRLF framing. Each `.json` file pairs the parsed body with the route
and status. `client-results.json` records all five probes' aggregate counts.

Run from the repository root, using a temporary environment of your choice:

```bash
uv venv --python tools/litellm-env/bin/python /tmp/kairo-071-current
uv pip install --python /tmp/kairo-071-current/bin/python 'litellm[proxy]==1.100.0'
python3 -c 'import sys, tempfile; sys.path.insert(0, "transcripts/071"); import reproduce; reproduce.LITELLM_BIN = "/tmp/kairo-071-current/bin/litellm"; reproduce.LITELLM_VERSION = "1.100.0"; work = tempfile.TemporaryDirectory(prefix="kairo-071-current-"); output = tempfile.mkdtemp(prefix="kairo-071-current-captures-"); reproduce.capture(output, work.name); work.cleanup()'
```

For the checked-in refresh, the temporary environment was
`/var/folders/h7/02529x_j2196_9dnc1k5pnr80000gn/T/opencode/kairo-071-litellm-current`
and the output directory was `transcripts/071/current-1.100.0`. Neither directory
name affects the model configuration or request bytes.

This confirms the issue is not fixed on 1.100.0. It does not establish
authenticated non-admin access or actual reuse of a live provider credential.
