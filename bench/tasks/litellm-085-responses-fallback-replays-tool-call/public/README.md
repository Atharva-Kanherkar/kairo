# Reproduction kit

`reproduce.sh` starts the real `litellm` proxy CLI from `/work/repo` with a
master key and `router_settings.fallbacks`, backed by a deterministic
Responses-API upstream (`upstream.py`) whose behavior is keyed on the
deployment model name: the primary streams a completed `append_ledger`
function call and then fails with an in-band `error` event; the fallback
streams its own `append_ledger` call and completes. It prints the raw SSE the
client received and every request the upstream saw. Other scenarios
(`response.failed`, failure before any output, a dropped connection, no fault)
are available in `upstream.py`. Proxy log: `/tmp/litellm-proxy.log`.
