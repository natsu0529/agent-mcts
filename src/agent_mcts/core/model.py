"""Search-tree data model: nodes, run metadata, and the in-memory tree."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from agent_mcts.adapters.base import TokenUsage


def _utcnow() -> datetime:
    return datetime.now(UTC)


class NodeStatus(StrEnum):
    PENDING = "pending"  # created, agent has not run yet
    RUNNING = "running"  # agent episode in flight
    EVALUATED = "evaluated"  # episode finished and value function scored it
    FAILED = "failed"  # episode errored or was cut off by budget


class Node(BaseModel):
    """One search-tree node = one agent attempt over an isolated worktree."""

    id: str
    parent_id: str | None = None
    status: NodeStatus = NodeStatus.PENDING

    prompt: str = ""  # what this expansion asked the agent to do
    summary: str = ""  # the agent's final message for this episode
    session_id: str | None = None  # agent session backing this node
    branch: str | None = None  # git branch holding this node's code snapshot

    reward: float | None = None  # this node's own evaluation in [0, 1]
    eval_detail: str = ""  # value-function feedback (fed into children's revision prompts)
    test_score: float | None = None  # reward components, when the value fn reports them
    judge_score: float | None = None
    visits: int = 0  # MCTS N
    value_sum: float = 0.0  # MCTS W
    value_max: float | None = None  # best value backed up through this node, if any

    cost_usd: float | None = 0.0  # None: the backend never reports a dollar figure
    cost_known: bool = True  # False: the figure was lost after the agent started
    usage: TokenUsage | None = None  # token counts, when the backend reports them
    duration_s: float = 0.0
    # Spend of this node's *evaluation* (an LLM judge), kept apart from the episode's.
    eval_cost_usd: float | None = 0.0
    eval_cost_known: bool = True
    eval_usage: TokenUsage | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def q(self) -> float:
        """Mean backed-up value (0.0 while unvisited)."""
        return self.value_sum / self.visits if self.visits else 0.0


class RunMeta(BaseModel):
    """Immutable metadata for one search run."""

    run_id: str
    task: str
    agent: str = "claude"
    repo_root: str
    base_commit: str | None = None  # commit the root worktree was created from
    # The settings the run was started with (model, timeouts, value and search
    # config), so `resume` continues under them. Empty for journals that predate it.
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class Selection(BaseModel):
    """A final head-to-head choice among the top-scoring nodes (see `core.judge`).

    The per-node reward guides the search; this decides what `apply` offers. A selection
    that could not be made (`node_id=None`) is still recorded, because it may have cost
    money.
    """

    node_id: str | None = None
    candidates: list[str] = Field(default_factory=list)  # node ids that were compared
    explanation: str = ""
    error: str = ""
    cost_usd: float | None = 0.0
    cost_known: bool = True
    usage: TokenUsage | None = None


class Tree:
    """In-memory search tree. Persistence lives in `journal`; policy lives in the engine."""

    def __init__(self, meta: RunMeta) -> None:
        self.meta = meta
        self.nodes: dict[str, Node] = {}
        self.selections: list[Selection] = []  # every selection made, for cost totals
        self.selection: Selection | None = None  # the one still current, if any

    @property
    def root(self) -> Node | None:
        return next((n for n in self.nodes.values() if n.parent_id is None), None)

    def add(self, node: Node) -> Node:
        if node.id in self.nodes:
            raise ValueError(f"duplicate node id: {node.id}")
        if node.parent_id is not None and node.parent_id not in self.nodes:
            raise ValueError(f"unknown parent id: {node.parent_id}")
        if node.parent_id is None and self.root is not None:
            raise ValueError("tree already has a root")
        self.nodes[node.id] = node
        self.selection = None  # a selection made before this node existed is stale
        return node

    def record_selection(self, selection: Selection) -> None:
        self.selections.append(selection)
        self.selection = selection

    def next_id(self) -> str:
        return f"n{len(self.nodes)}"

    def children(self, node_id: str) -> list[Node]:
        return [n for n in self.nodes.values() if n.parent_id == node_id]

    def path(self, node_id: str) -> list[Node]:
        """Nodes from the root down to (and including) `node_id`."""
        out: list[Node] = []
        current: str | None = node_id
        while current is not None:
            node = self.nodes[current]
            out.append(node)
            current = node.parent_id
        out.reverse()
        return out

    def best(self) -> Node | None:
        """The branch `apply` offers by default.

        A current final selection wins; otherwise the highest-reward evaluated node.
        """
        chosen = self.selection.node_id if self.selection is not None else None
        if chosen is not None and chosen in self.nodes:
            return self.nodes[chosen]
        return self.best_by_reward()

    def ranked(self) -> list[Node]:
        """Evaluated nodes, best reward first (ties: deeper first)."""
        scored = [
            n
            for n in self.nodes.values()
            if n.status is NodeStatus.EVALUATED and n.reward is not None
        ]
        return sorted(scored, key=lambda n: (n.reward or 0.0, len(self.path(n.id))), reverse=True)

    def best_by_reward(self) -> Node | None:
        """Highest-reward evaluated node.

        Ties prefer deeper nodes: an attempt that did work beats the untouched
        baseline scoring the same (e.g. both 1.0 on an already-green suite).
        """
        scored = [
            n
            for n in self.nodes.values()
            if n.status is NodeStatus.EVALUATED and n.reward is not None
        ]
        if not scored:
            return None
        return max(scored, key=lambda n: (n.reward or 0.0, len(self.path(n.id))))

    def backup(self, node_id: str, value: float) -> list[Node]:
        """Propagate `value` from `node_id` to the root; returns the updated nodes."""
        updated = self.path(node_id)
        for node in updated:
            node.visits += 1
            node.value_sum += value
            node.value_max = value if node.value_max is None else max(node.value_max, value)
        return updated
