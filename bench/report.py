"""Summarise bench/results/*.json as markdown tables."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

RESULTS = Path(__file__).resolve().parent / "results"


def pct(value: float | None) -> str:
    return "-" if value is None else f"{100 * value:.0f}%"


def attempts(run: dict) -> list[dict]:
    """Nodes that are agent attempts (not the untouched root) and were scored."""
    return [n for n in run["nodes"] if n["parent"] is not None and n["hidden"] is not None]


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
            f"| {r['task']} | {r['arm']} | {r['rep']} | {len(a)} | {pct(first)} | "
            f"{pct(r['chosen_hidden'])} ({r['chosen']}) | "
            f"{pct(max((n['hidden'] for n in a), default=None))} | "
            f"{pct(mean(n['hidden'] for n in a) if a else None)} | {r['wall_s'] / 60:.1f} |"
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
            f"| {task} | {arm} | {len(rs)} | {mean(len(attempts(r)) for r in rs):.1f} | "
            f"{pct(mean(firsts) if firsts else None)} | {pct(mean(chosen))} | {pct(mean(best))} | "
            f"{mean(r['wall_s'] for r in rs) / 60:.1f} |"
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
        print(
            f"| {arm} | {len(pairs)} | {sum(x > 1e-9 for x in d)} | "
            f"{sum(abs(x) <= 1e-9 for x in d)} | {sum(x < -1e-9 for x in d)} | "
            f"{100 * mean(d):+.1f} pt | {pct(min(f for f, _ in pairs))} | "
            f"{pct(min(c for _, c in pairs))} |"
        )

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
