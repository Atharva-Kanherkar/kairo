# `GET /health` prints deployment credentials that live in `extra_headers` and `aws_session_token`

Several of our deployments authenticate through headers rather than
`api_key`: a gateway token in `extra_headers.Authorization`, a Google key in
`extra_headers.x-goog-api-key`, and so on. Some Bedrock-style deployments also
carry an `aws_session_token`.

`GET /health` runs the health checks and returns each endpoint's
`litellm_params`. It already hides `api_key`, but it returns `extra_headers`,
`headers`, and `aws_session_token` exactly as configured, so anyone who can
read the health output (dashboards, uptime monitors, support tooling) gets
working provider credentials:

```json
{"healthy_endpoints": [{"model": "openai/mockmodel",
   "extra_headers": {"Authorization": "Bearer gw-live-...", "x-goog-api-key": "AIza..."},
   "aws_session_token": "IQoJb3JpZ2luX2VjE..."}]}
```

Health output should never contain credentials, whichever field of the
deployment they are configured in. The deployments themselves must of course
keep sending these headers to the provider.
