# ENTERPILOT/GoModel at abde73ca (main on 2026-08-16, reported as 0.1.77). The
# gateway is prebuilt; rebuilding after an edit is incremental (warm Go cache).
FROM golang:1.27-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash kairo && mkdir -p /work && chown kairo:kairo /work
COPY _lib/fetch-repo.sh /usr/local/bin/fetch-repo
COPY _lib/gomodel-build.sh /usr/local/bin/gomodel-build
USER kairo
ENV GOPATH=/home/kairo/go GOCACHE=/home/kairo/.cache/go-build GOTOOLCHAIN=local GOFLAGS=-buildvcs=false GOSUMDB=off
RUN fetch-repo https://github.com/ENTERPILOT/GoModel abde73ca50384317d28e333954f605fa05e9e6ab /work/repo 50
RUN cd /work/repo && go mod download && gomodel-build \
    && go test -count=1 -run '^$' ./internal/anthropicapi/ ./internal/providers/openai/ >/dev/null \
    && test -z "$(git status --porcelain)"
ENV GOPROXY=off
WORKDIR /work/repo
LABEL kairo.env="gomodel-abde73c"
