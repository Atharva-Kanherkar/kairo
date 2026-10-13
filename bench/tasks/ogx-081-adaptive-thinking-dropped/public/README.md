# Reproduction kit

`reproduce.sh` starts the real OGX server from `/work/repo` (`ogx go --insecure
--no-auth`) with `OPENAI_BASE_URL` pointed at a local deterministic
OpenAI-compatible upstream, sends the reported `/v1/messages` requests, and
prints each client response next to the chat completion body OGX forwarded.
Server log: `/tmp/ogx-server.log`.
