# Reproduction kit

`reproduce.sh` starts the real `litellm` proxy CLI from `/work/repo` (with a
master key) with one `openai/...` deployment pointed at a deterministic
Responses-API upstream (`upstream.py`). A prompt containing `REFUSE` gets a
structured refusal, `TOOL` gets a function call, anything else gets plain text;
`"stream": true` gets the matching SSE events. The script sends the reported
`/v1/messages` request (non-streaming and streaming) and prints what the
client received and what the upstream was sent. Proxy log:
`/tmp/litellm-proxy.log`.
