# Reproduction kit

`reproduce.sh` starts the real `litellm` proxy CLI from `/work/repo` (with a
master key) with one deployment whose `extra_headers`, `headers`, and
`aws_session_token` hold canary values, pointed at a deterministic
OpenAI-compatible upstream. It calls `GET /health` and prints the response,
then makes one chat completion and prints the headers the upstream received.
Proxy log: `/tmp/litellm-proxy.log`.
