# NVIDIA-NeMo/Switchyard at e9767d64 (main on 2026-08-13), the parent of the
# PR #389 merge. switchyard-server is prebuilt in the dev profile without debug
# info, so a rebuild after an edit only recompiles the touched crates.
FROM rust:1.96.1-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates procps \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash kairo && mkdir -p /work && chown kairo:kairo /work \
    && chmod -R a+w /usr/local/cargo /usr/local/rustup
COPY _lib/fetch-repo.sh /usr/local/bin/fetch-repo
COPY _lib/switchyard-build.sh /usr/local/bin/switchyard-build
USER kairo
ENV CARGO_HOME=/usr/local/cargo RUSTUP_HOME=/usr/local/rustup RUSTUP_TOOLCHAIN=1.96.1 \
    CARGO_PROFILE_DEV_DEBUG=0 CARGO_INCREMENTAL=1 CARGO_TERM_COLOR=never
RUN fetch-repo https://github.com/NVIDIA-NeMo/Switchyard e9767d642461af63e7f11dc74a85f1e9f56c1e78 /work/repo 50
RUN cd /work/repo && cargo fetch --locked && switchyard-build \
    && cargo test --locked --no-run -p switchyard-translation -p switchyard-llm-client >/dev/null \
    && test -z "$(git status --porcelain)"
ENV CARGO_NET_OFFLINE=true
WORKDIR /work/repo
LABEL kairo.env="switchyard-e9767d6"
