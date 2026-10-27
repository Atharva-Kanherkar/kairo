# Can a client override Switchyard's provider key with `api_key` in the request body?

A security review of our gateway fleet found that LiteLLM lets a client pass
`"api_key"` (and `"organization"`, `"extra_headers"`) in the JSON body and uses
it instead of the deployment's own credential. We assume Switchyard has the same
hole: a caller sending

```json
{"model": "captured-model", "api_key": "sk-attacker", "organization": "org-attacker",
 "extra_headers": {"Authorization": "Bearer sk-attacker"},
 "messages": [{"role": "user", "content": "hi"}]}
```

to `/v1/chat/completions` would get their key used against our backend (or
ours used against theirs). Please close it.
