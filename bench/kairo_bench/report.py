"""Aggregate job results into a leaderboard.

Reads every ``jobs/*/*/trial-*/result.json`` in a run directory, so a resumed
or extended run is summarized from the job files themselves.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from kairo_bench.tasks import CATEGORIES, DIFFICULTIES


def load_results(run_dir: Path) -> list[dict]:
    results = []
    for path in sorted(run_dir.glob("jobs/*/*/trial-*/result.json")):
        try:
            results.append(json.loads(path.read_text()))
        except json.JSONDecodeError:
            continue
    return results


def _pct(num: float, den: float) -> str:
    return f"{100 * num / den:.1f}%" if den else "n/a"


def summarize(results: list[dict]) -> dict:
    by_agent: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_agent[r.get("agent_label") or r.get("agent", "?")].append(r)

    agents = {}
    for label, rows in sorted(by_agent.items()):
        bug = [r for r in rows if r.get("kind") == "bug"]
        nobug = [r for r in rows if r.get("kind") == "no-bug"]

        def per_task(rs: list[dict]) -> dict[str, list[bool]]:
            d: dict[str, list[bool]] = defaultdict(list)
            for r in rs:
                d[r["task"]].append(bool(r.get("resolved")))
            return d

        bug_tasks = per_task(bug)
        nobug_tasks = per_task(nobug)
        mean_bug = sum(sum(v) / len(v) for v in bug_tasks.values()) / len(bug_tasks) if bug_tasks else 0.0
        any_bug = sum(any(v) for v in bug_tasks.values())
        costs = [r["usage"]["cost_usd"] for r in rows if isinstance(r.get("usage"), dict)
                 and isinstance(r["usage"].get("cost_usd"), (int, float))]
        durations = [r["agent_duration_s"] for r in rows if isinstance(r.get("agent_duration_s"), (int, float))]

        def breakdown(key: str, values: tuple[str, ...]) -> dict[str, str]:
            out = {}
            for v in values:
                rs = [r for r in bug if r.get(key) == v]
                if rs:
                    out[v] = f"{sum(bool(r.get('resolved')) for r in rs)}/{len(rs)}"
            return out

        agents[label] = {
            "bug_tasks": len(bug_tasks),
            "bug_resolved_mean": round(mean_bug, 4),
            "bug_resolved_any_trial": any_bug,
            "bug_verdict_correct": sum(bool(r.get("verdict_correct")) for r in bug),
            "bug_attempts": len(bug),
            "nobug_tasks": len(nobug_tasks),
            "nobug_correct": sum(bool(r.get("resolved")) for r in nobug),
            "nobug_attempts": len(nobug),
            "hallucinated_fixes": sum(bool(r.get("hallucinated_fix")) for r in nobug),
            "errors": sum(r.get("status") == "error" for r in rows),
            "timeouts": sum(bool(r.get("agent_timed_out")) for r in rows),
            "cost_usd_total": round(sum(costs), 4) if costs else None,
            "cost_usd_mean": round(sum(costs) / len(costs), 4) if costs else None,
            "duration_s_mean": round(sum(durations) / len(durations), 1) if durations else None,
            "by_category": breakdown("category", CATEGORIES),
            "by_difficulty": breakdown("difficulty", DIFFICULTIES),
        }

    matrix: dict[str, dict[str, str]] = defaultdict(dict)
    kinds: dict[str, str] = {}
    for label, rows in by_agent.items():
        cells: dict[str, list[bool]] = defaultdict(list)
        for r in rows:
            cells[r["task"]].append(bool(r.get("resolved")))
            kinds[r["task"]] = r.get("kind", "")
        for task, vals in cells.items():
            matrix[task][label] = f"{sum(vals)}/{len(vals)}"
    return {"agents": agents, "matrix": dict(sorted(matrix.items())), "kinds": kinds}


def render_markdown(summary: dict, meta: dict) -> str:
    agents = summary["agents"]
    lines = [f"# Kairo-30 results: {meta.get('run_id', '')}", ""]
    lines.append(f"- Created: {meta.get('created_at', '')}")
    lines.append(f"- Reproduction kit given to agents: {'yes' if meta.get('reproduction_kit') else 'no'}")
    lines.append(f"- Egress: {meta.get('egress', '')}")
    tb = meta.get("toolbox", {})
    if tb:
        lines.append("- Agent CLIs: " + ", ".join(f"{k} {v}" for k, v in sorted(tb.items())))
    lines += ["", "## Leaderboard", "",
              "| Agent | Bug tasks resolved | Resolved in any trial | No-bug tasks handled | Hallucinated fixes | "
              "Mean cost (USD) | Mean time (s) | Errors |",
              "|---|---|---|---|---|---|---|---|"]
    ranked = sorted(agents.items(), key=lambda kv: (-kv[1]["bug_resolved_mean"], kv[0]))
    for label, a in ranked:
        bug = f"{_pct(a['bug_resolved_mean'], 1)} of {a['bug_tasks']}" if a["bug_tasks"] else "n/a"
        anyt = f"{a['bug_resolved_any_trial']}/{a['bug_tasks']}" if a["bug_tasks"] else "n/a"
        nob = f"{a['nobug_correct']}/{a['nobug_attempts']}" if a["nobug_attempts"] else "n/a"
        hal = f"{a['hallucinated_fixes']}/{a['nobug_attempts']}" if a["nobug_attempts"] else "n/a"
        cost = f"{a['cost_usd_mean']:.2f}" if a["cost_usd_mean"] is not None else "n/a"
        dur = f"{a['duration_s_mean']:.0f}" if a["duration_s_mean"] is not None else "n/a"
        lines.append(f"| {label} | {bug} | {anyt} | {nob} | {hal} | {cost} | {dur} | {a['errors']} |")

    lines += ["", "## By failure category (bug tasks, resolved/attempts)", ""]
    labels = [label for label, _ in ranked]
    lines.append("| Category | " + " | ".join(labels) + " |")
    lines.append("|---|" + "---|" * len(labels))
    for cat in CATEGORIES:
        cells = [agents[label]["by_category"].get(cat, "") for label in labels]
        if any(cells):
            lines.append(f"| {cat} | " + " | ".join(cells) + " |")

    lines += ["", "## By difficulty (bug tasks, resolved/attempts)", ""]
    lines.append("| Difficulty | " + " | ".join(labels) + " |")
    lines.append("|---|" + "---|" * len(labels))
    for diff in DIFFICULTIES:
        cells = [agents[label]["by_difficulty"].get(diff, "") for label in labels]
        if any(cells):
            lines.append(f"| {diff} | " + " | ".join(cells) + " |")

    lines += ["", "## Per task (resolved trials/trials)", ""]
    lines.append("| Task | Kind | " + " | ".join(labels) + " |")
    lines.append("|---|---|" + "---|" * len(labels))
    for task, row in summary["matrix"].items():
        lines.append(f"| {task} | {summary['kinds'].get(task, '')} | "
                     + " | ".join(row.get(label, "") for label in labels) + " |")
    lines += ["", "A bug task is resolved when every hidden fail-to-pass and pass-to-pass test passes.",
              "A no-bug task is handled when the agent reports `not-a-bug`, changes no source file,",
              "and every hidden pass-to-pass test still passes. A hallucinated fix is a no-bug",
              "attempt where the agent reported a bug or changed a non-test file.", ""]
    return "\n".join(lines)


def write(run_dir: Path) -> tuple[Path, dict]:
    results = load_results(run_dir)
    summary = summarize(results)
    meta_path = run_dir / "run.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    (run_dir / "summary.json").write_text(json.dumps({"meta": meta, **summary}, indent=2, sort_keys=True) + "\n")
    md = render_markdown(summary, meta)
    (run_dir / "summary.md").write_text(md)
    return run_dir / "summary.md", summary
