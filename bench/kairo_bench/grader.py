"""Hidden, offline grading.

A patch is graded in a fresh container created from the task's environment
image with networking disabled. The task's public kit and hidden verifier are
copied in, the patch is applied to ``/work/repo``, protected paths are reset to
the base commit, and the verifier command runs. The verifier writes
``$KAIRO_RESULTS`` as JSON::

    {"tests": [{"name": "...", "status": "pass" | "fail" | "error", "detail": "..."}]}

Only test names declared in task.toml count. A declared test the verifier did
not report is ``missing`` and counts as a failure, so a verifier cannot pass by
skipping work.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from kairo_bench import docker
from kairo_bench.config import BENCH_ROOT
from kairo_bench.tasks import Task

LIB_DIR = BENCH_ROOT / "lib"
RESULTS_PATH = "/tmp/kairo-results.json"


@dataclass
class Grade:
    patch_applied: bool
    fail_to_pass: dict[str, str] = field(default_factory=dict)
    pass_to_pass: dict[str, str] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    verifier_exit: int | None = None
    verifier_timed_out: bool = False
    error: str = ""
    duration_s: float = 0.0

    @property
    def f2p_passed(self) -> bool:
        return bool(self.fail_to_pass) and all(v == "pass" for v in self.fail_to_pass.values())

    @property
    def p2p_passed(self) -> bool:
        return all(v == "pass" for v in self.pass_to_pass.values())

    def tests_pass(self, task: Task) -> bool:
        if not self.patch_applied or self.error:
            return False
        if task.kind == "bug":
            return self.f2p_passed and self.p2p_passed
        return self.p2p_passed

    def to_dict(self) -> dict:
        return {
            "patch_applied": self.patch_applied,
            "fail_to_pass": self.fail_to_pass,
            "pass_to_pass": self.pass_to_pass,
            "details": self.details,
            "verifier_exit": self.verifier_exit,
            "verifier_timed_out": self.verifier_timed_out,
            "error": self.error,
            "duration_s": round(self.duration_s, 1),
        }


def prepare_kit(container: docker.Container, task: Task, *, include_public: bool = True) -> None:
    """Copy the shared helper library and (unless withheld) the task's public kit."""
    container.copy_in(LIB_DIR, "/opt/kairo-lib", owner="0:0")
    container.output("chmod -R a+rX /opt/kairo-lib", user="root")
    if include_public and task.public_dir.is_dir():
        container.copy_in(task.public_dir, "/work/kairo")


def grade(task: Task, image: str, patch: str, out_dir: Path, *, name_hint: str = "grade") -> Grade:
    started = time.monotonic()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"kb-{name_hint}-{uuid.uuid4().hex[:8]}"
    container = docker.start(image, name, network="none", labels={"kairo-bench.role": "grader"})
    result = Grade(patch_applied=False)
    try:
        prepare_kit(container, task)
        container.copy_in(task.verifier_dir, "/work/kairo-verifier")

        if patch.strip():
            container.write_file("/tmp/kairo/agent.patch", patch)
            code, out = container.output(
                "cd /work/repo && git apply --binary --whitespace=nowarn /tmp/kairo/agent.patch")
            if code != 0:
                result.error = f"patch did not apply: {out.strip()[-1500:]}"
                (out_dir / "verifier.log").write_text(result.error + "\n")
                return result
        result.patch_applied = True

        if task.reset_paths:
            paths = " ".join(f"'{p}'" for p in task.reset_paths)
            container.output(f"cd /work/repo && git checkout kairo-base -- {paths} 2>/dev/null || true")

        with (out_dir / "verifier.log").open("wb") as log:
            code, timed_out = container.exec(
                # Prepend the helper library; keep any PYTHONPATH the environment image sets.
                'export PYTHONPATH="/opt/kairo-lib${PYTHONPATH:+:$PYTHONPATH}"; '
                f"cd /work/kairo-verifier && {task.verifier_command}",
                env={"KAIRO_RESULTS": RESULTS_PATH, "KAIRO_TASK": task.id, "PYTHONDONTWRITEBYTECODE": "1"},
                timeout=task.verifier_timeout, stdout=log, stderr=log)
        result.verifier_exit = code
        result.verifier_timed_out = timed_out

        raw = container.read_file(RESULTS_PATH)
        reported: dict[str, str] = {}
        if raw:
            (out_dir / "verifier-results.json").write_bytes(raw)
            try:
                for test in json.loads(raw).get("tests", []):
                    reported[test["name"]] = test.get("status", "error")
                    if test.get("detail"):
                        result.details[test["name"]] = str(test["detail"])[:2000]
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
                result.error = f"verifier wrote malformed results: {exc}"
        elif not timed_out:
            result.error = f"verifier exited {code} without writing results"
        if timed_out:
            result.error = f"verifier timed out after {task.verifier_timeout}s"

        result.fail_to_pass = {n: reported.get(n, "missing") for n in task.fail_to_pass}
        result.pass_to_pass = {n: reported.get(n, "missing") for n in task.pass_to_pass}
        return result
    finally:
        container.remove()
        result.duration_s = time.monotonic() - started
