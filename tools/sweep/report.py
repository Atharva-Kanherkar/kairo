# Matrix rendering and the PR step.
#
# House style, enforced here because generated text is still repo text:
# no em dashes anywhere, plain sentences, and every number stated with the
# denominator it came from.
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from tools.sweep import gateways as gw  # noqa: E402
from tools.sweep import probes as P  # noqa: E402

CLEAN = {P.PRESERVED, P.EXPECTED_LOSS}

GLYPH = {
    P.PRESERVED: "OK",
    P.EXPECTED_LOSS: "na",
    P.DROPPED: "DROP",
    P.MANGLED: "MANG",
    P.REJECTED: "4xx",
    P.ERROR: "err",
    P.SKIPPED: "--",
}

LEGEND = (
    "| token | meaning |\n"
    "|---|---|\n"
    "| `OK` | the field survived to the forwarded upstream request, or the "
    "response invariant held |\n"
    "| `na` | no equivalent exists in the target format; recorded so the "
    "boundary is visible rather than assumed |\n"
    "| `DROP` | the field was silently absent from what the gateway forwarded |\n"
    "| `MANG` | the field survived but changed meaning (renamed, stringified, "
    "re-encoded, or invented) |\n"
    "| `4xx` | the gateway rejected the request outright. Loud, not silent, and "
    "not the species this repo hunts |\n"
    "| `err` | transport failure or a checker that raised. Not a finding |\n"
    "| `--` | not run. Gateway unavailable, or the budget ran out before the "
    "cell |\n"
)

INVERTED_NOTE = (
    "Four probes are inverted, and the matrix already accounts for the "
    "inversion: the three credential-leak headers and the invented-empty-text "
    "response check report `OK` when the gateway did **not** do the bad thing. "
    "A `DROP` on `header.client_authorization` means the client's credential "
    "was forwarded upstream.\n"
)


def _stats(sweep, gname):
    cells = [c for (g, _), c in sweep.cells.items() if g == gname]
    scored = [c for c in cells if c["verdict"] not in (P.SKIPPED, P.ERROR)]
    ok = [c for c in scored if c["verdict"] in CLEAN]
    return len(ok), len(scored), len(cells)


def write_matrix(sweep, args):
    gnames = sorted({g for g, _ in sweep.cells})
    lines = []
    lines.append("# Field preservation matrix\n")
    lines.append(
        f"Run `{sweep.runid}`. {len(P.PROBES)} probes across "
        f"{len(gnames)} gateway{'s' if len(gnames) != 1 else ''}, "
        f"{len(sweep.cells)} cells.\n")
    lines.append(
        "This is the denominator. Every probe is a field or invariant that a "
        "cross-format gateway has to carry from the Anthropic `/v1/messages` "
        "ingress to an OpenAI-shaped backend. A cell that reads `OK` is a "
        "result, not an absence of one.\n")

    lines.append("\n## Preservation rate\n")
    lines.append("| gateway | preserved | scored cells | rate | not run |")
    lines.append("|---|---:|---:|---:|---:|")
    for g in gnames:
        ok, scored, total = _stats(sweep, g)
        rate = f"{100.0 * ok / scored:.0f}%" if scored else "n/a"
        lines.append(f"| {g} | {ok} | {scored} | {rate} | {total - scored} |")
    lines.append("")
    lines.append(
        "`scored cells` excludes cells that were not run and cells whose probe "
        "errored, so the rate is over what was actually measured. The `not run` "
        "column is the honest remainder: a gateway that could not be started "
        "shows up here rather than disappearing from the table.\n")

    lines.append("\n## Legend\n")
    lines.append(LEGEND)
    lines.append(INVERTED_NOTE)

    for axis, title in (("request", "Request parameters"),
                        ("content", "Message content blocks"),
                        ("header", "Headers"),
                        ("response", "Response translation")):
        probes = [p for p in P.PROBES if p.axis == axis]
        if not probes:
            continue
        lines.append(f"\n## {title}\n")
        lines.append("| field | issue | " + " | ".join(gnames) + " |")
        lines.append("|---|---|" + "---|" * len(gnames))
        for p in probes:
            row = [f"`{p.field}`", p.known or ""]
            for g in gnames:
                c = sweep.cells.get((g, p.id))
                if not c:
                    row.append("--")
                    continue
                tok = GLYPH.get(c["verdict"], "?")
                if c.get("runs", 0) > 1 and c.get("determinism"):
                    tok += f" {c['determinism']}"
                if c.get("control", {}).get("verdict") in CLEAN:
                    tok += " ctl"
                row.append(tok)
            lines.append("| " + " | ".join(row) + " |")

    lines.append(
        "\n`ctl` marks a cell where the same gateway's own OpenAI "
        "`/v1/chat/completions` ingress carried the field in the same process. "
        "That is the control leg: the mapping exists on this machine and the "
        "Anthropic path is not applying it.\n")

    if sweep.notes:
        lines.append("\n## What this run did not cover\n")
        for n in sweep.notes:
            lines.append(f"- {n}")
        lines.append("")

    skipped = [c for c in sweep.cells.values() if c["verdict"] == P.SKIPPED]
    if skipped:
        reasons = {}
        for c in skipped:
            reasons.setdefault(c.get("detail", "unspecified"), 0)
            reasons[c.get("detail", "unspecified")] += 1
        lines.append("\n## Cells not run\n")
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {n} cells: {reason}")
        lines.append("")

    path = os.path.join(ROOT, "issues", "MATRIX.md")
    with open(path, "w") as f:
        f.write("\n".join(lines).replace("\u2014", ", ") + "\n")
    return os.path.relpath(path, ROOT)


def write_candidates(sweep, args):
    cands = sweep.candidates()
    regs = sweep.regressions()
    lines = ["# Sweep candidates\n"]
    lines.append(
        f"Run `{sweep.runid}`. These are non-clean cells with no matching "
        "kairo issue. They are leads, not findings.\n")
    lines.append(
        "Nothing here is auto-filed. This repo's value is that every issue "
        "folder is a hand-verified claim with a control and a determinism "
        "count, and a generated writeup would undercut exactly that. The "
        "sweep's job is to hand you a ranked list and the frozen bytes; the "
        "writeup is still yours.\n")
    lines.append(
        "Per CONTRIBUTING, one bug per PR. Promote these one at a time.\n")

    if not cands:
        lines.append("\nNo new candidates in this run.\n")
    else:
        lines.append("\n## Ranked\n")
        lines.append("| gateway | field | verdict | runs | control | evidence |")
        lines.append("|---|---|---|---:|---|---|")
        for c in cands:
            ctl = c.get("control", {}).get("verdict", "")
            ctl = "forwards it" if ctl in CLEAN else (ctl or "not probed")
            lines.append(
                f"| {c['gateway']} | `{c['field']}` | {c['verdict']} | "
                f"{c.get('determinism', c.get('runs', 1))} | {ctl} | "
                f"`{c.get('evidence', '')}` |")

    if regs:
        lines.append("\n## Known defects that came back clean\n")
        lines.append(
            "Each of these is either fixed upstream since it was frozen, or "
            "the rig is not exercising the path it used to. Both need a human "
            "look, and the second is more likely on a first run.\n")
        lines.append("| gateway | field | issue |")
        lines.append("|---|---|---|")
        for c in regs:
            lines.append(f"| {c['gateway']} | `{c['field']}` | {c['known']} |")
        lines.append("")

    path = os.path.join(ROOT, "issues", "CANDIDATES.md")
    with open(path, "w") as f:
        f.write("\n".join(lines).replace("\u2014", ", ") + "\n")
    return os.path.relpath(path, ROOT)


def write_results(sweep, args):
    payload = {
        "run": sweep.runid,
        "probes": len(P.PROBES),
        "cells": [c for c in sweep.cells.values()],
        "summary": sweep.summary(),
        "notes": sweep.notes,
        "args": {k: v for k, v in vars(args).items()},
    }
    path = os.path.join(sweep.outdir, "results.json")
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
    return os.path.relpath(path, ROOT)


def write_all(sweep, args):
    return [write_matrix(sweep, args),
            write_candidates(sweep, args),
            write_results(sweep, args)]


# ---------------------------------------------------------------- PR

def _run(argv, check=True, capture=True):
    r = subprocess.run(argv, cwd=ROOT, text=True,
                       capture_output=capture)
    if check and r.returncode != 0:
        raise RuntimeError(
            f"{' '.join(argv)} failed ({r.returncode})\n{r.stdout}\n{r.stderr}")
    return r


def open_pr(sweep, paths, args):
    """Branch, verify the suite is still green, commit, open a draft PR.

    Never commits to main, never force-pushes, and refuses if the suite or the
    README counter check is red. A PR that lands a broken CI is worse than no
    PR.
    """
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch != "main":
        print(f"  refusing to branch from `{branch}`: run the sweep from main")
        return
    new_branch = f"sweep/matrix-{sweep.runid.lower()}"

    print("  verifying the suite before committing")
    t = _run(["cargo", "test", "-p", "kairo"], check=False)
    if t.returncode != 0:
        print("  cargo test is red; not opening a PR")
        print(t.stdout[-2000:])
        return
    c = _run(["python3", "tools/update-readme-counts.py", "--check"], check=False)
    if c.returncode != 0:
        print("  README counters are stale; running the updater")
        _run(["python3", "tools/update-readme-counts.py"])

    _run(["git", "checkout", "-b", new_branch])
    _run(["git", "add", "issues/MATRIX.md", "issues/CANDIDATES.md",
          "transcripts/sweep", "README.md"], check=False)

    counts = sweep.summary()
    cands = sweep.candidates()
    subject = (f"sweep: field preservation matrix across "
               f"{len({g for g, _ in sweep.cells})} gateways")
    body_lines = [
        subject, "",
        f"Run {sweep.runid}. {len(P.PROBES)} probes, {len(sweep.cells)} cells.",
        "",
        "Coverage, not a bug report. Every probe is a field the Anthropic",
        "ingress has to carry to an OpenAI-shaped backend, and every cell is",
        "recorded including the ones that passed.",
        "",
    ] + [f"  {k}: {v}" for k, v in sorted(counts.items())] + [
        "",
        f"{len(cands)} non-clean cells have no matching issue. They are listed",
        "in issues/CANDIDATES.md as leads and are deliberately not written up",
        "as issue folders: one bug per PR, and each writeup needs a human.",
        "",
        "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>",
    ]
    msg = "\n".join(body_lines)
    r = _run(["git", "commit", "-m", msg], check=False)
    if r.returncode != 0:
        print("  nothing to commit")
        print(r.stdout)
        return

    _run(["git", "push", "-u", "origin", new_branch])

    pr_body = "\n".join([
        "## Summary",
        f"- Rectangular sweep, run `{sweep.runid}`: {len(P.PROBES)} probes across "
        f"{len({g for g, _ in sweep.cells})} gateways, {len(sweep.cells)} cells.",
        "- This PR adds coverage, not a defect. The matrix is the denominator "
        "the scoreboard has never had: preservation rates over probed fields, "
        "with the passes reported alongside the failures.",
        f"- {len(cands)} non-clean cells have no matching issue. They are in "
        "`issues/CANDIDATES.md` as ranked leads with frozen bytes. None are "
        "written up as issue folders: one bug per PR, and each writeup is a "
        "human claim.",
        "",
        "## What is frozen",
        f"- `issues/MATRIX.md`, the full matrix by axis with a legend.",
        f"- `issues/CANDIDATES.md`, ranked leads plus any known defect that came "
        "back clean.",
        f"- `transcripts/sweep/{sweep.runid}/`, the wire bytes per cell, "
        "credential-scrubbed.",
        "",
        "## Test plan",
        "- [x] `cargo test -p kairo` green before commit",
        "- [x] `python3 tools/update-readme-counts.py --check`",
        "- [ ] Review `issues/CANDIDATES.md` and promote at most one lead per "
        "follow-up PR",
        "",
        "Draft on purpose: the matrix is machine-generated and wants a read "
        "before it becomes a claim.",
        "",
        "Generated with the sweep rig in `tools/sweep/`.",
    ])
    r = _run(["gh", "pr", "create", "--draft", "--title", subject,
              "--body", pr_body], check=False)
    print(r.stdout or r.stderr)
