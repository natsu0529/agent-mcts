"""Value functions: score a node's worktree in [0, 1].

The score drives UCT; the detail (e.g. failing-test output) is fed back into
children's revision prompts, closing the search's feedback loop.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from agent_mcts import proctree
from agent_mcts.adapters.base import TokenUsage


class Evaluation(BaseModel):
    score: float  # in [0, 1]
    detail: str = ""  # feedback shown to humans and to revising agents

    # Components, for value functions that combine signals (see `core.judge`).
    test_score: float | None = None
    judge_score: float | None = None  # None: no judge ran, or it produced no score
    generated_score: float | None = None  # pass ratio on generated tests (`core.generated`)

    # What the evaluation itself cost, when it spends money (an LLM judge does, and so
    # does writing the generated tests, charged to the evaluation that wrote them).
    cost_usd: float | None = 0.0  # None: the judge backend never reports dollars
    cost_known: bool = True  # False: the judge died after starting, spend is lost
    usage: TokenUsage | None = None


class ValueFunction(Protocol):
    async def evaluate(self, workdir: Path) -> Evaluation: ...


_PASSED = re.compile(r"(\d+)\s+passed")
_FAILED = re.compile(r"(\d+)\s+failed")
_ERRORS = re.compile(r"(\d+)\s+errors?")

_DETAIL_TAIL_CHARS = 4000


def _last_int(pattern: re.Pattern[str], output: str) -> int:
    matches = pattern.findall(output)
    return int(matches[-1]) if matches else 0


_CARGO_RESULT = re.compile(r"test result: \w+\. (\d+) passed; (\d+) failed")
_JUNIT = re.compile(
    r"Tests run:\s*(\d+),\s*Failures:\s*(\d+)(?:,\s*Errors:\s*(\d+))?(?:,\s*Skipped:\s*(\d+))?"
)
_GO_TEST = re.compile(r"(?m)^\s*--- (PASS|FAIL):")
_GO_PACKAGE = re.compile(r"(?m)^(ok|FAIL)\s+\S+")


def _ratio(passed: int, total: int) -> float:
    return passed / total if total else 0.0


def _pass_ratio(output: str) -> float:
    """Fraction of tests that passed, read from the runner's own summary.

    Each runner reports differently, so the recognizably runner-specific formats are
    tried first and the generic "N passed / N failed" wording (pytest, jest) last.
    Unrecognized output scores 0.0: no partial credit without evidence.
    """
    # cargo prints one result line per test binary; the suite is their sum.
    cargo = _CARGO_RESULT.findall(output)
    if cargo:
        passed = sum(int(p) for p, _ in cargo)
        return _ratio(passed, passed + sum(int(f) for _, f in cargo))

    # JUnit-style (Maven, Gradle): per-class lines, then the total — the last line wins.
    junit = _JUNIT.findall(output)
    if junit:
        run, failures, errors, skipped = (int(n or 0) for n in junit[-1])
        return _ratio(run - failures - errors - skipped, run - skipped)

    # go test -v marks every test; without -v only package verdicts are printed.
    go_tests = _GO_TEST.findall(output)
    if "PASS" in go_tests:
        return _ratio(go_tests.count("PASS"), len(go_tests))
    go_packages = _GO_PACKAGE.findall(output)
    if go_packages:
        return _ratio(go_packages.count("ok"), len(go_packages))

    passed = _last_int(_PASSED, output)
    return _ratio(passed, passed + _last_int(_FAILED, output) + _last_int(_ERRORS, output))


class CommandValueFunction:
    """Run a shell command (typically the test suite) in the worktree.

    Exit 0 → 1.0. On failure, the runner's summary (pytest, jest, cargo test, go test,
    JUnit-style) yields the pass ratio, so partial progress is rewarded; an
    unparseable failure scores 0.0.
    """

    def __init__(self, command: str, *, timeout_s: float = 600.0) -> None:
        self.command = command
        self.timeout_s = timeout_s

    async def evaluate(self, workdir: Path) -> Evaluation:
        proc = await asyncio.create_subprocess_shell(
            self.command,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            # A test command is a process tree too — runners fork workers, suites start
            # servers. Killing only the shell leaves those running against a worktree
            # that is about to be deleted, and holding the pipe we are reading.
            start_new_session=proctree.POSIX,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
        except TimeoutError:
            await proctree.terminate_tree(proc)
            return Evaluation(
                score=0.0,
                detail=f"value command timed out after {self.timeout_s:.0f}s: {self.command}",
            )
        except asyncio.CancelledError:
            # Ctrl-C: the run is over, so the test suite has no business continuing.
            await proctree.terminate_tree(proc)
            raise
        output = stdout.decode(errors="replace")
        detail = output[-_DETAIL_TAIL_CHARS:]
        if proc.returncode == 0:
            return Evaluation(score=1.0, detail=detail)
        return Evaluation(score=_pass_ratio(output), detail=detail)
