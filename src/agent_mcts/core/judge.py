"""LLM-as-judge value: an agent scores a node's diff, and tests stay the verifiable half.

Tests alone cannot rank attempts once the suite is green, and they reward deleting the
failing test. Following SWE-Search's Value Agent, a judge reads the task, the node's
cumulative diff and the test output, and returns an integer in [-100, 100] plus an
explanation that is fed back into revision prompts (hindsight feedback).

The judge is an ordinary `AgentBackend` episode, so it needs no API key of its own. It
runs in an empty scratch directory: everything it needs is in the prompt, and it must
not be able to edit the worktree it is scoring.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile
from pathlib import Path

from pydantic import BaseModel

from agent_mcts.adapters.base import AdapterError, AgentBackend, TokenUsage
from agent_mcts.core.value import Evaluation, ValueFunction

_REWARD = re.compile(r"<Reward>\s*([+-]?\d+)\s*</Reward>", re.IGNORECASE)
_EXPLANATION = re.compile(r"<Explanation>(.*?)</Explanation>", re.IGNORECASE | re.DOTALL)

_MAX_DIFF_CHARS = 30_000
_MAX_FILE_DIFF_CHARS = 8_000
_MAX_TEST_OUTPUT_CHARS = 3_000
_MAX_EXPLANATION_CHARS = 1_500


class JudgeVerdict(BaseModel):
    """One judge call. `score` is None when the judge produced nothing usable."""

    score: float | None = None  # in [0, 1]
    explanation: str = ""
    error: str = ""  # why there is no score
    cost_usd: float | None = 0.0  # None: the judge backend never reports dollars
    cost_known: bool = True  # False: the call died after starting, spend is lost
    usage: TokenUsage | None = None


def parse_verdict(text: str) -> tuple[float, str] | None:
    """Extract `(score in [0, 1], explanation)` from the judge's reply, or None.

    The last `<Reward>` wins, so a judge that quotes the output format while reasoning
    is still read correctly. Out-of-range integers are clamped rather than rejected.
    """
    rewards = _REWARD.findall(text)
    if not rewards:
        return None
    raw = max(-100, min(100, int(rewards[-1])))
    explanations = _EXPLANATION.findall(text)
    explanation = explanations[-1] if explanations else _REWARD.sub("", text)
    return (raw + 100) / 200, explanation.strip()


def render_diff(
    diff: str, *, max_chars: int = _MAX_DIFF_CHARS, max_file_chars: int = _MAX_FILE_DIFF_CHARS
) -> str:
    """Fit a diff into the prompt budget file by file, naming whatever was left out.

    A plain head-truncation would let one generated file push every other change out
    of the judge's view; the judge should at least know those files were touched.
    """
    if len(diff) <= max_chars:
        return diff
    files = [part for part in re.split(r"(?m)^(?=diff --git )", diff) if part]
    kept: list[str] = []
    omitted: list[str] = []
    used = 0
    for part in files:
        if len(part) > max_file_chars:
            part = part[:max_file_chars] + "\n…[file diff truncated]\n"
        if used + len(part) > max_chars:
            header = part.splitlines()[0] if part else ""
            omitted.append(header.removeprefix("diff --git ").strip() or "(unknown file)")
            continue
        kept.append(part)
        used += len(part)
    out = "".join(kept)
    if omitted:
        names = "\n".join(f"- {name}" for name in omitted)
        out += f"\n…[diffs omitted for length; these files also changed]\n{names}\n"
    return out


def judge_prompt(task: str, diff: str, test_output: str) -> str:
    tests = test_output.strip()[-_MAX_TEST_OUTPUT_CHARS:] or "(no output)"
    return (
        "You are the evaluator in a tree search over solutions to a coding task. "
        "Score one candidate solution. Do not modify any files and do not try to run "
        "anything: judge only from what is shown below.\n"
        f"\nTASK:\n{task}\n"
        "\nCANDIDATE CHANGE (cumulative diff from the starting repository):\n"
        f"```diff\n{render_diff(diff)}\n```\n"
        "\nOUTPUT OF THE EVALUATION COMMAND (test suite) ON THE CANDIDATE:\n"
        f"```\n{tests}\n```\n"
        "\nJudge the candidate on:\n"
        "1. Does the change actually address the task, rather than something adjacent?\n"
        "2. Is it complete and correct, including edge cases the task implies?\n"
        "3. Does it avoid collateral damage (unrelated edits, broken behavior)?\n"
        "4. Were tests deleted, skipped, or weakened to make the suite pass? If so, "
        "score it strongly negative — passing tests then mean nothing.\n"
        "\nScore with a single integer from -100 to 100: 100 = fully solves the task and "
        "could be merged as is; 0 = no real progress; negative = harmful or misleading "
        "(wrong direction, gamed evaluation).\n"
        "\nReply in exactly this format:\n"
        "<Explanation>One paragraph: the main strengths and the specific problems a "
        "revision should fix.</Explanation>\n"
        "<Reward>INTEGER</Reward>"
    )


class LLMJudge:
    """Asks an agent backend to score a candidate; never raises on judge failure."""

    def __init__(self, adapter: AgentBackend, *, task: str) -> None:
        self.adapter = adapter
        self.task = task

    async def score(self, *, diff: str, test_output: str) -> JudgeVerdict:
        prompt = judge_prompt(self.task, diff, test_output)
        with tempfile.TemporaryDirectory(prefix="agent-mcts-judge-") as scratch:
            # Some agent CLIs refuse to run outside a git repository.
            subprocess.run(["git", "init", "-q"], cwd=scratch, capture_output=True, check=False)
            try:
                result = await self.adapter.run_episode(prompt, Path(scratch))
            except AdapterError as exc:
                return JudgeVerdict(error=f"judge failed: {exc}", cost_known=exc.cost_known)
        spent = JudgeVerdict(
            cost_usd=result.cost_usd, cost_known=result.cost_known, usage=result.usage
        )
        if result.is_error:
            error = f"judge reported an error: {result.summary[:500]}"
            return spent.model_copy(update={"error": error})
        parsed = parse_verdict(result.summary)
        if parsed is None:
            return spent.model_copy(update={"error": "judge reply had no <Reward> tag"})
        score, explanation = parsed
        return spent.model_copy(update={"score": score, "explanation": explanation})


async def cumulative_diff(workdir: Path, base_commit: str) -> str:
    """Diff from the run's base commit to the worktree's HEAD (the node snapshot)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "diff",
        "--no-color",
        "--no-ext-diff",
        base_commit,
        "HEAD",
        cwd=workdir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or "git diff failed")
    return stdout.decode(errors="replace")


class HybridValueFunction:
    """`w_test * test_score + w_judge * judge_score`, weights normalized to sum to 1.

    A node with no changes relative to the base commit — the root, or a child whose
    agent did nothing — is not sent to the judge and gets judge_score 0. That is what
    keeps a no-op from tying with real work on an already-green suite.

    A judge that fails (timeout, crash, unparseable reply) does not fail the node: the
    judge half scores 0 and the reason is noted in the detail.
    """

    def __init__(
        self,
        tests: ValueFunction,
        judge: LLMJudge,
        *,
        base_commit: str,
        w_test: float = 0.5,
        w_judge: float = 0.5,
    ) -> None:
        total = w_test + w_judge
        if w_test < 0 or w_judge < 0 or total <= 0:
            raise ValueError("value weights must be non-negative and not both zero")
        self.tests = tests
        self.judge = judge
        self.base_commit = base_commit
        self.w_test = w_test / total
        self.w_judge = w_judge / total

    async def evaluate(self, workdir: Path) -> Evaluation:
        tested = await self.tests.evaluate(workdir)
        try:
            diff = await cumulative_diff(workdir, self.base_commit)
        except (OSError, RuntimeError) as exc:
            return self._combine(tested, JudgeVerdict(error=f"could not diff the node: {exc}"))
        if not diff.strip():
            return self._combine(
                tested,
                JudgeVerdict(score=0.0),
                judged=False,
            )
        return self._combine(tested, await self.judge.score(diff=diff, test_output=tested.detail))

    def _combine(
        self, tested: Evaluation, verdict: JudgeVerdict, *, judged: bool = True
    ) -> Evaluation:
        judge_score = verdict.score
        score = self.w_test * tested.score + self.w_judge * (judge_score or 0.0)
        if judge_score is None:
            note = f"Judge unavailable ({verdict.error}); scored on tests alone."
        elif not judged:
            # No judge ran, so do not label this as a judge's opinion. The root's detail
            # is quoted to first attempts as the baseline; an untouched tree gets the
            # test output alone, exactly as without a judge.
            return tested.model_copy(
                update={"score": score, "test_score": tested.score, "judge_score": 0.0}
            )
        else:
            reward = round(judge_score * 200 - 100)
            note = f"Judge ({reward:+d}/100): {verdict.explanation[:_MAX_EXPLANATION_CHARS]}"
        # The note goes first: revision prompts clip the detail from the end, and the
        # judge's critique is the part a revising agent cannot reconstruct by itself.
        detail = f"{note}\n\nTests ({tested.score:.2f}):\n{tested.detail}".rstrip()
        return Evaluation(
            score=score,
            detail=detail,
            test_score=tested.score,
            judge_score=judge_score,
            cost_usd=verdict.cost_usd,
            cost_known=verdict.cost_known,
            usage=verdict.usage,
        )
