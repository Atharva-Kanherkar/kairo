# Reproduction kit

`reproduce.sh` starts a local case-sensitive image origin (red at `/Cat.png`,
blue at `/cat.png`), loads both URLs through Dynamo's real `ImageLoader` from
`/work/repo/components/src`, and prints the decoded pixel per request and the
paths the origin was asked for. The compiled Rust extension is replaced by an
inert stub (`/opt/dynamo-shim`); the loader path does not use it. Upstream
unit tests for the loader run with:

    python -m pytest -q components/src/dynamo/common/tests/multimodal/test_image_loader.py
