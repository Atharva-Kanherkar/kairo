# BerriAI/litellm at 72a9e1c (litellm_internal_staging on 2026-08-26, parent of the PR #37090 merge).
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
COPY --chown=kairo litellm-72a9e1c/common.lock /tmp/common.lock
RUN python -m venv /work/.venv && /work/.venv/bin/pip install --no-cache-dir -q -r /tmp/common.lock

RUN fetch-repo https://github.com/BerriAI/litellm 72a9e1cf37e1aa8933e3dafd7e5a45a85f923705 /work/repo 20
COPY --chown=kairo litellm-72a9e1c/requirements.lock /tmp/requirements.lock
RUN /work/.venv/bin/pip install --no-cache-dir -q -r /tmp/requirements.lock \
    && install-litellm-src /work/.venv /work/repo
ENV PATH=/work/.venv/bin:$PATH VIRTUAL_ENV=/work/.venv PYTHONDONTWRITEBYTECODE=1 LITELLM_RUST=false \
    LITELLM_LOCAL_MODEL_COST_MAP=True LITELLM_TELEMETRY=False DISABLE_SCHEMA_UPDATE=True
RUN python -c "import litellm, litellm.proxy.proxy_server; print('litellm', litellm.__file__)" && litellm --help >/dev/null
WORKDIR /work/repo
LABEL kairo.env="litellm-72a9e1c"
