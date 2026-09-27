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
baseline_app = typer.Typer(help="Compare an adopted repository with its measured source baseline.")
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
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
    module: list[str] | None = typer.Option(None, "--module", help="Enable a profile module."),
) -> None:
    """Initialize a safe, minimal AI Kit project."""

    try:
        result = init_project(path, modules=tuple(module) if module else ("strict-verification",))
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


@app.command()
def verify(
    path: Path = typer.Option(Path("."), "--path", "-C", help="Git repository root."),
    check: bool = typer.Option(False, "--check", help="Inspect saved evidence; do not run checks."),
) -> None:
    """Run project-required verification and record commit-bound evidence."""

    from .verification import is_evidence_fresh, read_evidence, verify_project

    try:
        if check:
            evidence = read_evidence(path)
            if not is_evidence_fresh(path, evidence):
                _fail("saved evidence is missing, failing, unknown, or stale; run ai-kit verify")
            out.print(f"Saved passing evidence is current for {evidence['commit']}.")
            return
        evidence = verify_project(path)
    except (RuntimeError, OSError, ValueError) as exc:
        _fail(str(exc))
    for name, result in evidence["requirements"].items():
        out.print(f"{name}: {result['status'].upper()} {result['reason']}", markup=False)
    for warning in evidence["warnings"]:
        out.print(f"Review: {warning}", markup=False)
    if evidence.get("reason"):
        out.print(evidence["reason"], markup=False)
    out.print(
        f"Verification: {evidence['result'].upper()}; evidence: .ai-local/evidence/latest.json"
    )
    if evidence["result"] != "pass":
        raise typer.Exit(1)


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
