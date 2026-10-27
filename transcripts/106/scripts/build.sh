set -euo pipefail
apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq protobuf-compiler clang libclang-dev pkg-config libssl-dev cmake git >/dev/null
[ -x $HOME/.cargo/bin/cargo ] || curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal >/dev/null
. $HOME/.cargo/env
export PATH=$HOME/.local/bin:$PATH
. /work/venv/bin/activate
uv pip install -q maturin patchelf
cd /work
[ -d dynamo ] || GIT_LFS_SKIP_SMUDGE=1 git clone -q --filter=blob:none https://github.com/ai-dynamo/dynamo.git
cd dynamo && git fetch -q origin main && git checkout -q --detach ${REV:-origin/main}
echo "REV $(git rev-parse HEAD) $(git log -1 --format=%cd)"
cd lib/bindings/python
export CARGO_PROFILE_DEV_DEBUG=0 CARGO_BUILD_JOBS=90
maturin build --out /work/wheels/main
git -C /work/dynamo rev-parse HEAD > /work/wheels/main/REVISION
ls -la /work/wheels/main
echo BUILD_DONE
