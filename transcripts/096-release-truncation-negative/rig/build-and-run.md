# Rig: NIXL `releaseXferReq` reports success on a truncated transfer

Everything here runs on CPU. No GPU, no RDMA fabric, no provider keys.

## Pin

```
git clone --depth 1 https://github.com/ai-dynamo/nixl
cd nixl && git rev-parse HEAD
# 464c86388d2c6373ab922d852fa245d81bfb5fb6
```

## Build the POSIX plugin, no sanitizer

An ASan build of the dlopen'd plugin crashes inside NIXL's logging static
initializer (`InitializeNixlLogging`), before the harness reaches any transfer.
The byte evidence does not need ASan, so the recorded runs use a plain build.

```
meson setup build_noasan \
  -Denable_plugins=POSIX \
  -Ddisable_gds_backend=true \
  -Dsanitizer=none \
  -Dbuild_tests=false -Dbuild_examples=false \
  -Dbuild_nixl_ep=false -Dwith_trace=false
ninja -C build_noasan
```

On Ubuntu 24.04 the prerequisites are `build-essential pkg-config meson
ninja-build python3-pip git ca-certificates pybind11-dev libaio-dev libnuma-dev`.
`libaio-dev` matters: without it the POSIX backend falls back to another queue,
and the submission batch limit reproduced here is the AIO queue's.

## Compile and run

```
g++ -std=c++20 -g -O1 -o repro rig/repro_release_truncates.cpp \
  -I src/api/cpp -I src/api/cpp/.. -I src/infra \
  -Lbuild_noasan/src/core -Lbuild_noasan/src/utils/common -Lbuild_noasan/src/infra \
  -lnixl -lnixl_common -lnixl_build \
  -Wl,-rpath,$PWD/build_noasan/src/core \
  -Wl,-rpath,$PWD/build_noasan/src/utils/common \
  -Wl,-rpath,$PWD/build_noasan/src/infra \
  -Wl,-rpath,$PWD/build_noasan/src/plugins/posix

export NIXL_PLUGIN_DIR=$PWD/build_noasan/src/plugins/posix

./repro bug     /tmp/f.json 512   # release without polling to a terminal status
./repro control /tmp/f.json 512   # poll getXferStatus to SUCCESS, then release
./repro bug     /tmp/f.json 130   # descriptor sweep, see matrix/sweep.jsonl
```

One round per process, on purpose. In the bug arm the request handle is deleted
while the queued iocbs still hold it as their callback context, which is the
use-after-free already filed as nixl#1955. Any later poll would trip it, so the
harness makes no NIXL call after the release and reads the file with `pread`.

## What the harness asserts

Region `i` of the DRAM buffer is filled with byte `(i % 255) + 1`, and the
transfer is split into one 512 KiB descriptor per region, so the file itself
records exactly which descriptors landed. Nothing is inferred from return codes
alone; the bytes are compared against the expected per-region pattern.

## Confirm the queue type actually used

```
NIXL_LOG_LEVEL=INFO ./repro bug /tmp/f.json 512 2>&1 | grep "io queue type"
# POSIX backend initialized using io queue type: AIO
```

## Recorded output

`matrix/` holds one JSON record per round, and `sweep.jsonl` holds the descriptor
sweep. Both were produced by the commands above on 2026-10-08.