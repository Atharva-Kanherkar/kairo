# Reproduction kit

`reproduce.sh` rebuilds the GoModel gateway from `/work/repo` (`gomodel-build`,
warm Go cache) and starts it with a master key and one `openai`-type provider
pointed at a deterministic local Chat Completions upstream (`upstream.py`). It
sends the reported Anthropic `/v1/messages` request and prints the client
response and the body GoModel forwarded. Gateway log: `/tmp/gomodel.log`.
