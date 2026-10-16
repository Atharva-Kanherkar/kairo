# Building and running the 103 rig

## Builds

Both binaries were built on macOS arm64 with Go `go1.27.2` (`GOTOOLCHAIN=go1.27.2+auto`)
from a full, non-sparse checkout of `maximhq/bifrost`.

`dev` at `6c0f795da21130ca24dcf23f267d5edcb0cbcea1`, with every module taken from the
same checkout:

```text
git checkout 6c0f795da21130ca24dcf23f267d5edcb0cbcea1
make setup-workspace
mkdir -p transports/bifrost-http/ui && echo '<html></html>' > transports/bifrost-http/ui/index.html
cd transports && go build -o /tmp/bifrost-dev ./bifrost-http
```

`transports/v2.3.0` at `9237ec06672406e437b3cb33fc793ffd0403b3ae`, with the published
modules the release pins (core `v1.12.0`), not the workspace:

```text
git checkout transports/v2.3.0
mkdir -p transports/bifrost-http/ui && echo '<html></html>' > transports/bifrost-http/ui/index.html
cd transports && GOWORK=off go build -o /tmp/bifrost-v230 ./bifrost-http
```

The UI placeholder only satisfies `//go:embed all:ui`; the gateway API does not use it.
Source builds print `v1.0.0` in the startup banner because the release pipeline injects
the version. Identify a binary with `go version -m`:

| Binary | `vcs.revision` | `vcs.modified` | core | SHA-256 (this machine, no `-trimpath`) |
|---|---|---|---|---|
| dev | `6c0f795da21130ca24dcf23f267d5edcb0cbcea1` | `false` | `(devel)`, same checkout | `3a146f3b58fd6291c192a74353e8ed30dae0dde4edfde9d106925b53ab258b32` |
| v2.3.0 | `9237ec06672406e437b3cb33fc793ffd0403b3ae` | `false` | `v1.12.0` `h1:z2piBwOUHi60FnUIwWmLa5Rgnn6HIoDVEATBH6mzFM4=` | `d48515f9758c435606f30725317ff8b49f30284511031b2c697c710eb7347e0d` |

The hashes include local paths, so another machine will get different ones. The
revision and module lines are what to compare.

The official `maximhq/bifrost:v2.3.0` image was also tried. From Docker Desktop it could
not reach a relay on the host (`provider_connection_failed`), so the native build of the
same tag was used instead.

## Running

`reproduce.py` starts each binary on port 8094 with `config.json`, which configures one
`openai` key read from `OPENAI_API_KEY` and points the provider at a recording relay on
port 9940. A second relay on port 9941 sits in front of the gateway for clients. Both
relays write raw HTTP per case and redact credentials, OpenAI organization and project
ids, request and trace ids, rate-limit values and the Claude Code device id, marking each
redaction inline. The script fails if the key value appears in any output file.

```text
python3 transcripts/103/rig/reproduce.py \
  --build dev-6c0f795d=/tmp/bifrost-dev --build v2.3.0=/tmp/bifrost-v230 \
  --output /tmp/kairo-103 --runs 5 --extra-model openai/gpt-5.5 \
  --claude dev-6c0f795d --env-file .env
```

`--claude NAME` needs `claude` on `PATH`. Each Claude Code run uses a fresh
`CLAUDE_CONFIG_DIR` and working directory, `ANTHROPIC_BASE_URL` set to the client relay,
a dummy `ANTHROPIC_API_KEY` (the gateway runs without auth), `--strict-mcp-config` with
`mcp_server.py`, and `--allowedTools mcp__orders__lookup_order`. The MCP server appends
every `tools/call` to `ledger.jsonl` in the case directory.

The recorded run used Python 3.9.6, Claude Code 2.1.289, and live OpenAI on 2026-10-16
from 06:24Z.
