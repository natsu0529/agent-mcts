"""Project-level helpers: repo discovery, config file, value-command detection, run listing."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, ValidationError, model_validator

from agent_mcts.core.engine import SearchConfig

CONFIG_FILENAME = ".agent-mcts.toml"


class ProjectError(RuntimeError):
    pass


class ValueConfig(BaseModel):
    command: str | None = None
    # LLM-as-judge (see `core.judge`): reward = w_test * tests + w_judge * judge.
    judge: bool = False
    judge_agent: str | None = None  # default: the run's agent
    judge_model: str | None = None  # default: the run's model
    judge_timeout_s: float = Field(default=300.0, gt=0)
    w_test: float = Field(default=0.5, ge=0)
    w_judge: float = Field(default=0.5, ge=0)
    # With the judge on: after the search, compare the top nodes head to head and let
    # that decide what `apply` offers (see `core.judge.FinalSelector`).
    select: bool = True
    select_top_k: int = Field(default=3, ge=2)
    select_margin: float = Field(default=0.1, ge=0)  # only nodes this close to the best

    @model_validator(mode="after")
    def _weights_not_both_zero(self) -> Self:
        if self.w_test + self.w_judge <= 0:
            raise ValueError("w_test and w_judge cannot both be zero")
        return self


class ClaudeConfig(BaseModel):
    timeout_s: float = Field(default=600.0, gt=0)
    allowed_tools: list[str] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    """Contents of .agent-mcts.toml. Everything is optional; zero-config must work."""

    agent: str = "claude"
    model: str | None = None
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    value: ValueConfig = Field(default_factory=ValueConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)


def find_repo_root(start: Path) -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ProjectError(
            "agent-mcts must run inside a git repository (state branching is built on git)."
        )
    return Path(proc.stdout.strip())


def head_commit(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def is_dirty(repo: Path) -> bool:
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout
    return bool(out.strip())


def load_project_config(repo: Path) -> ProjectConfig:
    path = repo / CONFIG_FILENAME
    if not path.exists():
        return ProjectConfig()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ProjectError(f"could not parse {CONFIG_FILENAME}: {exc}") from exc
    try:
        return ProjectConfig.model_validate(data)
    except ValidationError as exc:
        raise ProjectError(f"invalid {CONFIG_FILENAME}:\n{_format_errors(exc)}") from exc


def _format_errors(exc: ValidationError) -> str:
    """Render pydantic's errors as `  key: reason` lines — a traceback is not a UI."""
    lines: list[str] = []
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or CONFIG_FILENAME
        lines.append(f"  {location}: {err['msg']}")
    return "\n".join(lines)


def detect_value_command(repo: Path) -> str | None:
    """Best-effort guess of the project's test command (the default value function)."""
    pyproject = repo / "pyproject.toml"
    if (
        (repo / "pytest.ini").exists()
        or (repo / "tests").is_dir()
        or (pyproject.exists() and "pytest" in pyproject.read_text(encoding="utf-8"))
    ):
        return "pytest -x -q"
    package_json = repo / "package.json"
    if package_json.exists():
        try:
            scripts = json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        if isinstance(scripts, dict) and "test" in scripts:
            return "npm test"
    makefile = repo / "Makefile"
    if makefile.exists() and "test:" in makefile.read_text(encoding="utf-8"):
        return "make test"
    return None


def runs_dir(repo: Path) -> Path:
    return repo / ".agent-mcts" / "runs"


def journal_path(repo: Path, run_id: str) -> Path:
    return runs_dir(repo) / run_id / "tree.jsonl"


def latest_run_id(repo: Path) -> str | None:
    """Run ids are timestamps, so lexicographic order is chronological."""
    base = runs_dir(repo)
    if not base.is_dir():
        return None
    candidates = sorted(d.name for d in base.iterdir() if (d / "tree.jsonl").exists())
    return candidates[-1] if candidates else None
