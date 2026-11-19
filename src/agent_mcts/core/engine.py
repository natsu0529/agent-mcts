"""The UCT search loop: wires an agent adapter, worktrees, a value function, and the journal.

Policy summary (see docs/design.md):
- one node expansion = one complete agent episode over an isolated worktree
- expand-first with fixed widths (root_width diverse attempts, refine_width revisions)
- UCT descent with c = sqrt(2) by default; failed episodes back up 0
- anytime: every state change is journaled, so Ctrl-C leaves a valid tree
"""

from __future__ import annotations

import math
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from agent_mcts.adapters.base import AdapterError, AdapterTimeout, AgentBackend
from agent_mcts.core import journal, prompts
from agent_mcts.core.model import Node, NodeStatus, TokenUsage, Tree
from agent_mcts.core.value import Evaluation, ValueFunction
from agent_mcts.core.worktree import GitError, WorktreeManager


class SearchConfig(BaseModel):
    max_nodes: int = 12  # episode budget (the root baseline is not an episode)
    max_cost_usd: float = 10.0
    c_uct: float = 1.414  # sqrt(2), the classic UCT exploration constant
    root_width: int = 3  # diverse first attempts under the root
    refine_width: int = 2  # revision children per non-root node
    max_depth: int = 3  # root is depth 0
    success_threshold: float = 0.999  # stop as soon as a node scores this high


class SearchEngine:
    """Runs one search. Sequential in v0.1; parallel expansion is roadmapped."""

    def __init__(
        self,
        *,
        tree: Tree,
        adapter: AgentBackend,
        value_fn: ValueFunction,
        worktrees: WorktreeManager,
        journal_path: Path,
        config: SearchConfig | None = None,
        on_change: Callable[[], None] | None = None,
        accepted_unknown_cost: frozenset[str] = frozenset(),
    ) -> None:
        self.tree = tree
        self.adapter = adapter
        self.value_fn = value_fn
        self.worktrees = worktrees
        self.journal_path = journal_path
        self.config = config or SearchConfig()
        self.on_change = on_change  # reassignable: the live TUI attaches itself here
        # Node ids whose lost cost figure the user has agreed to continue past (resume).
        # They still show as unknown in every total; they just no longer stop the loop.
        self.accepted_unknown_cost = accepted_unknown_cost

    @property
    def total_cost_usd(self) -> float:
        """Dollars reported so far, episodes plus evaluations (judge calls).

        Unreported figures (`None`) count $0.
        """
        return sum((n.cost_usd or 0.0) + (n.eval_cost_usd or 0.0) for n in self.tree.nodes.values())

    @property
    def unknown_cost_episodes(self) -> int:
        """Nodes whose spend was *lost* (timeout, crash, Ctrl-C after launch).

        Covers the episode and the node's evaluation: a judge call that dies mid-flight
        loses its figure exactly like an agent episode does.
        """
        return sum(not (n.cost_known and n.eval_cost_known) for n in self.tree.nodes.values())

    @property
    def unreported_cost_episodes(self) -> int:
        """Episodes run by a backend that never reports dollars (see `reports_cost`)."""
        return sum(n.cost_usd is None for n in self.tree.nodes.values())

    @property
    def total_usage(self) -> TokenUsage:
        total = TokenUsage()
        for node in self.tree.nodes.values():
            if node.usage is not None:
                total = total + node.usage
            if node.eval_usage is not None:
                total = total + node.eval_usage
        return total

    @property
    def episodes(self) -> int:
        return max(0, len(self.tree.nodes) - 1)

    @property
    def solved(self) -> bool:
        """Whether some evaluated node already meets the success threshold."""
        return any(
            n.parent_id is not None
            and n.status is NodeStatus.EVALUATED
            and (n.reward or 0.0) >= self.config.success_threshold
            for n in self.tree.nodes.values()
        )

    def recover_interrupted(self) -> list[Node]:
        """Close out nodes a killed process left RUNNING; returns the nodes it changed.

        Ctrl-C is handled in `_expand`, but a hard kill (SIGKILL, power loss) leaves the
        journal saying an episode is still in flight. Its worktree may still be on disk:
        snapshot what is there, mark the node failed, and treat its cost as lost.
        """
        recovered: list[Node] = []
        for node in self.tree.nodes.values():
            if node.status not in (NodeStatus.RUNNING, NodeStatus.PENDING):
                continue
            detail = "interrupted: the process running this search was killed"
            if self.worktrees.worktree_path(node.id).is_dir():
                self._fail_with_snapshot(node, detail)
            else:
                node.status = NodeStatus.FAILED
                node.reward = 0.0
                node.eval_detail = detail
            node.cost_known = node.parent_id is None  # the root baseline runs no agent
            recovered.append(node)
            if node.parent_id is not None:
                recovered.extend(self.tree.backup(node.id, 0.0))
        self._journal(recovered)
        return recovered

    async def run(self) -> Tree:
        if self.tree.root is None:
            await self._init_root()
        while self._within_budget() and not self.solved:
            parent = self._select()
            if parent is None:
                break  # every node is at width/depth capacity
            await self._expand(parent)
        return self.tree

    # -- setup ---------------------------------------------------------------

    async def _init_root(self) -> None:
        journal.append_meta(self.journal_path, self.tree.meta)
        root = self.tree.add(Node(id=self.tree.next_id(), status=NodeStatus.RUNNING))
        self._journal([root])

        self.worktrees.create(root.id)
        root.branch = self.worktrees.branch_name(root.id)
        try:
            evaluation = await self.value_fn.evaluate(self.worktrees.worktree_path(root.id))
        except BaseException as exc:
            # Same contract as `_expand`: a node that stops being worked on stops being
            # RUNNING. Otherwise a Ctrl-C during the baseline leaves a finished run whose
            # root renders as an in-flight expansion forever.
            root.status = NodeStatus.FAILED
            root.reward = 0.0
            root.eval_detail = f"baseline evaluation did not finish: {type(exc).__name__}"
            self._journal([root])
            raise
        self._record_evaluation(root, evaluation)
        self.tree.backup(root.id, evaluation.score)
        self._journal([root])

    # -- selection -----------------------------------------------------------

    def _width(self, node: Node) -> int:
        return self.config.root_width if node.parent_id is None else self.config.refine_width

    def _depth(self, node: Node) -> int:
        return len(self.tree.path(node.id)) - 1

    def _can_expand(self, node: Node) -> bool:
        return (
            node.status is NodeStatus.EVALUATED
            and self._depth(node) < self.config.max_depth
            and len(self.tree.children(node.id)) < self._width(node)
        )

    def _subtree_has_capacity(self, node: Node) -> bool:
        if self._can_expand(node):
            return True
        return any(self._subtree_has_capacity(c) for c in self.tree.children(node.id))

    def _uct(self, parent: Node, child: Node) -> float:
        explore = math.sqrt(math.log(max(parent.visits, 1)) / child.visits)
        return child.q + self.config.c_uct * explore

    def _select(self) -> Node | None:
        """Descend from the root; expand at the first node with spare width."""
        root = self.tree.root
        if root is None:
            return None
        node: Node = root
        while True:
            if self._can_expand(node):
                return node
            viable = [c for c in self.tree.children(node.id) if self._subtree_has_capacity(c)]
            if not viable:
                return None
            parent = node
            node = max(viable, key=lambda c: self._uct(parent, c))

    # -- expansion -----------------------------------------------------------

    async def _expand(self, parent: Node) -> Node:
        siblings = [c.summary for c in self.tree.children(parent.id)]
        if parent.parent_id is None:
            prompt = prompts.root_attempt_prompt(self.tree.meta.task, parent.eval_detail, siblings)
        else:
            prompt = prompts.revision_prompt(
                self.tree.meta.task, parent.reward or 0.0, parent.eval_detail, siblings
            )

        node = self.tree.add(
            Node(
                id=self.tree.next_id(),
                parent_id=parent.id,
                status=NodeStatus.RUNNING,
                prompt=prompt,
            )
        )
        self._journal([node])

        workdir = self.worktrees.create(node.id, base=parent.branch)
        try:
            result = await self.adapter.run_episode(
                prompt, workdir, resume_session=parent.session_id
            )
            node.session_id = result.session_id
            node.summary = result.summary
            node.cost_usd = result.cost_usd
            node.cost_known = result.cost_known
            node.usage = result.usage
            node.duration_s = result.duration_s
            if result.is_error:
                self._fail_with_snapshot(node, f"agent reported an error: {result.summary}")
            else:
                self.worktrees.commit_all(node.id)
                node.branch = self.worktrees.branch_name(node.id)
                evaluation = await self.value_fn.evaluate(workdir)
                self._record_evaluation(node, evaluation)
        except AdapterError as exc:
            # A timed-out or crashed agent may still have produced valuable edits. Snapshot
            # them before CLI cleanup removes the disposable worktree, while keeping the
            # node failed so an incomplete attempt can never become the automatic best.
            self._fail_with_snapshot(node, str(exc))
            node.summary = "Agent failed; partial worktree state was preserved for inspection."
            # Anything that died after the agent started may already have been billed
            # without ever reporting the amount; the search must stop rather than run
            # on against a cost ceiling it can no longer enforce.
            node.cost_known = exc.cost_known
            if isinstance(exc, AdapterTimeout):
                node.duration_s = exc.duration_s
        except BaseException as exc:
            # Ctrl-C arrives here as CancelledError, and BaseException also covers the
            # value function or a snapshot blowing up. Snapshot before unwinding, or the
            # CLI's cleanup deletes the worktree and the interrupted episode's work with
            # it — and leave a coherent node behind instead of a permanent RUNNING one.
            self._fail_with_snapshot(node, f"interrupted: {type(exc).__name__}")
            node.summary = "Search was interrupted; partial worktree state was preserved."
            node.cost_known = False
            self._journal([node])
            raise

        updated = self.tree.backup(node.id, node.reward or 0.0)
        self._journal(updated)
        return node

    @staticmethod
    def _record_evaluation(node: Node, evaluation: Evaluation) -> None:
        node.reward = evaluation.score
        node.eval_detail = evaluation.detail
        node.test_score = evaluation.test_score
        node.judge_score = evaluation.judge_score
        node.eval_cost_usd = evaluation.cost_usd
        node.eval_cost_known = evaluation.cost_known
        node.eval_usage = evaluation.usage
        node.status = NodeStatus.EVALUATED

    def _fail_with_snapshot(self, node: Node, detail: str) -> None:
        """Mark `node` failed and commit whatever its worktree holds onto the node branch.

        A failed node never becomes the automatic best, but its partial work stays
        inspectable. If the snapshot commit itself fails, the worktree is preserved on
        disk instead — losing the work to cleanup would be worse than a leftover
        directory, and the commit error must not mask the failure that got us here.
        """
        node.status = NodeStatus.FAILED
        node.reward = 0.0
        node.eval_detail = detail
        try:
            self.worktrees.commit_all(node.id, message=f"agent-mcts partial {node.id}")
        except GitError as exc:
            path = self.worktrees.preserve(node.id)
            node.eval_detail = (
                f"{detail}\n\ncould not snapshot the partial work to a branch ({exc}); "
                f"the worktree was kept at {path}"
            )
            return
        node.branch = self.worktrees.branch_name(node.id)

    # -- bookkeeping ----------------------------------------------------------

    def _within_budget(self) -> bool:
        # A backend that never reports dollars contributes $0 here and is bounded by
        # max_nodes alone; a *lost* figure (cost_known=False) still halts the search.
        # Judge calls count toward the dollar ceiling but are not episodes.
        return (
            self.episodes < self.config.max_nodes
            and self.total_cost_usd < self.config.max_cost_usd
            and not self._blocking_unknown_cost()
        )

    def _blocking_unknown_cost(self) -> bool:
        return any(
            not (n.cost_known and n.eval_cost_known) and n.id not in self.accepted_unknown_cost
            for n in self.tree.nodes.values()
        )

    def _journal(self, nodes: list[Node]) -> None:
        for node in nodes:
            journal.append_node(self.journal_path, node)
        if self.on_change is not None:
            self.on_change()
