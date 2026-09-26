"""Thin wrappers over the docker CLI.

Secrets are passed as ``-e NAME`` with the value in the docker client's own
environment, so they never appear in argv, ``ps`` output, or logs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tarfile
import io
from dataclasses import dataclass
from pathlib import Path

from kairo_bench.config import ENVS_DIR

LABEL = "kairo-bench"


class DockerError(RuntimeError):
    pass


def _run(args: list[str], *, check: bool = True, env: dict | None = None, **kw) -> subprocess.CompletedProcess:
    proc = subprocess.run(["docker", *args], env=env, **kw)
    if check and proc.returncode != 0:
        err = proc.stderr if isinstance(proc.stderr, str) else ""
        raise DockerError(f"docker {' '.join(args[:3])} failed ({proc.returncode}): {err[-2000:]}")
    return proc


def available() -> tuple[bool, str]:
    try:
        proc = _run(["info", "--format", "{{.ServerVersion}}"], check=False, capture_output=True, text=True)
    except FileNotFoundError:
        return False, "docker CLI not found"
    if proc.returncode != 0:
        return False, proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "docker daemon unreachable"
    return True, proc.stdout.strip()


def image_exists(tag: str) -> bool:
    return _run(["image", "inspect", tag], check=False, capture_output=True).returncode == 0


def image_id(tag: str) -> str:
    proc = _run(["image", "inspect", "--format", "{{.Id}}", tag], capture_output=True, text=True)
    return proc.stdout.strip()


# Files in an environment directory that are documentation, not build inputs.
NON_BUILD_FILES = {"agent-notes.md", "README.md"}


def tree_hash(paths: list[Path], extra: str = "") -> str:
    h = hashlib.sha256(extra.encode())
    for root in paths:
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for f in files:
            if "__pycache__" in f.parts or f.name in NON_BUILD_FILES:
                continue
            h.update(str(f.relative_to(root.parent)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def env_tag(env: str) -> str:
    """Content-addressed tag: the env directory plus the shared _lib files its Dockerfile copies."""
    env_dir = ENVS_DIR / env
    dockerfile = env_dir / "Dockerfile"
    if not dockerfile.is_file():
        raise DockerError(f"environment {env!r} has no envs/{env}/Dockerfile")
    shared = sorted({ENVS_DIR / m for m in re.findall(r"\b(_lib/[\w.-]+)", dockerfile.read_text())})
    return f"kairo-bench/env-{env}:{tree_hash([env_dir, *[p for p in shared if p.exists()]])}"


def build_env(env: str, *, log_path: Path | None = None, quiet: bool = False) -> str:
    tag = env_tag(env)
    if image_exists(tag):
        return tag
    args = ["build", "--label", LABEL, "-t", tag, "-f", str(ENVS_DIR / env / "Dockerfile"), str(ENVS_DIR)]
    _stream(args, log_path, quiet, f"building {tag}")
    return tag


def build_image(tag: str, dockerfile: Path, context: Path, build_args: dict[str, str], *,
                log_path: Path | None = None, quiet: bool = False) -> str:
    if image_exists(tag):
        return tag
    args = ["build", "--label", LABEL, "-t", tag, "-f", str(dockerfile)]
    for key, value in build_args.items():
        args += ["--build-arg", f"{key}={value}"]
    args.append(str(context))
    _stream(args, log_path, quiet, f"building {tag}")
    return tag


def _stream(args: list[str], log_path: Path | None, quiet: bool, what: str) -> None:
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w") as fh:
            proc = subprocess.run(["docker", *args], stdout=fh, stderr=subprocess.STDOUT)
    elif quiet:
        proc = subprocess.run(["docker", *args], capture_output=True, text=True)
    else:
        proc = subprocess.run(["docker", *args])
    if proc.returncode != 0:
        hint = f" (log: {log_path})" if log_path else ""
        tail = getattr(proc, "stdout", "") or ""
        raise DockerError(f"{what} failed{hint}\n{tail[-3000:] if isinstance(tail, str) else ''}")


def ensure_volume_from_image(volume: str, image: str, mount: str) -> None:
    """Populate a named volume from an image path (docker copies on first mount)."""
    proc = _run(["volume", "inspect", volume], check=False, capture_output=True)
    if proc.returncode == 0:
        return
    _run(["volume", "create", "--label", LABEL, volume], capture_output=True, text=True)
    _run(["run", "--rm", "--label", LABEL, "-v", f"{volume}:{mount}", image, "true"], capture_output=True, text=True)


def ensure_network(name: str, *, internal: bool) -> None:
    if _run(["network", "inspect", name], check=False, capture_output=True).returncode == 0:
        return
    args = ["network", "create", "--label", LABEL]
    if internal:
        args.append("--internal")
    _run([*args, name], capture_output=True, text=True)


@dataclass
class Container:
    name: str

    def exec(self, command: str, *, user: str = "kairo", env_names: list[str] | None = None,
             env: dict[str, str] | None = None, workdir: str | None = None, timeout: int | None = None,
             stdout=None, stderr=None, secrets: dict[str, str] | None = None) -> tuple[int, bool]:
        """Run ``bash -c command`` (the image's own PATH applies). Returns (exit code, timed_out)."""
        args = ["exec", "-u", user]
        if workdir:
            args += ["-w", workdir]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        for key in env_names or []:
            args += ["-e", key]
        args += [self.name, "bash", "-c", command]
        child_env = dict(os.environ)
        child_env.update(secrets or {})
        try:
            proc = subprocess.run(["docker", *args], env=child_env, stdout=stdout, stderr=stderr,
                                  timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            # Kill everything the exec started; the container itself is removed by the caller.
            _run(["exec", "-u", "root", self.name, "bash", "-c", "pkill -KILL -u kairo || true"],
                 check=False, capture_output=True)
            return 124, True
        return proc.returncode, False

    def output(self, command: str, *, user: str = "kairo", timeout: int = 300) -> tuple[int, str]:
        proc = subprocess.run(["docker", "exec", "-u", user, self.name, "bash", "-c", command],
                              capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return proc.returncode, proc.stdout + proc.stderr

    def copy_in(self, src: Path, dest: str, *, owner: str = "1000:1000") -> None:
        """Copy a file or a directory's contents to ``dest`` inside the container."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            if src.is_dir():
                for path in sorted(src.rglob("*")):
                    if "__pycache__" in path.parts:
                        continue
                    tar.add(path, arcname=str(path.relative_to(src)), recursive=False)
            else:
                tar.add(src, arcname=src.name)
        self.output(f"mkdir -p {dest}", user="root")
        proc = subprocess.run(["docker", "cp", "-", f"{self.name}:{dest}"], input=buf.getvalue(),
                              capture_output=True)
        if proc.returncode != 0:
            raise DockerError(f"docker cp into {self.name}:{dest} failed: {proc.stderr.decode()[-500:]}")
        self.output(f"chown -R {owner} {dest}", user="root")

    def write_file(self, dest: str, content: str | bytes, *, mode: int = 0o644) -> None:
        data = content.encode() if isinstance(content, str) else content
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name=Path(dest).name)
            info.size = len(data)
            info.mode = mode
            info.uid = info.gid = 1000
            tar.addfile(info, io.BytesIO(data))
        parent = str(Path(dest).parent)
        self.output(f"mkdir -p {parent} && chown 1000:1000 {parent}", user="root")
        proc = subprocess.run(["docker", "cp", "-", f"{self.name}:{parent}"], input=buf.getvalue(),
                              capture_output=True)
        if proc.returncode != 0:
            raise DockerError(f"write {dest} failed: {proc.stderr.decode()[-500:]}")

    def read_file(self, path: str) -> bytes | None:
        proc = subprocess.run(["docker", "exec", "-u", "root", self.name, "cat", path], capture_output=True)
        return proc.stdout if proc.returncode == 0 else None

    def copy_out(self, src: str, dest: Path) -> bool:
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["docker", "cp", f"{self.name}:{src}", str(dest)], capture_output=True)
        return proc.returncode == 0

    def remove(self) -> None:
        _run(["rm", "-f", "-v", self.name], check=False, capture_output=True)


def start(image: str, name: str, *, network: str, mounts: list[str] | None = None,
          env: dict[str, str] | None = None, cpus: float | None = None, memory: str | None = None,
          labels: dict[str, str] | None = None) -> Container:
    args = ["run", "-d", "--name", name, "--hostname", "kairo", "--label", LABEL,
            "--network", network, "--entrypoint", "sleep", "--init"]
    for key, value in (labels or {}).items():
        args += ["--label", f"{key}={value}"]
    for mount in mounts or []:
        args += ["-v", mount]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    if cpus:
        args += ["--cpus", str(cpus)]
    if memory:
        args += ["--memory", memory]
    args += [image, "infinity"]
    _run(["rm", "-f", "-v", name], check=False, capture_output=True)
    _run(args, capture_output=True, text=True)
    return Container(name)


def cleanup(prefix: str) -> int:
    proc = _run(["ps", "-aq", "--filter", f"label={LABEL}", "--filter", f"name={prefix}"],
                capture_output=True, text=True)
    ids = proc.stdout.split()
    if ids:
        _run(["rm", "-f", "-v", *ids], check=False, capture_output=True)
    return len(ids)


def inspect_json(obj: str) -> dict:
    proc = _run(["inspect", obj], capture_output=True, text=True)
    return json.loads(proc.stdout)[0]
