# 004, Gemini `thought_signature`: not dropped by LiteLLM, but smuggled into the tool-call id (charset violation)

- **Upstream (as cited)**: [ollama#14567](https://github.com/ollama/ollama/issues/14567),
  [claude-code-router#1431](https://github.com/musistudio/claude-code-router/issues/1431),
  open-webui#28492, "Gemini thought_signature dropped → 400". Those are Ollama
  and CCR bugs; we tested **LiteLLM**, which behaves differently (below).
- **Tool under test**: LiteLLM 1.96.2 → `gemini/gemini-3-flash-preview`.
- **Reproduced**: 2026-08-12. Wire evidence in `transcripts/004/`.

## Cited symptom, NOT reproduced on LiteLLM

Gemini 3 returns a `thought_signature` with every function call. LiteLLM does
**preserve** it (it is not dropped):

- OpenAI-Chat route: in `tool_calls[0].provider_specific_fields.thought_signature`.
- Anthropic route: in the `tool_use` block's `provider_specific_fields.signature`
  (`transcripts/004/anthropic-turn1-response.json`).

A single-tool multi-turn replay that omits the signature still completed
correctly (`stop`/`end_turn`, right answer) on both routes, so the hard-400
seen in Ollama/CCR did not occur here for the simple case.

## What DID reproduce, a latent interop bug (Switchyard#178 family)

On the **OpenAI-Chat** route, LiteLLM smuggles the signature into the
`tool_calls[].id` itself, using a `__thought__` delimiter:

```
id = "call_154389__thought__EooCCocCARFNMg8...=" 
     length 382, contains  6× '+'  6× '/'  1× '='   (base64 payload)
```

Problems, all demonstrable from the captured bytes:

1. **Charset violation.** OpenAI ids are `[a-zA-Z0-9_-]`, max 64. This id is
   382 chars and contains `+ / =`. Any strict client or downstream translator
   that validates ids rejects it.
2. **Untranslatable to Anthropic.** Anthropic requires `^[a-zA-Z0-9_-]{1,64}$`
   for `tool_use.id`. A gateway translating this OpenAI response to Anthropic
   must mangle the id, and if it mangles (like Switchyard's non-injective
   sanitizer, #178) the smuggled signature is corrupted and lost. The two
   "preserve the signature" and "produce a legal id" goals are in direct
   conflict here.
3. **Channel confusion.** Signature integrity is being carried in an identifier
   field, so any component that regenerates or normalizes ids (common, and
   correct behavior on its own) silently destroys reasoning-state integrity.

### Third dialect, worse: the Responses bridge (`transcripts/011/`)

The same `__thought__` smuggling appears on LiteLLM's `/v1/responses` bridge,
now inside `function_call.call_id`, **832 characters**, 21× `+`, 10× `/`, 1×
`=`. `call_id` is the field the client MUST echo verbatim in
`function_call_output` to return a tool result, so this monster id is on the
critical path of every multi-turn agent loop, not just an opaque handle.
The same response also carries an empty message item
(`output_text.text: null`) next to the call, a phantom-empty-block sibling of
Defect B in issue 001.

### Anthropic route

The Anthropic route avoids the id hack (clean `call_466174`) and instead uses
`provider_specific_fields.signature`, but a standard Anthropic `tool_use`
block has no field for it, so a normal Anthropic client won't echo it back on
the next turn. Same underlying hazard, different failure surface.

## Test invariants

1. Every `tool_calls[].id` / `tool_use.id` a tool emits MUST satisfy the target
   dialect's id contract (OpenAI `[a-zA-Z0-9_-]{1,64}`; Anthropic
   `^[a-zA-Z0-9_-]{1,64}$`).
2. Provider reasoning state (`thought_signature`) MUST round-trip through a
   dedicated field, never be packed into an identifier.
3. Signature survival must not depend on the client blindly echoing an
   oversized id back.

## Repro

```
tools/litellm-env/bin/litellm --config tools/litellm-config.yaml --port 4000
curl -s localhost:4000/v1/chat/completions -H 'content-type: application/json' \
  -d @transcripts/004/turn1.json | jq -r '.choices[0].message.tool_calls[0].id' | wc -c
```
