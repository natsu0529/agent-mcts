# agent-mcts design notes

Living document. Decisions land here with their reasoning; supersedes nothing silently.

## Node = (git worktree, agent session)

Validated by [the WP2 spike](spikes/2026-08-02-session-fork-worktree.md):

- **Filesystem state** branches via `git worktree` — one worktree per node, one branch per
  node (`agent-mcts/<run>/<node>`). After the agent finishes an expansion, the manager
  commits everything in the worktree onto the node's branch, so children can branch from
  an immutable snapshot and `apply` is an ordinary merge.
- **Conversation state** branches via session forking (`claude -p --resume <id>
  --fork-session`), which works across directories and at any depth.

## Expansion granularity: one node = one complete attempt

One node expansion = one headless agent episode (`claude -p <prompt>`) that runs until the
agent considers the task done, followed by evaluation (value function) of the resulting
worktree.

- **Root's children** explore *diverse approaches*: each child prompt asks the agent to
  attempt the task, optionally steered away from approaches its siblings already took.
- **Deeper children** are *revisions*: the child inherits the parent's conversation and
  code state, and its prompt includes the parent's evaluation feedback (failing tests,
  judge critique) with the instruction to repair or improve.

Why not step-level (one node = one agent action), as some research systems do:

1. A headless CLI call is naturally a whole episode; capping it to single actions fights
   the agent harness and its own planning.
2. The test-based value signal is only meaningful after a complete attempt — mid-attempt
   the tests are red for uninteresting reasons.
3. Episode nodes keep the tree shallow (depth 2–4, tens of nodes), which is what a
   several-minutes-per-node, dollars-per-run budget can afford.

This makes the MCTS action space "revision strategies" rather than "editor actions" —
the same flavor as SWE-Search's iterative refinement, and a natural fit for UCT: siblings
compete on approach, depth accumulates refinement.

## MCTS bookkeeping

Classic UCT statistics live on the node: `visits` (N), `value_sum` (W), `q = W/N`.
A node's own evaluation is `reward` ∈ [0, 1]; `backup()` propagates a value from a node
up to the root. Selection policy (UCT constant, expansion width, etc.) lives in the
search engine, not the data model.

## Selection policy knobs

The defaults are classic UCT: mean backup, descend from the root, expand the first node
with spare width. Three settings change that, all off by default.

- **`backup`.** `q = W/N` is a mean, which suits games where a position's value is an
  expectation over play. Here every node is a complete, directly scored solution and the
  goal is the single best one, so a parent with one excellent revision and one dud is a
  good place to dig even though its mean is low. `max` uses the best value backed up
  through the node (`value_max`); `mix` blends the two with `backup_mix`.
- **`selection = "global"`** scores every expandable node in the tree and takes the best,
  as SWE-Search does, instead of committing level by level. The paper's depth terms
  (`depth_bonus`, `depth_penalty`) only matter here: when descending, the candidates at
  each step are siblings, which share a depth, so the terms cancel.
- **`first_attempt_stop`** ends the search after the first attempt when it passes the
  project's tests and its judge or generated-tests score clears the threshold: a
  checkpoint in the spirit of DS-MCTS ("Learning to Stop", AAAI 2021), with a fixed
  threshold in place of a learned predictor until there are runs to learn from.
- **`give_up_after`** stops revising a line of attempts once its last N nodes all scored
  below `low_reward`.

## Parallel expansion

`parallelism = N` keeps up to N episodes in flight. The design goal was to add
concurrency without adding shared mutable state.

- **One event loop, no locks.** Episodes and evaluations overlap only while awaiting
  subprocesses. The tree, the journal and git bookkeeping are touched between awaits, so
  every journal line is still a valid tree.
- **`_begin` is synchronous.** The child node and its worktree exist before the next
  pick is made, so that pick sees the slot as taken. `_finish` runs the episode.
- **Virtual loss is computed, not stored.** A node's effective visit count is its real
  visits plus the episodes running in its subtree. That lowers its mean and exploration
  bonus while they run, and there is nothing to undo when they land or to repair after a
  crash.
- **Cost reservation.** Cost arrives only when an episode ends. Each episode in flight is
  assumed to cost the average of finished ones, and a launch that would cross the ceiling
  waits.
- **Unwinding.** On Ctrl-C or an unexpected error in one episode, every other episode is
  cancelled and awaited, so each snapshots its partial work.
- **Diversity without communication.** Siblings launched together have no summaries to
  steer away from. Each is told its slot number and asked to take the correspondingly
  ranked approach.

## Value: tests plus an optional LLM judge

The default value function runs the project's test command: exit 0 scores 1.0, otherwise
the pass ratio. That is a verifiable signal, but it cannot rank attempts once the suite
is green, and it rewards deleting the failing test.

`core/judge.py` adds the other half, following SWE-Search's Value Agent. The judge reads
the task, the node's cumulative diff from the run's base commit, and the test output, and
returns an integer in [-100, 100] with an explanation. The reward is

    w_test * test_score + w_judge * (judge + 100) / 200

Decisions worth knowing:

- **Empty diff, no judge call.** The root and any child that changed nothing get a judge
  score of 0 without spending anything. This is what breaks the tie between a no-op and
  real work on a green suite.
- **The judge is an `AgentBackend` episode** in an empty scratch directory. No second API
  integration, and the judge cannot touch the worktree it scores.
- **Judge spend is evaluation spend.** It is stored on the node separately from the
  episode's cost (`eval_cost_usd`), counts toward `max_cost_usd`, and does not count as an
  episode. A judge call whose cost figure is lost halts the search like a lost episode cost.
- **A failed judge does not fail the node.** The judge half scores 0 and the detail says why.
- **The explanation leads `eval_detail`,** so it survives prompt clipping and reaches the
  children's revision prompts (the paper's hindsight feedback).

### Final selection is a separate step

The reward guides the search; it is a poor tiebreaker. `FinalSelector` runs once the
search ends: the attempts within `select_margin` of the best (at most `select_top_k`) go
to one listwise judge call, each shown by its cumulative diff and its test output but not
its earlier critique, so the first opinion does not anchor the second. The choice is
appended to the journal as a `selection` record and `Tree.best()` returns it.

A selection is only valid for the tree it was made on. Adding a node clears it, in
memory and on journal replay, and the next run end makes a new one. Its cost stays on
the books either way. SWE-Search uses a multi-agent debate here; one call is the cheap
version of the same separation.

### Sibling ranking steers selection only

With `relative = true`, each time an attempt is evaluated its sibling group is ranked in
one judge call, by what each sibling changed relative to the shared parent. The place in
that ranking is stored as `sibling_score` (1.0 best, 0.0 worst) and blended into the
selection value by `sibling_weight`.

It is deliberately kept out of backup and out of `Tree.best()`: a rank only means
something inside its group, and both of those compare across groups. This is the
group-relative idea from GRPO applied to selection; nothing is trained. A per-parent
lock and a record of the last ranked group keep siblings that finish together from each
paying for the same ranking.

## Persistence: append-only journal

`.agent-mcts/runs/<run-id>/tree.jsonl` — first line is run metadata, then one full node
snapshot per line, last-wins per node id. Crash-safe (append-only), trivially replayable,
and doubles as the export format for future visualization. MCTS is anytime: whatever is
in the journal when you Ctrl-C is a valid, resumable tree.

## Run artifacts layout

```
<repo>/.agent-mcts/
  runs/<run-id>/tree.jsonl      # the tree
  worktrees/<run-id>/<node-id>/ # per-node worktrees (removable; branches survive)
```

The tool never touches the user's `.gitignore`; it appends `.agent-mcts/` to
`.git/info/exclude` instead.
