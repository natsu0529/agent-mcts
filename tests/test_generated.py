"""Generated-tests value: reply parsing, the derived command, and scoring attempts on
suites a fake writer returns. The suites really run under pytest; no API calls."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_mcts.adapters.base import AdapterError, EpisodeResult, TokenUsage
from agent_mcts.core.generated import (
    GeneratedTestsValueFunction,
    SuiteWriter,
    default_command,
    parse_suite,
)
from agent_mcts.core.value import Evaluation
from agent_mcts.project import ValueConfig

COMMAND = f"{sys.executable} -m pytest -q -p no:cacheprovider {{file}}"

SUITE = """\
from calc import add

def test_small():
    assert add(1, 2) == 3

def test_negative():
    assert add(-1, -1) == -2

def test_zero():
    assert add(0, 0) == 0

def test_large():
    assert add(10**6, 1) == 1000001
"""


def reply(path: str = "tests/test_calc_more.py", body: str = SUITE) -> str:
    return f"<TestFile>{path}</TestFile>\n```python\n{body}```\n"


class FakeWriter:
    """Returns scripted replies; an Exception in the script is raised instead."""

    def __init__(self, replies: list[str | Exception], *, cost_usd: float | None = 0.1) -> None:
        self.replies = list(replies)
        self.cost_usd = cost_usd
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "fake-writer"

    @property
    def reports_cost(self) -> bool:
        return self.cost_usd is not None

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        self.calls.append(
            {
                "prompt": prompt,
                "files": sorted(str(p.relative_to(workdir)) for p in workdir.rglob("*.py")),
            }
        )
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return EpisodeResult(
            session_id=f"w{len(self.calls)}",
            summary=item,
            cost_usd=self.cost_usd,
            usage=TokenUsage(input_tokens=100, output_tokens=10),
        )


class FixedTests:
    def __init__(self, score: float, detail: str = "3 passed") -> None:
        self.score = score
        self.detail = detail

    async def evaluate(self, workdir: Path) -> Evaluation:
        return Evaluation(score=self.score, detail=self.detail)


@pytest.fixture
def calc_repo(repo: Path) -> tuple[Path, str]:
    """The base commit: a stub to implement and one visible test."""
    (repo / "calc.py").write_text("def add(a, b):\n    raise NotImplementedError\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "conftest.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "stub"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, head


def attempt(tmp_path: Path, name: str, body: str) -> Path:
    """A worktree-like directory holding one attempt at calc.py."""
    work = tmp_path / name
    (work / "tests").mkdir(parents=True)
    (work / "tests" / "conftest.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n"
    )
    (work / "calc.py").write_text(body)
    return work


def value_fn(
    calc_repo: tuple[Path, str], writer: FakeWriter, store: Path, **kwargs: Any
) -> GeneratedTestsValueFunction:
    repo, head = calc_repo
    return GeneratedTestsValueFunction(
        FixedTests(1.0),
        SuiteWriter(writer, task="implement add", repo=repo, base_commit=head),
        store=store,
        command=COMMAND,
        **kwargs,
    )


# -- parsing and the derived command ------------------------------------------------


def test_parse_suite_keeps_the_directory_and_uses_its_own_name() -> None:
    suite = parse_suite(reply("pkg/tests/test_thing.py"), index=2)
    assert suite is not None
    assert suite.path == "pkg/tests/test_agent_mcts_generated_2.py"
    assert suite.content == SUITE


@pytest.mark.parametrize("path", ["/etc/test_x.py", "../outside/test_x.py"])
def test_parse_suite_refuses_paths_outside_the_repo(path: str) -> None:
    suite = parse_suite(reply(path), index=1)
    assert suite is not None
    assert suite.path == "tests/test_agent_mcts_generated_1.py"


def test_parse_suite_without_a_code_block_is_none() -> None:
    assert parse_suite("<TestFile>tests/test_x.py</TestFile> no code here", index=1) is None


@pytest.mark.parametrize(
    ("value_command", "expected"),
    [
        ("python -m pytest -q tests", "python -m pytest -q -p no:cacheprovider {file}"),
        ("uv run pytest", "uv run pytest -q -p no:cacheprovider {file}"),
        ("/x/.venv/bin/pytest -x", "/x/.venv/bin/pytest -q -p no:cacheprovider {file}"),
        ("npm test", None),
        ("make test", None),
    ],
)
def test_default_command(value_command: str, expected: str | None) -> None:
    assert default_command(value_command) == expected


def test_config_rejects_judge_and_generated_tests_together() -> None:
    with pytest.raises(ValidationError, match="choose one"):
        ValueConfig(judge=True, generate_tests=True)


def test_config_requires_the_file_placeholder() -> None:
    with pytest.raises(ValidationError, match="must contain"):
        ValueConfig(generate_tests=True, generate_command="pytest -q")


# -- scoring ------------------------------------------------------------------------


def test_attempts_are_scored_on_the_generated_suites(
    calc_repo: tuple[Path, str], tmp_path: Path
) -> None:
    writer = FakeWriter([reply(), reply()])
    fn = value_fn(calc_repo, writer, tmp_path / "store", suites=2)

    right = attempt(tmp_path, "right", "def add(a, b):\n    return a + b\n")
    first = asyncio.run(fn.evaluate(right))
    # Tests 1.0 and every generated test passes.
    assert first.score == pytest.approx(1.0)
    assert first.test_score == 1.0
    assert first.generated_score == pytest.approx(1.0)
    assert "all passed" in first.detail
    # The evaluation that wrote the suites carries their cost.
    assert first.cost_usd == pytest.approx(0.2)
    assert first.usage == TokenUsage(input_tokens=200, output_tokens=20)

    # The writer saw the base commit, not an attempt.
    assert "calc.py" in writer.calls[0]["files"]
    assert "implement add" in writer.calls[0]["prompt"]

    wrong = attempt(tmp_path, "wrong", "def add(a, b):\n    return a + b if a >= 0 else 0\n")
    second = asyncio.run(fn.evaluate(wrong))
    # test_negative fails in both suites: 3 of 4 pass.
    assert second.generated_score == pytest.approx(0.75)
    assert second.score == pytest.approx(0.5 * 1.0 + 0.5 * 0.75)
    assert "test_negative" in second.detail
    assert second.cost_usd == 0.0  # the suites were already written
    assert len(writer.calls) == 2

    # Nothing is left behind in the attempt.
    for work in (right, wrong):
        assert sorted(p.name for p in (work / "tests").iterdir() if p.suffix == ".py") == [
            "conftest.py"
        ]


def test_a_suite_directory_the_attempt_lacks_is_removed_again(
    calc_repo: tuple[Path, str], tmp_path: Path
) -> None:
    fn = value_fn(calc_repo, FakeWriter([reply("checks/deep/test_x.py")]), tmp_path / "s", suites=1)
    work = attempt(tmp_path, "a", "def add(a, b):\n    return a + b\n")
    (work / "checks").mkdir()
    (work / "checks" / "conftest.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parent.parent))\n"
    )

    evaluation = asyncio.run(fn.evaluate(work))

    assert evaluation.generated_score is not None
    assert sorted(p.name for p in (work / "checks").iterdir()) == ["conftest.py"]


def test_suites_are_stored_and_reused_by_a_resumed_search(
    calc_repo: tuple[Path, str], tmp_path: Path
) -> None:
    store = tmp_path / "store"
    asyncio.run(
        value_fn(calc_repo, FakeWriter([reply()]), store, suites=1).evaluate(
            attempt(tmp_path, "a", "def add(a, b):\n    return a + b\n")
        )
    )
    saved = json.loads((store / "suites.json").read_text())
    assert [s["path"] for s in saved["suites"]] == ["tests/test_agent_mcts_generated_1.py"]

    resumed_writer = FakeWriter([])
    evaluation = asyncio.run(
        value_fn(calc_repo, resumed_writer, store, suites=1).evaluate(
            attempt(tmp_path, "b", "def add(a, b):\n    return 0\n")
        )
    )

    assert resumed_writer.calls == []
    assert evaluation.generated_score == pytest.approx(0.25)  # only add(0, 0) == 0


def test_a_failed_writer_leaves_the_tests_alone_in_charge(
    calc_repo: tuple[Path, str], tmp_path: Path
) -> None:
    writer = FakeWriter([AdapterError("boom", cost_known=False), "no code at all"], cost_usd=0.1)
    fn = value_fn(calc_repo, writer, tmp_path / "store", suites=2)

    evaluation = asyncio.run(
        fn.evaluate(attempt(tmp_path, "a", "def add(a, b):\n    return a + b\n"))
    )

    assert evaluation.generated_score is None
    assert evaluation.score == pytest.approx(0.5)  # tests 1.0, generated half scores 0
    assert "Generated tests unavailable" in evaluation.detail
    assert "boom" in evaluation.detail
    assert evaluation.cost_known is False  # the crashed writer's spend is lost


def test_an_attempt_file_at_the_suite_path_is_not_overwritten(
    calc_repo: tuple[Path, str], tmp_path: Path
) -> None:
    fn = value_fn(calc_repo, FakeWriter([reply()]), tmp_path / "store", suites=1)
    work = attempt(tmp_path, "a", "def add(a, b):\n    return a + b\n")
    own = work / "tests" / "test_agent_mcts_generated_1.py"
    own.write_text("def test_mine():\n    pass\n")

    evaluation = asyncio.run(fn.evaluate(work))

    assert evaluation.generated_score == 0.0
    assert own.read_text() == "def test_mine():\n    pass\n"
