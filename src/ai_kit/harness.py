"""Preferred setup-harness configuration and interactive launcher.

AI Kit can hand a prepared setup protocol to one of four supported coding
harnesses. The user-wide preference lives in
``$XDG_CONFIG_HOME/ai-kit/config.toml`` as a top-level ``setup_harness`` string.
The launcher starts the chosen executable as a child process with inherited
streams and environment (plus a recursion marker), so the user's own sandbox,
approval, authentication, and model configuration are preserved untouched.

This module is deliberately thin: it configures a preference, resolves the
executable, and launches it. It does not implement a per-harness adapter
factory or any dependency beyond the Python standard library.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path

from .system import UnsafeWriteError, atomic_write_text, read_text_exact, resolve_xdg_paths

APP_NAME = "ai-kit"
CONFIG_FILENAME = "config.toml"
DEFAULT_HARNESS = "codex"

#: Supported harness codes mapped to their human-readable labels, in display order.
HARNESS_CHOICES: dict[str, str] = {
    "codex": "Codex",
    "codex_ds": "codex_ds",
    "opencode": "OpenCode",
    "claude": "Claude Code",
}

#: Environment variable set while AI Kit has a setup harness running.
SETUP_ACTIVE_MARKER = "AI_KIT_SETUP_ACTIVE"

#: Environment markers that mean an interactive agent session is already active.
ACTIVE_AGENT_MARKERS = (
    "CODEX_SANDBOX",
    "CODEX_THREAD_ID",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
)

_ACTIVE_MARKER_LABELS = {
    "CODEX_SANDBOX": "an active Codex sandbox",
    "CODEX_THREAD_ID": "an active Codex session",
    "CLAUDECODE": "an active Claude Code session",
    "CLAUDE_CODE_ENTRYPOINT": "an active Claude Code session",
}

#: Token that each harness's ``--help`` must advertise to prove prompt support.
_PROMPT_HELP_TOKENS = {
    "codex": "prompt",
    "codex_ds": "prompt",
    "opencode": "--prompt",
    "claude": "prompt",
}

_HELP_TIMEOUT_SECONDS = 10.0

_TABLE_HEADER = re.compile(r"^[ \t]*\[", re.MULTILINE)
_HARNESS_ASSIGNMENT = re.compile(
    r"""^(?P<indent>[ \t]*)"""
    r"""(?:setup_harness|"setup_harness"|'setup_harness')"""
    r"""[ \t]*=[ \t]*(?P<value>.+?)[ \t]*$""",
    re.MULTILINE,
)
_STRING_TOKEN = re.compile(
    r'"""(?:.|\n)*?"""'
    r"|'''(?:.|\n)*?'''"
    r'|"(?:[^"\\]|\\.)*"'
    r"|'[^']*'"
)


class HarnessError(RuntimeError):
    """Raised when the preferred harness cannot be configured or launched."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _config_path() -> Path:
    """Return ``$XDG_CONFIG_HOME/ai-kit/config.toml`` via the shared resolver."""

    return resolve_xdg_paths().config_dir / CONFIG_FILENAME


def _refuse_symlinks(path: Path) -> None:
    """Refuse a config file or any ancestor that is a symlink."""

    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise HarnessError(f"refusing symlinked config path: {candidate}")


def _parse_config(path: Path, text: str) -> dict[str, object]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise HarnessError(f"invalid TOML in {path}: {exc}") from exc


def _selected_harness(path: Path, data: Mapping[str, object]) -> str:
    if "setup_harness" not in data:
        return DEFAULT_HARNESS
    value = data["setup_harness"]
    if not isinstance(value, str) or value not in HARNESS_CHOICES:
        choices = ", ".join(HARNESS_CHOICES)
        raise HarnessError(f"{path}: setup_harness must be one of {choices}; got {value!r}")
    return value


def get_setup_harness() -> str:
    """Return the configured setup harness, defaulting to ``codex``.

    A missing key or missing file yields the default. A present-but-invalid
    selection, unreadable file, or symlinked path raises :class:`HarnessError`.
    """

    path = _config_path()
    _refuse_symlinks(path)
    if not path.exists():
        return DEFAULT_HARNESS
    if path.is_dir():
        raise HarnessError(f"config path is a directory: {path}")
    try:
        text = read_text_exact(path)
    except OSError as exc:
        raise HarnessError(f"cannot read {path}: {exc}") from exc
    return _selected_harness(path, _parse_config(path, text))


def set_setup_harness(name: str) -> bool:
    """Persist ``name`` as the setup harness, returning whether it changed.

    Only the top-level ``setup_harness`` value is touched; unrelated keys,
    comments, and formatting are preserved. An unchanged value is a no-op.
    Symlinked paths, invalid TOML, and unknown or invalid existing selections
    are refused rather than overwritten.
    """

    if name not in HARNESS_CHOICES:
        choices = ", ".join(HARNESS_CHOICES)
        raise HarnessError(f"unknown setup harness {name!r}; choose one of {choices}")

    path = _config_path()
    _refuse_symlinks(path)
    if path.exists() and path.is_dir():
        raise HarnessError(f"config path is a directory: {path}")

    expected: str | None = None
    if path.exists():
        try:
            original = read_text_exact(path)
        except OSError as exc:
            raise HarnessError(f"cannot read {path}: {exc}") from exc
        data = _parse_config(path, original)
        current = _selected_harness(path, data)
        if current == name:
            return False
        updated = (
            _replace_harness_text(original, name)
            if "setup_harness" in data
            else f'setup_harness = "{name}"\n' + original
        )
        if _parse_config(path, updated) != {**data, "setup_harness": name}:
            raise HarnessError("unfamiliar setup_harness formatting; edit config.toml manually")
        expected = original
    else:
        updated = f'setup_harness = "{name}"\n'

    _parse_config(path, updated)  # Guard against producing invalid TOML.
    try:
        atomic_write_text(path, updated, expected_content=expected)
    except UnsafeWriteError as exc:
        raise HarnessError(str(exc)) from exc
    return True


def _replace_harness_text(text: str, name: str) -> str:
    """Replace or insert the top-level ``setup_harness`` string in ``text``."""

    header = _TABLE_HEADER.search(text)
    top_level_end = header.start() if header else len(text)
    top_level = text[:top_level_end]

    matches = list(_HARNESS_ASSIGNMENT.finditer(top_level))
    if len(matches) > 1:
        raise HarnessError("refusing to rewrite config.toml: duplicate setup_harness keys")

    rendered = f'"{name}"'
    if not matches:
        line = f'setup_harness = "{name}"\n'
        if header is None:
            separator = "" if not text or text.endswith("\n") else "\n"
            return text + separator + line
        return text[: header.start()] + line + text[header.start() :]

    match = matches[0]
    token = _STRING_TOKEN.search(top_level, match.start("value"))
    if token is None or token.end() > match.end("value"):
        raise HarnessError("refusing to rewrite unfamiliar setup_harness value in config.toml")
    return text[: token.start()] + rendered + text[token.end() :]


# ---------------------------------------------------------------------------
# Launching
# ---------------------------------------------------------------------------


def harness_available(name: str) -> bool:
    """Return whether the harness executable is discoverable on ``PATH``."""

    if name not in HARNESS_CHOICES:
        return False
    return shutil.which(name) is not None


def launch_setup_harness(root: Path, name: str, protocol: Path) -> int:
    """Launch ``name`` interactively to run the pending setup protocol.

    The child inherits stdin/stdout/stderr and the full environment (plus
    :data:`SETUP_ACTIVE_MARKER`), preserving the user's permissions, approval
    configuration, authentication, and models. The child's exit status is
    returned verbatim. ``KeyboardInterrupt`` yields exit code 130.
    """

    if name not in HARNESS_CHOICES:
        choices = ", ".join(HARNESS_CHOICES)
        raise HarnessError(f"unknown setup harness {name!r}; choose one of {choices}")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise HarnessError(
            "launching a setup harness requires an interactive terminal "
            "(stdin and stdout must be TTYs)"
        )
    active = _active_session()
    if active is not None:
        raise HarnessError(f"refusing to launch a setup harness from {active}")

    executable = shutil.which(name)
    if executable is None:
        raise HarnessError(
            f"{name} executable was not found on PATH; install it or choose another harness"
        )

    repository_root = Path(root)
    prompt = _setup_prompt(repository_root, Path(protocol))
    argv = _launch_argv(executable, name, prompt)
    environment = {**os.environ, SETUP_ACTIVE_MARKER: "1"}

    try:
        _probe_prompt_support(executable, name)
    except KeyboardInterrupt:
        return 130
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(
            f"{name} --help did not respond within {exc.timeout:g}s; cannot confirm prompt support"
        ) from exc
    except OSError as exc:
        raise HarnessError(f"could not run {name} --help: {exc}") from exc

    try:
        completed = subprocess.run(
            argv,
            cwd=str(repository_root),
            env=environment,
            check=False,
        )
    except KeyboardInterrupt:
        return 130
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(
            f"{name} setup did not complete: timed out after {exc.timeout:g}s"
        ) from exc
    except OSError as exc:
        raise HarnessError(f"could not launch {name}: {exc}") from exc
    return completed.returncode


def _active_session() -> str | None:
    if os.environ.get(SETUP_ACTIVE_MARKER):
        return "an already-active ai-kit setup session"
    for marker in ACTIVE_AGENT_MARKERS:
        if os.environ.get(marker):
            return _ACTIVE_MARKER_LABELS[marker]
    return None


def _probe_prompt_support(executable: str, name: str) -> None:
    """Fail clearly when ``executable`` cannot accept an initial prompt."""

    completed = subprocess.run(
        [executable, "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_HELP_TIMEOUT_SECONDS,
        check=False,
    )
    if completed.returncode != 0:
        raise HarnessError(
            f"{name} --help exited with code {completed.returncode}; cannot confirm prompt support"
        )
    output = f"{completed.stdout}\n{completed.stderr}".lower()
    if _PROMPT_HELP_TOKENS[name] not in output:
        raise HarnessError(
            f"{name} --help does not advertise a prompt argument; cannot launch setup"
        )


def _launch_argv(executable: str, name: str, prompt: str) -> list[str]:
    if name == "opencode":
        return [executable, "--prompt", prompt]
    return [executable, prompt]


def _setup_prompt(root: Path, protocol: Path) -> str:
    """Build the bootstrap prompt, referring to a repository-relative protocol."""

    try:
        relative = os.path.relpath(protocol, root)
    except ValueError:  # pragma: no cover - different drives (Windows)
        relative = str(protocol)
    return (
        "You are running the AI Kit repository setup harness. "
        "Read the repository rules in AGENTS.md and any instruction files it points to, "
        f"then follow the AI Kit setup protocol at {relative!r} "
        "(a repository-relative path). Perform only the pending setup work described "
        "by that protocol. Never launch `ai-kit setup` or another agent harness "
        "recursively from inside this session. When the pending setup is complete, "
        "use `ai-kit setup --finalize` to finalize it (run that command; do not launch "
        "a new harness), and report any uncertainty instead of guessing."
    )
