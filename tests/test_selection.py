"""Final head-to-head selection: ranking replies, who gets compared, how the choice is
recorded, and when it goes stale. Fake judge throughout."""

import asyncio
import subprocess
from pathlib import Path

import pytest

from agent_mcts.adapters.base import AdapterTimeout, EpisodeResult
from agent_mcts.core import journal
from agent_mcts.core.engine import SearchConfig, SearchEngine
from agent_mcts.core.judge import (
    Candidate,
    FinalSelector,
    LLMJudge,
    evidence_part,
    parse_ranking,
    ranking_prompt,
)
from agent_mcts.core.model import Node, NodeStatus, RunMeta, Selection, Tree
from agent_mcts.core.value import Evaluation
from agent_mcts.core.worktree import WorktreeManager


class RankingBackend:
    name = "fake-judge"
    reports_cost = True

    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        self.prompts.append(prompt)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return EpisodeResult(session_id="j", summary=item, cost_usd=0.07)


class Worker:
    name = "fake"
    reports_cost = True

    def __init__(self) -> None:
        self.calls = 0

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        self.calls += 1
        (workdir / f"feature_{self.calls}.py").write_text(f"x = {self.calls}\n")
        return EpisodeResult(session_id=f"s{self.calls}", summary="did it", cost_usd=0.10)


class Scripted:
    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)

    async def evaluate(self, workdir: Path) -> Evaluation:
        score = self.scores.pop(0)
        return Evaluation(score=score, detail=f"Judge (+1/100): fine\n\nTests ({score:.2f}):\nok")


def ranking(order: str, why: str = "cleaner") -> str:
    return f"<Explanation>{why}</Explanation>\n<Ranking>{order}</Ranking>"


# -- parsing ------------------------------------------------------------------


def test_parse_ranking_reads_best_first() -> None:
    assert parse_ranking(ranking("n2, n1, n3"), ["n1", "n2", "n3"]) == (
        ["n2", "n1", "n3"],
        "cleaner",
    )
    assert parse_ranking(ranking("n2 > n1"), ["n1", "n2"]) == (["n2", "n1"], "cleaner")


def test_parse_ranking_rejects_incomplete_or_invented_rankings() -> None:
    labels = ["n1", "n2", "n3"]
    assert parse_ranking(ranking("n2, n1"), labels) is None  # one missing
    assert parse_ranking(ranking("n2, n1, n1"), labels) is None  # one repeated
    assert parse_ranking(ranking("n2, n1, n9"), labels) is None  # one invented
    assert parse_ranking("n2 is best", labels) is None


def test_ranking_prompt_shows_every_candidate_and_shares_the_budget() -> None:
    big = "diff --git a/f b/f\n" + "+line\n" * 20_000
    prompt = ranking_prompt(
        "do it",
        [Candidate(label="n1", diff=big, test_output="1 passed"), Candidate(label="n2", diff="d2")],
    )
    assert "CANDIDATE n1" in prompt and "CANDIDATE n2" in prompt
    assert "<Ranking>n1, n2</Ranking>" in prompt
    assert len(prompt) < 40_000


def test_evidence_part_drops_the_earlier_critique() -> None:
    assert evidence_part("Judge (+60/100): misses X\n\nTests (0.70):\n3 failed, 7 passed") == (
        "3 failed, 7 passed"
    )
    assert evidence_part("3 failed, 7 passed") == "3 failed, 7 passed"


# -- engine integration -------------------------------------------------------


def run(
    repo: Path,
    scores: list[float],
    backend: RankingBackend,
    *,
    top_k: int = 3,
    margin: float = 0.1,
    **settings: object,
) -> SearchEngine:
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    meta = RunMeta(run_id="r1", task="add the feature", repo_root=str(repo), base_commit=base)
    engine = SearchEngine(
        tree=Tree(meta),
        adapter=Worker(),
        value_fn=Scripted(scores),
        worktrees=WorktreeManager(repo, "r1"),
        journal_path=repo / ".agent-mcts" / "runs" / "r1" / "tree.jsonl",
        config=SearchConfig.model_validate({"max_nodes": 3, **settings}),
        selector=FinalSelector(
            LLMJudge(backend, task=meta.task),
            repo=repo,
            base_commit=base,
            top_k=top_k,
            margin=margin,
        ),
    )
    asyncio.run(engine.run())
    engine.worktrees.cleanup()
    return engine


def test_close_scores_are_settled_head_to_head(repo: Path) -> None:
    backend = RankingBackend([ranking("n2, n1", "n2 handles the edge case")])
    engine = run(repo, [0.0, 0.97, 0.96, 0.40], backend)
    tree = engine.tree

    assert tree.best_by_reward() is tree.nodes["n1"]
    assert tree.best() is tree.nodes["n2"]  # the comparison overrides a 0.01 lead
    selection = tree.selection
    assert selection is not None and selection.candidates == ["n2", "n1"]
    # n3 was too far behind to be worth comparing.
    prompt = backend.prompts[0]
    assert "CANDIDATE n1" in prompt and "CANDIDATE n2" in prompt and "CANDIDATE n3" not in prompt
    # Each candidate is shown by its own diff and its test output, not the old critique.
    assert "feature_1.py" in prompt and "feature_2.py" in prompt
    assert "Judge (+1/100)" not in prompt
    assert engine.total_cost_usd == pytest.approx(0.30 + 0.07)

    loaded = journal.load(engine.journal_path)
    assert loaded.selection == selection
    best = loaded.best()
    assert best is not None and best.id == "n2"


def test_a_clear_winner_is_not_compared(repo: Path) -> None:
    backend = RankingBackend([])
    engine = run(repo, [0.0, 0.95, 0.50, 0.40], backend)
    assert backend.prompts == [] and engine.tree.selection is None
    best = engine.tree.best()
    assert best is not None and best.id == "n1"


def test_top_k_limits_the_comparison(repo: Path) -> None:
    backend = RankingBackend([ranking("n1, n2")])
    run(repo, [0.0, 0.92, 0.91, 0.90], backend, top_k=2)
    assert "CANDIDATE n3" not in backend.prompts[0]


def test_unusable_ranking_falls_back_to_reward_and_keeps_the_cost(repo: Path) -> None:
    backend = RankingBackend(["they are all nice"])
    engine = run(repo, [0.0, 0.97, 0.96, 0.40], backend)
    selection = engine.tree.selection
    assert selection is not None and selection.node_id is None
    assert "did not rank every candidate" in selection.error
    best = engine.tree.best()
    assert best is not None and best.id == "n1"
    assert engine.total_cost_usd == pytest.approx(0.37)


def test_lost_selection_cost_is_counted_as_unknown(repo: Path) -> None:
    backend = RankingBackend([AdapterTimeout("judge timed out", duration_s=300.0)])
    engine = run(repo, [0.0, 0.97, 0.96, 0.40], backend)
    assert engine.unknown_cost_episodes == 1
    best = engine.tree.best()
    assert best is not None and best.id == "n1"


def test_selection_is_skipped_once_the_cost_ceiling_is_reached(repo: Path) -> None:
    backend = RankingBackend([])
    engine = run(repo, [0.0, 0.97, 0.96, 0.40], backend, max_cost_usd=0.15)
    assert engine.episodes == 2 and backend.prompts == []


def test_a_selection_goes_stale_when_the_search_continues(repo: Path) -> None:
    tree = Tree(RunMeta(run_id="r1", task="t", repo_root=str(repo)))
    tree.add(Node(id="n0", status=NodeStatus.EVALUATED, reward=0.0))
    tree.add(Node(id="n1", parent_id="n0", status=NodeStatus.EVALUATED, reward=0.9))
    tree.add(Node(id="n2", parent_id="n0", status=NodeStatus.EVALUATED, reward=0.8))
    tree.record_selection(Selection(node_id="n2", candidates=["n2", "n1"], cost_usd=0.07))
    best = tree.best()
    assert best is not None and best.id == "n2"

    tree.add(Node(id="n3", parent_id="n1", status=NodeStatus.EVALUATED, reward=0.95))
    best = tree.best()
    assert tree.selection is None and best is not None and best.id == "n3"
    assert len(tree.selections) == 1  # its cost is still on the books


def test_journal_replay_drops_a_selection_made_before_later_nodes(repo: Path) -> None:
    path = repo / "tree.jsonl"
    journal.append_meta(path, RunMeta(run_id="r1", task="t", repo_root=str(repo)))
    journal.append_node(path, Node(id="n0", status=NodeStatus.EVALUATED, reward=0.0))
    journal.append_node(
        path, Node(id="n1", parent_id="n0", status=NodeStatus.EVALUATED, reward=0.9)
    )
    journal.append_selection(path, Selection(node_id="n1", candidates=["n1"], cost_usd=0.07))
    assert journal.load(path).selection is not None

    journal.append_node(
        path, Node(id="n1", parent_id="n0", status=NodeStatus.EVALUATED, reward=0.9)
    )
    assert journal.load(path).selection is not None  # a re-snapshot of a known node is not news

    journal.append_node(path, Node(id="n2", parent_id="n0", status=NodeStatus.RUNNING))
    loaded = journal.load(path)
    assert loaded.selection is None and len(loaded.selections) == 1
