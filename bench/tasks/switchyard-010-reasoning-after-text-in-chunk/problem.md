# Streamed thinking arrives after the answer when the backend sends both in one chunk

Our reasoning model (OpenAI-compatible, via Switchyard's `/v1/messages`) often
sends one streaming chunk that carries both `reasoning_content` and `content`:

```json
{"choices": [{"index": 0, "delta": {"reasoning_content": "Thinking it through.", "content": "The answer is 4."}}]}
```

Switchyard's Anthropic stream then opens the `text` block first and the
`thinking` block second. Anthropic clients (and our UI) expect thinking to
precede the answer it produced; with this order the answer renders before its
reasoning, and replaying the turn later puts the thinking block after the
text. When reasoning and content arrive in separate chunks the order is right.
