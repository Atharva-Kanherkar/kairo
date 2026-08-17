#!/usr/bin/env python3
"""Rectangular field-preservation sweep across every gateway under test.

Runs the whole probe corpus against every gateway, records one verdict per
(gateway, probe) cell, freezes the wire bytes, and writes the coverage matrix.
Optionally opens a draft PR with the result.

The point is the denominator. "34 defects" has no base rate; "of 44 probed
fields across 5 gateways, N preserved and M dropped" does. Cells that come
back clean are part of the result and are reported.

Phases, deadline-driven (default 60 minutes):
  0  preflight        start the mock, attach or launch each gateway
  1  rectangular      every gateway x every probe, one pass      (~60% of budget)
  2  determinism      repeat the non-clean cells to N runs       (~20%)
  3  live impact      real keys, only with --live                (~15%)
  4  report + PR      always reserved, never skipped             (~5%)

Phase 4's budget is reserved up front, so a sweep that runs out of time still
writes its matrix. A timeout loses depth, never results.

Examples:
  python3 -m tools.sweep.sweep --minutes 60
  python3 -m tools.sweep.sweep --minutes 60 --open-pr
  python3 -m tools.sweep.sweep --gateways litellm,bifrost --repeats 5
  python3 -m tools.sweep.sweep --dry-run          # corpus self-check, no network
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from tools.sweep import gateways as gw  # noqa: E402
from tools.sweep import probes as P  # noqa: E402
from tools.sweep.mock import serve  # noqa: E402

CLEAN = {P.PRESERVED, P.EXPECTED_LOSS}

# ---------------------------------------------------------------- redaction

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AIza[A-Za-z0-9_\-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),
]

ENV_SECRET_KEYS = [
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
    "OPENROUTER_API_KEY", "AXONHUB_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY",
    "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "GITHUB_TOKEN",
]


def redact(text):
    """Scrub anything that looks like a live credential.

    The sweep's own leak probes use synthetic markers (CLIENTSECRET-3311 and
    friends), so a gateway that forwards the client's credential is still
    caught without a real key ever entering a transcript. This function is the
    belt to that braces: the live leg does use real keys, and a gateway under
    test may echo one into a recorded header.
    """
    for key in ENV_SECRET_KEYS:
        val = os.environ.get(key)
        if val and len(val) >= 8:
            text = text.replace(val, f"[REDACTED:{key}]")
    for pat in SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


# ---------------------------------------------------------------- http

def post(url, body, headers, timeout=25):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # connection reset, timeout, DNS, ...
        return 0, str(e).encode()


def parse_json(raw):
    try:
        return json.loads(raw)
    except Exception:
        # streaming probes come back as SSE; pull the JSON payloads out
        events = []
        for line in raw.decode("utf-8", "replace").splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    try:
                        events.append(json.loads(chunk))
                    except Exception:
                        pass
        return {"_sse": events} if events else {"_raw": raw.decode("utf-8", "replace")}


# ---------------------------------------------------------------- deadline

class Budget:
    def __init__(self, total_s, reserve_s):
        self.start = time.monotonic()
        self.total = total_s
        self.reserve = reserve_s

    def elapsed(self):
        return time.monotonic() - self.start

    def left(self):
        return max(0.0, self.total - self.reserve - self.elapsed())

    def expired(self):
        return self.left() <= 0

    def phase_deadline(self, frac):
        return self.start + (self.total - self.reserve) * frac


# ---------------------------------------------------------------- runner

class Sweep:
    def __init__(self, args):
        self.args = args
        self.runid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.outdir = os.path.join(ROOT, "transcripts", "sweep", self.runid)
        self.workdir = os.path.join(self.outdir, "_work")
        os.makedirs(self.workdir, exist_ok=True)
        self.capture = os.path.join(self.workdir, "upstream.jsonl")
        self.canned = os.path.join(self.workdir, "canned.json")
        open(self.capture, "w").close()
        self._stage_canned(None)
        self.offset = 0
        self.cells = {}  # (gateway, probe_id) -> cell dict
        self.notes = []

    # -- mock -----------------------------------------------------------

    def _stage_canned(self, obj):
        with open(self.canned, "w") as f:
            json.dump(obj if obj is not None else P.PROBES and
                      {"id": "chatcmpl-sweep", "object": "chat.completion",
                       "created": 0, "model": "captured",
                       "choices": [{"index": 0, "finish_reason": "stop",
                                    "message": {"role": "assistant",
                                                "content": "ok"}}],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                 "total_tokens": 2}}, f)

    def start_mock(self):
        t = threading.Thread(
            target=serve, args=(self.args.mock_port, self.capture, self.canned),
            daemon=True)
        t.start()
        if not gw.wait_for_port(self.args.mock_port, 10):
            raise SystemExit(f"capture mock never came up on :{self.args.mock_port}")

    def drain(self):
        """Return upstream records written since the last call."""
        with open(self.capture) as f:
            f.seek(self.offset)
            chunk = f.read()
            self.offset = f.tell()
        out = []
        for line in chunk.splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out

    # -- one cell -------------------------------------------------------

    def run_cell(self, g, probe):
        self._stage_canned(probe.canned)
        self.drain()  # discard anything left over from the previous cell

        body = g.shape_body(probe.body)
        headers = g.messages_headers(probe.headers)
        url = g.base_url() + g.messages_path
        status, raw = post(url, body, headers, timeout=self.args.timeout)
        recs = self.drain()

        cell = {
            "gateway": g.name, "probe": probe.id, "field": probe.field,
            "axis": probe.axis, "severity": probe.severity, "known": probe.known,
            "status": status, "upstream_records": len(recs),
        }

        if status == 0:
            cell["verdict"] = P.ERROR
            cell["detail"] = raw.decode("utf-8", "replace")[:200]
            return cell
        if status >= 400:
            cell["verdict"] = P.REJECTED
            cell["detail"] = raw.decode("utf-8", "replace")[:200]
            return cell
        if not recs and probe.axis != "response":
            cell["verdict"] = P.ERROR
            cell["detail"] = "gateway answered 2xx but forwarded nothing upstream"
            return cell

        fwd = recs[-1].get("body") if recs else {}
        hdr = recs[-1].get("headers") if recs else {}
        cli = parse_json(raw)
        try:
            cell["verdict"] = probe.expect(fwd or {}, hdr or {}, cli or {})
        except Exception as e:
            cell["verdict"] = P.ERROR
            cell["detail"] = f"checker raised: {type(e).__name__}: {e}"
            return cell

        cell["forwarded_keys"] = sorted((fwd or {}).keys()) if isinstance(fwd, dict) else []
        cell["evidence"] = self.freeze(g, probe, recs, cli)

        # Same-process control: if the gateway's OpenAI ingress carries the
        # field, the mapping exists and the Anthropic path simply is not
        # applying it. That is what pins blame on the translation layer.
        if probe.control and cell["verdict"] not in CLEAN:
            cell["control"] = self.run_control(g, probe)
        return cell

    def run_control(self, g, probe):
        self.drain()
        body = g.shape_body(probe.control)
        status, raw = post(g.base_url() + g.chat_path, body,
                           g.chat_headers(), timeout=self.args.timeout)
        recs = self.drain()
        if status >= 400 or not recs:
            return {"verdict": P.SKIPPED, "status": status,
                    "detail": "OpenAI ingress unavailable on this gateway"}
        fwd = recs[-1].get("body") or {}
        hdr = recs[-1].get("headers") or {}
        try:
            v = probe.expect(fwd, hdr, parse_json(raw))
        except Exception as e:
            v = P.ERROR
            return {"verdict": v, "detail": str(e)}
        return {"verdict": v, "status": status}

    def freeze(self, g, probe, recs, cli):
        """Write the wire bytes for this cell. Returns the relative path."""
        safe = probe.id.replace(".", "-")
        rel = os.path.join("transcripts", "sweep", self.runid,
                           f"{g.name}--{safe}.jsonl")
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            for r in recs:
                f.write(redact(json.dumps(r)) + "\n")
            f.write(redact(json.dumps({"_client_response": cli})) + "\n")
        return rel

    # -- phases ---------------------------------------------------------

    def phase_rectangular(self, gateways, budget):
        deadline = budget.phase_deadline(0.60)
        total = len(gateways) * len(P.PROBES)
        done = 0
        for g in gateways:
            for probe in P.PROBES:
                key = (g.name, probe.id)
                if g.skip_reason:
                    self.cells[key] = {
                        "gateway": g.name, "probe": probe.id, "field": probe.field,
                        "axis": probe.axis, "severity": probe.severity,
                        "known": probe.known, "verdict": P.SKIPPED,
                        "detail": g.skip_reason, "runs": 0,
                    }
                    continue
                if time.monotonic() > deadline:
                    self.cells[key] = {
                        "gateway": g.name, "probe": probe.id, "field": probe.field,
                        "axis": probe.axis, "severity": probe.severity,
                        "known": probe.known, "verdict": P.SKIPPED,
                        "detail": "phase 1 budget exhausted before this cell",
                        "runs": 0,
                    }
                    continue
                cell = self.run_cell(g, probe)
                cell["runs"] = 1
                cell["verdicts"] = [cell["verdict"]]
                self.cells[key] = cell
                done += 1
                if done % 10 == 0:
                    log(f"  phase 1: {done}/{total} cells, "
                        f"{budget.left():.0f}s left")

    def phase_determinism(self, gateways, budget):
        """Repeat the non-clean cells. A one-off is a lead, not a proof."""
        deadline = budget.phase_deadline(0.80)
        targets = [(k, c) for k, c in self.cells.items()
                   if c["verdict"] not in CLEAN and c["verdict"] != P.SKIPPED]
        targets.sort(key=lambda kc: 0 if kc[1]["severity"] == "high" else 1)
        by_name = {g.name: g for g in gateways}
        for (gname, pid), cell in targets:
            g = by_name.get(gname)
            probe = P.by_id(pid)
            if not g or not probe or g.skip_reason:
                continue
            while cell["runs"] < self.args.repeats:
                if time.monotonic() > deadline:
                    cell["detail"] = (cell.get("detail", "") +
                                      f" [only {cell['runs']}/{self.args.repeats} "
                                      "runs: phase 2 budget exhausted]").strip()
                    break
                again = self.run_cell(g, probe)
                cell["verdicts"].append(again["verdict"])
                cell["runs"] += 1
            cell["stable"] = len(set(cell["verdicts"])) == 1
            cell["determinism"] = (
                f"{cell['verdicts'].count(cell['verdict'])}/{cell['runs']}")

    # -- reporting ------------------------------------------------------

    def summary(self):
        counts = {}
        for c in self.cells.values():
            counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1
        return counts

    def candidates(self):
        """Non-clean cells with no matching kairo issue: the new leads."""
        out = []
        for c in self.cells.values():
            if c["verdict"] in CLEAN or c["verdict"] == P.SKIPPED:
                continue
            if c["known"]:
                continue
            out.append(c)
        out.sort(key=lambda c: (0 if c["severity"] == "high" else 1, c["gateway"]))
        return out

    def regressions(self):
        """Known defects that came back clean: either fixed upstream, or the
        rig is lying. Both are worth a human look, and neither is automatic."""
        return [c for c in self.cells.values()
                if c["known"] and c["verdict"] in CLEAN]


def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()


# ---------------------------------------------------------------- entry

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--minutes", type=float, default=60.0)
    ap.add_argument("--repeats", type=int, default=5,
                    help="runs per non-clean cell in phase 2 (repo convention: 5)")
    ap.add_argument("--gateways", default="",
                    help="comma list; default is all five")
    ap.add_argument("--mock-port", type=int, default=gw.MOCK_PORT)
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("--live", action="store_true",
                    help="run the live impact leg with real provider keys")
    ap.add_argument("--live-trials", type=int, default=5)
    ap.add_argument("--open-pr", action="store_true",
                    help="commit to a branch and open a draft PR")
    ap.add_argument("--dry-run", action="store_true",
                    help="self-check the corpus and exit; no network, no gateways")
    args = ap.parse_args()

    if args.dry_run:
        return dry_run()

    names = [n.strip() for n in args.gateways.split(",") if n.strip()]
    budget = Budget(args.minutes * 60, reserve_s=max(120.0, args.minutes * 60 * 0.05))
    sweep = Sweep(args)

    log(f"kairo sweep {sweep.runid}")
    log(f"  {len(P.PROBES)} probes, budget {args.minutes:.0f}m, "
        f"repeats {args.repeats}")
    log(f"  output {os.path.relpath(sweep.outdir, ROOT)}")

    sweep.start_mock()
    gateways = gw.build(names, sweep.workdir, args.mock_port)
    for g in gateways:
        ok = g.start()
        how = "attached" if g.attached else ("launched" if ok else "SKIPPED")
        log(f"  {g.name:<11} :{g.port} {how}"
            + (f"  ({g.skip_reason})" if g.skip_reason else ""))

    try:
        log("phase 1: rectangular sweep")
        sweep.phase_rectangular(gateways, budget)
        log(f"phase 2: determinism to {args.repeats} runs on non-clean cells")
        sweep.phase_determinism(gateways, budget)
        if args.live:
            from tools.sweep.live import run_live
            log("phase 3: live impact")
            sweep.notes.extend(run_live(sweep, gateways, budget, args))
        elif os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY"):
            sweep.notes.append(
                "Live impact leg not run: provider keys are present but --live "
                "was not passed. The matrix measures what is forwarded, not what "
                "it costs a real agent run.")
        else:
            sweep.notes.append(
                "Live impact leg not run: no provider keys in the environment.")
    finally:
        for g in gateways:
            g.stop()

    log("phase 4: report")
    from tools.sweep.report import write_all
    paths = write_all(sweep, args)
    for p in paths:
        log(f"  wrote {p}")

    counts = sweep.summary()
    log("summary: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    log(f"  candidates (new leads): {len(sweep.candidates())}")
    log(f"  known defects now clean: {len(sweep.regressions())}")

    if args.open_pr:
        from tools.sweep.report import open_pr
        open_pr(sweep, paths, args)
    return 0


def dry_run():
    """Validate the corpus without touching the network.

    Every probe is exercised against a synthetic 'perfect' gateway (forwards
    everything) and a synthetic 'null' gateway (forwards nothing), so a checker
    that can never fire, or can never pass, is caught before an hour is spent.
    """
    perfect_fwd = {
        "model": "m", "messages": [{"role": "system", "content": "SYSPROBE-7731"},
                                   {"role": "user", "content": "hi"}],
        "max_tokens": 64, "stop": ["STOPPROBE"], "temperature": 0.3, "top_p": 0.4,
        "top_k": 7, "user": "USERPROBE-4412", "service_tier": "standard_only",
        "stream": True, "reasoning_effort": "low", "reasoning": {"effort": "low"},
        "budget_tokens": 1024, "strict": True, "parallel_tool_calls": False,
        "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
        "mcp_servers": [{}], "context_management": {}, "cache_control": {},
        "response_format": {"type": "json_schema", "json_schema": {"name": "city"}},
        "tools": [{"type": "function", "function": {"name": "get_weather"}}],
        "image_url": {"url": "data:image/png;base64,x"}, "file": {"file_data": "x"},
        "is_error": True, "_probe_echo": ("ERRPROBE THINKPROBE-9021 IDPROBE_8899"),
    }
    perfect_hdr = {"anthropic-beta": "context-management-2025-06-27"}
    perfect_cli = {
        "stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 41},
    }
    # A single fixture cannot satisfy mutually exclusive probes: tool_choice is
    # one field with three different correct shapes. Override per probe.
    FWD_OVERRIDE = {
        "req.tool_choice.auto": {"tool_choice": "auto"},
        "req.tool_choice.any": {"tool_choice": "required"},
    }
    bad = 0
    for p in P.PROBES:
        try:
            v_null = p.expect({}, {}, {})
        except Exception as e:
            print(f"  FAIL {p.id}: checker raised on empty input: {e}")
            bad += 1
            continue
        try:
            cli = dict(perfect_cli)
            if p.id == "resp.finish_reason.length":
                cli["stop_reason"] = "max_tokens"
            if p.id == "resp.finish_reason.content_filter":
                cli["stop_reason"] = "refusal"
            if p.id in ("resp.refusal", "resp.tool_call_id"):
                cli["content"] = [{"type": "text",
                                   "text": "REFUSALPROBE IDECHO_6001"}]
            fwd = dict(perfect_fwd, **FWD_OVERRIDE.get(p.id, {}))
            v_perfect = p.expect(fwd, perfect_hdr, cli)
        except Exception as e:
            print(f"  FAIL {p.id}: checker raised on full input: {e}")
            bad += 1
            continue
        if p.id in P.INVERTED:
            continue  # inverted probes are clean on empty input by design
        if v_null in CLEAN and v_null != P.EXPECTED_LOSS:
            print(f"  WARN {p.id}: passes against a gateway that forwards nothing")
            bad += 1
        if v_perfect not in CLEAN:
            print(f"  WARN {p.id}: fails against a gateway that forwards "
                  f"everything ({v_perfect})")
            bad += 1
    axes = {}
    for p in P.PROBES:
        axes[p.axis] = axes.get(p.axis, 0) + 1
    print(f"corpus: {len(P.PROBES)} probes "
          + ", ".join(f"{k} {v}" for k, v in sorted(axes.items())))
    print(f"  known-issue probes (positive controls): "
          f"{sum(1 for p in P.PROBES if p.known)}")
    print(f"  cells per full sweep across 5 gateways: {len(P.PROBES) * 5}")
    print("dry run clean" if not bad else f"dry run found {bad} checker problems")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
