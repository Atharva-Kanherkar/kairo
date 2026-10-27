# Switchyard forwards the caller's `api-key` and `OpenAI-Organization` headers to the provider

Switchyard authenticates to our OpenAI / Azure OpenAI backends with credentials
from its own config. Callers are separate tenants with their own habits: some
send `api-key: ...` (Azure style), `OpenAI-Organization: org-...`, or
`OpenAI-Project: proj-...` on their requests to Switchyard.

Those headers arrive at the provider unchanged, next to the credentials
Switchyard adds. A caller's organization header makes OpenAI bill or reject
requests under a different organization (`401 mismatched_organization`), and
a caller's `api-key` can reach an Azure-style backend that honors it. Switchyard
already strips a caller's `Authorization` and `x-api-key`; these are credentials
and tenant selectors of the same kind and should stay with the backend config.
