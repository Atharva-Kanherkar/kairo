# Reproduction kit

`reproduce.sh` calls `any_llm.messages()` from the editable install in
`/work/repo` with `provider="openai"` and `api_base` pointed at a local
deterministic OpenAI-compatible upstream (no network, no keys). It prints the
client result and the exact JSON body the upstream received.
