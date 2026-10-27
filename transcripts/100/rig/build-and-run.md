# 100 rig: build and run

Everything here runs locally against the live Anthropic API. The relays in front
of and behind Switchyard record every exchange with credentials, tenant headers,
and the Claude Code device id redacted inline.

## Prerequisites

- Rust 1.99 (Switchyard pins it in `rust-toolchain.toml`).
- Python 3.9 or later, standard library only.
- Claude Code 2.1.289 on `PATH` as `claude`, only for the `--claude` cases.
- `ANTHROPIC_API_KEY` in the environment, or a `KEY=VALUE` file passed with
  `--env-file`. The key is never printed or written. Claude Code itself receives
  a placeholder key, because the Switchyard client uses `api_key_env`.
- Ports 9000, 9100, and 9101 free.

## Build the two Switchyard revisions

```bash
git clone https://github.com/NVIDIA-NeMo/Switchyard sy-main
cd sy-main && git checkout ab7d26443899239632e97d9d9d8aedaa58c96b99
cargo build --release -p switchyard-server
cd ..
git clone https://github.com/NVIDIA-NeMo/Switchyard sy-v030
cd sy-v030 && git checkout v0.3.0   # 336196f6fbfc97ddc71c1700f6092e564e9f23c2
cargo build --release -p switchyard-server
```

If Homebrew's `rustc` shadows rustup, set
`RUSTC=~/.rustup/toolchains/1.99-<host>/bin/rustc` for the build. If
`CARGO_TARGET_DIR` is set globally, point it at the checkout's own `target`
directory so the two revisions do not share artifacts.

## Run

```bash
python3 transcripts/100/rig/reproduce.py \
  --switchyard-bin /path/to/sy-main/target/release/switchyard-server \
  --switchyard-src /path/to/sy-main \
  --output /tmp/kairo-100-main --runs 5 --claude

python3 transcripts/100/rig/reproduce.py \
  --switchyard-bin /path/to/sy-v030/target/release/switchyard-server \
  --switchyard-src /path/to/sy-v030 \
  --output /tmp/kairo-100-v030 --runs 5
```

Expected second line of output on main:

```text
{"direct_server_tool_ok": 5, "direct_reencoded_tool_400": 5, "plan_forwarded_custom_tool_400": 5, "pass_forwarded_server_tool_ok": 5, "runs_per_case": 5, "claude_plan_websearch_failed": 5, "claude_pass_websearch_ok": 5}
```

The script exits non-zero if any case does not match, or if the key appears in
any output file.

## Layout of an output directory

```text
results.json                 identity (commit, clean tree, version), summary, per-run records
switchyard-log.txt           server log (.txt because the repository ignores *.log)
direct/<shape>-NN/upstream/  Anthropic called with no gateway
minimal/<route>-NN/client/   the client hop, client <-> Switchyard
minimal/<route>-NN/upstream/ the forwarded hop, Switchyard <-> Anthropic
claude/<route>-NN/...        same two hops for a Claude Code run, plus claude-stdout.json
```

The committed `capture/main-ab7d2644/claude` runs 02 to 05 keep only the
WebSearch exchange on each hop and Claude Code's output. Their other exchanges
are Claude Code's main loop, which repeats the full system prompt and tool list
and is kept complete for run 01 of each route. Nothing inside a kept file was
edited after recording. The two committed server logs were written as
`switchyard.log` by the run and renamed to `switchyard-log.txt`; their content
is unchanged.
