# 010, Switchyard #369 and #242 independently verified (offline rig)

- **Upstream**: [Switchyard#369](https://github.com/NVIDIA-NeMo/Switchyard/issues/369)
  (content_filter silently becomes end_turn) and
  [Switchyard#242](https://github.com/NVIDIA-NeMo/Switchyard/issues/242)
  (mixed reasoning/content chunks reordered for Anthropic clients). Both OPEN;
  both now confirmed with recorded bytes.
- **Tool under test**: Switchyard switchyard-server 0.2.0 (commit 2bef154 build).
- **Method**: offline capture rig, `tools/mock_upstream.py` serving canned
  OpenAI responses. Zero keys, deterministic.
- **Reproduced**: 2026-08-12. Evidence in `transcripts/015/`.

## Finding 1 (#369 confirmed): moderation sign