# mozilla-ai/any-llm at 3ee84f6a (2026-09-01), the parent of the merge of
# PR #1312. The SDK is installed editable, so source edits take effect at once.
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash kairo && mkdir -p /work && chown kairo:kairo /work
COPY _lib/fetch-repo.sh /usr/local/bin/fetch-repo
USER kairo
RUN fetch-repo https://github.com/mozilla-ai/any-llm 3ee84f6a3482f86d4c02c25643613809944709d1 /work/repo 300

COPY --chown=kairo any-llm-3ee84f6/requirements.lock /tmp/requirements.lock
RUN python -m venv /work/.venv \
    && /work/.venv/bin/pip install --no-cache-dir -q -r /tmp/requirements.lock \
    && /work/.venv/bin/pip install --no-cache-dir -q --no-deps -e /work/repo \
    && /work/.venv/bin/python -c "import any_llm; print('any_llm ok')"
ENV PATH=/work/.venv/bin:$PATH VIRTUAL_ENV=/work/.venv PYTHONDONTWRITEBYTECODE=1
WORKDIR /work/repo
LABEL kairo.env="any-llm-3ee84f6"
