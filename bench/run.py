"""Benchmark: does the search reach better answers than one attempt?

Each task is a small repo with a spec, a stub and a few visible tests. A separate set of
hidden tests, never shown to the agent, scores every node of every run.

    python bench/run.py validate                 # hidden tests pass on the references
    python bench/run.py run --work DIR [--reps 2] [--jobs 3] [--tasks ...] [--arms ...]
    python bench/run.py report [--tasks ...]

`run` is resumable: a cell that already has a result file is skipped.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BENCH = Path(__file__).resolve().parent
TASKS = BENCH / "tasks"
RESULTS = BENCH / "results"
PYTHON = sys.executable
AGENT_MCTS = str(Path(sys.executable).with_name("agent-mcts"))

PROMPT = (
    "Implement the function specified in README.md so that it meets every requirement "
    "of the spec. The tests in tests/ cover only a few basic cases; the spec in README.md "
    "is what counts."
)

# With several cells in flight the judge's replies slow down, and at the default 300 s
# a judge timeout zeroes the judge half of a good attempt and halts the search.
ARMS = {
    "tests": "judge = false\n",
    "judge": "judge = true\njudge_timeout_s = 900\n",
}

_HIDDEN_CONFTEST = """
import signal, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _time_limit():
    def _stop(signum, frame):
        raise TimeoutError("test took longer than 5 seconds")

    signal.signal(signal.SIGALRM, _stop)
    signal.setitimer(signal.ITIMER_REAL, 5)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
"""


def task_names() -> list[str]:
    return sorted(p.name for p in TASKS.iterdir() if (p / "repo").is_dir())


def hidden_score(task: str, source: Path) -> tuple[int, int | None]:
    """Run the task's hidden tests against the solution files in `source`.

    Returns (passed, collected). `collected` is None when pytest could not even import
    the solution, which the caller counts as zero passed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "w"
        shutil.copytree(
            source,
            work,
            ignore=shutil.ignore_patterns(".git", ".agent-mcts", "__pycache__", ".pytest_cache"),
        )
        hidden = work / "_hidden"
        shutil.copytree(
            TASKS / task / "hidden", hidden, ignore=shutil.ignore_patterns("__pycache__")
        )
        (hidden / "conftest.py").write_text(_HIDDEN_CONFTEST)
        report = Path(tmp) / "report.xml"
        try:
            subprocess.run(
                [
                    PYTHON,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    str(hidden),
                    f"--junitxml={report}",
                    "--rootdir",
                    str(work),
                    "-c",
                    "/dev/null",
                ],
                cwd=work,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return 0, None
        if not report.exists():
            return 0, None
        suite = ET.parse(report).getroot()
        suite = suite if suite.tag == "testsuite" else suite.find("testsuite")
        assert suite is not None
        tests = int(suite.get("tests", 0))
        bad = sum(int(suite.get(k, 0)) for k in ("failures", "errors", "skipped"))
        cases = len(suite.findall("testcase"))
        if cases < 5:  # import error: one synthetic "collection error" case
            return 0, None
        return tests - bad, tests


def hidden_total(task: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "s"
        shutil.copytree(TASKS / task / "repo", src)
        ref = TASKS / task / "reference.py"
        module = next(p for p in (TASKS / task / "repo").glob("*.py"))
        text = ref.read_text().replace(
            'Path(__file__).resolve().parent / "hidden"',
            'Path(__file__).resolve().parent / "_hidden"',
        )
        (src / module.name).write_text(text)
        passed, total = hidden_score(task, src)
        if total is None or passed != total:
            raise SystemExit(f"{task}: reference passes {passed}/{total} hidden tests")
        return total


def cmd_validate(_: argparse.Namespace) -> None:
    for task in task_names():
        total = hidden_total(task)
        stub_passed, stub_total = hidden_score(task, TASKS / task / "repo")
        visible = subprocess.run(
            [PYTHON, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=TASKS / task / "repo",
            capture_output=True,
            text=True,
            check=False,
        )
        print(
            f"{task}: reference {total}/{total} hidden; stub {stub_passed}/{stub_total} hidden; "
            f"visible on stub: {visible.stdout.strip().splitlines()[-1]}"
        )


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def run_cell(task: str, arm: str, rep: int, work_root: Path, nodes: int, model: str) -> str:
    cell = f"{task}-{arm}-{rep}"
    result_path = RESULTS / f"{cell}.json"
    if result_path.exists():
        return f"{cell}: already done"
    repo = work_root / cell
    if repo.exists():
        shutil.rmtree(repo)
    shutil.copytree(
        TASKS / task / "repo", repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
    )
    # Point pytest at tests/: agents leave scratch scripts such as test_manual.py at the
    # repo root, and one that fails on import would zero the reward of a good attempt.
    (repo / ".agent-mcts.toml").write_text(
        f'model = "{model}"\n\n[value]\n'
        f'command = "{PYTHON} -m pytest -q -p no:cacheprovider tests"\n'
        f"{ARMS[arm]}\n[search]\nmax_nodes = {nodes}\nmax_cost_usd = 50.0\n"
    )
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(
        repo,
        "-c",
        "user.name=bench",
        "-c",
        "user.email=bench@example.invalid",
        "commit",
        "-q",
        "-m",
        "task",
    )
    started = time.time()
    with (work_root / f"{cell}.log").open("w") as log:
        proc = subprocess.run(
            [AGENT_MCTS, "run", PROMPT, "-y"],
            cwd=repo,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    wall = time.time() - started
    result = score_run(task, repo)
    result.update(
        task=task,
        arm=arm,
        rep=rep,
        wall_s=round(wall, 1),
        exit_code=proc.returncode,
        model=model,
        max_nodes=nodes,
    )
    RESULTS.mkdir(exist_ok=True)
    result_path.write_text(json.dumps(result, indent=2))
    return (
        f"{cell}: chose {result['chosen']} hidden {result['chosen_hidden']} in {wall / 60:.1f} min"
    )


def score_run(task: str, repo: Path) -> dict:
    from agent_mcts import project
    from agent_mcts.core import journal

    run_id = project.latest_run_id(repo)
    if run_id is None:
        return {"error": "no run", "nodes": [], "chosen": None, "chosen_hidden": None}
    tree = journal.load(project.journal_path(repo, run_id))
    total = hidden_total(task)
    nodes = []
    for node in tree.nodes.values():
        hidden = None
        if node.branch:
            with tempfile.TemporaryDirectory() as tmp:
                archive = subprocess.run(
                    ["git", "archive", node.branch], cwd=repo, capture_output=True, check=False
                )
                if archive.returncode == 0:
                    subprocess.run(["tar", "-x", "-C", tmp], input=archive.stdout, check=True)
                    hidden = hidden_score(task, Path(tmp))[0] / total
        nodes.append(
            {
                "id": node.id,
                "parent": node.parent_id,
                "depth": len(tree.path(node.id)) - 1,
                "status": node.status.value,
                "reward": node.reward,
                "test_score": node.test_score,
                "judge_score": node.judge_score,
                "hidden": hidden,
                "cost_usd": node.cost_usd,
                "eval_cost_usd": node.eval_cost_usd,
                "duration_s": round(node.duration_s, 1),
            }
        )
    by_id = {n["id"]: n for n in nodes}
    best = tree.best()
    by_reward = tree.best_by_reward()
    sel = tree.selection
    return {
        "run_id": run_id,
        "hidden_total": total,
        "nodes": nodes,
        "chosen": best.id if best else None,
        "chosen_hidden": by_id[best.id]["hidden"] if best else None,
        "best_by_reward": by_reward.id if by_reward else None,
        "selection": sel.model_dump(mode="json") if sel else None,
        "selections_made": len(tree.selections),
    }


def cmd_run(args: argparse.Namespace) -> None:
    work_root = Path(args.work).resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    cells = [(t, a, r) for r in range(1, args.reps + 1) for t in args.tasks for a in args.arms]
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [
            pool.submit(run_cell, t, a, r, work_root, args.nodes, args.model) for t, a, r in cells
        ]
        for future in futures:
            print(future.result(), flush=True)


def cmd_rescore(args: argparse.Namespace) -> None:
    work_root = Path(args.work).resolve()
    for path in sorted(RESULTS.glob("*.json")):
        old = json.loads(path.read_text())
        new = score_run(old["task"], work_root / path.stem)
        old.update(new)
        path.write_text(json.dumps(old, indent=2))
        print(path.stem, old["chosen"], old["chosen_hidden"])


def cmd_report(args: argparse.Namespace) -> None:
    from report import main as report_main

    report_main(args.tasks)


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(required=True)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    p = sub.add_parser("run")
    p.add_argument("--work", required=True, help="scratch directory for the task repos")
    p.add_argument("--reps", type=int, default=2)
    p.add_argument("--jobs", type=int, default=3)
    p.add_argument("--nodes", type=int, default=5)
    p.add_argument("--model", default="haiku")
    p.add_argument("--tasks", nargs="+", default=task_names())
    p.add_argument("--arms", nargs="+", default=list(ARMS))
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("rescore")
    p.add_argument("--work", required=True)
    p.set_defaults(fn=cmd_rescore)
    p = sub.add_parser("report")
    p.add_argument("--tasks", nargs="+", help="only these tasks (default: all)")
    p.set_defaults(fn=cmd_report)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
