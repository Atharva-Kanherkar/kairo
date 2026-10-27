Environment: Python 3.12 virtualenv at /work/.venv. LiteLLM runs from the
/work/repo source tree (it is on sys.path), so edits take effect on the next
process start. The optional Rust bridge is not built; LITELLM_RUST=false keeps
every route on its Python path. Start the proxy with
`litellm --config <file> --port 4000` (current LiteLLM requires a master key:
set LITELLM_MASTER_KEY). Unit tests: `python -m pytest -q tests/test_litellm/...`.
