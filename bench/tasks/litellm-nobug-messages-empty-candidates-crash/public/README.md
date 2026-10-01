# Reproduction kit

`reproduce.sh` starts the real `litellm` proxy CLI from `/work/repo` (with a
master key) with one `gemini/...` deployment pointed at a deterministic local
Gemini API (`upstream.py`). A prompt containing `EMPTY` gets a
`generateContent` response with no candidates (a blocked prompt); anything
else gets a normal text answer. It prints what the Anthropic client received.
Proxy log: `/tmp/litellm-proxy.log`.
