# Safety-filtered answers look like normal completions on `/v1/messages`

Switchyard fronts an OpenAI-compatible backend for our Anthropic-SDK clients.
When the backend's safety system stops a generation, it returns
`finish_reason: "content_filter"` with whatever partial text it produced.
Switchyard's `/v1/messages` response for that turn says:

```json
{"stop_reason": "end_turn", "content": [{"type": "text", "text": "I can"}]}
```

Our client cannot tell a moderated, cut-off answer from a complete one, so the
truncated text is shown to users as the model's final reply and our moderation
metrics miss it. Streaming shows the same thing. Switchyard's own OpenAI route
passes `content_filter` through. The safety stop should stay distinguishable
from a normal end of turn on the Anthropic route too.
