"""Rewrite the README "Upstream activity" block from live GitHub state.

Lists every issue and pull request the maintainer authored in the upstream
projects kairo tests, with its current state. Needs the `gh` CLI, logged in.

  python3 tools/update-upstream-log.py          # rewrite the README block
  python3 tools/update-upstream-log.py --print  # print the block, touch nothing
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
START = "<!-- kairo-upstream:start -->"
END = "<!-- kairo-upstream:end -->"

AUTHOR = "Atharva-Kanherkar"

# (repository, display name, kairo finding numbers per item)
PROJECTS = (
    ("BerriAI/litellm", "LiteLLM", {
        36794: ("020",), 36898: ("024",), 37118: ("041",),
        39721: ("067",), 39723: ("067",),
        40118: ("074",), 40121: ("074",),
        42955: ("085",), 42958: ("085",),
        43153: ("087",), 43159: ("087",),
    }),
    ("NVIDIA-NeMo/Switchyard", "NVIDIA Switchyard", {
        369: ("010",), 370: ("010",), 380: ("007",), 410: ("027",),
        419: ("023",), 420: ("023",), 423: ("025",), 452: ("040",),
        521: ("065",), 523: ("065",), 543: ("063",), 544: ("063",),
        577: ("066",), 622: ("068",), 623: ("068",),
        802: ("083",), 803: ("083",),
    }),
    ("maximhq/bifrost", "Bifrost", {
        6887: ("072",), 6888: ("072",), 7032: ("075",), 7033: ("075",),
        7120: ("077",), 7121: ("077",), 7383: ("082",), 7385: ("082",),
        7560: ("086",), 7561: ("086",),
    }),
    ("ai-dynamo/dynamo", "Dynamo", {15100: ("080",), 15101: ("080",)}),
    ("ogx-ai/ogx", "OGX", {6614: ("081",), 6615: ("081",)}),
    ("agentgateway/agentgateway", "agentgateway", {3681: ("088",)}),
    ("mozilla-ai/any-llm", "any-llm", {
        1311: ("057", "058", "059", "060", "061", "062"),
        1312: ("057", "058", "059", "060", "061", "062"),
    }),
    ("64bit/async-openai", "async-openai", {590: ("088",)}),
)

TAG_RE = re.compile(r"^\s*(\[[a-z ]+\]|\([a-z ]+\))\s*:?\s*", re.IGNORECASE)


def gh_list(kind: str, repo: str) -> list[dict]:
    """Return the maintainer's issues or PRs in one repository, any state."""
    fields = "number,title,state,url,createdAt"
    if kind == "issue":
        fields += ",stateReason"
    out = subprocess.check_output(
        [
            "gh", kind, "list",
            "--repo", repo,
            "--author", AUTHOR,
            "--state", "all",
            "--limit", "200",
            "--json", fields,
        ],
        text=True,
    )
    items = json.loads(out)
    for item in items:
        item["state"] = normalize_state(item)
    return items


def normalize_state(item: dict) -> str:
    """Collapse GitHub's state and stateReason into one display state."""
    state = item["state"].lower()
    if state == "closed" and item.get("stateReason") == "NOT_PLANNED":
        return "not planned"
    if state == "closed" and item.get("stateReason") == "COMPLETED":
        return "completed"
    return state


def clean_title(title: str) -> str:
    """Drop a leading `[bug]:` style tag and escape table-breaking characters."""
    title = TAG_RE.sub("", title, count=1).strip()
    title = title.replace(chr(0x2014), "-").replace(chr(0x2013), "-")
    return title.replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def folder_links(numbers: tuple[str, ...], folders: dict[str, str]) -> str:
    """Link kairo findings, collapsing consecutive numbers into `a-b`."""

    def link(num: str) -> str:
        return f"[{num}]({folders[num]})" if num in folders else num

    runs: list[list[str]] = []
    for num in sorted(numbers):
        if runs and int(num) == int(runs[-1][-1]) + 1:
            runs[-1].append(num)
        else:
            runs.append([num])
    return ", ".join(
        link(run[0]) if len(run) == 1 else f"{link(run[0])}-{link(run[-1])}"
        for run in runs
    )


def row(item: dict, findings: tuple[str, ...], folders: dict[str, str]) -> str:
    state = item["state"]
    return (
        f"| {state} "
        f"| [#{item['number']}]({item['url']}) "
        f"| {clean_title(item['title'])} "
        f"| {folder_links(findings, folders) if findings else ''} "
        f"| {item['createdAt'][:10]} |"
    )


def table(items: list[dict], mapping: dict, folders: dict[str, str]) -> list[str]:
    lines = [
        "| State | # | Title | Finding | Opened |",
        "|---|---|---|---|---|",
    ]
    for item in sorted(items, key=lambda i: i["number"], reverse=True):
        lines.append(row(item, mapping.get(item["number"], ()), folders))
    return lines


def count(items: list[dict], *states: str) -> int:
    return sum(1 for i in items if i["state"] in states)


def render(data: dict[str, dict], folders: dict[str, str], today: str) -> str:
    """Build the Markdown block from `{repo: {"prs": [...], "issues": [...]}}`."""
    lines = [
        "| Project | Issues open | Issues closed | PRs merged | PRs open | PRs closed |",
        "|---|--:|--:|--:|--:|--:|",
    ]
    totals = [0] * 5
    for repo, name, _ in PROJECTS:
        prs, issues = data[repo]["prs"], data[repo]["issues"]
        cells = [
            count(issues, "open"), count(issues, "completed", "not planned"),
            count(prs, "merged"), count(prs, "open"), count(prs, "closed"),
        ]
        totals = [a + b for a, b in zip(totals, cells)]
        lines.append(
            f"| [{name}](https://github.com/{repo}) | "
            + " | ".join(str(c) for c in cells) + " |"
        )
    lines.append("| **Total** | " + " | ".join(f"**{t}**" for t in totals) + " |")
    lines += [
        "",
        f"State checked {today}. "
        "Refresh with `python3 tools/update-upstream-log.py`.",
        "",
    ]
    for repo, name, mapping in PROJECTS:
        prs, issues = data[repo]["prs"], data[repo]["issues"]
        lines += [
            "<details open>",
            f"<summary><b>{name}</b> "
            f"({len(issues)} issues, {len(prs)} pull requests)</summary>",
            "",
        ]
        if prs:
            lines += ["**Pull requests**", ""] + table(prs, mapping, folders) + [""]
        if issues:
            lines += ["**Issues**", ""] + table(issues, mapping, folders) + [""]
        lines += ["</details>", ""]
    return "\n".join(lines).rstrip("\n")


def find_folders() -> dict[str, str]:
    """Map `087` to `issues/087-slug` for every writeup folder on disk."""
    return {
        p.name.split("-", 1)[0]: f"issues/{p.name}"
        for p in sorted((ROOT / "issues").iterdir())
        if p.is_dir() and re.match(r"\d{3}-", p.name)
    }


def splice(text: str, block: str) -> str:
    """Replace the marked block in `text`, keeping the markers."""
    start, end = text.find(START), text.find(END)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(f"README.md is missing the {START} / {END} markers")
    return text[: start + len(START)] + "\n" + block + "\n" + text[end:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--print", action="store_true", dest="dry")
    args = parser.parse_args()

    data = {
        repo: {"prs": gh_list("pr", repo), "issues": gh_list("issue", repo)}
        for repo, _, _ in PROJECTS
    }
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    block = render(data, find_folders(), today)
    if args.dry:
        print(block)
        return 0
    README.write_text(splice(README.read_text(), block))
    return 0


if __name__ == "__main__":
    sys.exit(main())
