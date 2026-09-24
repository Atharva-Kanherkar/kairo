"""Coding-agent adapters.

Each adapter turns (task prompt, model) into one shell command that runs as the
unprivileged ``kairo`` user inside the task container, with the working
directory set to ``/work/repo``. The prompt is at ``/tmp/kairo/prompt.md``.
Transcripts go to stdout (captured by the runner) and ``/tmp/kairo-out``.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field

from kairo_bench.config import load_agents_file

PROMPT = "/tmp/kairo/prompt.md"
OUT = "/tmp/kairo-out"
TOOLBOX = "/opt/kairo-agents"


@dataclass(frozen=True)
class AgentSpec:
    name: str
    model: str
    description: str
    keys: tuple[str, ...]
    egress: tuple[str, ...]
    builtin: bool = False

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}" if self.model else self.name

    @property
    def slug(self) -> str:
        return "".join(c if c.isalnum() or c in "-_." else "-" for c in self.label)


@dataclass
class Invocation:
    command: str
    env: dict[str, str] = field(default_factory=dict)
    key_names: list[str] = field(default_factory=list)
    home_dirs: list[str] = field(default_factory=list)  # transcript dirs worth keeping


def known_agents() -> dict[str, dict]:
    return load_agents_file().get("agents", {})


def toolbox_versions() -> dict[str, str]:
    return load_agents_file().get("toolbox", {})


def parse_agent(selector: str) -> AgentSpec:
    name, _, model = selector.partition(":")
    agents = known_agents()
    if name not in agents:
        raise ValueError(f"unknown agent {name!r}; known: {', '.join(agents)}")
    raw = agents[name]
    return AgentSpec(
        name=name,
        model=model or raw.get("model", ""),
        description=raw.get("description", ""),
        keys=tuple(raw.get("keys", [])),
        egress=tuple(raw.get("egress", [])),
        builtin=bool(raw.get("builtin", False)),
    )


def available_keys(spec: AgentSpec, secrets: dict[str, str]) -> list[str]:
    return [k for k in spec.keys if secrets.get(k)]


def _model_flag(flag: str, model: str) -> str:
    return f"{flag} {shlex.quote(model)}" if model else ""


def invocation(spec: AgentSpec, secrets: dict[str, str]) -> Invocation:
    keys = available_keys(spec, secrets)
    common = {
        "HOME": "/home/kairo",
        "KAIRO_MODEL": spec.model,
    }
    prompt = f'"$(cat {PROMPT})"'

    if spec.name == "claude-code":
        return Invocation(
            command=(
                f"claude -p {prompt} {_model_flag('--model', spec.model)} "
                "--dangerously-skip-permissions --output-format stream-json --verbose"
            ),
            env={**common, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1",
                 "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1"},
            key_names=keys,
            home_dirs=[".claude/projects"],
        )

    if spec.name == "codex":
        return Invocation(
            command=(
                "printenv OPENAI_API_KEY | codex login --with-api-key >/dev/null 2>&1; "
                f"codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --json "
                f"-C /work/repo {_model_flag('-m', spec.model)} - < {PROMPT}"
            ),
            env=common,
            key_names=keys,
            home_dirs=[".codex/sessions"],
        )

    if spec.name == "gemini-cli":
        settings = json.dumps({
            "security": {"auth": {"selectedType": "gemini-api-key"}},
            "general": {"disableAutoUpdate": True, "disableUpdateNag": True},
            "privacy": {"usageStatisticsEnabled": False},
        })
        return Invocation(
            command=(
                f"mkdir -p ~/.gemini && printf '%s' {shlex.quote(settings)} > ~/.gemini/settings.json; "
                f"gemini --yolo --output-format stream-json {_model_flag('-m', spec.model)} -p {prompt}"
            ),
            env=common,
            key_names=keys,
            home_dirs=[".gemini/tmp"],
        )

    if spec.name == "opencode":
        config = json.dumps({
            "$schema": "https://opencode.ai/config.json",
            "permission": {"edit": "allow", "bash": "allow", "webfetch": "deny"},
            "autoupdate": False,
            "share": "disabled",
        })
        return Invocation(
            command=f"opencode run --auto --format json {_model_flag('-m', spec.model)} {prompt}",
            env={**common, "OPENCODE_CONFIG_CONTENT": config, "OPENCODE_DISABLE_AUTOUPDATE": "1"},
            key_names=keys,
            home_dirs=[".local/share/opencode"],
        )

    if spec.name == "mini-swe-agent":
        return Invocation(
            command=(
                f"mkdir -p {OUT} && mini -y --exit-immediately -l 0 {_model_flag('-m', spec.model)} "
                f"-o {OUT}/mini-trajectory.json -t {prompt}"
            ),
            env={**common, "MSWEA_COST_TRACKING": "ignore_errors", "MSWEA_SILENT_STARTUP": "1",
                 "MSWEA_CONFIGURED": "true"},
            key_names=keys,
        )

    if spec.name == "oracle":
        return Invocation(
            command=(
                "if [ -s /tmp/kairo/gold.patch ]; then git apply --binary /tmp/kairo/gold.patch; fi && "
                "cp /tmp/kairo/expected-verdict.json /work/verdict.json"
            ),
            env=common,
        )

    if spec.name == "noop":
        return Invocation(command="true", env=common)

    raise ValueError(f"no adapter for agent {spec.name!r}")


def parse_usage(spec: AgentSpec, stdout_text: str) -> dict:
    """Best-effort cost and token accounting from the agent's own stdout."""
    usage: dict = {}
    lines = [ln for ln in stdout_text.splitlines() if ln.startswith("{")]
    events = []
    for ln in lines:
        try:
            events.append(json.loads(ln))
        except json.JSONDecodeError:
            continue

    if spec.name == "claude-code":
        for ev in reversed(events):
            if ev.get("type") == "result":
                u = ev.get("usage", {}) or {}
                usage = {
                    "cost_usd": ev.get("total_cost_usd"),
                    "turns": ev.get("num_turns"),
                    "input_tokens": u.get("input_tokens"),
                    "output_tokens": u.get("output_tokens"),
                    "cache_read_tokens": u.get("cache_read_input_tokens"),
                    "cache_write_tokens": u.get("cache_creation_input_tokens"),
                    "agent_reported_error": bool(ev.get("is_error")),
                }
                break
    elif spec.name == "codex":
        inp = out = cached = turns = 0
        for ev in events:
            if ev.get("type") == "turn.completed":
                u = ev.get("usage", {}) or {}
                inp += int(u.get("input_tokens") or 0)
                cached += int(u.get("cached_input_tokens") or 0)
                out += int(u.get("output_tokens") or 0)
                turns += 1
        if turns:
            usage = {"input_tokens": inp, "cache_read_tokens": cached, "output_tokens": out, "turns": turns}
    elif spec.name == "gemini-cli":
        for ev in reversed(events):
            stats = ev.get("stats") if isinstance(ev, dict) else None
            if stats:
                usage = {
                    "input_tokens": stats.get("input_tokens") or stats.get("inputTokens"),
                    "output_tokens": stats.get("output_tokens") or stats.get("outputTokens"),
                    "turns": stats.get("tool_calls") or stats.get("toolCalls"),
                }
                break
    elif spec.name == "opencode":
        inp = out = 0
        cost = 0.0
        seen = False
        for ev in events:
            part = ev.get("part") or {}
            tokens = part.get("tokens") or {}
            if part.get("type") == "step-finish":
                seen = True
                inp += int(tokens.get("input") or 0)
                out += int(tokens.get("output") or 0)
                cost += float(part.get("cost") or 0)
        if seen:
            usage = {"input_tokens": inp, "output_tokens": out, "cost_usd": round(cost, 6)}
    return {k: v for k, v in usage.items() if v is not None}
