"""`first_attempt_stop`: ending the search after a first attempt that already looks good.

Fake agents, scripted evaluations with judge / generated-tests components; git, journal
and worktrees are real.
"""

import asyncio
from pathlib import Path

import pytest
from test_engine import FakeBackend
from test_resume import HALF, FakeAdapter, invoke
from typer.testing import CliRunner

from agent_mcts import cli
from agent_mcts.core.engine import SearchConfig, SearchEngine
from agent_mcts.core.model import RunMeta, Tree
from agent_mcts.core.value import Evaluation
from agent_mcts.core.worktree import WorktreeManager

BASELINE = Evaluation(score=0.0, test_score=0.0)


def judged(tests: float, judge: float | None) -> Evaluation:
    return Evaluation(score=0.5 * tests + 0.5 * (judge or 0.0), test_score=tests, judge_score=judge)


class Scripted:
    def __init__(self, evaluations: list[Evaluation]) -> None:
        self.evaluations = list(evaluations)

    async def evaluate(self, workdir: Path) -> Evaluation:
        return self.evaluations.pop(0)


def make_engine(
    repo: Path, evaluations: list[Evaluation], config: SearchConfig, *, run_id: str = "r1"
) -> SearchEngine:
    meta = RunMeta(
        run_id=run_id,
        task="make the tests pass",
        repo_root=str(repo),
        config={
            "timeout_s": 60.0,
            "value": {"command": HALF},
            "search": config.model_dump(mode="json"),
        },
    )
    return SearchEngine(
        tree=Tree(meta),
        adapter=FakeBackend(),
        value_fn=Scripted(evaluations),
        worktrees=WorktreeManager(repo, run_id),
        journal_path=repo / ".agent-mcts" / "runs" / run_id / "tree.jsonl",
        config=config,
    )


def search(repo: Path, evaluations: list[Evaluation], threshold: float | None) -> SearchEngine:
    config = SearchConfig(max_nodes=3, root_width=3, first_attempt_stop=threshold)
    engine = make_engine(repo, [BASELINE, *evaluations], config)
    asyncio.run(engine.run())
    return engine


def test_a_good_first_attempt_ends_the_search(repo: Path) -> None:
    engine = search(repo, [judged(1.0, 0.9)], threshold=0.85)
    assert engine.episodes == 1
    assert engine.stopped_after_first and engine.first_attempt is not None
    assert engine.first_attempt.id == "n1"


@pytest.mark.parametrize(
    ("first", "threshold"),
    [
        (judged(1.0, 0.8), 0.85),  # judge below the threshold
        (judged(0.5, 0.95), 0.85),  # the project's tests do not all pass
        (judged(1.0, None), 0.85),  # the judge produced no score
        (judged(1.0, 0.9), None),  # rule off
    ],
)
def test_otherwise_the_search_goes_on(
    repo: Path, first: Evaluation, threshold: float | None
) -> None:
    engine = search(repo, [first, judged(0.5, 0.5), judged(0.5, 0.5)], threshold)
    assert engine.episodes == 3
    assert not engine.stopped_after_first


def test_generated_tests_score_counts_when_there_is_no_judge(repo: Path) -> None:
    first = Evaluation(score=0.95, test_score=1.0, generated_score=0.9)
    engine = search(repo, [first], threshold=0.85)
    assert engine.episodes == 1


def test_only_the_first_attempt_is_checked(repo: Path) -> None:
    # A later attempt this good does not end the search: the rule is about whether
    # search was needed at all, decided once, where a single run would have stopped.
    engine = search(repo, [judged(1.0, 0.5), judged(1.0, 0.95), judged(0.5, 0.5)], 0.85)
    assert engine.episodes == 3


# -- CLI ----------------------------------------------------------------------


@pytest.fixture
def project_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "ClaudeCodeAdapter", FakeAdapter)
    FakeAdapter.calls = 0
    FakeAdapter.timeout_calls = set()
    FakeAdapter.models = []
    return repo


def test_run_refuses_the_flag_without_a_score_beyond_tests(project_repo: Path) -> None:
    result = CliRunner().invoke(
        cli.app, ["run", "build it", "-y", "--value", HALF, "--first-attempt-stop", "0.85"]
    )
    assert result.exit_code != 0
    assert "turn on --judge or --generate-tests" in " ".join(result.output.split())


def test_resume_continues_a_run_the_rule_ended(project_repo: Path) -> None:
    config = SearchConfig(max_nodes=3, root_width=3, first_attempt_stop=0.85)
    engine = make_engine(project_repo, [BASELINE, judged(1.0, 0.9)], config)
    asyncio.run(engine.run())
    engine.worktrees.cleanup()
    assert engine.episodes == 1

    output = invoke("resume", "-y")

    assert "stopped after its first attempt; continuing it without that rule" in output
    assert "Search finished: 3 episodes" in output
    assert FakeAdapter.calls == 2
