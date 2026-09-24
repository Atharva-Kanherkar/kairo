"""Run agents on tasks and grade them.

One job = (agent, task, trial). A job starts a container from the task's
environment image, mounts the agent toolbox read-only, copies the public kit
and the prompt in, runs the agent as the unprivileged ``kairo`` user, extracts
``git diff`` against the base commit plus the agent's verdict, removes the
container, and grades the patch in a fresh offline container.
"""

from __future__ import annotations

import json
import os
import tarfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from kairo_bench import docker, grader
from kairo_bench.agents import AgentSpec, invocation, parse_usage, toolbox_versions
from kairo_bench.config import BENCH_ROOT
from kairo_bench.paths import is_test_path
from kairo_bench.tasks import Task

TOOLBOX_DOCKERFILE = BENCH_ROOT / "agents" / "toolbox.Dockerfile"
AGENT_NETWORK = "kairo-bench-agents"
EGRESS_IMAGE = "python:3.12-slim-bookworm"
MAX_PATCH_BYTES = 5 * 1024 * 1024

_print_lock = threading.Lock()


def say(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


# ---------------------------------------------------------------- toolbox

def toolbox_tag() -> str:
    versions = toolbox_versions()
    digest = docker.tree_hash([TOOLBOX_DOCKERFILE], json.dumps(versions, sort_keys=True))
    return f"kairo-bench/agents:{digest}"


def ensure_toolbox(log_dir: Path) -> str:
    """Build the toolbox image and a named volume holding /opt/kairo-agents."""
    v = toolbox_versions()
    tag = toolbox_tag()
    docker.build_image(tag, TOOLBOX_DOCKERFILE, TOOLBOX_DOCKERFILE.parent, {
        "CLAUDE_CODE_VERSION": v["claude_code"],
        "CODEX_VERSION": v["codex"],
        "GEMINI_CLI_VERSION": v["gemini_cli"],
        "OPENCODE_VERSION": v["opencode"],
        "MINI_SWE_AGENT_VERSION": v["mini_swe_agent"],
    }, log_path=log_dir / "build-toolbox.log")
    volume = "kairo-bench-toolbox-" + tag.rsplit(":", 1)[1]
    docker.ensure_volume_from_image(volume, tag, "/opt/kairo-agents")
    return volume


# ---------------------------------------------------------------- egress

@dataclass
class Egress:
    mode: str  # "allowlist" | "open"
    network: str
    proxy_env: dict[str, str]
    proxy_name: str = ""

    def stop(self, log_dir: Path) -> None:
        if not self.proxy_name:
            return
        import subprocess
        with (log_dir / "egress.jsonl").open("w") as fh:
            subprocess.run(["docker", "logs", self.proxy_name], stdout=fh, stderr=subprocess.STDOUT)
        docker.Container(self.proxy_name).remove()


def start_egress(mode: str, allow: set[str], run_id: str) -> Egress:
    if mode == "open":
        return Egress(mode="open", network="bridge", proxy_env={})
    docker.ensure_network(AGENT_NETWORK, internal=True)
    name = f"kb-egress-{run_id}"
    import subprocess
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    # The proxy is the container's main process so every allow/deny decision lands in `docker logs`.
    source = Path(__file__).with_name("egress_proxy.py").read_text()
    subprocess.run(["docker", "run", "-d", "--name", name, "--label", docker.LABEL,
                    "--label", "kairo-bench.role=egress", "--network", "bridge",
                    "-e", "KAIRO_EGRESS_ALLOW=" + ",".join(sorted(allow)), "-e", "PYTHONUNBUFFERED=1",
                    EGRESS_IMAGE, "python", "-u", "-c", source], check=True, capture_output=True)
    subprocess.run(["docker", "network", "connect", "--alias", "kairo-egress", AGENT_NETWORK, name],
                   check=True, capture_output=True)
    for _ in range(40):
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        if '"decision": "start"' in logs.stdout:
            break
        time.sleep(0.25)
    else:
        raise docker.DockerError(f"egress proxy did not start: {logs.stdout[-500:]}{logs.stderr[-500:]}")
    url = "http://kairo-egress:3128"
    env = {
        "HTTPS_PROXY": url, "https_proxy": url, "HTTP_PROXY": url, "http_proxy": url,
        "ALL_PROXY": url, "NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1",
        "NODE_USE_ENV_PROXY": "1",
    }
    return Egress(mode="allowlist", network=AGENT_NETWORK, proxy_env=env, proxy_name=name)


# ---------------------------------------------------------------- prompt

def render_prompt(task: Task, reproduction: bool) -> str:
    template = (BENCH_ROOT / "prompts" / "task.md").read_text()
    repro = ""
    if reproduction and task.has_reproducer:
        repro = (BENCH_ROOT / "prompts" / "reproduction.md").read_text()
    notes_parts = [p.read_text().strip() for p in (BENCH_ROOT / "envs" / task.env / "agent-notes.md",
                                                    task.dir / "agent-notes.md") if p.is_file()]
    notes = ("\n" + "\n\n".join(notes_parts) + "\n") if notes_parts else ""
    return template.format(
        repo_name=task.repo_name,
        base_commit=task.base_commit[:12] + (f", {task.base_ref}" if task.base_ref else ""),
        notes=notes,
        problem=task.problem.strip(),
        reproduction=repro,
    )


def expected_verdict(task: Task) -> str:
    return json.dumps({"verdict": "bug" if task.kind == "bug" else "not-a-bug",
                       "summary": "oracle", "files": []})


# ---------------------------------------------------------------- job

@dataclass
class RunConfig:
    run_id: str
    run_dir: Path
    reproduction: bool
    agent_timeout: int | None
    cpus: float | None
    memory: str | None
    keep_containers: bool
    secrets: dict[str, str]


def job_dir(cfg: RunConfig, agent: AgentSpec, task: Task, trial: int) -> Path:
    return cfg.run_dir / "jobs" / agent.slug / task.id / f"trial-{trial}"


def run_job(cfg: RunConfig, agent: AgentSpec, task: Task, trial: int, image: str,
            toolbox_volume: str | None, egress: Egress | None) -> dict:
    out = job_dir(cfg, agent, task, trial)
    out.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    record: dict = {
        "run_id": cfg.run_id, "agent": agent.name, "model": agent.model, "agent_label": agent.label,
        "task": task.id, "trial": trial, "kind": task.kind, "category": task.category,
        "difficulty": task.difficulty, "repo": task.repo_name, "started_at": started_at,
        "reproduction_kit": cfg.reproduction and task.has_reproducer,
    }
    prompt = render_prompt(task, cfg.reproduction)
    (out / "prompt.md").write_text(prompt)
    inv = invocation(agent, cfg.secrets)

    name = f"kb-{cfg.run_id}-{uuid.uuid4().hex[:6]}"
    mounts = [f"{toolbox_volume}:/opt/kairo-agents:ro"] if toolbox_volume else []
    network = egress.network if egress else "none"
    container = docker.start(image, name, network=network, mounts=mounts, env=egress.proxy_env if egress else {},
                             cpus=cfg.cpus, memory=cfg.memory,
                             labels={"kairo-bench.run": cfg.run_id, "kairo-bench.role": "agent"})
    t0 = time.monotonic()
    patch_bytes = b""
    try:
        # Issue-only mode withholds the reproduction kit entirely, not just its mention.
        grader.prepare_kit(container, task, include_public=cfg.reproduction)
        container.write_file("/tmp/kairo/prompt.md", prompt)
        if agent.name == "oracle":
            container.write_file("/tmp/kairo/gold.patch",
                                 task.gold_patch.read_text() if task.gold_patch.is_file() else "")
            container.write_file("/tmp/kairo/expected-verdict.json", expected_verdict(task))
        container.output("mkdir -p /tmp/kairo-out /home/kairo && chown -R kairo /tmp/kairo /tmp/kairo-out /home/kairo",
                         user="root")

        # Keep the environment's PATH (venvs, toolchains) and put the agent CLIs first.
        _, image_path = container.output("printenv PATH")
        inv.env["PATH"] = f"/opt/kairo-agents/bin:{image_path.strip()}"
        timeout = cfg.agent_timeout or task.agent_timeout
        with (out / "agent.stdout.jsonl").open("wb") as so, (out / "agent.stderr.log").open("wb") as se:
            code, timed_out = container.exec(inv.command, env=inv.env, env_names=inv.key_names,
                                             secrets=cfg.secrets, workdir="/work/repo", timeout=timeout,
                                             stdout=so, stderr=se)
        record["agent_exit"] = code
        record["agent_timed_out"] = timed_out
        record["agent_duration_s"] = round(time.monotonic() - t0, 1)

        # Patch: everything under /work/repo that differs from the base commit, honoring .gitignore.
        container.output("cd /work/repo && git add -A >/dev/null 2>&1; "
                         "git diff --cached --binary kairo-base > /tmp/kairo-out/patch.diff", timeout=600)
        patch_bytes = container.read_file("/tmp/kairo-out/patch.diff") or b""
        if len(patch_bytes) > MAX_PATCH_BYTES:
            record["error"] = f"patch too large ({len(patch_bytes)} bytes)"
            patch_bytes = b""
        (out / "patch.diff").write_bytes(patch_bytes)
        _, files = container.output("cd /work/repo && git diff --cached --name-only kairo-base")
        record["files_changed"] = [f for f in files.splitlines() if f.strip()]
        record["patch_bytes"] = len(patch_bytes)

        verdict_raw = container.read_file("/work/verdict.json")
        record["verdict"] = None
        if verdict_raw:
            (out / "verdict.json").write_bytes(verdict_raw)
            try:
                record["verdict"] = str(json.loads(verdict_raw).get("verdict", "")).strip().lower() or None
            except (json.JSONDecodeError, AttributeError):
                record["verdict"] = "unparseable"

        _save_transcripts(container, inv.home_dirs, out)
        stdout_text = (out / "agent.stdout.jsonl").read_text(errors="replace")
        record["usage"] = parse_usage(agent, stdout_text)
    except Exception as exc:  # infrastructure failure, not an agent failure
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if not cfg.keep_containers:
            container.remove()

    if "error" in record and record.get("agent_exit") is None:
        record.update(status="error", resolved=False)
        _write(out, record)
        return record

    g = grader.grade(task, image, patch_bytes.decode(errors="replace"), out, name_hint=cfg.run_id)
    record["grade"] = g.to_dict()
    tests_ok = g.tests_pass(task)
    expected = "bug" if task.kind == "bug" else "not-a-bug"
    record["verdict_correct"] = record.get("verdict") == expected
    if task.kind == "bug":
        record["resolved"] = tests_ok
    else:
        # A non-bug is handled when the agent says so, leaves the source alone (tests it adds
        # are fine), and every behavior that is correct today still passes.
        source_changes = [f for f in record.get("files_changed", []) if not is_test_path(f)]
        record["source_files_changed"] = source_changes
        record["hallucinated_fix"] = record.get("verdict") == "bug" or bool(source_changes)
        record["resolved"] = tests_ok and record["verdict_correct"] and not source_changes
    # A patch that does not apply, breaks the build, or hangs the entry point is the
    # agent's failure, so grading problems count as unresolved. Harness faults surface
    # earlier as status "error"; `kairo-bench validate` proves each verifier itself.
    record["status"] = "resolved" if record["resolved"] else "unresolved"
    if g.error:
        record["grade_error"] = g.error
    _write(out, record)
    return record


def _save_transcripts(container: docker.Container, home_dirs: list[str], out: Path) -> None:
    for i, rel in enumerate(home_dirs):
        tmp = out / f".home-{i}"
        if container.copy_out(f"/home/kairo/{rel}", tmp):
            with tarfile.open(out / f"transcript-{i}.tar.gz", "w:gz") as tar:
                tar.add(tmp, arcname=rel)
            _rmtree(tmp)
    traj = out / "mini-trajectory.json"
    container.copy_out("/tmp/kairo-out/mini-trajectory.json", traj)


def _rmtree(path: Path) -> None:
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def _write(out: Path, record: dict) -> None:
    record["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (out / "result.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


# ---------------------------------------------------------------- run

def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:4]


def run(tasks: list[Task], agents: list[AgentSpec], *, trials: int, jobs: int, runs_dir: Path,
        run_id: str | None, reproduction: bool, egress_mode: str, agent_timeout: int | None,
        cpus: float | None, memory: str | None, keep_containers: bool, secrets: dict[str, str]) -> Path:
    run_id = run_id or new_run_id()
    run_dir = runs_dir / run_id
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    cfg = RunConfig(run_id=run_id, run_dir=run_dir, reproduction=reproduction, agent_timeout=agent_timeout,
                    cpus=cpus, memory=memory, keep_containers=keep_containers, secrets=secrets)

    images: dict[str, str] = {}
    for env in sorted({t.env for t in tasks}):
        say(f"[env] {env}")
        images[env] = docker.build_env(env, log_path=run_dir / "logs" / f"build-{env}.log")

    needs_toolbox = any(not a.builtin for a in agents)
    toolbox_volume = ensure_toolbox(run_dir / "logs") if needs_toolbox else None
    egress = None
    if needs_toolbox:
        allow = {h for a in agents for h in a.egress}
        egress = start_egress(egress_mode, allow, run_id)

    meta_path = run_dir / "run.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    meta.update({
        "run_id": run_id,
        "created_at": meta.get("created_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "agents": sorted({*meta.get("agents", []), *(a.label for a in agents)}),
        "tasks": sorted({*meta.get("tasks", []), *(t.id for t in tasks)}),
        "trials": max(trials, meta.get("trials", 0)),
        "reproduction_kit": reproduction,
        "egress": egress_mode,
        "toolbox": toolbox_versions(),
        "images": {**meta.get("images", {}), **{env: docker.image_id(tag) for env, tag in images.items()}},
        "host": os.uname().machine,
    })
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")

    pending = []
    for agent in agents:
        for task in tasks:
            for trial in range(1, trials + 1):
                if (job_dir(cfg, agent, task, trial) / "result.json").is_file():
                    continue
                pending.append((agent, task, trial))
    say(f"[run] {run_id}: {len(pending)} job(s) to run, {jobs} at a time -> {run_dir}")

    try:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            futures = {
                pool.submit(run_job, cfg, a, t, n, images[t.env], None if a.builtin else toolbox_volume,
                            None if a.builtin else egress): (a, t, n)
                for a, t, n in pending
            }
            for fut in as_completed(futures):
                a, t, n = futures[fut]
                try:
                    rec = fut.result()
                except Exception as exc:
                    rec = {"agent_label": a.label, "task": t.id, "trial": n, "status": "error",
                           "error": f"{type(exc).__name__}: {exc}"}
                    out = job_dir(cfg, a, t, n)
                    out.mkdir(parents=True, exist_ok=True)
                    _write(out, {**rec, "run_id": run_id, "agent": a.name, "model": a.model, "kind": t.kind,
                                 "category": t.category, "difficulty": t.difficulty, "resolved": False})
                with (run_dir / "results.jsonl").open("a") as fh:
                    fh.write(json.dumps(rec, sort_keys=True) + "\n")
                mark = {"resolved": "PASS", "unresolved": "fail", "error": "ERROR"}.get(rec.get("status"), "?")
                problem = rec.get("error") or rec.get("grade_error")
                extra = f" ({problem[:120]})" if problem else ""
                say(f"[{mark:>5}] {a.label:<32} {t.id:<48} trial {n}{extra}")
    finally:
        if egress:
            egress.stop(run_dir / "logs")
    return run_dir
