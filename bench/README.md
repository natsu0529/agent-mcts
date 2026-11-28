# Benchmark: does the search reach better answers?

A small harness that compares what `agent-mcts` hands back under different settings,
scored by tests the agent never sees.

## How it works

- Each task in `tasks/<name>/` has:
  - `repo/`: what the agent gets. A spec (`README.md`), a stub, and a few visible tests.
    The visible tests (`pytest tests`) are the value command. A bug-fix task instead
    ships a small working library whose visible tests already pass, plus `prompt.md`,
    the bug report the agent is given in place of the default prompt.
  - `hidden/`: a thorough test suite that is never copied into the agent's repo. It
    scores every node of every run.
  - `reference.py` (or `reference/`, the fixed files at their repo paths): a solution
    that passes every hidden test, to show they are satisfiable. It is not shown to the
    agent either.
- `run.py run` copies `repo/` into a fresh git repository **outside this checkout**,
  runs `agent-mcts run` there, then scores each node's branch on the hidden tests.
- Arms differ only in `.agent-mcts.toml`: `tests` (value = visible tests), `judge`
  (`judge = true`), `generated` (`generate_tests = true`), `best-of-n` and `sequential`.
- The first attempt of any run (`n1`) is what a single agent run would have produced,
  so it serves as the no-search baseline. The `single` arm makes only that attempt
  (`max_nodes = 1`, whatever `--nodes` says), to see cheaply how hard a new task is.
  It is left out unless named in `--arms`.
- `best-of-n` is the same-budget baseline for `generated` ([#35]): every episode is a
  first attempt under the root (`root_width` = the budget, `max_depth = 1`), each told
  the approaches of the ones before it as in the search, so nothing is revised, and the
  best by reward is returned. It is scored on the very
  tests the `generated` run of the same task and repeat wrote (`--generate-reuse`), which
  `run` keeps in `results/suites/`, so the pair differs only in how the episodes were
  spent. `run` starts it after the `generated` cells. The report pairs the two per task
  and repeat, with the best best-of-N attempt by hidden score as an oracle row. A pair is
  left out, with the reason listed, when either run failed, an attempt has no hidden
  score, the model, budget or test file differ, or a run's answer is not the one today's
  selection rule returns (the `tablefmt` `generated` runs predate the v0.4.1 rule).
  Episodes and cost count every attempt, scored or not.
- `sequential` is the second same-budget baseline ([#36]): one chain in which each
  attempt revises the one before it with its failing test output (`root_width = 1`,
  `refine_width = 1`, `max_depth` = the budget, `give_up_after = 0`), and the best
  attempt along the chain by reward is returned. It reuses the same tests and is paired
  the same way.

[#35]: https://github.com/natsu0529/agent-mcts/issues/35
[#36]: https://github.com/natsu0529/agent-mcts/issues/36

## Running it

```bash
uv run python bench/run.py validate                       # references pass, stubs fail
uv run python bench/run.py run --work /some/scratch/dir   # every task x every arm x 2 repeats
uv run python bench/run.py report [--tasks semver duration]
```

`run` calls your real agent CLI and takes a few hours with the defaults. It skips
cells that already have a file in `results/`, so it can be stopped and restarted.
Useful options: `--reps`, `--jobs`, `--nodes`, `--model`, `--tasks`, `--arms`.

To add a baseline to `generated` runs that already exist, run only that arm; it reuses
their tests from `results/suites/`:

```bash
uv run python bench/run.py run --work /some/scratch/dir --arms best-of-n \
  --tasks semver duration --reps 10
```

To see how hard a new task is before spending a full run on it, make a few single
attempts. Each costs about as much as a first attempt, a few tens of cents with
`haiku`:

```bash
uv run python bench/run.py run --work /some/scratch/dir --arms single --tasks mytask --reps 5
uv run python bench/run.py report --tasks mytask
```

Runs made before `results/suites/` existed can have their tests copied in with
`run.py collect-suites --work <their --work dir>`.

### Which model

`--model` is passed to Claude Code as it is, and an alias such as `haiku` moves to a newer
model when one is released. On 2026-11-27 `haiku` resolved to `claude-haiku-5-5`, and on
that model single attempts (one episode each) passed every hidden test of `semver` and
`duration` (5 of 5 each), at about $0.03 per attempt. With `claude-haiku-4-5`, four
single attempts on `semver` passed 49-94% (mean 69%) at $0.12-0.49 per attempt, in line
with the measurements below, which were all made with `haiku` before that date and so
were most likely made with `claude-haiku-4-5`.

So the default is now the full ID `claude-haiku-4-5`, which keeps new runs comparable with
the ones below. `run` also records the ID the model resolved to (`model_id`) and the
Claude Code version in each result, and the report pairs two runs only when both have the
same `model_id`. Results made before 2026-11-27 have none.

## Results so far

`results/` holds one JSON file per run and `REPORT.md`, the output of `report` for each
measurement.

### Sixth measurement: sequential revision at the same budget

2026-11-25, same setup as the fifth measurement: for each of the 20 `generated` runs on
semver and duration, one `sequential` run, a single chain in which each attempt revises
the one before it with its failing test output, five episodes, no branching ([#36]).
Scored on the same, byte-identical suite files. With best-of-N this gives four ways to
spend the same budget:

| how the five episodes are spent | hidden score | better / worse than its first attempt | cost per run |
|---|---:|---|---:|
| one attempt (a single agent run) | 83-88% | | |
| best-of-N: five first attempts, each told the earlier approaches | 93% | 11 / 1 | $1.78 |
| sequential: one attempt, four revisions in a line | 92% | 19 / 0 | $2.34 |
| search: the tree | 93% | 16 / 2 | $2.01 |

Paired by task and repeat (difference in hidden-test points with its 95% interval, runs
won, two-sided sign test):

| | difference | runs | p |
|---|---:|---|---:|
| search - best-of-N | +0.1 (-3.7 to +3.9) | 11 - 8 | 0.65 |
| search - sequential | +1.5 (-2.5 to +5.5) | 12 - 6 | 0.24 |
| best-of-N - sequential | +1.5 (-2.6 to +5.6) | 9 - 6 | 0.61 |

What it shows:

- No pair of the three differs detectably on these two tasks at five episodes. The
  intervals span about ±4 points, so differences of a few points either way remain
  possible.
- Revising with the failing tests improves on the starting attempt: the chain beat its
  first attempt in 19 runs of 20 and was never below it (+8.3 points, from first
  attempts that happened to be weaker, 83%). Within this measurement, the diverse
  first attempts of best-of-N ended at a similar score.
- What all three have in common is several attempts and a pick by generated tests.
  These runs cannot say which ingredient matters most beyond that.
- Cost per run: best-of-N $1.78, search $2.01, sequential $2.34. Revisions resume the
  session and carry its context. Test writing, shared by all arms, is left out.
- The caveats of the fifth measurement apply: an imperfect scorer (no suite passes
  completely on the reference; leaving out `duration` repeat 5, whose suite does not
  collect, moves search - sequential from +1.5 to +1.4 points), two tasks, one model,
  five episodes.

### Fifth measurement: best-of-N at the same budget

2026-11-25, v0.4.1 plus `--generate-reuse`, Claude Code with `haiku`, 5-episode budget.
For each of the 20 `generated` runs of the third measurement (semver and duration, 10
each), one `best-of-n` run: five first attempts under the root and no revisions, scored
on the very tests that run wrote ([#35]). The suite file is byte-identical across each
pair (`suites_sha256` in the results). The five attempts are not independent samples:
as in the search, each is told the approaches of the attempts before it and asked to
take a different one, the stronger of the two baselines #35 discussed.

| | search better | same | best-of-N better | search | best-of-N | difference (95% interval) | p | cost per run |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| duration | 7 | 1 | 2 | 96% | 96% | +0.4 (-2.2 to +3.0) pt | 0.18 | $1.57 / $1.59 |
| semver | 4 | 0 | 6 | 90% | 91% | -0.3 (-8.3 to +7.8) pt | 0.75 | $2.45 / $1.98 |
| both | 11 | 1 | 8 | 93% | 93% | +0.1 (-3.7 to +3.9) pt | 0.65 | $2.01 / $1.78 |

Difference: search minus best-of-N on the hidden tests, paired by task and repeat; p:
two-sided sign test. Cost leaves out writing the tests, which each pair shares.

What it shows:

- No difference between the search and best-of-N was detected on these two tasks at
  five episodes. The interval, about ±3.8 points, is wide enough to hide a real
  difference of a few points either way. The search cost about 13% more per run.
- Best-of-N improved on its own first attempt about as often as the search: better in
  11 runs and worse in 1 (+4.6 points), against 16 and 2 (+4.8).
- The revisions in the search raised the hidden score 32 times in 40, yet the search
  did not end higher than best-of-N. These runs do not show that the tree or the
  revisions add anything over diverse first attempts picked by generated tests; they
  cannot rule it out either.
- The generated tests are an imperfect scorer. Run against the reference
  implementations, none of the 60 suites used here passes completely (mean 94% of
  their tests pass), and one suite of `duration` repeat 5 does not even collect. Both
  arms of a pair are scored on the same suites, and leaving that repeat out leaves the
  difference at +0.07 points.
- In these runs the path of the reused suite file sat in the `.agent-mcts.toml`
  committed to the task repository, where an attempt could have read it. The attempts'
  transcripts show the file only in directory listings, never its contents. The path is
  now passed on the command line instead.
- Two tasks, one model, five episodes. A larger budget, harder tasks or another model
  may change this.

### Fourth measurement: a vague bug report

2026-11-22, v0.4.0, Claude Code with `haiku`, 5-episode budget, 10 runs per arm. The
`tablefmt` task is a small table-printing library and a user's bug report: "Columns
don't line up when cells contain Japanese", with one misaligned example. There is no
spec. The cause is `len()` used as display width in four places (column widths,
padding, truncation, wrapping); file names from macOS can also hold decomposed
characters whose combining marks take no width. The visible tests pass before any fix,
so the tests-only arm has nothing to search on and was not run. 71 hidden tests; the
unfixed library passes 29 (41%), a fix of the reported symptom alone about 70%.

| | runs | better | same | worse | first attempt | answer returned | cost per run |
|---|---|---|---|---|---|---|---|
| search, with judge | 10 | 3 | 7 | 0 | 91% | 93% | $1.67 |
| search, with generated tests (v0.4.0) | 10 | 0 | 0 | 10 | 88% | 41% | $2.20 |
| the same trees, picked with the baseline rule | 10 | 4 | 6 | 0 | 88% | 93% | |

What it shows:

- Generated tests failed in a systematic way. Every run, the test writer checked
  alignment by the character position of `|` in each line, which is the same mistake as
  the bug. Those tests pass on the unfixed code and fail on a correct fix, so the
  untouched repository scored highest and v0.4.0 returned it in all 10 runs, although
  the trees held attempts at 92% to 100%.
- Among the attempts themselves the generated tests were not wrong, only blind: of 54
  pairs that differ on the hidden tests they ranked 14 correctly, tied 40 and got none
  backwards.
- Since then the untouched repository is offered only when every attempt does worse on
  the project's own tests, and a warning says when the judge or the generated tests
  preferred it. Recomputed on the same ten trees, the answer improves on the first
  attempt in 4 runs and is never worse (88% to 93%), level with the judge.
- The judge did well here: it reads the diff instead of running tests, so the shared
  misunderstanding did not affect it. It rarely improved on the first attempt, which
  almost always scored 92% (every attempt missed only the combining marks).
- Generated tests are only as good as the writer's understanding of the task. When the
  task is about a misunderstanding the writer shares, they reward the bug.

### Third measurement: generated tests, 20 runs per setting

2026-11-22, v0.3.0 plus `generate_tests` (not yet released), Claude Code with `haiku`
for every role, 5-episode budget. The same two tasks as the second measurement
(below), 20 runs per arm. Runs 1-5 of `tests` and `judge` are the second measurement's.

The answer each run returned, against that run's own first attempt:

| | runs | better | same | worse | mean change | worst answer | wall time | cost per run |
|---|---|---|---|---|---|---|---|---|
| search, tests only | 20 | 1 | 18 | 1 | -0.6 pt | 39% | ~5 min | $0.43 |
| search, with judge | 20 | 11 | 5 | 4 | +5.4 pt | 68% | ~21 min | $1.81 |
| search, with generated tests | 20 | 16 | 2 | 2 | +4.8 pt | 81% | ~19 min | $2.44 |

Share of hidden tests passed, mean over runs:

| | first attempt | answer returned | best attempt in the tree |
|---|---|---|---|
| search, tests only | 84% | 83% | 84% |
| search, with judge | 83% | 88% | 91% |
| search, with generated tests | 88% | 93% | 94% |

What it shows:

- With generated tests the search beat a single attempt: better in 16 runs, worse in 2.
  If search made no difference, a split this lopsided would happen about once in 800
  (two-sided sign test, p = 0.001). The gain held on both tasks: `semver` 8 better,
  1 worse, +6.1 pt; `duration` 8 better, 1 worse, +3.6 pt. The two losses were small
  (-4 pt and -1 pt).
- Most of the gain comes from revisions, not from picking. A failing generated test is
  something concrete to fix: 32 of 40 revisions raised the hidden score (the judge arm:
  13 of 40), and in 13 of 20 runs the answer returned beat all three first attempts
  (the judge arm: 3 of 20).
- The reward ranks attempts better. Among attempts in the same run whose hidden scores
  differ, the higher reward went to the better one in 155 of 189 pairs, with 6 ties
  (judge: 119 of 170, with 11 ties). The answer returned was the best attempt in the
  tree in 14 of 20 runs (judge: 9).
- The judge arm's mean gain is about as large, but it comes from two runs whose first
  attempt was broken (19% and 37%, both rescued to 81% and 84%). Over the other 18 runs
  its mean change is +0.0 pt (9 better, 4 worse), and it still returned a 68% answer
  in place of a 91% first attempt.
- Writing the three suites cost $0.36-0.60 per run and took a few minutes. Revisions
  also do more work, so a run costs about a third more than with the judge.
- The limits: two tasks, both with a precise written spec, which is the easiest case for
  writing tests from the spec; one model; 20 runs per arm.

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
