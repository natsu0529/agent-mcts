# Benchmark: does the search reach better answers?

A small harness that compares what `agent-mcts` hands back under different settings,
scored by tests the agent never sees.

## How it works

- Each task in `tasks/<name>/` has:
  - `repo/`: what the agent gets. A spec (`README.md`), a stub, and a few visible tests.
    The visible tests are the value command.
  - `hidden/`: a thorough test suite that is never copied into the agent's repo. It
    scores every node of every run.
  - `reference.py`: a solution that passes every hidden test, to show they are
    satisfiable. It is not shown to the agent either.
- `run.py run` copies `repo/` into a fresh git repository **outside this checkout**,
  runs `agent-mcts run` there, then scores each node's branch on the hidden tests.
- Arms differ only in `.agent-mcts.toml`: `tests` (value = visible tests) and `judge`
  (`judge = true`).
- The first attempt of any run (`n1`) is what a single agent run would have produced,
  so it serves as the no-search baseline.

## Running it

```bash
uv run python bench/run.py validate                       # references pass, stubs fail
uv run python bench/run.py run --work /some/scratch/dir   # 3 tasks x 2 arms x 2 repeats
uv run python bench/run.py report
```

`run` calls your real agent CLI and takes about an hour with the defaults. It skips
cells that already have a file in `results/`, so it can be stopped and restarted.
Useful options: `--reps`, `--jobs`, `--nodes`, `--model`, `--tasks`, `--arms`.

## Results so far

`results/` holds one JSON file per run and `REPORT.md`, the output of `report`.

First measurement (2026-11-21, v0.3.0, Claude Code with `haiku`, 5-episode budget,
3 tasks x 2 repeats), share of hidden tests passed by the answer each setting offers:

| | mean | worst | episodes used | wall time |
|---|---|---|---|---|
| one attempt (the 12 first attempts) | 90% | 35% | 1 | ~3 min |
| search, tests only | 93% | 71% | 1.2 | ~3 min |
| search, with judge | 99% | 98% | 5 | ~21 min |

What it shows, and what it does not:

- The search with the judge never returned a bad answer; a single attempt did in 3 of
  12 runs.
- The tests-only search stops at the first attempt that passes the visible tests, so
  it used one episode in 5 of 6 runs and is close to a single attempt.
- The judge's scores are coarse: among attempts whose hidden scores differ, the higher
  reward went to the better one in 26 of 41 pairs. The final head-to-head comparison
  changed the pick in 2 of 6 runs and improved it both times.
- Revising an attempt that already passes the visible tests rarely helped (2 better,
  9 same, 1 worse of 12).
- This is weak evidence. Two repeats per cell cannot separate the effect from chance,
  and two of the three tasks are easy enough that the first attempt often scores
  98% or more. Harder tasks and more repeats are needed before changing defaults.
