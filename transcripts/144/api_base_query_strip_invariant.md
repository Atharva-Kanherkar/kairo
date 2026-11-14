# In-repo invariant: api_base query strings are credentials

1. `litellm/proxy/common_request_processing.py` (get_custom_headers):

```python
"x-litellm-model-api-base": (
    api_base.split("?")[0] if api_base else None
),  # don't include query params, risk of leaking sensitive info
```

2. `litellm/litellm_core_utils/secret_redaction.py` matches credentials in URLs:

```python
# Azure SAS tokens in URLs. The delimiter is a lookbehind, like the
# `key=` pattern above, so the `?` or `&` survives and the redacted URL
# stays well formed (this string is often a request line in a log).
r"(?<=[?&])sig=[A-Za-z0-9%+/=]+",
```

3. `litellm/litellm_core_utils/sensitive_data_masker.py` (`_DEFAULT_SENSITIVE_PATTERNS`)
   has no `base` segment: password, secret, key, token, auth, authorization,
   cookie, credential(s), access, private, certificate, fingerprint, tenancy.

4. `/health` (litellm/proxy/health_check.py, ADMIN_ONLY_HEALTH_DISPLAY_PARAMS)
   treats `api_base` as admin-only in the sibling disclosure surface.
