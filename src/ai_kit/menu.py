"""Keyboard-driven terminal menu for AI Kit.

The menu is intentionally small: it renders with the standard library
``curses`` module, exits the curses screen before running any command, and
shows every command's real output and exit status. It only runs when both
standard streams are terminals.
"""

from __future__ import annotations

import curses
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .project import (
    MODULE_ORDER,
    MODULES,
    ProjectError,
    _root,
    load_profile,
    set_profile_modules,
)


class MenuError(RuntimeError):
    """The terminal menu cannot run or an action could not be executed."""


_NOTES = {
    "strict-verification": "requires a verify check in every goal contract",
    "webapp": "requires acceptance and runtime_errors checks in every goal contract",
    "learning": "renders a private learning skill and .ai-local/learning/",
    "professional-repository": "renders a GitHub Actions verify workflow",
}
_DECLARATIVE = MODULES - _NOTES.keys()

_MIN_ROWS = 8
_MIN_COLS = 30


@dataclass(frozen=True, slots=True)
class Action:
    label: str
    kind: str  # "profile", "harness", "command", "dev", or "exit"
    args: tuple[str, ...] = ()


ACTIONS = (
    Action("Profile modules", "profile"),
    Action("Goal status", "command", ("goal", "status")),
    Action("Doctor", "command", ("doctor",)),
    Action("Sync managed files", "command", ("sync",)),
    Action("Run ./dev check", "dev", ("check",)),
    Action("Run ./dev verify", "dev", ("verify",)),
    Action("Setup harness", "harness"),
    Action("Set up / resume setup", "command", ("setup",)),
    Action("Exit", "exit"),
)


def _isatty(stream: object) -> bool:
    probe = getattr(stream, "isatty", None)
    return bool(probe and probe())


def interactive_ready() -> bool:
    """Whether a keyboard-driven menu can run on this terminal."""

    term = os.environ.get("TERM", "").strip()
    return _isatty(sys.stdin) and _isatty(sys.stdout) and bool(term) and term != "dumb"


def _require_terminal() -> None:
    if not (_isatty(sys.stdin) and _isatty(sys.stdout)):
        raise MenuError(
            "the terminal menu needs an interactive terminal; stdin and stdout must be "
            "TTYs (use ai-kit --help or an individual command in scripts)"
        )
    term = os.environ.get("TERM", "").strip()
    if not term or term == "dumb":
        raise MenuError(
            f"the terminal menu needs a capable terminal; TERM={term or 'unset'!r}. "
            "Set TERM to a capable value such as xterm-256color or use an individual command."
        )


def _write(stdscr: curses._CursesWindow, row: int, col: int, text: str, attr: int = 0) -> None:
    try:
        stdscr.addnstr(row, col, text, max(0, stdscr.getmaxyx()[1] - col - 1), attr)
    except curses.error:
        pass


def _too_small(stdscr: curses._CursesWindow, rows: int, cols: int) -> bool:
    if rows >= _MIN_ROWS and cols >= _MIN_COLS:
        return False
    stdscr.erase()
    _write(stdscr, 0, 0, "Terminal too small for the AI Kit menu.", curses.A_BOLD)
    _write(stdscr, 1, 0, f"Need at least {_MIN_ROWS} rows and {_MIN_COLS} columns.")
    _write(stdscr, 2, 0, "Resize or q to quit")
    stdscr.refresh()
    return True


def _harness_module():
    """Import :mod:`ai_kit.harness` lazily so a missing module is a clear error."""

    try:
        from . import harness
    except ImportError as exc:  # pragma: no cover - only when the module is absent
        raise MenuError("harness support is unavailable in this build") from exc
    return harness


def _preferred_harness_label() -> str:
    """Best-effort label for the preferred harness; never raises."""

    try:
        harness = _harness_module()
    except MenuError:
        return "(harness support unavailable)"
    try:
        current = harness.get_setup_harness()
        available = harness.harness_available(current)
    except harness.HarnessError:
        return "(unreadable preference)"
    label = harness.HARNESS_CHOICES.get(current, current)
    return f"{label}{'' if available else ' (not installed)'}"


def _screen(call):
    try:
        return curses.wrapper(call)
    except KeyboardInterrupt:
        return None
    except curses.error as exc:
        raise MenuError(f"the terminal menu could not start: {exc}") from exc


def _window_start(index: int, count: int, visible: int) -> int:
    if count <= visible:
        return 0
    return max(0, min(index - visible + 1, count - visible))


def _choose_action(repo: Path, modules: str, harness: str) -> int | None:
    def loop(stdscr) -> int | None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        index = 0
        while True:
            rows, cols = stdscr.getmaxyx()
            if _too_small(stdscr, rows, cols):
                key = stdscr.getch()
                if key in (ord("q"), 27, 3, 4):
                    return None
                continue
            stdscr.erase()
            _write(stdscr, 0, 0, "AI Kit terminal menu", curses.A_BOLD)
            _write(stdscr, 1, 0, f"Repository: {repo}"[: cols - 1])
            _write(stdscr, 2, 0, f"Modules: {modules}"[: cols - 1])
            _write(stdscr, 3, 0, f"Preferred harness: {harness}"[: cols - 1])
            _write(stdscr, 4, 0, "Choose an action")
            first = 5
            available = max(1, rows - first - 2)
            top = _window_start(index, len(ACTIONS), available)
            for offset, action in enumerate(ACTIONS[top : top + available]):
                position = top + offset
                label = f" {'>' if position == index else ' '} {action.label}"
                _write(
                    stdscr,
                    first + offset,
                    0,
                    label[: cols - 1],
                    curses.A_REVERSE if position == index else 0,
                )
            _write(
                stdscr,
                rows - 1,
                0,
                "Arrows or j/k move | Enter select | q exit"[: cols - 1],
            )
            stdscr.refresh()
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                index = (index - 1) % len(ACTIONS)
            elif key in (curses.KEY_DOWN, ord("j")):
                index = (index + 1) % len(ACTIONS)
            elif key in (curses.KEY_ENTER, 10, 13):
                return index
            elif key in (ord("q"), 27, 3, 4):
                return None
            elif key == curses.KEY_RESIZE:
                continue

    return _screen(loop)


def _draw_modules(stdscr, repo: Path, order: tuple[str, ...], cursor: int, selected: set[str]):
    rows, cols = stdscr.getmaxyx()
    stdscr.erase()
    _write(stdscr, 0, 0, "Profile modules", curses.A_BOLD)
    _write(stdscr, 1, 0, f"Repository: {repo}"[: cols - 1])
    _write(stdscr, 2, 0, "Space toggle | Enter apply | Esc/q cancel"[: cols - 1])
    first = 4
    available = max(1, rows - first - 3)
    top = _window_start(cursor, len(order), available)
    for offset, name in enumerate(order[top : top + available]):
        position = top + offset
        checked = "x" if name in selected else " "
        tag = " (declarative)" if name in _DECLARATIVE else ""
        label = f" [{checked}] {name}{tag}"
        _write(
            stdscr,
            first + offset,
            0,
            label[: cols - 1],
            curses.A_REVERSE if position == cursor else 0,
        )
    note = _NOTES.get(order[cursor], "declarative only: recorded but no behavior yet")
    _write(stdscr, rows - 2, 0, f"  {order[cursor]}: {note}"[: cols - 1])
    _write(
        stdscr,
        rows - 1,
        0,
        f"  {len(selected)} selected | declarative = no behavior yet"[: cols - 1],
    )
    stdscr.refresh()


def _choose_modules(repo: Path, current: tuple[str, ...]) -> tuple[str, ...] | None:
    def loop(stdscr) -> tuple[str, ...] | None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        order = MODULE_ORDER
        selected = set(current)
        cursor = 0
        while True:
            rows, cols = stdscr.getmaxyx()
            if _too_small(stdscr, rows, cols):
                key = stdscr.getch()
                if key in (ord("q"), 27, 3, 4):
                    return None
                continue
            _draw_modules(stdscr, repo, order, cursor, selected)
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                cursor = (cursor - 1) % len(order)
            elif key in (curses.KEY_DOWN, ord("j")):
                cursor = (cursor + 1) % len(order)
            elif key == ord(" "):
                name = order[cursor]
                if name in selected:
                    selected.discard(name)
                else:
                    selected.add(name)
            elif key in (curses.KEY_ENTER, 10, 13):
                return tuple(name for name in order if name in selected)
            elif key in (ord("q"), 27, 3, 4):
                return None
            elif key == curses.KEY_RESIZE:
                continue

    return _screen(loop)


def _draw_harness(
    stdscr,
    order: tuple[str, ...],
    labels: dict[str, str],
    current: str,
    cursor: int,
    available: dict[str, bool],
) -> None:
    rows, cols = stdscr.getmaxyx()
    stdscr.erase()
    _write(stdscr, 0, 0, "Preferred setup harness", curses.A_BOLD)
    _write(stdscr, 1, 0, "Saved user-wide for 'Set up / resume setup'."[: cols - 1])
    _write(stdscr, 2, 0, "Arrows or j/k move | Enter save | Esc/q cancel"[: cols - 1])
    first = 4
    room = max(1, rows - first - 3)
    top = _window_start(cursor, len(order), room)
    for offset, name in enumerate(order[top : top + room]):
        position = top + offset
        marker = ">" if position == cursor else " "
        chosen = "*" if name == current else " "
        state = "installed" if available.get(name) else "not installed"
        label = f" {marker} [{chosen}] {labels.get(name, name)} ({name}) - {state}"
        _write(
            stdscr,
            first + offset,
            0,
            label[: cols - 1],
            curses.A_REVERSE if position == cursor else 0,
        )
    _write(stdscr, rows - 2, 0, "  * = current preference"[: cols - 1])
    _write(
        stdscr,
        rows - 1,
        0,
        "  A not-installed harness is saved but must be installed before launching."[: cols - 1],
    )
    stdscr.refresh()


def _choose_harness(harness, current: str, available: dict[str, bool]) -> str | None:
    order = tuple(harness.HARNESS_CHOICES)
    labels = dict(harness.HARNESS_CHOICES)

    def loop(stdscr) -> str | None:
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        cursor = order.index(current) if current in order else 0
        while True:
            rows, cols = stdscr.getmaxyx()
            if _too_small(stdscr, rows, cols):
                key = stdscr.getch()
                if key in (ord("q"), 27, 3, 4):
                    return None
                continue
            _draw_harness(stdscr, order, labels, current, cursor, available)
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                cursor = (cursor - 1) % len(order)
            elif key in (curses.KEY_DOWN, ord("j")):
                cursor = (cursor + 1) % len(order)
            elif key in (curses.KEY_ENTER, 10, 13):
                return order[cursor]
            elif key in (ord("q"), 27, 3, 4):
                return None
            elif key == curses.KEY_RESIZE:
                continue

    return _screen(loop)


def _harness_flow() -> None:
    """Pick and persist the preferred setup harness without ever launching it."""

    try:
        harness = _harness_module()
    except MenuError as exc:
        _show(["Harness setup unavailable", str(exc)], ok=False)
        return
    try:
        current = harness.get_setup_harness()
        available = {name: harness.harness_available(name) for name in harness.HARNESS_CHOICES}
    except (harness.HarnessError, OSError, ValueError) as exc:
        _show(["Cannot read the harness preference", str(exc)], ok=False)
        return
    selection = _choose_harness(harness, current, available)
    if selection is None:
        return
    try:
        changed = harness.set_setup_harness(selection)
    except (harness.HarnessError, OSError, ValueError) as exc:
        _show(["Could not save the harness preference", str(exc)], ok=False)
        return
    label = harness.HARNESS_CHOICES.get(selection, selection)
    lines = [f"Preferred harness: {label} ({selection})"]
    if not available.get(selection, True):
        lines.append(
            "This harness is not installed yet; install it before 'Set up / resume setup'."
        )
    lines.append("Saved." if changed else "Unchanged.")
    _show(lines, ok=True)


def _wait_for_enter() -> None:
    try:
        input("Press Enter to return to the menu... ")
    except EOFError:
        print()


def _show(lines: list[str], *, ok: bool) -> None:
    print()
    for line in lines:
        print(line)
    if not ok:
        print("FAILED", file=sys.stderr)
    _wait_for_enter()


def _profile_flow(repo: Path) -> None:
    try:
        current = load_profile(repo).modules
    except ProjectError as exc:
        _show(["Cannot read .ai/profile.toml", str(exc)], ok=False)
        return
    selection = _choose_modules(repo, current)
    if selection is None:
        return
    try:
        result = set_profile_modules(repo, selection)
    except (ProjectError, OSError) as exc:
        _show(["Could not apply profile", str(exc)], ok=False)
        return
    if not result.changes:
        _show(["Modules unchanged; nothing to do."], ok=True)
        return
    _show(["Profile updated.", *result.changes], ok=True)


def _command_environment() -> dict[str, str]:
    env = dict(os.environ)
    source_root = str(Path(__file__).resolve().parent.parent)
    parts = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    if source_root not in parts:
        env["PYTHONPATH"] = os.pathsep.join([source_root, *parts])
    return env


def _command_flow(repo: Path, action: Action) -> None:
    if action.kind == "dev":
        program = repo / "dev"
        if not program.is_file() or not os.access(program, os.X_OK):
            _show([f"{program} is missing or not executable"], ok=False)
            return
        command = [str(program), *action.args]
    else:
        command = [sys.executable, "-m", "ai_kit.cli", *action.args, "-C", str(repo)]
    print(f"\n$ {' '.join(command)}")
    try:
        completed = subprocess.run(command, cwd=repo, env=_command_environment())
    except (OSError, KeyboardInterrupt) as exc:
        _show([f"could not run {' '.join(command)}: {exc}"], ok=False)
        return
    if completed.returncode != 0:
        _show([f"FAILED: exit code {completed.returncode}"], ok=False)
    else:
        _show(["Passed."], ok=True)


def run_menu(root: Path) -> int:
    """Run the terminal menu against ``root`` until the user exits."""

    _require_terminal()
    try:
        repo = _root(root)
    except ProjectError as exc:
        raise MenuError(str(exc)) from exc
    while True:
        try:
            modules = ", ".join(load_profile(repo).modules) or "(none)"
        except ProjectError:
            modules = "(unreadable profile)"
        index = _choose_action(repo, modules, _preferred_harness_label())
        if index is None:
            return 0
        action = ACTIONS[index]
        if action.kind == "exit":
            return 0
        if action.kind == "profile":
            _profile_flow(repo)
        elif action.kind == "harness":
            _harness_flow()
        else:
            _command_flow(repo, action)
