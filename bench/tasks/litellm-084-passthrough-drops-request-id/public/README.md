# Reproduction kit

`reproduce.sh` starts the real `litellm` proxy CLI from `/work/repo` with
`OPENAI_API_BASE` pointed at a local deterministic upstream that behaves like
OpenAI's `/v1/alpha/search` (HTTP 200 when the body has `id`, HTTP 400
`missing_required_parameter` otherwise). It sends the reported request through
`/openai_passthrough/v1/alpha/search` and prints the client response and the
exact body the upstream received. The proxy runs with a master key
(`rigs/litellm.py`), and the client authenticates with it. Proxy log: `/tmp/litellm-proxy.log`.
