import asyncio
import time
from pathlib import Path

import pytest

from agent_mcts.core.value import CommandValueFunction, Evaluation


def evaluate(command: str, workdir: Path, timeout_s: float = 30.0) -> Evaluation:
    return asyncio.run(CommandValueFunction(command, timeout_s=timeout_s).evaluate(workdir))


def test_exit_zero_is_perfect(tmp_path: Path) -> None:
    result = evaluate("echo all good", tmp_path)
    assert result.score == 1.0
    assert "all good" in result.detail


def test_pytest_style_partial_credit(tmp_path: Path) -> None:
    result = evaluate("echo '=== 3 failed, 2 passed in 0.5s ==='; exit 1", tmp_path)
    assert result.score == pytest.approx(0.4)


def test_errors_count_against_score(tmp_path: Path) -> None:
    result = evaluate("echo '1 failed, 2 passed, 1 error in 1s'; exit 1", tmp_path)
    assert result.score == pytest.approx(0.5)


def test_unparseable_failure_is_zero(tmp_path: Path) -> None:
    result = evaluate("echo kaboom >&2; exit 2", tmp_path)
    assert result.score == 0.0
    assert "kaboom" in result.detail  # stderr is folded into the feedback


def test_timeout_is_zero(tmp_path: Path) -> None:
    result = evaluate("sleep 5", tmp_path, timeout_s=0.3)
    assert result.score == 0.0
    assert "timed out" in result.detail


def test_timeout_does_not_wait_for_background_children(tmp_path: Path) -> None:
    """Test runners fork workers and suites start servers; the timeout must reach them.

    A child that inherits the shell's stdout keeps the pipe open, so killing only the
    shell means the following read blocks until that child exits on its own.
    """
    started = time.monotonic()
    result = evaluate("sleep 30 & sleep 30", tmp_path, timeout_s=0.5)

    assert time.monotonic() - started < 5.0
    assert result.score == 0.0


def test_cancellation_kills_the_value_command(tmp_path: Path) -> None:
    """Ctrl-C ends the run, so the test suite has no business still running afterwards."""
    marker = tmp_path / "finished.txt"
    value_fn = CommandValueFunction(f"sleep 0.8; echo done > {marker}")

    async def cancel_mid_run() -> None:
        task = asyncio.ensure_future(value_fn.evaluate(tmp_path))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_mid_run())

    time.sleep(1.0)  # past when the surviving command would have written its marker
    assert not marker.exists()


def test_cancellation_kills_the_whole_command_tree(tmp_path: Path) -> None:
    marker = tmp_path / "child_finished.txt"
    value_fn = CommandValueFunction(f"(sleep 0.8; echo done > {marker}) & wait")

    async def cancel_mid_run() -> None:
        task = asyncio.ensure_future(value_fn.evaluate(tmp_path))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_mid_run())

    time.sleep(1.0)
    assert not marker.exists()


def test_runs_in_workdir(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("here\n")
    result = evaluate("cat marker.txt", tmp_path)
    assert result.score == 1.0
    assert "here" in result.detail


def ratio(output: str, tmp_path: Path) -> float:
    (tmp_path / "out.txt").write_text(output)
    return evaluate("cat out.txt; exit 1", tmp_path).score


def test_cargo_sums_every_test_binary(tmp_path: Path) -> None:
    output = """
running 4 tests
test parser::unary ... FAILED
test result: FAILED. 3 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

running 2 tests
test result: ok. 2 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

error: test failed, to rerun pass `--lib`
"""
    assert ratio(output, tmp_path) == pytest.approx(5 / 6)


def test_go_verbose_counts_individual_tests(tmp_path: Path) -> None:
    output = """=== RUN   TestAdd
--- PASS: TestAdd (0.00s)
=== RUN   TestDivide
    calc_test.go:14: got 0, want error
--- FAIL: TestDivide (0.00s)
=== RUN   TestTable
    --- PASS: TestTable/small (0.00s)
--- PASS: TestTable (0.00s)
FAIL
FAIL\texample.com/calc\t0.21s
"""
    assert ratio(output, tmp_path) == pytest.approx(3 / 4)


def test_go_without_verbose_falls_back_to_packages(tmp_path: Path) -> None:
    output = """--- FAIL: TestDivide (0.00s)
    calc_test.go:14: got 0, want error
FAIL
FAIL\texample.com/calc\t0.21s
ok  \texample.com/parse\t0.11s
ok  \texample.com/render\t(cached)
"""
    assert ratio(output, tmp_path) == pytest.approx(2 / 3)


def test_junit_style_uses_the_final_total(tmp_path: Path) -> None:
    output = """[ERROR] Tests run: 3, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.04 s
[INFO] Tests run: 4, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 0.01 s
[ERROR] Tests run: 10, Failures: 2, Errors: 1, Skipped: 2
"""
    assert ratio(output, tmp_path) == pytest.approx(5 / 8)


def test_jest_summary_still_parses(tmp_path: Path) -> None:
    assert ratio("Tests:       2 failed, 6 passed, 8 total\n", tmp_path) == pytest.approx(0.75)
