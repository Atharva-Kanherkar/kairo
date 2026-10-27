# 110 transcripts

LiteLLM proxy against the live OpenAI API (`api.openai.com`), recorded on
2026-10-27 (provider `Date` headers: wire 10:07:30Z to 10:08:44Z, direct
10:09:00Z to 10:09:13Z, agents 10:23:00Z to 10:24:07Z). Three builds, all run from one venv
with the build's source tree first on `PYTHONPATH` (each proxy's import path is
printed by [`rig/reproduce.sh`](rig/reproduce.sh)):

- `main`: `main` at `cfe0100e1fca613e7b1af27dd1fe4807ca4d1d1a`, which contains
  [#48534](https://github.com/BerriAI/litellm/pull/48534) (merge commit `aa33b05`)
- `pre`: `74ce6d1d66d22626fad72fbb9219d3cadf134b77`, the parent of that merge
- `fix`: `main` plus [`rig/fix.diff`](rig/fix.diff), the candidate fix this finding
  first proposed and then rejected (it restores the silent drop)

Each build ran behind its own [`rig/relay.py`](rig/relay.py), which writes every
forwarded request and OpenAI's response. The relay replaced the values of
`Authorization`, `openai-organization`, `openai-project`, `set-cookie`, `CF-Ray` and
`x-ratelimit-*` with `[REDACTED]` at capture time; no other byte was changed.
Response bodies are stored de-chunked. The client `x-api-key` is the throwaway
local master key `sk-test-master`.

| Directory | What it holds |
|---|---|
| `wire/<alias>-<plain\|stream>-<stop\|nostop>-<build>-r<n>/` | [`rig/wire.py`](rig/wire.py): one `/v1/messages` request asking for `ALPHA STOPHERE OMEGA`, with `stop_sequences: ["STOPHERE"]` (`stop`) or without it (`nostop`). `client-request.http` and `client-response.http` are the proxy hop, `upstream/` is the forwarded OpenAI exchange (empty when LiteLLM refused before calling OpenAI). Aliases: `g54m` gpt-5.4-mini, `g55` gpt-5.5, `g41m` gpt-4.1-mini. 66 cells, `results.json` indexes them |
| `direct/<chat\|responses>-<model>-<stop\|nostop>-r<n>/` | [`rig/direct_raw.py`](rig/direct_raw.py): the same prompt sent straight to OpenAI with no gateway, Chat Completions with `stop` or Responses with or without `stop`. 21 cells, `results.json` |
| `agents/langchain-react/<alias>-<build>-r<n>/` | [`rig/react_agent.py`](rig/react_agent.py): LangChain classic ReAct agent (`langchain-classic` 1.0.8, `langchain-anthropic` 1.7.6, `anthropic` 1.17.0) on `ChatAnthropic`, which sends `stop_sequences: ["\nObservation"]`. `client/` is the agent to proxy hop recorded by [`rig/tap.py`](rig/tap.py) (LF line endings), `ledger.txt` has one JSON string per tool call, `result.json` the outcome. 12 cells, `results.json` |
| `rig/` | the scripts above, [`config-recorded.yaml`](rig/config-recorded.yaml), [`reproduce.sh`](rig/reproduce.sh) (cold start; [`cold-run.txt`](rig/cold-run.txt) is its output from a fresh fetch), [`index.py`](rig/index.py) (rebuilds the three indexes), `fix.diff` |

In `upstream/` the `NNN` in `fwd-NNN` is the relay's own counter and has no
meaning across cells.
