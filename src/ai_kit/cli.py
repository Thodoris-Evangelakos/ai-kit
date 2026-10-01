"""AI Kit command-line interface."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .project import ProjectError, doctor_project, init_project, preflight_adoption, sync_project
from .verification import FailureClass, classify_check

app = typer.Typer(
    help="Make a repository legible, reproducible, and verifiable.", no_args_is_help=True
)
goal_app = typer.Typer(help="Manage acceptance contracts and their verification evidence.")
baseline_app = typer.Typer(help="Compare an adopted repository with its measured source baseline.")
app.add_typer(goal_app, name="goal")
app.add_typer(baseline_app, name="baseline")
out = Console()
err = Console(stderr=True)


def _fail(message: str, code: int = 1) -> None:
    err.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", is_eager=True, help="Show AI Kit version."),
) -> None:
    if version:
        out.print(__version__)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        out.print(ctx.get_help())


@app.command("version")
def version_command() -> None:
    """Show AI Kit version."""

    out.print(__version__)


@app.command()
def init(
    target: Path | None = typer.Argument(
        None, help="Git repository root; defaults to the current directory."
    ),
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
    module: list[str] | None = typer.Option(None, "--module", help="Enable a profile module."),
) -> None:
    """Initialize a safe, minimal AI Kit project."""

    root = target if target is not None else path
    try:
        result = init_project(root, modules=tuple(module) if module else ("strict-verification",))
    except (ProjectError, OSError, ValueError) as exc:
        _fail(str(exc))
    for change in result.changes:
        out.print(f"created/updated {change}")
    for problem in result.problems:
        err.print(f"[red]{problem}[/red]")
    if result.problems:
        raise typer.Exit(1)
    out.print("AI Kit initialized." if result.changes else "AI Kit already current.")


@app.command()
def sync(
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
    check: bool = typer.Option(False, "--check", help="Report drift or pending generation."),
) -> None:
    """Render managed Codex files from the profile without losing manual edits."""

    try:
        result = sync_project(path, check=check)
    except (ProjectError, OSError, ValueError) as exc:
        _fail(str(exc))
    for change in result.changes:
        out.print(change)
    for problem in result.problems:
        err.print(f"[red]{problem}[/red]")
    if result.problems or (check and result.changes):
        if result.problems:
            err.print(
                "Restore the locked generated file, or move edits into "
                "project-owned files before sync."
            )
        raise typer.Exit(1)
    out.print("Managed files clean." if check or not result.changes else "Managed files updated.")


@app.command()
def doctor(
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
) -> None:
    """Run cheap, deterministic project health checks."""

    checks = doctor_project(path)
    table = Table(title="AI Kit doctor")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail")
    for item in checks:
        table.add_row(item.name, "OK" if item.ok else "FAIL", item.detail)
    out.print(table)
    if not all(item.ok for item in checks):
        raise typer.Exit(1)


def _checks(values: list[str] | None) -> dict[str, str]:
    if not values:
        return {"verify": "./dev verify"}
    checks: dict[str, str] = {}
    for value in values:
        name, separator, command = value.partition("=")
        if not separator or not name.strip() or not command.strip() or name in checks:
            _fail(f"invalid --check {value!r}; expected unique NAME=COMMAND", 2)
        checks[name.strip()] = command.strip()
    return checks


@goal_app.command("new")
def goal_new(
    goal_id: str,
    title: str = typer.Option(..., "--title", help="Short goal title."),
    intent: str = typer.Option(..., "--intent", help="User outcome to achieve."),
    accept: list[str] | None = typer.Option(
        None, "--accept", help="Observable acceptance criterion."
    ),
    check: list[str] | None = typer.Option(None, "--check", help="Required NAME=COMMAND check."),
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
) -> None:
    """Create an acceptance contract before implementing the goal."""

    from .goals import create_goal

    try:
        goal = create_goal(path, goal_id, title, intent, accept or [], _checks(check))
    except (RuntimeError, OSError, ValueError) as exc:
        _fail(str(exc))
    out.print(f"Created {goal.id} ({goal.state}). Commit its contract before verification.")


@goal_app.command("show")
def goal_show(
    goal_id: str,
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
) -> None:
    """Show one goal contract and its evidence."""

    from .goals import is_evidence_fresh, load_goal

    try:
        goal = load_goal(path, goal_id)
    except (RuntimeError, OSError, ValueError) as exc:
        _fail(str(exc))
    out.print(f"[bold]{goal.id}[/bold] — {goal.title} [{goal.state}]")
    out.print(goal.intent)
    for criterion in goal.acceptance:
        out.print(f"  • {criterion}")
    for name, command in goal.checks.items():
        out.print(f"  {name}: {command}")
    out.print(f"Evidence: {goal.evidence or 'none'}")
    if goal.state == "DONE" and not is_evidence_fresh(path.resolve(), goal):
        err.print("[yellow]DONE evidence is stale for the current product.[/yellow]")


@goal_app.command("status")
def goal_status(
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
) -> None:
    """List goal states."""

    from .goals import is_evidence_fresh, list_goals

    try:
        goals = list_goals(path)
    except (RuntimeError, OSError, ValueError) as exc:
        _fail(str(exc))
    if not goals:
        out.print("No goals.")
    for goal in goals:
        stale = goal.state == "DONE" and not is_evidence_fresh(path.resolve(), goal)
        out.print(f"{goal.id}: {goal.state}{' (stale)' if stale else ''} — {goal.title}")


@goal_app.command("state")
def goal_state(
    goal_id: str,
    state: str,
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
) -> None:
    """Record a legal manual lifecycle transition."""

    from .goals import set_goal_state

    try:
        goal = set_goal_state(path, goal_id, state.upper())
    except (RuntimeError, OSError, ValueError) as exc:
        _fail(str(exc))
    out.print(f"{goal.id}: {goal.state}")


@goal_app.command("verify")
def goal_verify(
    goal_id: str,
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
) -> None:
    """Execute all required checks and record commit-bound evidence."""

    from .goals import verify_goal

    try:
        goal = verify_goal(path, goal_id)
    except (RuntimeError, OSError, ValueError) as exc:
        _fail(str(exc))
    out.print(f"{goal.id}: {goal.state}; evidence {goal.evidence or 'none'}")
    if goal.evidence:
        for check_result in goal.evidence.checks:
            if check_result.status != "pass":
                out.print(
                    f"{classify_check(check_result.name)}: {check_result.name} "
                    f"{check_result.status} (exit {check_result.exit_code})"
                )
    if goal.evidence is None or goal.evidence.result != "pass":
        raise typer.Exit(1)


@goal_app.command("complete")
def goal_complete(
    goal_id: str,
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
) -> None:
    """Mark DONE only when current-commit evidence passes."""

    from .goals import complete_goal

    try:
        goal = complete_goal(path, goal_id)
    except (RuntimeError, OSError, ValueError) as exc:
        _fail(str(exc))
    out.print(f"{goal.id}: {goal.state}")


@goal_app.command("reopen")
def goal_reopen(
    goal_id: str,
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
) -> None:
    """Reopen a stale accepted goal for fresh verification."""

    from .goals import reopen_goal

    try:
        goal = reopen_goal(path, goal_id)
    except (RuntimeError, OSError, ValueError) as exc:
        _fail(str(exc))
    out.print(f"{goal.id}: {goal.state}; commit the reopened contract, then verify again")


@app.command()
def adopt(
    safe: bool = typer.Option(True, "--safe/--aggressive", help="Conservative adoption mode."),
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
) -> None:
    """Inspect an existing repository and record an honest baseline."""

    if not safe:
        _fail("aggressive adoption is not implemented; use --safe", 2)
    from .adoption import adopt_safe, mark_managed

    try:
        preflight_adoption(path)
        result = adopt_safe(path)
    except (RuntimeError, OSError, ValueError) as exc:
        _fail(str(exc))
    out.print(f"Source commit: {result.source_commit}")
    out.print(f"Inventory: {result.inventory_path}")
    out.print(f"Baseline: {result.baseline_path}")
    out.print(f"Custody: {result.custody_path}")
    for name, item in result.checks.items():
        label = classify_check(name, item.reason) if item.status != "pass" else "PASS"
        out.print(f"{label}: {name} {item.status} ({item.reason})")
    try:
        installed = init_project(path, adopted=True)
        if installed.problems:
            for problem in installed.problems:
                err.print(f"[red]AI Kit install blocked:[/red] {problem}")
            raise typer.Exit(1)
        mark_managed(result)
    except (ProjectError, RuntimeError, OSError, ValueError) as exc:
        _fail(f"baseline recorded, but AI Kit install is incomplete: {exc}")
    out.print("AI Kit custody established.")
    if result.status != "pass":
        raise typer.Exit(1)


@baseline_app.command("compare")
def baseline_compare(
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
) -> None:
    """Re-run the inherited check and report regressions or unknown evidence."""

    from .adoption import recheck_baseline

    try:
        comparison = recheck_baseline(path)
    except (RuntimeError, OSError, ValueError) as exc:
        _fail(str(exc))
    out.print(f"Current baseline status: {comparison.status}")
    for item in comparison.regressions:
        err.print(f"[red]{FailureClass.BASELINE_REGRESSION}:[/red] {item}")
    for item in comparison.unresolved:
        err.print(f"[yellow]Unknown:[/yellow] {item}")
    if comparison.regressions or comparison.unresolved:
        raise typer.Exit(1)
    out.print("No measured baseline regression.")


if __name__ == "__main__":
    app()
