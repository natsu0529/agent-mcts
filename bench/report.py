"""Summarise bench/results/*.json as markdown tables."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

RESULTS = Path(__file__).resolve().parent / "results"


def pct(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.0f}%"


def episodes(run: dict) -> list[dict]:
    """Every agent attempt (not the untouched root), scored on the hidden tests or not.

    Episode counts and spend come from these, so a node the hidden tests could not
    score still counts as the episode it was and the money it cost.
    """
    return [n for n in run["nodes"] if n["parent"] is not None]


def attempts(run: dict) -> list[dict]:
    """Agent attempts that were scored on the hidden tests."""
    return [n for n in episodes(run) if n["hidden"] is not None]


def unscored(run: dict) -> list[dict]:
    """Attempts that finished and were evaluated but have no hidden score."""
    return [n for n in episodes(run) if n["status"] == "evaluated" and n["hidden"] is None]


def attempt_cost(run: dict) -> float:
    """Dollars spent on the attempts and their scoring, without writing the tests.

    The root's evaluation carries the test writer's spend in a `generated` run and
    nothing in a baseline run, which reuses those tests, so it is left out of both.
    """
    return sum((n["cost_usd"] or 0.0) + (n["eval_cost_usd"] or 0.0) for n in episodes(run))


def current_choice(run: dict) -> str | None:
    """The answer today's rule would return from this run's nodes.

    Mirrors `Tree.best_by_reward`: the untouched root is a candidate only when every
    attempt does worse than it on the project's own tests. Results from before that
    rule (v0.4.0) can hold a different `chosen`.
    """
    scored = [n for n in run["nodes"] if n["status"] == "evaluated" and n["reward"] is not None]
    if not scored:
        return None

    def own(n: dict) -> float:
        return n["test_score"] if n["test_score"] is not None else n["reward"] or 0.0

    root = next((n for n in scored if n["parent"] is None), None)
    if root is not None:
        kept = [n for n in scored if n is not root and own(n) >= own(root)]
        if kept:
            scored = kept
    return max(scored, key=lambda n: (n["reward"] or 0.0, n["depth"]))["id"]


def mean_or_none(values: list[float]) -> float | None:
    return mean(values) if values else None


# Two-sided 95% quantiles of Student's t, by degrees of freedom (normal beyond 30).
_T975 = [12.71, 4.30, 3.18, 2.78, 2.57, 2.45, 2.36, 2.31, 2.26, 2.23, 2.20, 2.18, 2.16, 2.14,
         2.13, 2.12, 2.11, 2.10, 2.09, 2.09, 2.08, 2.07, 2.07, 2.06, 2.06, 2.06, 2.05, 2.05,
         2.05, 2.04]  # fmt: skip


def interval(diffs: list[float]) -> str:
    """Mean of paired differences in points, with its 95% t interval."""
    if len(diffs) < 2:
        return "-"
    t = _T975[len(diffs) - 2] if len(diffs) - 1 <= len(_T975) else 1.96
    m, half = mean(diffs), t * stdev(diffs) / math.sqrt(len(diffs))
    return f"{100 * m:+.1f} ({100 * (m - half):+.1f} to {100 * (m + half):+.1f})"


def sign_test(wins: int, losses: int) -> str:
    n = wins + losses
    if n == 0:
        return "-"
    tail = sum(math.comb(n, i) for i in range(min(wins, losses) + 1)) / 2**n
    return f"{min(1.0, 2 * tail):.2f}"


def unpaired_reason(search: dict, baseline: dict) -> str | None:
    """Why two runs of the same cell cannot be compared, or None when they can."""
    for run in (search, baseline):
        if run.get("error") or run.get("exit_code") or run.get("chosen_hidden") is None:
            return f"{run['arm']} run did not finish scoring"
        if missing := unscored(run):
            return f"{run['arm']} run has {len(missing)} attempt(s) without a hidden score"
        # A final head-to-head pick is stored as such; otherwise the answer must be the
        # one today's rule returns, or the two runs were picked by different rules.
        if run.get("selection") is None and run.get("chosen") != current_choice(run):
            return f"{run['arm']} run picked its answer with an older rule"
    if search.get("model") != baseline.get("model"):
        return "different models"
    if search.get("max_nodes") != baseline.get("max_nodes"):
        return "different budgets"
    if not search.get("suites_sha256") or search.get("suites_sha256") != baseline.get(
        "suites_sha256"
    ):
        return "not scored on the same generated tests"
    if baseline.get("suites_from") != search.get("run_id"):
        return "reused the tests of another generated run"
    return None


BASELINES = {
    "best-of-n": (
        "best-of-N",
        "Best-of-N spends the same episode budget on first attempts under the root, with "
        "no revisions. They are not independent samples: like the search's first "
        "attempts, each is told the approaches of the ones before it and asked to take "
        "a different one.",
    ),
}


def same_budget(runs: list[dict]) -> None:
    """The search against each same-budget baseline, paired by task and repeat."""
    for arm, (name, how) in BASELINES.items():
        same_budget_against(runs, arm, name, how)


def same_budget_against(runs: list[dict], baseline: str, name: str, how: str) -> None:
    by_cell = {(r["task"], r["arm"], r["rep"]): r for r in runs}
    pairs: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    excluded: list[str] = []
    for (task, arm, rep), other in sorted(by_cell.items()):
        search = by_cell.get((task, "generated", rep))
        if arm != baseline or search is None:
            continue
        reason = unpaired_reason(search, other)
        if reason:
            excluded.append(f"{task} {rep}: {reason}")
            continue
        pairs[task].append((search, other))
        pairs["ALL"].append((search, other))
    if not pairs and not excluded:
        return
    print(f"\n## Same budget and generated tests: the search against {name}\n")
    print(how)
    print("Each pair is the `generated` run and the baseline run of the same task and")
    print("repeat, with the same model and budget, both scored on the same generated test")
    print("file (checked by hash). Difference: search minus baseline in hidden-test points,")
    print(f"with a 95% t interval; p: two-sided sign test. Oracle: the best {name} attempt")
    print("by hidden score. Cost leaves out writing the tests, which the pair shares.\n")
    print(
        f"| task | pairs | search better | same | {name} better | search | {name} "
        f"| difference | p | oracle {name} | episodes | cost per run |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for task, ps in sorted(pairs.items(), key=lambda kv: (kv[0] == "ALL", kv[0])):
        d = [s["chosen_hidden"] - b["chosen_hidden"] for s, b in ps]
        wins, losses = sum(x > 1e-9 for x in d), sum(x < -1e-9 for x in d)
        oracle = [max((n["hidden"] for n in attempts(b)), default=0.0) for _, b in ps]
        print(
            f"| {task} | {len(ps)} | {wins} | {len(d) - wins - losses} | {losses} | "
            f"{pct(mean(s['chosen_hidden'] for s, _ in ps))} | "
            f"{pct(mean(b['chosen_hidden'] for _, b in ps))} | {interval(d)} | "
            f"{sign_test(wins, losses)} | {pct(mean(oracle))} | "
            f"{mean(len(episodes(s)) for s, _ in ps):.1f} / "
            f"{mean(len(episodes(b)) for _, b in ps):.1f} | "
            f"${mean(attempt_cost(s) for s, _ in ps):.2f} / "
            f"${mean(attempt_cost(b) for _, b in ps):.2f} |"
        )
    if excluded:
        print(f"\nNot paired ({len(excluded)}): " + "; ".join(excluded) + ".")


def main(tasks: list[str] | None = None) -> None:
    runs = [json.loads(p.read_text()) for p in RESULTS.glob("*.json")]
    runs.sort(key=lambda r: (r["task"], r["arm"], r["rep"]))
    if tasks:
        runs = [r for r in runs if r["task"] in tasks]
    if not runs:
        print("no results")
        return
    print("## Per run\n")
    print(
        "| task | arm | rep | episodes | first attempt | chosen | best in tree "
        "| mean of attempts | min |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for r in runs:
        a = attempts(r)
        first = next((n["hidden"] for n in r["nodes"] if n["id"] == "n1"), None)
        print(
            f"| {r['task']} | {r['arm']} | {r['rep']} | {len(episodes(r))} | {pct(first)} | "
            f"{pct(r['chosen_hidden'])} ({r['chosen']}) | "
            f"{pct(max((n['hidden'] for n in a), default=None))} | "
            f"{pct(mean_or_none([n['hidden'] for n in a]))} | "
            f"{(r.get('wall_s') or 0) / 60:.1f} |"
        )

    print("\n## Per task and arm (mean over repeats)\n")
    print("| task | arm | runs | episodes | first attempt | chosen | best in tree | min |")
    print("|---|---|---|---|---|---|---|---|")
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in runs:
        groups[(r["task"], r["arm"])].append(r)
        groups[("ALL", r["arm"])].append(r)
    for (task, arm), rs in sorted(groups.items(), key=lambda kv: (kv[0][0] == "ALL", kv[0])):
        firsts = [
            n["hidden"]
            for r in rs
            for n in r["nodes"]
            if n["id"] == "n1" and n["hidden"] is not None
        ]
        chosen = [r["chosen_hidden"] or 0.0 for r in rs]
        best = [max((n["hidden"] for n in attempts(r)), default=0.0) for r in rs]
        print(
            f"| {task} | {arm} | {len(rs)} | {mean(len(episodes(r)) for r in rs):.1f} | "
            f"{pct(mean_or_none(firsts))} | {pct(mean(chosen))} | {pct(mean(best))} | "
            f"{mean((r.get('wall_s') or 0) for r in rs) / 60:.1f} |"
        )

    print("\n## Within each run: the chosen answer against the first attempt\n")
    print("The first attempt is what a single agent run would have returned, so this")
    print("compares search and no search on the same run.\n")
    print(
        "| arm | runs | chosen better | same | chosen worse | mean change "
        "| worst first | worst chosen |"
    )
    print("|---|---|---|---|---|---|---|---|")
    for arm in sorted({r["arm"] for r in runs}):
        pairs = []
        for r in runs:
            first = next((n["hidden"] for n in r["nodes"] if n["id"] == "n1"), None)
            if r["arm"] == arm and first is not None:
                pairs.append((first, r["chosen_hidden"] or 0.0))
        d = [c - f for f, c in pairs]
        if not pairs:
            print(f"| {arm} | 0 | - | - | - | - | - | - |")
            continue
        print(
            f"| {arm} | {len(pairs)} | {sum(x > 1e-9 for x in d)} | "
            f"{sum(abs(x) <= 1e-9 for x in d)} | {sum(x < -1e-9 for x in d)} | "
            f"{100 * mean(d):+.1f} pt | {pct(min(f for f, _ in pairs))} | "
            f"{pct(min(c for _, c in pairs))} |"
        )

    same_budget(runs)

    print("\n## Does the reward rank attempts the way the hidden tests do?\n")
    print("Pairs of attempts in the same run whose hidden scores differ: how often the")
    print("higher reward went to the better one.\n")
    print("| arm | pairs | reward agrees | reward ties | reward disagrees |")
    print("|---|---|---|---|---|")
    for arm in sorted({r["arm"] for r in runs}):
        agree = tie = disagree = 0
        for r in runs:
            if r["arm"] != arm:
                continue
            a = [n for n in attempts(r) if n["reward"] is not None]
            for i in range(len(a)):
                for j in range(i + 1, len(a)):
                    dh = a[i]["hidden"] - a[j]["hidden"]
                    if abs(dh) < 1e-9:
                        continue
                    dr = a[i]["reward"] - a[j]["reward"]
                    if abs(dr) < 1e-9:
                        tie += 1
                    elif (dr > 0) == (dh > 0):
                        agree += 1
                    else:
                        disagree += 1
        print(f"| {arm} | {agree + tie + disagree} | {agree} | {tie} | {disagree} |")

    print("\n## Revisions: did a child score higher than its parent on the hidden tests?\n")
    print("| arm | revisions | better | same | worse | mean change |")
    print("|---|---|---|---|---|---|")
    for arm in sorted({r["arm"] for r in runs}):
        deltas = []
        for r in runs:
            if r["arm"] != arm:
                continue
            by_id = {n["id"]: n for n in r["nodes"]}
            for n in attempts(r):
                parent = by_id[n["parent"]]
                if parent["parent"] is not None and parent["hidden"] is not None:
                    deltas.append(n["hidden"] - parent["hidden"])
        if deltas:
            print(
                f"| {arm} | {len(deltas)} | {sum(d > 1e-9 for d in deltas)} | "
                f"{sum(abs(d) <= 1e-9 for d in deltas)} | {sum(d < -1e-9 for d in deltas)} | "
                f"{100 * mean(deltas):+.1f} pt |"
            )
        else:
            print(f"| {arm} | 0 | - | - | - | - |")


if __name__ == "__main__":
    main()
