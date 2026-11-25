# Configuration

Zero config required. When you need it, `.agent-mcts.toml`:

```toml
agent = "claude"                  # or "codex"
model = "haiku"                   # optional agent-model override

[claude]
timeout_s = 1200                  # wall-clock limit for each agent episode
allowed_tools = ["Bash(go test *)"] # optional additional headless permissions

[value]
command = "pytest tests/ -x -q"   # exit code + pass ratio → score in [0, 1]
judge = true                      # also score each attempt with an LLM judge (default false)
judge_model = "haiku"             # optional cheaper model for the judge only
w_test = 0.5                      # reward = w_test * tests + w_judge * judge
w_judge = 0.5
# generate_tests = true           # or: score on tests an agent writes from the task (not with judge)
# generate_suites = 3             # how many test files to write, once per run
# generate_command = "npx jest {file}"  # runs one file; derived from `command` for pytest
# w_generated = 0.5               # reward = w_test * tests + w_generated * generated tests
# generate_reuse = ".agent-mcts/runs/<run>/generated/suites.json"  # score on an earlier run's tests

[search]
max_nodes = 20                    # episode budget
max_cost_usd = 10.0               # ceiling on costs reported by completed episodes (Claude)
c_uct = 1.414                     # UCT exploration constant — yes, it's exposed
root_width = 3                    # diverse first attempts under the root
refine_width = 2                  # revision children per node
max_depth = 3
backup = "mean"                   # "mean" (classic UCT), "max" (best in subtree) or "mix"
selection = "descend"             # or "global": best expandable node anywhere in the tree
depth_bonus = 0.0                 # with "global": + depth_bonus * exp(-depth_bonus_decay * (d - 1))
depth_penalty = 0.0               # with "global": - depth_penalty * sqrt(d)
parallelism = 1                   # episodes in flight at once (`-j`); see "Running in parallel"
give_up_after = 0                 # stop revising a line after this many low scores in a row
low_reward = 0.25                 # what counts as a low score for give_up_after
```

CLI flags (`--agent`, `-n`, `--max-cost`, `--value`, `--model`, `--agent-timeout`, `--judge/--no-judge`, `--generate-tests/--no-generate-tests`, `-j/--parallel`) override the file. The configured value command is automatically allowed inside the headless Claude session; use `claude.allowed_tools` for additional commands the agent may need. If your `claude` or `codex` binary lives somewhere unusual, point `AGENT_MCTS_CLAUDE_BIN` or `AGENT_MCTS_CODEX_BIN` at it.

## LLM judge

Tests alone stop being useful once they pass: on an already-green suite every attempt scores 1.0, the search ends after one episode, and an attempt that deleted the failing test looks perfect. With `--judge` (or `judge = true`), each attempt is also scored by an LLM, as in SWE-Search's value agent:

- The judge reads the task, the attempt's cumulative diff and the test output, and returns a score from -100 to 100 plus a one-paragraph critique. It is told to score an attempt strongly negative if tests were deleted, skipped or weakened.
- The node's reward is `w_test * tests + w_judge * judge` (the judge score is mapped to [0, 1]). An attempt that changed nothing is not judged and gets a judge score of 0, so it can no longer tie with real work.
- The critique is fed into the revision prompts of that node's children, ahead of the test output.
- The judge runs through the same agent CLI as the search (`judge_agent` and `judge_model` override that), in an empty scratch directory so it cannot edit the code it scores. Its spend counts toward `max_cost_usd` but not toward `max_nodes`.
- If the judge times out or replies without a score, the attempt is scored on its tests alone (the judge half counts as 0) instead of being marked failed.

- **Final comparison.** Per-node scores are noisy, and two good attempts often land within a point of each other. When the search ends, the attempts within `select_margin` (0.1) of the best, up to `select_top_k` (3), are compared head to head in one more judge call, and its pick is what `apply` offers. With a clear winner there is nothing to compare and no call is made. Turn it off with `select = false`.

- **Sibling ranking (optional).** With `relative = true`, the attempts that share a parent are also ranked against each other each time a new one arrives, and that standing counts in choosing which one to revise next (`[search] sibling_weight`, 0.5). It does not change rewards or what `apply` offers. It costs one more judge call per attempt after a group's first.

`agent-mcts show <node>` prints both components and the critique.

## Generated tests

The judge only reads diffs, and on the [benchmark](../bench/README.md) that was the weak point: between two attempts that both passed the visible tests, it picked the better one 38 times in 62. With `--generate-tests` (or `generate_tests = true`), attempts are scored on tests instead, written for this run before the search starts:

- A separate agent episode reads the task and a copy of the repository at the base commit, never an attempt, and replies with a test file and the test directory it belongs in. This runs `generate_suites` (3) times, in parallel, through the same agent CLI and model as the search.
- Each attempt is scored on every suite, one file at a time: the file is copied into the attempt's worktree, run with `generate_command`, and removed again, so it never lands on a node's branch. `generate_command` is derived from a pytest value command (`python -m pytest -q tests` → `python -m pytest -q -p no:cacheprovider {file}`); for other runners set it yourself, with `{file}` where the file's path goes.
- The node's reward is `w_test * tests + w_generated * generated`, where `generated` is the mean pass ratio over the suites. Failing generated tests are quoted in children's revision prompts, marked as written by another agent and possibly wrong.
- Some generated tests are wrong. On the benchmark nearly all of those failed on every attempt alike, so they barely changed the ranking: the mean over three suites picked the better attempt of a pair 58 times in 62.
- The suites are stored with the run (`.agent-mcts/runs/<run>/generated/`), and `resume` scores new attempts on the same files. Writing them is charged to the baseline evaluation, so `show n0` shows its cost; it counts toward `max_cost_usd` but not toward `max_nodes`.
- To score a new run on the tests an earlier run wrote, for instance to compare two settings on equal tests, pass `--generate-reuse <earlier run>/generated/suites.json` (or set `generate_reuse`). The file is copied into the new run, and nothing is written or charged for tests. Every suite path must stay inside the repository. Prefer the flag when the attempts must not see where the tests are: the config file is part of the repository they work in.
- If no suite could be written, every attempt scores 0 on that half and the reason appears in its detail, so the tests alone decide.
- Generated tests can share the mistake the task is about. On a bug report about misaligned Japanese text, the writer measured alignment in characters, as the buggy code did, so the unfixed code passed its tests and every fix failed them. The untouched repository is therefore offered as the answer only when every attempt does worse on your own tests; otherwise the best attempt is, and `run` and `show` print a warning when the generated tests (or the judge) scored the untouched repository higher.

It is an alternative to the judge, not an addition: turn on one of them. `agent-mcts show <node>` prints `tests=` and `generated=`.

On the benchmark (two tasks with written specs, 20 runs, `haiku`), the answer a run returned beat that run's first attempt in 16 runs and fell behind it in 2, +4.8 points of hidden tests on average; with the judge it was 11 and 4. Writing three suites cost about $0.43 per run. See [the results](../bench/README.md#third-measurement-generated-tests-20-runs-per-setting).

On a vague bug report the judge is the safer choice: there the generated tests could not tell attempts apart, and the answer was as good as the judge's only thanks to the rule above ([results](../bench/README.md#fourth-measurement-a-vague-bug-report)).

## Running in parallel

An episode takes minutes, and a sequential search runs them one after another. `-j 3` (or `parallelism = 3`) keeps up to three episodes in flight, each in its own worktree.

- **The budget holds.** Episodes in flight count toward `max_nodes`. For the dollar ceiling, each episode in flight is assumed to cost what finished ones did on average, and a launch that would cross the ceiling is held back. Cost is only reported when an episode ends, so the run can still overshoot by the episodes already in flight.
- **Picks avoid each other.** A running episode counts as a visit that has returned nothing yet (virtual loss), which steers the next pick toward a different branch.
- **Attempts started together cannot read each other's summaries.** Each is told which attempt it is and asked to take the correspondingly ranked approach, so they spread out anyway.
- **Ctrl-C** snapshots the partial work of every episode in flight.
- **What you give up:** later picks are made without the results of episodes still running, so the same budget searches a little less cleverly. Your value command also runs concurrently in different worktrees, so it must not depend on a fixed port or a shared file outside the worktree.

A good starting point is `-j` equal to `root_width` (3 by default): the first attempts are independent anyway.

## Resuming a run

`agent-mcts resume [run-id]` continues a run that was interrupted or ran out of budget. It reuses the run's journal and branches, so the tree keeps growing in place.

- The run continues under the settings it was started with (value command, judge, model, search parameters), not under whatever `.agent-mcts.toml` says today. New nodes have to be scored the way the old ones were.
- `-n` and `--max-cost` set new *totals* for the run. A run that used 12 of 12 episodes needs `resume -n 20` to run 8 more.
- If earlier episodes were cut off before reporting their cost, `resume` says so and asks before continuing. The real spend so far is then higher than the figure shown.
- An episode that was running when the process was killed outright is marked failed, and whatever its worktree held is snapshotted to its branch.

## Failures, cost and timeouts

If an episode times out, the agent process fails, or you hit Ctrl-C mid-episode, agent-mcts snapshots any partial changes onto that node's branch before cleaning up its worktree. The node remains failed and is never selected automatically, but you can inspect it with `agent-mcts show <node>` and explicitly recover it with `agent-mcts apply <node>`. These snapshots run with `--no-verify` and signing disabled so a repo's commit hooks cannot reject them; in the rare case a snapshot still fails, the worktree is left on disk and its path is printed instead of being deleted.

Claude only reports cost in its final JSON payload, so any episode cut off before that payload arrives — a timeout, a crash, unparseable output, Ctrl-C — is shown with unknown rather than zero cost, and the search stops rather than claiming that subsequent calls still fit the cost ceiling.

Some agents never report a dollar figure at all (Codex CLI meters tokens only). With such a backend `max_nodes` is the only hard budget: the plan line says so, `--max-cost` is not enforced (you get a warning if you set it), and the status line shows token counts instead of dollars.

Both the agent and the value command are killed as whole process trees (POSIX process groups; `taskkill /F /T` on Windows), so `--agent-timeout` and the value-command timeout bound the real wall clock even when a subprocess outlives its parent, and Ctrl-C does not leave a test suite running against a worktree that is about to be deleted.

> **Python projects managed with uv:** the value command runs inside fresh worktrees, which don't inherit your virtualenv — use `command = "uv run pytest -q"` (or pass `--value "uv run pytest -q"`) so each worktree resolves its own environment.

agent-mcts checks PyPI at most once a day and prints a one-line hint when a newer version exists; set `AGENT_MCTS_NO_UPDATE_CHECK=1` to disable.
