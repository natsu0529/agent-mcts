"""Command-line entry point: run / resume / show / apply."""

from __future__ import annotations

import asyncio
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console

from agent_mcts import __version__, project, tui, update_check
from agent_mcts.adapters import AdapterError, AgentBackend, ClaudeCodeAdapter, CodexCliAdapter
from agent_mcts.core import journal
from agent_mcts.core.engine import SearchConfig, SearchEngine
from agent_mcts.core.judge import HybridValueFunction, LLMJudge
from agent_mcts.core.model import Node, NodeStatus, RunMeta, Tree
from agent_mcts.core.value import CommandValueFunction, ValueFunction
from agent_mcts.core.worktree import GitError, WorktreeManager

app = typer.Typer(
    help="Turn any coding agent into a tree-searching agent.",
    no_args_is_help=True,
)
console = Console()

SUPPORTED_AGENTS = ("claude", "codex")


def _fail(message: str) -> NoReturn:
    console.print(f"[red]error:[/] {message}")
    raise typer.Exit(code=1)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"agent-mcts {__version__}")
        raise typer.Exit()


def _build_adapter(
    agent_name: str,
    *,
    cfg: project.ProjectConfig,
    model: str | None,
    timeout_s: float,
    for_judge: bool = False,
) -> AgentBackend:
    if agent_name == "claude":
        value_command = cfg.value.command
        allowed_tools = list(dict.fromkeys([f"Bash({value_command})"] if value_command else []))
        allowed_tools.extend(tool for tool in cfg.claude.allowed_tools if tool not in allowed_tools)
        if for_judge:
            allowed_tools = []  # the judge reads a prompt; it has nothing to run
        return ClaudeCodeAdapter(
            model=model or cfg.model,
            timeout_s=timeout_s,
            allowed_tools=allowed_tools,
        )
    if agent_name == "codex":
        return CodexCliAdapter(model=model or cfg.model, timeout_s=timeout_s)
    supported = ", ".join(SUPPORTED_AGENTS)
    raise AdapterError(f"unsupported agent `{agent_name}`. Choose one of: {supported}")


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show version and exit.",
        ),
    ] = False,
) -> None:
    """agent-mcts — MCTS test-time search for coding agents."""
    if console.is_terminal:
        message = update_check.notice(__version__)
        if message is not None:
            console.print(f"[dim]{message}[/]")


@app.command()
def run(
    task: Annotated[str, typer.Argument(help="The task to search a solution for.")],
    max_nodes: Annotated[
        int | None, typer.Option("-n", "--max-nodes", help="Episode budget.")
    ] = None,
    max_cost: Annotated[
        float | None, typer.Option("--max-cost", help="Cost ceiling in USD.")
    ] = None,
    value: Annotated[
        str | None,
        typer.Option("--value", help="Value command (defaults to auto-detected tests)."),
    ] = None,
    model: Annotated[str | None, typer.Option("--model", help="Agent model override.")] = None,
    agent: Annotated[
        str | None,
        typer.Option("--agent", help="Agent backend to use: claude or codex."),
    ] = None,
    agent_timeout: Annotated[
        float | None,
        typer.Option("--agent-timeout", help="Maximum seconds for each agent episode."),
    ] = None,
    judge: Annotated[
        bool | None,
        typer.Option(
            "--judge/--no-judge",
            help="Score each attempt with an LLM judge as well as the value command.",
        ),
    ] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="Skip confirmations.")] = False,
) -> None:
    """Search for a solution to TASK with your coding agent."""
    try:
        repo = project.find_repo_root(Path.cwd())
        cfg = project.load_project_config(repo)
    except project.ProjectError as exc:
        _fail(str(exc))
    if max_nodes is not None:
        cfg.search.max_nodes = max_nodes
    if max_cost is not None:
        cfg.search.max_cost_usd = max_cost

    value_command = value or cfg.value.command or project.detect_value_command(repo)
    if value_command is None:
        _fail(
            "could not detect a test command to use as the value function. "
            f"Pass --value '<command>' or set \\[value].command in {project.CONFIG_FILENAME}."
        )
    cfg.value.command = value_command

    timeout_s = agent_timeout if agent_timeout is not None else cfg.claude.timeout_s
    if timeout_s <= 0:
        _fail("--agent-timeout must be greater than zero")
    agent_name = agent or cfg.agent
    if judge is not None:
        cfg.value.judge = judge
    cfg.model = model or cfg.model
    backends = _build_backends(agent_name, cfg=cfg, timeout_s=timeout_s)

    if project.is_dirty(repo):
        console.print(
            "[yellow]warning:[/] uncommitted changes detected — the search branches "
            "from HEAD and will not see them."
        )
    cost_flag_used = max_cost is not None or cfg.search.max_cost_usd != SearchConfig().max_cost_usd
    _print_plan(backends, cfg=cfg, timeout_s=timeout_s, cost_flag_used=cost_flag_used)
    if not yes:
        typer.confirm("Proceed?", abort=True)

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    tree = Tree(
        RunMeta(
            run_id=run_id,
            task=task,
            agent=agent_name,
            repo_root=str(repo),
            base_commit=project.head_commit(repo),
            config={
                "model": cfg.model,
                "timeout_s": timeout_s,
                "value": cfg.value.model_dump(mode="json"),
                "search": cfg.search.model_dump(mode="json"),
            },
        )
    )
    engine = _build_engine(tree, repo=repo, cfg=cfg, backends=backends)
    _search_and_report(engine, cfg=cfg, judged=backends.judge is not None)


@dataclass
class _Backends:
    agent_name: str  # as the user names it (`claude`, `codex`)
    agent: AgentBackend
    judge: AgentBackend | None
    judge_label: str  # "" or " + judge(<model>)", for the plan line


def _build_backends(agent_name: str, *, cfg: project.ProjectConfig, timeout_s: float) -> _Backends:
    try:
        adapter = _build_adapter(agent_name, cfg=cfg, model=cfg.model, timeout_s=timeout_s)
        if not cfg.value.judge:
            return _Backends(agent_name, adapter, None, "")
        judge_agent = cfg.value.judge_agent or agent_name
        judge_model = cfg.value.judge_model or cfg.model
        judge_adapter = _build_adapter(
            judge_agent,
            cfg=cfg,
            model=judge_model,
            timeout_s=cfg.value.judge_timeout_s,
            for_judge=True,
        )
        label = f" + judge({judge_model or judge_agent})"
        return _Backends(agent_name, adapter, judge_adapter, label)
    except AdapterError as exc:
        _fail(str(exc))


def _print_plan(
    backends: _Backends, *, cfg: project.ProjectConfig, timeout_s: float, cost_flag_used: bool
) -> None:
    adapter = backends.agent
    if adapter.reports_cost:
        budget = f"[bold]{cfg.search.max_nodes}[/] nodes / ${cfg.search.max_cost_usd:.2f}"
    else:
        budget = f"[bold]{cfg.search.max_nodes}[/] nodes ({adapter.name} does not report cost)"
        if cost_flag_used:
            console.print(
                f"[yellow]warning:[/] {adapter.name} does not report cost, so the "
                f"${cfg.search.max_cost_usd:.2f} ceiling is not enforced — the budget is "
                f"{cfg.search.max_nodes} episodes."
            )
    console.print(
        f"Agent: [bold]{backends.agent_name}[/] · "
        f"Value: [bold]{cfg.value.command}{backends.judge_label}[/] · "
        f"Budget: {budget} · Timeout: {timeout_s:.0f}s/episode"
    )


def _build_engine(
    tree: Tree,
    *,
    repo: Path,
    cfg: project.ProjectConfig,
    backends: _Backends,
    accepted_unknown_cost: frozenset[str] = frozenset(),
) -> SearchEngine:
    meta = tree.meta
    assert cfg.value.command is not None
    value_fn: ValueFunction = CommandValueFunction(cfg.value.command)
    if backends.judge is not None:
        value_fn = HybridValueFunction(
            value_fn,
            LLMJudge(backends.judge, task=meta.task),
            base_commit=meta.base_commit or project.head_commit(repo),
            w_test=cfg.value.w_test,
            w_judge=cfg.value.w_judge,
        )
    return SearchEngine(
        tree=tree,
        adapter=backends.agent,
        value_fn=value_fn,
        worktrees=WorktreeManager(repo, meta.run_id),
        journal_path=project.journal_path(repo, meta.run_id),
        config=cfg.search,
        accepted_unknown_cost=accepted_unknown_cost,
    )


def _search_and_report(engine: SearchEngine, *, cfg: project.ProjectConfig, judged: bool) -> None:
    tree = engine.tree
    worktrees = engine.worktrees
    view = tui.LiveSearchView(engine, console)
    engine.on_change = view.refresh
    try:
        with view:
            asyncio.run(engine.run())
    except KeyboardInterrupt:
        console.print(
            "\n[yellow]Interrupted[/] — the partial tree is saved and usable. "
            "Continue it with: [bold]agent-mcts resume[/]"
        )
    finally:
        try:
            worktrees.cleanup()
        except GitError as exc:
            console.print(f"[yellow]warning:[/] could not remove every worktree: {exc}")
        for node_id, path in worktrees.preserved.items():
            console.print(
                f"[yellow]kept[/] worktree for {node_id} at {path} "
                "— its changes could not be committed to a branch."
            )

    console.print(
        f"\nSearch finished: {engine.episodes} episodes, {_spend_summary(engine)} · "
        f"run {tree.meta.run_id}"
    )
    root = tree.root
    if not judged and root is not None and (root.reward or 0.0) >= cfg.search.success_threshold:
        console.print(
            "[yellow]warning:[/] the value command already passes on the untouched "
            "repository, so it cannot tell attempts apart: the search stops at the first "
            "attempt that keeps it passing, whatever that attempt did. Re-run with "
            "[bold]--judge[/], or use a value command that fails until the task is done."
        )
    best = tree.best()
    if best is None or best.parent_id is None:
        console.print("No attempt beat the baseline. Inspect with: [bold]agent-mcts show[/]")
    else:
        reward = best.reward if best.reward is not None else 0.0
        summary = best.summary.splitlines()[0][:80] if best.summary else ""
        console.print(f"Best: [bold]{best.id}[/] reward={reward:.2f} {summary}")
        console.print("Next: [bold]agent-mcts show[/] · [bold]agent-mcts apply[/]")


@app.command()
def resume(
    run_id: Annotated[
        str | None, typer.Argument(help="Run id to continue (default: the latest run).")
    ] = None,
    max_nodes: Annotated[
        int | None,
        typer.Option("-n", "--max-nodes", help="New total episode budget for the run."),
    ] = None,
    max_cost: Annotated[
        float | None, typer.Option("--max-cost", help="New total cost ceiling in USD.")
    ] = None,
    yes: Annotated[bool, typer.Option("-y", "--yes", help="Skip confirmations.")] = False,
) -> None:
    """Continue an interrupted or budget-limited search where it stopped."""
    tree = _load_tree(run_id)
    meta = tree.meta
    repo = Path(meta.repo_root)
    root = tree.root
    if root is None or root.status is NodeStatus.FAILED:
        _fail(
            f"run {meta.run_id} never finished its baseline evaluation, so there is "
            'nothing to continue. Start a new one with: agent-mcts run "<task>"'
        )

    try:
        cfg = project.load_project_config(repo)
    except project.ProjectError as exc:
        _fail(str(exc))
    # The run's own settings win over today's config file: a resumed search must score
    # new nodes the way it scored the old ones, or their rewards are not comparable.
    saved = meta.config
    try:
        if "value" in saved:
            cfg.value = project.ValueConfig.model_validate(saved["value"])
        if "search" in saved:
            cfg.search = SearchConfig.model_validate(saved["search"])
    except ValidationError as exc:
        _fail(f"run {meta.run_id} has unreadable saved settings: {exc}")
    cfg.model = saved.get("model", cfg.model)
    timeout_s = float(saved.get("timeout_s", cfg.claude.timeout_s))
    if not saved:
        console.print(
            "[yellow]warning:[/] this run predates saved settings; continuing with the "
            f"current {project.CONFIG_FILENAME} and defaults."
        )
        cfg.value.command = cfg.value.command or project.detect_value_command(repo)
    if cfg.value.command is None:
        _fail(f"no value command known for run {meta.run_id}; set \\[value].command and retry")
    if max_nodes is not None:
        cfg.search.max_nodes = max_nodes
    if max_cost is not None:
        cfg.search.max_cost_usd = max_cost

    backends = _build_backends(meta.agent, cfg=cfg, timeout_s=timeout_s)
    unknown = frozenset(
        n.id
        for n in tree.nodes.values()
        if not (n.cost_known and n.eval_cost_known) or n.status is NodeStatus.RUNNING
    )
    engine = _build_engine(
        tree, repo=repo, cfg=cfg, backends=backends, accepted_unknown_cost=unknown
    )

    console.print(f"Resuming run [bold]{meta.run_id}[/]: {meta.task}")
    if engine.solved:
        console.print(
            "This run already found an attempt that meets the success threshold. "
            "Next: [bold]agent-mcts show[/] · [bold]agent-mcts apply[/]"
        )
        return
    if engine.episodes >= cfg.search.max_nodes:
        _fail(
            f"the episode budget is used up ({engine.episodes}/{cfg.search.max_nodes}). "
            "Raise the total with -n, e.g. "
            f"agent-mcts resume -n {engine.episodes + SearchConfig().max_nodes}"
        )
    if backends.agent.reports_cost and engine.total_cost_usd >= cfg.search.max_cost_usd:
        _fail(
            f"the cost ceiling is reached (${engine.total_cost_usd:.2f} of "
            f"${cfg.search.max_cost_usd:.2f}). Raise the total with --max-cost."
        )
    _print_plan(backends, cfg=cfg, timeout_s=timeout_s, cost_flag_used=max_cost is not None)
    console.print(f"Already spent: {engine.episodes} episodes, {_spend_summary(engine)}")
    if unknown:
        console.print(
            f"[yellow]warning:[/] {len(unknown)} episode(s) in this run were cut off before "
            "reporting their cost, so the real spend so far is higher than shown and the "
            "cost ceiling only bounds what is reported."
        )
    if not yes:
        typer.confirm("Continue?", abort=True)

    engine.recover_interrupted()
    _search_and_report(engine, cfg=cfg, judged=backends.judge is not None)


def _spend_summary(engine: SearchEngine) -> str:
    lost = engine.unknown_cost_episodes
    if engine.adapter.reports_cost:
        spend = f"${engine.total_cost_usd:.2f}" + (" reported" if lost else "")
    else:
        spend = f"{tui.usage_label(engine)} tokens ({engine.adapter.name} does not report cost)"
    if lost:
        spend += (
            f" + {lost} episode(s) with unknown cost (cut off before the agent reported its spend)"
        )
    return spend


def _node_cost(node: Node) -> str:
    episode = _episode_cost(node)
    if not node.eval_cost_known:
        return f"{episode} + judge unknown"
    if node.eval_cost_usd:
        return f"{episode} + judge ${node.eval_cost_usd:.2f}"
    return episode


def _episode_cost(node: Node) -> str:
    if not node.cost_known:
        return "unknown"
    if node.cost_usd is None:
        if node.usage is None:
            return "not reported"
        usage = node.usage
        tokens = f"{tui.format_tokens(usage.input_tokens)} in / "
        tokens += f"{tui.format_tokens(usage.output_tokens)} out tokens"
        return f"not reported ({tokens})"
    return f"${node.cost_usd:.2f}"


def _load_tree(run_id: str | None) -> Tree:
    try:
        repo = project.find_repo_root(Path.cwd())
    except project.ProjectError as exc:
        _fail(str(exc))
    resolved = run_id or project.latest_run_id(repo)
    if resolved is None:
        _fail('no runs found. Start one with: agent-mcts run "<task>"')
    path = project.journal_path(repo, resolved)
    if not path.exists():
        _fail(f"run {resolved} has no journal at {path}")
    return journal.load(path)


@app.command()
def show(
    node: Annotated[str | None, typer.Argument(help="Node id for details (e.g. n3).")] = None,
    run_id: Annotated[str | None, typer.Option("--run", help="Run id (default: latest).")] = None,
) -> None:
    """Show the search tree of a run, or one node's full details."""
    tree = _load_tree(run_id)
    if node is None:
        console.print(f"task: {tree.meta.task}  ·  run {tree.meta.run_id}")
        console.print(tui.render_tree(tree))
        return
    found = tree.nodes.get(node)
    if found is None:
        _fail(f"no node {node} in run {tree.meta.run_id}")
    reward = "-" if found.reward is None else f"{found.reward:.2f}"
    status = f"{found.status.value}  reward={reward}  Q={found.q:.2f}  N={found.visits}"
    if found.test_score is not None:
        judged = "-" if found.judge_score is None else f"{found.judge_score:.2f}"
        status += f"  (tests={found.test_score:.2f} judge={judged})"
    for title, body in (
        ("status", status),
        ("branch", found.branch or "-"),
        ("session", found.session_id or "-"),
        ("cost", f"{_node_cost(found)} in {found.duration_s:.0f}s"),
        ("prompt", found.prompt or "-"),
        ("summary", found.summary or "-"),
        ("evaluation", found.eval_detail or "-"),
    ):
        console.print(f"[bold]{title}[/]: {body}")


@app.command()
def apply(
    node: Annotated[
        str | None, typer.Argument(help="Node id to apply (default: the best node).")
    ] = None,
    run_id: Annotated[str | None, typer.Option("--run", help="Run id (default: latest).")] = None,
) -> None:
    """Stage a node's changes onto your working tree (squash merge — you commit)."""
    tree = _load_tree(run_id)
    target = tree.nodes.get(node) if node is not None else tree.best()
    if target is None:
        _fail(f"no node {node} in run {tree.meta.run_id}" if node else "run has no scored nodes")
    if target.parent_id is None:
        _fail("the best node is the untouched baseline — nothing to apply")
    if target.branch is None:
        _fail(f"node {target.id} has no snapshot branch (it failed before committing)")

    repo = Path(tree.meta.repo_root)
    if project.is_dirty(repo):
        _fail("your working tree has uncommitted changes; commit or stash before applying")
    proc = subprocess.run(
        ["git", "merge", "--squash", target.branch],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"squash merge of {target.branch} failed:\n{proc.stdout}{proc.stderr}")
    reward = target.reward if target.reward is not None else 0.0
    console.print(
        f"Staged [bold]{target.id}[/] (reward={reward:.2f}) from {target.branch}.\n"
        "Review with [bold]git diff --cached[/] and commit when happy."
    )
