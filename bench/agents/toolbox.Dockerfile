# Agent toolbox. Every coding-agent CLI is installed under /opt/kairo-agents so
# the harness can mount it read-only into any Debian-based task environment
# without rebuilding that environment. Versions are pinned in agents.toml and
# passed in as build arguments.
FROM node:22-bookworm-slim

ARG CLAUDE_CODE_VERSION
ARG CODEX_VERSION
ARG GEMINI_CLI_VERSION
ARG OPENCODE_VERSION
ARG MINI_SWE_AGENT_VERSION

ENV PREFIX=/opt/kairo-agents \
    UV_PYTHON_INSTALL_DIR=/opt/kairo-agents/python \
    UV_TOOL_DIR=/opt/kairo-agents/uv-tools \
    UV_TOOL_BIN_DIR=/opt/kairo-agents/bin \
    UV_NO_CACHE=1 \
    PATH=/opt/kairo-agents/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/kairo-agents/bin && cp /usr/local/bin/node /opt/kairo-agents/bin/node \
    && npm install -g --prefix /opt/kairo-agents --no-fund --no-audit \
        "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
        "@openai/codex@${CODEX_VERSION}" \
        "@google/gemini-cli@${GEMINI_CLI_VERSION}" \
        "opencode-ai@${OPENCODE_VERSION}" \
    && npm cache clean --force

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /opt/kairo-agents/bin/uv
RUN uv python install 3.12 \
    && uv tool install --python 3.12 "mini-swe-agent==${MINI_SWE_AGENT_VERSION}"

RUN for c in node claude codex gemini opencode mini; do command -v "$c" >/dev/null || { echo "missing $c"; exit 1; }; done
