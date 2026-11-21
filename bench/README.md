# Benchmark: does the search reach better answers?

A small harness that compares what `agent-mcts` hands back under different settings,
scored by tests the agent never sees.

## How it works

- Each task in `tasks/<name>/` has:
  - `repo/`: what the agent gets. A spec (`README.md`), a stub, and a few visible tests.
    The visible tests (`pytest tests`) are the value command.
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
uv run python bench/run.py run --work /some/scratch/dir   # every task x 2 arms x 2 repeats
uv run python bench/run.py report [--tasks semver duration]
```

`run` calls your real agent CLI and takes a few hours with the defaults. It skips
cells that already have a file in `results/`, so it can be stopped and restarted.
Useful options: `--reps`, `--jobs`, `--nodes`, `--model`, `--tasks`, `--arms`.

## Results so far

`results/` holds one JSON file per run and `REPORT.md`, the output of `report` for each
measurement.

### Second measurement: harder tasks, five repeats

2026-11-21, v0.3.0, Claude Code with `haiku`, 5-episode budget, `judge_timeout_s = 900`.
Two tasks, five repeats per arm:

- `semver`: npm-style range matching (`^1.2.3`, `~1.2`, `1.x || >=2.0.0-rc.1`, hyphen
  ranges, the prerelease rule). The 79 hidden tests were generated from node-semver.
- `duration`: adding ISO 8601 durations to datetimes (month-end clamping, fractions,
  negative durations, strict syntax). 78 hidden tests.

Share of hidden tests passed:

| | mean | worst | episodes used | wall time |
|---|---|---|---|---|
| one attempt (the 20 first attempts) | 86% | 61% | 1 | ~4 min |
| search, tests only | 81% | 39% | 1.1 | ~5 min |
| search, with judge | 88% | 68% | 5 | ~23 min |

Each arm started from different first attempts, so the fairer comparison is within a
run: the answer the search returned against that run's own first attempt.

| | runs | better | same | worse | mean change |
|---|---|---|---|---|---|
| search, tests only | 10 | 0 | 9 | 1 | -2.2 pt |
| search, with judge | 10 | 4 | 4 | 2 | -0.1 pt |

What it shows:

- On these tasks the search did not beat a single attempt on average.
- The judge does rescue some failures that pass the visible tests: first attempts at
  73% and 85% became 86% and 90%. It also throws good answers away: in one `semver` run
  it replaced a 91% first attempt with a 68% one that it scored higher (0.91 against
  0.87).
- The judge's ranking is the limit. Among attempts that all pass the visible tests and
  differ on the hidden tests, it put the better one higher in 38 of 62 pairs, tied 4
  and got 20 wrong. The final head-to-head changed the pick in 1 of 10 runs (+1 pt).
- Revisions in the judge arm mostly held their ground: 7 better, 12 same, 1 worse,
  +1.0 pt on average.
- The tests-only search returned the first attempt unchanged in 9 of 10 runs, because
  that attempt already passed the visible tests. In the tenth the first attempt failed
  one visible test (61% hidden) and the second passed them all (39% hidden).
- The judge arm used 5x the episodes, about 5x the wall time and about 4x the cost the
  CLI reports ($1.83 against $0.44 per run).
- Ten runs per arm is still small; one run more or less changes the mean by a point or
  two.

The value command used to be `pytest -q`, which also collected scratch scripts the agent
left at the repo root (`test_manual.py`, `quick_test.py`). In two tests-only runs such a
script failed on import, so a good attempt got reward 0, and in one of them the search
swapped a 91% answer for a 68% one. The tests-only arm was re-run with `pytest tests`.
The judge arm was not: its attempts with scratch scripts all passed the visible tests,
so their rewards are the same under either command.

### First measurement: three easier tasks, two repeats

2026-11-21, v0.3.0, Claude Code with `haiku`, 5-episode budget, value command
`pytest -q`. Tasks `cron`, `deriv` and `glob`, two repeats per arm. Share of hidden tests
passed by the answer each setting offers:

| | mean | worst | episodes used | wall time |
|---|---|---|---|---|
| one attempt (the 12 first attempts) | 90% | 35% | 1 | ~3 min |
| search, tests only | 93% | 71% | 1.2 | ~3 min |
| search, with judge | 99% | 98% | 5 | ~21 min |

What it shows, and what it does not:

- The search with the judge never returned a bad answer; a single attempt did in 3 of
  12 runs. But all three bad first attempts were in the tests-only arm: every
  first attempt in the judge arm already scored 98% or more, so the judge never had a
  bad attempt to rescue.
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
