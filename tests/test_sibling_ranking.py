"""Sibling-relative judging: standings from a head-to-head ranking steer selection,
and never leak into backup or the final choice. Fake judge throughout."""

import asyncio
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_mcts import cli
from agent_mcts.adapters.base import AdapterTimeout, EpisodeResult
from agent_mcts.core import journal
from agent_mcts.core.engine import SearchConfig, SearchEngine
from agent_mcts.core.judge import LLMJudge, SiblingRanker
from agent_mcts.core.model import NodeStatus, RunMeta, Tree
from agent_mcts.core.value import Evaluation
from agent_mcts.core.worktree import WorktreeManager


class Worker:
    name = "fake"
    reports_cost = True

    def __init__(self, delay: float = 0.0) -> None:
        self.calls = 0
        self.delay = delay

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        self.calls += 1
        call_no = self.calls
        (workdir / f"feature_{call_no}.py").write_text(f"x = {call_no}\n")
        await asyncio.sleep(self.delay)
        return EpisodeResult(session_id=f"s{call_no}", summary="did it", cost_usd=0.10)


class Scripted:
    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)

    async def evaluate(self, workdir: Path) -> Evaluation:
        return Evaluation(score=self.scores.pop(0), detail="ok")


class Ranker:
    """Ranks by a fixed preference order, whatever subset it is shown."""

    name = "fake-judge"
    reports_cost = True

    def __init__(self, preference: list[str], *, fail: Exception | str | None = None) -> None:
        self.preference = preference
        self.fail = fail
        self.groups: list[list[str]] = []
        self.prompts: list[str] = []

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        shown = [n for n in self.preference if f"CANDIDATE {n} " in prompt]
        self.groups.append(sorted(shown))
        self.prompts.append(prompt)
        if isinstance(self.fail, Exception):
            raise self.fail
        reply = (
            self.fail or f"<Explanation>by taste</Explanation><Ranking>{', '.join(shown)}</Ranking>"
        )
        return EpisodeResult(session_id="j", summary=reply, cost_usd=0.04)


def run(
    repo: Path,
    scores: list[float],
    ranker: Ranker | None,
    worker: Worker | None = None,
    **settings: object,
) -> SearchEngine:
    meta = RunMeta(run_id="r1", task="add the feature", repo_root=str(repo))
    engine = SearchEngine(
        tree=Tree(meta),
        adapter=worker or Worker(),
        value_fn=Scripted(scores),
        worktrees=WorktreeManager(repo, "r1"),
        journal_path=repo / ".agent-mcts" / "runs" / "r1" / "tree.jsonl",
        config=SearchConfig.model_validate(
            {"root_width": 2, "refine_width": 2, "c_uct": 0.0, **settings}
        ),
        sibling_ranker=(
            SiblingRanker(LLMJudge(ranker, task=meta.task), repo=repo) if ranker else None
        ),
    )
    asyncio.run(engine.run())
    engine.worktrees.cleanup()
    return engine


def test_without_ranking_the_higher_reward_is_revised(repo: Path) -> None:
    engine = run(repo, [0.0, 0.60, 0.62, 0.5], None, max_nodes=3)
    assert engine.tree.nodes["n3"].parent_id == "n2"


def test_standing_among_siblings_redirects_selection(repo: Path) -> None:
    # n2 has the slightly higher absolute score, but head to head the judge prefers n1.
    ranker = Ranker(["n1", "n2", "n3"])
    engine = run(repo, [0.0, 0.60, 0.62, 0.5], ranker, max_nodes=3)
    tree = engine.tree

    assert tree.nodes["n1"].sibling_score == 1.0
    assert tree.nodes["n2"].sibling_score == 0.0
    assert tree.nodes["n3"].parent_id == "n1"
    assert ranker.groups == [["n1", "n2"]]  # n3 has no evaluated sibling yet
    # Each sibling is shown by what it changed relative to the shared parent.
    assert "feature_1.py" in ranker.prompts[0] and "feature_2.py" in ranker.prompts[0]


def test_standing_does_not_touch_backup_or_the_final_choice(repo: Path) -> None:
    engine = run(repo, [0.0, 0.60, 0.62, 0.5], Ranker(["n1", "n2", "n3"]), max_nodes=2)
    tree = engine.tree
    best = tree.best()
    assert best is not None and best.id == "n2"  # rewards alone decide what apply offers
    assert tree.nodes["n1"].q == pytest.approx(0.60) and tree.nodes["n2"].q == pytest.approx(0.62)
    root = tree.root
    assert root is not None and root.value_sum == pytest.approx(0.0 + 0.60 + 0.62)


def test_sibling_weight_zero_ignores_the_standing(repo: Path) -> None:
    ranker = Ranker(["n1", "n2", "n3"])
    engine = run(repo, [0.0, 0.60, 0.62, 0.5], ranker, max_nodes=3, sibling_weight=0.0)
    assert engine.tree.nodes["n3"].parent_id == "n2"


def test_a_group_is_reranked_when_a_sibling_arrives(repo: Path) -> None:
    ranker = Ranker(["n3", "n1", "n2"])
    engine = run(repo, [0.0, 0.5, 0.5, 0.5], ranker, max_nodes=3, root_width=3)
    tree = engine.tree

    assert ranker.groups == [["n1", "n2"], ["n1", "n2", "n3"]]
    assert [tree.nodes[n].sibling_score for n in ("n3", "n1", "n2")] == [1.0, 0.5, 0.0]
    # Ranking spend lands on the attempt that triggered it, and in the totals.
    assert tree.nodes["n1"].eval_cost_usd == 0.0
    assert tree.nodes["n2"].eval_cost_usd == pytest.approx(0.04)
    assert tree.nodes["n3"].eval_cost_usd == pytest.approx(0.04)
    assert engine.total_cost_usd == pytest.approx(0.30 + 0.08)
    assert journal.load(engine.journal_path).nodes["n1"].sibling_score == 0.5


def test_unusable_ranking_leaves_standings_alone_but_costs(repo: Path) -> None:
    ranker = Ranker(["n1", "n2"], fail="hard to say")
    engine = run(repo, [0.0, 0.60, 0.62], ranker, max_nodes=2)
    tree = engine.tree
    assert tree.nodes["n1"].sibling_score is None and tree.nodes["n2"].sibling_score is None
    assert tree.nodes["n2"].eval_cost_usd == pytest.approx(0.04)
    assert tree.nodes["n2"].status is NodeStatus.EVALUATED


def test_lost_ranking_cost_halts_the_search(repo: Path) -> None:
    ranker = Ranker(["n1", "n2"], fail=AdapterTimeout("judge timed out", duration_s=300.0))
    engine = run(repo, [0.0, 0.60, 0.62, 0.5, 0.5], ranker, max_nodes=4)
    assert engine.episodes == 2
    assert engine.unknown_cost_episodes == 1
    assert engine.tree.nodes["n2"].status is NodeStatus.EVALUATED


def test_siblings_finishing_together_are_all_ranked(repo: Path) -> None:
    ranker = Ranker(["n2", "n3", "n1"])
    engine = run(
        repo,
        [0.0, 0.5, 0.5, 0.5],
        ranker,
        Worker(delay=0.02),
        max_nodes=3,
        root_width=3,
        parallelism=3,
    )
    tree = engine.tree
    assert [tree.nodes[n].sibling_score for n in ("n2", "n3", "n1")] == [1.0, 0.5, 0.0]
    assert ranker.groups[-1] == ["n1", "n2", "n3"]
    assert len(ranker.groups) <= 2  # never one call per sibling for the same group


# -- CLI ----------------------------------------------------------------------


class CliAdapter:
    rank_calls = 0

    def __init__(self, binary: str | None = None, **_: object) -> None:
        self.calls = 0

    name = "fake"
    reports_cost = True

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        if prompt.startswith("You are the final judge"):
            CliAdapter.rank_calls += 1
            reply = "<Explanation>n1 first</Explanation><Ranking>n1, n2</Ranking>"
            return EpisodeResult(session_id="r", summary=reply, cost_usd=0.01)
        if prompt.startswith("You are the evaluator"):
            reply = "<Explanation>fine</Explanation><Reward>20</Reward>"
            return EpisodeResult(session_id="j", summary=reply, cost_usd=0.01)
        self.calls += 1
        (workdir / f"attempt_{id(self)}_{self.calls}.txt").write_text("work\n")
        return EpisodeResult(session_id=f"s{self.calls}", summary="tried", cost_usd=0.1)


def test_cli_relative_setting_enables_sibling_ranking(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "ClaudeCodeAdapter", CliAdapter)
    CliAdapter.rank_calls = 0
    (repo / ".agent-mcts.toml").write_text(
        "[value]\njudge = true\nrelative = true\nselect = false\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "config"], cwd=repo, check=True)

    runner = CliRunner()
    result = runner.invoke(cli.app, ["run", "build it", "-y", "-n", "2", "--value", "true"])
    assert result.exit_code == 0, result.output
    assert "+ sibling ranking" in " ".join(result.output.split())
    assert CliAdapter.rank_calls == 1

    shown = " ".join(runner.invoke(cli.app, ["show", "n1"]).output.split())
    assert "among siblings=1.00" in shown
