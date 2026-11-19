"""`agent-mcts resume`: continuing a run under its own settings, past lost costs and
after a hard kill. Fake adapters throughout; git, journal and worktrees are real."""

import asyncio
import json
import subprocess
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from agent_mcts import cli, project
from agent_mcts.adapters.base import AdapterTimeout, EpisodeResult
from agent_mcts.core import journal
from agent_mcts.core.engine import SearchConfig, SearchEngine
from agent_mcts.core.model import Node, NodeStatus, RunMeta, Tree
from agent_mcts.core.value import Evaluation
from agent_mcts.core.worktree import WorktreeManager

runner = CliRunner()

HALF = "echo '1 failed, 1 passed'; exit 1"  # always 0.5: never solved, never stops early


class FakeAdapter:
    calls = 0
    timeout_calls: ClassVar[set[int]] = set()
    models: ClassVar[list[str | None]] = []

    def __init__(
        self,
        binary: str | None = None,
        *,
        model: str | None = None,
        permission_mode: str = "acceptEdits",
        timeout_s: float = 600.0,
        allowed_tools: list[str] | None = None,
    ) -> None:
        self.model = model

    @property
    def name(self) -> str:
        return "fake"

    @property
    def reports_cost(self) -> bool:
        return True

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        FakeAdapter.calls += 1
        FakeAdapter.models.append(self.model)
        (workdir / f"attempt_{FakeAdapter.calls}.txt").write_text("work\n")
        if FakeAdapter.calls in FakeAdapter.timeout_calls:
            raise AdapterTimeout("timed out", duration_s=600.0)
        return EpisodeResult(
            session_id=f"s{FakeAdapter.calls}",
            summary=f"approach {FakeAdapter.calls}",
            cost_usd=0.5,
        )


@pytest.fixture
def project_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "ClaudeCodeAdapter", FakeAdapter)
    FakeAdapter.calls = 0
    FakeAdapter.timeout_calls = set()
    FakeAdapter.models = []
    return repo


def invoke(*args: str) -> str:
    result = runner.invoke(cli.app, list(args))
    assert result.exit_code == 0, result.output
    return " ".join(result.output.split())


def start(*args: str, value: str = HALF) -> str:
    return invoke("run", "fix the widget", "-y", "--value", value, *args)


def load_latest(repo: Path) -> Tree:
    run_id = project.latest_run_id(repo)
    assert run_id is not None
    return journal.load(project.journal_path(repo, run_id))


def test_resume_continues_the_same_run_under_its_saved_settings(project_repo: Path) -> None:
    start("-n", "1", "--model", "haiku")
    first = load_latest(project_repo)
    assert len(first.nodes) == 2
    assert first.meta.config["value"]["command"] == HALF
    assert first.meta.config["model"] == "haiku"

    # A config file written later must not change how the run scores or which model runs.
    (project_repo / ".agent-mcts.toml").write_text('model = "opus"\n[value]\ncommand = "true"\n')
    output = invoke("resume", "-n", "3", "-y")

    assert f"Resuming run {first.meta.run_id}" in output
    assert "Already spent: 1 episodes, $0.50" in output
    assert "Search finished: 3 episodes, $1.50" in output
    resumed = load_latest(project_repo)
    assert resumed.meta.run_id == first.meta.run_id  # no new run was created
    assert sorted(resumed.nodes) == ["n0", "n1", "n2", "n3"]
    assert all(n.reward == pytest.approx(0.5) for n in resumed.children("n0"))
    assert FakeAdapter.models == ["haiku", "haiku", "haiku"]
    # Earlier siblings still steer later ones, across the restart.
    assert resumed.nodes["n2"].prompt.count("approach 1") == 1
    # Worktrees are gone again; every node kept its branch.
    assert not any((project_repo / ".agent-mcts" / "worktrees").rglob("attempt_*.txt"))
    branches = subprocess.run(
        ["git", "branch", "--list", "agent-mcts/*"],
        cwd=project_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "/n3" in branches


def test_resume_revises_from_surviving_branches(project_repo: Path) -> None:
    start("-n", "3")  # three root attempts fill the root's width
    invoke("resume", "-n", "4", "-y")

    tree = load_latest(project_repo)
    revision = tree.nodes["n4"]
    assert revision.parent_id in {"n1", "n2", "n3"}
    # The revision's worktree was created from its parent's branch, not from scratch.
    files = subprocess.run(
        ["git", "ls-tree", "--name-only", revision.branch or ""],
        cwd=project_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    parent_no = (revision.parent_id or "n0")[1:]
    assert f"attempt_{parent_no}.txt" in files and "attempt_4.txt" in files


def test_resume_refuses_when_the_budget_is_used_up(project_repo: Path) -> None:
    start("-n", "1")
    result = runner.invoke(cli.app, ["resume", "-y"])
    assert result.exit_code == 1
    flat = " ".join(result.output.split())
    assert "episode budget is used up (1/1)" in flat
    assert "agent-mcts resume -n 13" in flat
    assert FakeAdapter.calls == 1


def test_resume_refuses_when_the_cost_ceiling_is_reached(project_repo: Path) -> None:
    start("-n", "5", "--max-cost", "0.4")  # one $0.50 episode crosses the ceiling
    result = runner.invoke(cli.app, ["resume", "-y"])
    assert result.exit_code == 1
    assert "cost ceiling is reached ($0.50 of $0.40)" in " ".join(result.output.split())

    invoke("resume", "--max-cost", "1.2", "-y")
    assert FakeAdapter.calls == 3  # $0.50, $1.00, then $1.50 crosses $1.20


def test_resume_of_a_solved_run_does_nothing(project_repo: Path) -> None:
    start("-n", "3", value="test -f attempt_1.txt")
    output = invoke("resume", "-n", "9", "-y")
    assert "already found an attempt that meets the success threshold" in output
    assert FakeAdapter.calls == 1


def test_resume_continues_past_a_lost_cost_after_warning(project_repo: Path) -> None:
    FakeAdapter.timeout_calls = {2}
    output = start("-n", "5")
    assert "Search finished: 2 episodes" in output  # the lost figure halted the run

    output = invoke("resume", "-y")

    assert "1 episode(s) in this run were cut off before reporting their cost" in output
    assert "Search finished: 5 episodes" in output
    tree = load_latest(project_repo)
    assert tree.nodes["n2"].status is NodeStatus.FAILED and not tree.nodes["n2"].cost_known
    assert tree.nodes["n2"].branch  # the timed-out episode's work is still recoverable


def test_a_new_lost_cost_still_halts_a_resumed_run(project_repo: Path) -> None:
    FakeAdapter.timeout_calls = {1, 3}
    start("-n", "6")
    assert FakeAdapter.calls == 1
    output = invoke("resume", "-y")
    assert FakeAdapter.calls == 3  # accepted n1's loss, stopped again at the new one
    assert "Search finished: 3 episodes" in output


def test_resume_asks_before_spending(project_repo: Path) -> None:
    start("-n", "1")
    result = runner.invoke(cli.app, ["resume", "-n", "3"], input="n\n")
    assert result.exit_code == 1
    assert "Continue?" in result.output
    assert FakeAdapter.calls == 1


def test_resume_by_run_id_and_unknown_run(project_repo: Path) -> None:
    start("-n", "1")
    run_id = load_latest(project_repo).meta.run_id
    assert "Search finished: 2 episodes" in invoke("resume", run_id, "-n", "2", "-y")

    result = runner.invoke(cli.app, ["resume", "19990101-000000", "-y"])
    assert result.exit_code == 1
    assert "has no journal" in " ".join(result.output.split())


def test_resume_of_a_run_without_saved_settings_uses_current_config(project_repo: Path) -> None:
    start("-n", "1")
    run_id = project.latest_run_id(project_repo)
    assert run_id is not None
    path = project.journal_path(project_repo, run_id)
    lines = path.read_text().splitlines()
    meta = json.loads(lines[0])
    del meta["config"]  # a journal written before settings were saved
    path.write_text("\n".join([json.dumps(meta), *lines[1:]]) + "\n")
    (project_repo / ".agent-mcts.toml").write_text(f'[value]\ncommand = "{HALF}"\n')

    output = invoke("resume", "-n", "2", "-y")
    assert "predates saved settings" in output
    assert "Search finished: 2 episodes" in output


def test_resume_refuses_a_run_whose_baseline_never_finished(project_repo: Path) -> None:
    run_dir = project_repo / ".agent-mcts" / "runs" / "20200101-000000"
    path = run_dir / "tree.jsonl"
    meta = RunMeta(run_id="20200101-000000", task="t", repo_root=str(project_repo))
    journal.append_meta(path, meta)
    journal.append_node(path, Node(id="n0", status=NodeStatus.FAILED, reward=0.0))

    result = runner.invoke(cli.app, ["resume", "-y"])
    assert result.exit_code == 1
    assert "never finished its baseline evaluation" in " ".join(result.output.split())


# -- engine: recovering from a hard kill --------------------------------------


class NeverCalled:
    name = "fake"
    reports_cost = True

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        raise AssertionError("no episode should run")


class Half:
    async def evaluate(self, workdir: Path) -> Evaluation:
        return Evaluation(score=0.5)


def test_recover_interrupted_snapshots_work_left_by_a_killed_process(repo: Path) -> None:
    meta = RunMeta(run_id="r1", task="t", repo_root=str(repo))
    worktrees = WorktreeManager(repo, "r1")
    tree = Tree(meta)
    root = tree.add(Node(id="n0", status=NodeStatus.EVALUATED, reward=0.2, visits=1))
    worktrees.create("n0")
    root.branch = worktrees.branch_name("n0")
    # The process died mid-episode: the journal says RUNNING and the worktree has edits.
    stuck = tree.add(Node(id="n1", parent_id="n0", status=NodeStatus.RUNNING))
    (worktrees.create("n1", base=root.branch) / "half_done.py").write_text("x = 1\n")
    gone = tree.add(Node(id="n2", parent_id="n0", status=NodeStatus.RUNNING))  # no worktree

    journal_path = repo / ".agent-mcts" / "runs" / "r1" / "tree.jsonl"
    journal.append_meta(journal_path, meta)
    engine = SearchEngine(
        tree=tree,
        adapter=NeverCalled(),
        value_fn=Half(),
        worktrees=worktrees,
        journal_path=journal_path,
        config=SearchConfig(max_nodes=2),
        accepted_unknown_cost=frozenset({"n1", "n2"}),
    )
    recovered = engine.recover_interrupted()

    assert {n.id for n in recovered} >= {"n1", "n2"}
    assert stuck.status is NodeStatus.FAILED and gone.status is NodeStatus.FAILED
    assert not stuck.cost_known and not gone.cost_known
    assert stuck.visits == 1 and root.visits == 3  # failures were backed up as 0
    files = subprocess.run(
        ["git", "ls-tree", "--name-only", stuck.branch or ""],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "half_done.py" in files
    assert gone.branch is None
    assert journal.load(journal_path).nodes["n1"].status is NodeStatus.FAILED
    # Budget is spent (2 episodes), so the loop does not start another one.
    asyncio.run(engine.run())
    worktrees.cleanup()
