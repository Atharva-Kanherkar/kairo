Environment: CPU-only. The Python components under /work/repo/components/src
are on PYTHONPATH; the compiled Rust extension (`dynamo._core`) is replaced by
an inert stub in /opt/dynamo-shim. Multimodal unit tests:
`python -m pytest -q components/src/dynamo/common/tests/multimodal`.
