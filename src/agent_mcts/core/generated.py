"""Generated-tests value: tests an agent writes from the task, run against every attempt.

Once the project's own tests pass, they cannot tell attempts apart, and the LLM judge
only reads diffs. Here, before the search, a separate agent episode reads the task and
the repository at the base commit (never an attempt) and writes a few test files. Every
node is then scored on them as well as on the value command.

On the benchmark (`bench/spec_tests.py`), for pairs of attempts that both passed the
visible tests, the mean over three generated suites ranked the better one higher 58
times in 62; the judge did 38. Some generated tests are themselves wrong, but nearly
all of those fail on every attempt alike, so they barely move the ranking.

The suites are written once per run and stored next to its journal, so a resumed search
scores new nodes on exactly the tests the old ones saw.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, field_validator

from agent_mcts.adapters.base import AdapterError, AgentBackend, TokenUsage
from agent_mcts.core.value import CommandValueFunction, Evaluation, ValueFunction

_TEST_FILE = re.compile(r"<TestFile>\s*(.*?)\s*</TestFile>", re.IGNORECASE | re.DOTALL)
_CODE_BLOCK = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.DOTALL)
_FAILED_LINE = re.compile(r"(?m)^(?:FAILED|ERROR) .*$")

_MAX_FAILURE_LINES = 20
_MAX_NOTE_CHARS = 2_000

WRITER_PROMPT = """\
Another agent will be asked to do the task below in this repository. Before it starts, \
write tests that check whether a solution does the task correctly. You will not see \
the solution.

## Task

{task}

## What to do

- Read the repository to learn what the task asks for: the spec, docs, the code to \
change, and the existing tests and how they import the code.
- Do not change any file. Put the whole test file in your reply instead.
- Test only what the task and the repository's docs state. If they do not decide a \
case, leave it out.
- One behaviour per test, so a single bug fails only a few tests. Use parametrized \
tables of cases where they fit.
- Work out each expected value carefully. A wrong expected value is worse than a \
missing test.
- Use the same test framework and import style as the existing tests, and choose a \
path for the file inside the existing test directory.

Reply with the path in a <TestFile> tag, then the complete file in one code block:

<TestFile>tests/test_example.py</TestFile>
```python
...
```
"""


class GeneratedSuite(BaseModel):
    """One generated test file: where it goes in a worktree, and what it contains."""

    path: str  # relative to the repository root, POSIX separators
    content: str

    @field_validator("path")
    @classmethod
    def _inside_the_repository(cls, path: str) -> str:
        # Suites can come from a file (`generate_reuse`), not only from `parse_suite`,
        # and the path is written to and deleted in every attempt's worktree.
        posix = PurePosixPath(path)
        if not path or "\\" in path or posix.is_absolute() or ".." in posix.parts:
            raise ValueError(f"suite path must be relative and inside the repository: {path!r}")
        if posix.name in ("", "."):
            raise ValueError(f"suite path must name a file: {path!r}")
        return path


class WriterResult(BaseModel):
    suite: GeneratedSuite | None = None
    error: str | None = None
    cost_usd: float | None = 0.0
    cost_known: bool = True
    usage: TokenUsage | None = None


def parse_suite(text: str, *, index: int) -> GeneratedSuite | None:
    """The writer's reply as a suite placed at a fresh name in the directory it chose."""
    block = _CODE_BLOCK.search(text)
    if block is None or not block.group(1).strip():
        return None
    tag = _TEST_FILE.search(text)
    chosen = PurePosixPath(tag.group(1).strip()) if tag else PurePosixPath("tests/test_x.py")
    if chosen.is_absolute() or ".." in chosen.parts:
        chosen = PurePosixPath("tests/test_x.py")
    # Our own name, not the writer's: it must not overwrite a test the attempt has, and
    # several suites share the directory.
    name = f"test_agent_mcts_generated_{index}{chosen.suffix or '.py'}"
    return GeneratedSuite(path=str(chosen.parent / name), content=block.group(1))


def default_command(value_command: str) -> str | None:
    """A command that runs one generated pytest file, derived from the value command.

    `python -m pytest -q tests` becomes `python -m pytest -q {file}`: keep everything up
    to the word `pytest` (the interpreter, `uv run`, ...) and drop the rest. None when
    the value command does not run pytest.
    """
    try:
        words = shlex.split(value_command)
    except ValueError:
        return None
    for i, word in enumerate(words):
        if PurePosixPath(word).name in ("pytest", "py.test"):
            return shlex.join(words[: i + 1]) + " -q -p no:cacheprovider {file}"
    return None


async def _export(repo: Path, commit: str, dest: Path) -> None:
    """Write the tree of `commit` into `dest`, with an empty git repo for agent CLIs."""
    archive = await asyncio.create_subprocess_exec(
        "git",
        "archive",
        "--format=tar",
        commit,
        cwd=repo,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    data, err = await archive.communicate()
    if archive.returncode != 0:
        raise RuntimeError(err.decode(errors="replace").strip() or "git archive failed")
    untar = subprocess.run(["tar", "-x", "-C", str(dest)], input=data, capture_output=True)
    if untar.returncode != 0:
        raise RuntimeError(untar.stderr.decode(errors="replace").strip() or "tar failed")
    # Some agent CLIs refuse to run outside a git repository.
    subprocess.run(["git", "init", "-q"], cwd=dest, capture_output=True, check=False)


class SuiteWriter:
    """Runs one agent episode per suite on a copy of the base commit."""

    def __init__(self, adapter: AgentBackend, *, task: str, repo: Path, base_commit: str) -> None:
        self.adapter = adapter
        self.task = task
        self.repo = repo
        self.base_commit = base_commit

    async def write(self, index: int) -> WriterResult:
        with tempfile.TemporaryDirectory(prefix="agent-mcts-tests-") as scratch:
            # A copy, so the writer can read everything and change nothing that matters.
            try:
                await _export(self.repo, self.base_commit, Path(scratch))
            except (OSError, RuntimeError) as exc:
                return WriterResult(error=f"could not copy the repository: {exc}")
            try:
                result = await self.adapter.run_episode(
                    WRITER_PROMPT.format(task=self.task), Path(scratch)
                )
            except AdapterError as exc:
                return WriterResult(error=f"test writer failed: {exc}", cost_known=exc.cost_known)
        spent = WriterResult(
            cost_usd=result.cost_usd, cost_known=result.cost_known, usage=result.usage
        )
        if result.is_error:
            return spent.model_copy(update={"error": f"test writer error: {result.summary[:500]}"})
        suite = parse_suite(result.summary, index=index)
        if suite is None:
            return spent.model_copy(update={"error": "test writer reply had no code block"})
        return spent.model_copy(update={"suite": suite})


def _compiled(pycache: Path, stem: str) -> list[Path]:
    """Bytecode files for `stem` in `pycache`, if it is a real directory."""
    if pycache.is_symlink() or not pycache.is_dir():
        return []
    return list(pycache.glob(f"{stem}.*.pyc"))


class GeneratedTestsValueFunction:
    """`w_test * tests + w_generated * generated`, weights normalized to sum to 1.

    `generated` is the mean pass ratio over the suites, each run on its own with
    `command` (`{file}` is the suite's path in the worktree). The suites are written on
    the first evaluation, the root baseline, whose evaluation cost carries their spend.
    If no suite could be written, every node scores 0 on that half and the reason is
    noted in its detail, so the ranking falls back to the tests alone.

    With `reuse`, the suites are taken from that `suites.json` (one an earlier run
    stored) instead of being written, so two runs can be scored on the same tests.
    """

    def __init__(
        self,
        tests: ValueFunction,
        writer: SuiteWriter,
        *,
        store: Path,
        command: str,
        suites: int = 3,
        w_test: float = 0.5,
        w_generated: float = 0.5,
        timeout_s: float = 600.0,
        reuse: Path | None = None,
    ) -> None:
        total = w_test + w_generated
        if w_test < 0 or w_generated < 0 or total <= 0:
            raise ValueError("value weights must be non-negative and not both zero")
        if "{file}" not in command:
            raise ValueError("the generated-tests command must contain {file}")
        self.tests = tests
        self.writer = writer
        self.store = store
        self.command = command
        self.suites = suites
        self.w_test = w_test / total
        self.w_generated = w_generated / total
        self.timeout_s = timeout_s
        self.reuse = reuse
        self._lock = asyncio.Lock()
        self._loaded: list[GeneratedSuite] | None = None
        self._write_errors: list[str] = []

    @property
    def _index_file(self) -> Path:
        return self.store / "suites.json"

    async def _ensure_suites(self) -> tuple[list[GeneratedSuite], WriterResult | None]:
        """The suites, writing them if this run has none yet; the spend if it wrote."""
        async with self._lock:
            if self._loaded is not None:
                return self._loaded, None
            if self.reuse is not None and not self._index_file.exists():
                # Copied into this run's store, so a resumed search reads it from there
                # even if the original file has moved.
                self.store.mkdir(parents=True, exist_ok=True)
                self._index_file.write_text(
                    self.reuse.read_text(encoding="utf-8"), encoding="utf-8"
                )
            if self._index_file.exists():
                saved = json.loads(self._index_file.read_text(encoding="utf-8"))
                self._loaded = [GeneratedSuite.model_validate(s) for s in saved["suites"]]
                self._write_errors = list(saved.get("errors", []))
                return self._loaded, None
            results = await asyncio.gather(
                *(self.writer.write(i) for i in range(1, self.suites + 1))
            )
            self._loaded = [r.suite for r in results if r.suite is not None]
            self._write_errors = [r.error for r in results if r.error]
            self.store.mkdir(parents=True, exist_ok=True)
            self._index_file.write_text(
                json.dumps(
                    {
                        "suites": [s.model_dump() for s in self._loaded],
                        "errors": self._write_errors,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            known = [r.cost_usd for r in results]
            usage = [r.usage for r in results if r.usage is not None]
            spend = WriterResult(
                cost_usd=None if any(c is None for c in known) else sum(c or 0.0 for c in known),
                cost_known=all(r.cost_known for r in results),
                usage=sum(usage[1:], usage[0]) if usage else None,
            )
            return self._loaded, spend

    async def _run_suite(self, workdir: Path, suite: GeneratedSuite) -> Evaluation:
        root = workdir.resolve()
        target = root / suite.path
        # A symlinked directory in the attempt could still lead outside the worktree.
        if not target.parent.resolve().is_relative_to(root):
            return Evaluation(
                score=0.0, detail=f"{suite.path} would be written outside the attempt"
            )
        # lexists: a dangling symlink in the attempt is something there too.
        if os.path.lexists(target):
            return Evaluation(score=0.0, detail=f"{suite.path} already exists in the attempt")
        made: list[Path] = []
        parent = target.parent
        while not os.path.lexists(parent):
            made.append(parent)
            parent = parent.parent
        pycache = target.parent / "__pycache__"
        compiled_before = set(_compiled(pycache, target.stem))
        had_pycache = os.path.lexists(pycache)
        created: list[Path] = []
        written = False
        try:
            # One directory at a time and the file exclusively, never through a link
            # that appeared since the checks above.
            try:
                for directory in reversed(made):
                    directory.mkdir()
                    created.append(directory)
                # O_EXCL alone already refuses an existing link at the path itself;
                # O_NOFOLLOW is added where the platform has it (not on Windows).
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(target, flags, 0o644)
                written = True
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    out.write(suite.content)
            except OSError as e:
                return Evaluation(score=0.0, detail=f"{suite.path} could not be written: {e}")
            # No bytecode: a __pycache__ next to the suite may be a link out of the
            # attempt, and Python would write the suite's and conftest's .pyc there.
            runner = CommandValueFunction(
                self.command.format(file=shlex.quote(suite.path)),
                timeout_s=self.timeout_s,
                env={"PYTHONDONTWRITEBYTECODE": "1"},
            )
            return await runner.evaluate(workdir)
        finally:
            # The worktree is snapshotted again if the search is interrupted; the
            # generated tests must never end up on a node's branch. Only what this
            # created is removed, and nothing reached through a symlink.
            # Running the file leaves bytecode next to it unless PYTHONDONTWRITEBYTECODE
            # is set; a directory this created holds nothing else.
            if written:
                target.unlink(missing_ok=True)
            if created:
                top = created[0]
                if top.is_dir() and not top.is_symlink():
                    shutil.rmtree(top, ignore_errors=True)
            elif not pycache.is_symlink() and pycache.is_dir():
                for compiled in _compiled(pycache, target.stem):
                    if compiled not in compiled_before:
                        compiled.unlink(missing_ok=True)
                if not had_pycache and not any(pycache.iterdir()):
                    pycache.rmdir()

    async def evaluate(self, workdir: Path) -> Evaluation:
        tested = await self.tests.evaluate(workdir)
        suites, spend = await self._ensure_suites()
        spent = spend or WriterResult()
        runs = [await self._run_suite(workdir, s) for s in suites]
        generated = sum(r.score for r in runs) / len(runs) if runs else None
        score = self.w_test * tested.score + self.w_generated * (generated or 0.0)
        note = self._note(suites, runs, generated)
        # The note goes first, as with the judge: revision prompts clip the detail from
        # the end, and the generated tests are what a revising agent cannot see itself.
        detail = f"{note}\n\nTests ({tested.score:.2f}):\n{tested.detail}".rstrip()
        return Evaluation(
            score=score,
            detail=detail,
            test_score=tested.score,
            generated_score=generated,
            cost_usd=spent.cost_usd,
            cost_known=spent.cost_known,
            usage=spent.usage,
        )

    def _note(
        self, suites: list[GeneratedSuite], runs: list[Evaluation], generated: float | None
    ) -> str:
        if generated is None:
            reasons = "; ".join(self._write_errors) or "no suite was written"
            return f"Generated tests unavailable ({reasons}); scored on tests alone."
        failures = [line for r in runs for line in _FAILED_LINE.findall(r.detail)]
        # A suite that scored below 1 without pytest failure lines (refused before it
        # ran, timed out, ...) says why in the first line of its detail.
        failures += [
            r.detail.splitlines()[0][:200]
            for r in runs
            if r.score < 1 and r.detail.strip() and not _FAILED_LINE.search(r.detail)
        ]
        head = (
            f"Generated tests ({generated:.2f}, mean over {len(suites)} suites written from "
            "the task by another agent; a few of them may be wrong):"
        )
        if not failures:
            return f"{head} all passed."
        shown = "\n".join(failures[:_MAX_FAILURE_LINES])
        more = len(failures) - _MAX_FAILURE_LINES
        if more > 0:
            shown += f"\n... and {more} more"
        return f"{head}\n{shown}"[:_MAX_NOTE_CHARS]
