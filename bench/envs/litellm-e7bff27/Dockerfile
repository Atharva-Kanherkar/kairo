# BerriAI/litellm at e7bff27 (main on 2026-09-21, parent of the PR #40121 merge).
# Layer order matters: every LiteLLM environment installs the same common.lock
# first, so Docker stores those dependencies once. The per-commit
# requirements.lock (exported from that commit's own uv.lock) then adjusts the
# few pins that differ. The checkout runs from source (see install-litellm-src.sh).
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates procps libsndfile1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash kairo && mkdir -p /work && chown kairo:kairo /work
COPY _lib/fetch-repo.sh /usr/local/bin/fetch-repo
COPY _lib/install-litellm-src.sh /usr/local/bin/install-litellm-src
USER kairo
COPY --chown=kairo litellm-e7bff27/common.lock /tmp/common.lock
RUN python -m venv /work/.venv && /work/.venv/bin/pip install --no-cache-dir -q -r /tmp/common.lock

RUN fetch-repo https://github.com/BerriAI/litellm e7bff277a6f93ec4a26850d05ce2f6c2842c1512 /work/repo 20
COPY --chown=kairo litellm-e7bff27/requirements.lock /tmp/requirements.lock
RUN /work/.venv/bin/pip install --no-cache-dir -q -r /tmp/requirements.lock \
    && install-litellm-src /work/.venv /work/repo
ENV PATH=/work/.venv/bin:$PATH VIRTUAL_ENV=/work/.venv PYTHONDONTWRITEBYTECODE=1 LITELLM_RUST=false \
    LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_TELEMETRY=False DISABLE_SCHEMA_UPDATE=True
RUN python -c "import litellm, litellm.proxy.proxy_server; print('litellm', litellm.__file__)" && litellm --help >/dev/null
WORKDIR /work/repo
LABEL kairo.env="litellm-e7bff27"
