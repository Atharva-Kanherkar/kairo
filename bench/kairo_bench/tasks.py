"""Task and split loading.

A task lives in ``tasks/<id>/`` and contains:

- ``task.toml``: metadata (schema below, validated by :func:`load_task`)
- ``problem.md``: the problem statement the agent sees
- ``public/``: files copied to ``/work/kairo`` for the agent (reproducer, mock
  upstream, configs). Optional ``public/reproduce.sh`` is the documented
  reproduction command.
- ``verifier/``: hidden grading files copied to ``/work/kairo-verifier`` only in a
  fresh grading container, never into the agent's container
- ``gold.patch``: reference fix, validated by ``kairo-bench validate``
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from kairo_bench.config import BENCH_ROOT

TASKS_DIR = BENCH_ROOT / "tasks"
SPLITS_DIR = BENCH_ROOT / "splits"

KINDS = ("bug", "no-bug")
CATEGORIES = (
    "stop-reason-mapping",
    "request-field-drop",
    "content-loss",
    "tool-call-identity",
    "stream-lifecycle",
    "credential-boundary",
    "crash",
    "state-and-history",
)
DIFFICULTIES = ("easy", "medium", "hard")
GOLD_SOURCES = ("upstream-merged", "upstream-open-pr", "kairo-reference", "none")


class TaskError(ValueError):
    pass


@dataclass(frozen=True)
class Task:
    id: str
    dir: Path
    title: str
    kind: str
    env: str
    category: str
    difficulty: str
    finding: str
    repo_name: str
    repo_url: str
    base_commit: str
    base_ref: str
    upstream_issues: tuple[str, ...]
    upstream_fix: str
    upstream_status: str
    gold_source: str
    gold_url: str
    verifier_command: str
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    reset_paths: tuple[str, ...]
    agent_timeout: int
    verifier_timeout: int
    languages: tuple[str, ...] = field(default_factory=tuple)
    splits: tuple[str, ...] = field(default_factory=tuple)

    @property
    def problem(self) -> str:
        return (self.dir / "problem.md").read_text()

    @property
    def public_dir(self) -> Path:
        return self.dir / "public"

    @property
    def verifier_dir(self) -> Path:
        return self.dir / "verifier"

    @property
    def gold_patch(self) -> Path:
        return self.dir / "gold.patch"

    @property
    def has_reproducer(self) -> bool:
        return (self.public_dir / "reproduce.sh").is_file()


def _req(d: dict, key: str, where: str):
    if key not in d:
        raise TaskError(f"{where}: missing required key {key!r}")
    return d[key]


def _choice(value: str, allowed: tuple[str, ...], where: str) -> str:
    if value not in allowed:
        raise TaskError(f"{where}: {value!r} is not one of {', '.join(allowed)}")
    return value


def load_task(task_dir: Path) -> Task:
    where = str(task_dir.relative_to(BENCH_ROOT))
    raw = tomllib.loads((task_dir / "task.toml").read_text())
    repo = _req(raw, "repo", where)
    upstream = raw.get("upstream", {})
    gold = raw.get("gold", {})
    verifier = _req(raw, "verifier", where)
    agent = raw.get("agent", {})

    task = Task(
        id=_req(raw, "id", where),
        dir=task_dir,
        title=_req(raw, "title", where),
        kind=_choice(_req(raw, "kind", where), KINDS, f"{where}.kind"),
        env=_req(raw, "env", where),
        category=_choice(_req(raw, "category", where), CATEGORIES, f"{where}.category"),
        difficulty=_choice(_req(raw, "difficulty", where), DIFFICULTIES, f"{where}.difficulty"),
        finding=str(raw.get("finding", "")),
        repo_name=_req(repo, "name", f"{where}.repo"),
        repo_url=_req(repo, "url", f"{where}.repo"),
        base_commit=_req(repo, "base_commit", f"{where}.repo"),
        base_ref=repo.get("base_ref", ""),
        upstream_issues=tuple(upstream.get("issues", [])),
        upstream_fix=upstream.get("fix", ""),
        upstream_status=upstream.get("status", ""),
        gold_source=_choice(gold.get("source", "none"), GOLD_SOURCES, f"{where}.gold.source"),
        gold_url=gold.get("url", ""),
        verifier_command=_req(verifier, "command", f"{where}.verifier"),
        fail_to_pass=tuple(verifier.get("fail_to_pass", [])),
        pass_to_pass=tuple(verifier.get("pass_to_pass", [])),
        reset_paths=tuple(verifier.get("reset_paths", [])),
        agent_timeout=int(agent.get("timeout_sec", 2700)),
        verifier_timeout=int(verifier.get("timeout_sec", 900)),
        languages=tuple(raw.get("languages", [])),
        splits=tuple(raw.get("splits", [])),
    )

    if task.id != task_dir.name:
        raise TaskError(f"{where}: id {task.id!r} must match the folder name")
    if len(task.base_commit) != 40:
        raise TaskError(f"{where}: base_commit must be a full 40-character SHA")
    if not (task_dir / "problem.md").is_file():
        raise TaskError(f"{where}: missing problem.md")
    if not task.verifier_dir.is_dir():
        raise TaskError(f"{where}: missing verifier/")
    if task.kind == "bug":
        if not task.fail_to_pass:
            raise TaskError(f"{where}: a bug task needs at least one fail_to_pass test")
        if task.gold_source == "none" or not task.gold_patch.is_file():
            raise TaskError(f"{where}: a bug task needs gold.patch and gold.source")
    else:
        if task.fail_to_pass:
            raise TaskError(f"{where}: a no-bug task must not declare fail_to_pass tests")
        if not task.pass_to_pass:
            raise TaskError(f"{where}: a no-bug task needs pass_to_pass tests")
    overlap = set(task.fail_to_pass) & set(task.pass_to_pass)
    if overlap:
        raise TaskError(f"{where}: tests listed as both kinds: {sorted(overlap)}")
    return task


def all_tasks() -> list[Task]:
    if not TASKS_DIR.is_dir():
        return []
    return [load_task(p) for p in sorted(TASKS_DIR.iterdir()) if (p / "task.toml").is_file()]


def split_names() -> list[str]:
    return sorted(p.stem for p in SPLITS_DIR.glob("*.txt"))


def load_split(name: str) -> list[str]:
    path = SPLITS_DIR / f"{name}.txt"
    if not path.is_file():
        raise TaskError(f"unknown split {name!r}; known: {', '.join(split_names())}")
    ids = []
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            ids.append(line)
    return ids


def select(split: str | None, ids: list[str] | None) -> list[Task]:
    tasks = {t.id: t for t in all_tasks()}
    wanted: list[str] = []
    if split:
        wanted.extend(load_split(split))
    if ids:
        for pattern in ids:
            matches = [tid for tid in tasks if tid == pattern or tid.startswith(pattern)]
            if not matches:
                raise TaskError(f"no task matches {pattern!r}")
            wanted.extend(matches)
    if not split and not ids:
        wanted = list(tasks)
    missing = [tid for tid in wanted if tid not in tasks]
    if missing:
        raise TaskError(f"split references unknown tasks: {', '.join(missing)}")
    seen: set[str] = set()
    return [tasks[t] for t in wanted if not (t in seen or seen.add(t))]
