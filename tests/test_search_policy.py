"""Selection policy knobs: backup rule, global selection with depth terms, giving up.

Most cases build a tree by hand and ask the engine which node it would expand next —
the policy is a pure function of the tree, so no episodes need to run.
"""

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_mcts.adapters.base import EpisodeResult
from agent_mcts.core.engine import SearchConfig, SearchEngine
from agent_mcts.core.model import Node, NodeStatus, RunMeta, Tree
from agent_mcts.core.value import Evaluation
from agent_mcts.core.worktree import WorktreeManager


class Backend:
    name = "fake"
    reports_cost = True

    def __init__(self) -> None:
        self.calls = 0

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        self.calls += 1
        (workdir / f"attempt_{self.calls}.txt").write_text("work\n")
        return EpisodeResult(session_id=f"s{self.calls}", summary="tried", cost_usd=0.01)


class Scripted:
    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)

    async def evaluate(self, workdir: Path) -> Evaluation:
        return Evaluation(score=self.scores.pop(0))


def engine_for(repo: Path, tree: Tree, config: SearchConfig, scores: list[float]) -> SearchEngine:
    return SearchEngine(
        tree=tree,
        adapter=Backend(),
        value_fn=Scripted(scores),
        worktrees=WorktreeManager(repo, tree.meta.run_id),
        journal_path=repo / ".agent-mcts" / "runs" / tree.meta.run_id / "tree.jsonl",
        config=config,
    )


def node(
    node_id: str,
    parent: str | None,
    reward: float,
    *,
    visits: int = 1,
    total: float | None = None,
    best: float | None = None,
) -> Node:
    return Node(
        id=node_id,
        parent_id=parent,
        status=NodeStatus.EVALUATED,
        reward=reward,
        visits=visits,
        value_sum=reward if total is None else total,
        value_max=reward if best is None else best,
    )


def lopsided_tree() -> Tree:
    """A has one excellent revision and one dud; B is steadily mediocre.

    A: mean 0.30, best 0.95, both revision slots used.
    B: mean 0.50, best 0.55, one revision slot free.
    """
    tree = Tree(RunMeta(run_id="r1", task="t", repo_root="/x"))
    tree.add(node("n0", None, 0.0, visits=6, total=2.2, best=0.95))
    tree.add(node("a", "n0", 0.25, visits=4, total=1.2, best=0.95))
    tree.add(node("a1", "a", 0.95))
    tree.add(node("a2", "a", 0.0))
    tree.add(node("b", "n0", 0.55, visits=2, total=1.0, best=0.55))
    tree.add(node("b1", "b", 0.45))
    return tree


def select(repo: Path, tree: Tree, **settings: object) -> str | None:
    config = SearchConfig.model_validate(
        {"root_width": 2, "refine_width": 2, "c_uct": 0.0, **settings}
    )
    chosen = engine_for(repo, tree, config, []).next_to_expand()
    return chosen.id if chosen is not None else None


# -- backup rule --------------------------------------------------------------


def test_mean_backup_follows_the_steady_branch(repo: Path) -> None:
    assert select(repo, lopsided_tree()) == "b"


def test_max_backup_follows_the_branch_with_the_best_solution(repo: Path) -> None:
    # A's mean is dragged down by its dud, but its best revision is where to dig.
    assert select(repo, lopsided_tree(), backup="max") == "a1"


def test_mix_backup_interpolates(repo: Path) -> None:
    assert select(repo, lopsided_tree(), backup="mix", backup_mix=0.5) == "a1"
    assert select(repo, lopsided_tree(), backup="mix", backup_mix=0.1) == "b"


def test_backup_records_the_best_value_on_the_path() -> None:
    tree = Tree(RunMeta(run_id="r1", task="t", repo_root="/x"))
    tree.add(Node(id="n0"))
    tree.add(Node(id="n1", parent_id="n0"))
    tree.add(Node(id="n2", parent_id="n0"))
    tree.backup("n1", 0.4)
    tree.backup("n2", 0.9)
    tree.backup("n1", 0.1)
    assert tree.nodes["n0"].value_max == pytest.approx(0.9)
    assert tree.nodes["n1"].value_max == pytest.approx(0.4)
    assert tree.nodes["n0"].q == pytest.approx((0.4 + 0.9 + 0.1) / 3)


def test_nodes_from_older_journals_fall_back_to_the_mean(repo: Path) -> None:
    tree = lopsided_tree()
    for n in tree.nodes.values():
        n.value_max = None  # journaled before value_max existed
    assert select(repo, tree, backup="max") == "b"


# -- global selection and depth terms -----------------------------------------


def deep_tree() -> Tree:
    """Root is full. `a` (depth 1) and its better revision `a1` (depth 2) can expand."""
    tree = Tree(RunMeta(run_id="r1", task="t", repo_root="/x"))
    tree.add(node("n0", None, 0.0, visits=3, total=1.1))
    tree.add(node("a", "n0", 0.5, visits=2, total=1.0))  # mean 0.5
    tree.add(node("a1", "a", 0.6))
    return tree


def test_descending_selection_expands_the_shallow_node_first(repo: Path) -> None:
    assert select(repo, deep_tree(), root_width=1) == "a"


def test_global_selection_takes_the_best_expandable_node_anywhere(repo: Path) -> None:
    assert select(repo, deep_tree(), root_width=1, selection="global") == "a1"


def test_depth_penalty_pulls_global_selection_back_up(repo: Path) -> None:
    chosen = select(repo, deep_tree(), root_width=1, selection="global", depth_penalty=0.5)
    assert chosen == "a"


def test_depth_bonus_favors_early_nodes(repo: Path) -> None:
    chosen = select(repo, deep_tree(), root_width=1, selection="global", depth_bonus=1.0)
    assert chosen == "a"


def test_depth_terms_do_not_reorder_siblings_when_descending(repo: Path) -> None:
    # Siblings share a depth, so the terms cancel in sibling-vs-sibling comparisons.
    plain = select(repo, lopsided_tree())
    with_terms = select(repo, lopsided_tree(), depth_bonus=2.0, depth_penalty=0.7)
    assert plain == with_terms == "b"


def test_global_selection_runs_a_whole_search(repo: Path) -> None:
    tree = Tree(RunMeta(run_id="r1", task="t", repo_root=str(repo)))
    config = SearchConfig(max_nodes=4, root_width=2, refine_width=2, selection="global", c_uct=0.5)
    engine = engine_for(repo, tree, config, [0.0, 0.2, 0.8, 0.9, 0.3])
    asyncio.run(engine.run())
    engine.worktrees.cleanup()

    assert engine.episodes == 4
    assert all(n.status is NodeStatus.EVALUATED for n in tree.nodes.values())
    assert tree.nodes["n3"].parent_id == "n2"  # the 0.8 attempt was revised first


# -- giving up on a line of attempts ------------------------------------------


def run_line(repo: Path, **settings: object) -> SearchEngine:
    tree = Tree(RunMeta(run_id="r1", task="t", repo_root=str(repo)))
    config = SearchConfig.model_validate(
        {"max_nodes": 5, "root_width": 1, "refine_width": 1, "max_depth": 5, **settings}
    )
    engine = engine_for(repo, tree, config, [0.0, 0.1, 0.1, 0.1, 0.1, 0.1])
    asyncio.run(engine.run())
    engine.worktrees.cleanup()
    return engine


def test_without_the_rule_a_hopeless_line_uses_the_whole_budget(repo: Path) -> None:
    assert run_line(repo).episodes == 5


def test_give_up_after_consecutive_low_rewards(repo: Path) -> None:
    engine = run_line(repo, give_up_after=2, low_reward=0.25)
    assert engine.episodes == 2  # two low scores in a row, then nothing left to expand


def test_a_good_score_resets_the_streak(repo: Path) -> None:
    tree = Tree(RunMeta(run_id="r1", task="t", repo_root=str(repo)))
    config = SearchConfig(
        max_nodes=5, root_width=1, refine_width=1, max_depth=5, give_up_after=2, low_reward=0.25
    )
    engine = engine_for(repo, tree, config, [0.0, 0.1, 0.6, 0.1, 0.1, 0.1])
    asyncio.run(engine.run())
    engine.worktrees.cleanup()
    assert engine.episodes == 4  # low, good, low, low → stop


# -- config -------------------------------------------------------------------


def test_defaults_are_classic_uct() -> None:
    config = SearchConfig()
    assert (config.backup, config.selection) == ("mean", "descend")
    assert config.depth_bonus == 0 and config.depth_penalty == 0 and config.give_up_after == 0


def test_unknown_backup_rule_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchConfig.model_validate({"backup": "median"})
