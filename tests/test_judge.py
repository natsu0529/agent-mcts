"""LLM-as-judge value: reply parsing, diff fitting, the hybrid score, and the engine's
accounting of judge spend. The judge is a fake backend throughout — no API calls."""

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_mcts.adapters.base import AdapterError, AdapterTimeout, EpisodeResult, TokenUsage
from agent_mcts.core import journal
from agent_mcts.core.engine import SearchConfig, SearchEngine
from agent_mcts.core.judge import (
    HybridValueFunction,
    LLMJudge,
    cumulative_diff,
    judge_prompt,
    parse_verdict,
    render_diff,
)
from agent_mcts.core.model import NodeStatus, RunMeta, Tree
from agent_mcts.core.value import CommandValueFunction, Evaluation
from agent_mcts.core.worktree import WorktreeManager


def reply(reward: int, explanation: str = "looks reasonable") -> str:
    return f"<Explanation>{explanation}</Explanation>\n<Reward>{reward}</Reward>"


class FakeJudgeBackend:
    """Returns scripted replies; an Exception in the script is raised instead."""

    def __init__(self, replies: list[str | Exception], *, cost_usd: float | None = 0.02) -> None:
        self.replies = list(replies)
        self.cost_usd = cost_usd
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "fake-judge"

    @property
    def reports_cost(self) -> bool:
        return self.cost_usd is not None

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        self.calls.append(
            {
                "prompt": prompt,
                "workdir": workdir,
                "files": sorted(p.name for p in workdir.iterdir() if p.name != ".git"),
            }
        )
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return EpisodeResult(
            session_id=f"j{len(self.calls)}",
            summary=item,
            cost_usd=self.cost_usd,
            usage=TokenUsage(input_tokens=100, output_tokens=10),
        )


class FixedTests:
    def __init__(self, score: float, detail: str = "3 passed") -> None:
        self.score = score
        self.detail = detail

    async def evaluate(self, workdir: Path) -> Evaluation:
        return Evaluation(score=self.score, detail=self.detail)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit_change(repo: Path, name: str = "feature.py", body: str = "x = 1\n") -> None:
    (repo / name).write_text(body)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "work")


# -- parsing ------------------------------------------------------------------


def test_parse_maps_reward_to_unit_interval() -> None:
    assert parse_verdict(reply(100)) == (1.0, "looks reasonable")
    assert parse_verdict(reply(0)) == (0.5, "looks reasonable")
    assert parse_verdict(reply(-100)) == (0.0, "looks reasonable")
    parsed = parse_verdict(reply(60, "handles the chain rule"))
    assert parsed is not None
    assert parsed[0] == pytest.approx(0.8)


def test_parse_clamps_out_of_range_rewards() -> None:
    assert parse_verdict(reply(250)) == (1.0, "looks reasonable")
    assert parse_verdict(reply(-999)) == (0.0, "looks reasonable")


def test_parse_takes_the_last_reward_tag() -> None:
    text = "The format is <Reward>INTEGER</Reward>, e.g. <Reward>10</Reward>.\n" + reply(-40)
    parsed = parse_verdict(text)
    assert parsed is not None
    assert parsed[0] == pytest.approx(0.3)


def test_parse_without_reward_tag_is_none() -> None:
    assert parse_verdict("I think this is about a 7 out of 10.") is None
    assert parse_verdict("") is None


def test_parse_without_explanation_tag_uses_remaining_text() -> None:
    assert parse_verdict("Solid work overall. <Reward>50</Reward>") == (0.75, "Solid work overall.")


# -- diff fitting -------------------------------------------------------------


def file_diff(name: str, lines: int) -> str:
    body = "".join(f"+line {i}\n" for i in range(lines))
    return f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n{body}"


def test_small_diff_is_passed_through() -> None:
    diff = file_diff("a.py", 3)
    assert render_diff(diff) == diff


def test_oversized_file_is_truncated_but_others_survive() -> None:
    diff = file_diff("generated.json", 5000) + file_diff("fix.py", 3)
    out = render_diff(diff, max_chars=4000, max_file_chars=1000)
    assert "[file diff truncated]" in out
    assert "b/fix.py" in out and "+line 2" in out
    assert len(out) < 4000


def test_files_that_do_not_fit_are_named() -> None:
    diff = file_diff("a.py", 100) + file_diff("b.py", 100) + file_diff("c.py", 100)
    out = render_diff(diff, max_chars=1500, max_file_chars=1200)
    assert "a/a.py" in out.split("these files also changed")[0]
    omitted = out.split("these files also changed")[1]
    assert "a/b.py b/b.py" in omitted and "a/c.py b/c.py" in omitted


# -- the judge call -----------------------------------------------------------


def test_judge_runs_in_an_empty_scratch_directory(repo: Path) -> None:
    backend = FakeJudgeBackend([reply(40)])
    verdict = asyncio.run(
        LLMJudge(backend, task="add a feature").score(diff="diff --git a/x b/x", test_output="ok")
    )

    assert verdict.score == pytest.approx(0.7)
    assert verdict.cost_usd == pytest.approx(0.02)
    call = backend.calls[0]
    assert call["workdir"] != repo and repo not in call["workdir"].parents
    assert call["files"] == []  # nothing to edit: the code under review is not there
    assert "add a feature" in call["prompt"] and "diff --git a/x b/x" in call["prompt"]
    assert not call["workdir"].exists()  # scratch is removed afterwards


def test_judge_prompt_asks_about_weakened_tests() -> None:
    prompt = judge_prompt("fix the bug", "diff --git a/t b/t", "1 passed")
    assert "deleted, skipped, or weakened" in prompt
    assert "<Reward>INTEGER</Reward>" in prompt


def test_unparseable_judge_reply_keeps_its_cost() -> None:
    backend = FakeJudgeBackend(["no tags here"])
    verdict = asyncio.run(LLMJudge(backend, task="t").score(diff="d", test_output=""))
    assert verdict.score is None
    assert "no <Reward> tag" in verdict.error
    assert verdict.cost_usd == pytest.approx(0.02) and verdict.cost_known


def test_judge_timeout_loses_the_cost_figure() -> None:
    backend = FakeJudgeBackend([AdapterTimeout("judge timed out", duration_s=300.0)])
    verdict = asyncio.run(LLMJudge(backend, task="t").score(diff="d", test_output=""))
    assert verdict.score is None
    assert not verdict.cost_known


def test_judge_that_never_started_costs_nothing() -> None:
    backend = FakeJudgeBackend([AdapterError("no binary")])
    verdict = asyncio.run(LLMJudge(backend, task="t").score(diff="d", test_output=""))
    assert verdict.score is None
    assert verdict.cost_known


# -- hybrid value -------------------------------------------------------------


def hybrid(repo: Path, backend: FakeJudgeBackend, tests: Any, **weights: float) -> Evaluation:
    base = git(repo, "rev-list", "--max-parents=0", "HEAD")
    fn = HybridValueFunction(tests, LLMJudge(backend, task="task"), base_commit=base, **weights)
    return asyncio.run(fn.evaluate(repo))


def test_cumulative_diff_is_empty_at_the_base(repo: Path) -> None:
    base = git(repo, "rev-parse", "HEAD")
    assert asyncio.run(cumulative_diff(repo, base)) == ""
    commit_change(repo)
    assert "feature.py" in asyncio.run(cumulative_diff(repo, base))


def test_hybrid_combines_tests_and_judge(repo: Path) -> None:
    commit_change(repo)
    backend = FakeJudgeBackend([reply(60, "misses the product rule")])
    result = hybrid(repo, backend, FixedTests(1.0, "10 passed"))

    assert result.score == pytest.approx(0.5 * 1.0 + 0.5 * 0.8)
    assert result.test_score == 1.0
    assert result.judge_score == pytest.approx(0.8)
    assert result.cost_usd == pytest.approx(0.02)
    # The critique leads the detail so prompt clipping never drops it.
    assert result.detail.startswith("Judge (+60/100): misses the product rule")
    assert "10 passed" in result.detail
    assert "feature.py" in backend.calls[0]["prompt"]
    assert "10 passed" in backend.calls[0]["prompt"]


def test_hybrid_weights_are_normalized(repo: Path) -> None:
    commit_change(repo)
    backend = FakeJudgeBackend([reply(100)])
    result = hybrid(repo, backend, FixedTests(0.0), w_test=3, w_judge=1)
    assert result.score == pytest.approx(0.25)


def test_hybrid_rejects_degenerate_weights(repo: Path) -> None:
    with pytest.raises(ValueError, match="weights"):
        HybridValueFunction(
            FixedTests(1.0),
            LLMJudge(FakeJudgeBackend([]), task="t"),
            base_commit="HEAD",
            w_test=0,
            w_judge=0,
        )


def test_unchanged_tree_is_not_sent_to_the_judge(repo: Path) -> None:
    backend = FakeJudgeBackend([])
    result = hybrid(repo, backend, FixedTests(1.0))

    assert backend.calls == []  # no spend on an empty diff
    assert result.judge_score == 0.0
    assert result.score == pytest.approx(0.5)  # green tests alone are worth half
    assert "no changes" in result.detail


def test_failed_judge_falls_back_to_tests_without_failing(repo: Path) -> None:
    commit_change(repo)
    backend = FakeJudgeBackend([AdapterTimeout("judge timed out", duration_s=300.0)])
    result = hybrid(repo, backend, FixedTests(0.6, "2 failed, 3 passed"))

    assert result.judge_score is None
    assert result.score == pytest.approx(0.3)
    assert not result.cost_known
    assert "Judge unavailable" in result.detail and "2 failed, 3 passed" in result.detail


def test_hybrid_wraps_the_real_command_value(repo: Path) -> None:
    commit_change(repo)
    backend = FakeJudgeBackend([reply(0)])
    result = hybrid(repo, backend, CommandValueFunction("echo '1 failed, 1 passed'; exit 1"))
    assert result.test_score == pytest.approx(0.5)
    assert result.score == pytest.approx(0.5)


# -- engine integration -------------------------------------------------------


class WorkerBackend:
    """Episode `idle` leaves the worktree untouched; every other episode adds a file."""

    def __init__(self, idle: set[int]) -> None:
        self.idle = idle
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def reports_cost(self) -> bool:
        return True

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        self.calls += 1
        if self.calls not in self.idle:
            (workdir / f"feature_{self.calls}.py").write_text("x = 1\n")
        return EpisodeResult(session_id=f"s{self.calls}", summary="did it", cost_usd=0.10)


def run_engine(
    repo: Path, backend: WorkerBackend, judge: FakeJudgeBackend, config: SearchConfig
) -> tuple[SearchEngine, Path]:
    base = git(repo, "rev-parse", "HEAD")
    meta = RunMeta(run_id="r1", task="add the feature", repo_root=str(repo), base_commit=base)
    journal_path = repo / ".agent-mcts" / "runs" / "r1" / "tree.jsonl"
    engine = SearchEngine(
        tree=Tree(meta),
        adapter=backend,
        value_fn=HybridValueFunction(
            CommandValueFunction("true"),  # an already-green suite
            LLMJudge(judge, task=meta.task),
            base_commit=base,
        ),
        worktrees=WorktreeManager(repo, "r1"),
        journal_path=journal_path,
        config=config,
    )
    asyncio.run(engine.run())
    return engine, journal_path


def test_green_baseline_no_longer_rewards_a_no_op(repo: Path) -> None:
    # Tests alone would score every node 1.0 and stop after the first child, and the
    # idle first child would be offered as best.
    backend = WorkerBackend(idle={1})
    judge = FakeJudgeBackend([reply(60), reply(20)])
    engine, journal_path = run_engine(repo, backend, judge, SearchConfig(max_nodes=3))
    tree = engine.tree

    assert engine.episodes == 3  # the search did not stop at the first green child
    root = tree.root
    assert root is not None
    idle, worked, weaker = tree.children(root.id)
    assert root.reward == pytest.approx(0.5) and root.judge_score == 0.0
    assert idle.reward == pytest.approx(0.5)  # green tests, nothing done
    assert worked.reward == pytest.approx(0.9)
    assert weaker.reward == pytest.approx(0.8)
    best = tree.best()
    assert best is not None and best.id == worked.id
    assert len(judge.calls) == 2  # neither the root nor the idle child was judged

    # Components and judge spend survive a journal round trip.
    loaded = journal.load(journal_path).nodes[worked.id]
    assert loaded.test_score == 1.0
    assert loaded.judge_score == pytest.approx(0.8)
    assert loaded.eval_cost_usd == pytest.approx(0.02)


def test_judge_feedback_reaches_the_revision_prompt(repo: Path) -> None:
    prompts: list[str] = []

    class Recording(WorkerBackend):
        async def run_episode(
            self, prompt: str, workdir: Path, *, resume_session: str | None = None
        ) -> EpisodeResult:
            prompts.append(prompt)
            return await super().run_episode(prompt, workdir, resume_session=resume_session)

    judge = FakeJudgeBackend([reply(40, "the parser ignores unary minus"), reply(70)])
    run_engine(repo, Recording(idle=set()), judge, SearchConfig(max_nodes=2, root_width=1))

    assert "the parser ignores unary minus" in prompts[1]


def test_judge_cost_counts_toward_the_ceiling_but_not_episodes(repo: Path) -> None:
    judge = FakeJudgeBackend([reply(10)] * 5, cost_usd=0.25)
    config = SearchConfig(max_nodes=5, max_cost_usd=0.60)
    engine, _ = run_engine(repo, WorkerBackend(idle=set()), judge, config)

    # Each node costs $0.10 + $0.25; the ceiling is crossed after the second one.
    assert engine.episodes == 2
    assert engine.total_cost_usd == pytest.approx(0.70)
    assert engine.total_usage.input_tokens == 200


def test_lost_judge_cost_halts_the_search_but_keeps_the_node(repo: Path) -> None:
    judge = FakeJudgeBackend([AdapterTimeout("judge timed out", duration_s=300.0), reply(50)])
    engine, _ = run_engine(repo, WorkerBackend(idle=set()), judge, SearchConfig(max_nodes=4))

    assert engine.episodes == 1
    assert engine.unknown_cost_episodes == 1
    (child,) = engine.tree.children("n0")
    assert child.status is NodeStatus.EVALUATED  # a flaky judge does not fail the branch
    assert child.judge_score is None
    assert child.reward == pytest.approx(0.5)


def test_old_journals_without_judge_fields_still_load(repo: Path) -> None:
    path = repo / "tree.jsonl"
    path.write_text(
        '{"kind": "meta", "run_id": "r0", "task": "t", "repo_root": "/x"}\n'
        '{"kind": "node", "id": "n0", "status": "evaluated", "reward": 0.4, "cost_usd": 0.1}\n'
    )
    node = journal.load(path).nodes["n0"]
    assert node.judge_score is None and node.test_score is None
    assert node.eval_cost_usd == 0.0 and node.eval_cost_known
