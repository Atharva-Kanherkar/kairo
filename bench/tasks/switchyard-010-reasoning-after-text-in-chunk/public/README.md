# Reproduction kit

`reproduce.sh` rebuilds `switchyard-server` from `/work/repo` (`switchyard-build`,
incremental dev build) and starts it with one passthrough route
(`captured-model`) whose llm_client points at a deterministic local OpenAI
Chat Completions upstream (`upstream.py`). It sends the reported request and
prints what the client received and what Switchyard forwarded. Server log:
`/tmp/switchyard.log`.
