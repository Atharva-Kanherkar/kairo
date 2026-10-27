# A virtual-key caller receives our provider's secret header

We run Bifrost with governance on (`enforce_auth_on_inference: true`) and give
teams provider-scoped virtual keys. They are not allowed to read provider
configuration: `GET /api/providers` with a virtual key is refused. One custom
OpenAI-compatible provider authenticates with a static header that we set in
`network_config.extra_headers`:

```json
"network_config": {"base_url": "https://llm.internal.example", "extra_headers": {"X-Provider-Secret": "..."}}
```

That upstream echoes request headers back on its responses. A team calling
`POST /v1/responses` with only its virtual key gets our `X-Provider-Secret`
value back, both as an HTTP response header and inside the JSON response's
provider-header metadata. Bifrost already strips `Authorization` from provider
responses before re-serving them, and it already has a notion of which header
names carry credentials, but this custom credential header goes straight
through to the caller.
