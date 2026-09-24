"""Paths, the .env loader, and agents.toml."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[1]
ENVS_DIR = BENCH_ROOT / "envs"
AGENTS_FILE = BENCH_ROOT / "agents.toml"
DEFAULT_RUNS_DIR = BENCH_ROOT / "runs"


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Parse KEY=VALUE lines. Values never reach argv or logs.

    Precedence: real environment first, then bench/.env, then the repository
    root .env. A key that is set but empty counts as unset.
    """
    values: dict[str, str] = {}
    candidates = [path] if path else [BENCH_ROOT.parent / ".env", BENCH_ROOT / ".env"]
    for candidate in candidates:
        if not candidate or not candidate.is_file():
            continue
        for raw in candidate.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export ") :]
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            if value:
                values[key] = value
    for key, value in os.environ.items():
        if value:
            values[key] = value
    return values


def load_agents_file() -> dict:
    return tomllib.loads(AGENTS_FILE.read_text())
