"""Prove every task's verifier before any agent is scored.

For a bug task, the unpatched base must fail every fail-to-pass test and pass
every pass-to-pass test, and the gold patch must pass everything. For a no-bug
task, the unpatched base must pass every pass-to-pass test. Each check can be
repeated to report N of N. Results are written to ``validation/<task>.json``.
"""

from __future__ import annotations

import json
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from kairo_bench import docker, grader
from kairo_bench.config import BENCH_ROOT
from kairo_bench.tasks import Task

VALIDATION_DIR = BENCH_ROOT / "validation"


def _check_base(task: Task, g: grader.Grade) -> list[str]:
    problems = []
    if g.error and task.kind == "no-bug":
        problems.append(f"base: {g.error}")
    for name, status in g.fail_to_pass.items():
        if status == "pass":
            problems.append(f"base: fail-to-pass test {name!r} already passes")
        elif status in ("missing", "error"):
            problems.append(f"base: fail-to-pass test {name!r} is {status}, not a clean fail")
    for name, status in g.pass_to_pass.items():
        if status != "pass":
            problems.append(f"base: pass-to-pass test {name!r} is {status}")
    return problems


def _check_gold(g: grader.Grade) -> list[str]:
    problems = []
    if not g.patch_applied:
        problems.append(f"gold: {g.error or 'patch did not apply'}")
    if g.error and g.patch_applied:
        problems.append(f"gold: {g.error}")
    for name, status in {**g.fail_to_pass, **g.pass_to_pass}.items():
        if status != "pass":
            problems.append(f"gold: test {name!r} is {status}")
    return problems


def validate_task(task: Task, repeats: int, log_root: Path) -> dict:
    image = docker.build_env(task.env, log_path=log_root / f"build-{task.env}.log")
    runs = []
    problems: list[str] = []
    for i in range(1, repeats + 1):
        base = grader.grade(task, image, "", log_root / task.id / f"base-{i}", name_hint="validate")
        p = _check_base(task, base)
        entry = {"repeat": i, "base": base.to_dict(), "base_ok": not p}
        problems += [f"[{i}] {x}" for x in p]
        if task.kind == "bug":
            gold = grader.grade(task, image, task.gold_patch.read_text(), log_root / task.id / f"gold-{i}",
                                name_hint="validate")
            q = _check_gold(gold)
            entry.update(gold=gold.to_dict(), gold_ok=not q)
            problems += [f"[{i}] {x}" for x in q]
        runs.append(entry)
    ok = not problems
    base_ok = sum(r["base_ok"] for r in runs)
    gold_ok = sum(r.get("gold_ok", False) for r in runs)
    return {
        "task": task.id,
        "kind": task.kind,
        "ok": ok,
        "repeats": repeats,
        "base_as_expected": f"{base_ok}/{repeats}",
        "gold_as_expected": f"{gold_ok}/{repeats}" if task.kind == "bug" else "n/a",
        "problems": problems,
        "env": task.env,
        "image_id": docker.image_id(image),
        "base_commit": task.base_commit,
        "gold_source": task.gold_source,
        "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "runs": runs,
    }


def validate(tasks: list[Task], *, repeats: int, jobs: int, write: bool, log_dir: Path | None) -> list[dict]:
    log_root = log_dir or Path(tempfile.mkdtemp(prefix="kairo-validate-"))
    for env in sorted({t.env for t in tasks}):
        print(f"[env] {env}", flush=True)
        docker.build_env(env, log_path=log_root / f"build-{env}.log")
    reports = []
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futs = {pool.submit(validate_task, t, repeats, log_root): t for t in tasks}
        for fut in as_completed(futs):
            task = futs[fut]
            try:
                rep = fut.result()
            except Exception as exc:
                rep = {"task": task.id, "kind": task.kind, "ok": False, "problems": [f"{type(exc).__name__}: {exc}"]}
            reports.append(rep)
            status = "ok" if rep["ok"] else "FAIL"
            print(f"[{status:>4}] {task.id:<52} base {rep.get('base_as_expected', '?'):<5} "
                  f"gold {rep.get('gold_as_expected', '?')}", flush=True)
            for problem in rep.get("problems", [])[:8]:
                print(f"         {problem}", flush=True)
            if write and rep["ok"]:
                VALIDATION_DIR.mkdir(exist_ok=True)
                slim = {k: v for k, v in rep.items() if k != "runs"}
                slim["tests"] = _test_matrix(rep)
                (VALIDATION_DIR / f"{task.id}.json").write_text(json.dumps(slim, indent=2, sort_keys=True) + "\n")
    print(f"logs: {log_root}")
    return sorted(reports, key=lambda r: r["task"])


def _test_matrix(rep: dict) -> dict:
    """Per test: status on base and on gold for every repeat."""
    out: dict[str, dict[str, list[str]]] = {}
    for run in rep.get("runs", []):
        for phase in ("base", "gold"):
            g = run.get(phase)
            if not g:
                continue
            for name, status in {**g["fail_to_pass"], **g["pass_to_pass"]}.items():
                out.setdefault(name, {}).setdefault(phase, []).append(status)
    return out
