# ogx-ai/ogx v1.4.0 (051a8a0d). Runtime dependencies come from the project's own
# uv.lock (--frozen); only pytest is added for the unit tests.
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates procps build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash kairo && mkdir -p /work && chown kairo:kairo /work
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
COPY _lib/fetch-repo.sh /usr/local/bin/fetch-repo
USER kairo
RUN fetch-repo https://github.com/ogx-ai/ogx 051a8a0dff57d8f7a40aced27cfcf8b629212b36 /work/repo 200

ENV UV_PROJECT_ENVIRONMENT=/work/.venv UV_LINK_MODE=copy UV_NO_CACHE=1 UV_PYTHON_DOWNLOADS=never
RUN cd /work/repo && uv sync --frozen --no-dev --python /usr/local/bin/python3.12 \
    && uv pip install --python /work/.venv/bin/python "pytest==8.4.2" "pytest-asyncio==1.2.0" "pytest-timeout==2.4.0" \
    && /work/.venv/bin/ogx --help >/dev/null && echo "ogx ok"
ENV PATH=/work/.venv/bin:$PATH VIRTUAL_ENV=/work/.venv PYTHONDONTWRITEBYTECODE=1 UV_OFFLINE=1
WORKDIR /work/repo
LABEL kairo.env="ogx-051a8a0"
