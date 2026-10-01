# Reproduction kit

`reproduce.sh` rebuilds the Bifrost HTTP gateway from `/work/repo` (Go
workspace, warm cache; about 15 s), starts it with a built-in `openai`
provider and a chat-only custom provider pointed at a deterministic local
OpenAI upstream (`upstream.py`), sends the reported `/anthropic/v1/messages`
request through both, and prints the client responses and the bodies the
upstream received. Gateway log: `/tmp/bifrost.log`. Rebuild by hand with
`bifrost-build`.
