# Reproduction kit

`reproduce.sh` rebuilds the Bifrost HTTP gateway from `/work/repo` and starts
it with governance enabled (admin credentials, `enforce_auth_on_inference`, one
provider-scoped virtual key) and a custom OpenAI-compatible provider whose
`network_config.extra_headers` carries a canary secret. The deterministic
upstream (`upstream.py`) reflects request headers on its response, as some
real upstreams do. It prints the HTTP response a virtual-key caller receives
from `POST /v1/responses`. Gateway log: `/tmp/bifrost.log`.
