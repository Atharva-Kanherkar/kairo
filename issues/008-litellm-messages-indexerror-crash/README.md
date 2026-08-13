# 008, LiteLLM /v1/messages adapter: unhandled IndexError → HTTP 500 "list index out of range"

- **Upstream family**: [litellm#17904](https://github.com/BerriAI/litellm/issues/17904)
  (tool names >64 chars through the Anthropic adapter). This is the **crash**
  variant.
- **Tool under test**: LiteLLM 1.96.2, Anthropic `/v1/messages` ingress →
  `gemini/gemini-2.5-flash` backend.
- **Reproduced**: 2026-08-12, 5/5 with a >64-char tool name.
  Evidence: `transcripts/probe/bug008-clientbody.json`.

## What breaks

An Anthropic `/v1/messages` request that declares a tool whose `name` is longer
than 64 characters (legal in Anthropic; the limit is OpenAI's) makes LiteLLM
return:

```json
{"error":{"message":"list index out of range","type":"None","param":"None","code":"500"}}
```

A raw Python `IndexError` reaches the client as an opaque HTTP 500. No hint that
the tool name is the cause.

## Root cause (in LiteLLM source)

`translate_openai_response_to_anthropic`
(`litellm/llms/anthropic/experimental_pass_through/adapters/transformation.py:1326`):

```python
anthropic_finish_reason = self._translate_openai_finish_reason_to_anthropic(
    openai_finish_reason=response.choices[0].finish_reason  # <-- choices is []
)
```

`response.choices[0]` is indexed with no empty-guard. When the upstream
translation yields an empty `choices` list, this throws `IndexError`. The
long-tool-name path reaches this state deterministically (the name is truncated
to satisfy OpenAI's 64-char cap via `tool_name_mapping`, and the restore path
ends with empty choices). Full traceback observed:

```
proxy/anthropic_endpoints/endpoints.py:95  anthropic_response
 .../adapters/handler.py:602               async_anthropic_messages_handler
 .../adapters/transformation.py:206        translate_completion_output_params
 .../adapters/transformation.py:1326       translate_openai_response_to_anthropic
IndexError: list index out of range
```

## Test invariants

1. A tool name legal in the inbound dialect but too long for the target MUST be
   handled (mapped/truncated with restoration) or rejected with a clear 4xx -
   never crash.
2. `response.choices[0]` in any translation path MUST be guarded; an empty
   choices list must produce a valid (possibly empty) message or a typed error,
   not an unhandled `IndexError`.

## Repro

```
tools/litellm-env/bin/litellm --config tools/litellm-config.yaml --port 4000  # gemini-flash route
NAME=get_weather_$(python3 -c "print('x'*60)")
curl -s localhost:4000/v1/messages -H 'content-type: application/json' -H 'anthropic-version: 2023-06-01' \
  -d "{\"model\":\"gemini-flash\",\"max_tokens\":80,\"tools\":[{\"name\":\"$NAME\",\"description\":\"w\",\"input_schema\":{\"type\":\"object\",\"properties\":{\"city\":{\"type\":\"string\"}},\"required\":[\"city\"]}}],\"tool_choice\":{\"type\":\"any\"},\"messages\":[{\"role\":\"user\",\"content\":\"weather in Paris\"}]}"
# -> {"error":{"message":"list index out of range","code":"500"}}
```
