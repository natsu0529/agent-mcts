"""Experiment: do tests an agent writes from the spec rank attempts like the hidden tests?

The LLM judge reads diffs and never runs anything. Here an agent sees only the task's
spec, stub and visible tests, writes a pytest file, and every attempt a benchmark run
produced is scored on it. The question is whether that score separates attempts that
all pass the visible tests, which is where the judge was right 38 times in 62.

    python bench/spec_tests.py extract --work DIR --out OUT   # attempts from `run.py run`
    python bench/spec_tests.py generate --out OUT [--suites 3] [--model haiku]
    python bench/spec_tests.py score --out OUT
    python bench/spec_tests.py report --out OUT

Every step skips what is already in OUT, so it can be stopped and restarted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean

from run import _HIDDEN_CONFTEST, PYTHON, RESULTS, TASKS

DEFAULT_TASKS = ["semver", "duration"]

GENERATE_PROMPT = """\
You are writing a test suite for a function that someone else will implement. You will \
not see their code.

Below are the spec (README.md), the stub the implementation must fill in, and the few \
tests that already exist.

Write a pytest file that checks the spec thoroughly: every rule, the edge cases, and the \
error cases the spec defines.

- Test only what the spec states. If the spec does not decide a case, leave it out.
- One behaviour per test, so a single bug fails only a few tests. Use \
`pytest.mark.parametrize` for tables of cases.
- Import the function the same way the existing tests do.
- Work out each expected value from the spec, carefully. A wrong expected value is worse \
than a missing test.

Reply with the complete file in a single ```python code block and nothing else.

## README.md

{readme}

## {stub_name}

```python
{stub}
```

## Existing tests ({tests_name})

```python
{tests}
```
"""

_CODE_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)


def module_name(task: str) -> str:
    return next((TASKS / task / "repo").glob("*.py")).name


# --- extract ---------------------------------------------------------------------------


def cmd_extract(args: argparse.Namespace) -> None:
    """Copy each attempt's solution file out of the run repos, with its scores."""
    work = Path(args.work).resolve()
    for task in args.tasks:
        module = module_name(task)
        dest = Path(args.out) / "candidates" / task
        dest.mkdir(parents=True, exist_ok=True)
        meta: dict[str, dict] = {}
        for path in sorted(RESULTS.glob(f"{task}-*.json")):
            run = json.loads(path.read_text())
            cell = path.stem
            for node in run["nodes"]:
                if node["parent"] is None or node["hidden"] is None:
                    continue
                branch = f"agent-mcts/{run['run_id']}/{node['id']}"
                shown = subprocess.run(
                    ["git", "show", f"{branch}:{module}"],
                    cwd=work / cell,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if shown.returncode != 0:
                    print(f"{cell} {node['id']}: no {module} on {branch}", file=sys.stderr)
                    continue
                name = f"{cell}-{node['id']}"
                (dest / f"{name}.py").write_text(shown.stdout)
                visible = node["test_score"] if node["test_score"] is not None else node["reward"]
                meta[name] = {
                    "run": cell,
                    "arm": run["arm"],
                    "node": node["id"],
                    "hidden": node["hidden"],
                    "visible": visible,
                    "judge": node["judge_score"],
                    "chosen": run["chosen"] == node["id"],
                }
        (dest / "meta.json").write_text(json.dumps(meta, indent=2))
        print(f"{task}: {len(meta)} attempts")


# --- generate --------------------------------------------------------------------------


def generate_prompt(task: str) -> str:
    repo = TASKS / task / "repo"
    stub = next(repo.glob("*.py"))
    tests = next((repo / "tests").glob("test_*.py"))
    return GENERATE_PROMPT.format(
        readme=(repo / "README.md").read_text(),
        stub_name=stub.name,
        stub=stub.read_text(),
        tests_name=f"tests/{tests.name}",
        tests=tests.read_text(),
    )


async def generate_one(task: str, model: str, index: int, out: Path) -> str:
    from agent_mcts.adapters.claude_code import ClaudeCodeAdapter

    path = out / "suites" / task / f"{model}-{index}.py"
    if path.exists():
        return f"{path.name}: already done"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same as the judge: an empty scratch repo, so the writer sees only the prompt.
    adapter = ClaudeCodeAdapter(model=model, permission_mode="default", timeout_s=900)
    with tempfile.TemporaryDirectory(prefix="spec-tests-") as scratch:
        subprocess.run(["git", "init", "-q"], cwd=scratch, check=True)
        result = await adapter.run_episode(generate_prompt(task), Path(scratch))
    block = _CODE_BLOCK.search(result.summary or "")
    if result.is_error or block is None:
        return f"{task} {model}-{index}: no code block ({(result.summary or '')[:200]!r})"
    path.write_text(block.group(1))
    cost = path.with_suffix(".cost.json")
    cost.write_text(json.dumps({"cost_usd": result.cost_usd, "duration_s": result.duration_s}))
    return f"{task} {model}-{index}: {len(block.group(1).splitlines())} lines"


async def generate_all(args: argparse.Namespace) -> None:
    out = Path(args.out)
    limit = asyncio.Semaphore(args.jobs)

    async def one(task: str, index: int) -> None:
        async with limit:
            print(await generate_one(task, args.model, index, out), flush=True)

    await asyncio.gather(*(one(t, i) for t in args.tasks for i in range(1, args.suites + 1)))


def cmd_generate(args: argparse.Namespace) -> None:
    asyncio.run(generate_all(args))


# --- score -----------------------------------------------------------------------------


def run_suite(task: str, solution: str, suite: Path) -> dict[str, bool] | None:
    """Per-test results of `suite` against one solution file; None if nothing ran."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / module_name(task)).write_text(solution)
        tests = work / "tests"
        tests.mkdir()
        shutil.copy(suite, tests / f"test_{suite.stem.replace('-', '_')}.py")
        (tests / "conftest.py").write_text(_HIDDEN_CONFTEST)
        report = work / "report.xml"
        try:
            subprocess.run(
                [
                    PYTHON,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    str(tests),
                    f"--junitxml={report}",
                    "--rootdir",
                    str(work),
                    "-c",
                    "/dev/null",
                ],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return None
        if not report.exists():
            return None
        results = {}
        for case in ET.parse(report).getroot().iter("testcase"):
            name = f"{case.get('classname')}::{case.get('name')}"
            results[name] = not any(case.find(t) is not None for t in ("failure", "error"))
        # A solution that fails to import yields one synthetic collection-error case.
        if len(results) <= 1 and not all(results.values()):
            return None
        return results


def suites_for(task: str, out: Path) -> dict[str, Path]:
    suites = {p.stem: p for p in sorted((out / "suites" / task).glob("*.py"))}
    suites["hidden"] = next((TASKS / task / "hidden").glob("test_*.py"))
    return suites


def cmd_score(args: argparse.Namespace) -> None:
    out = Path(args.out)
    for task in args.tasks:
        cand_dir = out / "candidates" / task
        solutions = {p.stem: p.read_text() for p in sorted(cand_dir.glob("*.py"))}
        solutions["reference"] = (TASKS / task / "reference.py").read_text()
        for suite_name, suite in suites_for(task, out).items():
            dest = out / "scores" / task / f"{suite_name}.json"
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                futures = {n: pool.submit(run_suite, task, s, suite) for n, s in solutions.items()}
                matrix = {n: f.result() for n, f in futures.items()}
            dest.write_text(json.dumps(matrix, indent=1))
            ref = matrix["reference"]
            ref_text = "-" if ref is None else f"{sum(ref.values())}/{len(ref)}"
            print(f"{task} {suite_name}: reference passes {ref_text}", flush=True)


# --- report ----------------------------------------------------------------------------


def ratio(results: dict[str, bool] | None, keep: set[str] | None = None) -> float:
    if results is None:
        return 0.0
    names = [n for n in results if keep is None or n in keep]
    return sum(results[n] for n in names) / len(names) if names else 0.0


def pair_counts(items: list[tuple[float, float]]) -> tuple[int, int, int]:
    """(agree, tie, disagree) over pairs whose hidden scores differ; items are (hidden, score)."""
    agree = tie = disagree = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            dh = items[i][0] - items[j][0]
            if abs(dh) < 1e-9:
                continue
            ds = items[i][1] - items[j][1]
            if abs(ds) < 1e-9:
                tie += 1
            elif (ds > 0) == (dh > 0):
                agree += 1
            else:
                disagree += 1
    return agree, tie, disagree


def cmd_report(args: argparse.Namespace) -> None:
    out = Path(args.out)
    print("## Generated suites\n")
    print("| task | suite | tests | reference passes | lines |")
    print("|---|---|---|---|---|")
    scored: dict[str, dict[str, dict]] = {}
    for task in args.tasks:
        meta = json.loads((out / "candidates" / task / "meta.json").read_text())
        scored[task] = {}
        for path in sorted((out / "scores" / task).glob("*.json")):
            matrix = json.loads(path.read_text())
            scored[task][path.stem] = matrix
            ref = matrix["reference"] or {}
            lines = "-"
            suite_file = out / "suites" / task / f"{path.stem}.py"
            if suite_file.exists():
                lines = str(len(suite_file.read_text().splitlines()))
            print(
                f"| {task} | {path.stem} | {len(ref)} | {sum(ref.values())}/{len(ref)} | {lines} |"
            )
        missing = [n for n in meta if n not in scored[task].get("hidden", {})]
        if missing:
            print(f"\n{task}: {len(missing)} attempts not scored", file=sys.stderr)

    print("\n## Hidden scores reproduced from the extracted files\n")
    for task in args.tasks:
        meta = json.loads((out / "candidates" / task / "meta.json").read_text())
        hidden = scored[task]["hidden"]
        off = [n for n, m in meta.items() if abs(ratio(hidden.get(n)) - m["hidden"]) > 1e-9]
        print(f"- {task}: {len(meta) - len(off)} of {len(meta)} match the benchmark's score")
        for n in off:
            print(f"  - {n}: benchmark {meta[n]['hidden']:.3f}, here {ratio(hidden.get(n)):.3f}")

    print("\n## Ranking pairs of visible-green attempts in the same judge run\n")
    print("Same pairs as the judge's 38 agree / 4 tie / 20 disagree. `oracle-filtered` drops")
    print("generated tests the reference fails, to show what correct generated tests would do.\n")
    print("| scorer | agree | tie | disagree |")
    print("|---|---|---|---|")
    rows: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for task in args.tasks:
        meta = json.loads((out / "candidates" / task / "meta.json").read_text())
        runs: dict[str, list[str]] = defaultdict(list)
        for name, m in meta.items():
            if m["arm"] == "judge" and m["visible"] == 1.0:
                runs[m["run"]].append(name)
        suites = {s: mtx for s, mtx in scored[task].items() if s != "hidden"}
        for names in runs.values():
            scorers: dict[str, list[float]] = {"judge": [meta[n]["judge"] for n in names]}
            for s, mtx in suites.items():
                scorers[s] = [ratio(mtx.get(n)) for n in names]
                keep = {t for t, ok in (mtx["reference"] or {}).items() if ok}
                scorers[f"{s} oracle-filtered"] = [ratio(mtx.get(n), keep) for n in names]
            if suites:
                scorers["mean of generated"] = [
                    mean(ratio(mtx.get(n)) for mtx in suites.values()) for n in names
                ]
            for label, scores in scorers.items():
                items = [(meta[n]["hidden"], sc) for n, sc in zip(names, scores, strict=True)]
                for k, v in enumerate(pair_counts(items)):
                    rows[label][k] += v
    for label, (a, t, d) in rows.items():
        print(f"| {label} | {a} | {t} | {d} |")

    print("\n## Picking one answer per judge run\n")
    print("Each judge run's attempts, ranked by visible tests first, then by the scorer;")
    print("ties go to the earliest node. Hidden-test score of the pick, mean over runs.\n")
    print("| pick by | mean hidden | better than first | same | worse |")
    print("|---|---|---|---|---|")
    picks: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for task in args.tasks:
        meta = json.loads((out / "candidates" / task / "meta.json").read_text())
        runs = defaultdict(list)
        for name, m in meta.items():
            if m["arm"] == "judge":
                runs[m["run"]].append(name)
        suites = {s: mtx for s, mtx in scored[task].items() if s != "hidden"}
        for names in runs.values():
            names.sort(key=lambda n: int(meta[n]["node"][1:]))
            first = next(meta[n]["hidden"] for n in names if meta[n]["node"] == "n1")
            chosen = next(meta[n]["hidden"] for n in names if meta[n]["chosen"])
            picks["agent-mcts as run (judge)"].append((first, chosen))
            scorers = {s: {n: ratio(mtx.get(n)) for n in names} for s, mtx in suites.items()}
            if suites:
                scorers["mean of generated"] = {
                    n: mean(ratio(mtx.get(n)) for mtx in suites.values()) for n in names
                }
            for label, score in scorers.items():
                best = max(names, key=lambda n: (meta[n]["visible"], score[n], -names.index(n)))
                picks[label].append((first, meta[best]["hidden"]))
    for label, pairs in picks.items():
        d = [c - f for f, c in pairs]
        print(
            f"| {label} | {100 * mean(c for _, c in pairs):.1f}% | {sum(x > 1e-9 for x in d)} | "
            f"{sum(abs(x) <= 1e-9 for x in d)} | {sum(x < -1e-9 for x in d)} |"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(required=True)
    for name, fn in [
        ("extract", cmd_extract),
        ("generate", cmd_generate),
        ("score", cmd_score),
        ("report", cmd_report),
    ]:
        p = sub.add_parser(name)
        p.add_argument("--out", required=True, help="directory for candidates, suites, scores")
        p.add_argument("--tasks", nargs="+", default=DEFAULT_TASKS)
        p.add_argument("--jobs", type=int, default=3)
        p.set_defaults(fn=fn)
        if name == "extract":
            p.add_argument("--work", required=True, help="the --work directory of run.py run")
        if name == "generate":
            p.add_argument("--suites", type=int, default=3)
            p.add_argument("--model", default="haiku")
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
