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
from agent_mcts.core.model import Node, Selection, Tree
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


class Candidate(BaseModel):
    """One solution in a head-to-head comparison."""

    label: str
    diff: str
    test_output: str = ""


class Ranking(BaseModel):
    """One ranking call. `order` is None when the judge produced nothing usable."""

    order: list[str] | None = None  # candidate labels, best first
    explanation: str = ""
    error: str = ""
    cost_usd: float | None = 0.0
    cost_known: bool = True
    usage: TokenUsage | None = None


_RANKING = re.compile(r"<Ranking>(.*?)</Ranking>", re.IGNORECASE | re.DOTALL)


def parse_ranking(text: str, labels: list[str]) -> tuple[list[str], str] | None:
    """Extract `(labels best-first, explanation)`, or None unless it ranks every label once."""
    rankings = _RANKING.findall(text)
    if not rankings:
        return None
    order = [part.strip() for part in re.split(r"[,>\n]", rankings[-1]) if part.strip()]
    if sorted(order) != sorted(labels):
        return None
    explanations = _EXPLANATION.findall(text)
    explanation = explanations[-1] if explanations else _RANKING.sub("", text)
    return order, explanation.strip()


def ranking_prompt(task: str, candidates: list[Candidate]) -> str:
    per_candidate = max(2_000, _MAX_DIFF_CHARS // len(candidates))
    blocks: list[str] = []
    for candidate in candidates:
        tests = candidate.test_output.strip()[-1_500:] or "(no output)"
        blocks.append(
            f"\n=== CANDIDATE {candidate.label} ===\n"
            f"```diff\n{render_diff(candidate.diff, max_chars=per_candidate)}\n```\n"
            f"Evaluation command output for {candidate.label}:\n```\n{tests}\n```\n"
        )
    labels = ", ".join(c.label for c in candidates)
    return (
        "You are the final judge in a tree search over solutions to a coding task. "
        "Compare the candidate solutions below against each other and rank them. Do not "
        "modify any files and do not try to run anything: judge only from what is shown.\n"
        f"\nTASK:\n{task}\n"
        f"{''.join(blocks)}"
        "\nRank by which change you would rather merge: it addresses the task, is "
        "complete and correct including implied edge cases, and avoids collateral damage. "
        "A candidate that deleted, skipped or weakened tests to pass ranks last.\n"
        "\nReply in exactly this format, listing every candidate once, best first:\n"
        "<Explanation>One paragraph: what decides the order.</Explanation>\n"
        f"<Ranking>{labels}</Ranking>"
    )


class LLMJudge:
    """Asks an agent backend to score or rank candidates; never raises on judge failure."""

    def __init__(self, adapter: AgentBackend, *, task: str) -> None:
        self.adapter = adapter
        self.task = task

    async def _ask(self, prompt: str) -> tuple[str | None, JudgeVerdict]:
        """Run one judge episode: `(reply or None, a verdict carrying spend and error)`."""
        with tempfile.TemporaryDirectory(prefix="agent-mcts-judge-") as scratch:
            # Some agent CLIs refuse to run outside a git repository.
            subprocess.run(["git", "init", "-q"], cwd=scratch, capture_output=True, check=False)
            try:
                result = await self.adapter.run_episode(prompt, Path(scratch))
            except AdapterError as exc:
                failed = JudgeVerdict(error=f"judge failed: {exc}", cost_known=exc.cost_known)
                return None, failed
        spent = JudgeVerdict(
            cost_usd=result.cost_usd, cost_known=result.cost_known, usage=result.usage
        )
        if result.is_error:
            error = f"judge reported an error: {result.summary[:500]}"
            return None, spent.model_copy(update={"error": error})
        return result.summary, spent

    async def score(self, *, diff: str, test_output: str) -> JudgeVerdict:
        reply, spent = await self._ask(judge_prompt(self.task, diff, test_output))
        if reply is None:
            return spent
        parsed = parse_verdict(reply)
        if parsed is None:
            return spent.model_copy(update={"error": "judge reply had no <Reward> tag"})
        score, explanation = parsed
        return spent.model_copy(update={"score": score, "explanation": explanation})

    async def rank(self, candidates: list[Candidate]) -> Ranking:
        """Rank candidates against each other in one call (listwise)."""
        reply, spent = await self._ask(ranking_prompt(self.task, candidates))
        ranking = Ranking(
            error=spent.error,
            cost_usd=spent.cost_usd,
            cost_known=spent.cost_known,
            usage=spent.usage,
        )
        if reply is None:
            return ranking
        parsed = parse_ranking(reply, [c.label for c in candidates])
        if parsed is None:
            error = "judge reply did not rank every candidate exactly once"
            return ranking.model_copy(update={"error": error})
        order, explanation = parsed
        return ranking.model_copy(update={"order": order, "explanation": explanation})


async def diff_between(repo: Path, base: str, head: str) -> str:
    """`git diff base head`, run anywhere inside the repository."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        "diff",
        "--no-color",
        "--no-ext-diff",
        base,
        head,
        cwd=repo,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or "git diff failed")
    return stdout.decode(errors="replace")


async def cumulative_diff(workdir: Path, base_commit: str) -> str:
    """Diff from the run's base commit to the worktree's HEAD (the node snapshot)."""
    return await diff_between(workdir, base_commit, "HEAD")


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


_TESTS_HEADER = re.compile(r"\n\nTests \(\d\.\d\d\):\n")


def evidence_part(detail: str) -> str:
    """The test output inside a detail written by `HybridValueFunction._combine`.

    A comparison between nodes should read the evidence, not the earlier per-node
    critiques, or the first opinion anchors the second.
    """
    parts = _TESTS_HEADER.split(detail, maxsplit=1)
    return parts[1] if len(parts) == 2 else detail


class FinalSelector:
    """Picks the node `apply` offers by comparing the top-scoring nodes head to head.

    Per-node judge scores are noisy: two good attempts routinely land within a point or
    two of each other, and then the higher number is not evidence. SWE-Search separates
    the value that guides the search from a discriminator that makes the final choice;
    this is that step, as one listwise judge call.
    """

    def __init__(
        self,
        judge: LLMJudge,
        *,
        repo: Path,
        base_commit: str,
        top_k: int = 3,
        margin: float = 0.1,
    ) -> None:
        self.judge = judge
        self.repo = repo
        self.base_commit = base_commit
        self.top_k = top_k
        self.margin = margin

    def contenders(self, tree: Tree) -> list[Node]:
        """Up to `top_k` attempts whose reward is within `margin` of the best one."""
        attempts = [n for n in tree.ranked() if n.parent_id is not None and n.branch]
        if not attempts:
            return []
        floor = (attempts[0].reward or 0.0) - self.margin
        return [n for n in attempts if (n.reward or 0.0) >= floor][: self.top_k]

    async def select(self, tree: Tree) -> Selection | None:
        """Compare the contenders; None when there is nothing to choose between."""
        contenders = self.contenders(tree)
        if len(contenders) < 2:
            return None
        ids = [n.id for n in contenders]
        try:
            candidates = [
                Candidate(
                    label=n.id,
                    diff=await diff_between(self.repo, self.base_commit, n.branch or ""),
                    test_output=evidence_part(n.eval_detail),
                )
                for n in contenders
            ]
        except (OSError, RuntimeError) as exc:
            return Selection(candidates=ids, error=f"could not diff the candidates: {exc}")
        ranking = await self.judge.rank(candidates)
        return Selection(
            node_id=ranking.order[0] if ranking.order else None,
            candidates=ranking.order or ids,
            explanation=ranking.explanation,
            error=ranking.error,
            cost_usd=ranking.cost_usd,
            cost_known=ranking.cost_known,
            usage=ranking.usage,
        )


class SiblingRanker:
    """Ranks the attempts that share a parent against each other.

    An absolute judge score drifts from call to call, while UCT only ever compares
    children of one parent. Ranking those children in a single call compares like with
    like: same starting point, same prompt, same judge reply. This is the
    group-relative idea from GRPO, used for selection only; nothing is trained.
    """

    def __init__(self, judge: LLMJudge, *, repo: Path) -> None:
        self.judge = judge
        self.repo = repo

    async def rank(self, parent: Node, children: list[Node]) -> Ranking:
        """Rank `children` by what each changed relative to `parent`."""
        try:
            candidates = [
                Candidate(
                    label=child.id,
                    diff=await diff_between(self.repo, parent.branch or "", child.branch or "")
                    or "(no changes relative to the shared starting point)",
                    test_output=evidence_part(child.eval_detail),
                )
                for child in children
            ]
        except (OSError, RuntimeError) as exc:
            return Ranking(error=f"could not diff the siblings: {exc}")
        return await self.judge.rank(candidates)
