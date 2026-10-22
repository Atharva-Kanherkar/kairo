# 107 transcripts

LiteLLM proxy against the live Gemini API (`generativelanguage.googleapis.com`),
recorded on 2026-10-22. Three builds: `v1.106.2` (`49943cd5`, editable install from
the tag), `litellm[proxy]==1.88.2` from PyPI (the last release before
[#29325](https://github.com/BerriAI/litellm/pull/29325)), and `v1.106.2` with only
[`rig/fix.diff`](rig/fix.diff) applied ("patched"). Each build ran behind its own
[`rig/relay.py`](rig/relay.py), which writes every forwarded request and Google's
response. The relay replaced the `x-goog-api-key` value with `[REDACTED]` at capture
time; no other byte was changed. Response bodies are stored de-chunked, so a
`Transfer-Encoding: chunked` header can sit above a plain body. The client
`Authorization` header carries the throwaway local master key `sk-test-master`.
The image in every request is [`rig/code.png`](rig/code.png), which shows the text
`ZEBRA-7342`.

| Directory | What it holds |
|---|---|
| `wire/<dialect>-<model>-<build>-r<n>/` | [`rig/wire.py`](rig/wire.py): one tool-result-image turn in `chat` (`/v1/chat/completions`), `messages` (`/v1/messages`) or `responses` (`/v1/responses`). `client-request.http` and `client-response.http` are the proxy hop, `upstream/` is the forwarded Gemini exchange. Models: `g25p` gemini-2.5-pro, `g25fl` gemini-2.5-flash-lite, `g38f` gemini-3.8-flash. 81 cells; `results.json` indexes them with both status codes |
| `direct/<model>-<shape>-r<n>/` | [`rig/direct_raw.py`](rig/direct_raw.py): the same turn sent straight to Google with no gateway, the image either nested in `functionResponse.parts` or as a sibling `inlineData` part. 18 cells, `results.json` |
| `agents/claude-code/<model>-<build>-r<n>/` | [`rig/cc2.sh`](rig/cc2.sh): Claude Code 2.1.289 `-p` run that reads `code.png`, then a text-only `--resume` turn. `out.json` and `out2.json` are the two `--output-format json` results, `session.jsonl` is the session log, `answer.txt` and `ok.txt` are the files the agent wrote when it got that far. `results.txt` is the matrix summary |
| `agents/codex/<model>-<build>-r<n>/` | [`rig/cximg.sh`](rig/cximg.sh): Codex 0.159.3 `exec` asked to use `view_image` on `code.png`. `out.txt` is the full console output, `answer.txt` the file it wrote. `results.txt` is the summary |
| `rig/` | the scripts above, both proxy configs, [`reproduce.sh`](rig/reproduce.sh) (cold start; [`cold-run.txt`](rig/cold-run.txt) is its output from a fresh clone, 27/27 as expected), [`index.py`](rig/index.py) (rebuilds the indexes), `fix.diff` |

Agent runs used the unrecorded [`rig/config.yaml`](rig/config.yaml) on ports 4012
(`v1.106.2`) and 4016 (patched); the wire matrix used
[`rig/config-recorded.yaml`](rig/config-recorded.yaml) with the relay port changed
per build. In the `wire/` forwarded requests, the `NNN` in `fwd-NNN` is the relay's
own counter and has no meaning across cells.
