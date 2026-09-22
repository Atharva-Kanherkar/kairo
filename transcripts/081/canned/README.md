# Canned upstream response for kairo 081

The self-contained capture server returns a deterministic OpenAI chat
completion for every request. It serves `mock-gpt` from `/v1/models` and
records sanitized request bodies in `capture.jsonl`.
