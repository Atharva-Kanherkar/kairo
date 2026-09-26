"""kairo-bench command line."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from kairo_bench import docker, report, runner
from kairo_bench.agents import available_keys, known_agents, parse_agent
from kairo_bench.config import BENCH_ROOT, DEFAULT_RUNS_DIR, load_dotenv
from kairo_bench.tasks import Task, TaskError, all_tasks, select, split_names

DEFAULT_SPLITS = "kairo-30,negatives"


def _tasks(args) -> list[Task]:
    ids = [t for t in (args.tasks or "").split(",") if t] or None
    if ids:
        return select(None, ids)
    splits = [s for s in (args.split or DEFAULT_SPLITS).split(",") if s]
    if splits == ["all"]:
        return all_tasks()
    seen, out = set(), []
    for split in splits:
        for task in select(split, None):
            if task.id not in seen:
                seen.add(task.id)
                out.append(task)
    return out


def _add_selection(p: argparse.ArgumentParser) -> None:
    p.add_argument("--split", help=f"comma-separated splits, or 'all' (default: {DEFAULT_SPLITS})")
    p.add_argument("--tasks", help="comma-separated task ids or id prefixes (overrides --split)")


def cmd_list(args) -> int:
    tasks = _tasks(args)
    print(f"{'task':<52} {'kind':<7} {'difficulty':<10} {'category':<22} {'gold':<17} repo")
    for t in tasks:
        print(f"{t.id:<52} {t.kind:<7} {t.difficulty:<10} {t.category:<22} {t.gold_source:<17} {t.repo_name}")
    bug = sum(t.kind == "bug" for t in tasks)
    print(f"\n{len(tasks)} task(s): {bug} bug, {len(tasks) - bug} no-bug. Splits: {', '.join(split_names())}")
    return 0


def cmd_show(args) -> int:
    task = select(None, [args.task])[0]
    print(runner.render_prompt(task, not args.no_repro))
    return 0


def cmd_doctor(args) -> int:
    ok, info = docker.available()
    print(f"docker: {'ok ' + info if ok else 'NOT AVAILABLE: ' + info}")
    free = shutil.disk_usage(BENCH_ROOT).free / 1e9
    print(f"disk free: {free:.0f} GB{'  (warning: environments need roughly 40 GB)' if free < 40 else ''}")
    secrets = load_dotenv()
    print("agents:")
    for name, raw in known_agents().items():
        if raw.get("builtin"):
            print(f"  {name:<16} builtin control")
            continue
        spec = parse_agent(name)
        have = available_keys(spec, secrets)
        state = f"ready ({', '.join(have)})" if have else f"no key (set one of {', '.join(spec.keys)})"
        print(f"  {name:<16} {spec.model or '(CLI default model)':<30} {state}")
    try:
        tasks = all_tasks()
        print(f"tasks: {len(tasks)} loaded, splits: {', '.join(split_names())}")
    except TaskError as exc:
        print(f"tasks: INVALID: {exc}")
        return 1
    return 0 if ok else 1


def cmd_build(args) -> int:
    tasks = _tasks(args)
    log_dir = DEFAULT_RUNS_DIR / "_build"
    for env in sorted({t.env for t in tasks}):
        print(f"[env] {env} ...", flush=True)
        tag = docker.build_env(env, log_path=log_dir / f"build-{env}.log")
        print(f"[env] {env} -> {tag}")
    if args.toolbox:
        print("[toolbox] building agent CLIs ...", flush=True)
        print(f"[toolbox] volume {runner.ensure_toolbox(log_dir)}")
    return 0


def cmd_validate(args) -> int:
    from kairo_bench.validate import validate
    tasks = _tasks(args)
    log_dir = Path(args.log_dir) if args.log_dir else DEFAULT_RUNS_DIR / "_validate"
    reports = validate(tasks, repeats=args.repeats, jobs=args.jobs, write=not args.no_write, log_dir=log_dir)
    bad = [r for r in reports if not r["ok"]]
    print(f"\n{len(reports) - len(bad)}/{len(reports)} task(s) validated")
    return 1 if bad else 0


def cmd_run(args) -> int:
    secrets = load_dotenv(Path(args.env_file) if args.env_file else None)
    tasks = _tasks(args)
    if args.agents:
        agents = [parse_agent(s) for s in args.agents.split(",") if s]
    else:
        agents = [parse_agent(n) for n, raw in known_agents().items()
                  if not raw.get("builtin") and available_keys(parse_agent(n), secrets)]
        if not agents:
            print("No agent has a key. Put keys in bench/.env (see bench/.env.example) or pass --agents.")
            return 2
    for a in agents:
        if not a.builtin and not available_keys(a, secrets):
            print(f"agent {a.label} needs one of {', '.join(a.keys)}; none is set.")
            return 2
    ok, info = docker.available()
    if not ok:
        print(f"docker is not available: {info}")
        return 2

    print(f"Kairo-30: {len(tasks)} task(s) x {len(agents)} agent(s) x {args.trials} trial(s)")
    for a in agents:
        print(f"  agent {a.label}")
    run_dir = runner.run(
        tasks, agents, trials=args.trials, jobs=args.jobs, runs_dir=Path(args.runs_dir),
        run_id=args.run_id, reproduction=not args.no_repro, egress_mode=args.egress,
        agent_timeout=args.agent_timeout, cpus=args.cpus, memory=args.memory,
        keep_containers=args.keep_containers, secrets=secrets)
    path, _ = report.write(run_dir)
    print()
    print(path.read_text())
    print(f"Results: {run_dir}")
    return 0


def cmd_report(args) -> int:
    runs_dir = Path(args.runs_dir)
    if args.run in (None, "latest"):
        candidates = sorted(p for p in runs_dir.iterdir() if (p / "run.json").is_file()) if runs_dir.is_dir() else []
        if not candidates:
            print("no runs found")
            return 1
        run_dir = candidates[-1]
    else:
        run_dir = Path(args.run)
        if not run_dir.is_dir():
            run_dir = runs_dir / args.run
    path, summary = report.write(run_dir)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(path.read_text())
    return 0


def cmd_catalog(args) -> int:
    """Write TASKS.md: every task with its upstream links, gold source, and validation record."""
    from kairo_bench.validate import VALIDATION_DIR
    tasks = all_tasks()
    issues_dir = BENCH_ROOT.parent / "issues"

    def finding_link(t: Task) -> str:
        num = t.finding[:3]
        # Some findings have more than one folder; link the most complete writeup.
        match = sorted(issues_dir.glob(f"{num}-*/README.md"), key=lambda p: -p.stat().st_size) if num.isdigit() else []
        return f"[{t.finding}](../issues/{match[0].parent.name}/README.md)" if match else t.finding

    def validation(t: Task) -> str:
        path = VALIDATION_DIR / f"{t.id}.json"
        if not path.is_file():
            return "not yet"
        rec = json.loads(path.read_text())
        base = rec.get("base_as_expected", "?")
        return f"base {base}, gold {rec.get('gold_as_expected')}" if t.kind == "bug" else f"base {base}"

    def upstream(t: Task) -> str:
        links = []
        for url in t.upstream_issues:
            m = re.search(r"github\.com/([^/]+/[^/]+)/(issues|pull)/(\d+)", url)
            links.append(f"[{m.group(1).split('/')[1]}#{m.group(3)}]({url})" if m else url)
        if t.upstream_fix:
            m = re.search(r"/pull/(\d+)", t.upstream_fix)
            links.append(f"fix [#{m.group(1)}]({t.upstream_fix})" if m else t.upstream_fix)
        return ", ".join(links) or t.upstream_status

    lines = ["# Kairo-30 task catalog", "",
             "Generated by `kairo-bench catalog` from `tasks/*/task.toml` and `validation/*.json`.",
             "Do not edit by hand.", ""]
    for kind, title in (("bug", "Bug tasks (split `kairo-30`)"), ("no-bug", "No-bug tasks (split `negatives`)")):
        rows = [t for t in tasks if t.kind == kind]
        lines += [f"## {title}", "", "| Task | Project | Category | Difficulty | Gold patch | Upstream | Kairo finding | Validated |",
                  "|---|---|---|---|---|---|---|---|"]
        for t in rows:
            gold = t.gold_source if kind == "bug" else "n/a"
            lines.append(f"| `{t.id}` | {t.repo_name} | {t.category} | {t.difficulty} | {gold} | {upstream(t)} | "
                         f"{finding_link(t)} | {validation(t)} |")
        lines.append("")
    out = BENCH_ROOT / "TASKS.md"
    out.write_text("\n".join(lines))
    print(f"wrote {out} ({len(tasks)} tasks)")
    return 0


def cmd_clean(args) -> int:
    n = docker.cleanup("kb-")
    print(f"removed {n} container(s)")
    return 0


def cmd_prune(args) -> int:
    """Remove environment images whose tag no longer matches envs/ (plus build cache)."""
    import subprocess
    from kairo_bench.config import ENVS_DIR
    current = {docker.env_tag(p.name) for p in ENVS_DIR.iterdir() if (p / "Dockerfile").is_file()}
    out = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", "kairo-bench/*"],
                         capture_output=True, text=True).stdout.split()
    stale = [t for t in out if t.startswith("kairo-bench/env-") and t not in current]
    if args.all:
        stale = [t for t in out if t.startswith("kairo-bench/env-")]
    for tag in stale:
        subprocess.run(["docker", "rmi", tag], capture_output=True)
        print(f"removed {tag}")
    subprocess.run(["docker", "builder", "prune", "-f", "--filter", "until=1h"], capture_output=True)
    print(f"removed {len(stale)} image(s); pruned build cache older than an hour")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kairo-bench", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="list tasks")
    _add_selection(p)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="print the exact prompt an agent receives for a task")
    p.add_argument("task")
    p.add_argument("--no-repro", action="store_true")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("doctor", help="check docker, disk, and which agents have keys")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("build", help="build task environments (and optionally the agent toolbox)")
    _add_selection(p)
    p.add_argument("--toolbox", action="store_true", help="also build the agent CLI toolbox")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("validate", help="prove each verifier: base fails, gold passes (no keys needed)")
    _add_selection(p)
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--jobs", type=int, default=2)
    p.add_argument("--no-write", action="store_true", help="do not update bench/validation/")
    p.add_argument("--log-dir")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("run", help="run agents on tasks, grade, and print the leaderboard")
    _add_selection(p)
    p.add_argument("--agents", help="comma-separated NAME or NAME:MODEL (default: every agent with a key)")
    p.add_argument("--trials", type=int, default=1)
    p.add_argument("--jobs", type=int, default=2, help="concurrent jobs")
    p.add_argument("--run-id", help="resume or extend an existing run")
    p.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    p.add_argument("--env-file", help="read keys from this file instead of bench/.env and ../.env")
    p.add_argument("--no-repro", action="store_true", help="issue-only mode: withhold the reproduction kit")
    p.add_argument("--egress", choices=["allowlist", "open"], default="allowlist")
    p.add_argument("--agent-timeout", type=int, help="seconds per agent attempt (default: per task, 45 min)")
    p.add_argument("--cpus", type=float, default=4.0)
    p.add_argument("--memory", default="8g")
    p.add_argument("--keep-containers", action="store_true")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("report", help="rebuild the leaderboard for a run")
    p.add_argument("run", nargs="?", default="latest")
    p.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("catalog", help="regenerate TASKS.md from task metadata and validation records")
    p.set_defaults(fn=cmd_catalog)

    p = sub.add_parser("clean", help="remove leftover kairo-bench containers")
    p.set_defaults(fn=cmd_clean)

    p = sub.add_parser("prune", help="remove stale environment images and old build cache")
    p.add_argument("--all", action="store_true", help="remove every environment image, not only stale ones")
    p.set_defaults(fn=cmd_prune)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except (TaskError, ValueError, docker.DockerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted; containers left behind can be removed with `kairo-bench clean`", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
