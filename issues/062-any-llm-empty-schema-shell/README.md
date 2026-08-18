# 062, any-llm silently forwards an empty JSON schema for a natural `output_format` mistake

- **Upstream**: no ticket, not yet filed. Adjacent to kairo 040/042/051 (field
  dropped entirely) but a distinct failure mode: the wire field is present,
  the schema is `{}`.
- **Tool under test**: mozilla-ai/any-llm **1.26.0** (`any-llm-sdk`).
- **Reproduced**: 2026-08-18. Capture 5/5. Client decode raises
  `JSONDecodeError` on the mock response (status 0), but the upstream body
  was already forwarded. Evidence: `transcripts/057/`.
- **Not a credential incident**: no keys in the frozen files.

## What breaks

Callers often pass the JSON-schema dict one level too shallow:

```json
{"type":"json_schema","schema":{"properties":{"city":{...}}}}
```

That is the shape nested under Anthropic's documented `output_config.format`,
not a top-level Messages API field. any-llm accepts it, forwards
`response_format.json_schema.schema: {}`, and never errors. HTTP 200 on the
wire out of the bridge, unconstrained output, no warning.

The documented shape survives intact 5/5:

```json
{"format":{"type":"json_schema","schema":{"title":"CityOut",...}}}
```

See `transcripts/057/al-output-format-control-upstream.jsonl`.

## Wire evidence

Violation — `transcripts/057/al-output-format-empty-schema-upstream.jsonl` (5/5):

```json
{"response_format":{"type":"json_schema","json_schema":{"name":"structured_output","schema":{}}}}
```

`city` / `ok` properties are gone. `json_schema_forwarded` alone would read
Conformant; the property checker catches the silent empty shell.

## Root cause

`any_llm/utils/messages_compat.py`, `_output_config_to_response_format`: when
the caller passes a bare `{type, schema}` dict, `fmt.get("schema")` is empty
because the schema sits at the top level, not under `format`.

## Test

`any_llm_wrong_output_format_shape_forwards_empty_schema` (violation) and
`any_llm_output_config_shape_keeps_schema` (control). Uses
`json_schema_property_forwarded(..., "city")`.

## Repro

```
python3 -m venv /tmp/kairo-venv
/tmp/kairo-venv/bin/pip install 'any-llm-sdk[openai]'
/tmp/kairo-venv/bin/python3 transcripts/057/hunt.py
```

Do not start a separate mock on port 9996; the hunt spawns its own.
