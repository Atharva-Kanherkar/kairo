# The live impact leg: what a dropped field actually costs a real agent run.
#
# The capture matrix proves a field never reaches the backend. That is a wire
# fact. It is not yet an impact claim, and "gateway X drops output_format" is a
# much weaker sentence than "through gateway X, N of 5 runs returned a markdown
# fence the caller cannot json.loads, against 0 of 5 direct".
#
# This leg is opt-in (--live), key-gated, budget-gated, and trial-capped. It
# relaunches each gateway with its backend pointed at a real provider instead
# of the capture mock, sends the same probe both ways, and scores an observable
# consequence rather than the presence of a field.
#
# Keys are read from the environment and never written anywhere. Every
# transcript this leg produces goes through the same redaction as the capture
# leg before it touches disk.
from __future__ import annotations

import json
import os
import time

from tools.sweep import gateways as gw
from tools.sweep import probes as P
from tools.sweep.sweep import parse_json, post, redact

PROVIDER_BASE = os.environ.get("KAIRO_LIVE_BASE", "https://api.openai.com/v1")
PROVIDER_KEY_ENV = os.environ.get("KAIRO_LIVE_KEY_ENV", "OPENAI_API_KEY")
PROVIDER_MODEL = os.environ.get("KAIRO_LIVE_MODEL", "gpt-4o-mini")


def _live_gateway(cls, workdir, port_offset=100):
    """Same adapter, backend pointed at a real provider instead of the mock."""

    class Live(cls):
        name = cls.name
        port = cls.port + port_offset

        def mock_base(self, suffix="/v1"):
            return PROVIDER_BASE

        def launch_env(self):
            env = dict(os.environ)
            env["OPENAI_API_KEY"] = os.environ.get(PROVIDER_KEY_ENV, "")
            env["OPENAI_BASE_URL"] = PROVIDER_BASE
            return env

    Live.__name__ = f"Live{cls.__name__}"
    return Live(workdir)


# ---------------------------------------------------------------- observables
# Each returns (label, anthropic_body, openai_control_body, score_fn).
# score_fn(client_response) -> True when the run came out RIGHT.


def _text_of(resp):
    blocks = resp.get("content") or []
    if isinstance(blocks, list):
        return " ".join(b.get("text", "") for b in blocks
                        if isinstance(b, dict) and b.get("type") == "text")
    choices = resp.get("choices") or []
    if choices:
        return (choices[0].get("message") or {}).get("content") or ""
    return json.dumps(resp)


def _tool_blocks(resp):
    blocks = resp.get("content") or []
    if isinstance(blocks, list):
        n = sum(1 for b in blocks if isinstance(b, dict)
                and b.get("type") == "tool_use")
        if n:
            return n
    choices = resp.get("choices") or []
    if choices:
        return len((choices[0].get("message") or {}).get("tool_calls") or [])
    return 0


def obs_json_schema(model):
    ask = "Return the city Paris and ok true."
    a_body = {
        "model": model, "max_tokens": 200,
        "messages": [{"role": "user", "content": ask}],
        "output_config": {"format": {"type": "json_schema", "name": "city",
                                     "schema": P.SCHEMA}},
    }
    o_body = {
        "model": PROVIDER_MODEL, "max_tokens": 200,
        "messages": [{"role": "user", "content": ask}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "city", "schema": P.SCHEMA,
                                            "strict": True}},
    }

    def score(resp):
        text = _text_of(resp).strip()
        try:
            obj = json.loads(text)
        except Exception:
            return False  # a fence, prose, or truncated JSON
        return isinstance(obj, dict) and "city" in obj and "ok" in obj

    return ("output_config.format: caller can json.loads the body",
            a_body, o_body, score)


def obs_stop_sequences(model):
    ask = "Count from 1 to 10, comma separated, digits only, no other words."
    a_body = {
        "model": model, "max_tokens": 200,
        "messages": [{"role": "user", "content": ask}],
        "stop_sequences": ["5"],
    }
    o_body = {
        "model": PROVIDER_MODEL, "max_tokens": 200,
        "messages": [{"role": "user", "content": ask}],
        "stop": ["5"],
    }

    def score(resp):
        # "6 is absent" alone is satisfied by an empty body, a refusal, or
        # prose, which would report the stop sequence as honored when it was
        # ignored. Require positive evidence that generation happened AND
        # stopped: an early digit present, the stop token and everything
        # after it absent.
        text = _text_of(resp).strip()
        if not text:
            return False
        generated = any(d in text for d in ("1", "2", "3", "4"))
        stopped = not any(d in text for d in ("5", "6", "7", "8", "9", "10"))
        return generated and stopped

    return ("stop_sequences: generation runs and stops at the sequence",
            a_body, o_body, score)


def obs_parallel(model):
    tools = [
        {"name": "get_weather", "description": "Weather for a city",
         "input_schema": {"type": "object",
                          "properties": {"city": {"type": "string"}},
                          "required": ["city"]}},
        {"name": "get_time", "description": "Current time in a city",
         "input_schema": {"type": "object",
                          "properties": {"city": {"type": "string"}},
                          "required": ["city"]}},
    ]
    ask = "Get both the weather and the current time for Paris."
    a_body = {
        "model": model, "max_tokens": 400,
        "messages": [{"role": "user", "content": ask}],
        "tools": tools,
        "tool_choice": {"type": "auto", "disable_parallel_tool_use": True},
    }
    o_body = {
        "model": PROVIDER_MODEL, "max_tokens": 400,
        "messages": [{"role": "user", "content": ask}],
        "parallel_tool_calls": False,
        "tools": [{"type": "function",
                   "function": {"name": t["name"], "description": t["description"],
                                "parameters": t["input_schema"]}} for t in tools],
    }

    def score(resp):
        return _tool_blocks(resp) <= 1  # the flag was honored

    return ("disable_parallel_tool_use: at most one tool call per turn",
            a_body, o_body, score)


OBSERVABLES = {
    "req.output_config.format": obs_json_schema,
    "req.output_format.legacy": obs_json_schema,
    "req.stop_sequences": obs_stop_sequences,
    "req.disable_parallel_tool_use": obs_parallel,
}


# ---------------------------------------------------------------- runner

def run_live(sweep, capture_gateways, budget, args):
    notes = []
    key = os.environ.get(PROVIDER_KEY_ENV, "")
    if not key:
        notes.append(
            f"Live impact leg not run: {PROVIDER_KEY_ENV} is unset. The matrix "
            "measures what is forwarded, not what the loss costs.")
        return notes

    # Only bother with fields this run actually saw dropped somewhere.
    targets = {}
    for (gname, pid), cell in sweep.cells.items():
        if pid in OBSERVABLES and cell["verdict"] in (P.DROPPED, P.MANGLED):
            targets.setdefault(gname, []).append(pid)
    if not targets:
        notes.append(
            "Live impact leg had nothing to measure: no observable field came "
            "back dropped in the capture matrix.")
        return notes

    deadline = budget.phase_deadline(0.95)
    results = []

    # Direct-provider control first: one baseline, reused across gateways.
    baselines = {}
    for pid in {p for v in targets.values() for p in v}:
        label, _, o_body, score = OBSERVABLES[pid](PROVIDER_MODEL)
        wins = 0
        runs = 0
        for _ in range(args.live_trials):
            if time.monotonic() > deadline:
                break
            st, raw = post(f"{PROVIDER_BASE}/chat/completions", o_body,
                           {"content-type": "application/json",
                            "authorization": f"Bearer {key}"}, timeout=60)
            runs += 1
            if st == 200 and score(parse_json(raw)):
                wins += 1
        baselines[pid] = (label, wins, runs)

    by_cls = {g.name: type(g) for g in capture_gateways}
    for gname, pids in targets.items():
        cls = by_cls.get(gname)
        if not cls:
            continue
        live = _live_gateway(cls.__mro__[0] if cls.__name__.startswith("Live")
                             else cls, sweep.workdir)
        if not live.start():
            notes.append(
                f"Live leg skipped {gname}: {live.skip_reason or 'would not start'}")
            continue
        try:
            for pid in pids:
                label, a_body, _, score = OBSERVABLES[pid](live.model)
                wins = 0
                runs = 0
                for _ in range(args.live_trials):
                    if time.monotonic() > deadline:
                        break
                    st, raw = post(live.base_url() + live.messages_path,
                                   live.shape_body(a_body),
                                   live.messages_headers(), timeout=60)
                    runs += 1
                    resp = parse_json(raw)
                    if st == 200 and score(resp):
                        wins += 1
                    with open(os.path.join(sweep.outdir,
                                           f"live--{gname}--{pid}.jsonl"), "a") as f:
                        f.write(redact(json.dumps(
                            {"status": st, "response": resp})) + "\n")
                b_label, b_wins, b_runs = baselines.get(pid, (label, 0, 0))
                results.append({
                    "gateway": gname, "probe": pid, "observable": label,
                    "gateway_ok": f"{wins}/{runs}",
                    "direct_ok": f"{b_wins}/{b_runs}",
                })
        finally:
            live.stop()

    if results:
        notes.append(
            "Live impact leg, "
            f"{PROVIDER_MODEL} via {PROVIDER_BASE}, {args.live_trials} trials "
            "per cell:")
        for r in results:
            notes.append(
                f"  {r['gateway']} {r['observable']}: through the gateway "
                f"{r['gateway_ok']}, direct to the provider {r['direct_ok']}")
        notes.append(
            "A gateway column well below the direct column is the impact "
            "number the wire evidence alone cannot give you.")
        with open(os.path.join(sweep.outdir, "live-results.json"), "w") as f:
            json.dump(results, f, indent=2)
    return notes
