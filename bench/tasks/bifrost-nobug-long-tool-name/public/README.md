# Reproduction kit

`reproduce.sh` rebuilds the Bifrost HTTP gateway from `/work/repo` and starts
it with the built-in `openai` provider pointed at a deterministic local
Responses-API upstream (`upstream.py`) that records every request. It sends
the reported request and prints what the client received and exactly what
Bifrost forwarded (body and headers). Gateway log: `/tmp/bifrost.log`.
