# A 502 from Switchyard contains our Gemini API key

Some providers authenticate with a query-string key, so one llm_client is
configured as:

```toml
[llm_clients.gemini]
format = "openai_chat"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai?key=AIza..."
```

When that upstream is unreachable (DNS failure, connection refused, TLS
error), the caller gets HTTP 502 with a JSON error whose `message` includes the
full request URL, key included:

```json
{"error": {"message": "error sending request for url (https://.../openai/chat/completions?key=AIza...)", "type": "upstream_error"}}
```

Callers must never see provider credentials. The error should still say that
the upstream could not be reached, without the URL's secret parts.
