"""CLI end-to-end tests: a fake adapter is injected, everything else is real
(engine, worktrees, journal, value command, git)."""

import json
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest
from typer.testing import CliRunner

from agent_mcts import __version__, cli, project
from agent_mcts.adapters.base import EpisodeResult, TokenUsage

runner = CliRunner()

VALUE_CMD = "test -f attempt.txt"  # baseline lacks the file → 0.0; any episode → 1.0


class FakeAdapter:
    """Constructor-compatible stand-in for ClaudeCodeAdapter."""

    calls = 0
    last_timeout_s = 0.0
    last_allowed_tools: ClassVar[list[str]] = []

    def __init__(
        self,
        binary: str | None = None,
        *,
        model: str | None = None,
        permission_mode: str = "acceptEdits",
        timeout_s: float = 600.0,
        allowed_tools: list[str] | None = None,
    ) -> None:
        self.model = model
        FakeAdapter.last_timeout_s = timeout_s
        FakeAdapter.last_allowed_tools = allowed_tools or []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def reports_cost(self) -> bool:
        return True

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        FakeAdapter.calls += 1
        (workdir / "attempt.txt").write_text(f"episode {FakeAdapter.calls}\n")
        return EpisodeResult(
            session_id=f"s{FakeAdapter.calls}",
            summary=f"approach {FakeAdapter.calls}",
            cost_usd=0.5,
        )


class FakeUnpricedAdapter(FakeAdapter):
    """Stand-in for a backend that meters tokens but never reports dollars (Codex)."""

    @property
    def reports_cost(self) -> bool:
        return False

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        FakeAdapter.calls += 1
        (workdir / "attempt.txt").write_text(f"episode {FakeAdapter.calls}\n")
        return EpisodeResult(
            session_id=f"s{FakeAdapter.calls}",
            summary=f"approach {FakeAdapter.calls}",
            cost_usd=None,
            usage=TokenUsage(input_tokens=1200, output_tokens=300),
        )


class FakeCodexAdapter(FakeUnpricedAdapter):
    @property
    def name(self) -> str:
        return "codex"


@pytest.fixture
def project_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(repo)
    monkeypatch.setattr(cli, "ClaudeCodeAdapter", FakeAdapter)
    monkeypatch.setattr(cli, "CodexCliAdapter", FakeCodexAdapter)
    FakeAdapter.calls = 0
    FakeAdapter.last_timeout_s = 0.0
    FakeAdapter.last_allowed_tools = []
    return repo


def do_run(args: list[str] | None = None) -> str:
    result = runner.invoke(
        cli.app, ["run", "fix the widget", "-y", "--value", VALUE_CMD, *(args or [])]
    )
    assert result.exit_code == 0, result.output
    return result.output


def test_version() -> None:
    result = runner.invoke(cli.app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_run_end_to_end(project_repo: Path) -> None:
    output = do_run(["-n", "3"])

    assert "Best:" in output and "n1" in output
    assert "Search finished: 1 episodes" in output  # perfect score → early stop
    journals = list((project_repo / ".agent-mcts" / "runs").glob("*/tree.jsonl"))
    assert len(journals) == 1
    # Worktrees are cleaned up; snapshot branches survive.
    branches = subprocess.run(
        ["git", "branch", "--list", "agent-mcts/*"],
        cwd=project_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "/n1" in branches
    assert not any((project_repo / ".agent-mcts" / "worktrees").rglob("attempt.txt"))


def test_run_configures_timeout_and_preapproves_value_command(project_repo: Path) -> None:
    output = do_run(["--agent-timeout", "42"])

    assert "Timeout: 42s/episode" in output
    assert FakeAdapter.last_timeout_s == pytest.approx(42.0)
    assert FakeAdapter.last_allowed_tools == [f"Bash({VALUE_CMD})"]


def test_run_loads_claude_options_from_project_config(project_repo: Path) -> None:
    (project_repo / ".agent-mcts.toml").write_text(
        '[claude]\ntimeout_s = 90\nallowed_tools = ["Bash(git diff *)"]\n'
    )

    do_run()

    assert FakeAdapter.last_timeout_s == pytest.approx(90.0)
    assert FakeAdapter.last_allowed_tools == [f"Bash({VALUE_CMD})", "Bash(git diff *)"]


def test_run_with_unpriced_agent_budgets_by_episodes(
    project_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "ClaudeCodeAdapter", FakeUnpricedAdapter)

    output = " ".join(do_run(["-n", "3"]).split())  # rich wraps long lines

    assert "Budget: 3 nodes (fake does not report cost)" in output
    assert "warning" not in output  # the default ceiling was never asked for
    assert "Search finished: 1 episodes, 1.2k in / 300 out tokens" in output
    assert "unknown cost" not in output
    assert "$" not in output

    node_view = runner.invoke(cli.app, ["show", "n1"])
    assert node_view.exit_code == 0, node_view.output
    assert "not reported (1.2k in / 300 out tokens)" in " ".join(node_view.output.split())


def test_run_with_unpriced_agent_warns_about_an_explicit_ceiling(
    project_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "ClaudeCodeAdapter", FakeUnpricedAdapter)

    output = " ".join(do_run(["--max-cost", "5"]).split())

    assert "warning:" in output
    assert "$5.00 ceiling is not enforced" in output


def test_run_selects_codex_agent(project_repo: Path) -> None:
    output = do_run(["--agent", "codex", "--agent-timeout", "42"])

    assert "Agent: codex" in output
    assert FakeAdapter.last_timeout_s == pytest.approx(42.0)
    assert FakeAdapter.last_allowed_tools == []


def test_run_rejects_unknown_agent(project_repo: Path) -> None:
    result = runner.invoke(
        cli.app, ["run", "fix the widget", "-y", "--value", VALUE_CMD, "--agent", "bogus"]
    )
    assert result.exit_code == 1
    assert "unsupported agent" in result.output


def test_run_rejects_reused_suites_without_generated_tests(project_repo: Path) -> None:
    (project_repo / ".agent-mcts.toml").write_text('[value]\ngenerate_reuse = "s.json"\n')
    result = runner.invoke(cli.app, ["run", "fix the widget", "-y", "--value", VALUE_CMD])
    assert result.exit_code == 1
    assert "generate_reuse needs generated tests" in result.output
    assert FakeAdapter.calls == 0


def test_run_rejects_reused_suites_it_cannot_read(project_repo: Path) -> None:
    (project_repo / ".agent-mcts.toml").write_text('[value]\ngenerate_reuse = "missing.json"\n')
    result = runner.invoke(
        cli.app,
        ["run", "fix the widget", "-y", "--value", "python -m pytest -q", "--generate-tests"],
    )
    assert result.exit_code == 1
    assert "cannot reuse generated tests" in result.output
    assert FakeAdapter.calls == 0


def test_run_rejects_non_positive_agent_timeout(project_repo: Path) -> None:
    result = runner.invoke(
        cli.app, ["run", "fix the widget", "-y", "--value", VALUE_CMD, "--agent-timeout", "0"]
    )
    assert result.exit_code == 1
    assert "greater than zero" in result.output


def test_show_tree_and_node(project_repo: Path) -> None:
    do_run()

    tree_view = runner.invoke(cli.app, ["show"])
    assert tree_view.exit_code == 0, tree_view.output
    assert "n0" in tree_view.output and "n1" in tree_view.output
    assert "best" in tree_view.output
    # UCT stats must survive rich markup (regression: [r=…] was parsed as a tag).
    assert "Q=" in tree_view.output and "r=1.00" in tree_view.output

    node_view = runner.invoke(cli.app, ["show", "n1"])
    assert node_view.exit_code == 0, node_view.output
    assert "prompt" in node_view.output
    assert "fix the widget" in node_view.output.replace("\n", " ")

    missing = runner.invoke(cli.app, ["show", "n9"])
    assert missing.exit_code == 1


def test_apply_stages_best_node(project_repo: Path) -> None:
    do_run()

    result = runner.invoke(cli.app, ["apply"])
    assert result.exit_code == 0, result.output
    assert "Staged" in result.output

    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=project_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "attempt.txt" in staged


def test_apply_refuses_dirty_tree(project_repo: Path) -> None:
    do_run()
    (project_repo / "app.py").write_text("print('changed')\n")

    result = runner.invoke(cli.app, ["apply"])
    assert result.exit_code == 1
    assert "uncommitted" in result.output


def test_run_without_value_command_fails(project_repo: Path) -> None:
    result = runner.invoke(cli.app, ["run", "fix it", "-y"])
    assert result.exit_code == 1
    assert "--value" in result.output


def test_show_without_runs_fails(project_repo: Path) -> None:
    result = runner.invoke(cli.app, ["show"])
    assert result.exit_code == 1
    assert "no runs" in result.output


class FakeJudgingAdapter(FakeAdapter):
    """Plays both roles: writes a file as the agent, returns a verdict as the judge."""

    judge_calls = 0
    rank_calls = 0
    judge_models: ClassVar[list[str | None]] = []
    judge_allowed_tools: ClassVar[list[list[str]]] = []

    def __init__(
        self,
        binary: str | None = None,
        *,
        model: str | None = None,
        permission_mode: str = "acceptEdits",
        timeout_s: float = 600.0,
        allowed_tools: list[str] | None = None,
    ) -> None:
        super().__init__(model=model, timeout_s=timeout_s, allowed_tools=allowed_tools)
        self.allowed_tools = allowed_tools or []

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        if prompt.startswith("You are the final judge"):
            FakeJudgingAdapter.rank_calls += 1
            return EpisodeResult(
                session_id="rank",
                summary="<Explanation>n2 is tidier</Explanation><Ranking>n2, n1</Ranking>",
                cost_usd=0.05,
            )
        if not prompt.startswith("You are the evaluator"):
            return await super().run_episode(prompt, workdir, resume_session=resume_session)
        FakeJudgingAdapter.judge_calls += 1
        FakeJudgingAdapter.judge_models.append(self.model)
        FakeJudgingAdapter.judge_allowed_tools.append(self.allowed_tools)
        return EpisodeResult(
            session_id=f"j{FakeJudgingAdapter.judge_calls}",
            summary="<Explanation>solid but untested</Explanation><Reward>60</Reward>",
            cost_usd=0.05,
        )


@pytest.fixture
def judged_repo(project_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cli, "ClaudeCodeAdapter", FakeJudgingAdapter)
    FakeJudgingAdapter.judge_calls = 0
    FakeJudgingAdapter.rank_calls = 0
    FakeJudgingAdapter.judge_models = []
    FakeJudgingAdapter.judge_allowed_tools = []
    return project_repo


def test_run_with_judge_scores_and_reports_components(judged_repo: Path) -> None:
    (judged_repo / ".agent-mcts.toml").write_text('[value]\njudge_model = "haiku"\n')
    output = do_run(["-n", "2", "--judge"])

    assert "+ judge(haiku)" in output  # the plan line names the judge
    # Tests pass and the judge says +60 → 0.5 + 0.5 * 0.8; no early stop at 1.0.
    assert "Search finished: 2 episodes" in output
    assert "reward=0.90" in output
    # 2 episodes at $0.50, 2 judge calls and 1 final comparison at $0.05 each.
    assert "$1.15" in output
    # The two attempts tie on reward; the head-to-head comparison decides.
    flat = " ".join(output.split())
    assert "Final comparison: n2 chosen over n1. n2 is tidier" in flat
    assert "Best: n2" in flat
    assert FakeJudgingAdapter.rank_calls == 1
    assert FakeJudgingAdapter.judge_calls == 2
    assert FakeJudgingAdapter.judge_models == ["haiku", "haiku"]
    assert FakeJudgingAdapter.judge_allowed_tools == [[], []]  # the judge runs nothing

    shown = runner.invoke(cli.app, ["show", "n1"]).output
    assert "tests=1.00 judge=0.80" in shown
    assert "$0.50 + evaluation $0.05" in shown
    assert "solid but untested" in shown


def test_judge_is_off_by_default(judged_repo: Path) -> None:
    do_run(["-n", "1"])
    assert FakeJudgingAdapter.judge_calls == 0


def test_config_enables_the_judge(judged_repo: Path) -> None:
    (judged_repo / ".agent-mcts.toml").write_text("[value]\njudge = true\n")
    do_run(["-n", "1"])
    assert FakeJudgingAdapter.judge_calls == 1


def test_no_judge_flag_overrides_config(judged_repo: Path) -> None:
    (judged_repo / ".agent-mcts.toml").write_text("[value]\njudge = true\n")
    do_run(["-n", "1", "--no-judge"])
    assert FakeJudgingAdapter.judge_calls == 0


class FakeTestWritingAdapter(FakeAdapter):
    """Plays both roles: writes a file as the agent, writes a test file as the writer."""

    writer_calls = 0
    writer_files: ClassVar[list[list[str]]] = []
    suite = (
        "from pathlib import Path\n\ndef test_attempt():\n    assert Path('attempt.txt').exists()\n"
    )

    async def run_episode(
        self, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        if not prompt.startswith("Another agent will be asked"):
            return await super().run_episode(prompt, workdir, resume_session=resume_session)
        FakeTestWritingAdapter.writer_calls += 1
        FakeTestWritingAdapter.writer_files.append(sorted(p.name for p in workdir.iterdir()))
        suite = self.suite
        return EpisodeResult(
            session_id=f"w{FakeTestWritingAdapter.writer_calls}",
            summary=f"<TestFile>tests/test_a.py</TestFile>\n```python\n{suite}```\n",
            cost_usd=0.1,
        )


@pytest.fixture
def test_writing_repo(project_repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cli, "ClaudeCodeAdapter", FakeTestWritingAdapter)
    FakeTestWritingAdapter.writer_calls = 0
    FakeTestWritingAdapter.writer_files = []
    command = f"{sys.executable} -m pytest -q -p no:cacheprovider {{file}}"
    (project_repo / ".agent-mcts.toml").write_text(f"[value]\ngenerate_command = '{command}'\n")
    return project_repo


def test_run_reuses_suites_given_on_the_command_line(
    test_writing_repo: Path, tmp_path: Path
) -> None:
    saved = tmp_path / "suites.json"
    suite = {
        "path": "tests/test_agent_mcts_generated_1.py",
        "content": FakeTestWritingAdapter.suite,
    }
    saved.write_text(json.dumps({"suites": [suite], "errors": []}))

    output = do_run(["-n", "2", "--generate-tests", "--generate-reuse", str(saved)])

    assert "reused from" in output
    assert FakeTestWritingAdapter.writer_calls == 0
    assert "reward=1.00" in output
    # The path stays out of the committed config: the agent's worktree cannot see it.
    assert "generate_reuse" not in (test_writing_repo / ".agent-mcts.toml").read_text()
    run_dir = test_writing_repo / ".agent-mcts" / "runs" / _latest_run(test_writing_repo)
    assert (run_dir / "generated" / "suites.json").read_text() == saved.read_text()


@pytest.mark.parametrize("path", ["../outside.py", "/tmp/test_x.py", "tests\\test_x.py"])
def test_run_rejects_reused_suites_that_point_outside_the_repo(
    test_writing_repo: Path, tmp_path: Path, path: str
) -> None:
    saved = tmp_path / "suites.json"
    saved.write_text(json.dumps({"suites": [{"path": path, "content": "x = 1\n"}]}))
    result = runner.invoke(
        cli.app,
        [
            *["run", "fix it", "-y", "--value", VALUE_CMD, "--generate-tests"],
            *["--generate-reuse", str(saved)],
        ],
    )
    assert result.exit_code == 1
    assert "inside the repository" in result.output
    assert FakeAdapter.calls == 0


def test_run_with_generated_tests(test_writing_repo: Path) -> None:
    output = do_run(["-n", "2", "--generate-tests"])

    assert "+ 3 generated test suites" in output  # the plan line names them
    # The baseline lacks attempt.txt, the first attempt makes every test pass.
    assert "Search finished: 1 episodes" in output
    assert "reward=1.00" in output
    # One episode at $0.50, three suites at $0.10 charged to the baseline.
    assert "$0.80" in output
    assert FakeTestWritingAdapter.writer_calls == 3
    assert all("app.py" in files for files in FakeTestWritingAdapter.writer_files)

    assert "tests=1.00 generated=1.00" in runner.invoke(cli.app, ["show", "n1"]).output
    root = runner.invoke(cli.app, ["show", "n0"]).output
    assert "tests=0.00 generated=0.00" in root
    assert "evaluation $0.30" in root
    # The suites never land on a node's branch.
    files = subprocess.run(
        [
            "git",
            "ls-tree",
            "-r",
            "--name-only",
            "agent-mcts/" + _latest_run(test_writing_repo) + "/n1",
        ],
        cwd=test_writing_repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert not any("generated" in f for f in files)


class FakeBugSharingWriter(FakeTestWritingAdapter):
    """Writes tests that the untouched repository passes and every attempt fails, the way
    tests that measure alignment in characters favour the unfixed table code."""

    suite = (
        "from pathlib import Path\n\n"
        "def test_unchanged():\n"
        "    assert not Path('attempt.txt').exists()\n\n"
        "def test_impossible():\n"
        "    assert False\n"
    )


def test_generated_tests_that_prefer_the_untouched_code_are_overruled(
    test_writing_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "ClaudeCodeAdapter", FakeBugSharingWriter)
    # The project's own tests pass before and after every attempt.
    result = runner.invoke(
        cli.app, ["run", "fix the widget", "-y", "--value", "true", "-n", "2", "--generate-tests"]
    )
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())

    assert "Search finished: 2 episodes" in output
    # n0 scores 0.75 (tests 1.0, half the generated tests); attempts 0.5.
    assert "untouched repository scored higher (0.75)" in output
    assert "the generated tests put it ahead" in output
    assert "Best: n1" in output  # both attempts score 0.5; the first wins the tie
    assert "warning" in runner.invoke(cli.app, ["show"]).output
    # `apply` offers the attempt, not "nothing to apply".
    subprocess.run(["git", "add", "-A"], cwd=test_writing_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "config"], cwd=test_writing_repo, check=True)
    staged = runner.invoke(cli.app, ["apply"])
    assert staged.exit_code == 0, staged.output
    assert "Staged n1" in " ".join(staged.output.split())


def _latest_run(repo: Path) -> str:
    run = project.latest_run_id(repo)
    assert run is not None
    return run


def test_generated_tests_and_judge_are_alternatives(test_writing_repo: Path) -> None:
    result = runner.invoke(
        cli.app, ["run", "fix it", "-y", "--value", VALUE_CMD, "--generate-tests", "--judge"]
    )
    assert result.exit_code == 1
    assert "alternatives" in result.output


def test_generated_tests_need_a_command_for_non_pytest_projects(project_repo: Path) -> None:
    result = runner.invoke(
        cli.app, ["run", "fix it", "-y", "--value", VALUE_CMD, "--generate-tests"]
    )
    assert result.exit_code == 1
    assert "generate_command" in result.output


def test_config_rejects_zero_weights(judged_repo: Path) -> None:
    (judged_repo / ".agent-mcts.toml").write_text("[value]\nw_test = 0\nw_judge = 0\n")
    result = runner.invoke(cli.app, ["run", "fix the widget", "-y", "--value", VALUE_CMD])
    assert result.exit_code == 1
    assert "cannot both be zero" in result.output


def test_green_baseline_without_judge_warns(project_repo: Path) -> None:
    result = runner.invoke(cli.app, ["run", "add a feature", "-y", "-n", "2", "--value", "true"])
    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "already passes on the untouched repository" in flat
    assert "--judge" in flat


def test_failing_baseline_does_not_warn(project_repo: Path) -> None:
    assert "already passes" not in " ".join(do_run(["-n", "1"]).split())


def test_green_baseline_with_judge_does_not_warn(judged_repo: Path) -> None:
    result = runner.invoke(
        cli.app, ["run", "add a feature", "-y", "-n", "1", "--value", "true", "--judge"]
    )
    assert result.exit_code == 0, result.output
    assert "already passes" not in " ".join(result.output.split())


def test_show_rounds_the_reward(judged_repo: Path) -> None:
    do_run(["-n", "1", "--judge"])
    shown = runner.invoke(cli.app, ["show", "n1"]).output
    assert "reward=0.90 " in shown


def test_missing_value_command_names_the_config_key(project_repo: Path) -> None:
    result = runner.invoke(cli.app, ["run", "fix the widget", "-y"])
    assert result.exit_code == 1
    assert "[value].command" in " ".join(result.output.split())


def test_apply_and_show_follow_the_final_comparison(judged_repo: Path) -> None:
    do_run(["-n", "2", "--judge"])

    tree_view = " ".join(runner.invoke(cli.app, ["show"]).output.split())
    assert "Final comparison: n2 chosen over n1" in tree_view
    applied = runner.invoke(cli.app, ["apply"])
    assert applied.exit_code == 0, applied.output
    assert "Staged n2" in applied.output


def test_final_comparison_can_be_turned_off(judged_repo: Path) -> None:
    (judged_repo / ".agent-mcts.toml").write_text("[value]\nselect = false\n")
    output = " ".join(do_run(["-n", "2", "--judge"]).split())
    assert FakeJudgingAdapter.rank_calls == 0
    assert "Final comparison" not in output


def test_long_final_comparison_reasoning_is_clipped_in_the_run_summary(
    judged_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_reason = "n2 is tidier. " + "It also handles more cases. " * 40 + "THE END"

    async def verbose(
        self: FakeJudgingAdapter, prompt: str, workdir: Path, *, resume_session: str | None = None
    ) -> EpisodeResult:
        if prompt.startswith("You are the final judge"):
            reply = f"<Explanation>{long_reason}</Explanation><Ranking>n2, n1</Ranking>"
            return EpisodeResult(session_id="rank", summary=reply, cost_usd=0.05)
        return await original(self, prompt, workdir, resume_session=resume_session)

    original = FakeJudgingAdapter.run_episode
    monkeypatch.setattr(FakeJudgingAdapter, "run_episode", verbose)

    summary = " ".join(do_run(["-n", "2", "--judge"]).split())
    assert "Final comparison: n2 chosen over n1. n2 is tidier." in summary
    assert "THE END" not in summary and "full text: agent-mcts show" in summary
    assert "THE END" in " ".join(runner.invoke(cli.app, ["show"]).output.split())
