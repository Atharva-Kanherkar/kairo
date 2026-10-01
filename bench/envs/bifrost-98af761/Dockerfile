# maximhq/bifrost at 98af761c (dev on 2026-09-07), the parent of the PR #6888 merge.
# The HTTP gateway is built from the Go workspace (core, framework, transports,
# plugins) so edits anywhere in the checkout take effect on the next build. The
# UI is replaced by a stub page; the benchmark only drives the HTTP API.
FROM golang:1.27-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash kairo && mkdir -p /work && chown kairo:kairo /work
COPY _lib/fetch-repo.sh /usr/local/bin/fetch-repo
COPY _lib/bifrost-build.sh /usr/local/bin/bifrost-build
USER kairo
ENV GOPATH=/home/kairo/go GOCACHE=/home/kairo/.cache/go-build GOTOOLCHAIN=local GOFLAGS=-buildvcs=false \
    CGO_ENABLED=1 GOSUMDB=off
RUN fetch-repo https://github.com/maximhq/bifrost 98af761c0adab5dee4c91c7c5121034a73c10488 /work/repo 30
RUN cd /work/repo \
    && go work init ./core ./framework ./transports \
    && for d in plugins/*/; do if [ -f "$d/go.mod" ]; then go work use "$d"; fi; done \
    && printf 'go.work\ngo.work.sum\n' >> .git/info/exclude \
    && mkdir -p transports/bifrost-http/ui && printf '<!doctype html><title>bifrost</title>\n' > transports/bifrost-http/ui/index.html \
    && bifrost-build \
    && cd core && go test -count=1 -run '^$' ./providers/openai/ ./providers/anthropic/ ./providers/gemini/ \
         ./providers/utils/ ./mcp/ ./schemas/ >/dev/null \
    && cd .. && test -z "$(git status --porcelain)"
ENV GOPROXY=off GOFLAGS="-buildvcs=false"
WORKDIR /work/repo
LABEL kairo.env="bifrost-98af761"
