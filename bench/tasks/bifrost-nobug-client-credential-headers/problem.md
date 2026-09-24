# Client credential headers leak through Bifrost to OpenAI

A security review flagged that some gateways forward the client's own
credential headers to the upstream provider. Our apps send an assortment of
them to Bifrost out of habit: `x-api-key`, `api-key`, `OpenAI-Organization`,
`OpenAI-Project`, and an `Authorization: Bearer` with the app's own token. We
believe Bifrost passes these through to OpenAI next to the provider key it
configures, which would let one tenant's credentials reach our provider
account and could mix organizations. Please make sure Bifrost only sends the
configured provider credentials upstream.

```bash
curl -s localhost:8080/anthropic/v1/messages -H 'content-type: application/json' \
  -H 'x-api-key: client-key' -H 'api-key: client-azure-key' -H 'OpenAI-Organization: org-client' \
  -d '{"model": "openai/gpt-4o", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]}'
```
