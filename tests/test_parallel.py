"""Parallel expansion: concurrency limit, budget, virtual loss, prompts, and unwinding.

The fake backend blocks each episode on a gate the test controls, so "how many episodes
are in flight" is observed directly instead of inferred from timing.
"""

import asyncio
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_mcts import cli
from agent_mcts.adapters.base import EpisodeResult
from agent_mcts.core import journal
from agent_mcts.core.engine import SearchConfig, SearchEngine
from agent_mcts.core.model import Node, NodeStatus, RunMeta, Tree
from agent_mcts.core.value import Evaluation
from agent_mcts.core.worktree import WorktreeManager


class GatedBackend:
    """Episodes wait `delay` seconds; the peak number waiting at once is recorded."""

    name = "fake"
    reports_cost = True

    def __init__(
        self, *, delay: float = 0.02, cost_usd: float = 0.10, explode_on: int | None = None
    ) -> None:
        self.delay = delay
        self.cost_usd = cost_usd
        self.explode_on = explode_on
        self.prompts: list[str] = []
        self.running = 0
        self.peak = 0
        self.started = asyncio.Event()

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        call_no = len(self.prompts) + 1
        self.prompts.append(prompt)
        self.running += 1
        self.peak = max(self.peak, self.running)
        self.started.set()
        try:
            (workdir / f"attempt_{call_no}.txt").write_text("work\n")
            await asyncio.sleep(self.delay)
            if call_no == self.explode_on:
                raise RuntimeError("unexpected crash inside an episode")
        finally:
            self.running -= 1
        return EpisodeResult(
            session_id=f"s{call_no}", summary=f"approach {call_no}", cost_usd=self.cost_usd
        )


class Fixed:
    def __init__(self, score: float = 0.5) -> None:
        self.score = score

    async def evaluate(self, workdir: Path) -> Evaluation:
        return Evaluation(score=self.score)


def make_engine(
    repo: Path, backend: GatedBackend, config: SearchConfig, tree: Tree | None = None
) -> SearchEngine:
    tree = tree or Tree(RunMeta(run_id="r1", task="build the thing", repo_root=str(repo)))
    return SearchEngine(
        tree=tree,
        adapter=backend,
        value_fn=Fixed(),
        worktrees=WorktreeManager(repo, "r1"),
        journal_path=repo / ".agent-mcts" / "runs" / "r1" / "tree.jsonl",
        config=config,
    )


def test_sequential_by_default(repo: Path) -> None:
    backend = GatedBackend()
    engine = make_engine(repo, backend, SearchConfig(max_nodes=3))
    asyncio.run(engine.run())
    assert backend.peak == 1
    assert all("running in parallel" not in p for p in backend.prompts)


def test_runs_up_to_parallelism_episodes_at_once(repo: Path) -> None:
    backend = GatedBackend()
    engine = make_engine(repo, backend, SearchConfig(max_nodes=6, root_width=3, parallelism=3))
    tree = asyncio.run(engine.run())

    assert backend.peak == 3
    assert engine.episodes == 6  # in-flight episodes count toward the budget: no overshoot
    assert all(n.status is NodeStatus.EVALUATED for n in tree.nodes.values())
    # The journal written under concurrency replays to the same tree.
    loaded = journal.load(engine.journal_path)
    assert {i: n.visits for i, n in loaded.nodes.items()} == {
        i: n.visits for i, n in tree.nodes.items()
    }
    root = tree.root
    assert root is not None and root.visits == 7  # baseline + six backups, none lost


def test_parallelism_is_capped_by_what_can_be_expanded(repo: Path) -> None:
    backend = GatedBackend()
    config = SearchConfig(max_nodes=2, root_width=2, parallelism=8)
    asyncio.run(make_engine(repo, backend, config).run())
    assert backend.peak == 2


def test_concurrent_siblings_are_told_their_slot(repo: Path) -> None:
    backend = GatedBackend()
    config = SearchConfig(max_nodes=3, root_width=3, parallelism=3)
    asyncio.run(make_engine(repo, backend, config).run())

    first, second, third = backend.prompts
    assert "running in parallel" not in first  # nothing else was in flight yet
    assert "You are attempt #2" in second and "position 2" in second
    assert "You are attempt #3" in third
    # None had finished, so there were no summaries to steer away from.
    assert all("already took these approaches" not in p for p in backend.prompts)


def test_virtual_loss_steers_the_next_pick_elsewhere(repo: Path) -> None:
    def evaluated(node_id: str, parent: str | None, reward: float, visits: int = 1) -> Node:
        return Node(
            id=node_id,
            parent_id=parent,
            status=NodeStatus.EVALUATED,
            reward=reward,
            visits=visits,
            value_sum=reward * visits,
            value_max=reward,
        )

    tree = Tree(RunMeta(run_id="r1", task="t", repo_root=str(repo)))
    tree.add(evaluated("n0", None, 0.0, visits=3))
    tree.add(evaluated("a", "n0", 0.50))
    tree.add(evaluated("b", "n0", 0.45))
    config = SearchConfig(root_width=2, refine_width=2, c_uct=0.0, parallelism=2)
    engine = make_engine(repo, GatedBackend(), config, tree)

    first = engine.next_to_expand()
    assert first is not None and first.id == "a"
    tree.add(Node(id="a1", parent_id="a", status=NodeStatus.RUNNING))  # that pick, in flight
    second = engine.next_to_expand()
    assert second is not None and second.id == "b"  # a's mean is halved while a1 runs


def test_cost_reservation_holds_back_launches_near_the_ceiling(repo: Path) -> None:
    # $0.10 per episode, ceiling $0.35, three root slots, then revisions.
    backend = GatedBackend(cost_usd=0.10)
    config = SearchConfig(max_nodes=12, max_cost_usd=0.35, root_width=3, parallelism=3)
    engine = make_engine(repo, backend, config)
    asyncio.run(engine.run())

    # Sequentially this stops after the 4th episode ($0.40 >= $0.35). With three in
    # flight and no reservation it could reach six.
    assert engine.episodes == 4
    assert engine.total_cost_usd == pytest.approx(0.40)


def test_cancellation_snapshots_every_episode_in_flight(repo: Path) -> None:
    backend = GatedBackend(delay=30.0)
    engine = make_engine(repo, backend, SearchConfig(max_nodes=6, root_width=3, parallelism=3))

    async def interrupt() -> None:
        task = asyncio.create_task(engine.run())
        while backend.running < 3:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(interrupt())

    children = engine.tree.children("n0")
    assert len(children) == 3
    assert all(c.status is NodeStatus.FAILED and not c.cost_known for c in children)
    for index, child in enumerate(children, start=1):
        files = subprocess.run(
            ["git", "ls-tree", "--name-only", child.branch or ""],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert f"attempt_{index}.txt" in files  # partial work survived on its branch
    assert all(
        n.status is not NodeStatus.RUNNING for n in journal.load(engine.journal_path).nodes.values()
    )


def test_one_crashing_episode_unwinds_the_others(repo: Path) -> None:
    backend = GatedBackend(delay=0.05, explode_on=1)
    engine = make_engine(repo, backend, SearchConfig(max_nodes=6, root_width=3, parallelism=3))

    with pytest.raises(RuntimeError, match="unexpected crash"):
        asyncio.run(engine.run())

    assert backend.running == 0  # nothing left running behind the error
    assert all(n.status is not NodeStatus.RUNNING for n in engine.tree.nodes.values())


def test_solved_run_launches_nothing_more(repo: Path) -> None:
    backend = GatedBackend()
    engine = make_engine(repo, backend, SearchConfig(max_nodes=9, root_width=3, parallelism=3))
    engine.value_fn = Fixed(1.0)
    asyncio.run(engine.run())
    assert engine.episodes == 3  # the batch in flight finishes; no further batch starts


# -- CLI ----------------------------------------------------------------------


class CliAdapter(GatedBackend):
    last: "CliAdapter | None" = None

    def __init__(self, binary: str | None = None, **_: object) -> None:
        super().__init__()
        CliAdapter.last = self


def test_cli_parallel_flag_and_config(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "ClaudeCodeAdapter", CliAdapter)
    half = "echo '1 failed, 1 passed'; exit 1"

    result = CliRunner().invoke(
        cli.app, ["run", "build it", "-y", "-n", "3", "-j", "3", "--value", half]
    )
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "Parallel: 3" in flat and "Search finished: 3 episodes" in flat
    assert CliAdapter.last is not None and CliAdapter.last.peak == 3

    (repo / ".agent-mcts.toml").write_text("[search]\nparallelism = 0\n")
    result = CliRunner().invoke(cli.app, ["run", "build it", "-y", "--value", half])
    assert result.exit_code == 1
    assert "search.parallelism" in result.output
