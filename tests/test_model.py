import pytest

from agent_mcts.core.model import Node, NodeStatus, RunMeta, Tree


def make_tree() -> Tree:
    return Tree(RunMeta(run_id="r1", task="fix tests", repo_root="/tmp/x"))


def test_add_and_relations() -> None:
    tree = make_tree()
    root = tree.add(Node(id=tree.next_id()))
    child_a = tree.add(Node(id=tree.next_id(), parent_id=root.id))
    child_b = tree.add(Node(id=tree.next_id(), parent_id=root.id))
    grandchild = tree.add(Node(id=tree.next_id(), parent_id=child_a.id))

    assert tree.root is root
    assert {n.id for n in tree.children(root.id)} == {child_a.id, child_b.id}
    assert [n.id for n in tree.path(grandchild.id)] == [root.id, child_a.id, grandchild.id]


def test_add_integrity_checks() -> None:
    tree = make_tree()
    tree.add(Node(id="n0"))
    with pytest.raises(ValueError, match="duplicate"):
        tree.add(Node(id="n0"))
    with pytest.raises(ValueError, match="unknown parent"):
        tree.add(Node(id="n1", parent_id="nope"))
    with pytest.raises(ValueError, match="already has a root"):
        tree.add(Node(id="n2"))


def test_backup_propagates_to_root() -> None:
    tree = make_tree()
    tree.add(Node(id="n0"))
    tree.add(Node(id="n1", parent_id="n0"))
    tree.add(Node(id="n2", parent_id="n1"))

    tree.backup("n2", 1.0)
    tree.backup("n2", 0.0)
    tree.backup("n1", 0.5)

    assert tree.nodes["n2"].visits == 2
    assert tree.nodes["n2"].q == pytest.approx(0.5)
    assert tree.nodes["n1"].visits == 3
    assert tree.nodes["n1"].q == pytest.approx(0.5)
    assert tree.nodes["n0"].visits == 3
    assert tree.nodes["n0"].value_sum == pytest.approx(1.5)


def test_q_is_zero_when_unvisited() -> None:
    assert Node(id="n0").q == 0.0


def test_best_picks_highest_reward() -> None:
    tree = make_tree()
    tree.add(Node(id="n0"))
    tree.add(Node(id="n1", parent_id="n0", reward=0.3, status=NodeStatus.EVALUATED))
    tree.add(Node(id="n2", parent_id="n0", reward=0.9, status=NodeStatus.EVALUATED))
    best = tree.best()
    assert best is not None and best.id == "n2"


def test_best_is_none_without_rewards() -> None:
    tree = make_tree()
    tree.add(Node(id="n0"))
    assert tree.best() is None


def test_best_prefers_deeper_node_on_tie() -> None:
    # A green-suite baseline and a successful attempt both score 1.0;
    # the attempt (which did work) must win the tie, not the untouched root.
    tree = make_tree()
    tree.add(Node(id="n0", reward=1.0, status=NodeStatus.EVALUATED))
    tree.add(Node(id="n1", parent_id="n0", reward=1.0, status=NodeStatus.EVALUATED))
    best = tree.best()
    assert best is not None and best.id == "n1"


def test_best_ignores_failed_nodes() -> None:
    tree = make_tree()
    tree.add(Node(id="n0", reward=0.0, status=NodeStatus.EVALUATED))
    tree.add(Node(id="n1", parent_id="n0", reward=0.0, status=NodeStatus.FAILED))
    best = tree.best()
    assert best is not None and best.id == "n0"


def scored(
    tree: Tree, node_id: str, parent: str | None, reward: float, tests: float | None = None
) -> None:
    tree.add(
        Node(
            id=node_id,
            parent_id=parent,
            reward=reward,
            test_score=tests,
            status=NodeStatus.EVALUATED,
        )
    )


def test_best_skips_a_baseline_only_the_extra_scorer_prefers() -> None:
    # Generated tests that share the bug: the unfixed code passes more of them than the
    # fix does, while both pass the project's own tests.
    tree = make_tree()
    scored(tree, "n0", None, 0.92, tests=1.0)
    scored(tree, "n1", "n0", 0.76, tests=1.0)
    scored(tree, "n2", "n0", 0.70, tests=1.0)
    assert tree.best() is tree.nodes["n1"]
    assert tree.baseline_outscored()


def test_best_keeps_the_baseline_when_every_attempt_breaks_the_tests() -> None:
    tree = make_tree()
    scored(tree, "n0", None, 0.9, tests=1.0)
    scored(tree, "n1", "n0", 0.6, tests=0.5)
    assert tree.best() is tree.nodes["n0"]
    assert not tree.baseline_outscored()


def test_best_keeps_the_baseline_on_tests_alone() -> None:
    # Without a judge or generated tests the reward is the tests.
    tree = make_tree()
    scored(tree, "n0", None, 0.6)
    scored(tree, "n1", "n0", 0.3)
    assert tree.best() is tree.nodes["n0"]


def test_best_prefers_an_attempt_that_kept_the_tests() -> None:
    tree = make_tree()
    scored(tree, "n0", None, 0.92, tests=1.0)
    scored(tree, "n1", "n0", 0.80, tests=0.5)  # higher reward, but broke a test
    scored(tree, "n2", "n0", 0.70, tests=1.0)
    assert tree.best() is tree.nodes["n2"]


def test_baseline_outscored_is_false_when_an_attempt_wins_outright() -> None:
    tree = make_tree()
    scored(tree, "n0", None, 0.5, tests=1.0)
    scored(tree, "n1", "n0", 0.9, tests=1.0)
    assert tree.best() is tree.nodes["n1"]
    assert not tree.baseline_outscored()
