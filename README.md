# agent-mcts 🌳

**Turn any coding agent into a tree-searching agent.**

`agent-mcts` is an open-source (MIT) test-time search harness that wraps coding agents — Claude Code, Codex CLI, Kimi CLI — with Monte Carlo Tree Search. Instead of a single trajectory that either works or doesn't, your agent explores multiple solution branches, backtracks from dead ends, and concentrates budget on the most promising path.

![agent-mcts searching, applying the best branch, and the resulting diff](demo/demo.gif)

> **Status: v0.3.** Early but real — the full loop (search → live tree → apply) works today with Claude Code and Codex CLI. Built in the open; star the repo to follow along, or [jump in](#contributing) — early contributors shape the architecture.

## Does it help?

First measurement ([bench/](bench/README.md), v0.3.0, Claude Code with `haiku`, 5-episode budget, 3 tasks × 2 repeats). Each answer is scored on hidden tests the agent never sees:

| | mean | worst | wall time |
|---|---:|---:|---:|
| single agent run | 90% | **35%** | ~3 min |
| agent-mcts, tests only | 93% | 71% | ~3 min |
| agent-mcts, with `--judge` | **99%** | **98%** | ~21 min |

What the per-run numbers show:

- **A failure the visible tests catch, the search recovers.** The 35% first attempt failed the visible tests; the search started a second, different approach from the root, and that one passed 100% of the hidden tests.
- **A failure that passes the visible tests, tests-only search keeps.** The 71% and 87% first attempts were green on the visible tests, so the search stopped there. The judge exists for this case, but in the judge runs every first attempt already scored 98% or more, so this measurement does not show yet whether the judge catches it.
- **The judge costs about 5× the episodes and 7× the wall time.**

Three tasks and two repeats cannot separate the effect from chance. Harder tasks and more repeats are next ([full report](bench/results/REPORT.md)).

## Why

Production coding agents (Claude Code, Codex, Kimi, ...) run a **single linear loop**: act, observe, retry in place. No branching, no backtracking, no principled exploration.

Research shows search helps: [SWE-Search (ICLR 2025)](https://arxiv.org/abs/2410.20285) reported a ~23% relative improvement on SWE-bench by adding MCTS on top of software agents. But that result lives in a research framework — there is no tool that brings it to the agent you already use.

`agent-mcts` closes that gap:

- **Bring your own agent.** A thin adapter layer speaks to each agent's headless mode (`claude -p`, `codex exec`, `kimi -p`). The search engine never knows which agent it's driving.
- **Real MCTS, not best-of-N.** UCT selection, expansion, evaluation, backup. Budget flows toward branches that look promising, away from dead ends.
- **State you can trust.** Every node is an isolated `git worktree` + a forked agent session. Your working tree is never touched until *you* apply a result.
- **Terminal-native.** Watch the tree grow live in your terminal. 100% Python, no browser required.

## Quickstart

```bash
uv tool install agent-mcts
cd your-project
agent-mcts run "fix the flaky test in tests/test_auth.py"
agent-mcts run --agent codex "fix the flaky test in tests/test_auth.py"
```

The run auto-detects your installed agent and your test command, then shows the plan before spending anything:

```
Agent: claude · Value: pytest -x -q · Budget: 12 nodes / $10.00
Proceed? [y/N]
```

While searching, the tree grows live in your terminal (real output):

```
task: fix the flaky test in tests/test_auth.py
run 20260802-190219 · episodes 3/12 · cost $0.31/$10.00
✓ n0 [r=0.00 Q=0.42 N=4]
├── ✓ n1 [r=0.67 Q=0.78 N=2] mocked the clock in the auth fixture
│   └── ● n3 [r=? Q=0.00 N=0] expanding…
└── ✗ n2 [r=0.00 Q=0.00 N=1] sleep-based fix (tests timed out)
```

When it finishes (or when you Ctrl-C — MCTS is anytime, the partial tree is saved):

```bash
agent-mcts show        # inspect the tree; `show n3` prints a node's prompt, summary, evaluation
agent-mcts resume      # continue the latest run where it stopped (`resume -n 20` raises the budget)
agent-mcts apply       # stage the best node's changes (squash merge — you review and commit)
agent-mcts apply n2    # ...or pick a different branch
```

## How it works

```
                ┌─────────────────────────────┐
                │        search engine        │   UCT selection · expansion
                │   (agent-agnostic, Python)  │   hybrid value fn · backup
                └──────────────┬──────────────┘
                               │ AgentBackend protocol
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
     claude adapter      codex adapter      kimi adapter
      (claude -p)        (codex exec)        (kimi -p)
```

- **Node = (git worktree, agent session).** Filesystem state branches via worktrees; conversation state branches via session forking (native on Claude Code, context-injection fallback elsewhere).
- **Value function = your tests.** Exit 0 scores 1.0; a failing run earns partial credit by pass ratio (read from pytest, jest, cargo test, go test and JUnit-style summaries), and the failing output is fed into children's revision prompts. `pytest` / `npm test` / `make test` are picked up automatically. Add `--judge` to have an LLM score each attempt's diff as well — see [LLM judge](docs/configuration.md#llm-judge).
- **Every node is a git branch** (`agent-mcts/<run>/<node>`), so `apply` is an ordinary squash merge and everything is auditable after the fact. Worktrees are disposable; branches are the record.
- **Every state change is journaled** (append-only jsonl per run), so an interrupted search is still a valid, inspectable tree.

## Supported agents

| Agent | Headless | Session fork | Status |
|---|---|---|---|
| Claude Code | `claude -p` | native (`--fork-session`) | ✅ v0.1 |
| Codex CLI | `codex exec --json` | context injection | ✅ v0.2 |
| Kimi CLI | `kimi -p` | context injection | planned (v0.4) |
| Gemini CLI, OpenCode, ... | | | [adapter PRs welcome!](#contributing) |

## Configuration

Zero config required. When you need it, `.agent-mcts.toml`:

```toml
agent = "claude"                  # or "codex"

[value]
command = "pytest tests/ -x -q"   # exit code + pass ratio → score in [0, 1]
judge = true                      # also score each attempt with an LLM judge

[search]
max_nodes = 20                    # episode budget
max_cost_usd = 10.0
parallelism = 3                   # episodes in flight at once (`-j`)
```

CLI flags (`--agent`, `-n`, `--max-cost`, `--value`, `--model`, `--agent-timeout`, `--judge/--no-judge`, `-j/--parallel`) override the file.

> **Python projects managed with uv:** the value command runs inside fresh worktrees, which don't inherit your virtualenv — use `command = "uv run pytest -q"` (or pass `--value "uv run pytest -q"`) so each worktree resolves its own environment.

Every option, the [LLM judge](docs/configuration.md#llm-judge), [parallel runs](docs/configuration.md#running-in-parallel), [resuming](docs/configuration.md#resuming-a-run) and how [failures, cost and timeouts](docs/configuration.md#failures-cost-and-timeouts) are handled: [docs/configuration.md](docs/configuration.md).

Search hyperparameters are first-class: this is built by an MCTS researcher and meant to double as a research harness for test-time search over real software tasks.

## Roadmap

- [x] **v0.1** — Claude Code adapter, UCT engine, worktree state, test-based value fn, live TUI
- [x] **v0.2** — Codex CLI adapter, episode-count budget for agents that report no dollar cost, recoverable timed-out and interrupted episodes
- [x] **v0.3** — LLM-as-judge value fn, final head-to-head selection, sibling ranking, `resume`, parallel expansion, selection policy knobs
- [ ] **v0.4** — Kimi CLI adapter, in-agent activation (`/tree` inside Claude Code via MCP), search-tree export + richer visualization, benchmark mode (same task, N agents, compare trees)

## Contributing

This project is **open source under the MIT license** and developed fully in the open — issues, design discussions, and PRs all happen here on GitHub. Contributions are welcome from day one.

The highest-impact contribution is an **agent adapter**: a few hundred lines implementing the [`AgentBackend` protocol](src/agent_mcts/adapters/base.py) for your favorite CLI agent, with no need to touch the search core. Use the [Claude Code adapter](src/agent_mcts/adapters/claude_code.py) as the reference, and the fake-binary pattern in [its tests](tests/test_claude_adapter.py) to keep CI free of API calls. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup.

Also welcome: bug reports, docs, benchmark tasks, and arguing with our UCT constants.

## License

[MIT](LICENSE) — do whatever you want, just keep the notice.
