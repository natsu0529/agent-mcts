"""The UCT search loop: wires an agent adapter, worktrees, a value function, and the journal.

Policy summary (see docs/design.md):
- one node expansion = one complete agent episode over an isolated worktree
- expand-first with fixed widths (root_width diverse attempts, refine_width revisions)
- UCT descent with c = sqrt(2) by default; failed episodes back up 0
- anytime: every state change is journaled, so Ctrl-C leaves a valid tree
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from agent_mcts.adapters.base import AdapterError, AdapterTimeout, AgentBackend
from agent_mcts.core import journal, prompts
from agent_mcts.core.judge import FinalSelector, SiblingRanker
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

    # What a node is worth during selection. "mean" is classic UCT (Q = W/N). Every node
    # here is a complete, directly scored solution and the goal is the single best one,
    # so "max" (best reward seen in the subtree) or "mix" can fit better than a mean
    # that one poor sibling drags down.
    backup: Literal["mean", "max", "mix"] = "mean"
    backup_mix: float = Field(default=0.5, ge=0, le=1)  # weight of max in "mix"

    # "descend" walks down from the root comparing siblings (classic UCT, expand-first).
    # "global" scores every expandable node in the tree and takes the best, as
    # SWE-Search does; only there do the depth terms below change anything, since
    # siblings share a depth.
    selection: Literal["descend", "global"] = "descend"
    depth_bonus: float = Field(default=0.0, ge=0)  # alpha: + alpha * exp(-beta * (d - 1))
    depth_bonus_decay: float = Field(default=1.0, ge=0)  # beta
    depth_penalty: float = Field(default=0.0, ge=0)  # gamma: - gamma * sqrt(d)

    # Stop revising a line of attempts once its last `give_up_after` nodes all scored
    # below `low_reward`. 0 disables the rule.
    give_up_after: int = Field(default=0, ge=0)
    low_reward: float = Field(default=0.25, ge=0, le=1)

    # Episodes in flight at once. 1 is the sequential search. Higher values trade some
    # search quality for wall-clock time: later picks are made without the results of
    # the episodes still running, and siblings launched together cannot read each
    # other's summaries.
    parallelism: int = Field(default=1, ge=1)

    # How much a node's standing among its siblings (`Node.sibling_score`) counts in
    # selection, against its backed-up value. Only matters when siblings are ranked.
    sibling_weight: float = Field(default=0.5, ge=0, le=1)


class SearchEngine:
    """Runs one search, expanding up to `config.parallelism` nodes at a time.

    Everything runs on one event loop: episodes and evaluations overlap while they wait
    on subprocesses, but the tree, the journal and git bookkeeping are only ever touched
    between awaits, so they need no locking and the journal stays a valid tree.
    """

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
        selector: FinalSelector | None = None,
        sibling_ranker: SiblingRanker | None = None,
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
        self.selector = selector  # optional final head-to-head choice after the search
        self.sibling_ranker = sibling_ranker  # optional ranking within sibling groups
        self._rank_locks: dict[str, asyncio.Lock] = {}
        self._ranked_groups: dict[str, frozenset[str]] = {}

    @property
    def total_cost_usd(self) -> float:
        """Dollars reported so far, episodes plus evaluations (judge calls).

        Unreported figures (`None`) count $0.
        """
        nodes = sum(
            (n.cost_usd or 0.0) + (n.eval_cost_usd or 0.0) for n in self.tree.nodes.values()
        )
        return nodes + sum(s.cost_usd or 0.0 for s in self.tree.selections)

    @property
    def unknown_cost_episodes(self) -> int:
        """Nodes whose spend was *lost* (timeout, crash, Ctrl-C after launch).

        Covers the episode and the node's evaluation: a judge call that dies mid-flight
        loses its figure exactly like an agent episode does.
        """
        nodes = sum(not (n.cost_known and n.eval_cost_known) for n in self.tree.nodes.values())
        return nodes + sum(not s.cost_known for s in self.tree.selections)

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
        for selection in self.tree.selections:
            if selection.usage is not None:
                total = total + selection.usage
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
        tasks: set[asyncio.Task[Node]] = set()
        try:
            while True:
                while len(tasks) < self.config.parallelism and self._may_launch(len(tasks)):
                    parent = self._select()
                    if parent is None:
                        break  # nothing expandable until an in-flight episode lands
                    # `_begin` is synchronous on purpose: the new node is in the tree
                    # before the next pick, so that pick sees the slot as taken.
                    tasks.add(asyncio.create_task(self._finish(*self._begin(parent))))
                if not tasks:
                    break
                done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()  # re-raise whatever an episode could not handle
        except BaseException:
            # Ctrl-C, or one episode blew up. Every other episode gets cancelled and is
            # awaited, so each one snapshots its partial work before the run unwinds.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        await self._select_final()
        return self.tree

    async def _select_final(self) -> None:
        """Let the selector choose among the top nodes, unless one already did.

        Skipped when the dollar ceiling is already reached: the search stopped for lack
        of money, and the selection is one more paid call.
        """
        if self.selector is None or self.tree.selection is not None:
            return
        if self.adapter.reports_cost and self.total_cost_usd >= self.config.max_cost_usd:
            return
        selection = await self.selector.select(self.tree)
        if selection is None:
            return
        self.tree.record_selection(selection)
        journal.append_selection(self.journal_path, selection)
        if self.on_change is not None:
            self.on_change()

    def _may_launch(self, in_flight: int) -> bool:
        if not self._within_budget() or self.solved:
            return False
        if in_flight == 0 or not self.adapter.reports_cost:
            return True
        # Cost is only known when an episode ends, so episodes in flight are spending
        # money that no total shows yet. Hold back the launch that would cross the
        # ceiling if they each cost what a finished one did on average.
        finished = [
            (n.cost_usd or 0.0) + (n.eval_cost_usd or 0.0)
            for n in self.tree.nodes.values()
            if n.parent_id is not None and n.status is not NodeStatus.RUNNING
        ]
        expected = sum(finished) / len(finished) if finished else 0.0
        return self.total_cost_usd + in_flight * expected < self.config.max_cost_usd

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
            and not self._given_up(node)
        )

    def _given_up(self, node: Node) -> bool:
        """Whether this line of attempts has scored low too many times in a row."""
        streak = self.config.give_up_after
        if streak <= 0:
            return False
        line = self.tree.path(node.id)[1:]  # the root is the baseline, not an attempt
        if len(line) < streak:
            return False
        return all((n.reward or 0.0) < self.config.low_reward for n in line[-streak:])

    def _in_flight(self, node: Node) -> int:
        """Episodes still running in `node`'s subtree."""
        return sum(
            (child.status is NodeStatus.RUNNING) + self._in_flight(child)
            for child in self.tree.children(node.id)
        )

    def _visits(self, node: Node) -> int:
        """Visits including episodes in flight below the node (virtual loss).

        Counting a running episode as a visit that has returned nothing yet lowers the
        node's mean and its exploration bonus, which steers concurrent picks toward
        other branches. Nothing is stored, so there is nothing to undo when it lands.
        """
        return node.visits + self._in_flight(node)

    def _value(self, node: Node) -> float:
        """A node's worth for selection, per the configured backup rule."""
        visits = self._visits(node)
        mean = node.value_sum / visits if visits else 0.0
        best = node.value_max if node.value_max is not None else mean
        if self.config.backup == "max":
            value = best
        elif self.config.backup == "mix":
            mix = self.config.backup_mix
            value = mix * best + (1 - mix) * mean
        else:
            value = mean
        if node.sibling_score is None:
            return value
        weight = self.config.sibling_weight
        return (1 - weight) * value + weight * node.sibling_score

    def _depth_term(self, node: Node) -> float:
        cfg = self.config
        if cfg.depth_bonus == 0 and cfg.depth_penalty == 0:
            return 0.0
        depth = self._depth(node)
        bonus = cfg.depth_bonus * math.exp(-cfg.depth_bonus_decay * (depth - 1))
        return bonus - cfg.depth_penalty * math.sqrt(depth)

    def _subtree_has_capacity(self, node: Node) -> bool:
        if self._can_expand(node):
            return True
        return any(self._subtree_has_capacity(c) for c in self.tree.children(node.id))

    def _uct(self, parent: Node, child: Node) -> float:
        explore = math.sqrt(math.log(max(self._visits(parent), 1)) / max(self._visits(child), 1))
        return self._value(child) + self.config.c_uct * explore + self._depth_term(child)

    def next_to_expand(self) -> Node | None:
        """The node the policy would expand now; None when nothing can be expanded."""
        return self._select()

    def _select(self) -> Node | None:
        root = self.tree.root
        if root is None:
            return None
        if self.config.selection == "global":
            return self._select_global()
        return self._select_descending(root)

    def _select_global(self) -> Node | None:
        """Best expandable node anywhere in the tree, each scored against its parent."""
        candidates = [n for n in self.tree.nodes.values() if self._can_expand(n)]
        if not candidates:
            return None

        def score(node: Node) -> float:
            parent = self.tree.nodes[node.parent_id] if node.parent_id is not None else node
            return self._uct(parent, node)

        return max(candidates, key=score)

    def _select_descending(self, root: Node) -> Node | None:
        """Descend from the root; expand at the first node with spare width."""
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
        return await self._finish(*self._begin(parent))

    def _begin(self, parent: Node) -> tuple[Node, Node, Path]:
        """Create the child node and its worktree; returns (node, parent, workdir)."""
        children = self.tree.children(parent.id)
        siblings = [c.summary for c in children]
        # Siblings still running have no summary to steer away from. Tell this attempt
        # which one it is, so concurrent attempts spread out without seeing each other.
        slot = len(children) + 1 if any(c.status is NodeStatus.RUNNING for c in children) else 0
        if parent.parent_id is None:
            prompt = prompts.root_attempt_prompt(
                self.tree.meta.task, parent.eval_detail, siblings, parallel_slot=slot
            )
        else:
            prompt = prompts.revision_prompt(
                self.tree.meta.task,
                parent.reward or 0.0,
                parent.eval_detail,
                siblings,
                parallel_slot=slot,
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
        try:
            workdir = self.worktrees.create(node.id, base=parent.branch)
        except BaseException:
            # No worktree means no episode: do not leave a node that claims to be running.
            node.status = NodeStatus.FAILED
            node.reward = 0.0
            node.eval_detail = "could not create a worktree for this attempt"
            self._journal([node])
            raise
        return node, parent, workdir

    async def _finish(self, node: Node, parent: Node, workdir: Path) -> Node:
        """Run the episode and its evaluation for a node `_begin` created."""
        prompt = node.prompt
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
        # After the node is fully recorded: an interruption during the ranking must not
        # turn a finished, evaluated attempt into a failed one.
        await self._rank_siblings(parent, node)
        return node

    async def _rank_siblings(self, parent: Node, arrived: Node) -> None:
        """Re-rank `parent`'s evaluated children now that `arrived` has joined them."""
        if self.sibling_ranker is None or arrived.status is not NodeStatus.EVALUATED:
            return
        lock = self._rank_locks.setdefault(parent.id, asyncio.Lock())
        async with lock:
            group = [
                c
                for c in self.tree.children(parent.id)
                if c.status is NodeStatus.EVALUATED and c.branch
            ]
            ids = frozenset(c.id for c in group)
            # Siblings that finish together would otherwise each pay for the same ranking.
            if len(group) < 2 or self._ranked_groups.get(parent.id) == ids:
                return
            ranking = await self.sibling_ranker.rank(parent, group)
            arrived.eval_cost_usd = (
                None
                if arrived.eval_cost_usd is None or ranking.cost_usd is None
                else arrived.eval_cost_usd + ranking.cost_usd
            )
            arrived.eval_cost_known = arrived.eval_cost_known and ranking.cost_known
            if ranking.usage is not None:
                arrived.eval_usage = (arrived.eval_usage or TokenUsage()) + ranking.usage
            changed = [arrived]
            if ranking.order is not None:
                self._ranked_groups[parent.id] = ids
                last = len(ranking.order) - 1
                for place, node_id in enumerate(ranking.order):
                    self.tree.nodes[node_id].sibling_score = 1 - place / last
                changed = group
            self._journal(changed)

    @staticmethod
    def _record_evaluation(node: Node, evaluation: Evaluation) -> None:
        node.reward = evaluation.score
        node.eval_detail = evaluation.detail
        node.test_score = evaluation.test_score
        node.judge_score = evaluation.judge_score
        node.generated_score = evaluation.generated_score
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
