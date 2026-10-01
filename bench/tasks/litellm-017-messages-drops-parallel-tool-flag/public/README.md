# Reproduction kit

`reproduce.sh` starts the real `litellm` proxy CLI from `/work/repo` (with a
master key) with one `openai/...` deployment pointed at a deterministic
OpenAI upstream (`upstream.py`). It sends the reported Anthropic `/v1/messages`
request and prints the client response and the exact body LiteLLM forwarded
(LiteLLM sends `/v1/messages` for OpenAI models to `/v1/responses`). Proxy log:
`/tmp/litellm-proxy.log`.
