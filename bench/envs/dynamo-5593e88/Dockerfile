# ai-dynamo/dynamo at 5593e885 (main on 2026-09-19). CPU-only: the compiled
# Rust extension is replaced by an inert stub (/opt/dynamo-shim); the Python
# components under components/src run from the checkout directly.
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash kairo && mkdir -p /work && chown kairo:kairo /work
COPY _lib/fetch-repo.sh /usr/local/bin/fetch-repo
COPY dynamo-5593e88/shim /opt/dynamo-shim
USER kairo
RUN fetch-repo https://github.com/ai-dynamo/dynamo 5593e8857c508bebce7259e107a85c57cd142fd8 /work/repo 50

COPY --chown=kairo dynamo-5593e88/requirements.lock /tmp/requirements.lock
RUN python -m venv /work/.venv \
    && /work/.venv/bin/pip install --no-cache-dir -q --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements.lock
ENV PATH=/work/.venv/bin:$PATH VIRTUAL_ENV=/work/.venv PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/work/repo/components/src:/work/repo/lib/bindings/python/src:/opt/dynamo-shim
RUN cd /work/repo && python -c "from dynamo.common.multimodal.image_loader import ImageLoader; print('ImageLoader ok')"
WORKDIR /work/repo
LABEL kairo.env="dynamo-5593e88"
