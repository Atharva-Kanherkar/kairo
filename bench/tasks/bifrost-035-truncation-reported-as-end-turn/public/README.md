# Reproduction kit

`reproduce.sh` rebuilds the Bifrost HTTP gateway from `/work/repo` (Go
workspace, warm cache; about 15 s) and starts it with the built-in `openai`
provider pointed at a deterministic Responses-API upstream (`upstream.py`,
whose reply is chosen by a marker word in the prompt). It sends the reported
`/anthropic/v1/messages` request and prints what the client received. Gateway
log: `/tmp/bifrost.log`. Rebuild by hand with `bifrost-build`.
