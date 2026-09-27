# 088 independent review evidence

Captured 2026-09-27 by the independent reviewer, following
`.github/agents/kairo-reproduction-reviewer.agent.md`. Nothing here was produced
by the author's rig runs. Provider IDs, fingerprints, and `obfuscation` values in
live captures are redacted inline as `[REDACTED]`. No credential appears in any
file; the live script reads `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` from the
environment.

## Targets

- agentgateway `v1.5.0`, darwin-arm64 release binary, sha256
  `da432d35bd696da0564f7b2b6bbc783542b6b9c616d6c0c4d4c3daef9dfa11a1`
  (`git_revision fe673247`).
- agentgateway `v1.6.0-alpha.2`, darwin-arm64 release binary, sha256
  `6f741d16a15296a48f4794d6005c6f9b726f21fc4382f209ed5342cfbc84e8f6`
  (`git_revision 02110e2a`). Both checksums match the published `.sha256` files.
- openai-python 2.48.0 (CPython 3.9.6) and 3.19.2 (CPython 3.12), the current
  release. langchain-openai 1.6.6. openai-node 7.23.0.

## Files

| File | What it shows |
|---|---|
| `reproduce-v1.5.0-openai-2.48.0.txt` | `consumer/reproduce.sh` run cold by the reviewer, exit 0 |
| `reproduce-v1.5.0-openai-3.19.2.txt` | same script on the current SDK, exit 0, 8 of 8 raise |
| `reproduce-v1.6.0-alpha.2-openai-3.19.2.txt` | same script on the newest pre-release binary, exit 0 |
| `raw-capture-check.txt`, `gateway-raw-v1.5.0.sse` | 5 of 5 raw gateway captures byte-identical to `../observed.sse` apart from `created` |
| `forwarded-request-v1.5.0.json` | the Anthropic request the gateway forwarded; no `tool_choice` is added |
| `live-anthropic-wire.txt`, `live-anthropic-{1..5}.sse` | live `claude-haiku-4-5` through the `llm-basic` layout in `live-agentgateway-config.yaml`: `"type":null` on every continuation delta, 5 of 5 |
| `live-anthropic-sdk.txt` | the same live route through `client.chat.completions.stream()`: `AssertionError` 3 of 3 on 2.48.0 and 3 of 3 on 3.19.2 |
| `live-openai-direct.txt`, `live-openai-direct-{1..3}.sse` | control, OpenAI direct with the same tool and prompt: continuation deltas carry only `index` and `function`, 3 of 3 |
| `langchain-outcomes.txt` | LangChain `ChatOpenAI` with tools, streaming, and `response_format` raises 3 of 3 on the conversion route; without `response_format`, and on the passthrough route, it completes |
| `openai-node-outcomes.txt` | openai-node's accumulating helper tolerates the null tag on the same bytes |

## Reproduce

```bash
# no credential
transcripts/088/review/run-keyless.sh /path/to/agentgateway-v1.5.0 /path/to/python-with-openai-3.19.2-and-langchain-openai-1.6.6

# live; both keys must already be exported
transcripts/088/review/run-live.sh /path/to/agentgateway-v1.5.0 /path/to/python-openai-2.48.0 /path/to/python-openai-3.19.2
```
